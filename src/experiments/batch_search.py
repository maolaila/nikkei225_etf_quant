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
    "backtest.risk.max_implied_dispersion_bps": [15, 30, 50, 75],
    "backtest.execution.latency_bars": [0, 1, 2],
    "backtest.execution.slippage_bps": [3, 5, 10],
    "backtest.execution.fallback_spread_bps": [5, 8, 12],
}

AGGRESSIVE_PARAMETER_SPACE: dict[str, list[Any]] = {
    "model.prediction.min_confidence": [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40],
    "model.prediction.min_action_probability": [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40],
    "strategy.exit.max_holding_minutes": [30, 45, 60, 90, 120, 180],
    "strategy.exit.stop_loss_pct.long_1x": [0.5, 0.8, 1.2, 1.8, 2.5],
    "strategy.exit.stop_loss_pct.long_2x": [0.8, 1.0, 1.5, 2.2, 3.0],
    "strategy.exit.stop_loss_pct.short_1x": [0.5, 0.8, 1.2, 1.8, 2.5],
    "strategy.exit.stop_loss_pct.short_2x": [0.8, 1.0, 1.5, 2.2, 3.0],
    "strategy.exit.dynamic_holding.confidence_extend_multiplier": [1.20, 1.50, 1.80, 2.20],
    "strategy.exit.dynamic_holding.max_minutes": [90, 120, 180, 240],
    "strategy.exit.dynamic_stop_loss.high_confidence_widen_multiplier": [1.20, 1.40, 1.80, 2.20],
    "strategy.exit.dynamic_stop_loss.low_confidence_tighten_multiplier": [0.90, 1.00, 1.10],
    "strategy.exit.exit_on_neutral_signal": [False],
    "strategy.exit.exit_on_opposite_signal": [False, True],
    "backtest.position_limits.long_1x.max_equity_pct": [25, 40, 60, 75, 90],
    "backtest.position_limits.long_1x.absolute_max_equity_pct": [45, 60, 80, 100],
    "backtest.position_limits.long_2x.max_equity_pct": [25, 40, 60, 75, 90],
    "backtest.position_limits.long_2x.absolute_max_equity_pct": [45, 60, 80, 100],
    "backtest.position_limits.short_1x.max_equity_pct": [20, 35, 50, 70],
    "backtest.position_limits.short_1x.absolute_max_equity_pct": [40, 60, 80, 100],
    "backtest.position_limits.short_2x.max_equity_pct": [25, 40, 60, 80, 100],
    "backtest.position_limits.short_2x.absolute_max_equity_pct": [45, 60, 80, 100],
    "backtest.position_sizing.min_multiplier": [0.50, 0.70, 0.90, 1.10],
    "backtest.position_sizing.max_multiplier": [1.00, 1.25, 1.50, 2.00],
    "backtest.position_sizing.trend_multiplier": [1.00, 1.20, 1.50],
    "backtest.position_sizing.range_multiplier": [0.90, 1.00, 1.25],
    "backtest.position_sizing.unknown_regime_multiplier": [1.00, 1.20],
    "backtest.position_sizing.leveraged_etf_multiplier": [1.00, 1.20, 1.40],
    "backtest.risk.max_trades_per_day": [2, 4, 6, 8, 12],
    "backtest.risk.max_daily_loss_pct": [2.0, 5.0, 10.0, 20.0],
    "backtest.risk.max_consecutive_losses": [5, 10, 20],
    "backtest.risk.max_implied_dispersion_bps": [50, 75, 100, 150],
    "backtest.execution.latency_bars": [0, 1, 2],
    "backtest.execution.slippage_bps": [3, 5],
    "backtest.execution.fallback_spread_bps": [5, 8],
}

