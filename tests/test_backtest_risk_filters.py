from __future__ import annotations

import pandas as pd

from src.backtest.engine import run_backtest


class DispersionLake:
    def __init__(self):
        times = pd.date_range("2026-01-05 09:10", periods=10, freq="1min")
        self.bars = pd.DataFrame(
            {
                "timestamp": times,
                "symbol": ["1321"] * len(times),
                "interval": ["1m"] * len(times),
                "open": [100.0] * len(times),
                "high": [100.5] * len(times),
                "low": [99.5] * len(times),
                "close": [100.0] * len(times),
                "volume": [1000] * len(times),
                "turnover": [100000] * len(times),
                "provider": ["test"] * len(times),
                "adjusted": [True] * len(times),
            }
        )
        self.signals = pd.DataFrame(
            {
                "timestamp": [times[1]],
                "predicted_action": [1],
                "action_name": ["long_1x"],
                "prob_flat": [0.01],
                "prob_long_1x": [0.95],
                "prob_long_2x": [0.01],
                "prob_short_1x": [0.01],
                "prob_short_2x": [0.02],
                "confidence": [0.95],
                "expected_return_bps": [20.0],
                "expected_cost_bps": [5.0],
                "net_edge_bps": [15.0],
                "reason": ["dispersion blocked"],
                "market_regime": ["range"],
                "implied_nikkei_dispersion_bps": [60.0],
            }
        )

    def read_frame(self, layer, name):
        if layer == "normalized":
            return self.bars
        if layer == "models":
            return self.signals
        raise FileNotFoundError


def test_backtest_blocks_entries_when_etf_implied_dispersion_is_too_high(monkeypatch, tmp_path):
    monkeypatch.setattr("src.backtest.engine.DataLake", DispersionLake)
    monkeypatch.setattr("src.backtest.engine.output_dir", lambda config: tmp_path)
    monkeypatch.setattr("src.backtest.engine.write_backtest_report", lambda *args, **kwargs: tmp_path / "report.md")

    config = {
        "backtest": {
            "initial_cash_jpy": 100000,
            "execution": {"signal_delay_bars": 1, "slippage_bps": 0, "fallback_spread_bps": 0},
            "cost": {"commission_rate_pct": 0, "fixed_commission_jpy": 0},
            "risk": {"max_trades_per_day": 3, "max_implied_dispersion_bps": 30},
            "position_limits": {"long_1x": {"max_equity_pct": 50}},
            "report": {"output_dir": str(tmp_path)},
        },
        "strategy": {"exit": {"max_holding_minutes": 60, "force_exit_time": "15:10", "stop_loss_pct": {"long_1x": 5}}},
        "model": {"prediction": {"min_confidence": 0.55, "min_action_probability": 0.80}},
        "etf_universe": {
            "long_1x": {
                "direction": 1,
                "leverage": 1,
                "candidates": [{"symbol": "1321", "enabled": True, "priority": 1}],
            }
        },
    }

    metrics, trade_log, _, signals = run_backtest(config)

    assert metrics["total_trades"] == 0
    assert trade_log.empty
    assert signals.iloc[0]["entry_blocked"]
    assert "etf_dispersion_filter" in signals.iloc[0]["entry_block_reason"]

