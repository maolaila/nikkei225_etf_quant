from __future__ import annotations

from src.config.loader import load_project_config


def test_load_project_config_applies_experiment_overlays(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "strategy.yaml").write_text(
        "strategy:\n  signal:\n    min_confidence: 0.55\nlive_trading:\n  enabled: false\n",
        encoding="utf-8",
    )
    overlay = tmp_path / "experiment.yaml"
    overlay.write_text(
        "strategy:\n  signal:\n    min_confidence: 0.30\nbacktest:\n  report:\n    output_dir: data/reports/experiments/test\n",
        encoding="utf-8",
    )

    config = load_project_config(config_dir, [overlay])

    assert config["strategy"]["signal"]["min_confidence"] == 0.30
    assert config["backtest"]["report"]["output_dir"] == "data/reports/experiments/test"
    assert config["live_trading"]["enabled"] is False
