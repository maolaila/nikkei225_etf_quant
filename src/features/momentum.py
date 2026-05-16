from __future__ import annotations

import pandas as pd


def momentum(series: pd.Series, periods: int) -> pd.Series:
    return series.pct_change(periods)