SUMMARY_COLUMNS = [
    "rank",
    "candidate_id",
    "objective",
    "score",
    "passes_target",
    "average_monthly_return_pct",
    "median_monthly_return_pct",
    "min_monthly_return_pct",
    "max_monthly_return_pct",
    "loss_month_ratio",
    "negative_month_ratio",
    "monthly_return_std_pct",
    "total_return_pct",
    "total_abs_return_pct",
    "total_loss_pct",
    "total_trades",
    "profit_factor",
    "max_drawdown_pct",
    "drawdown_loss_pct",
    "dominant_month_ratio",
    "stable_band_direction",
    "direction_month_ratio",
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
    objective: str = "profit",
    risk_profile: str = "default",
    target_monthly_loss_pct: float = 5.0,
    target_total_loss_pct: float = 5.0,
    max_total_loss_pct: float = 20.0,
    target_total_abs_return_pct: float | None = None,
    max_total_abs_return_pct: float | None = None,
    max_positive_month_ratio: float = 0.20,
    min_negative_month_ratio: float = 0.60,
    min_stable_month_ratio: float = 0.60,
    min_loss_month_ratio: float = 0.0,
    output_root: str | Path = "data/reports/experiments",
) -> tuple[pd.DataFrame, Path]:
    """Run many backtest-only parameter candidates and write a compact ranking.

    The search intentionally reuses the latest walk-forward prediction artifact.
    It varies only prediction gates, exits, risk, and sizing so AI does not spend
    tokens supervising each candidate backtest.
    """

    _assert_required_artifacts()
    objective = _objective(objective)
    risk_profile = _risk_profile(risk_profile)
    target_total_abs_return_pct = abs(float(target_total_abs_return_pct if target_total_abs_return_pct is not None else target_total_loss_pct))
    max_total_abs_return_pct = abs(float(max_total_abs_return_pct if max_total_abs_return_pct is not None else max_total_loss_pct))
    batch_dir = ensure_dir(Path(output_root) / f"batch_search_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    overlays = generate_candidate_overlays(candidates=candidates, seed=seed, risk_profile=risk_profile)
    rows: list[dict[str, Any]] = []
    ranking = pd.DataFrame()
    started_at = datetime.now().isoformat(timespec="seconds")
    summary_context = {
        "started_at": started_at,
        "target_monthly_return_pct": target_monthly_return_pct,
        "objective": objective,
        "risk_profile": risk_profile,
        "target_monthly_loss_pct": target_monthly_loss_pct,
        "target_total_loss_pct": target_total_loss_pct,
        "max_total_loss_pct": max_total_loss_pct,
        "target_total_abs_return_pct": target_total_abs_return_pct,
        "max_total_abs_return_pct": max_total_abs_return_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "max_positive_month_ratio": max_positive_month_ratio,
        "min_negative_month_ratio": min_negative_month_ratio,
        "min_stable_month_ratio": min_stable_month_ratio,
        "min_loss_month_ratio": min_loss_month_ratio,
        "requested_candidate_count": candidates,
    }

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
                objective=objective,
                target_monthly_loss_pct=target_monthly_loss_pct,
                target_total_loss_pct=target_total_loss_pct,
                max_total_loss_pct=max_total_loss_pct,
                target_total_abs_return_pct=target_total_abs_return_pct,
                max_total_abs_return_pct=max_total_abs_return_pct,
                max_positive_month_ratio=max_positive_month_ratio,
                min_negative_month_ratio=min_negative_month_ratio,
                min_stable_month_ratio=min_stable_month_ratio,
                min_loss_month_ratio=min_loss_month_ratio,
            )
        except Exception as exc:  # pragma: no cover - exercised by supervisor runs
            row = {
                "candidate_id": candidate_id,
                "objective": objective,
                "score": -1_000_000.0,
                "passes_target": False,
                "error": str(exc),
                "average_monthly_return_pct": None,
                "median_monthly_return_pct": None,
                "min_monthly_return_pct": None,
                "max_monthly_return_pct": None,
                "loss_month_ratio": None,
                "negative_month_ratio": None,
                "monthly_return_std_pct": None,
                "total_return_pct": None,
                "total_abs_return_pct": None,
                "total_loss_pct": None,
                "total_trades": None,
                "profit_factor": None,
                "max_drawdown_pct": None,
                "drawdown_loss_pct": None,
                "dominant_month_ratio": None,
                "positive_active_month_ratio": None,
                "win_rate_pct": None,
                "data_is_synthetic": None,
                "walk_forward_windows": None,
                "walk_forward_fallback_used": None,
                "report_dir": str(candidate_dir),
                **_flatten_overlay(overlay),
            }
        rows.append(row)
        ranking = _rank_candidate_rows(rows, objective=objective)
        _write_batch_outputs(
            batch_dir,
            ranking,
            completed=index == len(overlays),
            completed_candidate_count=index,
            **summary_context,
        )
    return ranking, batch_dir


