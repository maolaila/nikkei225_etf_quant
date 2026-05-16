from __future__ import annotations

import pandas as pd


def volume_ratio(volume: pd.Series, window: int) -> pd.Series:
    return volume / volume.rolling(window, min_periods=2).mean()

