from __future__ import annotations

from typing import Any

import pandas as pd

from src.models.model_registry import create_model


def predict(config: dict[str, Any], features: pd.DataFrame, model_name: str | None = None) -> pd.DataFrame:
    return create_model(config, model_name).predict(features)

