from __future__ import annotations

from typing import Any

from src.models.model_registry import create_model


def train_model(config: dict[str, Any], model_name: str | None = None):
    return create_model(config, model_name)

