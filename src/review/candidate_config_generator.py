from __future__ import annotations

from pathlib import Path

from src.utils.paths import ensure_dir


def write_candidate_config_stub() -> Path:
    path = ensure_dir("data/reports/daily") / "candidate_config.yaml"
    path.write_text("# Candidate config only. Human approval required before applying.\n", encoding="utf-8")
    return path

