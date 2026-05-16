from __future__ import annotations

import pandas as pd

from src.market.bar_builder import BarBuilder, Trade
from src.market.quote import Quote, evaluate_quote


def test_quote_check_rejects_missing_bid_ask_and_wide_spread():
    config = {"risk": {"live_quote": {"max_spread_bps": 5, "max_quote_age_seconds": 10}}}

    missing = evaluate_quote(Quote(symbol="1321", best_bid=None, best_ask=101), config, now=pd.Timestamp("2026-01-05 09:00:01"))
    assert not missing.approved
    assert missing.reason == "missing_bid_ask"

    wide = evaluate_quote(
        Quote(symbol="1321", best_bid=100, best_ask=101, quote_time=pd.Timestamp("2026-01-05 09:00:00")),
        config,
        now=pd.Timestamp("2026-01-05 09:00:01"),
    )
    assert not wide.approved
    assert wide.reason == "spread_too_wide"


def test_bar_builder_uses_exchange_time_and_drops_duplicate_or_out_of_order_ticks():
    builder = BarBuilder(timeframes=("1min",))
    assert builder.on_trade(Trade("1321", 100.0, 10, pd.Timestamp("2026-01-05 09:00:01"), trade_id="a")) == []
    assert builder.on_trade(Trade("1321", 101.0, 10, pd.Timestamp("2026-01-05 09:00:30"), trade_id="b")) == []
    assert builder.on_trade(Trade("1321", 999.0, 10, pd.Timestamp("2026-01-05 09:00:20"), trade_id="old")) == []
    assert builder.on_trade(Trade("1321", 101.0, 10, pd.Timestamp("2026-01-05 09:00:30"), trade_id="b")) == []

    closed = builder.on_trade(Trade("1321", 102.0, 5, pd.Timestamp("2026-01-05 09:01:01"), trade_id="c"))

    assert len(closed) == 1
    assert closed[0]["timestamp"] == pd.Timestamp("2026-01-05 09:01:00")
    assert closed[0]["open"] == 100.0
    assert closed[0]["close"] == 101.0
    assert closed[0]["volume"] == 20

