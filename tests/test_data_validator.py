from __future__ import annotations

from src.config.loader import load_project_config


def test_safety_defaults_are_disabled():
    config = load_project_config()
    assert config["live_trading"]["enabled"] is False
    assert config["kabustation"]["live_order_enabled"] is False

