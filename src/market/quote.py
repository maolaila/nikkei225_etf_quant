from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class Quote:
    symbol: str
    best_bid: float | None
    best_ask: float | None
    bid_depth: float | None = None
    ask_depth: float | None = None
    volume: float | None = None
    turnover: float | None = None
    last_price: float | None = None
    last_trade_time: pd.Timestamp | None = None
    quote_time: pd.Timestamp | None = None
    received_time: pd.Timestamp | None = None

    @property
    def has_bid_ask(self) -> bool:
        return self.best_bid is not None and self.best_ask is not None and self.best_bid > 0 and self.best_ask > 0

    @property
    def mid_price(self) -> float | None:
        if not self.has_bid_ask:
            return None
        return (float(self.best_bid) + float(self.best_ask)) / 2.0

    @property
    def spread(self) -> float | None:
        if not self.has_bid_ask:
            return None
        return float(self.best_ask) - float(self.best_bid)

    @property
    def spread_bps(self) -> float | None:
        mid = self.mid_price
        spread = self.spread
        if mid is None or spread is None or mid <= 0:
            return None
        return spread / mid * 10000.0


@dataclass(frozen=True)
class QuoteCheck:
    approved: bool
    reason: str
    mid_price: float | None
    spread_bps: float | None
    quote_age_seconds: float | None


def quote_from_mapping(payload: dict[str, Any]) -> Quote:
    return Quote(
        symbol=str(payload.get("symbol", "")),
        best_bid=_float_or_none(payload.get("best_bid", payload.get("bid"))),
        best_ask=_float_or_none(payload.get("best_ask", payload.get("ask"))),
        bid_depth=_float_or_none(payload.get("bid_depth")),
        ask_depth=_float_or_none(payload.get("ask_depth")),
        volume=_float_or_none(payload.get("volume")),
        turnover=_float_or_none(payload.get("turnover")),
        last_price=_float_or_none(payload.get("last_price")),
        last_trade_time=_timestamp_or_none(payload.get("last_trade_time")),
        quote_time=_timestamp_or_none(payload.get("quote_time", payload.get("timestamp"))),
        received_time=_timestamp_or_none(payload.get("received_time")),
    )


def evaluate_quote(quote: Quote, config: dict[str, Any], now: pd.Timestamp | None = None) -> QuoteCheck:
    risk = config.get("risk", {}).get("live_quote", {})
    max_spread_bps = float(risk.get("max_spread_bps", 20.0))
    max_quote_age_seconds = float(risk.get("max_quote_age_seconds", 3.0))
    min_bid_depth = float(risk.get("min_bid_depth", 0.0))
    min_ask_depth = float(risk.get("min_ask_depth", 0.0))

    if not quote.has_bid_ask:
        return QuoteCheck(False, "missing_bid_ask", None, None, None)
    if quote.best_ask is not None and quote.best_bid is not None and quote.best_ask < quote.best_bid:
        return QuoteCheck(False, "crossed_or_invalid_bid_ask", quote.mid_price, quote.spread_bps, None)

    quote_age = None
    if quote.quote_time is not None:
        reference_now = pd.Timestamp(now) if now is not None else pd.Timestamp.utcnow()
        quote_time = pd.Timestamp(quote.quote_time)
        if reference_now.tzinfo is not None and quote_time.tzinfo is None:
            reference_now = reference_now.tz_localize(None)
        elif reference_now.tzinfo is None and quote_time.tzinfo is not None:
            quote_time = quote_time.tz_localize(None)
        quote_age = max(0.0, (reference_now - quote_time).total_seconds())
        if quote_age > max_quote_age_seconds:
            return QuoteCheck(False, "quote_stale", quote.mid_price, quote.spread_bps, quote_age)

    spread_bps = quote.spread_bps
    if spread_bps is None or spread_bps > max_spread_bps:
        return QuoteCheck(False, "spread_too_wide", quote.mid_price, spread_bps, quote_age)

    if min_bid_depth > 0 and (quote.bid_depth is None or quote.bid_depth < min_bid_depth):
        return QuoteCheck(False, "bid_depth_too_thin", quote.mid_price, spread_bps, quote_age)
    if min_ask_depth > 0 and (quote.ask_depth is None or quote.ask_depth < min_ask_depth):
        return QuoteCheck(False, "ask_depth_too_thin", quote.mid_price, spread_bps, quote_age)

    return QuoteCheck(True, "quote_ok", quote.mid_price, spread_bps, quote_age)


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if pd.notna(number) else None


def _timestamp_or_none(value: Any) -> pd.Timestamp | None:
    if value in (None, ""):
        return None
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    return timestamp if pd.notna(timestamp) else None

