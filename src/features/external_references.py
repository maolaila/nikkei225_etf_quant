from __future__ import annotations

import numpy as np
import pandas as pd


OPTIONAL_REFERENCE_COLUMNS = [
    "futures_return_1m",
    "futures_return_3m",
    "futures_return_5m",
    "index_return_1m",
    "index_return_3m",
    "index_return_5m",
    "etf_vs_futures_gap",
    "etf_vs_index_gap",
    "etf_vs_inav_premium_bps",
]


def add_optional_reference_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Add optional external-reference feature columns when source columns exist.

    Supported raw columns are intentionally simple placeholders:
    `nikkei225_futures_mid`, `nikkei225_index`, and `etf_inav`.
    If they are absent, the feature columns are still created as NaN so reports
    can clearly identify ETF-only mode.
    """

    output = frame.copy()
    group_columns = [column for column in ["trade_date", "session"] if column in output]
    group = output.groupby(group_columns, sort=False) if group_columns else None

    if "nikkei225_futures_mid" in output:
        _add_returns(output, group, "nikkei225_futures_mid", "futures_return")
        output["etf_vs_futures_gap"] = output["return_1m"] - output["futures_return_1m"]
    if "nikkei225_index" in output:
        _add_returns(output, group, "nikkei225_index", "index_return")
        output["etf_vs_index_gap"] = output["return_1m"] - output["index_return_1m"]
    if "etf_inav" in output:
        output["etf_vs_inav_premium_bps"] = (output["mid_price_proxy"] / output["etf_inav"] - 1.0) * 10000.0

    for column in OPTIONAL_REFERENCE_COLUMNS:
        if column not in output:
            output[column] = np.nan
    return output


def _add_returns(output: pd.DataFrame, group: pd.core.groupby.DataFrameGroupBy | None, price_column: str, prefix: str) -> None:
    prices = pd.to_numeric(output[price_column], errors="coerce")
    for horizon in (1, 3, 5):
        if group is None:
            output[f"{prefix}_{horizon}m"] = prices.pct_change(horizon)
        else:
            output[f"{prefix}_{horizon}m"] = group[price_column].transform(lambda series: pd.to_numeric(series, errors="coerce").pct_change(horizon))

