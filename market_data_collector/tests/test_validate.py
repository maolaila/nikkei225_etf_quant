from __future__ import annotations

import pandas as pd

from market_data_collector.validate import validate_dataframe


def _frame(**overrides: object) -> pd.DataFrame:
    row = {
        "datetime": pd.Timestamp("2025-01-06 15:30:00"),
        "date": "2025-01-06",
        "symbol": "1570",
        "open": 10.0,
        "high": 12.0,
        "low": 9.0,
        "close": 11.0,
        "volume": 100.0,
        "turnover": 1000.0,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def _report(frame: pd.DataFrame) -> pd.Series:
    return validate_dataframe(frame, "unit", "1d", "1570").iloc[0]


def test_detects_high_less_than_low() -> None:
    row = _report(_frame(high=8.0, low=9.0))
    assert row["invalid_high_low_count"] == 1
    assert "high < low" in row["error"]


def test_detects_open_outside_high_low() -> None:
    row = _report(_frame(open=13.0))
    assert row["invalid_open_range_count"] == 1


def test_detects_close_outside_high_low() -> None:
    row = _report(_frame(close=8.0))
    assert row["invalid_close_range_count"] == 1


def test_detects_duplicate_datetime() -> None:
    frame = pd.concat([_frame(), _frame()], ignore_index=True)
    row = _report(frame)
    assert row["duplicate_datetime_count"] == 1


def test_detects_negative_volume() -> None:
    row = _report(_frame(volume=-1.0))
    assert row["negative_volume_count"] == 1
