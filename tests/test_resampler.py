from __future__ import annotations

import pandas as pd

from src.data.resampler import resample_ohlcv


def test_resample_ohlcv_uses_past_bars_only_at_right_label():
    frame = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-05 09:00", periods=3, freq="1min"),
            "symbol": ["1321"] * 3,
            "open": [100, 101, 102],
            "high": [101, 102, 103],
            "low": [99, 100, 101],
            "close": [100.5, 101.5, 102.5],
            "volume": [10, 20, 30],
            "turnover": [1000, 2020, 3060],
        }
    )
    out = resample_ohlcv(frame, "3min")
    assert out.iloc[0]["timestamp"] == pd.Timestamp("2026-01-05 09:00")
    assert out.iloc[-1]["close"] == 102.5
    assert out.iloc[-1]["volume"] == 50

