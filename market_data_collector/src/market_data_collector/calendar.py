from __future__ import annotations

from datetime import datetime, time

import pandas as pd

TOKYO_TZ = "Asia/Tokyo"
MORNING_START = time(9, 0)
MORNING_END = time(11, 30)
AFTERNOON_START = time(12, 30)
AFTERNOON_END = time(15, 30)


def _to_time(value: datetime | pd.Timestamp) -> time:
    return pd.Timestamp(value).time()


def is_morning_session(dt: datetime | pd.Timestamp) -> bool:
    current = _to_time(dt)
    return MORNING_START <= current <= MORNING_END


def is_afternoon_session(dt: datetime | pd.Timestamp) -> bool:
    current = _to_time(dt)
    return AFTERNOON_START <= current <= AFTERNOON_END


def get_session_name(dt: datetime | pd.Timestamp) -> str:
    if is_morning_session(dt):
        return "morning"
    if is_afternoon_session(dt):
        return "afternoon"
    return "closed"


def is_market_time(dt: datetime | pd.Timestamp) -> bool:
    return get_session_name(dt) != "closed"
