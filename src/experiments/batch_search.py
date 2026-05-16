from __future__ import annotations

import random
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from src.backtest.engine import run_backtest
from src.config.loader import deep_merge
from src.data.data_lake import DataLake
from src.utils.paths import ensure_dir
from src.utils.serialization import write_json


PARAMETER_SPACE: dict[str, list[Any]] = {
    "model.prediction.min_confidence": [0.25, 0.30, 0.35, 0.40, 0.45, 0.50],
    "model.prediction.min_action_probability": [0.25, 0.30, 0.35, 0.40, 0.45, 0.50],
    "strategy.exit.max_holding_minutes": [20, 30, 45, 60, 90],
    "strategy.exit.stop_loss_pct.long_2x": [0.5, 0.8, 1.0, 1.2, 1.5],
    "strategy.exit.stop_loss_pct.short_2x": [0.5, 0.8, 1.0, 1.2, 1.5],
    "strategy.exit.dynamic_holding.confidence_extend_multiplier": [1.20, 1.35, 1.50],
    "strategy.exit.dynamic_holding.max_minutes": [90, 120, 150],
    "strategy.exit.dynamic_stop_loss.high_confidence_widen_multiplier": [1.10, 1.25, 1.40],
    "strategy.exit.exit_on_neutral_signal": [False, True],
    "strategy.exit.exit_on_opposite_signal": [True],
    "backtest.position_limits.long_2x.max_equity_pct": [15, 25, 35, 45],
    "backtest.position_limits.short_2x.max_equity_pct": [15, 25, 30, 40],
    "backtest.position_limits.long_2x.absolute_max_equity_pct": [35, 45, 50],
    "backtest.position_limits.short_2x.absolute_max_equity_pct": [30, 40, 45],
    "backtest.position_sizing.max_multiplier": [1.00, 1.15, 1.25],
    "backtest.position_sizing.trend_multiplier": [1.00, 1.10, 1.20],
    "backtest.risk.max_trades_per_day": [1, 2, 3, 4],
    "backtest.risk.max_daily_loss_pct": [0.8, 1.2, 1.5, 2.0],
    "backtest.risk.max_consecutive_losses": [1, 2, 3],
}

SUMMARY_COLUMNS = [
    "rank",
    "candidate_id",
    "score",
    "passes_target",
    "average_monthly_return_pct",
    "median_monthly_return_pct",
    "min_monthly_return_pct",
    "total_return_pct",
    "total_trades",
    "profit_factor",
    "max_drawdown_pct",
    "positive_active_month_ratio",
    "win_rate_pct",
    "data_is_synthetic",
    "walk_forward_windows",
    "walk_forward_fallback_used",
    "report_dir",
]


