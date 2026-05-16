from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd


class BaseModel(ABC):
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def fit(self, features: pd.DataFrame, labels: pd.DataFrame | None = None) -> "BaseModel":
        return self

    @abstractmethod
    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        ...


class StrategyModel(BaseModel):
    """Production-facing strategy model interface."""

