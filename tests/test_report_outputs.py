from __future__ import annotations

import json

import pandas as pd

from src.backtest.report import TRADE_LOG_COLUMNS, render_existing_report, write_backtest_report
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
    trade_html = (tmp_path / "trade_log.html").read_text(encoding="utf-8")
    monthly_html = (tmp_path / "monthly_returns.html").read_text(encoding="utf-8")
    assert "<title>Simulated Trade Log</title>" in trade_html
    assert "Rows: <span id=\"visibleCount\">0</span> / 0" in trade_html
    assert "<input id=\"filterInput\"" in trade_html
    assert "<table id=\"dataTable\">" in trade_html
    assert "click headers to sort.</div>" in trade_html
    assert "<title>Monthly Returns</title>" in monthly_html


def test_render_existing_report_overwrites_stale_monthly_return_artifacts(tmp_path):
    metrics = {
        "total_return_pct": 1.0,
        "average_monthly_return_pct": 0.5,
        "min_monthly_return_pct": -2.0,
        "positive_month_ratio": 1 / 3,
        "positive_active_month_ratio": 0.5,
        "total_trades": 0,
        "final_equity_jpy": 1010000.0,
        "max_drawdown_pct": -2.0,
        "win_rate_pct": 0.0,
        "profit_factor": 0.0,
        "monthly_returns": [
            {
                "year_month": "2026-01",
                "year": 2026,
                "month": 1,
                "start_equity_jpy": 100.0,
                "end_equity_jpy": 98.0,
                "pnl_jpy": -2.0,
                "return_pct": -2.0,
            },
            {
                "year_month": "2026-02",
                "year": 2026,
                "month": 2,
                "start_equity_jpy": 98.0,
                "end_equity_jpy": 98.0,
                "pnl_jpy": 0.0,
                "return_pct": 0.0,
            },
            {
                "year_month": "2026-03",
                "year": 2026,
                "month": 3,
                "start_equity_jpy": 98.0,
                "end_equity_jpy": 101.43,
                "pnl_jpy": 3.43,
                "return_pct": 3.5,
            },
        ],
    }
    write_json(tmp_path / "metrics.json", metrics)
    pd.DataFrame([{"year_month": "2099-01", "return_pct": 99.0}]).to_csv(tmp_path / "monthly_returns.csv", index=False)
    pd.DataFrame(columns=TRADE_LOG_COLUMNS).to_csv(tmp_path / "trade_log.csv", index=False)
    pd.DataFrame(
        [
            {
                "timestamp": "2026-03-31 15:00",
                "cash": 1010000.0,
                "position_symbol": "",
                "position_qty": 0,
                "equity": 1010000.0,
            }
        ]
    ).to_csv(tmp_path / "equity_curve.csv", index=False)
    pd.DataFrame().to_csv(tmp_path / "signal_log.csv", index=False)

    render_existing_report({"backtest": {"report": {"output_dir": str(tmp_path)}}})

    monthly = pd.read_csv(tmp_path / "monthly_returns.csv")
    assert monthly["year_month"].tolist() == ["2026-01", "2026-02", "2026-03"]
    assert monthly["return_pct"].tolist() == [-2.0, 0.0, 3.5]
    monthly_html = (tmp_path / "monthly_returns.html").read_text(encoding="utf-8")
    assert "2099-01" not in monthly_html
    assert "2026-01" in monthly_html
    assert "-2.00" in monthly_html
    assert "+3.50" in monthly_html


def test_write_json_replaces_non_finite_numbers(tmp_path):
    path = tmp_path / "metrics.json"
    write_json(path, {"annualized_return_pct": float("nan"), "profit_factor": float("inf")})

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {"annualized_return_pct": None, "profit_factor": None}
