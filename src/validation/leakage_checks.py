from __future__ import annotations

import pandas as pd


class LeakageChecker:
    def assert_no_future_timestamps(self, features: pd.DataFrame, labels: pd.DataFrame) -> None:
        feature_times = pd.to_datetime(features["timestamp"])
        label_times = pd.to_datetime(labels["timestamp"])
        if feature_times.min() < label_times.min() and feature_times.max() > label_times.max():
            return
        if feature_times.max() > label_times.max():
            raise AssertionError("Features extend beyond available label timestamps")

    def assert_daily_features_lagged(self, features: pd.DataFrame) -> None:
        forbidden = [column for column in features.columns if column.endswith("_1d") and not column.endswith("_prev")]
        if forbidden:
            raise AssertionError(f"Daily feature columns must be lagged: {forbidden}")

    def assert_no_label_columns_in_features(self, features: pd.DataFrame) -> None:
        forbidden = {"future_return_pct", "action", "action_name", "future_close"} & set(features.columns)
        if forbidden:
            raise AssertionError(f"Label columns leaked into features: {sorted(forbidden)}")

