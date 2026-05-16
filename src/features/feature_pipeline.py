from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from src.data.data_lake import DataLake
from src.data.symbol_mapper import SymbolMapper
from src.features.external_references import add_optional_reference_features
from src.features.implied_nikkei import add_etf_implied_nikkei_features
from src.features.multi_timeframe import add_multi_timeframe_features


def _session_name(timestamp: pd.Timestamp) -> str:
    hhmm = timestamp.strftime("%H:%M")
    if "09:00" <= hhmm <= "11:30":
        return "morning"
    if "12:30" <= hhmm <= "15:30":
        return "afternoon"
    return "closed"


def _safe_pct_change(series: pd.Series, periods: int = 1) -> pd.Series:
    return series.pct_change(periods=periods).replace([np.inf, -np.inf], np.nan)


def build_feature_frame(config: dict[str, Any]) -> pd.DataFrame:
    lake = DataLake()
    bars = lake.read_frame("normalized", "minute_bars")
    bars["timestamp"] = pd.to_datetime(bars["timestamp"])
    bars["symbol"] = bars["symbol"].astype(str)
    reference_symbol = SymbolMapper(config).reference_symbol()
    implied_features = add_etf_implied_nikkei_features(bars, config)
    frame = bars[bars["symbol"] == reference_symbol].copy()
    if frame.empty:
        raise ValueError(f"Reference symbol {reference_symbol} is missing from normalized data")
    frame = frame.sort_values("timestamp").reset_index(drop=True)
    if not implied_features.empty:
        frame = frame.merge(implied_features, on="timestamp", how="left")
    reference_mid_column = f"etf_mid_{reference_symbol}"
    if reference_mid_column in frame:
        frame["mid_price_proxy"] = pd.to_numeric(frame[reference_mid_column], errors="coerce").fillna(frame["close"])
    else:
        frame["mid_price_proxy"] = frame["close"]
    frame["mid_price_source"] = "historical_close_proxy"
    frame["trade_date"] = frame["timestamp"].dt.date.astype(str)
    frame["session"] = frame["timestamp"].map(_session_name)
    session_group = frame.groupby(["symbol", "trade_date", "session"], sort=False)
    day_group = frame.groupby(["symbol", "trade_date"], sort=False)

    frame["return_1m"] = session_group["mid_price_proxy"].transform(lambda s: _safe_pct_change(s, 1))
    frame["return_3m"] = session_group["mid_price_proxy"].transform(lambda s: _safe_pct_change(s, 3))
    frame["return_5m"] = session_group["mid_price_proxy"].transform(lambda s: _safe_pct_change(s, 5))
    frame["return_15m"] = session_group["mid_price_proxy"].transform(lambda s: _safe_pct_change(s, 15))
    frame["return_30m"] = session_group["mid_price_proxy"].transform(lambda s: _safe_pct_change(s, 30))
    frame = add_optional_reference_features(frame)
    frame = add_multi_timeframe_features(frame, config).copy()
    session_group = frame.groupby(["symbol", "trade_date", "session"], sort=False)
    frame["ema_12"] = session_group["mid_price_proxy"].transform(lambda s: s.ewm(span=12, adjust=False, min_periods=12).mean())
    frame["ema_26"] = session_group["mid_price_proxy"].transform(lambda s: s.ewm(span=26, adjust=False, min_periods=26).mean())
    frame["macd"] = frame["ema_12"] - frame["ema_26"]
    frame["macd_signal"] = session_group["macd"].transform(lambda s: s.ewm(span=9, adjust=False, min_periods=9).mean())
    frame["macd_hist"] = frame["macd"] - frame["macd_signal"]
    frame["rsi_14"] = session_group["mid_price_proxy"].transform(_rsi)
    low_9 = session_group["low"].transform(lambda s: s.rolling(9, min_periods=3).min())
    high_9 = session_group["high"].transform(lambda s: s.rolling(9, min_periods=3).max())
    denominator = (high_9 - low_9).replace(0, np.nan)
    frame["kdj_k"] = ((frame["mid_price_proxy"] - low_9) / denominator * 100.0).clip(0, 100)
    frame["kdj_d"] = session_group["kdj_k"].transform(lambda s: s.rolling(3, min_periods=2).mean())
    frame["kdj_j"] = 3.0 * frame["kdj_k"] - 2.0 * frame["kdj_d"]
    daily_close = frame.groupby("trade_date")["mid_price_proxy"].last()
    prev_daily_return = daily_close.pct_change().shift(1)
    frame["return_1d_prev"] = frame["trade_date"].map(prev_daily_return).astype(float)

    cumulative_turnover = day_group["turnover"].cumsum()
    cumulative_volume = day_group["volume"].cumsum().replace(0, np.nan)
    frame["intraday_vwap"] = cumulative_turnover / cumulative_volume
    frame["price_vs_vwap_pct"] = (frame["mid_price_proxy"] / frame["intraday_vwap"] - 1.0) * 100.0
    frame["vwap_cross_direction"] = np.sign(frame["mid_price_proxy"] - frame["intraday_vwap"]).astype(float)
    vwap_cross = frame["vwap_cross_direction"].diff().fillna(0).ne(0).astype(int)
    frame["vwap_cross_count_30m"] = vwap_cross.rolling(30, min_periods=1).sum()

    frame["range_5m_pct"] = session_group["high"].transform(lambda s: s.rolling(5, min_periods=2).max()) / session_group[
        "low"
    ].transform(lambda s: s.rolling(5, min_periods=2).min()) - 1.0
    frame["range_15m_pct"] = session_group["high"].transform(lambda s: s.rolling(15, min_periods=2).max()) / session_group[
        "low"
    ].transform(lambda s: s.rolling(15, min_periods=2).min()) - 1.0
    frame["range_30m_pct"] = session_group["high"].transform(lambda s: s.rolling(30, min_periods=2).max()) / session_group[
        "low"
    ].transform(lambda s: s.rolling(30, min_periods=2).min()) - 1.0
    frame["realized_vol_15m"] = session_group["return_1m"].transform(lambda s: s.rolling(15, min_periods=3).std())
    frame["realized_vol_30m"] = session_group["return_1m"].transform(lambda s: s.rolling(30, min_periods=3).std())
    candle_range = (frame["high"] - frame["low"]).replace(0, np.nan)
    frame["candle_body_pct"] = (frame["close"] - frame["open"]).abs() / frame["open"] * 100.0
    frame["upper_shadow_pct"] = (frame["high"] - frame[["open", "close"]].max(axis=1)) / candle_range * 100.0
    frame["lower_shadow_pct"] = (frame[["open", "close"]].min(axis=1) - frame["low"]) / candle_range * 100.0

    frame["volume_ratio_5m"] = frame["volume"] / session_group["volume"].transform(lambda s: s.rolling(5, min_periods=2).mean())
    frame["volume_ratio_20m"] = frame["volume"] / session_group["volume"].transform(lambda s: s.rolling(20, min_periods=2).mean())
    frame["turnover_ratio_20m"] = frame["turnover"] / session_group["turnover"].transform(
        lambda s: s.rolling(20, min_periods=2).mean()
    )
    frame["volume_spike_flag"] = (frame["volume_ratio_20m"] > 1.5).astype(int)

    minute_number = day_group.cumcount()
    opening_mask = minute_number < 30
    opening_high_seen = frame["high"].where(opening_mask)
    opening_low_seen = frame["low"].where(opening_mask)
    frame["opening_range_high_30m"] = opening_high_seen.groupby(frame["trade_date"]).cummax()
    frame["opening_range_low_30m"] = opening_low_seen.groupby(frame["trade_date"]).cummin()
    frame["opening_range_high_30m"] = frame["opening_range_high_30m"].groupby(frame["trade_date"]).ffill()
    frame["opening_range_low_30m"] = frame["opening_range_low_30m"].groupby(frame["trade_date"]).ffill()
    frame["breakout_opening_high"] = (frame["mid_price_proxy"] > frame["opening_range_high_30m"]).astype(int)
    frame["breakdown_opening_low"] = (frame["mid_price_proxy"] < frame["opening_range_low_30m"]).astype(int)

    previous_close = daily_close.shift(1)
    first_open = frame.groupby("trade_date")["open"].transform("first")
    frame["gap_pct"] = (first_open / frame["trade_date"].map(previous_close).astype(float) - 1.0) * 100.0
    frame["gap_direction"] = np.sign(frame["gap_pct"]).fillna(0)
    frame["gap_filled_flag"] = (
        ((frame["gap_direction"] > 0) & (frame["low"] <= frame["trade_date"].map(previous_close).astype(float)))
        | ((frame["gap_direction"] < 0) & (frame["high"] >= frame["trade_date"].map(previous_close).astype(float)))
    ).astype(int)
    frame["gap_fill_distance_pct"] = (
        (frame["mid_price_proxy"] - frame["trade_date"].map(previous_close).astype(float)).abs() / frame["mid_price_proxy"] * 100.0
    )

    regime_config = config.get("features", {}).get("market_regime", {})
    regime_lookback = int(regime_config.get("volatility_median_lookback_minutes", 7800))
    regime_min_periods = int(regime_config.get("volatility_median_min_periods", 120))
    historical_vol_median = frame.groupby("symbol")["realized_vol_30m"].transform(
        lambda series: series.shift(1).rolling(regime_lookback, min_periods=regime_min_periods).median()
    )
    fallback_vol_median = frame.groupby("symbol")["realized_vol_30m"].transform(
        lambda series: series.shift(1).expanding(min_periods=1).median()
    )
    regime_threshold = historical_vol_median.fillna(fallback_vol_median)
    frame["market_regime"] = np.where(frame["realized_vol_30m"] > regime_threshold, "trend", "range")
    frame.loc[frame["realized_vol_30m"].isna() | regime_threshold.isna(), "market_regime"] = "unknown"
    numeric = frame.select_dtypes(include=[np.number]).columns
    frame[numeric] = frame[numeric].replace([np.inf, -np.inf], np.nan)
    return frame


def _rsi(series: pd.Series, window: int = 14) -> pd.Series:
    diff = series.diff()
    gain = diff.clip(lower=0).rolling(window, min_periods=window).mean()
    loss = (-diff.clip(upper=0)).rolling(window, min_periods=window).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def build_features(config: dict[str, Any]) -> tuple[pd.DataFrame, str]:
    frame = build_feature_frame(config)
    path = DataLake().write_frame(frame, "features", "features")
    return frame, str(path)
