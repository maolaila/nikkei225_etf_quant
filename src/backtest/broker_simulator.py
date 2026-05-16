from __future__ import annotations

from src.backtest.cost_model import CostModel


class BrokerSimulator:
    def __init__(self, cost_model: CostModel) -> None:
        self.cost_model = cost_model

    def buy_fill_price(self, open_price: float, notional: float = 0.0, bar_turnover: float | None = None) -> float:
        return self.cost_model.buy_price(open_price, notional, bar_turnover)

    def sell_fill_price(self, open_price: float, notional: float = 0.0, bar_turnover: float | None = None) -> float:
        return self.cost_model.sell_price(open_price, notional, bar_turnover)
