from __future__ import annotations

import pandas as pd

from src.features.implied_nikkei import add_etf_implied_nikkei_features


def test_implied_nikkei_features_normalize_leveraged_inverse_etfs():
    rows = []
    prices = {
        "1321": [100.0, 101.0],
        "1570": [100.0, 102.0],
        "1571": [100.0, 99.0],
        "1357": [100.0, 98.0],
    }
    for symbol, values in prices.items():
        for timestamp, close in zip(pd.date_range("2026-01-05 09:00", periods=2, freq="1min"), values):
            rows.append(
                {
                    "timestamp": timestamp,
                    "symbol": symbol,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 100,
                    "turnover": close * 100,
                }
            )
    features = add_etf_implied_nikkei_features(pd.DataFrame(rows))
    second = features[features["timestamp"] == pd.Timestamp("2026-01-05 09:01")].iloc[0]

    assert round(second["implied_nikkei_1321_bps"], 6) == 100.0
    assert round(second["implied_nikkei_1570_bps"], 6) == 100.0
    assert round(second["implied_nikkei_1571_bps"], 6) == 100.0
    assert round(second["implied_nikkei_1357_bps"], 6) == 100.0
    assert abs(second["implied_nikkei_dispersion_bps"]) < 1e-9
    assert second["etf_implied_source_count"] == 4
    assert second["historical_bid_ask_unavailable"] == 1

