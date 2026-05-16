from __future__ import annotations

import pandas as pd

from src.backtest.engine import run_backtest


class FakeLake:
    def __init__(self):
        times = pd.date_range("2026-01-05 09:00", periods=90, freq="1min")
        self.bars = pd.DataFrame(
            {
                "timestamp": times,
                "symbol": ["1321"] * len(times),
                "interval": ["1m"] * len(times),
                "open": [100 + i * 0.1 for i in range(len(times))],
                "high": [101 + i * 0.1 for i in range(len(times))],
                "low": [99 + i * 0.1 for i in range(len(times))],
                "close": [100 + i * 0.1 for i in range(len(times))],
                "volume": [1000] * len(times),
                "turnover": [100000] * len(times),
                "provider": ["test"] * len(times),
                "adjusted": [True] * len(times),
            }
        )
        self.signals = pd.DataFrame(
            {
                "timestamp": [times[5], times[70]],
                "predicted_action": [1, 0],
                "action_name": ["long_1x", "flat"],
                "confidence": [0.9, 0.9],
                "reason": ["unit test buy", "unit test exit"],
                "market_regime": ["trend", "range"],
            }
        )

    def read_frame(self, layer, name):
        if layer == "normalized":
            return self.bars
        if layer == "models":
            return self.signals
        raise FileNotFoundError


def test_backtest_engine_logs_buy_and_sell(monkeypatch, tmp_path):
    fake = FakeLake()
    monkeypatch.setattr("src.backtest.engine.DataLake", lambda: fake)
    monkeypatch.setattr("src.backtest.engine.output_dir", lambda config: tmp_path)
    monkeypatch.setattr("src.backtest.engine.write_backtest_report", lambda *args, **kwargs: tmp_path / "report.md")
    config = {
        "backtest": {
            "initial_cash_jpy": 100000,
            "execution": {"signal_delay_bars": 1, "slippage_bps": 0, "fallback_spread_bps": 0},
            "cost": {"commission_rate_pct": 0, "fixed_commission_jpy": 0},
            "risk": {"max_trades_per_day": 3},
            "position_limits": {"long_1x": {"max_equity_pct": 50}},
            "report": {"output_dir": str(tmp_path)},
        },
        "strategy": {"exit": {"max_holding_minutes": 60, "force_exit_time": "15:10", "stop_loss_pct": {"long_1x": 5}}},
        "model": {"prediction": {"min_confidence": 0.55}},
        "etf_universe": {
            "long_1x": {
                "direction": 1,
                "leverage": 1,
                "candidates": [{"symbol": "1321", "enabled": True, "priority": 1}],
            }
        },
    }
    metrics, trade_log, _, _ = run_backtest(config)
    assert metrics["total_trades"] == 1
    assert set(trade_log["action"]) == {"BUY", "SELL"}
    assert trade_log.iloc[0]["reason"] == "unit test buy"
    assert trade_log.iloc[0]["market_regime"] == "trend"
    assert {"reference_price", "commission_jpy", "execution_cost_bps", "market_impact_bps"}.issubset(trade_log.columns)


