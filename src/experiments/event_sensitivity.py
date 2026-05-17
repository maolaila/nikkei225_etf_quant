from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pandas as pd

from src.backtest.engine import run_backtest
from src.config.loader import deep_merge
from src.data.cleaner import normalize_bars
from src.data.data_lake import DataLake
from src.utils.paths import ensure_dir
from src.utils.serialization import write_json
from src.validation.event_audit import EventAuditResult
from src.validation.event_audit import corporate_action_candidate_dates
from src.validation.event_audit import market_event_dates
from src.validation.event_audit import run_event_audit
from src.validation.walk_forward import run_walk_forward


def run_event_sensitivity_backtests(
    config: dict[str, Any],
    *,
    audit_result: EventAuditResult | None = None,
    output_root: str | Path = "data/reports/event_sensitivity",
    model_name: str | None = "latest",
) -> dict[str, Any]:
    _ensure_normalized_data(config)
    audit_result = audit_result or run_event_audit(config)
    out = ensure_dir(output_root)
    daily_flags = audit_result.daily_flags
    event_dates = market_event_dates(daily_flags)
    corporate_dates = corporate_action_candidate_dates(daily_flags)
    rows: list[dict[str, Any]] = []

    for scenario in _training_scenario_specs(event_dates, corporate_dates):
        rows.append(_run_training_exclusion_scenario(config, scenario, out, model_name))

    for scenario in _entry_scenario_specs(event_dates, corporate_dates):
        _ensure_normalized_data(config)
        scenario_dir = out / str(scenario["name"])
        scenario_config = deep_merge(
            config,
            {
                "backtest": {
                    "report": {"output_dir": str(scenario_dir)},
                    "entry_date_filter": {
                        "enabled": True,
                        "name": scenario["name"],
                        "mode": scenario["mode"],
                        "dates": scenario["dates"],
                    },
                }
            },
        )
        metrics, _, _, _ = run_backtest(scenario_config, model_name=model_name)
        rows.append(_scenario_row(scenario, metrics, scenario_dir))

    ranking = pd.DataFrame(rows)
    ranking.to_csv(out / "event_sensitivity.csv", index=False)
    summary = {
        "output_root": str(out),
        "audit_output_dir": str(audit_result.output_dir),
        "event_dates": event_dates,
        "corporate_action_candidate_dates": corporate_dates,
        "scenarios": rows,
    }
    write_json(out / "event_sensitivity_summary.json", summary)
    (out / "event_sensitivity.md").write_text(_render_summary(summary), encoding="utf-8")
    return summary


def _training_scenario_specs(event_dates: list[str], corporate_dates: list[str]) -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    if event_dates:
        scenarios.append(
            {
                "name": "exclude_market_event_days_from_training",
                "mode": "training_exclude",
                "dates": event_dates,
                "description": "Retrain walk-forward predictions with audited market event dates removed from each training window.",
            }
        )
    if corporate_dates:
        scenarios.append(
            {
                "name": "exclude_corporate_action_candidates_from_training",
                "mode": "training_exclude",
                "dates": corporate_dates,
                "description": "Retrain walk-forward predictions with suspected split/adjustment dates removed from training windows.",
            }
        )
    return scenarios


def _entry_scenario_specs(event_dates: list[str], corporate_dates: list[str]) -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    if event_dates:
        scenarios.extend(
            [
                {
                    "name": "exclude_market_event_days",
                    "mode": "exclude",
                    "dates": event_dates,
                    "description": "Block new entries on audited market event days.",
                },
                {
                    "name": "market_event_day_stress",
                    "mode": "include",
                    "dates": event_dates,
                    "description": "Allow new entries only on audited market event days.",
                },
            ]
        )
    if corporate_dates:
        scenarios.append(
            {
                "name": "exclude_corporate_action_candidates",
                "mode": "exclude",
                "dates": corporate_dates,
                "description": "Block new entries on suspected split/adjustment discontinuity dates.",
            }
        )
    return scenarios


