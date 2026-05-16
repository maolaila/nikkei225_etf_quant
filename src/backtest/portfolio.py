from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class Position:
    symbol: str
    action_name: str
    quantity: int
    entry_price: float
    entry_timestamp: pd.Timestamp
    entry_reason: str
    entry_commission_jpy: float = 0.0
    confidence: float = 0.0
    action_probability: float = 0.0
    sizing_multiplier: float = 1.0
    base_equity_pct: float = 0.0
    target_equity_pct: float = 0.0
    absolute_max_equity_pct: float = 0.0
    max_holding_minutes: int = 60
    stop_loss_pct: float = 1.0
    market_regime: str = "unknown"

    def market_value(self, price: float) -> float:
        return self.quantity * price

    def unrealized_pnl(self, price: float) -> float:
        return self.quantity * (price - self.entry_price)

    def unrealized_pct(self, price: float) -> float:
        return (price / self.entry_price - 1.0) * 100.0
