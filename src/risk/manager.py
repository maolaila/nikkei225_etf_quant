from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from src.market.quote import Quote, evaluate_quote
from src.market.session import session_block_reason


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str
    position_multiplier: float = 1.0


class RiskManager:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def approve_signal(
        self,
        signal: dict[str, Any],
        market_state: dict[str, Any],
        portfolio_state: dict[str, Any],
    ) -> RiskDecision:
        timestamp = pd.Timestamp(signal.get("timestamp", market_state.get("timestamp", pd.Timestamp.utcnow())))
        session_reason = session_block_reason(timestamp, self.config)
        if session_reason:
            return RiskDecision(False, session_reason)

        quote = market_state.get("quote")
        if isinstance(quote, Quote):
            quote_check = evaluate_quote(quote, self.config)
            if not quote_check.approved:
                return RiskDecision(False, quote_check.reason)

        risk = self.config.get("backtest", {}).get("risk", {}) | self.config.get("risk", {})
        max_dispersion = float(risk.get("max_implied_dispersion_bps", risk.get("max_dispersion_bps", 0.0)))
        dispersion = _float_or_none(signal.get("implied_nikkei_dispersion_bps", market_state.get("implied_nikkei_dispersion_bps")))
        if max_dispersion > 0 and dispersion is not None and dispersion > max_dispersion:
            return RiskDecision(False, "etf_dispersion_filter")

        max_position = float(risk.get("max_position_size", risk.get("max_position_size_pct", 100.0)))
        position_size = _float_or_none(portfolio_state.get("position_size", portfolio_state.get("position_size_pct")))
        if position_size is not None and position_size > max_position:
            return RiskDecision(False, "max_position_filter")

        daily_loss = _float_or_none(portfolio_state.get("daily_loss_pct"))
        max_daily_loss = float(risk.get("max_daily_loss_pct", 0.0))
        if max_daily_loss > 0 and daily_loss is not None and daily_loss <= -abs(max_daily_loss):
            return RiskDecision(False, "max_daily_loss_filter")

        consecutive_losses = int(portfolio_state.get("consecutive_losses", 0) or 0)
        max_consecutive_losses = int(risk.get("max_consecutive_losses", 0) or 0)
        if max_consecutive_losses > 0 and consecutive_losses >= max_consecutive_losses:
            return RiskDecision(False, "max_consecutive_loss_filter")

        if _float_or_none(signal.get("net_edge_bps")) is not None and float(signal.get("net_edge_bps", 0.0)) <= 0.0:
            return RiskDecision(False, "non_positive_net_edge")

        return RiskDecision(True, "approved")


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if pd.notna(number) else None

