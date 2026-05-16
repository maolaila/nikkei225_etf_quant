from __future__ import annotations

import pandas as pd


def intraday_vwap(frame: pd.DataFrame) -> pd.Series:
    return frame["turnover"].cumsum() / frame["volume"].cumsum().replace(0, pd.NA)