def generate_candidate_overlays(*, candidates: int, seed: int = 42, risk_profile: str = "default") -> list[dict[str, Any]]:
    if candidates <= 0:
        raise ValueError("candidates must be greater than zero")

    parameter_space = _parameter_space(risk_profile)
    keys = list(parameter_space)
    rng = random.Random(seed)
    overlays: list[dict[str, Any]] = []
    seen: set[str] = set()
    attempts = 0
    max_attempts = candidates * 50
    while len(overlays) < candidates and attempts < max_attempts:
        attempts += 1
        combo = tuple(rng.choice(parameter_space[key]) for key in keys)
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


def candidate_passes_stable_loss_target(
    metrics: dict[str, Any],
    *,
    target_monthly_loss_pct: float,
    target_total_loss_pct: float,
    max_total_loss_pct: float,
    max_drawdown_pct: float,
    min_trades: int,
    max_positive_month_ratio: float,
    min_negative_month_ratio: float,
    min_loss_month_ratio: float,
    min_walk_forward_windows: int,
) -> bool:
    loss_stats = stable_loss_stats(metrics, target_monthly_loss_pct=target_monthly_loss_pct)
    total_loss = _loss_pct(metrics.get("total_return_pct", 0.0))
    drawdown_loss = _loss_pct(metrics.get("max_drawdown_pct", 0.0))
    return (
        not bool(metrics.get("data_is_synthetic", True))
        and int(metrics.get("walk_forward_windows", 0) or 0) >= min_walk_forward_windows
        and not bool(metrics.get("walk_forward_fallback_used", True))
        and int(metrics.get("total_trades", 0) or 0) >= min_trades
        and total_loss >= abs(target_total_loss_pct)
        and total_loss <= abs(max_total_loss_pct)
        and drawdown_loss <= abs(max_drawdown_pct)
        and float(metrics.get("average_monthly_return_pct", 0.0) or 0.0) < 0.0
        and float(metrics.get("median_monthly_return_pct", 0.0) or 0.0) <= 0.0
        and float(loss_stats["positive_month_ratio"]) <= max_positive_month_ratio
        and float(loss_stats["negative_month_ratio"]) >= min_negative_month_ratio
        and float(loss_stats["loss_month_ratio"]) >= min_loss_month_ratio
    )


