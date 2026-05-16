from __future__ import annotations

import logging
from datetime import date
from typing import Any

import httpx
import pandas as pd

from market_data_collector.config import Settings
from market_data_collector.models import STANDARD_COLUMNS, get_symbol_config
from market_data_collector.providers.base import MarketDataProvider, ProviderError

LOGGER = logging.getLogger(__name__)


class TwelveDataProvider(MarketDataProvider):
    name = "twelvedata"
    endpoint = "https://api.twelvedata.com/time_series"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def fetch_daily(
        self,
        symbol: str,
        from_date: date,
        to_date: date,
        dry_run: bool = False,
        max_pages: int | None = None,
    ) -> pd.DataFrame:
        return self.fetch_intraday(symbol, "1d", from_date, to_date, dry_run=dry_run, max_pages=max_pages)

    def fetch_intraday(
        self,
        symbol: str,
        interval: str,
        from_date: date,
        to_date: date,
        dry_run: bool = False,
        max_pages: int | None = None,
    ) -> pd.DataFrame:
        config = get_symbol_config(symbol)
        api_interval = _interval(interval)
        params = {
            "symbol": config.twelvedata_symbol,
            "exchange": "JPX",
            "interval": api_interval,
            "start_date": from_date.isoformat(),
            "end_date": to_date.isoformat(),
            "apikey": self.settings.twelvedata_api_key or "",
            "format": "JSON",
            "timezone": "Asia/Tokyo",
            "order": "ASC",
        }
        if dry_run:
            LOGGER.info(
                "dry-run provider=twelvedata endpoint=time_series symbol=%s exchange=JPX interval=%s from=%s to=%s",
                symbol,
                api_interval,
                from_date,
                to_date,
            )
            return pd.DataFrame(columns=STANDARD_COLUMNS)
        if not self.settings.twelvedata_api_key:
            raise ProviderError("TWELVEDATA_API_KEY is required for Twelve Data requests.")
        with httpx.Client(timeout=30.0) as client:
            response = client.get(self.endpoint, params=params)
        if response.status_code >= 400:
            raise ProviderError(f"Twelve Data HTTP {response.status_code}: {response.text[:500]}")
        payload = response.json()
        if payload.get("status") == "error":
            message = payload.get("message", "unknown error")
            raise ProviderError(
                f"Twelve Data error: {message}. Check plan permissions, quota, and symbol availability."
            )
        return self._standardize(payload.get("values", []), symbol)

    def _standardize(self, rows: list[dict[str, Any]], symbol: str) -> pd.DataFrame:
        fetched_at = pd.Timestamp.now(tz="UTC").isoformat()
        config = get_symbol_config(symbol)
        output: list[dict[str, Any]] = []
        for row in rows:
            dt = pd.Timestamp(row.get("datetime"), tz="Asia/Tokyo")
            output.append(
                {
                    "datetime": dt,
                    "date": dt.strftime("%Y-%m-%d"),
                    "time": dt.strftime("%H:%M:%S"),
                    "symbol": symbol,
                    "code": config.twelvedata_symbol,
                    "open": _number(row.get("open")),
                    "high": _number(row.get("high")),
                    "low": _number(row.get("low")),
                    "close": _number(row.get("close")),
                    "volume": _number(row.get("volume")),
                    "turnover": None,
                    "provider": self.name,
                    "fetched_at": fetched_at,
                }
            )
        frame = pd.DataFrame(output)
        if frame.empty:
            return pd.DataFrame(columns=STANDARD_COLUMNS)
        return frame[STANDARD_COLUMNS].sort_values("datetime").reset_index(drop=True)


def _interval(interval: str) -> str:
    mapping = {"1d": "1day", "1m": "1min", "3min": "3min", "5min": "5min", "30min": "30min"}
    if interval not in mapping:
        raise ProviderError(f"Unsupported Twelve Data interval: {interval}")
    return mapping[interval]


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
