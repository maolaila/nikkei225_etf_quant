from __future__ import annotations

from src.backtest.cost_model import CostModel


def test_cost_model_applies_spread_and_slippage():
    cost = CostModel(slippage_bps=3, fallback_spread_bps=8)
    assert cost.buy_price(100) > 100
    assert cost.sell_price(100) < 100
    assert round(cost.buy_price(100) - cost.sell_price(100), 4) == 0.14


def test_cost_model_applies_market_impact_from_turnover():
    cost = CostModel(slippage_bps=0, fallback_spread_bps=0, market_impact_bps_per_turnover_pct=2, max_market_impact_bps=20)
    buy_price = cost.buy_price(100, notional=100_000, bar_turnover=1_000_000)
    assert round(buy_price, 4) == 100.2
    assert cost.market_impact_bps(100_000, 1_000_000) == 20


def test_cost_model_commission_can_model_min_and_max_order_fee():
    cost = CostModel(commission_rate_pct=0.1, fixed_commission_jpy=0, min_commission_jpy=50, max_commission_jpy=120)
    assert cost.commission(10_000) == 50
    assert cost.commission(200_000) == 120
