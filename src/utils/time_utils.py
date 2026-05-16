from __future__ import annotations

from datetime import time

import pandas as pd


def parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def ensure_timestamp(series_or_value):
    return pd.to_datetime(series_or_value)

