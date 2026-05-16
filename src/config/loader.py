from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


CONFIG_FILES = (
    "symbols.yaml",
    "data_sources.yaml",
    "historical.yaml",
    "strategy.yaml",
    "labeling.yaml",
    "model.yaml",
    "backtest.yaml",
    "paper_account.yaml",
    "daily_review.yaml",
    "ai_review.yaml",
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_yaml(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.is_absolute():
        file_path = project_root() / file_path
    if not file_path.exists():
        raise FileNotFoundError(f"Config file not found: {file_path}")
    with file_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in YAML file: {file_path}")
    return data


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def load_project_config(config_dir: str | Path = "config", override_paths: list[str | Path] | None = None) -> dict[str, Any]:
    root = project_root()
    cfg_dir = Path(config_dir)
    if not cfg_dir.is_absolute():
        cfg_dir = root / cfg_dir
    config: dict[str, Any] = {}
    for name in CONFIG_FILES:
        path = cfg_dir / name
        if path.exists():
            config = deep_merge(config, load_yaml(path))
    for override_path in override_paths or []:
        config = deep_merge(config, load_yaml(override_path))
    validate_safety(config)
    return config


def get_nested(config: dict[str, Any], dotted_key: str, default: Any = None) -> Any:
    cursor: Any = config
    for part in dotted_key.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return default
        cursor = cursor[part]
    return cursor


def validate_safety(config: dict[str, Any]) -> None:
    if get_nested(config, "live_trading.enabled", False) is not False:
        raise ValueError("Safety violation: live_trading.enabled must be false")
    if get_nested(config, "kabustation.live_order_enabled", False) is not False:
        raise ValueError("Safety violation: kabustation.live_order_enabled must be false")
    if get_nested(config, "tachibana.live_order_enabled", False) is not False:
        raise ValueError("Safety violation: tachibana.live_order_enabled must be false")
    if get_nested(config, "ibkr.live_order_enabled", False) is not False:
        raise ValueError("Safety violation: ibkr.live_order_enabled must be false")
