from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from src.config.schema import ACTION_ID_TO_NAME, ACTION_NAME_TO_ID
from src.models.base import BaseModel
from src.models.rule_based_model import RuleBasedModel


EXCLUDED_FEATURE_COLUMNS = {
    "timestamp",
    "symbol",
    "interval",
    "date",
    "trade_date",
    "time",
    "session",
    "provider",
    "market_regime",
    "fetched_at",
    "future_return_pct",
    "future_close",
    "action",
    "action_name",
}


class SklearnClassifierModel(BaseModel):
    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.feature_columns: list[str] = []
        self.estimator: Any | None = None
        self.fallback = RuleBasedModel(config)
        self.fitted = False

    def _make_estimator(self) -> Any:
        raise NotImplementedError

    def fit(self, features: pd.DataFrame, labels: pd.DataFrame | None = None) -> "SklearnClassifierModel":
        if labels is None or features.empty or labels.empty:
            return self
        merged = features.copy()
        merged["timestamp"] = pd.to_datetime(merged["timestamp"])
        y_frame = labels[["timestamp", "action"]].copy()
        y_frame["timestamp"] = pd.to_datetime(y_frame["timestamp"])
        merged = merged.merge(y_frame, on="timestamp", how="inner")
        if merged.empty or merged["action"].nunique() < 2:
            return self
        self.feature_columns = _feature_columns(merged)
        if not self.feature_columns:
            return self
        x = _clean_features(merged, self.feature_columns)
        y = merged["action"].astype(int)
        self.estimator = self._make_estimator()
        self.estimator.fit(x, y)
        self.fitted = True
        return self

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        if not self.fitted or self.estimator is None or not self.feature_columns:
            return self.fallback.predict(features)
        frame = features.copy()
        frame["timestamp"] = pd.to_datetime(frame["timestamp"])
        x = _clean_features(frame, self.feature_columns)
        predicted = self.estimator.predict(x).astype(int)
        if hasattr(self.estimator, "predict_proba"):
            probabilities = self.estimator.predict_proba(x)
            classes = [int(item) for item in self.estimator.classes_]
        else:
            probabilities = np.zeros((len(frame), len(ACTION_ID_TO_NAME)))
            classes = list(ACTION_ID_TO_NAME)
        rows: list[dict[str, Any]] = []
        market_regimes = frame["market_regime"].astype(str).tolist() if "market_regime" in frame else ["unknown"] * len(frame)
        symbols = frame["symbol"].astype(str).tolist() if "symbol" in frame else [""] * len(frame)
        trailing_return_bps = (
            pd.to_numeric(frame["return_5m"], errors="coerce").fillna(0.0) * 10000.0
            if "return_5m" in frame
            else pd.Series([0.0] * len(frame))
        )
        for index, timestamp in enumerate(frame["timestamp"]):
            action_id = int(predicted[index])
            probs_by_name = {name: 0.0 for name in ACTION_NAME_TO_ID}
            for class_index, class_id in enumerate(classes):
                name = ACTION_ID_TO_NAME.get(int(class_id), "flat")
                if class_index < probabilities.shape[1]:
                    probs_by_name[name] = float(probabilities[index, class_index])
            confidence = max(probs_by_name.values()) if probs_by_name else 0.0
            action_name = ACTION_ID_TO_NAME.get(action_id, "flat")
            expected_return_bps = _trade_expected_return_bps(action_name, float(trailing_return_bps.iloc[index]))
            expected_cost_bps = _expected_cost_bps(action_name, self.config)
            net_edge_bps = expected_return_bps - expected_cost_bps
            row = {
                    "timestamp": timestamp,
                    "predicted_action": action_id,
                    "action_name": action_name,
                    "prob_flat": probs_by_name["flat"],
                    "prob_long_1x": probs_by_name["long_1x"],
                    "prob_long_2x": probs_by_name["long_2x"],
                    "prob_short_1x": probs_by_name["short_1x"],
                    "prob_short_2x": probs_by_name["short_2x"],
                    "confidence": confidence,
                    "expected_return_bps": expected_return_bps,
                    "expected_cost_bps": expected_cost_bps,
                    "net_edge_bps": net_edge_bps,
                    "recommended_position_size": _recommended_position_size(action_name, confidence, self.config),
                    "reason_codes": "model_probability;spread_not_observed;liquidity_not_observed;etf_consistency_checked_if_available",
                    "reason": f"{self.__class__.__name__} predicted {action_name} from {len(self.feature_columns)} features",
                    "market_regime": market_regimes[index],
                    "reference_symbol": symbols[index],
                }
            row.update(_audit_feature_fields(frame, index))
            rows.append(row)
        return pd.DataFrame(rows)


class LogisticRegressionModel(SklearnClassifierModel):
    def _make_estimator(self) -> Any:
        return Pipeline(
            [
                ("scale", StandardScaler()),
                (
                    "model",
                    LogisticRegression(
                        max_iter=1000,
                        class_weight=self.config.get("model", {}).get("training", {}).get("class_weight", "balanced"),
                        random_state=int(self.config.get("model", {}).get("random_state", 42)),
                    ),
                ),
            ]
        )


class RandomForestModel(SklearnClassifierModel):
    def _make_estimator(self) -> Any:
        training = self.config.get("model", {}).get("training", {})
        return RandomForestClassifier(
            n_estimators=int(training.get("n_estimators", 200)),
            max_depth=training.get("max_depth", 8),
            min_samples_leaf=int(training.get("min_samples_leaf", 20)),
            class_weight=training.get("class_weight", "balanced"),
            random_state=int(self.config.get("model", {}).get("random_state", 42)),
            n_jobs=-1,
        )


def _feature_columns(frame: pd.DataFrame) -> list[str]:
    numeric_columns = frame.select_dtypes(include=[np.number]).columns
    return [column for column in numeric_columns if column not in EXCLUDED_FEATURE_COLUMNS]


def _clean_features(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    x = frame.reindex(columns=columns).replace([np.inf, -np.inf], np.nan)
    medians = x.median(numeric_only=True).fillna(0.0)
    return x.fillna(medians).fillna(0.0)


def _trade_expected_return_bps(action_name: str, reference_return_bps: float) -> float:
    if action_name.startswith("short"):
        return max(0.0, -reference_return_bps)
    if action_name.startswith("long"):
        return max(0.0, reference_return_bps)
    return 0.0


def _expected_cost_bps(action_name: str, config: dict[str, Any]) -> float:
    if action_name == "flat":
        return 0.0
    costs = config.get("cost_aware_labeling", {}).get("estimated_round_trip_cost_pct", {})
    try:
        return abs(float(costs.get(action_name, 0.0))) * 100.0
    except (TypeError, ValueError):
        return 0.0


def _recommended_position_size(action_name: str, confidence: float, config: dict[str, Any]) -> float:
    if action_name == "flat":
        return 0.0
    limit = config.get("backtest", {}).get("position_limits", {}).get(action_name, {})
    base = float(limit.get("max_equity_pct", 0.0))
    return max(0.0, min(base, base * max(0.0, min(confidence, 1.0))))


def _audit_feature_fields(frame: pd.DataFrame, index: int) -> dict[str, Any]:
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
    return {column: frame[column].iloc[index] for column in columns if column in frame}
