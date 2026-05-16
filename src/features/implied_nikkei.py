from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from src.market.session import session_name


@dataclass(frozen=True)
class EtfImpliedSpec:
    symbol: str
    direction: int
    leverage: float


DEFAULT_IMPLIED_SPECS = (
    EtfImpliedSpec("1321", 1, 1.0),
    EtfImpliedSpec("1570", 1, 2.0),
    EtfImpliedSpec("1571", -1, 1.0),
    EtfImpliedSpec("1357", -1, 2.0),
)


def add_etf_implied_nikkei_features(bars: pd.DataFrame, config: dict[str, Any] | None = None) -> pd.DataFrame:
    """Build timestamp-level cross-ETF Nikkei consistency features.

    Historical J-Quants minute bars do not include bid/ask. In that case this
    uses close as an explicit mid-price proxy and exposes
    `has_real_bid_ask=0`, so downstream reports can flag the limitation.
    """

    config = config or {}
    specs = _specs_from_config(config) or list(DEFAULT_IMPLIED_SPECS)
    symbols = {spec.symbol for spec in specs}
    if bars.empty:
        return _empty_frame(specs)

    data = bars.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"])
    data["symbol"] = data["symbol"].astype(str)
    data = data[data["symbol"].isin(symbols)].sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    if data.empty:
        return _empty_frame(specs)

    has_bid_ask = {"best_bid", "best_ask"}.issubset(data.columns)
    if has_bid_ask:
        bid = pd.to_numeric(data["best_bid"], errors="coerce")
        ask = pd.to_numeric(data["best_ask"], errors="coerce")
        mid = (bid + ask) / 2.0
        valid_mid = bid.gt(0) & ask.gt(0) & ask.ge(bid)
        data["mid_price_proxy"] = mid.where(valid_mid)
        data["has_real_bid_ask"] = valid_mid.astype(int)
        data["spread_bps"] = ((ask - bid) / data["mid_price_proxy"] * 10000.0).replace([np.inf, -np.inf], np.nan)
    else:
        data["mid_price_proxy"] = pd.to_numeric(data["close"], errors="coerce")
        data["has_real_bid_ask"] = 0
        data["spread_bps"] = np.nan

    data["trade_date"] = data["timestamp"].dt.date.astype(str)
    data["session_bucket"] = data["timestamp"].map(_feature_session_bucket)
    group = data.groupby(["symbol", "trade_date", "session_bucket"], sort=False)
    data["mid_return_1m"] = group["mid_price_proxy"].pct_change().replace([np.inf, -np.inf], np.nan)

    pieces: list[pd.DataFrame] = []
    for spec in specs:
        one = data[data["symbol"] == spec.symbol][["timestamp", "mid_price_proxy", "mid_return_1m", "spread_bps", "has_real_bid_ask"]].copy()
        one = one.rename(
            columns={
                "mid_price_proxy": f"etf_mid_{spec.symbol}",
                "mid_return_1m": f"etf_return_{spec.symbol}_1m",
                "spread_bps": f"etf_spread_{spec.symbol}_bps",
                "has_real_bid_ask": f"etf_has_real_bid_ask_{spec.symbol}",
            }
        )
        implied = one[f"etf_return_{spec.symbol}_1m"] * spec.direction / spec.leverage
        one[f"implied_nikkei_{spec.symbol}"] = implied
        one[f"implied_nikkei_{spec.symbol}_bps"] = implied * 10000.0
        pieces.append(one.set_index("timestamp"))

    wide = pd.concat(pieces, axis=1).sort_index()
    implied_columns = [f"implied_nikkei_{spec.symbol}" for spec in specs]
    implied_bps_columns = [f"implied_nikkei_{spec.symbol}_bps" for spec in specs]
    available_implied = wide.reindex(columns=implied_columns)
    available_implied_bps = wide.reindex(columns=implied_bps_columns)

    wide["implied_nikkei_median"] = available_implied.median(axis=1, skipna=True)
    wide["implied_nikkei_mean"] = available_implied.mean(axis=1, skipna=True)
    wide["implied_nikkei_dispersion"] = available_implied.max(axis=1, skipna=True) - available_implied.min(axis=1, skipna=True)
    wide["implied_nikkei_max_gap"] = _max_abs_gap_from_median(available_implied)
    wide["implied_nikkei_median_bps"] = available_implied_bps.median(axis=1, skipna=True)
    wide["implied_nikkei_mean_bps"] = available_implied_bps.mean(axis=1, skipna=True)
    wide["implied_nikkei_dispersion_bps"] = available_implied_bps.max(axis=1, skipna=True) - available_implied_bps.min(axis=1, skipna=True)
    wide["implied_nikkei_max_gap_bps"] = _max_abs_gap_from_median(available_implied_bps)
    wide["etf_implied_source_count"] = available_implied.notna().sum(axis=1)
    wide["etf_implied_all_core_available"] = (wide["etf_implied_source_count"] == len(specs)).astype(int)
    wide["historical_bid_ask_unavailable"] = (wide[[f"etf_has_real_bid_ask_{spec.symbol}" for spec in specs]].sum(axis=1) == 0).astype(int)

    if "1321" in symbols and "1570" in symbols:
        wide["etf_pair_gap_1570_1321_bps"] = wide.get("implied_nikkei_1570_bps") - wide.get("implied_nikkei_1321_bps")
    if "1321" in symbols and "1571" in symbols:
        wide["etf_pair_gap_1571_1321_bps"] = wide.get("implied_nikkei_1571_bps") - wide.get("implied_nikkei_1321_bps")
    if "1321" in symbols and "1357" in symbols:
        wide["etf_pair_gap_1357_1321_bps"] = wide.get("implied_nikkei_1357_bps") - wide.get("implied_nikkei_1321_bps")

    wide = wide.reset_index()
    numeric = wide.select_dtypes(include=[np.number]).columns
    wide[numeric] = wide[numeric].replace([np.inf, -np.inf], np.nan)
    return wide


