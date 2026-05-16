from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CostModel:
    slippage_bps: float = 3.0
    fallback_spread_bps: float = 8.0
    market_impact_enabled: bool = True
    market_impact_bps_per_turnover_pct: float = 2.0
    max_market_impact_bps: float = 20.0
    commission_enabled: bool = True
    commission_rate_pct: float = 0.0
    fixed_commission_jpy: float = 0.0
    min_commission_jpy: float = 0.0
    max_commission_jpy: float = 0.0
    system_fee_jpy: float = 0.0

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "CostModel":
        execution = config.get("backtest", {}).get("execution", {})
        cost = config.get("backtest", {}).get("cost", {})
        impact = execution.get("market_impact", {})
        return cls(
            slippage_bps=float(execution.get("slippage_bps", 3.0)),
            fallback_spread_bps=float(execution.get("fallback_spread_bps", 8.0)),
            market_impact_enabled=bool(impact.get("enabled", True)),
            market_impact_bps_per_turnover_pct=float(impact.get("bps_per_1pct_bar_turnover", 2.0)),
            max_market_impact_bps=float(impact.get("max_bps", 20.0)),
            commission_enabled=bool(cost.get("commission_enabled", True)),
            commission_rate_pct=float(cost.get("commission_rate_pct", 0.0)),
            fixed_commission_jpy=float(cost.get("fixed_commission_jpy", 0.0)),
            min_commission_jpy=float(cost.get("min_commission_jpy", 0.0)),
            max_commission_jpy=float(cost.get("max_commission_jpy", 0.0)),
            system_fee_jpy=float(cost.get("system_fee_jpy", 0.0)),
        )

    def market_impact_bps(self, notional: float = 0.0, bar_turnover: float | None = None) -> float:
        if not self.market_impact_enabled or notional <= 0 or bar_turnover is None or bar_turnover <= 0:
            return 0.0
        participation_pct = notional / bar_turnover * 100.0
        impact = participation_pct * self.market_impact_bps_per_turnover_pct
        return max(0.0, min(self.max_market_impact_bps, impact))

    def total_execution_bps(self, notional: float = 0.0, bar_turnover: float | None = None) -> float:
        return self.slippage_bps + (self.fallback_spread_bps / 2.0) + self.market_impact_bps(notional, bar_turnover)

    def buy_price(self, reference_price: float, notional: float = 0.0, bar_turnover: float | None = None) -> float:
        adjustment = self.total_execution_bps(notional, bar_turnover) / 10000.0
        return reference_price * (1.0 + adjustment)

    def sell_price(self, reference_price: float, notional: float = 0.0, bar_turnover: float | None = None) -> float:
        adjustment = self.total_execution_bps(notional, bar_turnover) / 10000.0
        return reference_price * (1.0 - adjustment)

    def commission(self, notional: float) -> float:
        if not self.commission_enabled:
            return 0.0
        commission = notional * self.commission_rate_pct / 100.0 + self.fixed_commission_jpy + self.system_fee_jpy
        if self.min_commission_jpy > 0:
            commission = max(commission, self.min_commission_jpy)
        if self.max_commission_jpy > 0:
            commission = min(commission, self.max_commission_jpy)
        return commission

    def execution_breakdown(
        self,
        reference_price: float,
        side: str,
        notional: float = 0.0,
        bar_turnover: float | None = None,
    ) -> dict[str, float]:
        impact_bps = self.market_impact_bps(notional, bar_turnover)
        total_bps = self.total_execution_bps(notional, bar_turnover)
        fill_price = self.buy_price(reference_price, notional, bar_turnover) if side == "BUY" else self.sell_price(reference_price, notional, bar_turnover)
        return {
            "reference_price": reference_price,
            "fill_price": fill_price,
            "slippage_bps": self.slippage_bps,
            "spread_bps": self.fallback_spread_bps,
            "half_spread_bps": self.fallback_spread_bps / 2.0,
            "market_impact_bps": impact_bps,
            "execution_cost_bps": total_bps,
            "bar_turnover_jpy": float(bar_turnover or 0.0),
        }
