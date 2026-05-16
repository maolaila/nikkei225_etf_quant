from __future__ import annotations

import pandas as pd

from market_data_collector.resample import resample_ohlcv


def _minute_frame(datetimes: list[str]) -> pd.DataFrame:
    rows = []
    for index, timestamp in enumerate(pd.to_datetime(datetimes), start=1):
        rows.append(
            {
                "datetime": timestamp,
                "date": timestamp.strftime("%Y-%m-%d"),
                "time": timestamp.strftime("%H:%M:%S"),
                "symbol": "1570",
                "code": "15700",
                "open": float(index),
                "high": float(index + 1),
                "low": float(index - 1),
                "close": float(index + 0.5),
                "volume": index * 10,
                "turnover": index * 100,
                "provider": "unit",
                "fetched_at": "2026-01-01T00:00:00Z",
            }
        )
    return pd.DataFrame(rows)


def test_5min_ohlc_aggregation_is_correct() -> None:
    frame = _minute_frame([f"2025-01-06 09:0{i}:00" for i in range(5)])
    out = resample_ohlcv(frame, "5min")
    assert len(out) == 1
    row = out.iloc[0]
    assert row["open"] == 1.0
    assert row["high"] == 6.0
    assert row["low"] == 0.0
    assert row["close"] == 5.5
    assert row["volume"] == 150
    assert row["turnover"] == 1500


def test_resample_does_not_create_lunch_break_fake_bars() -> None:
    frame = _minute_frame(["2025-01-06 11:29:00", "2025-01-06 11:30:00", "2025-01-06 12:30:00", "2025-01-06 12:31:00"])
    out = resample_ohlcv(frame, "5min")
    times = pd.to_datetime(out["datetime"]).dt.time.astype(str).tolist()
    assert not any("11:35:00" <= value < "12:30:00" for value in times)
    assert "12:30:00" in times


def test_1d_aggregation_is_correct() -> None:
    frame = _minute_frame(["2025-01-06 09:00:00", "2025-01-06 09:01:00", "2025-01-06 15:30:00"])
    out = resample_ohlcv(frame, "1d")
    assert len(out) == 1
    row = out.iloc[0]
    assert row["time"] == "15:30:00"
    assert row["open"] == 1.0
    assert row["high"] == 4.0
    assert row["low"] == 0.0
    assert row["close"] == 3.5
    assert row["volume"] == 60
