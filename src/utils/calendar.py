from __future__ import annotations

from datetime import date, datetime, time

import pandas as pd


def business_days(start: str | date, end: str | date, max_days: int | None = None) -> pd.DatetimeIndex:
    days = pd.bdate_range(start=start, end=end)
    if max_days and len(days) > max_days:
        days = days[-max_days:]
    return days


def tokyo_session_minutes(day: pd.Timestamp) -> pd.DatetimeIndex:
    morning = pd.date_range(
        datetime.combine(day.date(), time(9, 0)),
        datetime.combine(day.date(), time(11, 30)),
        freq="1min",
    )
    afternoon = pd.date_range(
        datetime.combine(day.date(), time(12, 30)),
        datetime.combine(day.date(), time(15, 30)),
        freq="1min",
    )
    return morning.append(afternoon)