def _specs_from_config(config: dict[str, Any]) -> list[EtfImpliedSpec]:
    universe = config.get("etf_universe", {})
    specs: list[EtfImpliedSpec] = []
    for bucket in universe.values():
        try:
            direction = int(bucket.get("direction", 0))
            leverage = float(bucket.get("leverage", 1.0))
        except (TypeError, ValueError):
            continue
        for candidate in bucket.get("candidates", []):
            if candidate.get("enabled", False) is not True:
                continue
            symbol = str(candidate.get("symbol", ""))
            if symbol in {"1321", "1570", "1571", "1357"} and direction and leverage:
                specs.append(EtfImpliedSpec(symbol, direction, leverage))
    order = {spec.symbol: index for index, spec in enumerate(DEFAULT_IMPLIED_SPECS)}
    return sorted(specs, key=lambda spec: order.get(spec.symbol, 999))


def _feature_session_bucket(timestamp: pd.Timestamp) -> str:
    name = session_name(timestamp)
    if name == "morning_close":
        return "morning"
    if name == "afternoon_close":
        return "afternoon"
    return name


def _max_abs_gap_from_median(frame: pd.DataFrame) -> pd.Series:
    median = frame.median(axis=1, skipna=True)
    return frame.sub(median, axis=0).abs().max(axis=1, skipna=True)


def _empty_frame(specs: list[EtfImpliedSpec] | tuple[EtfImpliedSpec, ...]) -> pd.DataFrame:
    columns = ["timestamp"]
    for spec in specs:
        columns.extend(
            [
                f"etf_mid_{spec.symbol}",
                f"etf_return_{spec.symbol}_1m",
                f"etf_spread_{spec.symbol}_bps",
                f"etf_has_real_bid_ask_{spec.symbol}",
                f"implied_nikkei_{spec.symbol}",
                f"implied_nikkei_{spec.symbol}_bps",
            ]
        )
    columns.extend(
        [
            "implied_nikkei_median",
            "implied_nikkei_mean",
            "implied_nikkei_dispersion",
            "implied_nikkei_max_gap",
            "implied_nikkei_median_bps",
            "implied_nikkei_mean_bps",
            "implied_nikkei_dispersion_bps",
            "implied_nikkei_max_gap_bps",
            "etf_implied_source_count",
            "etf_implied_all_core_available",
            "historical_bid_ask_unavailable",
        ]
    )
    return pd.DataFrame(columns=columns)

