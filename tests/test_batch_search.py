from __future__ import annotations

from src.experiments.batch_search import candidate_passes_target, generate_candidate_overlays, score_candidate
from src.experiments.train_until_target import TargetGates


def test_generate_candidate_overlays_are_bounded_and_nested():
    overlays = generate_candidate_overlays(candidates=5, seed=7)

    assert len(overlays) == 5
    assert "model" in overlays[0]
    assert "prediction" in overlays[0]["model"]
    assert "backtest" in overlays[0]
    assert "strategy" in overlays[0]


def test_candidate_passes_target_requires_real_walk_forward_metrics():
    metrics = {
        "data_is_synthetic": False,
        "walk_forward_windows": 6,
        "walk_forward_fallback_used": False,
        "total_trades": 50,
        "total_return_pct": 12.0,
        "average_monthly_return_pct": 3.1,
        "profit_factor": 1.25,
        "max_drawdown_pct": -10.0,
        "positive_active_month_ratio": 0.60,
        "min_monthly_return_pct": -6.0,
    }

    assert candidate_passes_target(
        metrics,
        target_monthly_return_pct=3.0,
        min_trades=50,
        min_profit_factor=1.2,
        max_drawdown_pct=15.0,
        min_positive_month_ratio=0.55,
        min_monthly_return_floor_pct=-8.0,
        min_walk_forward_windows=6,
    )

    metrics["data_is_synthetic"] = True
    assert not candidate_passes_target(
        metrics,
        target_monthly_return_pct=3.0,
        min_trades=50,
        min_profit_factor=1.2,
        max_drawdown_pct=15.0,
        min_positive_month_ratio=0.55,
        min_monthly_return_floor_pct=-8.0,
        min_walk_forward_windows=6,
    )


def test_score_penalizes_missing_sample_size():
    base = {
        "data_is_synthetic": False,
        "walk_forward_windows": 6,
        "walk_forward_fallback_used": False,
        "total_trades": 60,
        "total_return_pct": 12.0,
        "average_monthly_return_pct": 3.0,
        "profit_factor": 1.4,
        "max_drawdown_pct": -8.0,
        "positive_active_month_ratio": 0.65,
        "min_monthly_return_pct": -4.0,
    }
    low_trade = dict(base, total_trades=5)
    kwargs = {
        "target_monthly_return_pct": 3.0,
        "min_trades": 50,
        "min_profit_factor": 1.2,
        "max_drawdown_pct": 15.0,
        "min_positive_month_ratio": 0.55,
        "min_monthly_return_floor_pct": -8.0,
        "min_walk_forward_windows": 6,
    }

    assert score_candidate(base, **kwargs) > score_candidate(low_trade, **kwargs)


def test_target_gates_match_batch_search_arguments():
    gates = TargetGates(target_monthly_return_pct=2.5, min_trades=25)

    assert gates.as_kwargs()["target_monthly_return_pct"] == 2.5
    assert gates.as_kwargs()["min_trades"] == 25
