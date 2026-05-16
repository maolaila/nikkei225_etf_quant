from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from market_data_collector.config import Settings
from market_data_collector.models import STANDARD_COLUMNS, get_symbol_config
from market_data_collector.providers.base import MarketDataProvider, ProviderError

LOGGER = logging.getLogger(__name__)


class YFinanceProvider(MarketDataProvider):
    name = "yfinance"

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
        yf_interval = _interval(interval)
        if interval == "1m" and (date.today() - from_date).days > 30:
            raise ProviderError(
                "yfinance 1m history is limited and is not suitable as the official long-horizon backtest data source."
            )
        if dry_run:
            LOGGER.info(
                "dry-run provider=yfinance ticker=%s interval=%s from=%s to=%s",
                config.yfinance_ticker,
                yf_interval,
                from_date,
                to_date,
            )
            return pd.DataFrame(columns=STANDARD_COLUMNS)
        try:
            import yfinance as yf
        except ImportError as exc:
            raise ProviderError("yfinance is not installed. Install the optional dependency first.") from exc
        frame = yf.download(
            config.yfinance_ticker,
            start=from_date.isoformat(),
            end=to_date.isoformat(),
            interval=yf_interval,
            auto_adjust=False,
            progress=False,
        )
        if frame.empty:
            return pd.DataFrame(columns=STANDARD_COLUMNS)
        return self._standardize(frame, symbol)

    def _standardize(self, frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
        config = get_symbol_config(symbol)
        fetched_at = pd.Timestamp.now(tz="UTC").isoformat()
        data = frame.copy()
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = [str(column[0]) for column in data.columns]
        data = data.reset_index()
        dt_column = "Datetime" if "Datetime" in data.columns else "Date"
        output: list[dict[str, object]] = []
        for row in data.to_dict(orient="records"):
            dt = pd.Timestamp(row[dt_column])
            if dt.tzinfo is None:
                dt = dt.tz_localize("Asia/Tokyo")
            else:
                dt = dt.tz_convert("Asia/Tokyo")
            output.append(
                {
                    "datetime": dt,
                    "date": dt.strftime("%Y-%m-%d"),
                    "time": dt.strftime("%H:%M:%S"),
                    "symbol": symbol,
                    "code": config.yfinance_ticker,
                    "open": _number(row.get("Open")),
                    "high": _number(row.get("High")),
                    "low": _number(row.get("Low")),
                    "close": _number(row.get("Close")),
                    "volume": _number(row.get("Volume")),
                    "turnover": None,
                    "provider": self.name,
                    "fetched_at": fetched_at,
                }
            )
        result = pd.DataFrame(output)
        return result[STANDARD_COLUMNS].sort_values("datetime").reset_index(drop=True)


def _interval(interval: str) -> str:
    mapping = {"1d": "1d", "1m": "1m", "3min": "5m", "5min": "5m", "30min": "30m"}
    if interval not in mapping:
        raise ProviderError(f"Unsupported yfinance interval: {interval}")
    if interval == "3min":
        LOGGER.warning("yfinance does not provide 3min directly; fetch 1m or 5m and resample for formal tests.")
    return mapping[interval]


def _number(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)
