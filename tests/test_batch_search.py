from __future__ import annotations

from pathlib import Path

from src.experiments import batch_search
from src.experiments.batch_search import candidate_passes_stable_loss_target
from src.experiments.batch_search import candidate_passes_stable_band_target
from src.experiments.batch_search import candidate_passes_target
from src.experiments.batch_search import generate_candidate_overlays
from src.experiments.batch_search import score_candidate
from src.experiments.batch_search import score_stable_loss_candidate
from src.experiments.batch_search import score_stable_band_candidate
from src.experiments.batch_search import stable_loss_stats
from src.experiments.train_until_target import TargetGates


def test_generate_candidate_overlays_are_bounded_and_nested():
    overlays = generate_candidate_overlays(candidates=5, seed=7)

    assert len(overlays) == 5
    assert "model" in overlays[0]
    assert "prediction" in overlays[0]["model"]
    assert "backtest" in overlays[0]
    assert "strategy" in overlays[0]


def test_generate_candidate_overlays_support_aggressive_risk_profile():
    overlays = generate_candidate_overlays(candidates=3, seed=11, risk_profile="aggressive")

    assert len(overlays) == 3
    first = overlays[0]
    assert first["backtest"]["risk"]["max_trades_per_day"] >= 2
    assert "long_1x" in first["backtest"]["position_limits"]
    assert "short_1x" in first["backtest"]["position_limits"]
    assert first["backtest"]["position_limits"]["long_2x"]["max_equity_pct"] >= 25


