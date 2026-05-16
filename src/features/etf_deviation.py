from __future__ import annotations

import pandas as pd


def etf_deviation(actual_return: pd.Series, reference_return: pd.Series, direction: int, leverage: float) -> pd.Series:
    return actual_return - reference_return * direction * leverage

