from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

from src.backtest.broker_simulator import BrokerSimulator
from src.backtest.cost_model import CostModel
from src.backtest.execution_model import next_bar
from src.backtest.metrics import summarize_backtest
from src.backtest.portfolio import Position
from src.backtest.report import output_dir, write_backtest_report
from src.config.schema import ACTION_ID_TO_NAME
from src.data.data_lake import DataLake
from src.data.symbol_mapper import SymbolMapper
from src.market.session import session_block_reason
from src.market.session import session_name
from src.models.model_registry import create_model
from src.utils.serialization import write_json
from src.utils.serialization import read_json

ACTION_PROBABILITY_COLUMNS = {
    "flat": "prob_flat",
    "long_1x": "prob_long_1x",
    "long_2x": "prob_long_2x",
    "short_1x": "prob_short_1x",
    "short_2x": "prob_short_2x",
}


def _session(timestamp: pd.Timestamp) -> str:
    name = session_name(timestamp)
    if name == "morning_close":
        return "morning"
    if name == "afternoon_close":
        return "afternoon"
    return name


def _side(action_name: str) -> str:
    if action_name.startswith("long"):
        return "long"
    if action_name.startswith("short"):
        return "short"
    return "flat"


class BacktestEngine:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.mapper = SymbolMapper(config)
        self.cost_model = CostModel.from_config(config)
        self.broker = BrokerSimulator(self.cost_model)
        self.initial_cash = float(config.get("backtest", {}).get("initial_cash_jpy", 1_000_000))
        execution_config = config.get("backtest", {}).get("execution", {})
        self.delay_bars = int(execution_config.get("latency_bars", execution_config.get("signal_delay_bars", 1)))
        self.max_trades_per_day = int(config.get("backtest", {}).get("risk", {}).get("max_trades_per_day", 3))
        self.execution_config = execution_config
        self.risk_config = config.get("backtest", {}).get("risk", {})
        exit_config = config.get("strategy", {}).get("exit", {})
        self.max_holding_minutes = int(exit_config.get("max_holding_minutes", 60))
        self.force_exit_time = str(exit_config.get("force_exit_time", "15:10"))
        self.force_exit_times = _time_list(exit_config.get("force_exit_times"))
        self.exit_on_neutral_signal = bool(exit_config.get("exit_on_neutral_signal", True))
        self.exit_on_opposite_signal = bool(exit_config.get("exit_on_opposite_signal", True))
        self.exit_if_no_profit_enabled = bool(exit_config.get("exit_if_no_profit_enabled", False))
        self.exit_if_no_profit_after_minutes = int(exit_config.get("exit_if_no_profit_after_minutes", 0) or 0)
        self.dynamic_holding_config = exit_config.get("dynamic_holding", {})
        self.dynamic_stop_config = exit_config.get("dynamic_stop_loss", {})
        self.take_profit_config = exit_config.get("take_profit", {})
        prediction_config = config.get("model", {}).get("prediction", {})
        self.min_confidence = float(prediction_config.get("min_confidence", 0.55))
        self.min_action_probability = float(prediction_config.get("min_action_probability", self.min_confidence))
        self.position_sizing_config = config.get("backtest", {}).get("position_sizing", {})
        self.entry_filters = self._load_entry_filters()
        self.entry_date_filter = self._load_entry_date_filter()
        self.realized_loss_gate_enabled = bool(
            config.get("backtest", {}).get("risk", {}).get("realized_loss_gate", {}).get("enabled", False)
        )
        self.max_daily_loss_pct = float(config.get("backtest", {}).get("risk", {}).get("max_daily_loss_pct", 0.0))
        self.max_consecutive_losses = int(config.get("backtest", {}).get("risk", {}).get("max_consecutive_losses", 0))
        self.max_spread_bps = float(self.risk_config.get("max_spread_bps", 0.0))
        self.max_quote_age_seconds = float(self.risk_config.get("max_quote_age_seconds", 0.0))
        self.min_bid_depth = float(self.risk_config.get("min_bid_depth", 0.0))
        self.min_ask_depth = float(self.risk_config.get("min_ask_depth", 0.0))
        self.max_implied_dispersion_bps = float(self.risk_config.get("max_implied_dispersion_bps", 0.0))

    def run(self, model_name: str | None = None) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        lake = DataLake()
        bars = lake.read_frame("normalized", "minute_bars")
        bars["timestamp"] = pd.to_datetime(bars["timestamp"])
        bars["symbol"] = bars["symbol"].astype(str)
        data_providers = sorted(bars.get("provider", pd.Series(dtype=str)).astype(str).unique())
        data_is_synthetic = any("synthetic" in provider.lower() for provider in data_providers)
        signals = self._load_or_predict_signals(lake, model_name)
        signals["timestamp"] = pd.to_datetime(signals["timestamp"])
        signals = signals.sort_values("timestamp").reset_index(drop=True)
        signals = self._apply_prediction_gates(signals)
        signals = self._annotate_entry_filters(signals)

        bars_by_symbol = {}
        for symbol, group in bars.groupby("symbol"):
            prepared = group.sort_values("timestamp").reset_index(drop=True)
            prepared["_timestamp"] = pd.to_datetime(prepared["timestamp"])
            bars_by_symbol[symbol] = prepared
        available_symbols = set(bars_by_symbol)
        cash = self.initial_cash
        position: Position | None = None
        trade_rows: list[dict[str, Any]] = []
        equity_rows: list[dict[str, Any]] = []
        trades_by_day: dict[str, int] = defaultdict(int)
        realized_pnl_by_day: dict[str, float] = defaultdict(float)
        consecutive_losses_by_day: dict[str, int] = defaultdict(int)
        day_start_equity: dict[str, float] = {}
        dynamic_entry_blocks: dict[int, str] = {}

        for signal_index, signal in enumerate(signals.itertuples(index=False)):
            signal_time = pd.Timestamp(signal.timestamp)
            trade_date = str(signal_time.date())
            action_name = str(getattr(signal, "action_name", ACTION_ID_TO_NAME.get(int(signal.predicted_action), "flat")))
            current_price = self._mark_price(position, signal_time, bars_by_symbol) if position else 0.0
            if trade_date not in day_start_equity:
                day_start_equity[trade_date] = cash + (
                    position.market_value(current_price) if position is not None and current_price > 0 else 0.0
                )
            if position is not None:
                exit_reason = self._exit_reason(position, action_name, signal_time, current_price)
                if exit_reason:
                    cash, sell_row = self._close_position(position, signal, bars_by_symbol, cash, exit_reason)
                    if sell_row is not None:
                        trade_rows.append(sell_row)
                        exit_trade_date = str(sell_row["trade_date"])
                        pnl_jpy = float(sell_row["pnl_jpy"])
                        realized_pnl_by_day[exit_trade_date] += pnl_jpy
                        if pnl_jpy < 0:
                            consecutive_losses_by_day[exit_trade_date] += 1
                        else:
                            consecutive_losses_by_day[exit_trade_date] = 0
                        position = None

            entry_blocked = bool(getattr(signal, "entry_blocked", False))
            if position is None and action_name != "flat" and not entry_blocked:
                risk_block_reason = self._risk_entry_block_reason(
                    trade_date=trade_date,
                    realized_pnl_jpy=realized_pnl_by_day[trade_date],
                    consecutive_losses=consecutive_losses_by_day[trade_date],
                    day_start_equity=day_start_equity.get(trade_date, self.initial_cash),
                )
                if risk_block_reason:
                    entry_blocked = True
                    dynamic_entry_blocks[signal_index] = risk_block_reason

            if position is None and action_name != "flat" and not entry_blocked:
                if trades_by_day[trade_date] < self.max_trades_per_day:
                    instrument = self.mapper.select_for_action(action_name, available_symbols)
                    if instrument is not None:
                        execution_block_reason = self._execution_entry_block_reason(
                            action_name,
                            signal,
                            bars_by_symbol[instrument.symbol],
                        )
                        if execution_block_reason:
                            dynamic_entry_blocks[signal_index] = execution_block_reason
                            continue
                        position, cash, buy_row = self._open_position(instrument.symbol, action_name, signal, bars_by_symbol, cash)
                        if buy_row is not None and position is not None:
                            trade_rows.append(buy_row)
                            trades_by_day[trade_date] += 1

            equity_rows.append(
                {
                    "timestamp": signal_time,
                    "cash": cash,
                    "position_symbol": position.symbol if position else "",
                    "position_qty": position.quantity if position else 0,
                    "equity": cash + (position.market_value(self._mark_price(position, signal_time, bars_by_symbol)) if position else 0.0),
                }
            )

        self._apply_dynamic_entry_blocks(signals, dynamic_entry_blocks)
        if position is not None:
            last_signal = signals.iloc[-1]
            cash, sell_row = self._close_position(position, last_signal, bars_by_symbol, cash, "end_of_backtest")
            if sell_row is not None:
                trade_rows.append(sell_row)
            equity_rows.append(
                {
                    "timestamp": pd.Timestamp(last_signal["timestamp"]),
                    "cash": cash,
                    "position_symbol": "",
                    "position_qty": 0,
                    "equity": cash,
                }
            )

        trade_log = pd.DataFrame(trade_rows)
        equity_curve = pd.DataFrame(equity_rows).drop_duplicates("timestamp", keep="last")
        if equity_curve.empty:
            equity_curve = pd.DataFrame([{"timestamp": pd.Timestamp.now(tz="UTC"), "cash": cash, "position_symbol": "", "position_qty": 0, "equity": cash}])
        metrics = summarize_backtest(equity_curve, trade_log, self.initial_cash)
        metrics["data_providers"] = data_providers
        metrics["data_is_synthetic"] = data_is_synthetic
        metrics["real_data_required"] = bool(self.config.get("market_data_collector", {}).get("require_real_provider", False))
        metrics["data_rows"] = int(len(bars))
        metrics["data_start"] = str(bars["timestamp"].min()) if not bars.empty else ""
        metrics["data_end"] = str(bars["timestamp"].max()) if not bars.empty else ""
        walk_forward_summary = self._read_walk_forward_summary()
        metrics["walk_forward_windows"] = int(walk_forward_summary.get("windows", 0)) if walk_forward_summary else 0
        metrics["walk_forward_fallback_used"] = bool(walk_forward_summary.get("fallback_used", False)) if walk_forward_summary else True
        metrics["walk_forward_rows"] = int(walk_forward_summary.get("rows", 0)) if walk_forward_summary else 0
        metrics["entry_date_filter_enabled"] = bool(self.entry_date_filter.get("enabled", False))
        metrics["entry_date_filter_name"] = str(self.entry_date_filter.get("name", ""))
        metrics["entry_date_filter_mode"] = str(self.entry_date_filter.get("mode", ""))
        metrics["entry_date_filter_dates"] = sorted(self.entry_date_filter.get("dates", set()))
        metrics["entry_date_filter_blocked_signals"] = self._entry_date_filter_blocked_count(signals)
        out = output_dir(self.config)
        write_json(out / "metrics.json", metrics)
        write_backtest_report(self.config, metrics, trade_log, equity_curve, signals)
        return metrics, trade_log, equity_curve, signals

    def _load_or_predict_signals(self, lake: DataLake, model_name: str | None) -> pd.DataFrame:
        try:
            return lake.read_frame("models", "walk_forward_predictions")
        except FileNotFoundError:
            features = lake.read_frame("features", "features")
            return create_model(self.config, model_name).predict(features)

    def _read_walk_forward_summary(self) -> dict[str, Any]:
        path = Path("data/models/walk_forward_summary.json")
        if not path.exists():
            return {}
        try:
            return read_json(path)
        except (OSError, ValueError):
            return {}

    def _apply_prediction_gates(self, signals: pd.DataFrame) -> pd.DataFrame:
        gated = signals.copy()
        if "confidence" not in gated:
            return gated
        if "action_name" not in gated:
            gated["action_name"] = gated["predicted_action"].map(lambda value: ACTION_ID_TO_NAME.get(int(value), "flat"))
        gated["action_probability"] = [
            self._signal_action_probability(row)
            for row in gated.itertuples(index=False)
        ]
        confidence = pd.to_numeric(gated["confidence"], errors="coerce").fillna(0.0)
        action_probability = pd.to_numeric(gated["action_probability"], errors="coerce").fillna(0.0)
        non_flat = gated["action_name"].astype(str) != "flat"
        suppressed = (confidence < self.min_confidence) | (non_flat & (action_probability < self.min_action_probability))
        if "net_edge_bps" in gated:
            net_edge = pd.to_numeric(gated["net_edge_bps"], errors="coerce").fillna(0.0)
            suppressed = suppressed | (non_flat & (net_edge <= 0.0))
        if suppressed.any():
            gate_note = (
                f"suppressed_by_prediction_gate(min_confidence={self.min_confidence:.4f}, "
                f"min_action_probability={self.min_action_probability:.4f})"
            )
            gated.loc[suppressed, "reason"] = gated.loc[suppressed, "reason"].fillna("").astype(str) + f"; {gate_note}"
            gated.loc[suppressed, "predicted_action"] = 0
            gated.loc[suppressed, "action_name"] = "flat"
        return gated

    def _annotate_entry_filters(self, signals: pd.DataFrame) -> pd.DataFrame:
        annotated = signals.copy()
        if "action_name" not in annotated:
            annotated["action_name"] = annotated["predicted_action"].map(lambda value: ACTION_ID_TO_NAME.get(int(value), "flat"))
        reasons = [
            self._entry_block_reason(
                action_name=str(row.action_name),
                timestamp=pd.Timestamp(row.timestamp),
                market_regime=str(getattr(row, "market_regime", "unknown")),
                time_basis="signal",
            )
            for row in annotated.itertuples(index=False)
        ]
        date_reasons = [
            self._entry_date_block_reason(
                action_name=str(row.action_name),
                timestamp=pd.Timestamp(row.timestamp),
            )
            for row in annotated.itertuples(index=False)
        ]
        combined = [_join_reasons(reason, date_reason) for reason, date_reason in zip(reasons, date_reasons)]
        annotated["entry_blocked"] = [bool(reason) for reason in combined]
        annotated["entry_block_reason"] = combined
        return annotated

    def _load_entry_filters(self) -> list[dict[str, Any]]:
        filters = self.config.get("strategy", {}).get("entry_filters", [])
        if isinstance(filters, dict):
            filters = [filters]
        if not isinstance(filters, list):
            return []
        loaded: list[dict[str, Any]] = []
        for index, raw_filter in enumerate(filters):
            if not isinstance(raw_filter, dict):
                continue
            if raw_filter.get("enabled", True) is False:
                continue
            loaded.append(
                {
                    "name": str(raw_filter.get("name", f"entry_filter_{index + 1}")),
                    "actions": _string_set(raw_filter.get("actions")),
                    "sessions": _string_set(raw_filter.get("sessions")),
                    "market_regimes": _string_set(raw_filter.get("market_regimes")),
                    "start_time": _time_string(raw_filter.get("start_time")),
                    "end_time": _time_string(raw_filter.get("end_time")),
                    "time_basis": _time_basis(raw_filter.get("time_basis", "signal")),
                }
            )
        return loaded

    def _load_entry_date_filter(self) -> dict[str, Any]:
        raw_filter = self.config.get("backtest", {}).get("entry_date_filter", {})
        if not isinstance(raw_filter, dict) or raw_filter.get("enabled", False) is not True:
            return {"enabled": False, "name": "", "mode": "", "dates": set()}
        dates = _date_set(raw_filter.get("dates"))
        if not dates:
            return {"enabled": False, "name": "", "mode": "", "dates": set()}
        mode = str(raw_filter.get("mode", "exclude"))
        if mode not in {"exclude", "include"}:
            raise ValueError(f"Expected entry_date_filter mode to be exclude or include; got {mode!r}")
        return {
            "enabled": True,
            "name": str(raw_filter.get("name", f"entry_date_filter_{mode}")),
            "mode": mode,
            "dates": dates,
        }

    def _entry_date_block_reason(self, action_name: str, timestamp: pd.Timestamp) -> str:
        if action_name == "flat" or not bool(self.entry_date_filter.get("enabled", False)):
            return ""
        trade_date = pd.Timestamp(timestamp).strftime("%Y-%m-%d")
        dates = self.entry_date_filter.get("dates", set())
        mode = str(self.entry_date_filter.get("mode", "exclude"))
        if mode == "exclude" and trade_date in dates:
            return str(self.entry_date_filter.get("name", "entry_date_filter_exclude"))
        if mode == "include" and trade_date not in dates:
            return str(self.entry_date_filter.get("name", "entry_date_filter_include"))
        return ""

    def _entry_block_reason(
        self,
        action_name: str,
        timestamp: pd.Timestamp,
        market_regime: str,
        *,
        time_basis: str,
    ) -> str:
        if action_name == "flat":
            return ""
        session = _session(timestamp)
        for entry_filter in self.entry_filters:
            if not _time_basis_matches(entry_filter["time_basis"], time_basis):
                continue
            actions = entry_filter["actions"]
            sessions = entry_filter["sessions"]
            market_regimes = entry_filter["market_regimes"]
            start_time = entry_filter["start_time"]
            end_time = entry_filter["end_time"]
            if actions and action_name not in actions:
                continue
            if sessions and session not in sessions:
                continue
            if market_regimes and market_regime not in market_regimes:
                continue
            if not _time_matches(timestamp, start_time, end_time):
                continue
            return str(entry_filter["name"])
        return ""

    def _execution_entry_block_reason(self, action_name: str, signal: Any, symbol_bars: pd.DataFrame) -> str:
        bar = next_bar(symbol_bars, pd.Timestamp(_signal_value(signal, "timestamp")), self.delay_bars)
        if bar is None:
            return ""
        reasons = [
            self._entry_block_reason(
                action_name=action_name,
                timestamp=pd.Timestamp(bar.timestamp),
                market_regime=self._signal_market_regime(signal),
                time_basis="execution",
            ),
            self._market_entry_block_reason(signal, bar),
        ]
        return "; ".join(reason for reason in reasons if reason)

    def _market_entry_block_reason(self, signal: Any, bar: Any) -> str:
        timestamp = pd.Timestamp(_bar_value(bar, "timestamp"))
        session_reason = session_block_reason(timestamp, self.config)
        if session_reason:
            return session_reason

        if self.max_spread_bps > 0:
            spread_bps = _safe_float(_bar_value(bar, "spread_bps", _signal_value(signal, "spread_bps", None)), float("nan"))
            if pd.notna(spread_bps) and spread_bps > self.max_spread_bps:
                return f"spread_filter({spread_bps:.2f}>{self.max_spread_bps:.2f})"
            if pd.isna(spread_bps) and bool(self.execution_config.get("require_bid_ask", False)):
                return "missing_bid_ask_for_spread_filter"

        if self.max_quote_age_seconds > 0:
            quote_age = _safe_float(_bar_value(bar, "quote_age_seconds", _signal_value(signal, "quote_age_seconds", None)), float("nan"))
            if pd.notna(quote_age) and quote_age > self.max_quote_age_seconds:
                return f"quote_stale_filter({quote_age:.2f}>{self.max_quote_age_seconds:.2f})"

        bid_depth = _safe_float(_bar_value(bar, "bid_depth", None), float("nan"))
        ask_depth = _safe_float(_bar_value(bar, "ask_depth", None), float("nan"))
        if self.min_bid_depth > 0 and pd.notna(bid_depth) and bid_depth < self.min_bid_depth:
            return f"depth_filter_bid({bid_depth:.0f}<{self.min_bid_depth:.0f})"
        if self.min_ask_depth > 0 and pd.notna(ask_depth) and ask_depth < self.min_ask_depth:
            return f"depth_filter_ask({ask_depth:.0f}<{self.min_ask_depth:.0f})"

        if self.max_implied_dispersion_bps > 0:
            dispersion = _safe_float(_signal_value(signal, "implied_nikkei_dispersion_bps", None), float("nan"))
            if pd.notna(dispersion) and dispersion > self.max_implied_dispersion_bps:
                return f"etf_dispersion_filter({dispersion:.2f}>{self.max_implied_dispersion_bps:.2f})"

        return ""

    def _risk_entry_block_reason(
        self,
        trade_date: str,
        realized_pnl_jpy: float,
        consecutive_losses: int,
        day_start_equity: float,
    ) -> str:
        if not self.realized_loss_gate_enabled:
            return ""
        if self.max_daily_loss_pct > 0 and day_start_equity > 0:
            daily_loss_limit = day_start_equity * self.max_daily_loss_pct / 100.0
            if realized_pnl_jpy <= -daily_loss_limit:
                return f"risk_daily_loss_limit({trade_date})"
        if self.max_consecutive_losses > 0 and consecutive_losses >= self.max_consecutive_losses:
            return f"risk_max_consecutive_losses({trade_date})"
        return ""

    @staticmethod
    def _apply_dynamic_entry_blocks(signals: pd.DataFrame, dynamic_entry_blocks: dict[int, str]) -> None:
        for row_index, reason in dynamic_entry_blocks.items():
            existing = str(signals.at[row_index, "entry_block_reason"]) if "entry_block_reason" in signals else ""
            existing = "" if existing == "nan" else existing
            signals.at[row_index, "entry_blocked"] = True
            signals.at[row_index, "entry_block_reason"] = "; ".join(part for part in (existing, reason) if part)

    def _entry_date_filter_blocked_count(self, signals: pd.DataFrame) -> int:
        name = str(self.entry_date_filter.get("name", ""))
        if not name or "entry_block_reason" not in signals:
            return 0
        return int(signals["entry_block_reason"].fillna("").astype(str).str.contains(name, regex=False).sum())

    @staticmethod
    def _signal_action_probability(signal: Any) -> float:
        action_name = str(getattr(signal, "action_name", ACTION_ID_TO_NAME.get(int(getattr(signal, "predicted_action", 0)), "flat")))
        probability_column = ACTION_PROBABILITY_COLUMNS.get(action_name)
        if probability_column and hasattr(signal, probability_column):
            try:
                value = float(getattr(signal, probability_column))
                if pd.notna(value):
                    return value
            except (TypeError, ValueError):
                pass
        try:
            return float(getattr(signal, "confidence", 0.0))
        except (TypeError, ValueError):
            return 0.0

    def _open_position(
        self,
        symbol: str,
        action_name: str,
        signal: Any,
        bars_by_symbol: dict[str, pd.DataFrame],
        cash: float,
    ) -> tuple[Position | None, float, dict[str, Any] | None]:
        bar = next_bar(bars_by_symbol[symbol], pd.Timestamp(signal.timestamp), self.delay_bars)
        if bar is None:
            return None, cash, None
        reference_price = self._execution_reference_price(bar, "BUY")
        bar_turnover = self._bar_turnover(bar, reference_price)
        sizing = self._dynamic_position_sizing(action_name, signal, execution_timestamp=pd.Timestamp(bar.timestamp))
        notional_target = cash * sizing["target_equity_pct"] / 100.0
        base_price = self.broker.buy_fill_price(reference_price)
        quantity = int(notional_target // base_price)
        if quantity <= 0:
            return None, cash, None
        price = self.broker.buy_fill_price(reference_price, quantity * reference_price, bar_turnover)
        quantity = int(notional_target // price)
        if quantity <= 0:
            return None, cash, None
        price = self.broker.buy_fill_price(reference_price, quantity * reference_price, bar_turnover)
        notional = quantity * price
        commission = self.cost_model.commission(notional)
        while quantity > 0 and notional + commission > cash:
            quantity -= 1
            price = self.broker.buy_fill_price(reference_price, quantity * reference_price, bar_turnover)
            notional = quantity * price
            commission = self.cost_model.commission(notional)
        if quantity <= 0:
            return None, cash, None
        cash_after = cash - notional - commission
        execution = self.cost_model.execution_breakdown(reference_price, "BUY", quantity * reference_price, bar_turnover)
        position = Position(
            symbol=symbol,
            action_name=action_name,
            quantity=quantity,
            entry_price=price,
            entry_timestamp=pd.Timestamp(bar.timestamp),
            entry_reason=str(signal.reason),
            entry_commission_jpy=commission,
            confidence=sizing["confidence"],
            action_probability=sizing["action_probability"],
            sizing_multiplier=sizing["sizing_multiplier"],
            base_equity_pct=sizing["base_equity_pct"],
            target_equity_pct=sizing["target_equity_pct"],
            absolute_max_equity_pct=sizing["absolute_max_equity_pct"],
            max_holding_minutes=int(sizing["max_holding_minutes"]),
            stop_loss_pct=sizing["stop_loss_pct"],
            take_profit_pct=sizing["take_profit_pct"],
            market_regime=sizing["market_regime"],
        )
        return position, cash_after, self._trade_row(
            timestamp=pd.Timestamp(bar.timestamp),
            action="BUY",
            symbol=symbol,
            position_action=action_name,
            price=price,
            quantity=quantity,
            reason=str(signal.reason),
            exit_reason="",
            pnl_jpy=0.0,
            pnl_pct=0.0,
            market_regime=str(getattr(signal, "market_regime", "unknown")),
            reference_price=reference_price,
            notional_jpy=notional,
            commission_jpy=commission,
            execution=execution,
            signal=signal,
            bar=bar,
            risk_filter_results="approved",
            confidence=sizing["confidence"],
            action_probability=sizing["action_probability"],
            sizing_multiplier=sizing["sizing_multiplier"],
            base_equity_pct=sizing["base_equity_pct"],
            target_equity_pct=sizing["target_equity_pct"],
            absolute_max_equity_pct=sizing["absolute_max_equity_pct"],
            max_holding_minutes=int(sizing["max_holding_minutes"]),
            stop_loss_pct=sizing["stop_loss_pct"],
            take_profit_pct=sizing["take_profit_pct"],
        )

    def _close_position(
        self,
        position: Position,
        signal: Any,
        bars_by_symbol: dict[str, pd.DataFrame],
        cash: float,
        exit_reason: str,
    ) -> tuple[float, dict[str, Any] | None]:
        signal_time = pd.Timestamp(signal["timestamp"] if isinstance(signal, pd.Series) else signal.timestamp)
        bar = next_bar(bars_by_symbol[position.symbol], signal_time, self.delay_bars)
        if bar is None:
            symbol_bars = bars_by_symbol[position.symbol]
            bar = symbol_bars.iloc[-1]
        reference_price = self._execution_reference_price(bar, "SELL")
        bar_turnover = self._bar_turnover(bar, reference_price)
        price = self.broker.sell_fill_price(reference_price, position.quantity * reference_price, bar_turnover)
        notional = position.quantity * price
        commission = self.cost_model.commission(notional)
        pnl = position.quantity * (price - position.entry_price) - position.entry_commission_jpy - commission
        cash_after = cash + notional - commission
        execution = self.cost_model.execution_breakdown(reference_price, "SELL", position.quantity * reference_price, bar_turnover)
        reason = str(signal.get("reason", "") if isinstance(signal, pd.Series) else getattr(signal, "reason", ""))
        market_regime = str(signal.get("market_regime", "unknown") if isinstance(signal, pd.Series) else getattr(signal, "market_regime", "unknown"))
        row = self._trade_row(
            timestamp=pd.Timestamp(bar.timestamp),
            action="SELL",
            symbol=position.symbol,
            position_action=position.action_name,
            price=price,
            quantity=position.quantity,
            reason=reason or position.entry_reason,
            exit_reason=exit_reason,
            pnl_jpy=pnl,
            pnl_pct=(price / position.entry_price - 1.0) * 100.0,
            market_regime=market_regime,
            reference_price=reference_price,
            notional_jpy=notional,
            commission_jpy=commission,
            execution=execution,
            signal=signal,
            bar=bar,
            risk_filter_results=exit_reason,
            confidence=position.confidence,
            action_probability=position.action_probability,
            sizing_multiplier=position.sizing_multiplier,
            base_equity_pct=position.base_equity_pct,
            target_equity_pct=position.target_equity_pct,
            absolute_max_equity_pct=position.absolute_max_equity_pct,
            max_holding_minutes=position.max_holding_minutes,
            stop_loss_pct=position.stop_loss_pct,
            take_profit_pct=position.take_profit_pct,
        )
        return cash_after, row

    def _exit_reason(self, position: Position, signal_action: str, signal_time: pd.Timestamp, current_price: float) -> str | None:
        if current_price <= 0:
            return None
        hold_minutes = (signal_time - position.entry_timestamp).total_seconds() / 60.0
        pnl_pct = position.unrealized_pct(current_price)
        if pnl_pct <= -abs(position.stop_loss_pct):
            return "stop_loss"
        if position.take_profit_pct > 0 and pnl_pct >= abs(position.take_profit_pct):
            return "take_profit"
        if (
            self.exit_if_no_profit_enabled
            and self.exit_if_no_profit_after_minutes > 0
            and hold_minutes >= self.exit_if_no_profit_after_minutes
            and pnl_pct <= 0.0
        ):
            return "no_profit_time_stop"
        if hold_minutes >= position.max_holding_minutes:
            return "max_holding_minutes"
        force_exit_match = self._matched_force_exit_time(position.entry_timestamp, signal_time)
        if force_exit_match:
            return f"force_exit_time_{force_exit_match}"
        if not self.force_exit_times and signal_time.strftime("%H:%M") >= self.force_exit_time:
            return "force_exit_time"
        if self.exit_on_neutral_signal and signal_action == "flat":
            return "neutral_signal"
        if self.exit_on_opposite_signal and signal_action != "flat" and _side(signal_action) != _side(position.action_name):
            return f"opposite_signal_{signal_action}"
        return None

    def _mark_price(self, position: Position | None, timestamp: pd.Timestamp, bars_by_symbol: dict[str, pd.DataFrame]) -> float:
        if position is None:
            return 0.0
        group = bars_by_symbol[position.symbol]
        times = group["_timestamp"] if "_timestamp" in group.columns else pd.to_datetime(group["timestamp"])
        idx = times.searchsorted(timestamp, side="right") - 1
        if idx < 0:
            return float(position.entry_price)
        return float(group.iloc[int(idx)].close)

    def _execution_reference_price(self, bar: Any, side: str) -> float:
        if bool(self.execution_config.get("use_bid_ask_prices", False)):
            bid = _safe_float(_bar_value(bar, "best_bid", _bar_value(bar, "bid", None)), float("nan"))
            ask = _safe_float(_bar_value(bar, "best_ask", _bar_value(bar, "ask", None)), float("nan"))
            if pd.notna(bid) and pd.notna(ask) and bid > 0 and ask >= bid:
                return float(ask if side == "BUY" else bid)
        mid = _safe_float(_bar_value(bar, "mid", _bar_value(bar, "mid_price", None)), float("nan"))
        if bool(self.execution_config.get("use_mid_price", False)) and pd.notna(mid) and mid > 0:
            return float(mid)
        return float(_bar_value(bar, "open"))

    def _bar_turnover(self, bar: Any, reference_price: float) -> float:
        turnover = getattr(bar, "turnover", 0.0)
        try:
            turnover_value = float(turnover)
        except (TypeError, ValueError):
            turnover_value = 0.0
        if pd.notna(turnover_value) and turnover_value > 0:
            return turnover_value
        volume = getattr(bar, "volume", 0.0)
        try:
            volume_value = float(volume)
        except (TypeError, ValueError):
            volume_value = 0.0
        if pd.notna(volume_value) and volume_value > 0:
            return volume_value * reference_price
        return 0.0

    def _trade_row(
        self,
        timestamp: pd.Timestamp,
        action: str,
        symbol: str,
        position_action: str,
        price: float,
        quantity: int,
        reason: str,
        exit_reason: str,
        pnl_jpy: float,
        pnl_pct: float,
        market_regime: str,
        reference_price: float,
        notional_jpy: float,
        commission_jpy: float,
        execution: dict[str, float],
        signal: Any | None = None,
        bar: Any | None = None,
        risk_filter_results: str = "",
        confidence: float = 0.0,
        action_probability: float = 0.0,
        sizing_multiplier: float = 1.0,
        base_equity_pct: float = 0.0,
        target_equity_pct: float = 0.0,
        absolute_max_equity_pct: float = 0.0,
        max_holding_minutes: int = 0,
        stop_loss_pct: float = 0.0,
        take_profit_pct: float = 0.0,
    ) -> dict[str, Any]:
        return {
            "timestamp": timestamp,
            "action": action,
            "symbol": symbol,
            "reference_price": reference_price,
            "price": price,
            "quantity": quantity,
            "notional_jpy": notional_jpy,
            "commission_jpy": commission_jpy,
            "order_id": "",
            "order_type": "simulated_market",
            "submitted_price": reference_price,
            "filled_price": price,
            "bid": _bar_value(bar, "best_bid", _bar_value(bar, "bid", "")) if bar is not None else "",
            "ask": _bar_value(bar, "best_ask", _bar_value(bar, "ask", "")) if bar is not None else "",
            "mid": _bar_value(bar, "mid", _bar_value(bar, "mid_price", reference_price)) if bar is not None else reference_price,
            "slippage_bps": execution["slippage_bps"],
            "spread_bps": execution["spread_bps"],
            "bid_depth": _bar_value(bar, "bid_depth", "") if bar is not None else "",
            "ask_depth": _bar_value(bar, "ask_depth", "") if bar is not None else "",
            "quote_time": _bar_value(bar, "quote_time", "") if bar is not None else "",
            "last_trade_time": _bar_value(bar, "last_trade_time", "") if bar is not None else "",
            "market_impact_bps": execution["market_impact_bps"],
            "execution_cost_bps": execution["execution_cost_bps"],
            "bar_turnover_jpy": execution["bar_turnover_jpy"],
            "expected_return_bps": _safe_float(_signal_value(signal, "expected_return_bps", 0.0), 0.0),
            "expected_cost_bps": _safe_float(_signal_value(signal, "expected_cost_bps", execution["execution_cost_bps"]), execution["execution_cost_bps"]),
            "net_edge_bps": _safe_float(_signal_value(signal, "net_edge_bps", 0.0), 0.0),
            "recommended_position_size": _safe_float(_signal_value(signal, "recommended_position_size", target_equity_pct), target_equity_pct),
            "reason_codes": str(_signal_value(signal, "reason_codes", "")),
            "risk_filter_results": risk_filter_results,
            "implied_nikkei_1321_bps": _signal_value(signal, "implied_nikkei_1321_bps", ""),
            "implied_nikkei_1570_bps": _signal_value(signal, "implied_nikkei_1570_bps", ""),
            "implied_nikkei_1571_bps": _signal_value(signal, "implied_nikkei_1571_bps", ""),
            "implied_nikkei_1357_bps": _signal_value(signal, "implied_nikkei_1357_bps", ""),
            "implied_nikkei_dispersion_bps": _signal_value(signal, "implied_nikkei_dispersion_bps", ""),
            "futures_return_1m": _signal_value(signal, "futures_return_1m", ""),
            "index_return_1m": _signal_value(signal, "index_return_1m", ""),
            "etf_vs_inav_premium_bps": _signal_value(signal, "etf_vs_inav_premium_bps", ""),
            "reason": reason,
            "exit_reason": exit_reason,
            "pnl_jpy": pnl_jpy,
            "pnl_pct": pnl_pct,
            "position_action": position_action,
            "trade_date": str(timestamp.date()),
            "year": str(timestamp.year),
            "month": timestamp.strftime("%Y-%m"),
            "session": _session(timestamp),
            "market_regime": market_regime,
            "confidence": confidence,
            "action_probability": action_probability,
            "sizing_multiplier": sizing_multiplier,
            "base_equity_pct": base_equity_pct,
            "target_equity_pct": target_equity_pct,
            "absolute_max_equity_pct": absolute_max_equity_pct,
            "max_holding_minutes": max_holding_minutes,
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
        }

    def _dynamic_position_sizing(
        self,
        action_name: str,
        signal: Any,
        execution_timestamp: pd.Timestamp | None = None,
    ) -> dict[str, Any]:
        limits = self.config.get("backtest", {}).get("position_limits", {}).get(action_name, {})
        base_pct = float(limits.get("max_equity_pct", 30.0))
        absolute_max_pct = float(limits.get("absolute_max_equity_pct", base_pct))
        confidence = self._signal_confidence(signal)
        action_probability = self._signal_action_probability(signal)
        market_regime = self._signal_market_regime(signal)
        multiplier = 1.0

        if bool(self.position_sizing_config.get("dynamic_enabled", False)):
            strength = max(confidence, action_probability)
            floor = float(self.position_sizing_config.get("confidence_floor", self.min_confidence))
            full = float(self.position_sizing_config.get("confidence_full", 0.75))
            min_multiplier = float(self.position_sizing_config.get("min_multiplier", 0.35))
            max_multiplier = float(self.position_sizing_config.get("max_multiplier", 1.25))
            strength_ratio = _clamp((strength - floor) / max(full - floor, 0.001), 0.0, 1.0)
            multiplier = min_multiplier + (max_multiplier - min_multiplier) * strength_ratio
            if market_regime == "trend":
                multiplier *= float(self.position_sizing_config.get("trend_multiplier", 1.10))
            elif market_regime == "range":
                multiplier *= float(self.position_sizing_config.get("range_multiplier", 0.85))
            else:
                multiplier *= float(self.position_sizing_config.get("unknown_regime_multiplier", 0.90))
            if action_name.endswith("2x"):
                multiplier *= float(self.position_sizing_config.get("leveraged_etf_multiplier", 0.95))

        multiplier *= self._position_sizing_adjustment_multiplier(
            action_name,
            signal,
            execution_timestamp=execution_timestamp,
        )
        min_pct = float(self.position_sizing_config.get("min_equity_pct", 1.0))
        target_pct = _clamp(base_pct * multiplier, min_pct, absolute_max_pct)
        return {
            "confidence": confidence,
            "action_probability": action_probability,
            "sizing_multiplier": multiplier,
            "base_equity_pct": base_pct,
            "target_equity_pct": target_pct,
            "absolute_max_equity_pct": absolute_max_pct,
            "max_holding_minutes": self._dynamic_max_holding_minutes(action_name, signal),
            "stop_loss_pct": self._dynamic_stop_loss_pct(action_name, signal),
            "take_profit_pct": self._take_profit_pct(action_name),
            "market_regime": market_regime,
        }

    def _take_profit_pct(self, action_name: str) -> float:
        config = self.take_profit_config
        if not isinstance(config, dict) or bool(config.get("enabled", False)) is not True:
            return 0.0
        pct_by_action = config.get("pct", {})
        if isinstance(pct_by_action, dict) and action_name in pct_by_action:
            return max(0.0, float(pct_by_action.get(action_name, 0.0) or 0.0))
        multiplier = float(config.get("stop_loss_multiple", 0.0) or 0.0)
        if multiplier <= 0:
            return 0.0
        return abs(self._base_stop_loss_pct(action_name)) * multiplier

    def _dynamic_max_holding_minutes(self, action_name: str, signal: Any) -> int:
        base_minutes = self.max_holding_minutes
        config = self.dynamic_holding_config
        if not bool(config.get("enabled", False)):
            return base_minutes
        confidence = self._signal_confidence(signal)
        action_probability = self._signal_action_probability(signal)
        strength = max(confidence, action_probability)
        threshold = float(config.get("confidence_extend_threshold", 0.65))
        full = float(config.get("confidence_full", 0.85))
        extension = float(config.get("confidence_extend_multiplier", 1.50))
        ratio = _clamp((strength - threshold) / max(full - threshold, 0.001), 0.0, 1.0)
        multiplier = 1.0 + (extension - 1.0) * ratio
        market_regime = self._signal_market_regime(signal)
        if strength <= float(config.get("low_confidence_reduce_threshold", 0.45)):
            multiplier *= float(config.get("low_confidence_multiplier", 0.75))
        if market_regime == "trend" and strength >= threshold:
            multiplier *= float(config.get("trend_multiplier", 1.25))
        elif market_regime == "range":
            multiplier *= float(config.get("range_multiplier", 0.75))
        else:
            multiplier *= float(config.get("unknown_regime_multiplier", 0.90))
        if action_name.endswith("2x"):
            multiplier *= float(config.get("leveraged_etf_multiplier", 0.90))
        min_minutes = int(config.get("min_minutes", 20))
        max_minutes = int(config.get("max_minutes", 120))
        return int(round(_clamp(base_minutes * multiplier, min_minutes, max_minutes)))

    def _dynamic_stop_loss_pct(self, action_name: str, signal: Any) -> float:
        base_pct = self._base_stop_loss_pct(action_name)
        config = self.dynamic_stop_config
        if not bool(config.get("enabled", False)):
            return base_pct
        confidence = self._signal_confidence(signal)
        action_probability = self._signal_action_probability(signal)
        strength = max(confidence, action_probability)
        multiplier = 1.0
        if strength >= float(config.get("high_confidence_threshold", 0.70)):
            multiplier *= float(config.get("high_confidence_widen_multiplier", 1.25))
        elif strength <= float(config.get("low_confidence_threshold", 0.45)):
            multiplier *= float(config.get("low_confidence_tighten_multiplier", 0.75))
        market_regime = self._signal_market_regime(signal)
        if market_regime == "trend":
            multiplier *= float(config.get("trend_widen_multiplier", 1.15))
        elif market_regime == "range":
            multiplier *= float(config.get("range_tighten_multiplier", 0.85))
        minimums = config.get("min_pct", {})
        maximums = config.get("max_pct", {})
        min_pct = float(minimums.get(action_name, base_pct * 0.5)) if isinstance(minimums, dict) else base_pct * 0.5
        max_pct = float(maximums.get(action_name, base_pct * 1.6)) if isinstance(maximums, dict) else base_pct * 1.6
        return _clamp(base_pct * multiplier, min_pct, max_pct)

    def _base_stop_loss_pct(self, action_name: str) -> float:
        return float(self.config.get("strategy", {}).get("exit", {}).get("stop_loss_pct", {}).get(action_name, 1.0))

    def _position_sizing_adjustment_multiplier(
        self,
        action_name: str,
        signal: Any,
        execution_timestamp: pd.Timestamp | None = None,
    ) -> float:
        adjustments = self.position_sizing_config.get("adjustments", [])
        if isinstance(adjustments, dict):
            adjustments = [adjustments]
        if not isinstance(adjustments, list):
            return 1.0
        signal_timestamp = pd.Timestamp(_signal_value(signal, "timestamp"))
        execution_timestamp = pd.Timestamp(execution_timestamp) if execution_timestamp is not None else signal_timestamp
        market_regime = self._signal_market_regime(signal)
        confidence = self._signal_confidence(signal)
        action_probability = self._signal_action_probability(signal)
        multiplier = 1.0
        for adjustment in adjustments:
            if not isinstance(adjustment, dict) or adjustment.get("enabled", True) is False:
                continue
            time_basis = _time_basis(adjustment.get("time_basis", "signal"))
            actions = _string_set(adjustment.get("actions"))
            sessions = _string_set(adjustment.get("sessions"))
            market_regimes = _string_set(adjustment.get("market_regimes"))
            start_time = _time_string(adjustment.get("start_time"))
            end_time = _time_string(adjustment.get("end_time"))
            if actions and action_name not in actions:
                continue
            if market_regimes and market_regime not in market_regimes:
                continue
            if not _threshold_matches(
                confidence,
                min_value=adjustment.get("min_confidence"),
                max_value=adjustment.get("max_confidence"),
            ):
                continue
            if not _threshold_matches(
                action_probability,
                min_value=adjustment.get("min_action_probability"),
                max_value=adjustment.get("max_action_probability"),
            ):
                continue
            if not _adjustment_time_matches(
                time_basis=time_basis,
                signal_timestamp=signal_timestamp,
                execution_timestamp=execution_timestamp,
                sessions=sessions,
                start_time=start_time,
                end_time=end_time,
            ):
                continue
            multiplier *= _safe_float(adjustment.get("multiplier", 1.0), 1.0)
        return multiplier

    @staticmethod
    def _signal_confidence(signal: Any) -> float:
        return _safe_float(_signal_value(signal, "confidence", 0.0), 0.0)

    @staticmethod
    def _signal_market_regime(signal: Any) -> str:
        value = _signal_value(signal, "market_regime", "unknown")
        return str(value) if value not in (None, "") else "unknown"

    def _matched_force_exit_time(self, entry_timestamp: pd.Timestamp, signal_timestamp: pd.Timestamp) -> str:
        if not self.force_exit_times:
            return ""
        entry_hhmm = pd.Timestamp(entry_timestamp).strftime("%H:%M")
        signal_hhmm = pd.Timestamp(signal_timestamp).strftime("%H:%M")
        for force_exit_time in self.force_exit_times:
            if entry_hhmm < force_exit_time <= signal_hhmm:
                return force_exit_time
        return ""


def run_backtest(config: dict[str, Any], model_name: str | None = None) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    return BacktestEngine(config).run(model_name)


def _string_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    try:
        return {str(item) for item in value}
    except TypeError:
        return {str(value)}


def _date_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        values = [value]
    else:
        try:
            values = list(value)
        except TypeError:
            values = [value]
    dates: set[str] = set()
    for item in values:
        if item in (None, ""):
            continue
        dates.add(str(pd.Timestamp(item).date()))
    return dates


def _join_reasons(*reasons: str) -> str:
    return "; ".join(reason for reason in reasons if reason)


def _time_string(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = str(value)
    if len(text) == 5 and text[2] == ":":
        return text
    raise ValueError(f"Expected HH:MM time string, got {value!r}")


def _time_basis(value: Any) -> str:
    basis = str(value or "signal")
    if basis not in {"signal", "execution", "both"}:
        raise ValueError(f"Expected entry filter time_basis to be signal, execution, or both; got {value!r}")
    return basis


def _time_basis_matches(filter_basis: str, current_basis: str) -> bool:
    return filter_basis == "both" or filter_basis == current_basis


def _adjustment_time_matches(
    *,
    time_basis: str,
    signal_timestamp: pd.Timestamp,
    execution_timestamp: pd.Timestamp,
    sessions: set[str],
    start_time: str,
    end_time: str,
) -> bool:
    timestamps = [signal_timestamp, execution_timestamp] if time_basis == "both" else [
        execution_timestamp if time_basis == "execution" else signal_timestamp
    ]
    for timestamp in timestamps:
        if sessions and _session(timestamp) not in sessions:
            continue
        if _time_matches(timestamp, start_time, end_time):
            return True
    return False


def _time_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [_time_string(value)]
    try:
        return sorted({_time_string(item) for item in value if item not in (None, "")})
    except TypeError:
        return [_time_string(value)]


def _time_matches(timestamp: pd.Timestamp, start_time: str, end_time: str) -> bool:
    if not start_time and not end_time:
        return True
    hhmm = timestamp.strftime("%H:%M")
    if start_time and end_time:
        if start_time <= end_time:
            return start_time <= hhmm <= end_time
        return hhmm >= start_time or hhmm <= end_time
    if start_time:
        return hhmm >= start_time
    return hhmm <= end_time


def _threshold_matches(value: float, *, min_value: Any = None, max_value: Any = None) -> bool:
    if min_value not in (None, "") and value < _safe_float(min_value, float("-inf")):
        return False
    if max_value not in (None, "") and value > _safe_float(max_value, float("inf")):
        return False
    return True


def _signal_value(signal: Any, key: str, default: Any = None) -> Any:
    if signal is None:
        return default
    if isinstance(signal, pd.Series):
        return signal.get(key, default)
    return getattr(signal, key, default)


def _bar_value(bar: Any, key: str, default: Any = None) -> Any:
    if bar is None:
        return default
    if isinstance(bar, pd.Series):
        return bar.get(key, default)
    return getattr(bar, key, default)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if pd.notna(number) else default


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
