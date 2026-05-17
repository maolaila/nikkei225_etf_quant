from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.backtest.engine import run_backtest
from src.config.loader import deep_merge
from src.data.cleaner import normalize_bars
from src.data.data_lake import DataLake
from src.experiments.batch_search import candidate_passes_target, run_batch_search
from src.experiments.event_sensitivity import run_event_sensitivity_backtests
from src.features.feature_pipeline import build_features
from src.labeling.future_return_labeler import build_labels
from src.utils.paths import ensure_dir
from src.utils.serialization import write_json
from src.validation.event_audit import run_event_audit
from src.validation.walk_forward import run_walk_forward


GENERATED_ARTIFACTS = [
    ("features", "features.parquet"),
    ("features", "features.csv"),
    ("labels", "labels.parquet"),
    ("labels", "labels.csv"),
    ("models", "walk_forward_predictions.parquet"),
    ("models", "walk_forward_predictions.csv"),
    ("models", "walk_forward_summary.json"),
]


@dataclass(frozen=True)
class TargetGates:
    target_monthly_return_pct: float = 3.0
    min_trades: int = 50
    min_profit_factor: float = 1.2
    max_drawdown_pct: float = 15.0
    min_positive_month_ratio: float = 0.55
    min_monthly_return_floor_pct: float = -8.0
    min_walk_forward_windows: int = 6

    def as_kwargs(self) -> dict[str, Any]:
        return {
            "target_monthly_return_pct": self.target_monthly_return_pct,
            "min_trades": self.min_trades,
            "min_profit_factor": self.min_profit_factor,
            "max_drawdown_pct": self.max_drawdown_pct,
            "min_positive_month_ratio": self.min_positive_month_ratio,
            "min_monthly_return_floor_pct": self.min_monthly_return_floor_pct,
            "min_walk_forward_windows": self.min_walk_forward_windows,
        }


