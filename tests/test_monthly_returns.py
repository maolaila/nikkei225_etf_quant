from __future__ import annotations

import pandas as pd

from src.backtest.metrics import calculate_monthly_returns, summarize_backtest
from src.backtest.report import MONTHLY_RETURN_COLUMN_LABELS, write_filterable_table_html


def test_monthly_returns_record_positive_and_negative_months(tmp_path):
    equity = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-01-31 15:00", "2026-02-27 15:00", "2026-03-31 15:00"]),
            "equity": [110.0, 99.0, 118.8],
        }
    )
    rows = calculate_monthly_returns(equity, initial_cash=100.0)
    assert [row["year_month"] for row in rows] == ["2026-01", "2026-02", "2026-03"]
    assert round(rows[0]["return_pct"], 4) == 10.0
    assert round(rows[1]["return_pct"], 4) == -10.0
    assert round(rows[2]["return_pct"], 4) == 20.0

    metrics = summarize_backtest(equity, pd.DataFrame(), initial_cash=100.0)
    assert metrics["returns_by_month_pct"]["2026-02"] == rows[1]["return_pct"]
    assert round(metrics["average_monthly_return_pct"], 4) == round((10.0 - 10.0 + 20.0) / 3.0, 4)
    assert metrics["active_months"] == 3
    assert round(metrics["positive_active_month_ratio"], 4) == round(2 / 3, 4)

    html_path = write_filterable_table_html(
        pd.DataFrame(rows),
        tmp_path / "monthly_returns.html",
        "月度收益率记录",
        column_labels=MONTHLY_RETURN_COLUMN_LABELS,
        description="字段关系：月度收益率可以为正数、负数或 0。",
    )
    text = html_path.read_text(encoding="utf-8")
    assert "<title>月度收益率记录</title>" in text
    assert "月度收益率(%)" in text
    assert "字段关系" in text
    assert "filterInput" in text
    assert "2026-02" in text
    assert "-10.00" in text
