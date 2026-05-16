from __future__ import annotations

from pathlib import Path

from src.utils.paths import ensure_dir


def write_daily_review_stub() -> Path:
    path = ensure_dir("data/reports/daily") / "daily_review.md"
    path.write_text("# Daily Review\n\nPaper-trading review stub for pass 1.\n", encoding="utf-8")
    return path

