from __future__ import annotations

import pandas as pd


def realized_vol(returns: pd.Series, window: int) -> pd.Series:
    return returns.rolling(window, min_periods=3).std()

