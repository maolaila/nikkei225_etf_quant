from __future__ import annotations

from typing import Any

from src.models.lightgbm_model import LightGBMModel
from src.models.rule_based_model import RuleBasedModel
from src.models.sklearn_model import LogisticRegressionModel, RandomForestModel
from src.models.xgboost_model import XGBoostModel


MODEL_TYPES = {
    "rule_based": RuleBasedModel,
    "logistic_regression": LogisticRegressionModel,
    "random_forest": RandomForestModel,
    "lightgbm": LightGBMModel,
    "xgboost": XGBoostModel,
}


def create_model(config: dict[str, Any], model_name: str | None = None):
    selected = model_name or config.get("model", {}).get("type", "rule_based")
    if selected == "latest":
        selected = "rule_based"
    return MODEL_TYPES.get(selected, RuleBasedModel)(config)

