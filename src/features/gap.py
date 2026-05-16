from __future__ import annotations

import pandas as pd


def gap_pct(open_series: pd.Series, previous_close: pd.Series) -> pd.Series:
    return (open_series / previous_close - 1.0) * 100.0