def _run_training_exclusion_scenario(
    config: dict[str, Any],
    scenario: dict[str, Any],
    output_root: Path,
    model_name: str | None,
) -> dict[str, Any]:
    scenario_dir = output_root / str(scenario["name"])
    scenario_config = deep_merge(
        config,
        {
            "model": {"training": {"exclude_event_dates": scenario["dates"]}},
            "backtest": {"report": {"output_dir": str(scenario_dir)}},
        },
    )
    walk_forward_model = None if model_name in (None, "latest") else model_name
    with _temporary_model_artifacts():
        run_walk_forward(scenario_config, model_name=walk_forward_model)
        _ensure_normalized_data(config)
        metrics, _, _, _ = run_backtest(scenario_config, model_name="latest")
        summary_path = Path("data/models/walk_forward_summary.json")
        if summary_path.exists():
            scenario_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(summary_path, scenario_dir / "walk_forward_summary.json")
    return _scenario_row(scenario, metrics, scenario_dir)


def _scenario_row(scenario: dict[str, Any], metrics: dict[str, Any], report_dir: Path) -> dict[str, Any]:
    keys = [
        "total_return_pct",
        "average_monthly_return_pct",
        "min_monthly_return_pct",
        "max_drawdown_pct",
        "total_trades",
        "profit_factor",
        "positive_active_month_ratio",
        "entry_date_filter_blocked_signals",
        "walk_forward_windows",
        "walk_forward_fallback_used",
    ]
    row = {
        "scenario": scenario["name"],
        "mode": scenario["mode"],
        "dates": ",".join(scenario["dates"]),
        "date_count": len(scenario["dates"]),
        "description": scenario["description"],
        "report_dir": str(report_dir),
    }
    row.update({key: metrics.get(key) for key in keys})
    return row


_MODEL_ARTIFACTS = [
    Path("data/models/walk_forward_predictions.parquet"),
    Path("data/models/walk_forward_predictions.csv"),
    Path("data/models/walk_forward_summary.json"),
]


def _ensure_normalized_data(config: dict[str, Any]) -> None:
    lake = DataLake()
    if lake.exists("normalized", "minute_bars"):
        return
    if not lake.exists("raw", "minute_bars"):
        raise FileNotFoundError("Missing data/raw/minute_bars; import or download historical data first.")
    normalize_bars(config)


@contextmanager
def _temporary_model_artifacts():
    with tempfile.TemporaryDirectory() as temp_name:
        temp_dir = Path(temp_name)
        existing: list[tuple[Path, Path]] = []
        for path in _MODEL_ARTIFACTS:
            if path.exists():
                backup = temp_dir / path.name
                shutil.copy2(path, backup)
                existing.append((path, backup))
        try:
            yield
        finally:
            for path in _MODEL_ARTIFACTS:
                if path.exists():
                    path.unlink()
            for path, backup in existing:
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, path)


def _render_summary(summary: dict[str, Any]) -> str:
    rows = summary.get("scenarios", [])
    lines = [
        "# Event Sensitivity Backtests",
        "",
        "These scenarios do not change the main training dataset. Training-exclusion scenarios temporarily rebuild walk-forward predictions and restore the original artifacts after the run.",
        "",
        f"- Event dates: {', '.join(summary.get('event_dates', [])) or 'none'}",
        f"- Corporate action candidate dates: {', '.join(summary.get('corporate_action_candidate_dates', [])) or 'none'}",
        "",
        "| Scenario | Mode | Dates | Total Return % | Avg Monthly % | Max Drawdown % | Trades | Blocked Signals |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {scenario} | {mode} | {date_count} | {total_return_pct} | {average_monthly_return_pct} | "
            "{max_drawdown_pct} | {total_trades} | {entry_date_filter_blocked_signals} |".format(**row)
        )
    if not rows:
        lines.append("| none |  | 0 |  |  |  |  |  |")
    return "\n".join(lines) + "\n"