def test_candidate_passes_target_requires_real_walk_forward_metrics():
    metrics = {
        "data_is_synthetic": False,
        "data_providers": ["jquants"],
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
        "data_providers": ["jquants"],
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


def test_stable_loss_objective_prefers_bounded_consistent_losses():
    bounded_loss = {
        "data_is_synthetic": False,
        "data_providers": ["jquants"],
        "walk_forward_windows": 6,
        "walk_forward_fallback_used": False,
        "total_trades": 80,
        "total_return_pct": -8.0,
        "average_monthly_return_pct": -1.2,
        "median_monthly_return_pct": -0.9,
        "max_monthly_return_pct": 0.0,
        "max_drawdown_pct": -9.0,
        "monthly_returns": [
            {"return_pct": -5.2},
            {"return_pct": -0.6},
            {"return_pct": -0.8},
            {"return_pct": -0.4},
            {"return_pct": -0.9},
            {"return_pct": 0.0},
        ],
    }
    too_deep_loss = {
        **bounded_loss,
        "total_return_pct": -42.0,
        "average_monthly_return_pct": -5.5,
        "median_monthly_return_pct": -5.1,
        "max_drawdown_pct": -45.0,
        "monthly_returns": [{"return_pct": value} for value in [-5.2, -6.0, -5.6, -4.8, -6.4, -5.1]],
    }
    shallow_loss = {
        **bounded_loss,
        "total_return_pct": -3.0,
        "average_monthly_return_pct": -0.3,
        "median_monthly_return_pct": -0.2,
        "max_monthly_return_pct": 0.5,
        "max_drawdown_pct": -3.5,
        "monthly_returns": [{"return_pct": value} for value in [-0.4, -0.5, 0.5, -0.2, 0.0, -0.1]],
    }
    kwargs = {
        "target_monthly_loss_pct": 5.0,
        "target_total_loss_pct": 5.0,
        "max_total_loss_pct": 20.0,
        "max_drawdown_pct": 20.0,
        "min_trades": 50,
        "max_positive_month_ratio": 0.20,
        "min_negative_month_ratio": 0.60,
        "min_loss_month_ratio": 0.10,
        "min_walk_forward_windows": 6,
    }

    assert candidate_passes_stable_loss_target(bounded_loss, **kwargs)
    assert not candidate_passes_stable_loss_target(too_deep_loss, **kwargs)
    assert not candidate_passes_stable_loss_target(shallow_loss, **kwargs)
    assert score_stable_loss_candidate(bounded_loss, **kwargs) > score_stable_loss_candidate(too_deep_loss, **kwargs)
    assert score_stable_loss_candidate(bounded_loss, **kwargs) > score_stable_loss_candidate(shallow_loss, **kwargs)
    assert stable_loss_stats(bounded_loss, target_monthly_loss_pct=5.0)["loss_month_ratio"] >= 0.10


def test_stable_band_objective_accepts_profit_or_loss_inside_band():
    stable_profit = {
        "data_is_synthetic": False,
        "data_providers": ["jquants"],
        "walk_forward_windows": 6,
        "walk_forward_fallback_used": False,
        "total_trades": 80,
        "total_return_pct": 8.0,
        "average_monthly_return_pct": 1.2,
        "max_drawdown_pct": -6.0,
        "monthly_returns": [{"return_pct": value} for value in [1.0, 1.2, 0.8, 0.0, 1.1, 0.6]],
    }
    stable_loss = {
        **stable_profit,
        "total_return_pct": -8.0,
        "average_monthly_return_pct": -1.2,
        "monthly_returns": [{"return_pct": value} for value in [-1.0, -1.2, -0.8, 0.0, -1.1, -0.6]],
    }
    too_large = {**stable_profit, "total_return_pct": 35.0}
    unstable = {
        **stable_profit,
        "monthly_returns": [{"return_pct": value} for value in [4.0, -3.8, 3.5, -3.4, 4.1, -3.9]],
    }
    kwargs = {
        "target_total_abs_return_pct": 5.0,
        "max_total_abs_return_pct": 20.0,
        "max_drawdown_pct": 20.0,
        "min_trades": 50,
        "min_stable_month_ratio": 0.60,
        "min_walk_forward_windows": 6,
    }

    assert candidate_passes_stable_band_target(stable_profit, **kwargs)
    assert candidate_passes_stable_band_target(stable_loss, **kwargs)
    assert not candidate_passes_stable_band_target(too_large, **kwargs)
    assert not candidate_passes_stable_band_target(unstable, **kwargs)
    assert score_stable_band_candidate(stable_profit, **kwargs) > score_stable_band_candidate(too_large, **kwargs)


def test_stable_band_rejects_synthetic_provider_even_when_flag_is_false():
    metrics = {
        "data_is_synthetic": False,
        "data_providers": ["jquants"],
        "walk_forward_windows": 6,
        "walk_forward_fallback_used": False,
        "total_trades": 80,
        "total_return_pct": -8.0,
        "average_monthly_return_pct": -1.2,
        "max_drawdown_pct": -6.0,
        "monthly_returns": [{"return_pct": value} for value in [-1.0, -1.2, -0.8, 0.0, -1.1, -0.6]],
    }
    kwargs = {
        "target_total_abs_return_pct": 5.0,
        "max_total_abs_return_pct": 20.0,
        "max_drawdown_pct": 20.0,
        "min_trades": 50,
        "min_stable_month_ratio": 0.60,
        "min_walk_forward_windows": 6,
    }

    assert candidate_passes_stable_band_target(metrics, **kwargs)
    assert not candidate_passes_stable_band_target({**metrics, "data_providers": ["synthetic"]}, **kwargs)
    assert not candidate_passes_stable_band_target({**metrics, "data_providers": ["jquants", "synthetic_csv"]}, **kwargs)
    assert not candidate_passes_stable_band_target({**metrics, "data_providers": []}, **kwargs)


def test_target_gates_match_batch_search_arguments():
    gates = TargetGates(target_monthly_return_pct=2.5, min_trades=25)

    assert gates.as_kwargs()["target_monthly_return_pct"] == 2.5
    assert gates.as_kwargs()["min_trades"] == 25


def test_batch_search_writes_progress_summary_after_each_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(batch_search, "_assert_required_artifacts", lambda: None)

    summary_payloads = []
    real_write_json = batch_search.write_json

    def record_write_json(path, payload):
        if Path(path).name == "summary.json":
            summary_payloads.append(dict(payload))
        real_write_json(path, payload)

    def fake_run_backtest(config, model_name):
        report_dir = config["backtest"]["report"]["output_dir"]
        metrics = {
            "data_is_synthetic": False,
            "data_providers": ["jquants"],
            "walk_forward_windows": 6,
            "walk_forward_fallback_used": False,
            "total_trades": 60,
            "total_return_pct": 1.0,
            "average_monthly_return_pct": 0.2,
            "median_monthly_return_pct": 0.1,
            "min_monthly_return_pct": -0.5,
            "max_monthly_return_pct": 0.6,
            "profit_factor": 1.1,
            "max_drawdown_pct": -2.0,
            "positive_active_month_ratio": 0.5,
            "win_rate_pct": 52.0,
        }
        return metrics | {"report_dir": report_dir}, None, None, None

    monkeypatch.setattr(batch_search, "write_json", record_write_json)
    monkeypatch.setattr(batch_search, "run_backtest", fake_run_backtest)

    ranking, batch_dir = batch_search.run_batch_search({}, candidates=2, output_root=tmp_path)

    assert len(ranking) == 2
    assert (batch_dir / "ranking.csv").exists()
    assert (batch_dir / "summary.json").exists()
    assert [payload["completed"] for payload in summary_payloads] == [False, True]
    assert summary_payloads[-1]["requested_candidate_count"] == 2
    assert summary_payloads[-1]["completed_candidate_count"] == 2


def test_batch_search_accepts_stable_band_objective(tmp_path, monkeypatch):
    monkeypatch.setattr(batch_search, "_assert_required_artifacts", lambda: None)

    def fake_run_backtest(config, model_name):
        return {
            "data_is_synthetic": False,
            "data_providers": ["jquants"],
            "walk_forward_windows": 6,
            "walk_forward_fallback_used": False,
            "total_trades": 60,
            "total_return_pct": -6.0,
            "average_monthly_return_pct": -0.5,
            "median_monthly_return_pct": -0.4,
            "min_monthly_return_pct": -1.0,
            "max_monthly_return_pct": 0.0,
            "profit_factor": 0.5,
            "max_drawdown_pct": -4.0,
            "positive_active_month_ratio": 0.0,
            "win_rate_pct": 20.0,
            "monthly_returns": [{"return_pct": value} for value in [-0.5, -0.4, -0.8, 0.0, -0.3, -0.6]],
        }, None, None, None

    monkeypatch.setattr(batch_search, "run_backtest", fake_run_backtest)

    ranking, _ = batch_search.run_batch_search(
        {},
        candidates=1,
        objective="stable-band",
        output_root=tmp_path,
    )

    assert ranking.iloc[0]["objective"] == "stable-band"
    assert bool(ranking.iloc[0]["passes_target"])
    assert ranking.iloc[0]["stable_band_direction"] == "negative"
    assert ranking.iloc[0]["direction_month_ratio"] >= 0.60


def test_stable_band_rejects_direction_mismatch():
    metrics = {
        "data_is_synthetic": False,
        "data_providers": ["jquants"],
        "walk_forward_windows": 6,
        "walk_forward_fallback_used": False,
        "total_trades": 60,
        "total_return_pct": 6.0,
        "max_drawdown_pct": -4.0,
        "average_monthly_return_pct": 0.5,
        "monthly_returns": [{"return_pct": value} for value in [-0.5, -0.4, -0.8, 0.1, -0.3, 2.0]],
    }

    assert not batch_search.candidate_passes_stable_band_target(
        metrics,
        target_total_abs_return_pct=5.0,
        max_total_abs_return_pct=20.0,
        max_drawdown_pct=20.0,
        min_trades=50,
        min_stable_month_ratio=0.60,
        min_walk_forward_windows=6,
    )
