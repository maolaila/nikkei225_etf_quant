from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from src.config.schema import ACTION_NAME_TO_ID
from src.data.data_lake import DataLake


def action_from_future_return(future_return_pct: float, config: dict[str, Any]) -> str:
    thresholds = config.get("labeling", {}).get("thresholds", {})
    strong_long = float(thresholds.get("strong_long_return_pct", 0.45))
    weak_long = float(thresholds.get("weak_long_return_pct", 0.20))
    strong_short = float(thresholds.get("strong_short_return_pct", -0.45))
    weak_short = float(thresholds.get("weak_short_return_pct", -0.20))
    neutral_abs = float(thresholds.get("neutral_abs_return_pct", 0.15))
    if abs(future_return_pct) < neutral_abs:
        return "flat"
    if future_return_pct >= strong_long:
        return "long_2x"
    if future_return_pct >= weak_long:
        return "long_1x"
    if future_return_pct <= strong_short:
        return "short_2x"
    if future_return_pct <= weak_short:
        return "short_1x"
    return "flat"


def apply_cost_awareness(action: str, future_return_pct: float, config: dict[str, Any]) -> str:
    cost_cfg = config.get("cost_aware_labeling", {})
    if not cost_cfg.get("enabled", True) or action == "flat":
        return action
    costs = cost_cfg.get("estimated_round_trip_cost_pct", {})
    cost = abs(float(costs.get(action, 0.0)))
    if abs(future_return_pct) <= cost:
        return "flat"
    return action


def build_labels(config: dict[str, Any]) -> tuple[pd.DataFrame, str]:
    lake = DataLake()
    features = lake.read_frame("features", "features")
    features["timestamp"] = pd.to_datetime(features["timestamp"])
    features = features.sort_values("timestamp").reset_index(drop=True)
    labeling_config = config.get("labeling", {})
    horizons = [int(item) for item in labeling_config.get("horizons_minutes", [1, 3, 5, 15])]
    horizon = int(labeling_config.get("primary_horizon_minutes", 15))
    if horizon not in horizons:
        horizons.append(horizon)
    target_price_column = str(labeling_config.get("target_price_column", "mid_price_proxy"))
    if target_price_column not in features:
        target_price_column = "close"
    labels = features[["timestamp", "symbol", target_price_column]].copy()
    labels = labels.rename(columns={target_price_column: "target_mid_price"})
    if "trade_date" in features:
        labels["trade_date"] = features["trade_date"].astype(str)
    else:
        labels["trade_date"] = labels["timestamp"].dt.date.astype(str)
    labels["session"] = features["session"].astype(str) if "session" in features else ""
    group_columns = ["symbol", "trade_date", "session"]
    grouped = labels.groupby(group_columns, sort=False)["target_mid_price"]
    for item in sorted(set(horizons)):
        future_price = grouped.shift(-item)
        labels[f"target_return_{item}m"] = future_price / labels["target_mid_price"] - 1.0
        labels[f"target_return_{item}m_bps"] = labels[f"target_return_{item}m"] * 10000.0
    labels["future_return_pct"] = labels[f"target_return_{horizon}m"] * 100.0
    labels["action_name"] = labels["future_return_pct"].apply(lambda value: action_from_future_return(value, config))
    labels["action_name"] = [
        apply_cost_awareness(action, future_return, config)
        if np.isfinite(future_return)
        else "flat"
        for action, future_return in zip(labels["action_name"], labels["future_return_pct"])
    ]
    cost_bps = [_estimated_action_cost_bps(action, config) for action in labels["action_name"]]
    gross_bps = pd.to_numeric(labels[f"target_return_{horizon}m_bps"], errors="coerce")
    labels["estimated_cost_bps"] = cost_bps
    labels["net_target_return_bps"] = np.sign(gross_bps) * (gross_bps.abs() - labels["estimated_cost_bps"]).clip(lower=0.0)
    min_edge_bps = float(config.get("cost_aware_labeling", {}).get("min_edge_bps", 5.0))
    labels.loc[labels["net_target_return_bps"].abs() < min_edge_bps, "action_name"] = "flat"
    labels["action"] = labels["action_name"].map(ACTION_NAME_TO_ID).astype(int)
    path = lake.write_frame(labels, "labels", "labels")
    return labels, str(path)


def _estimated_action_cost_bps(action: str, config: dict[str, Any]) -> float:
    if action == "flat":
        return 0.0
    costs = config.get("cost_aware_labeling", {}).get("estimated_round_trip_cost_pct", {})
    try:
        return abs(float(costs.get(action, 0.0))) * 100.0
    except (TypeError, ValueError):
        return 0.0
