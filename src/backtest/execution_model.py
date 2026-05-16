from __future__ import annotations

import pandas as pd


def next_bar(group: pd.DataFrame, timestamp: pd.Timestamp, delay_bars: int = 1) -> pd.Series | None:
    times = group["_timestamp"] if "_timestamp" in group.columns else pd.to_datetime(group["timestamp"])
    index = times.searchsorted(pd.Timestamp(timestamp), side="right") + max(delay_bars - 1, 0)
    if index >= len(group):
        return None
    return group.iloc[int(index)]