def test_backtest_engine_applies_action_probability_gate(monkeypatch, tmp_path):
    fake = FakeLake()
    fake.signals = pd.DataFrame(
        {
            "timestamp": [fake.bars["timestamp"].iloc[5]],
            "predicted_action": [1],
            "action_name": ["long_1x"],
            "prob_flat": [0.10],
            "prob_long_1x": [0.30],
            "prob_long_2x": [0.20],
            "prob_short_1x": [0.20],
            "prob_short_2x": [0.20],
            "confidence": [0.90],
            "reason": ["low action probability"],
        }
    )
    monkeypatch.setattr("src.backtest.engine.DataLake", lambda: fake)
    monkeypatch.setattr("src.backtest.engine.output_dir", lambda config: tmp_path)
    monkeypatch.setattr("src.backtest.engine.write_backtest_report", lambda *args, **kwargs: tmp_path / "report.md")
    config = {
        "backtest": {
            "initial_cash_jpy": 100000,
            "execution": {"signal_delay_bars": 1, "slippage_bps": 0, "fallback_spread_bps": 0},
            "cost": {"commission_rate_pct": 0, "fixed_commission_jpy": 0},
            "risk": {"max_trades_per_day": 3},
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
    assert signals.iloc[0]["action_name"] == "flat"
    assert "suppressed_by_prediction_gate" in signals.iloc[0]["reason"]


def test_backtest_engine_applies_entry_filter_without_rewriting_signal(monkeypatch, tmp_path):
    fake = FakeLake()
    fake.signals = pd.DataFrame(
        {
            "timestamp": [fake.bars["timestamp"].iloc[5]],
            "predicted_action": [1],
            "action_name": ["long_1x"],
            "prob_flat": [0.10],
            "prob_long_1x": [0.90],
            "prob_long_2x": [0.00],
            "prob_short_1x": [0.00],
            "prob_short_2x": [0.00],
            "confidence": [0.90],
            "reason": ["filtered entry"],
            "market_regime": ["trend"],
        }
    )
    monkeypatch.setattr("src.backtest.engine.DataLake", lambda: fake)
    monkeypatch.setattr("src.backtest.engine.output_dir", lambda config: tmp_path)
    monkeypatch.setattr("src.backtest.engine.write_backtest_report", lambda *args, **kwargs: tmp_path / "report.md")
    config = {
        "backtest": {
            "initial_cash_jpy": 100000,
            "execution": {"signal_delay_bars": 1, "slippage_bps": 0, "fallback_spread_bps": 0},
            "cost": {"commission_rate_pct": 0, "fixed_commission_jpy": 0},
            "risk": {"max_trades_per_day": 3},
            "position_limits": {"long_1x": {"max_equity_pct": 50}},
            "report": {"output_dir": str(tmp_path)},
        },
        "strategy": {
            "entry_filters": [
                {
                    "name": "block_morning_trend_entries",
                    "actions": ["long_1x"],
                    "sessions": ["morning"],
                    "market_regimes": ["trend"],
                }
            ],
            "exit": {"max_holding_minutes": 60, "force_exit_time": "15:10", "stop_loss_pct": {"long_1x": 5}},
        },
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
    assert signals.iloc[0]["action_name"] == "long_1x"
    assert signals.iloc[0]["entry_blocked"]
    assert signals.iloc[0]["entry_block_reason"] == "block_morning_trend_entries"


def test_backtest_engine_applies_time_window_entry_filter(monkeypatch, tmp_path):
    fake = FakeLake()
    fake.signals = pd.DataFrame(
        {
            "timestamp": [pd.Timestamp("2026-01-05 14:05")],
            "predicted_action": [1],
            "action_name": ["long_1x"],
            "prob_flat": [0.10],
            "prob_long_1x": [0.90],
            "prob_long_2x": [0.00],
            "prob_short_1x": [0.00],
            "prob_short_2x": [0.00],
            "confidence": [0.90],
            "reason": ["late filtered entry"],
            "market_regime": ["range"],
        }
    )
    monkeypatch.setattr("src.backtest.engine.DataLake", lambda: fake)
    monkeypatch.setattr("src.backtest.engine.output_dir", lambda config: tmp_path)
    monkeypatch.setattr("src.backtest.engine.write_backtest_report", lambda *args, **kwargs: tmp_path / "report.md")
    config = {
        "backtest": {
            "initial_cash_jpy": 100000,
            "execution": {"signal_delay_bars": 1, "slippage_bps": 0, "fallback_spread_bps": 0},
            "cost": {"commission_rate_pct": 0, "fixed_commission_jpy": 0},
            "risk": {"max_trades_per_day": 3},
            "position_limits": {"long_1x": {"max_equity_pct": 50}},
            "report": {"output_dir": str(tmp_path)},
        },
        "strategy": {
            "entry_filters": [
                {
                    "name": "block_late_afternoon_entries",
                    "actions": ["long_1x"],
                    "start_time": "14:00",
                    "end_time": "15:30",
                }
            ],
            "exit": {"max_holding_minutes": 60, "force_exit_time": "15:10", "stop_loss_pct": {"long_1x": 5}},
        },
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
    assert signals.iloc[0]["action_name"] == "long_1x"
    assert signals.iloc[0]["entry_blocked"]
    assert signals.iloc[0]["entry_block_reason"] == "block_late_afternoon_entries"


def test_backtest_engine_applies_execution_time_entry_filter(monkeypatch, tmp_path):
    class OvernightLake:
        def __init__(self):
            times = pd.to_datetime(
                [
                    "2026-01-05 15:29",
                    "2026-01-06 09:00",
                    "2026-01-06 09:01",
                ]
            )
            self.bars = pd.DataFrame(
                {
                    "timestamp": times,
                    "symbol": ["1321"] * len(times),
                    "interval": ["1m"] * len(times),
                    "open": [100.0, 101.0, 101.5],
                    "high": [100.5, 101.5, 102.0],
                    "low": [99.5, 100.5, 101.0],
                    "close": [100.0, 101.0, 101.5],
                    "volume": [1000] * len(times),
                    "turnover": [100000] * len(times),
                    "provider": ["test"] * len(times),
                    "adjusted": [True] * len(times),
                }
            )
            self.signals = pd.DataFrame(
                {
                    "timestamp": [pd.Timestamp("2026-01-05 15:29")],
                    "predicted_action": [1],
                    "action_name": ["long_1x"],
                    "prob_flat": [0.01],
                    "prob_long_1x": [0.95],
                    "prob_long_2x": [0.01],
                    "prob_short_1x": [0.01],
                    "prob_short_2x": [0.02],
                    "confidence": [0.95],
                    "reason": ["previous close signal"],
                    "market_regime": ["trend"],
                }
            )

        def read_frame(self, layer, name):
            if layer == "normalized":
                return self.bars
            if layer == "models":
                return self.signals
            raise FileNotFoundError

    monkeypatch.setattr("src.backtest.engine.DataLake", OvernightLake)
    monkeypatch.setattr("src.backtest.engine.output_dir", lambda config: tmp_path)
    monkeypatch.setattr("src.backtest.engine.write_backtest_report", lambda *args, **kwargs: tmp_path / "report.md")
    config = {
        "backtest": {
            "initial_cash_jpy": 100000,
            "execution": {"signal_delay_bars": 1, "slippage_bps": 0, "fallback_spread_bps": 0},
            "cost": {"commission_rate_pct": 0, "fixed_commission_jpy": 0},
            "risk": {"max_trades_per_day": 3},
            "position_limits": {"long_1x": {"max_equity_pct": 50}},
            "report": {"output_dir": str(tmp_path)},
        },
        "strategy": {
            "entry_filters": [
                {
                    "name": "block_next_open_execution",
                    "actions": ["long_1x"],
                    "time_basis": "execution",
                    "start_time": "09:00",
                    "end_time": "09:30",
                }
            ],
            "exit": {"max_holding_minutes": 60, "force_exit_time": "15:10", "stop_loss_pct": {"long_1x": 5}},
        },
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
    assert signals.iloc[0]["entry_block_reason"] == "block_next_open_execution"


def test_backtest_engine_applies_dynamic_sizing_holding_and_stop(monkeypatch, tmp_path):
    fake = FakeLake()
    fake.signals = pd.DataFrame(
        {
            "timestamp": [fake.bars["timestamp"].iloc[5], fake.bars["timestamp"].iloc[80]],
            "predicted_action": [2, 0],
            "action_name": ["long_2x", "flat"],
            "prob_flat": [0.01, 0.90],
            "prob_long_1x": [0.01, 0.02],
            "prob_long_2x": [0.95, 0.02],
            "prob_short_1x": [0.01, 0.03],
            "prob_short_2x": [0.02, 0.03],
            "confidence": [0.95, 0.90],
            "reason": ["strong long signal", "exit"],
            "market_regime": ["trend", "trend"],
        }
    )
    monkeypatch.setattr("src.backtest.engine.DataLake", lambda: fake)
    monkeypatch.setattr("src.backtest.engine.output_dir", lambda config: tmp_path)
    monkeypatch.setattr("src.backtest.engine.write_backtest_report", lambda *args, **kwargs: tmp_path / "report.md")
    config = {
        "backtest": {
            "initial_cash_jpy": 100000,
            "execution": {"signal_delay_bars": 1, "slippage_bps": 0, "fallback_spread_bps": 0},
            "cost": {"commission_rate_pct": 0, "fixed_commission_jpy": 0},
            "risk": {"max_trades_per_day": 3},
            "position_limits": {"long_2x": {"max_equity_pct": 35, "absolute_max_equity_pct": 50}},
            "position_sizing": {
                "dynamic_enabled": True,
                "confidence_floor": 0.30,
                "confidence_full": 0.80,
                "min_multiplier": 0.35,
                "max_multiplier": 1.25,
                "trend_multiplier": 1.10,
                "leveraged_etf_multiplier": 0.95,
            },
            "report": {"output_dir": str(tmp_path)},
        },
        "strategy": {
            "exit": {
                "max_holding_minutes": 60,
                "force_exit_time": "15:10",
                "stop_loss_pct": {"long_2x": 1.0},
                "dynamic_holding": {
                    "enabled": True,
                    "confidence_extend_threshold": 0.65,
                    "confidence_full": 0.85,
                    "confidence_extend_multiplier": 1.50,
                    "trend_multiplier": 1.25,
                    "leveraged_etf_multiplier": 0.90,
                    "min_minutes": 20,
                    "max_minutes": 120,
                },
                "dynamic_stop_loss": {
                    "enabled": True,
                    "high_confidence_threshold": 0.70,
                    "high_confidence_widen_multiplier": 1.25,
                    "trend_widen_multiplier": 1.15,
                    "min_pct": {"long_2x": 0.60},
                    "max_pct": {"long_2x": 1.60},
                },
            }
        },
        "model": {"prediction": {"min_confidence": 0.55, "min_action_probability": 0.80}},
        "etf_universe": {
            "long_2x": {
                "direction": 1,
                "leverage": 2,
                "candidates": [{"symbol": "1321", "enabled": True, "priority": 1}],
            }
        },
    }

    _, trade_log, _, _ = run_backtest(config)

    buy = trade_log[trade_log["action"] == "BUY"].iloc[0]
    assert buy["target_equity_pct"] > 35
    assert buy["target_equity_pct"] <= 50
    assert buy["max_holding_minutes"] > 60
    assert buy["stop_loss_pct"] > 1.0


def test_backtest_engine_applies_position_sizing_adjustment_rule(monkeypatch, tmp_path):
    fake = FakeLake()
    monkeypatch.setattr("src.backtest.engine.DataLake", lambda: fake)
    monkeypatch.setattr("src.backtest.engine.output_dir", lambda config: tmp_path)
    monkeypatch.setattr("src.backtest.engine.write_backtest_report", lambda *args, **kwargs: tmp_path / "report.md")
    config = {
        "backtest": {
            "initial_cash_jpy": 100000,
            "execution": {"signal_delay_bars": 1, "slippage_bps": 0, "fallback_spread_bps": 0},
            "cost": {"commission_rate_pct": 0, "fixed_commission_jpy": 0},
            "risk": {"max_trades_per_day": 3},
            "position_limits": {"long_1x": {"max_equity_pct": 50, "absolute_max_equity_pct": 60}},
            "position_sizing": {
                "adjustments": [
                    {
                        "name": "morning_long_stepdown",
                        "actions": ["long_1x"],
                        "sessions": ["morning"],
                        "start_time": "09:00",
                        "end_time": "09:30",
                        "multiplier": 0.40,
                    }
                ]
            },
            "report": {"output_dir": str(tmp_path)},
        },
        "strategy": {"exit": {"max_holding_minutes": 60, "force_exit_time": "15:10", "stop_loss_pct": {"long_1x": 5}}},
        "model": {"prediction": {"min_confidence": 0.55}},
        "etf_universe": {
            "long_1x": {
                "direction": 1,
                "leverage": 1,
                "candidates": [{"symbol": "1321", "enabled": True, "priority": 1}],
            }
        },
    }

    _, trade_log, _, _ = run_backtest(config)

    buy = trade_log[trade_log["action"] == "BUY"].iloc[0]
    assert buy["target_equity_pct"] == 20
    assert buy["sizing_multiplier"] == 0.40


def test_backtest_engine_applies_execution_time_position_sizing_adjustment(monkeypatch, tmp_path):
    fake = FakeLake()
    monkeypatch.setattr("src.backtest.engine.DataLake", lambda: fake)
    monkeypatch.setattr("src.backtest.engine.output_dir", lambda config: tmp_path)
    monkeypatch.setattr("src.backtest.engine.write_backtest_report", lambda *args, **kwargs: tmp_path / "report.md")
    config = {
        "backtest": {
            "initial_cash_jpy": 100000,
            "execution": {"signal_delay_bars": 1, "slippage_bps": 0, "fallback_spread_bps": 0},
            "cost": {"commission_rate_pct": 0, "fixed_commission_jpy": 0},
            "risk": {"max_trades_per_day": 3},
            "position_limits": {"long_1x": {"max_equity_pct": 50, "absolute_max_equity_pct": 60}},
            "position_sizing": {
                "adjustments": [
                    {
                        "name": "execution_open_stepdown",
                        "time_basis": "execution",
                        "actions": ["long_1x"],
                        "sessions": ["morning"],
                        "start_time": "09:06",
                        "end_time": "09:06",
                        "multiplier": 0.40,
                    }
                ]
            },
            "report": {"output_dir": str(tmp_path)},
        },
        "strategy": {"exit": {"max_holding_minutes": 60, "force_exit_time": "15:10", "stop_loss_pct": {"long_1x": 5}}},
        "model": {"prediction": {"min_confidence": 0.55}},
        "etf_universe": {
            "long_1x": {
                "direction": 1,
                "leverage": 1,
                "candidates": [{"symbol": "1321", "enabled": True, "priority": 1}],
            }
        },
    }

    _, trade_log, _, _ = run_backtest(config)

    buy = trade_log[trade_log["action"] == "BUY"].iloc[0]
    assert buy["timestamp"].strftime("%H:%M") == "09:06"
    assert buy["target_equity_pct"] == 20
    assert buy["sizing_multiplier"] == 0.40


def test_backtest_engine_applies_opt_in_no_profit_time_stop(monkeypatch, tmp_path):
    fake = FakeLake()
    fake.bars["close"] = [100.0 - i * 0.01 for i in range(len(fake.bars))]
    fake.bars["open"] = fake.bars["close"]
    fake.bars["high"] = fake.bars["close"] + 0.1
    fake.bars["low"] = fake.bars["close"] - 0.1
    fake.signals = pd.DataFrame(
        {
            "timestamp": [fake.bars["timestamp"].iloc[5], fake.bars["timestamp"].iloc[40]],
            "predicted_action": [1, 0],
            "action_name": ["long_1x", "flat"],
            "prob_flat": [0.01, 0.90],
            "prob_long_1x": [0.95, 0.02],
            "prob_long_2x": [0.01, 0.02],
            "prob_short_1x": [0.01, 0.03],
            "prob_short_2x": [0.02, 0.03],
            "confidence": [0.95, 0.90],
            "reason": ["entry", "still flat"],
            "market_regime": ["range", "range"],
        }
    )
    monkeypatch.setattr("src.backtest.engine.DataLake", lambda: fake)
    monkeypatch.setattr("src.backtest.engine.output_dir", lambda config: tmp_path)
    monkeypatch.setattr("src.backtest.engine.write_backtest_report", lambda *args, **kwargs: tmp_path / "report.md")
    config = {
        "backtest": {
            "initial_cash_jpy": 100000,
            "execution": {"signal_delay_bars": 1, "slippage_bps": 0, "fallback_spread_bps": 0},
            "cost": {"commission_rate_pct": 0, "fixed_commission_jpy": 0},
            "risk": {"max_trades_per_day": 3},
            "position_limits": {"long_1x": {"max_equity_pct": 50}},
            "report": {"output_dir": str(tmp_path)},
        },
        "strategy": {
            "exit": {
                "max_holding_minutes": 60,
                "force_exit_time": "15:10",
                "stop_loss_pct": {"long_1x": 5},
                "exit_on_neutral_signal": False,
                "exit_if_no_profit_enabled": True,
                "exit_if_no_profit_after_minutes": 30,
            }
        },
        "model": {"prediction": {"min_confidence": 0.55, "min_action_probability": 0.80}},
        "etf_universe": {
            "long_1x": {
                "direction": 1,
                "leverage": 1,
                "candidates": [{"symbol": "1321", "enabled": True, "priority": 1}],
            }
        },
    }

    _, trade_log, _, _ = run_backtest(config)

    sell = trade_log[trade_log["action"] == "SELL"].iloc[0]
    assert sell["exit_reason"] == "no_profit_time_stop"


def test_backtest_engine_applies_opt_in_intraday_force_exit_times(monkeypatch, tmp_path):
    fake = FakeLake()
    times = pd.date_range("2026-01-05 11:00", periods=90, freq="1min")
    fake.bars = pd.DataFrame(
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
    fake.signals = pd.DataFrame(
        {
            "timestamp": [times[0], times[20], times[40]],
            "predicted_action": [1, 1, 0],
            "action_name": ["long_1x", "long_1x", "flat"],
            "prob_flat": [0.01, 0.01, 0.90],
            "prob_long_1x": [0.95, 0.95, 0.02],
            "prob_long_2x": [0.01, 0.01, 0.02],
            "prob_short_1x": [0.01, 0.01, 0.03],
            "prob_short_2x": [0.02, 0.02, 0.03],
            "confidence": [0.95, 0.95, 0.90],
            "reason": ["entry", "lunch cutoff", "later flat"],
            "market_regime": ["range", "range", "range"],
        }
    )
    monkeypatch.setattr("src.backtest.engine.DataLake", lambda: fake)
    monkeypatch.setattr("src.backtest.engine.output_dir", lambda config: tmp_path)
    monkeypatch.setattr("src.backtest.engine.write_backtest_report", lambda *args, **kwargs: tmp_path / "report.md")
    config = {
        "backtest": {
            "initial_cash_jpy": 100000,
            "execution": {"signal_delay_bars": 1, "slippage_bps": 0, "fallback_spread_bps": 0},
            "cost": {"commission_rate_pct": 0, "fixed_commission_jpy": 0},
            "risk": {"max_trades_per_day": 3},
            "position_limits": {"long_1x": {"max_equity_pct": 50}},
            "report": {"output_dir": str(tmp_path)},
        },
        "strategy": {
            "exit": {
                "max_holding_minutes": 120,
                "force_exit_times": ["11:20", "15:10"],
                "stop_loss_pct": {"long_1x": 5},
                "exit_on_neutral_signal": False,
            }
        },
        "model": {"prediction": {"min_confidence": 0.55, "min_action_probability": 0.80}},
        "etf_universe": {
            "long_1x": {
                "direction": 1,
                "leverage": 1,
                "candidates": [{"symbol": "1321", "enabled": True, "priority": 1}],
            }
        },
    }

    _, trade_log, _, _ = run_backtest(config)

    sell = trade_log[trade_log["action"] == "SELL"].iloc[0]
    assert sell["exit_reason"] == "force_exit_time_11:20"
