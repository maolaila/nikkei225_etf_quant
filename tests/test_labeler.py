from __future__ import annotations

from src.labeling.future_return_labeler import action_from_future_return, apply_cost_awareness


CONFIG = {
    "labeling": {
        "thresholds": {
            "weak_long_return_pct": 0.20,
            "strong_long_return_pct": 0.45,
            "weak_short_return_pct": -0.20,
            "strong_short_return_pct": -0.45,
            "neutral_abs_return_pct": 0.15,
        }
    },
    "cost_aware_labeling": {
        "enabled": True,
        "estimated_round_trip_cost_pct": {"long_1x": 0.10, "long_2x": 0.16, "short_1x": 0.12, "short_2x": 0.20},
    },
}


def test_future_return_label_thresholds_and_costs():
    assert action_from_future_return(0.50, CONFIG) == "long_2x"
    assert action_from_future_return(-0.50, CONFIG) == "short_2x"
    assert action_from_future_return(0.05, CONFIG) == "flat"
    assert apply_cost_awareness("long_1x", 0.08, CONFIG) == "flat"

