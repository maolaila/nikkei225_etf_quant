from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ProviderName = Literal["jquants", "twelvedata", "yfinance"]
Interval = Literal["1d", "1m", "3min", "5min", "30min"]
OutputFormat = Literal["parquet", "csv"]


ETF_SYMBOLS = {
    "1357": {
        "jquants_code": "13570",
        "twelvedata_symbol": "1357",
        "yfinance_ticker": "1357.T",
        "name": "NEXT FUNDS Nikkei 225 Double Inverse Index ETF",
    },
    "1570": {
        "jquants_code": "15700",
        "twelvedata_symbol": "1570",
        "yfinance_ticker": "1570.T",
        "name": "NEXT FUNDS Nikkei 225 Leveraged Index ETF",
    },
    "1321": {
        "jquants_code": "13210",
        "twelvedata_symbol": "1321",
        "yfinance_ticker": "1321.T",
        "name": "NEXT FUNDS Nikkei 225 Exchange Traded Fund",
    },
    "1571": {
        "jquants_code": "15710",
        "twelvedata_symbol": "1571",
        "yfinance_ticker": "1571.T",
        "name": "NEXT FUNDS Nikkei 225 Inverse Index ETF",
    },
}

STANDARD_COLUMNS = [
    "datetime",
    "date",
    "time",
    "symbol",
    "code",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
    "provider",
    "fetched_at",
]

SUPPORTED_INTERVALS: set[str] = {"1d", "1m", "3min", "5min", "30min"}
SUPPORTED_PROVIDERS: set[str] = {"jquants", "twelvedata", "yfinance"}


@dataclass(frozen=True)
class SymbolConfig:
    symbol: str
    jquants_code: str
    twelvedata_symbol: str
    yfinance_ticker: str
    name: str


def get_symbol_config(symbol: str) -> SymbolConfig:
    key = str(symbol)
    if key not in ETF_SYMBOLS:
        supported = ", ".join(sorted(ETF_SYMBOLS))
        raise ValueError(f"Unsupported ETF symbol {symbol!r}. Supported symbols: {supported}")
    item = ETF_SYMBOLS[key]
    return SymbolConfig(
        symbol=key,
        jquants_code=str(item["jquants_code"]),
        twelvedata_symbol=str(item["twelvedata_symbol"]),
        yfinance_ticker=str(item["yfinance_ticker"]),
        name=str(item["name"]),
    )


def parse_symbols(symbols: str) -> list[str]:
    parsed = [part.strip() for part in symbols.split(",") if part.strip()]
    for symbol in parsed:
        get_symbol_config(symbol)
    return parsed
