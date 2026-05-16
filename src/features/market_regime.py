from __future__ import annotations

import numpy as np
import pandas as pd


def classify_regime(realized_vol: pd.Series) -> pd.Series:
    threshold = realized_vol.median()
    return pd.Series(np.where(realized_vol > threshold, "trend", "range"), index=realized_vol.index)
