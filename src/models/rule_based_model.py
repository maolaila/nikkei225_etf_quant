from __future__ import annotations


import numpy as np
import pandas as pd

from src.config.schema import ACTION_NAME_TO_ID
from src.models.base import BaseModel


class RuleBasedModel(BaseModel):
    """A transparent momentum model using only current and past feature columns."""

    def _classify(self, return_5m_pct: float) -> tuple[str, float, str]:
        signal_cfg = self.config.get("strategy", {}).get("signal", {})
        weak = float(signal_cfg.get("weak_return_5m_pct", 0.035))
        strong = float(signal_cfg.get("strong_return_5m_pct", 0.085))
        if not np.isfinite(return_5m_pct):
            return "flat", 0.50, "insufficient trailing bars"
        abs_move = abs(return_5m_pct)
        confidence = min(0.99, 0.55 + abs_move / max(strong * 4.0, 0.001))
        if return_5m_pct >= strong:
            return "long_2x", confidence, f"5m momentum {return_5m_pct:.3f}% >= strong threshold {strong:.3f}%"
        if return_5m_pct >= weak:
            return "long_1x", confidence, f"5m momentum {return_5m_pct:.3f}% >= weak threshold {weak:.3f}%"
        if return_5m_pct <= -strong:
            return "short_2x", confidence, f"5m momentum {return_5m_pct:.3f}% <= strong short threshold {-strong:.3f}%"
        if return_5m_pct <= -weak:
            return "short_1x", confidence, f"5m momentum {return_5m_pct:.3f}% <= weak short threshold {-weak:.3f}%"
        return "flat", 0.75, f"5m momentum {return_5m_pct:.3f}% inside neutral band"

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        frame = features.copy()
        frame["timestamp"] = pd.to_datetime(frame["timestamp"])
        return_pct = frame.get("return_5m", pd.Series(index=frame.index, dtype=float)).astype(float) * 100.0
        market_regimes = frame["market_regime"].astype(str).tolist() if "market_regime" in frame else ["unknown"] * len(frame)
        symbols = frame["symbol"].astype(str).tolist() if "symbol" in frame else [""] * len(frame)
        rows = []
        for index, (timestamp, value) in enumerate(zip(frame["timestamp"], return_pct)):
            action_name, confidence, reason = self._classify(float(value))
            expected_return_bps = _trade_expected_return_bps(action_name, float(value) * 100.0)
            expected_cost_bps = _expected_cost_bps(action_name, self.config)
            net_edge_bps = expected_return_bps - expected_cost_bps
            recommended_position_size = _recommended_position_size(action_name, confidence, self.config)
            probs = {name: 0.025 for name in ACTION_NAME_TO_ID}
            probs[action_name] = max(probs[action_name], confidence)
            total = sum(probs.values())
            probs = {key: value / total for key, value in probs.items()}
            row = {
                    "timestamp": timestamp,
                    "predicted_action": ACTION_NAME_TO_ID[action_name],
                    "action_name": action_name,
                    "prob_flat": probs["flat"],
                    "prob_long_1x": probs["long_1x"],
                    "prob_long_2x": probs["long_2x"],
                    "prob_short_1x": probs["short_1x"],
                    "prob_short_2x": probs["short_2x"],
                    "confidence": confidence,
                    "expected_return_bps": expected_return_bps,
                    "expected_cost_bps": expected_cost_bps,
                    "net_edge_bps": net_edge_bps,
                    "recommended_position_size": recommended_position_size,
                    "reason_codes": _reason_codes(action_name),
                    "reason": reason,
                    "market_regime": market_regimes[index],
                    "reference_symbol": symbols[index],
                }
            row.update(_audit_feature_fields(frame, index))
            rows.append(row)
        return pd.DataFrame(rows)


def _trade_expected_return_bps(action_name: str, reference_return_bps: float) -> float:
    if action_name.startswith("short"):
        return max(0.0, -reference_return_bps)
    if action_name.startswith("long"):
        return max(0.0, reference_return_bps)
    return 0.0


def _expected_cost_bps(action_name: str, config: dict) -> float:
    if action_name == "flat":
        return 0.0
    costs = config.get("cost_aware_labeling", {}).get("estimated_round_trip_cost_pct", {})
    try:
        return abs(float(costs.get(action_name, 0.0))) * 100.0
    except (TypeError, ValueError):
        return 0.0


def _recommended_position_size(action_name: str, confidence: float, config: dict) -> float:
    if action_name == "flat":
        return 0.0
    limit = config.get("backtest", {}).get("position_limits", {}).get(action_name, {})
    base = float(limit.get("max_equity_pct", 0.0))
    return max(0.0, min(base, base * max(0.0, min(confidence, 1.0))))


def _reason_codes(action_name: str) -> str:
    if action_name == "flat":
        return "flat_or_no_edge"
    return "trend_signal;spread_not_observed;liquidity_not_observed;etf_consistency_checked_if_available"


def _audit_feature_fields(frame: pd.DataFrame, index: int) -> dict[str, float]:
    columns = [
        "implied_nikkei_1321_bps",
        "implied_nikkei_1570_bps",
        "implied_nikkei_1571_bps",
        "implied_nikkei_1357_bps",
        "implied_nikkei_dispersion_bps",
        "implied_nikkei_max_gap_bps",
        "etf_implied_source_count",
        "historical_bid_ask_unavailable",
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
    values: dict[str, float] = {}
    for column in columns:
        if column in frame:
            values[column] = frame[column].iloc[index]
    return values
