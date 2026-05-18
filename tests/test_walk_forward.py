from __future__ import annotations

import pandas as pd

from src.validation.leakage_checks import LeakageChecker
from src.validation.purged_split import walk_forward_splits


def test_leakage_checker_rejects_label_columns_in_features():
    checker = LeakageChecker()
    features = pd.DataFrame({"timestamp": [pd.Timestamp("2026-01-01")], "future_return_pct": [1.0]})
    try:
        checker.assert_no_label_columns_in_features(features)
    except AssertionError:
        return
    raise AssertionError("Expected label leakage assertion")


def test_leakage_checker_rejects_features_after_label_range():
    checker = LeakageChecker()
    features = pd.DataFrame({"timestamp": pd.to_datetime(["2026-01-01", "2026-01-03"])})
    labels = pd.DataFrame({"timestamp": pd.to_datetime(["2026-01-01", "2026-01-02"])})
    try:
        checker.assert_no_future_timestamps(features, labels)
    except AssertionError:
        return
    raise AssertionError("Expected future timestamp leakage assertion")


def test_walk_forward_splits_create_multiple_purged_windows():
    timestamps = pd.Series(pd.date_range("2024-01-01 09:00", "2025-07-31 15:00", freq="1D"))
    windows = walk_forward_splits(
        timestamps,
        train_window_months=6,
        test_window_months=1,
        step_months=1,
        purge_minutes=60,
        min_train_rows=10,
        min_test_rows=5,
    )
    assert len(windows) >= 10
    assert all(window.train_end < window.test_start for window in windows)
    assert windows[1].train_start > windows[0].train_start
