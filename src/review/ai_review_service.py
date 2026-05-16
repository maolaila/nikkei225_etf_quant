from __future__ import annotations

from pathlib import Path

from src.utils.paths import ensure_dir


AI_REVIEW_PROMPT_TEMPLATE = """You are a quantitative trading review assistant.

All trades are simulated. Do not claim guaranteed profit. Suggestions must be candidates only and require human review.

Daily data:
{{daily_data_json}}
"""


def write_ai_review_stub() -> Path:
    path = ensure_dir("data/reports/daily") / "ai_review.md"
    path.write_text("# AI Review\n\nAI review disabled by default. Candidate-only workflow preserved.\n", encoding="utf-8")
    return path

