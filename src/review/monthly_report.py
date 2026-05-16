from __future__ import annotations

from pathlib import Path

from src.utils.paths import ensure_dir


def write_monthly_report_stub() -> Path:
    path = ensure_dir("data/reports/monthly") / "monthly_report.md"
    path.write_text("# Monthly Report\n\nHistorical summary stub for pass 1.\n", encoding="utf-8")
    return path

