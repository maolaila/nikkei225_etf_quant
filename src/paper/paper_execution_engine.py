from __future__ import annotations

from typing import Any

from src.backtest.cost_model import CostModel


class PaperExecutionEngine:
    def __init__(self, config: dict[str, Any]) -> None:
        paper = config.get("paper_account", {}).get("execution_model", {})
        self.cost_model = CostModel(
            slippage_bps=float(paper.get("buy_slippage_bps", 3)),
            fallback_spread_bps=float(paper.get("fallback_spread_bps", 8)),
        )

    def simulated_buy_price(self, close: float) -> float:
        return self.cost_model.buy_price(close)

    def simulated_sell_price(self, close: float) -> float:
        return self.cost_model.sell_price(close)

