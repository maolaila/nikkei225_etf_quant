from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def max_drawdown_pct(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0
    return float(drawdown.min() * 100.0)


def summarize_backtest(
    equity_curve: pd.DataFrame,
    trade_log: pd.DataFrame,
    initial_cash: float,
) -> dict[str, Any]:
    final_equity = float(equity_curve["equity"].iloc[-1]) if not equity_curve.empty else initial_cash
    sells = trade_log[trade_log["action"] == "SELL"].copy() if not trade_log.empty else pd.DataFrame()
    pnl = sells["pnl_jpy"].astype(float) if not sells.empty else pd.Series(dtype=float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    gross_profit = float(wins.sum()) if not wins.empty else 0.0
    gross_loss = float(losses.sum()) if not losses.empty else 0.0
    total_trades = int(len(sells))
    day_pnl = sells.groupby("trade_date")["pnl_jpy"].sum() if not sells.empty else pd.Series(dtype=float)
    action_returns = sells.groupby("position_action")["pnl_jpy"].sum().to_dict() if not sells.empty else {}
    etf_returns = sells.groupby("symbol")["pnl_jpy"].sum().to_dict() if not sells.empty else {}
    yearly_returns = sells.groupby("year")["pnl_jpy"].sum().to_dict() if not sells.empty else {}
    monthly_returns = sells.groupby("month")["pnl_jpy"].sum().to_dict() if not sells.empty else {}
    monthly_return_rows = calculate_monthly_returns(equity_curve, initial_cash)
    monthly_return_pct = pd.Series([row["return_pct"] for row in monthly_return_rows], dtype=float)
    positive_months = int((monthly_return_pct > 0).sum()) if not monthly_return_pct.empty else 0
    negative_months = int((monthly_return_pct < 0).sum()) if not monthly_return_pct.empty else 0
    flat_months = int((monthly_return_pct == 0).sum()) if not monthly_return_pct.empty else 0
    total_months = int(len(monthly_return_pct))
    active_months = positive_months + negative_months
    session_returns = sells.groupby("session")["pnl_jpy"].sum().to_dict() if not sells.empty else {}
    regime_returns = sells.groupby("market_regime")["pnl_jpy"].sum().to_dict() if not sells.empty else {}
    total_commission = float(trade_log["commission_jpy"].sum()) if "commission_jpy" in trade_log.columns and not trade_log.empty else 0.0
    average_execution_cost_bps = float(trade_log["execution_cost_bps"].mean()) if "execution_cost_bps" in trade_log.columns and not trade_log.empty else 0.0
    trade_return_pct = sells["pnl_pct"].astype(float) if "pnl_pct" in sells and not sells.empty else pd.Series(dtype=float)
    holding_minutes = _holding_minutes(trade_log)
    turnover_jpy = float(trade_log["notional_jpy"].sum()) if "notional_jpy" in trade_log.columns and not trade_log.empty else 0.0
    equity_returns = equity_curve["equity"].pct_change().replace([np.inf, -np.inf], np.nan).dropna() if not equity_curve.empty else pd.Series(dtype=float)
    downside_returns = equity_returns[equity_returns < 0]
    exposure_time_pct = _exposure_time_pct(equity_curve)
    avg_bar_turnover = float(trade_log["bar_turnover_jpy"].replace(0, np.nan).mean()) if "bar_turnover_jpy" in trade_log and not trade_log.empty else 0.0
    return {
        "initial_cash_jpy": initial_cash,
        "final_equity_jpy": final_equity,
        "total_return_pct": (final_equity / initial_cash - 1.0) * 100.0,
        "annualized_return_pct": np.nan,
        "max_drawdown_pct": max_drawdown_pct(equity_curve["equity"]) if not equity_curve.empty else 0.0,
        "sharpe": _annualized_ratio(equity_returns),
        "sortino": _annualized_ratio(equity_returns, downside_returns),
        "win_rate_pct": float((len(wins) / total_trades) * 100.0) if total_trades else 0.0,
        "profit_factor": float(gross_profit / abs(gross_loss)) if gross_loss < 0 else float("inf") if gross_profit > 0 else 0.0,
        "average_trade_pnl_jpy": float(pnl.mean()) if total_trades else 0.0,
        "average_trade_return_pct": float(trade_return_pct.mean()) if total_trades else 0.0,
        "median_trade_return_pct": float(trade_return_pct.median()) if total_trades else 0.0,
        "average_winning_trade_jpy": float(wins.mean()) if not wins.empty else 0.0,
        "average_losing_trade_jpy": float(losses.mean()) if not losses.empty else 0.0,
        "max_single_loss_jpy": float(losses.min()) if not losses.empty else 0.0,
        "largest_loss_jpy": float(losses.min()) if not losses.empty else 0.0,
        "largest_win_jpy": float(wins.max()) if not wins.empty else 0.0,
        "max_consecutive_losses": _max_consecutive_losses(pnl),
        "total_trades": total_trades,
        "number_of_trades": total_trades,
        "daily_average_trades": float(sells.groupby("trade_date").size().mean()) if total_trades else 0.0,
        "turnover_jpy": turnover_jpy,
        "turnover": turnover_jpy,
        "average_holding_time_minutes": float(holding_minutes.mean()) if not holding_minutes.empty else 0.0,
        "exposure_time_pct": exposure_time_pct,
        "capacity_estimate_jpy": avg_bar_turnover * 0.01 if avg_bar_turnover > 0 else 0.0,
        "profitable_days": int((day_pnl > 0).sum()) if not day_pnl.empty else 0,
        "losing_days": int((day_pnl < 0).sum()) if not day_pnl.empty else 0,
        "flat_days": int((day_pnl == 0).sum()) if not day_pnl.empty else 0,
        "returns_by_action_jpy": {str(k): float(v) for k, v in action_returns.items()},
        "returns_by_etf_jpy": {str(k): float(v) for k, v in etf_returns.items()},
        "returns_by_year_jpy": {str(k): float(v) for k, v in yearly_returns.items()},
        "returns_by_month_jpy": {str(k): float(v) for k, v in monthly_returns.items()},
        "monthly_returns": monthly_return_rows,
        "returns_by_month_pct": {row["year_month"]: float(row["return_pct"]) for row in monthly_return_rows},
        "average_monthly_return_pct": float(monthly_return_pct.mean()) if not monthly_return_pct.empty else 0.0,
        "median_monthly_return_pct": float(monthly_return_pct.median()) if not monthly_return_pct.empty else 0.0,
        "min_monthly_return_pct": float(monthly_return_pct.min()) if not monthly_return_pct.empty else 0.0,
        "max_monthly_return_pct": float(monthly_return_pct.max()) if not monthly_return_pct.empty else 0.0,
        "positive_months": positive_months,
        "negative_months": negative_months,
        "flat_months": flat_months,
        "total_months": total_months,
        "active_months": active_months,
        "positive_month_ratio": float(positive_months / total_months) if total_months else 0.0,
        "positive_active_month_ratio": float(positive_months / active_months) if active_months else 0.0,
        "total_commission_jpy": total_commission,
        "average_execution_cost_bps": average_execution_cost_bps,
        "returns_by_session_jpy": {str(k): float(v) for k, v in session_returns.items()},
        "returns_by_market_regime_jpy": {str(k): float(v) for k, v in regime_returns.items()},
    }


def calculate_monthly_returns(equity_curve: pd.DataFrame, initial_cash: float) -> list[dict[str, Any]]:
    if equity_curve.empty:
        return []

    frame = equity_curve.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    frame = frame.sort_values("timestamp").reset_index(drop=True)
    frame["year_month"] = frame["timestamp"].dt.strftime("%Y-%m")

    rows: list[dict[str, Any]] = []
    previous_end_equity = float(initial_cash)
    for year_month, group in frame.groupby("year_month", sort=True):
        start_equity = previous_end_equity
        end_equity = float(group["equity"].iloc[-1])
        pnl_jpy = end_equity - start_equity
        return_pct = (end_equity / start_equity - 1.0) * 100.0 if start_equity else 0.0
        year, month = year_month.split("-", 1)
        rows.append(
            {
                "year_month": year_month,
                "year": int(year),
                "month": int(month),
                "start_equity_jpy": start_equity,
                "end_equity_jpy": end_equity,
                "pnl_jpy": pnl_jpy,
                "return_pct": return_pct,
            }
        )
        previous_end_equity = end_equity

    return rows


def _max_consecutive_losses(pnl: pd.Series) -> int:
    max_streak = 0
    current = 0
    for value in pnl:
        if value < 0:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return max_streak


def _annualized_ratio(returns: pd.Series, denominator_returns: pd.Series | None = None) -> float:
    if returns.empty:
        return 0.0
    denominator = denominator_returns if denominator_returns is not None else returns
    std = float(denominator.std())
    if not np.isfinite(std) or std == 0.0:
        return 0.0
    return float(returns.mean() / std * np.sqrt(252 * 300))


def _holding_minutes(trade_log: pd.DataFrame) -> pd.Series:
    if trade_log.empty or "timestamp" not in trade_log or "action" not in trade_log:
        return pd.Series(dtype=float)
    rows = trade_log.copy()
    rows["timestamp"] = pd.to_datetime(rows["timestamp"])
    entry_by_symbol: dict[str, pd.Timestamp] = {}
    durations: list[float] = []
    for row in rows.sort_values("timestamp").itertuples(index=False):
        symbol = str(getattr(row, "symbol", ""))
        action = str(getattr(row, "action", ""))
        timestamp = pd.Timestamp(getattr(row, "timestamp"))
        if action == "BUY":
            entry_by_symbol[symbol] = timestamp
        elif action == "SELL" and symbol in entry_by_symbol:
            durations.append(max(0.0, (timestamp - entry_by_symbol.pop(symbol)).total_seconds() / 60.0))
    return pd.Series(durations, dtype=float)


def _exposure_time_pct(equity_curve: pd.DataFrame) -> float:
    if equity_curve.empty or "position_qty" not in equity_curve:
        return 0.0
    active = pd.to_numeric(equity_curve["position_qty"], errors="coerce").fillna(0).ne(0)
    return float(active.mean() * 100.0) if len(active) else 0.0
