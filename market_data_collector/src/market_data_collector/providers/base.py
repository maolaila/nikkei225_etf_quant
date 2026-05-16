from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import pandas as pd


class ProviderError(RuntimeError):
    """Provider failed with a non-retryable error."""


class RetryableProviderError(ProviderError):
    """Provider failed with a retryable error such as 429 or 5xx."""


class MarketDataProvider(ABC):
    name: str

    @abstractmethod
    def fetch_daily(
        self,
        symbol: str,
        from_date: date,
        to_date: date,
        dry_run: bool = False,
        max_pages: int | None = None,
    ) -> pd.DataFrame:
        ...

    @abstractmethod
    def fetch_intraday(
        self,
        symbol: str,
        interval: str,
        from_date: date,
        to_date: date,
        dry_run: bool = False,
        max_pages: int | None = None,
    ) -> pd.DataFrame:
        ...
