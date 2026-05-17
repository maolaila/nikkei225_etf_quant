from __future__ import annotations

import pandas as pd

from src.validation.event_audit import build_event_audit_frames
from src.validation.event_audit import corporate_action_candidate_dates
from src.validation.event_audit import market_event_dates


def test_event_audit_separates_market_events_from_corporate_action_candidates():
    bars = pd.DataFrame(
        [
            _bar("2026-01-05 09:00", "1321", 100.0, 100.0, 99.0, 100.0),
            _bar("2026-01-05 15:30", "1321", 100.0, 100.0, 99.0, 100.0),
            _bar("2026-01-06 09:00", "1321", 100.0, 101.0, 99.0, 100.0),
            _bar("2026-01-06 09:01", "1321", 100.0, 101.0, 89.0, 90.0),
            _bar("2026-01-06 15:30", "1321", 90.0, 91.0, 89.0, 90.0),
            _bar("2026-01-05 09:00", "1357", 117.0, 118.0, 116.0, 117.0),
            _bar("2026-01-05 15:30", "1357", 117.0, 118.0, 116.0, 117.0),
            _bar("2026-01-06 09:00", "1357", 11700.0, 11710.0, 11690.0, 11700.0),
            _bar("2026-01-06 15:30", "1357", 11700.0, 11710.0, 11690.0, 11700.0),
        ]
    )

    daily_flags, abnormal_minutes, summary = build_event_audit_frames(bars, _config())

    assert market_event_dates(daily_flags) == ["2026-01-06"]
    assert corporate_action_candidate_dates(daily_flags) == ["2026-01-06"]
    split_row = daily_flags[(daily_flags["symbol"] == "1357") & (daily_flags["trade_date"] == "2026-01-06")].iloc[0]
    assert split_row["corporate_action_candidate"]
    assert not split_row["event_day"]
    assert split_row["training_exclusion_candidate"]
    assert not abnormal_minutes.empty
    assert summary["abnormal_minute_bar_count"] >= 1


def _bar(timestamp: str, symbol: str, open_: float, high: float, low: float, close: float) -> dict[str, object]:
    return {
        "timestamp": pd.Timestamp(timestamp),
        "symbol": symbol,
        "interval": "1m",
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1000,
        "turnover": close * 1000,
        "provider": "test",
        "adjusted": True,
    }


def _config() -> dict[str, object]:
    return {
        "historical": {
            "event_audit": {
                "black_swan_abs_underlying_return_pct": 7.5,
                "extreme_intraday_underlying_range_pct": 7.5,
                "abnormal_minute_abs_underlying_return_pct": 1.0,
                "corporate_action_raw_close_jump_ratio": 5.0,
                "corporate_action_abs_close_return_pct": 50.0,
            }
        },
        "etf_universe": {
            "long_1x": {
                "direction": 1,
                "leverage": 1,
                "candidates": [{"symbol": "1321", "enabled": True, "priority": 1}],
            },
            "short_2x": {
                "direction": -1,
                "leverage": 2,
                "candidates": [{"symbol": "1357", "enabled": True, "priority": 1}],
            },
        },
    }
