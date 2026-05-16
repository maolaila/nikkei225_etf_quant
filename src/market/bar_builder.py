from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from src.market.quote import Quote
from src.market.session import is_continuous_auction


@dataclass(frozen=True)
class Trade:
    symbol: str
    price: float
    quantity: float
    trade_time: pd.Timestamp
    trade_id: str | None = None


@dataclass
class MutableBar:
    symbol: str
    timeframe: str
    start: pd.Timestamp
    end: pd.Timestamp
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float = 0.0
    turnover: float = 0.0
    last_quote_time: pd.Timestamp | None = None
    quote_updates: int = 0
    trade_updates: int = 0
    stale_quote_updates: int = 0
    missing_bid_ask_updates: int = 0

    def update_price(self, price: float, quantity: float = 0.0) -> None:
        if self.open is None:
            self.open = price
        self.high = price if self.high is None else max(self.high, price)
        self.low = price if self.low is None else min(self.low, price)
        self.close = price
        if quantity > 0:
            self.volume += quantity
            self.turnover += quantity * price
            self.trade_updates += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.end,
            "symbol": self.symbol,
            "interval": self.timeframe,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "turnover": self.turnover,
            "quote_time": self.last_quote_time,
            "quote_updates": self.quote_updates,
            "trade_updates": self.trade_updates,
            "stale_quote_updates": self.stale_quote_updates,
            "missing_bid_ask_updates": self.missing_bid_ask_updates,
            "provider": "live_bar_builder",
            "adjusted": False,
        }


@dataclass
class BarBuilder:
    timeframes: tuple[str, ...] = ("1min", "3min", "5min", "15min")
    max_quote_age_seconds: float = 3.0
    _open_bars: dict[tuple[str, str], MutableBar] = field(default_factory=dict)
    _closed_bars: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)
    _last_event_time: dict[str, pd.Timestamp] = field(default_factory=dict)
    _seen_trade_ids: set[tuple[str, str]] = field(default_factory=set)

    def on_quote(self, quote: Quote) -> list[dict[str, Any]]:
        event_time = _first_timestamp(quote.quote_time, quote.received_time, quote.last_trade_time)
        if pd.isna(event_time) or not is_continuous_auction(event_time):
            return []
        symbol = str(quote.symbol)
        if event_time < self._last_event_time.get(symbol, event_time):
            return []
        closed = self._roll(symbol, event_time)
        price = quote.mid_price
        for timeframe in self.timeframes:
            bar = self._bar(symbol, timeframe, event_time)
            bar.quote_updates += 1
            bar.last_quote_time = event_time
            if price is None:
                bar.missing_bid_ask_updates += 1
                continue
            if quote.received_time is not None:
                age = max(0.0, (pd.Timestamp(quote.received_time) - event_time).total_seconds())
                if age > self.max_quote_age_seconds:
                    bar.stale_quote_updates += 1
                    continue
            bar.update_price(float(price))
        self._last_event_time[symbol] = max(event_time, self._last_event_time.get(symbol, event_time))
        return closed

    def on_trade(self, trade: Trade) -> list[dict[str, Any]]:
        event_time = pd.Timestamp(trade.trade_time)
        if pd.isna(event_time) or not is_continuous_auction(event_time):
            return []
        if trade.trade_id:
            key = (trade.symbol, trade.trade_id)
            if key in self._seen_trade_ids:
                return []
            self._seen_trade_ids.add(key)
        symbol = str(trade.symbol)
        if event_time < self._last_event_time.get(symbol, event_time):
            return []
        closed = self._roll(symbol, event_time)
        for timeframe in self.timeframes:
            self._bar(symbol, timeframe, event_time).update_price(float(trade.price), float(trade.quantity))
        self._last_event_time[symbol] = max(event_time, self._last_event_time.get(symbol, event_time))
        return closed

    def get_closed_bar(self, symbol: str, timeframe: str) -> dict[str, Any] | None:
        bars = self._closed_bars.get((str(symbol), timeframe), [])
        return bars[-1] if bars else None

    def _roll(self, symbol: str, event_time: pd.Timestamp) -> list[dict[str, Any]]:
        closed: list[dict[str, Any]] = []
        for timeframe in self.timeframes:
            key = (symbol, timeframe)
            current = self._open_bars.get(key)
            if current is None:
                continue
            bucket_start = _bucket_start(event_time, timeframe)
            if bucket_start <= current.start:
                continue
            if current.open is not None:
                row = current.to_dict()
                self._closed_bars.setdefault(key, []).append(row)
                closed.append(row)
            self._open_bars.pop(key, None)
        return closed

    def _bar(self, symbol: str, timeframe: str, event_time: pd.Timestamp) -> MutableBar:
        key = (symbol, timeframe)
        current = self._open_bars.get(key)
        bucket_start = _bucket_start(event_time, timeframe)
        if current is not None and bucket_start == current.start:
            return current
        if current is not None and current.open is not None:
            self._closed_bars.setdefault(key, []).append(current.to_dict())
        bucket_end = bucket_start + pd.Timedelta(minutes=_timeframe_minutes(timeframe))
        current = MutableBar(symbol=symbol, timeframe=timeframe, start=bucket_start, end=bucket_end)
        self._open_bars[key] = current
        return current


def _timeframe_minutes(timeframe: str) -> int:
    text = timeframe.lower().replace("min", "").replace("m", "")
    minutes = int(text)
    if minutes <= 0:
        raise ValueError(f"timeframe must be positive minutes, got {timeframe!r}")
    return minutes


def _bucket_start(timestamp: pd.Timestamp, timeframe: str) -> pd.Timestamp:
    minutes = _timeframe_minutes(timeframe)
    ts = pd.Timestamp(timestamp)
    session_start = ts.normalize() + (pd.Timedelta(hours=12, minutes=30) if ts.strftime("%H:%M") >= "12:30" else pd.Timedelta(hours=9))
    elapsed = int((ts - session_start).total_seconds() // 60)
    bucket_offset = (max(elapsed, 0) // minutes) * minutes
    return session_start + pd.Timedelta(minutes=bucket_offset)


def _first_timestamp(*values: pd.Timestamp | None) -> pd.Timestamp:
    for value in values:
        if value is not None and pd.notna(value):
            return pd.Timestamp(value)
    return pd.NaT