def run_train_until_target(
    config: dict[str, Any],
    *,
    max_cycles: int = 100,
    candidates_per_cycle: int = 48,
    seed: int = 42,
    force_rebuild: bool = False,
    model_name: str | None = None,
    output_root: str | Path = "data/reports/long_run",
    gates: TargetGates | None = None,
    run_event_sensitivity: bool = True,
) -> dict[str, Any]:
    gates = gates or TargetGates()
    run_dir = ensure_dir(Path(output_root) / f"train_until_target_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    status_path = run_dir / "status.json"
    report_path = run_dir / "report.md"

    if force_rebuild:
        invalidate_training_artifacts()

    _write_status(status_path, {"status": "building_pipeline", "run_dir": str(run_dir), "cycles": []})
    pipeline_summary = _ensure_training_pipeline(config, model_name=model_name)
    baseline_metrics, _, _, _ = run_backtest(_with_report_dir(config, run_dir / "baseline"), model_name="latest")
    baseline_passes = candidate_passes_target(baseline_metrics, **gates.as_kwargs())
    event_sensitivity_summary = (
        run_event_sensitivity_backtests(config, output_root=run_dir / "event_sensitivity", model_name="latest")
        if run_event_sensitivity and pipeline_summary.get("event_audit")
        else {}
    )
    cycles: list[dict[str, Any]] = []
    best_row: dict[str, Any] | None = None
    passing_row: dict[str, Any] | None = None

    if baseline_passes:
        passing_row = {"candidate_id": "baseline", "report_dir": str(run_dir / "baseline"), **baseline_metrics}

    cycle = 0
    while passing_row is None and (max_cycles <= 0 or cycle < max_cycles):
        cycle += 1
        cycle_output_root = run_dir / "cycles" / f"cycle_{cycle:03d}"
        ranking, batch_dir = run_batch_search(
            config,
            candidates=candidates_per_cycle,
            seed=seed + cycle - 1,
            output_root=cycle_output_root,
            **gates.as_kwargs(),
        )
        cycle_best = ranking.iloc[0].to_dict() if not ranking.empty else {}
        if best_row is None or float(cycle_best.get("score", -1_000_000.0) or -1_000_000.0) > float(best_row.get("score", -1_000_000.0) or -1_000_000.0):
            best_row = cycle_best
        passing = ranking[ranking["passes_target"].fillna(False).astype(bool)] if "passes_target" in ranking else pd.DataFrame()
        if not passing.empty:
            passing_row = passing.iloc[0].to_dict()
        cycle_summary = {
            "cycle": cycle,
            "batch_dir": str(batch_dir),
            "best_candidate": cycle_best,
            "passing_candidate_count": int(len(passing)),
        }
        cycles.append(cycle_summary)
        _write_status(
            status_path,
            {
                "status": "target_met" if passing_row is not None else "running",
                "run_dir": str(run_dir),
                "pipeline": pipeline_summary,
                "baseline_passes": baseline_passes,
                "baseline_metrics": baseline_metrics,
                "event_sensitivity": event_sensitivity_summary,
                "best_candidate": best_row,
                "passing_candidate": passing_row,
                "cycles": cycles,
                "gates": gates.as_kwargs(),
            },
        )

    final_status = "target_met" if passing_row is not None else "max_cycles_reached"
    result = {
        "status": final_status,
        "run_dir": str(run_dir),
        "status_path": str(status_path),
        "report_path": str(report_path),
        "pipeline": pipeline_summary,
        "baseline_passes": baseline_passes,
        "baseline_metrics": baseline_metrics,
        "event_sensitivity": event_sensitivity_summary,
        "best_candidate": best_row,
        "passing_candidate": passing_row,
        "cycles": cycles,
        "gates": gates.as_kwargs(),
    }
    _write_status(status_path, result)
    report_path.write_text(_render_report(result), encoding="utf-8")
    return result


def invalidate_training_artifacts(root: str | Path = "data") -> list[Path]:
    data_root = Path(root)
    removed: list[Path] = []
    for layer, filename in GENERATED_ARTIFACTS:
        path = data_root / layer / filename
        if path.exists():
            path.unlink()
            removed.append(path)
    backtest_dir = data_root / "reports" / "backtest"
    if backtest_dir.exists():
        shutil.rmtree(backtest_dir)
        removed.append(backtest_dir)
    return removed


def _ensure_training_pipeline(config: dict[str, Any], *, model_name: str | None) -> dict[str, Any]:
    lake = DataLake()
    if not lake.exists("raw", "minute_bars"):
        raise FileNotFoundError("Missing data/raw/minute_bars; import or download historical data first.")
    if not lake.exists("normalized", "minute_bars"):
        normalize_bars(config)
    event_audit_summary: dict[str, Any] | None = None
    if bool(config.get("historical", {}).get("event_audit", {}).get("enabled", True)):
        event_audit = run_event_audit(config)
        event_audit_summary = {**event_audit.summary, "output_dir": str(event_audit.output_dir)}
    features, features_path = build_features(config)
    labels, labels_path = build_labels(config)
    predictions, summary = run_walk_forward(config, model_name=model_name)
    return {
        "event_audit": event_audit_summary,
        "features_rows": int(len(features)),
        "features_path": features_path,
        "labels_rows": int(len(labels)),
        "labels_path": labels_path,
        "prediction_rows": int(len(predictions)),
        "walk_forward_summary": summary,
    }


def _with_report_dir(config: dict[str, Any], report_dir: Path) -> dict[str, Any]:
    return deep_merge(config, {"backtest": {"report": {"output_dir": str(report_dir)}}})


def _write_status(path: Path, payload: dict[str, Any]) -> None:
    write_json(path, payload)


def _render_report(result: dict[str, Any]) -> str:
    best = result.get("best_candidate") or {}
    passing = result.get("passing_candidate") or {}
    lines = [
        "# Train Until Target",
        "",
        f"- Status: {result.get('status')}",
        f"- Cycles: {len(result.get('cycles', []))}",
        f"- Run dir: {result.get('run_dir')}",
        f"- Baseline passes: {result.get('baseline_passes')}",
        "",
        "## Gates",
        "",
    ]
    for key, value in (result.get("gates") or {}).items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Best Candidate", ""])
    if best:
        for key in ("rank", "candidate_id", "score", "passes_target", "average_monthly_return_pct", "total_return_pct", "total_trades", "profit_factor", "max_drawdown_pct", "report_dir"):
            lines.append(f"- {key}: {best.get(key)}")
    else:
        lines.append("No searched candidate was evaluated.")
    event_audit = (result.get("pipeline") or {}).get("event_audit") or {}
    if event_audit:
        lines.extend(
            [
                "",
                "## Event Audit",
                "",
                f"- Output dir: {event_audit.get('output_dir')}",
                f"- Market event dates: {', '.join(event_audit.get('event_dates', [])) or 'none'}",
                f"- Corporate action candidate dates: {', '.join(event_audit.get('corporate_action_candidate_dates', [])) or 'none'}",
                f"- Abnormal minute bars: {event_audit.get('abnormal_minute_bar_count', 0)}",
            ]
        )
    event_sensitivity = result.get("event_sensitivity") or {}
    if event_sensitivity:
        lines.extend(
            [
                "",
                "## Event Sensitivity",
                "",
                f"- Output root: {event_sensitivity.get('output_root')}",
            ]
        )
        for row in event_sensitivity.get("scenarios", []):
            lines.append(
                f"- {row.get('scenario')}: total_return_pct={row.get('total_return_pct')}, "
                f"trades={row.get('total_trades')}, blocked_signals={row.get('entry_date_filter_blocked_signals')}"
            )
    lines.extend(["", "## Passing Candidate", ""])
    if passing:
        for key in ("rank", "candidate_id", "score", "average_monthly_return_pct", "total_return_pct", "total_trades", "profit_factor", "max_drawdown_pct", "report_dir"):
            lines.append(f"- {key}: {passing.get(key)}")
    else:
        lines.append("No candidate met all gates.")
    return "\n".join(lines) + "\n"
