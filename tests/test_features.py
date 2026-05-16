from __future__ import annotations

import pandas as pd

from src.features.feature_pipeline import build_feature_frame


class FakeLake:
    def read_frame(self, layer, name):
        minutes = list(pd.date_range("2026-01-05 09:00", periods=40, freq="1min"))
        return pd.DataFrame(
            {
                "timestamp": minutes,
                "symbol": ["1321"] * len(minutes),
                "interval": ["1m"] * len(minutes),
                "open": range(100, 100 + len(minutes)),
                "high": range(101, 101 + len(minutes)),
                "low": range(99, 99 + len(minutes)),
                "close": range(100, 100 + len(minutes)),
                "volume": [1000] * len(minutes),
                "turnover": [100000] * len(minutes),
                "provider": ["test"] * len(minutes),
                "adjusted": [True] * len(minutes),
            }
        )


def test_feature_pipeline_creates_trailing_momentum(monkeypatch):
    monkeypatch.setattr("src.features.feature_pipeline.DataLake", lambda: FakeLake())
    config = {"labeling": {"reference_symbol": "1321"}}
    features = build_feature_frame(config)
    assert "return_5m" in features.columns
    assert features["return_5m"].iloc[10] > 0
    assert "future_return_pct" not in features.columns


def test_opening_range_features_only_use_seen_bars(monkeypatch):
    monkeypatch.setattr("src.features.feature_pipeline.DataLake", lambda: FakeLake())
    config = {"labeling": {"reference_symbol": "1321"}}
    features = build_feature_frame(config)

    assert features["opening_range_high_30m"].iloc[0] == 101
    assert features["opening_range_high_30m"].iloc[10] == 111
    assert features["opening_range_high_30m"].iloc[29] == 130
    assert features["opening_range_high_30m"].iloc[30] == 130
