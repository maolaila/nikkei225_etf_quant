from __future__ import annotations

from pathlib import Path
from typing import Any

from src.paper.account import PaperAccount
from src.utils.paths import ensure_dir
from src.utils.serialization import write_json


def run_paper_trade(config: dict[str, Any], provider: str) -> Path:
    account = PaperAccount.from_config(config)
    out_dir = ensure_dir("data/reports/paper")
    state = account.state()
    state.update(
        {
            "provider": provider,
            "live_order_enabled": False,
            "message": "Paper-trading stub only; no broker order API is called.",
        }
    )
    path = out_dir / "account_state.json"
    write_json(path, state)
    return path