def run_batch_search(
    config: dict[str, Any],
    *,
    candidates: int = 24,
    seed: int = 42,
    target_monthly_return_pct: float = 3.0,
    min_trades: int = 50,
    min_profit_factor: float = 1.2,
    max_drawdown_pct: float = 15.0,
    min_positive_month_ratio: float = 0.55,
    min_monthly_return_floor_pct: float = -8.0,
    min_walk_forward_windows: int = 6,
    output_root: str | Path = "data/reports/experiments",
) -> tuple[pd.DataFrame, Path]:
    """Run many backtest-only parameter candidates and write a compact ranking.

    The search intentionally reuses the latest walk-forward prediction artifact.
    It varies only prediction gates, exits, risk, and sizing so AI does not spend
    tokens supervising each candidate backtest.
    """

    _assert_required_artifacts()
    batch_dir = ensure_dir(Path(output_root) / f"batch_search_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    overlays = generate_candidate_overlays(candidates=candidates, seed=seed)
    rows: list[dict[str, Any]] = []

    for index, overlay in enumerate(overlays, start=1):
        candidate_id = f"candidate_{index:03d}"
        candidate_dir = batch_dir / candidate_id
        candidate_config = _candidate_config(config, overlay, candidate_dir)
        _write_candidate_files(candidate_dir, overlay, candidate_config)
        try:
            metrics, _, _, _ = run_backtest(candidate_config, model_name="latest")
            row = _metrics_row(
                candidate_id=candidate_id,
                candidate_dir=candidate_dir,
                overlay=overlay,
                metrics=metrics,
                target_monthly_return_pct=target_monthly_return_pct,
                min_trades=min_trades,
                min_profit_factor=min_profit_factor,
                max_drawdown_pct=max_drawdown_pct,
                min_positive_month_ratio=min_positive_month_ratio,
                min_monthly_return_floor_pct=min_monthly_return_floor_pct,
                min_walk_forward_windows=min_walk_forward_windows,
            )
        except Exception as exc:  # pragma: no cover - exercised by supervisor runs
            row = {
                "candidate_id": candidate_id,
                "score": -1_000_000.0,
                "passes_target": False,
                "error": str(exc),
                "average_monthly_return_pct": None,
                "median_monthly_return_pct": None,
                "min_monthly_return_pct": None,
                "total_return_pct": None,
                "total_trades": None,
                "profit_factor": None,
                "max_drawdown_pct": None,
                "positive_active_month_ratio": None,
                "win_rate_pct": None,
                "data_is_synthetic": None,
                "walk_forward_windows": None,
                "walk_forward_fallback_used": None,
                "report_dir": str(candidate_dir),
                **_flatten_overlay(overlay),
            }
        rows.append(row)

    ranking = pd.DataFrame(rows).sort_values(["score", "average_monthly_return_pct"], ascending=[False, False], na_position="last")
    ranking.insert(0, "rank", range(1, len(ranking) + 1))
    ordered_columns = [column for column in SUMMARY_COLUMNS if column in ranking.columns]
    extra_columns = [column for column in ranking.columns if column not in ordered_columns]
    ranking = ranking[ordered_columns + extra_columns]
    ranking.to_csv(batch_dir / "ranking.csv", index=False)
    _write_summary(batch_dir, ranking, target_monthly_return_pct)
    return ranking, batch_dir


def generate_candidate_overlays(*, candidates: int, seed: int = 42) -> list[dict[str, Any]]:
    if candidates <= 0:
        raise ValueError("candidates must be greater than zero")

    keys = list(PARAMETER_SPACE)
    rng = random.Random(seed)
    overlays: list[dict[str, Any]] = []
    seen: set[str] = set()
    attempts = 0
    max_attempts = candidates * 50
    while len(overlays) < candidates and attempts < max_attempts:
        attempts += 1
        combo = tuple(rng.choice(PARAMETER_SPACE[key]) for key in keys)
        signature = repr(combo)
        if signature not in seen:
            seen.add(signature)
            overlays.append(_overlay_from_combo(keys, combo))
    if len(overlays) < candidates:
        raise RuntimeError(f"Only generated {len(overlays)} unique candidates out of requested {candidates}")
    return overlays


def candidate_passes_target(
    metrics: dict[str, Any],
    *,
    target_monthly_return_pct: float,
    min_trades: int,
    min_profit_factor: float,
    max_drawdown_pct: float,
    min_positive_month_ratio: float,
    min_monthly_return_floor_pct: float,
    min_walk_forward_windows: int,
) -> bool:
    return (
        not bool(metrics.get("data_is_synthetic", True))
        and int(metrics.get("walk_forward_windows", 0) or 0) >= min_walk_forward_windows
        and not bool(metrics.get("walk_forward_fallback_used", True))
        and int(metrics.get("total_trades", 0) or 0) >= min_trades
        and float(metrics.get("total_return_pct", 0.0) or 0.0) > 0.0
        and float(metrics.get("average_monthly_return_pct", 0.0) or 0.0) >= target_monthly_return_pct
        and float(metrics.get("profit_factor", 0.0) or 0.0) >= min_profit_factor
        and float(metrics.get("max_drawdown_pct", 0.0) or 0.0) >= -abs(max_drawdown_pct)
        and float(metrics.get("positive_active_month_ratio", 0.0) or 0.0) >= min_positive_month_ratio
        and float(metrics.get("min_monthly_return_pct", 0.0) or 0.0) >= min_monthly_return_floor_pct
    )


def score_candidate(
    metrics: dict[str, Any],
    *,
    target_monthly_return_pct: float,
    min_trades: int,
    min_profit_factor: float,
    max_drawdown_pct: float,
    min_positive_month_ratio: float,
    min_monthly_return_floor_pct: float,
    min_walk_forward_windows: int,
) -> float:
    avg_monthly = float(metrics.get("average_monthly_return_pct", 0.0) or 0.0)
    min_monthly = float(metrics.get("min_monthly_return_pct", 0.0) or 0.0)
    total_return = float(metrics.get("total_return_pct", 0.0) or 0.0)
    trades = int(metrics.get("total_trades", 0) or 0)
    profit_factor = float(metrics.get("profit_factor", 0.0) or 0.0)
    drawdown = float(metrics.get("max_drawdown_pct", 0.0) or 0.0)
    positive_ratio = float(metrics.get("positive_active_month_ratio", 0.0) or 0.0)

    score = (
        avg_monthly * 40.0
        + min_monthly * 5.0
        + total_return * 1.5
        + min(profit_factor, 5.0) * 20.0
        + positive_ratio * 60.0
        + drawdown * 2.0
        + min(trades, min_trades * 2) / max(min_trades, 1) * 10.0
    )
    if bool(metrics.get("data_is_synthetic", True)):
        score -= 500.0
    if bool(metrics.get("walk_forward_fallback_used", True)):
        score -= 250.0
    if int(metrics.get("walk_forward_windows", 0) or 0) < min_walk_forward_windows:
        score -= 150.0
    if trades < min_trades:
        score -= 5_000.0 + (min_trades - trades) * 50.0
    if avg_monthly < target_monthly_return_pct:
        score -= (target_monthly_return_pct - avg_monthly) * 30.0
    if profit_factor < min_profit_factor:
        score -= (min_profit_factor - profit_factor) * 40.0
    if drawdown < -abs(max_drawdown_pct):
        score -= (abs(drawdown) - abs(max_drawdown_pct)) * 10.0
    if positive_ratio < min_positive_month_ratio:
        score -= (min_positive_month_ratio - positive_ratio) * 80.0
    if min_monthly < min_monthly_return_floor_pct:
        score -= (min_monthly_return_floor_pct - min_monthly) * 15.0
    return float(score)


def _assert_required_artifacts() -> None:
    lake = DataLake()
    missing = [
        f"{layer}/{name}"
        for layer, name in (("normalized", "minute_bars"), ("models", "walk_forward_predictions"))
        if not lake.exists(layer, name)
    ]
    if missing:
        raise FileNotFoundError(
            "batch-search requires existing normalized minute bars and walk-forward predictions. "
            f"Missing: {', '.join(missing)}. Run normalize-data/build-features/build-labels/walk-forward first."
        )


def _candidate_config(config: dict[str, Any], overlay: dict[str, Any], candidate_dir: Path) -> dict[str, Any]:
    report_overlay = {"backtest": {"report": {"output_dir": str(candidate_dir)}}}
    return deep_merge(deep_merge(deepcopy(config), overlay), report_overlay)


def _overlay_from_combo(keys: list[str], combo: tuple[Any, ...]) -> dict[str, Any]:
    overlay: dict[str, Any] = {}
    for key, value in zip(keys, combo, strict=True):
        _set_dotted(overlay, key, value)
    return overlay


def _set_dotted(target: dict[str, Any], key: str, value: Any) -> None:
    parts = key.split(".")
    cursor = target
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = value


def _flatten_overlay(overlay: dict[str, Any]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}

    def visit(prefix: str, node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                visit(f"{prefix}.{key}" if prefix else str(key), value)
        else:
            flattened[f"param.{prefix}"] = node

    visit("", overlay)
    return flattened


def _metrics_row(
    *,
    candidate_id: str,
    candidate_dir: Path,
    overlay: dict[str, Any],
    metrics: dict[str, Any],
    target_monthly_return_pct: float,
    min_trades: int,
    min_profit_factor: float,
    max_drawdown_pct: float,
    min_positive_month_ratio: float,
    min_monthly_return_floor_pct: float,
    min_walk_forward_windows: int,
) -> dict[str, Any]:
    gates = {
        "target_monthly_return_pct": target_monthly_return_pct,
        "min_trades": min_trades,
        "min_profit_factor": min_profit_factor,
        "max_drawdown_pct": max_drawdown_pct,
        "min_positive_month_ratio": min_positive_month_ratio,
        "min_monthly_return_floor_pct": min_monthly_return_floor_pct,
        "min_walk_forward_windows": min_walk_forward_windows,
    }
    passes = candidate_passes_target(metrics, **gates)
    score = score_candidate(metrics, **gates)
    metric_columns = {
        "candidate_id": candidate_id,
        "score": score,
        "passes_target": passes,
        "report_dir": _display_path(candidate_dir),
    }
    for key in (
        "average_monthly_return_pct",
        "median_monthly_return_pct",
        "min_monthly_return_pct",
        "total_return_pct",
        "total_trades",
        "profit_factor",
        "max_drawdown_pct",
        "positive_active_month_ratio",
        "win_rate_pct",
        "data_is_synthetic",
        "walk_forward_windows",
        "walk_forward_fallback_used",
        "total_commission_jpy",
        "average_execution_cost_bps",
    ):
        metric_columns[key] = metrics.get(key)
    return {**metric_columns, **_flatten_overlay(overlay)}


def _write_candidate_files(candidate_dir: Path, overlay: dict[str, Any], candidate_config: dict[str, Any]) -> None:
    candidate_dir.mkdir(parents=True, exist_ok=True)
    (candidate_dir / "candidate_overlay.yaml").write_text(yaml.safe_dump(overlay, sort_keys=False), encoding="utf-8")
    (candidate_dir / "effective_config.yaml").write_text(yaml.safe_dump(candidate_config, sort_keys=False), encoding="utf-8")


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def _write_summary(batch_dir: Path, ranking: pd.DataFrame, target_monthly_return_pct: float) -> None:
    best = ranking.iloc[0].to_dict() if not ranking.empty else {}
    passing = ranking[ranking["passes_target"].fillna(False).astype(bool)] if "passes_target" in ranking.columns else pd.DataFrame()
    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "target_monthly_return_pct": target_monthly_return_pct,
        "candidate_count": int(len(ranking)),
        "passing_candidate_count": int(len(passing)),
        "best_candidate": best,
        "ranking_csv": str(batch_dir / "ranking.csv"),
    }
    write_json(batch_dir / "summary.json", summary)
    (batch_dir / "report.md").write_text(_render_markdown(summary, ranking), encoding="utf-8")


def _render_markdown(summary: dict[str, Any], ranking: pd.DataFrame) -> str:
    top = ranking.head(10).to_dict(orient="records") if not ranking.empty else []
    lines = [
        "# Batch Strategy Search",
        "",
        "This local run reused the latest walk-forward predictions and varied backtest-only candidate parameters.",
        "",
        f"- Target monthly return pct: {summary['target_monthly_return_pct']}",
        f"- Candidate count: {summary['candidate_count']}",
        f"- Passing candidates: {summary['passing_candidate_count']}",
        "",
        "## Top Candidates",
        "",
    ]
    if not top:
        lines.append("No candidates were evaluated.")
        return "\n".join(lines) + "\n"
    visible = [
        "rank",
        "candidate_id",
        "score",
        "passes_target",
        "average_monthly_return_pct",
        "total_trades",
        "profit_factor",
        "max_drawdown_pct",
        "positive_active_month_ratio",
        "report_dir",
    ]
    lines.append("| " + " | ".join(visible) + " |")
    lines.append("| " + " | ".join(["---"] * len(visible)) + " |")
    for row in top:
        lines.append("| " + " | ".join(_markdown_value(row.get(column, "")) for column in visible) + " |")
    return "\n".join(lines) + "\n"


def _markdown_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)
