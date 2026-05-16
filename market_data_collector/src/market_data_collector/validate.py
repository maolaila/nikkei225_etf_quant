from __future__ import annotations

import pandas as pd


def validate_dataframe(df: pd.DataFrame, provider: str, interval: str, symbol: str) -> pd.DataFrame:
    columns = [
        "provider",
        "interval",
        "symbol",
        "date",
        "row_count",
        "duplicate_datetime_count",
        "invalid_high_low_count",
        "invalid_open_range_count",
        "invalid_close_range_count",
        "negative_volume_count",
        "negative_turnover_count",
        "warning",
        "error",
    ]
    if df.empty:
        return pd.DataFrame(
            [
                {
                    "provider": provider,
                    "interval": interval,
                    "symbol": symbol,
                    "date": "",
                    "row_count": 0,
                    "duplicate_datetime_count": 0,
                    "invalid_high_low_count": 0,
                    "invalid_open_range_count": 0,
                    "invalid_close_range_count": 0,
                    "negative_volume_count": 0,
                    "negative_turnover_count": 0,
                    "warning": "no rows",
                    "error": "no rows",
                }
            ],
            columns=columns,
        )
    frame = df.copy()
    frame["datetime"] = pd.to_datetime(frame["datetime"])
    frame["date"] = frame.get("date", frame["datetime"].dt.strftime("%Y-%m-%d")).astype(str)
    rows: list[dict[str, object]] = []
    for trade_date, group in frame.groupby("date", sort=True):
        duplicate_count = int(group["datetime"].duplicated().sum())
        invalid_high_low = int((group["high"].astype(float) < group["low"].astype(float)).sum())
        open_values = group["open"].astype(float)
        close_values = group["close"].astype(float)
        high_values = group["high"].astype(float)
        low_values = group["low"].astype(float)
        invalid_open = int(((open_values > high_values) | (open_values < low_values)).sum())
        invalid_close = int(((close_values > high_values) | (close_values < low_values)).sum())
        negative_volume = int((group["volume"].fillna(0).astype(float) < 0).sum()) if "volume" in group else 0
        negative_turnover = int((group["turnover"].fillna(0).astype(float) < 0).sum()) if "turnover" in group else 0
        error_items = []
        if duplicate_count:
            error_items.append("duplicate datetime")
        if invalid_high_low:
            error_items.append("high < low")
        if invalid_open:
            error_items.append("open outside high/low")
        if invalid_close:
            error_items.append("close outside high/low")
        if negative_volume:
            error_items.append("negative volume")
        if negative_turnover:
            error_items.append("negative turnover")
        warning = ""
        if interval != "1d":
            warning = "minute data may omit no-trade minutes; missing minute bars are warnings, not automatic errors"
        rows.append(
            {
                "provider": provider,
                "interval": interval,
                "symbol": symbol,
                "date": trade_date,
                "row_count": int(len(group)),
                "duplicate_datetime_count": duplicate_count,
                "invalid_high_low_count": invalid_high_low,
                "invalid_open_range_count": invalid_open,
                "invalid_close_range_count": invalid_close,
                "negative_volume_count": negative_volume,
                "negative_turnover_count": negative_turnover,
                "warning": warning,
                "error": "; ".join(error_items),
            }
        )
    return pd.DataFrame(rows, columns=columns)