def candidate_passes_stable_band_target(
    metrics: dict[str, Any],
    *,
    target_total_abs_return_pct: float,
    max_total_abs_return_pct: float,
    max_drawdown_pct: float,
    min_trades: int,
    min_stable_month_ratio: float,
    min_walk_forward_windows: int,
) -> bool:
    stats = stable_band_stats(metrics)
    drawdown_loss = _loss_pct(metrics.get("max_drawdown_pct", 0.0))
    direction = stable_band_direction(metrics)
    direction_ratio = stable_band_direction_month_ratio(metrics)
    return (
        not bool(metrics.get("data_is_synthetic", True))
        and int(metrics.get("walk_forward_windows", 0) or 0) >= min_walk_forward_windows
        and not bool(metrics.get("walk_forward_fallback_used", True))
        and int(metrics.get("total_trades", 0) or 0) >= min_trades
        and direction in {"positive", "negative"}
        and stable_band_return_in_range(
            metrics,
            target_total_abs_return_pct=target_total_abs_return_pct,
            max_total_abs_return_pct=max_total_abs_return_pct,
        )
        and drawdown_loss <= abs(max_drawdown_pct)
        and direction_ratio >= min_stable_month_ratio
        and float(stats["dominant_month_ratio"]) >= min_stable_month_ratio
        and float(metrics.get("average_monthly_return_pct", 0.0) or 0.0) != 0.0
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


def score_stable_loss_candidate(
    metrics: dict[str, Any],
    *,
    target_monthly_loss_pct: float,
    target_total_loss_pct: float,
    max_total_loss_pct: float,
    max_drawdown_pct: float,
    min_trades: int,
    max_positive_month_ratio: float,
    min_negative_month_ratio: float,
    min_loss_month_ratio: float,
    min_walk_forward_windows: int,
) -> float:
    target_monthly = abs(float(target_monthly_loss_pct))
    target_total = abs(float(target_total_loss_pct))
    max_total = abs(float(max_total_loss_pct))
    max_drawdown = abs(float(max_drawdown_pct))
    ideal_total_loss = min(max_total, max(target_total, target_total * 1.5))
    avg_monthly = float(metrics.get("average_monthly_return_pct", 0.0) or 0.0)
    median_monthly = float(metrics.get("median_monthly_return_pct", 0.0) or 0.0)
    max_monthly = float(metrics.get("max_monthly_return_pct", 0.0) or 0.0)
    total_loss = _loss_pct(metrics.get("total_return_pct", 0.0))
    drawdown_loss = _loss_pct(metrics.get("max_drawdown_pct", 0.0))
    trades = int(metrics.get("total_trades", 0) or 0)
    stats = stable_loss_stats(metrics, target_monthly_loss_pct=target_monthly)
    avg_loss = -avg_monthly
    median_loss = -median_monthly

    score = (
        300.0
        - abs(total_loss - ideal_total_loss) * 18.0
        - drawdown_loss * 5.0
        + min(max(avg_loss, 0.0), target_monthly) * 20.0
        + min(max(median_loss, 0.0), target_monthly) * 12.0
        + float(stats["loss_month_ratio"]) * 110.0
        + float(stats["negative_month_ratio"]) * 130.0
        - float(stats["positive_month_ratio"]) * 180.0
        - max(0.0, max_monthly) * 45.0
        - float(stats["monthly_return_std_pct"]) * 12.0
        + min(trades, max(min_trades * 2, 1)) / max(min_trades, 1) * 15.0
    )
    if bool(metrics.get("data_is_synthetic", True)):
        score -= 500.0
    if bool(metrics.get("walk_forward_fallback_used", True)):
        score -= 250.0
    if int(metrics.get("walk_forward_windows", 0) or 0) < min_walk_forward_windows:
        score -= 150.0
    if trades < min_trades:
        score -= 2_000.0 + (min_trades - trades) * 25.0
    if total_loss < target_total:
        score -= 1_000.0 + (target_total - total_loss) * 80.0
    if total_loss > max_total:
        score -= 1_000.0 + (total_loss - max_total) * 120.0
    if drawdown_loss > max_drawdown:
        score -= 1_000.0 + (drawdown_loss - max_drawdown) * 100.0
    if avg_monthly >= 0.0:
        score -= 300.0 + avg_monthly * 50.0
    if median_monthly > 0.0:
        score -= 250.0 + median_monthly * 50.0
    if float(stats["loss_month_ratio"]) < min_loss_month_ratio:
        score -= (min_loss_month_ratio - float(stats["loss_month_ratio"])) * 300.0
    if float(stats["negative_month_ratio"]) < min_negative_month_ratio:
        score -= (min_negative_month_ratio - float(stats["negative_month_ratio"])) * 350.0
    if float(stats["positive_month_ratio"]) > max_positive_month_ratio:
        score -= (float(stats["positive_month_ratio"]) - max_positive_month_ratio) * 250.0
    return float(score)


def score_stable_band_candidate(
    metrics: dict[str, Any],
    *,
    target_total_abs_return_pct: float,
    max_total_abs_return_pct: float,
    max_drawdown_pct: float,
    min_trades: int,
    min_stable_month_ratio: float,
    min_walk_forward_windows: int,
) -> float:
    target_total = abs(float(target_total_abs_return_pct))
    max_total = abs(float(max_total_abs_return_pct))
    max_drawdown = abs(float(max_drawdown_pct))
    ideal_total = min(max_total, max(target_total, target_total * 1.5))
    total_return = float(metrics.get("total_return_pct", 0.0) or 0.0)
    total_abs_return = abs(total_return)
    drawdown_loss = _loss_pct(metrics.get("max_drawdown_pct", 0.0))
    trades = int(metrics.get("total_trades", 0) or 0)
    stats = stable_band_stats(metrics)
    direction = stable_band_direction(metrics)
    direction_ratio = stable_band_direction_month_ratio(metrics)
    band_midpoint = (target_total + max_total) / 2.0
    half_width = max((max_total - target_total) / 2.0, 1e-9)
    range_fit = max(0.0, 1.0 - (abs(total_abs_return - band_midpoint) / half_width))

    score = (
        300.0
        + range_fit * 180.0
        + direction_ratio * 180.0
        - abs(total_abs_return - ideal_total) * 2.0
        - drawdown_loss * 5.0
        - float(stats["monthly_return_std_pct"]) * 12.0
        - float(stats["flat_month_ratio"]) * 50.0
        + min(trades, max(min_trades * 2, 1)) / max(min_trades, 1) * 15.0
    )
    if bool(metrics.get("data_is_synthetic", True)):
        score -= 500.0
    if bool(metrics.get("walk_forward_fallback_used", True)):
        score -= 250.0
    if int(metrics.get("walk_forward_windows", 0) or 0) < min_walk_forward_windows:
        score -= 150.0
    if trades < min_trades:
        score -= 2_000.0 + (min_trades - trades) * 25.0
    if direction not in {"positive", "negative"}:
        score -= 2_000.0
    if not stable_band_return_in_range(
        metrics,
        target_total_abs_return_pct=target_total_abs_return_pct,
        max_total_abs_return_pct=max_total_abs_return_pct,
    ):
        score -= 1_000.0
    if total_abs_return < target_total:
        score -= 1_000.0 + (target_total - total_abs_return) * 80.0
    if total_abs_return > max_total:
        score -= 1_000.0 + (total_abs_return - max_total) * 120.0
    if drawdown_loss > max_drawdown:
        score -= 1_000.0 + (drawdown_loss - max_drawdown) * 100.0
    if direction_ratio < min_stable_month_ratio:
        score -= (min_stable_month_ratio - direction_ratio) * 350.0
    return float(score)


def stable_loss_stats(metrics: dict[str, Any], *, target_monthly_loss_pct: float) -> dict[str, float]:
    returns = [
        float(row.get("return_pct", 0.0) or 0.0)
        for row in metrics.get("monthly_returns", []) or []
        if isinstance(row, dict)
    ]
    if not returns:
        return {
            "loss_month_ratio": 0.0,
            "negative_month_ratio": 0.0,
            "positive_month_ratio": 0.0,
            "monthly_return_std_pct": 0.0,
        }
    series = pd.Series(returns, dtype=float)
    target = -abs(float(target_monthly_loss_pct))
    return {
        "loss_month_ratio": float((series <= target).mean()),
        "negative_month_ratio": float((series < 0.0).mean()),
        "positive_month_ratio": float((series > 0.0).mean()),
        "monthly_return_std_pct": float(series.std(ddof=0)) if len(series) > 1 else 0.0,
    }


def stable_band_stats(metrics: dict[str, Any]) -> dict[str, float]:
    returns = [
        float(row.get("return_pct", 0.0) or 0.0)
        for row in metrics.get("monthly_returns", []) or []
        if isinstance(row, dict)
    ]
    if not returns:
        return {
            "positive_month_ratio": 0.0,
            "negative_month_ratio": 0.0,
            "flat_month_ratio": 0.0,
            "dominant_month_ratio": 0.0,
            "monthly_return_std_pct": 0.0,
        }
    series = pd.Series(returns, dtype=float)
    positive = float((series > 0.0).mean())
    negative = float((series < 0.0).mean())
    return {
        "positive_month_ratio": positive,
        "negative_month_ratio": negative,
        "flat_month_ratio": float((series == 0.0).mean()),
        "dominant_month_ratio": max(positive, negative),
        "monthly_return_std_pct": float(series.std(ddof=0)) if len(series) > 1 else 0.0,
    }


def stable_band_direction(metrics: dict[str, Any]) -> str:
    total_return = float(metrics.get("total_return_pct", 0.0) or 0.0)
    if total_return > 0.0:
        return "positive"
    if total_return < 0.0:
        return "negative"
    return ""


def stable_band_direction_month_ratio(metrics: dict[str, Any]) -> float:
    direction = stable_band_direction(metrics)
    stats = stable_band_stats(metrics)
    if direction == "positive":
        return float(stats["positive_month_ratio"])
    if direction == "negative":
        return float(stats["negative_month_ratio"])
    return 0.0


def stable_band_return_in_range(
    metrics: dict[str, Any],
    *,
    target_total_abs_return_pct: float,
    max_total_abs_return_pct: float,
) -> bool:
    total_return = float(metrics.get("total_return_pct", 0.0) or 0.0)
    min_abs = abs(float(target_total_abs_return_pct))
    max_abs = abs(float(max_total_abs_return_pct))
    if total_return > 0.0:
        return min_abs <= total_return <= max_abs
    if total_return < 0.0:
        return -max_abs <= total_return <= -min_abs
    return False


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
    objective: str,
    target_monthly_loss_pct: float,
    target_total_loss_pct: float,
    max_total_loss_pct: float,
    target_total_abs_return_pct: float,
    max_total_abs_return_pct: float,
    max_positive_month_ratio: float,
    min_negative_month_ratio: float,
    min_stable_month_ratio: float,
    min_loss_month_ratio: float,
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
    if objective == "stable-loss":
        passes = candidate_passes_stable_loss_target(
            metrics,
            target_monthly_loss_pct=target_monthly_loss_pct,
            target_total_loss_pct=target_total_loss_pct,
            max_total_loss_pct=max_total_loss_pct,
            max_drawdown_pct=max_drawdown_pct,
            min_trades=min_trades,
            max_positive_month_ratio=max_positive_month_ratio,
            min_negative_month_ratio=min_negative_month_ratio,
            min_loss_month_ratio=min_loss_month_ratio,
            min_walk_forward_windows=min_walk_forward_windows,
        )
        score = score_stable_loss_candidate(
            metrics,
            target_monthly_loss_pct=target_monthly_loss_pct,
            target_total_loss_pct=target_total_loss_pct,
            max_total_loss_pct=max_total_loss_pct,
            max_drawdown_pct=max_drawdown_pct,
            min_trades=min_trades,
            max_positive_month_ratio=max_positive_month_ratio,
            min_negative_month_ratio=min_negative_month_ratio,
            min_loss_month_ratio=min_loss_month_ratio,
            min_walk_forward_windows=min_walk_forward_windows,
        )
    elif objective == "stable-band":
        passes = candidate_passes_stable_band_target(
            metrics,
            target_total_abs_return_pct=target_total_abs_return_pct,
            max_total_abs_return_pct=max_total_abs_return_pct,
            max_drawdown_pct=max_drawdown_pct,
            min_trades=min_trades,
            min_stable_month_ratio=min_stable_month_ratio,
            min_walk_forward_windows=min_walk_forward_windows,
        )
        score = score_stable_band_candidate(
            metrics,
            target_total_abs_return_pct=target_total_abs_return_pct,
            max_total_abs_return_pct=max_total_abs_return_pct,
            max_drawdown_pct=max_drawdown_pct,
            min_trades=min_trades,
            min_stable_month_ratio=min_stable_month_ratio,
            min_walk_forward_windows=min_walk_forward_windows,
        )
    else:
        passes = candidate_passes_target(metrics, **gates)
        score = score_candidate(metrics, **gates)
    loss_stats = stable_loss_stats(metrics, target_monthly_loss_pct=target_monthly_loss_pct)
    metric_columns = {
        "candidate_id": candidate_id,
        "objective": objective,
        "score": score,
        "passes_target": passes,
        "report_dir": _display_path(candidate_dir),
    }
    for key in (
        "average_monthly_return_pct",
        "median_monthly_return_pct",
        "min_monthly_return_pct",
        "max_monthly_return_pct",
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
    metric_columns["total_loss_pct"] = _loss_pct(metrics.get("total_return_pct", 0.0))
    metric_columns["total_abs_return_pct"] = abs(float(metrics.get("total_return_pct", 0.0) or 0.0))
    metric_columns["drawdown_loss_pct"] = _loss_pct(metrics.get("max_drawdown_pct", 0.0))
    metric_columns["dominant_month_ratio"] = max(
        float(loss_stats["positive_month_ratio"]),
        float(loss_stats["negative_month_ratio"]),
    )
    metric_columns["stable_band_direction"] = stable_band_direction(metrics)
    metric_columns["direction_month_ratio"] = stable_band_direction_month_ratio(metrics)
    metric_columns.update(loss_stats)
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


def _rank_candidate_rows(rows: list[dict[str, Any]], *, objective: str) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    average_monthly_ascending = objective == "stable-loss"
    ranking = pd.DataFrame(rows).sort_values(
        ["score", "average_monthly_return_pct"],
        ascending=[False, average_monthly_ascending],
        na_position="last",
    )
    ranking.insert(0, "rank", range(1, len(ranking) + 1))
    ordered_columns = [column for column in SUMMARY_COLUMNS if column in ranking.columns]
    extra_columns = [column for column in ranking.columns if column not in ordered_columns]
    return ranking[ordered_columns + extra_columns]


def _write_batch_outputs(
    batch_dir: Path,
    ranking: pd.DataFrame,
    *,
    completed: bool,
    completed_candidate_count: int,
    started_at: str,
    requested_candidate_count: int,
    target_monthly_return_pct: float,
    objective: str,
    risk_profile: str,
    target_monthly_loss_pct: float,
    target_total_loss_pct: float,
    max_total_loss_pct: float,
    target_total_abs_return_pct: float,
    max_total_abs_return_pct: float,
    max_drawdown_pct: float,
    max_positive_month_ratio: float,
    min_negative_month_ratio: float,
    min_stable_month_ratio: float,
    min_loss_month_ratio: float,
) -> None:
    ranking.to_csv(batch_dir / "ranking.csv", index=False)
    _write_summary(
        batch_dir,
        ranking,
        completed=completed,
        completed_candidate_count=completed_candidate_count,
        started_at=started_at,
        requested_candidate_count=requested_candidate_count,
        target_monthly_return_pct=target_monthly_return_pct,
        objective=objective,
        risk_profile=risk_profile,
        target_monthly_loss_pct=target_monthly_loss_pct,
        target_total_loss_pct=target_total_loss_pct,
        max_total_loss_pct=max_total_loss_pct,
        target_total_abs_return_pct=target_total_abs_return_pct,
        max_total_abs_return_pct=max_total_abs_return_pct,
        max_drawdown_pct=max_drawdown_pct,
        max_positive_month_ratio=max_positive_month_ratio,
        min_negative_month_ratio=min_negative_month_ratio,
        min_stable_month_ratio=min_stable_month_ratio,
        min_loss_month_ratio=min_loss_month_ratio,
    )


def _write_summary(
    batch_dir: Path,
    ranking: pd.DataFrame,
    *,
    completed: bool,
    completed_candidate_count: int,
    started_at: str,
    requested_candidate_count: int,
    target_monthly_return_pct: float,
    objective: str,
    risk_profile: str,
    target_monthly_loss_pct: float,
    target_total_loss_pct: float,
    max_total_loss_pct: float,
    target_total_abs_return_pct: float,
    max_total_abs_return_pct: float,
    max_drawdown_pct: float,
    max_positive_month_ratio: float,
    min_negative_month_ratio: float,
    min_stable_month_ratio: float,
    min_loss_month_ratio: float,
) -> None:
    best = ranking.iloc[0].to_dict() if not ranking.empty else {}
    passing = ranking[ranking["passes_target"].fillna(False).astype(bool)] if "passes_target" in ranking.columns else pd.DataFrame()
    summary = {
        "created_at": started_at,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "completed": completed,
        "objective": objective,
        "risk_profile": risk_profile,
        "target_monthly_return_pct": target_monthly_return_pct,
        "target_monthly_loss_pct": target_monthly_loss_pct,
        "target_total_loss_pct": target_total_loss_pct,
        "max_total_loss_pct": max_total_loss_pct,
        "target_total_abs_return_pct": target_total_abs_return_pct,
        "max_total_abs_return_pct": max_total_abs_return_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "max_positive_month_ratio": max_positive_month_ratio,
        "min_negative_month_ratio": min_negative_month_ratio,
        "min_stable_month_ratio": min_stable_month_ratio,
        "min_loss_month_ratio": min_loss_month_ratio,
        "requested_candidate_count": int(requested_candidate_count),
        "completed_candidate_count": int(completed_candidate_count),
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
        f"- Objective: {summary['objective']}",
        f"- Risk profile: {summary['risk_profile']}",
    ]
    if summary["objective"] == "stable-band":
        lines.extend(
            [
                f"- Target total abs return pct: {summary['target_total_abs_return_pct']}",
                f"- Max total abs return pct: {summary['max_total_abs_return_pct']}",
                f"- Max drawdown pct: {summary['max_drawdown_pct']}",
                f"- Min stable month ratio: {summary['min_stable_month_ratio']}",
            ]
        )
    elif summary["objective"] == "stable-loss":
        lines.extend(
            [
                f"- Target monthly loss pct: {summary['target_monthly_loss_pct']}",
                f"- Target total loss pct: {summary['target_total_loss_pct']}",
                f"- Max total loss pct: {summary['max_total_loss_pct']}",
                f"- Max drawdown pct: {summary['max_drawdown_pct']}",
                f"- Max positive month ratio: {summary['max_positive_month_ratio']}",
                f"- Min negative month ratio: {summary['min_negative_month_ratio']}",
                f"- Min loss month ratio: {summary['min_loss_month_ratio']}",
            ]
        )
    else:
        lines.append(f"- Target monthly return pct: {summary['target_monthly_return_pct']}")
    lines.extend(
        [
            f"- Candidate count: {summary['candidate_count']}",
            f"- Passing candidates: {summary['passing_candidate_count']}",
            "",
            "## Top Candidates",
            "",
        ]
    )
    if not top:
        lines.append("No candidates were evaluated.")
        return "\n".join(lines) + "\n"
    visible = [
        "rank",
        "candidate_id",
        "score",
        "passes_target",
        "average_monthly_return_pct",
        "total_return_pct",
        "total_abs_return_pct",
        "dominant_month_ratio",
        "loss_month_ratio",
        "negative_month_ratio",
        "total_loss_pct",
        "drawdown_loss_pct",
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


def _loss_pct(value: Any) -> float:
    return max(0.0, -float(value or 0.0))


def _parameter_space(risk_profile: str) -> dict[str, list[Any]]:
    profile = _risk_profile(risk_profile)
    if profile == "aggressive":
        return AGGRESSIVE_PARAMETER_SPACE
    return PARAMETER_SPACE


def _objective(value: str) -> str:
    normalized = str(value or "profit").replace("_", "-").lower()
    if normalized not in {"profit", "stable-loss", "stable-band"}:
        raise ValueError(f"Expected objective to be profit, stable-loss, or stable-band; got {value!r}")
    return normalized


def _risk_profile(value: str) -> str:
    normalized = str(value or "default").replace("_", "-").lower()
    if normalized not in {"default", "aggressive"}:
        raise ValueError(f"Expected risk_profile to be default or aggressive; got {value!r}")
    return normalized
