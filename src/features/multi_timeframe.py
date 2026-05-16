from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from src.data.resampler import add_resampled_sets, resample_ohlcv


DEFAULT_INTRADAY_WINDOWS = (3, 5, 15)


def add_multi_timeframe_features(frame: pd.DataFrame, config: dict[str, Any] | None = None) -> pd.DataFrame:
    """Add trailing 3m/5m/15m and previous-daily context features.

    These features are computed at each 1-minute timestamp using only the
    current and prior rows within the same Tokyo session. Daily features are
    lagged by one full trading day before being mapped back to intraday rows.
    """

    config = config or {}
    windows = tuple(_configured_windows(config))
    output = frame.copy()
    if output.empty:
        return output

    output["timestamp"] = pd.to_datetime(output["timestamp"])
    output = output.sort_values("timestamp").reset_index(drop=True)
    if "trade_date" not in output:
        output["trade_date"] = output["timestamp"].dt.date.astype(str)
    if "session" not in output:
        output["session"] = ""

    session_group = output.groupby(["trade_date", "session"], sort=False)
    price = pd.to_numeric(output["mid_price_proxy"], errors="coerce") if "mid_price_proxy" in output else pd.to_numeric(output["close"], errors="coerce")
    output["_mtf_price"] = price

    for window in windows:
        prefix = f"tf{window}m"
        rolling_price = session_group["_mtf_price"]
        rolling_high = session_group["high"]
        rolling_low = session_group["low"]
        rolling_volume = session_group["volume"]
        rolling_turnover = session_group["turnover"]

        output[f"{prefix}_return"] = rolling_price.transform(lambda series, w=window: series.pct_change(w))
        output[f"{prefix}_range_pct"] = (
            rolling_high.transform(lambda series, w=window: series.rolling(w, min_periods=max(2, min(w, 3))).max())
            / rolling_low.transform(lambda series, w=window: series.rolling(w, min_periods=max(2, min(w, 3))).min())
            - 1.0
        )
        output[f"{prefix}_volume_sum"] = rolling_volume.transform(lambda series, w=window: series.rolling(w, min_periods=1).sum())
        turnover_sum = rolling_turnover.transform(lambda series, w=window: series.rolling(w, min_periods=1).sum())
        volume_sum = output[f"{prefix}_volume_sum"].replace(0, np.nan)
        output[f"{prefix}_vwap"] = turnover_sum / volume_sum
        output[f"{prefix}_price_vs_vwap_pct"] = (output["_mtf_price"] / output[f"{prefix}_vwap"] - 1.0) * 100.0
        output[f"{prefix}_momentum_confirm"] = np.sign(output[f"{prefix}_return"]).fillna(0.0)

    output = add_daily_context_features(output)
    output = output.drop(columns=["_mtf_price"])
    numeric = output.select_dtypes(include=[np.number]).columns
    output[numeric] = output[numeric].replace([np.inf, -np.inf], np.nan)
    return output


def add_daily_context_features(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    if output.empty:
        return output
    daily = (
        output.groupby("trade_date", sort=True)
        .agg(
            daily_open=("open", "first"),
            daily_high=("high", "max"),
            daily_low=("low", "min"),
            daily_close=("mid_price_proxy", "last") if "mid_price_proxy" in output else ("close", "last"),
            daily_volume=("volume", "sum"),
            daily_turnover=("turnover", "sum"),
        )
        .sort_index()
    )
    daily["daily_return"] = daily["daily_close"].pct_change()
    daily["daily_return_3d"] = daily["daily_close"].pct_change(3)
    daily["daily_return_5d"] = daily["daily_close"].pct_change(5)
    daily["daily_return_20d"] = daily["daily_close"].pct_change(20)
    daily["daily_volatility_20d"] = daily["daily_return"].rolling(20, min_periods=5).std()
    daily["daily_range_pct"] = daily["daily_high"] / daily["daily_low"].replace(0, np.nan) - 1.0
    lagged = daily.shift(1).add_prefix("prev_")
    for column in lagged.columns:
        output[column] = output["trade_date"].map(lagged[column]).astype(float)
    output["day_open_to_now_return"] = (
        (pd.to_numeric(output["mid_price_proxy"], errors="coerce") if "mid_price_proxy" in output else pd.to_numeric(output["close"], errors="coerce"))
        / output.groupby("trade_date")["open"].transform("first")
        - 1.0
    )
    return output


def _configured_windows(config: dict[str, Any]) -> list[int]:
    raw = config.get("features", {}).get("multi_timeframe", {}).get("intraday_windows_minutes", DEFAULT_INTRADAY_WINDOWS)
    windows: list[int] = []
    for value in raw:
        try:
            window = int(value)
        except (TypeError, ValueError):
            continue
        if window > 1 and window not in windows:
            windows.append(window)
    return windows or list(DEFAULT_INTRADAY_WINDOWS)


__all__ = ["add_multi_timeframe_features", "add_resampled_sets", "resample_ohlcv"]
