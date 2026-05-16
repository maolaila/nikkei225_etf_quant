from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any

import httpx
import pandas as pd
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from market_data_collector.config import Settings
from market_data_collector.models import STANDARD_COLUMNS, get_symbol_config
from market_data_collector.providers.base import MarketDataProvider, ProviderError, RetryableProviderError

LOGGER = logging.getLogger(__name__)


class JQuantsProvider(MarketDataProvider):
    name = "jquants"
    daily_endpoint = "https://api.jquants.com/v2/equities/bars/daily"
    minute_endpoint = "https://api.jquants.com/v2/equities/bars/minute"
    minute_disabled_message = (
        "J-Quants minute bars require Light Plan or higher plus the stock minute/tick add-on. "
        "Set JQUANTS_ENABLE_MINUTE=true only after the add-on is enabled."
    )

    def __init__(self, settings: Settings, min_request_interval_seconds: float = 15.0) -> None:
        self.settings = settings
        self.min_request_interval_seconds = min_request_interval_seconds
        self._last_request_monotonic = 0.0

    def fetch_daily(
        self,
        symbol: str,
        from_date: date,
        to_date: date,
        dry_run: bool = False,
        max_pages: int | None = None,
    ) -> pd.DataFrame:
        config = get_symbol_config(symbol)
        params: dict[str, Any] = {"code": config.jquants_code, "from": from_date.isoformat(), "to": to_date.isoformat()}
        if dry_run:
            LOGGER.info(
                "dry-run provider=jquants endpoint=daily symbol=%s code=%s from=%s to=%s",
                symbol,
                config.jquants_code,
                from_date,
                to_date,
            )
            return pd.DataFrame(columns=STANDARD_COLUMNS)
        payloads = self._fetch_paginated(self.daily_endpoint, params, "daily", symbol, config.jquants_code, max_pages)
        rows = _records_from_payloads(payloads, ("daily_quotes", "bars", "data", "items"))
        return self._standardize_daily(rows, symbol)

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
        if not self.settings.jquants_enable_minute:
            raise ProviderError(self.minute_disabled_message)
        params: dict[str, Any] = {
            "code": config.jquants_code,
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
            "interval": interval,
        }
        if dry_run:
            LOGGER.info(
                "dry-run provider=jquants endpoint=minute symbol=%s code=%s interval=%s from=%s to=%s",
                symbol,
                config.jquants_code,
                interval,
                from_date,
                to_date,
            )
            return pd.DataFrame(columns=STANDARD_COLUMNS)
        payloads = self._fetch_paginated(self.minute_endpoint, params, "minute", symbol, config.jquants_code, max_pages)
        rows = _records_from_payloads(payloads, ("minute_bars", "bars", "data", "items"))
        return self._standardize_minute(rows, symbol)

    def _headers(self) -> dict[str, str]:
        if not self.settings.jquants_api_key:
            raise ProviderError(
                "JQUANTS_API_KEY is required for real J-Quants requests. Put it in .env or the environment."
            )
        return {"x-api-key": self.settings.jquants_api_key}

    def _fetch_paginated(
        self,
        endpoint: str,
        base_params: dict[str, Any],
        endpoint_type: str,
        symbol: str,
        code: str,
        max_pages: int | None,
    ) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        pagination_key: str | None = None
        page = 1
        while True:
            if max_pages is not None and page > max_pages:
                break
            params = dict(base_params)
            if pagination_key:
                params["pagination_key"] = pagination_key
            payload = self._request_json(endpoint, params)
            rows = _records_from_payload(payload, ("daily_quotes", "minute_bars", "bars", "data", "items"))
            LOGGER.info(
                "request provider=jquants symbol=%s code=%s endpoint=%s from=%s to=%s page=%s rows=%s",
                symbol,
                code,
                endpoint_type,
                base_params.get("from") or base_params.get("date"),
                base_params.get("to") or base_params.get("date"),
                page,
                len(rows),
            )
            payloads.append(payload)
            pagination_key = payload.get("pagination_key") or payload.get("paginationKey")
            if not pagination_key:
                break
            page += 1
        return payloads

    @retry(
        retry=retry_if_exception_type(RetryableProviderError),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _request_json(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        self._respect_rate_limit()
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(endpoint, headers=self._headers(), params=params)
        except httpx.HTTPError as exc:
            raise RetryableProviderError(f"J-Quants request failed: {exc}") from exc

        if response.status_code in {401, 403}:
            raise ProviderError(
                "J-Quants returned 401/403. The API key may be invalid, the plan may not include this endpoint, "
                "or the required add-on may not be enabled."
            )
        if response.status_code == 429 or 500 <= response.status_code < 600:
            raise RetryableProviderError(f"J-Quants retryable HTTP {response.status_code}: {response.text[:300]}")
        if response.status_code >= 400:
            raise ProviderError(f"J-Quants HTTP {response.status_code}: {response.text[:500]}")
        return response.json()

    def _respect_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_monotonic
        wait_seconds = self.min_request_interval_seconds - elapsed
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        self._last_request_monotonic = time.monotonic()

    def _standardize_daily(self, rows: list[dict[str, Any]], symbol: str) -> pd.DataFrame:
        fetched_at = pd.Timestamp.now(tz="UTC").isoformat()
        output: list[dict[str, Any]] = []
        for row in rows:
            raw_date = _value(row, "Date", "date")
            if raw_date is None:
                continue
            date_text = str(raw_date)[:10]
            output.append(
                {
                    "datetime": _tokyo_datetime(date_text, "15:30:00"),
                    "date": date_text,
                    "time": "15:30:00",
                    "symbol": symbol,
                    "code": str(_value(row, "Code", "code") or get_symbol_config(symbol).jquants_code),
                    "open": _number(_value(row, "AdjustmentOpen", "Open", "open")),
                    "high": _number(_value(row, "AdjustmentHigh", "High", "high")),
                    "low": _number(_value(row, "AdjustmentLow", "Low", "low")),
                    "close": _number(_value(row, "AdjustmentClose", "Close", "close")),
                    "volume": _number(_value(row, "AdjustmentVolume", "Volume", "volume")),
                    "turnover": _number(_value(row, "TurnoverValue", "turnover")),
                    "provider": self.name,
                    "fetched_at": fetched_at,
                    "adjustment_factor": _number(_value(row, "AdjustmentFactor", "adjustment_factor")),
                    "raw_open": _number(_value(row, "Open", "open")),
                    "raw_high": _number(_value(row, "High", "high")),
                    "raw_low": _number(_value(row, "Low", "low")),
                    "raw_close": _number(_value(row, "Close", "close")),
                    "raw_volume": _number(_value(row, "Volume", "volume")),
                }
            )
        return _ordered_frame(output)

    def _standardize_minute(self, rows: list[dict[str, Any]], symbol: str) -> pd.DataFrame:
        fetched_at = pd.Timestamp.now(tz="UTC").isoformat()
        output: list[dict[str, Any]] = []
        for row in rows:
            raw_date = _value(row, "Date", "date")
            raw_time = _value(row, "Time", "time") or "00:00:00"
            if raw_date is None:
                continue
            date_text = str(raw_date)[:10]
            time_text = _normalize_time(raw_time)
            output.append(
                {
                    "datetime": _tokyo_datetime(date_text, time_text),
                    "date": date_text,
                    "time": time_text,
                    "symbol": symbol,
                    "code": str(_value(row, "Code", "code") or get_symbol_config(symbol).jquants_code),
                    "open": _number(_value(row, "O", "Open", "open")),
                    "high": _number(_value(row, "H", "High", "high")),
                    "low": _number(_value(row, "L", "Low", "low")),
                    "close": _number(_value(row, "C", "Close", "close")),
                    "volume": _number(_value(row, "Vo", "Volume", "volume")),
                    "turnover": _number(_value(row, "Va", "TurnoverValue", "turnover")),
                    "provider": self.name,
                    "fetched_at": fetched_at,
                }
            )
        return _ordered_frame(output)


def _records_from_payloads(payloads: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for payload in payloads:
        rows.extend(_records_from_payload(payload, keys))
    return rows


def _records_from_payload(payload: dict[str, Any], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None and value != "":
            return value
    return None


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _tokyo_datetime(date_text: str, time_text: str) -> pd.Timestamp:
    return pd.Timestamp(f"{date_text} {time_text}", tz="Asia/Tokyo")


def _normalize_time(value: Any) -> str:
    text = str(value)
    if len(text) == 5:
        return f"{text}:00"
    if len(text) == 4 and text.isdigit():
        return f"{text[:2]}:{text[2:]}:00"
    return text


def _ordered_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=STANDARD_COLUMNS)
    ordered = [column for column in STANDARD_COLUMNS if column in frame.columns]
    extras = [column for column in frame.columns if column not in ordered]
    return frame[ordered + extras].sort_values("datetime").reset_index(drop=True)
