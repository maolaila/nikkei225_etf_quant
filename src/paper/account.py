from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PaperAccount:
    cash: float
    positions: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "PaperAccount":
        return cls(cash=float(config.get("paper_account", {}).get("initial_cash", 1_000_000)))

    def state(self) -> dict[str, Any]:
        return {"cash": self.cash, "positions": self.positions, "mode": "paper_trading"}

