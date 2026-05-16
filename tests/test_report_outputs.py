from __future__ import annotations

import json

import pandas as pd

from src.backtest.report import TRADE_LOG_COLUMNS, write_backtest_report
from src.utils.serialization import write_json


def test_empty_trade_report_keeps_trade_schema(tmp_path):
    metrics = {
        "total_return_pct": 0.0,
        "average_monthly_return_pct": 0.0,
        "min_monthly_return_pct": 0.0,
        "positive_month_ratio": 0.0,
        "positive_active_month_ratio": 0.0,
        "total_trades": 0,
        "final_equity_jpy": 1000000.0,
        "max_drawdown_pct": 0.0,
        "win_rate_pct": 0.0,
        "profit_factor": 0.0,
        "monthly_returns": [],
    }
    equity_curve = pd.DataFrame(
        [{"timestamp": pd.Timestamp("2026-01-01 15:00"), "cash": 1000000.0, "position_symbol": "", "position_qty": 0, "equity": 1000000.0}]
    )

    write_backtest_report(
        {"backtest": {"report": {"output_dir": str(tmp_path)}}},
        metrics,
        pd.DataFrame(),
        equity_curve,
        pd.DataFrame(),
    )

    trade_log = pd.read_csv(tmp_path / "trade_log.csv")
    assert list(trade_log.columns) == TRADE_LOG_COLUMNS
    assert "reason" in trade_log.columns
    assert "exit_reason" in trade_log.columns


def test_write_json_replaces_non_finite_numbers(tmp_path):
    path = tmp_path / "metrics.json"
    write_json(path, {"annualized_return_pct": float("nan"), "profit_factor": float("inf")})

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {"annualized_return_pct": None, "profit_factor": None}
