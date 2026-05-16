from __future__ import annotations

import pandas as pd

from market_data_collector.calendar import AFTERNOON_START, MORNING_START, get_session_name
from market_data_collector.models import STANDARD_COLUMNS


def resample_ohlcv(frame: pd.DataFrame, target_interval: str) -> pd.DataFrame:
    if target_interval not in {"3min", "5min", "30min", "1d"}:
        raise ValueError(f"Unsupported target interval: {target_interval}")
    if frame.empty:
        return pd.DataFrame(columns=STANDARD_COLUMNS)
    data = frame.copy()
    data["datetime"] = pd.to_datetime(data["datetime"])
    data = data.sort_values("datetime").reset_index(drop=True)
    data["date"] = data["datetime"].dt.strftime("%Y-%m-%d")
    if target_interval == "1d":
        return _resample_daily(data)
    minutes = int(target_interval.removesuffix("min"))
    pieces: list[pd.DataFrame] = []
    for (_, session), group in data.groupby(["date", data["datetime"].map(get_session_name)], sort=True):
        if session == "closed" or group.empty:
            continue
        session_start = MORNING_START if session == "morning" else AFTERNOON_START
        session_start_ts = pd.Timestamp.combine(group["datetime"].iloc[0].date(), session_start)
        if group["datetime"].dt.tz is not None:
            session_start_ts = session_start_ts.tz_localize(group["datetime"].dt.tz)
        offset_minutes = ((group["datetime"] - session_start_ts).dt.total_seconds() // 60).astype(int)
        bucket_start = session_start_ts + pd.to_timedelta((offset_minutes // minutes) * minutes, unit="min")
        prepared = group.assign(_bucket=bucket_start)
        pieces.append(_aggregate(prepared, "_bucket"))
    if not pieces:
        return pd.DataFrame(columns=STANDARD_COLUMNS)
    return _finalize(pd.concat(pieces, ignore_index=True))


def _resample_daily(data: pd.DataFrame) -> pd.DataFrame:
    output = _aggregate(data, "date")
    output["datetime"] = pd.to_datetime(output["date"] + " 15:30:00")
    if pd.to_datetime(data["datetime"]).dt.tz is not None:
        output["datetime"] = output["datetime"].dt.tz_localize(pd.to_datetime(data["datetime"]).dt.tz)
    return _finalize(output)


def _aggregate(data: pd.DataFrame, group_column: str) -> pd.DataFrame:
    grouped = data.groupby(group_column, sort=True, dropna=True)
    output = grouped.agg(
        datetime=("datetime", "first"),
        symbol=("symbol", "first"),
        code=("code", "first"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        turnover=("turnover", "sum"),
        provider=("provider", "first"),
        fetched_at=("fetched_at", "first"),
    ).reset_index(drop=True)
    if group_column == "_bucket":
        output["datetime"] = list(grouped.groups.keys())
    elif group_column == "date":
        output["date"] = output["datetime"].dt.strftime("%Y-%m-%d")
    return output


def _finalize(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.dropna(subset=["open", "close"]).copy()
    result["datetime"] = pd.to_datetime(result["datetime"])
    result["date"] = result["datetime"].dt.strftime("%Y-%m-%d")
    result["time"] = result["datetime"].dt.strftime("%H:%M:%S")
    ordered = [column for column in STANDARD_COLUMNS if column in result.columns]
    extras = [column for column in result.columns if column not in ordered]
    return result[ordered + extras].sort_values("datetime").reset_index(drop=True)
