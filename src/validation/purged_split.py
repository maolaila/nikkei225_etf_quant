from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class SplitWindow:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def simple_time_split(timestamps: pd.Series, train_fraction: float = 0.6) -> SplitWindow:
    ordered = pd.to_datetime(timestamps).sort_values().reset_index(drop=True)
    if ordered.empty:
        raise ValueError("Cannot split empty timestamp series")
    split_index = max(1, min(len(ordered) - 1, int(len(ordered) * train_fraction)))
    return SplitWindow(
        train_start=ordered.iloc[0],
        train_end=ordered.iloc[split_index - 1],
        test_start=ordered.iloc[split_index],
        test_end=ordered.iloc[-1],
    )


def walk_forward_splits(
    timestamps: pd.Series,
    train_window_months: int = 12,
    test_window_months: int = 1,
    step_months: int = 1,
    purge_minutes: int = 60,
    min_train_rows: int = 100,
    min_test_rows: int = 1,
) -> list[SplitWindow]:
    ordered = pd.Series(pd.to_datetime(timestamps).dropna().sort_values().unique())
    if ordered.empty:
        raise ValueError("Cannot split empty timestamp series")

    train_window_months = max(1, int(train_window_months))
    test_window_months = max(1, int(test_window_months))
    step_months = max(1, int(step_months))
    purge_delta = pd.Timedelta(minutes=max(0, int(purge_minutes)))

    windows: list[SplitWindow] = []
    train_start_cutoff = pd.Timestamp(ordered.iloc[0])
    data_end = pd.Timestamp(ordered.iloc[-1])

    while True:
        train_end_cutoff = train_start_cutoff + pd.DateOffset(months=train_window_months)
        test_start_cutoff = train_end_cutoff + purge_delta
        test_end_cutoff = test_start_cutoff + pd.DateOffset(months=test_window_months)
        if test_start_cutoff > data_end:
            break

        train_values = ordered[(ordered >= train_start_cutoff) & (ordered < train_end_cutoff)]
        test_values = ordered[(ordered >= test_start_cutoff) & (ordered < test_end_cutoff)]
        if len(train_values) >= min_train_rows and len(test_values) >= min_test_rows:
            windows.append(
                SplitWindow(
                    train_start=pd.Timestamp(train_values.iloc[0]),
                    train_end=pd.Timestamp(train_values.iloc[-1]),
                    test_start=pd.Timestamp(test_values.iloc[0]),
                    test_end=pd.Timestamp(test_values.iloc[-1]),
                )
            )

        train_start_cutoff = train_start_cutoff + pd.DateOffset(months=step_months)
        if train_start_cutoff >= data_end:
            break

    return windows
