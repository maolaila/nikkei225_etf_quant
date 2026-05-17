from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.data.data_lake import DataLake
from src.data.symbol_mapper import SymbolMapper
from src.utils.paths import ensure_dir
from src.utils.serialization import write_json


@dataclass(frozen=True)
class EventAuditResult:
    daily_flags: pd.DataFrame
    abnormal_minutes: pd.DataFrame
    summary: dict[str, Any]
    output_dir: Path


def run_event_audit(
    config: dict[str, Any],
    *,
    output_dir: str | Path | None = None,
    lake: DataLake | None = None,
) -> EventAuditResult:
    lake = lake or DataLake()
    bars = lake.read_frame("normalized", "minute_bars")
    daily_flags, abnormal_minutes, summary = build_event_audit_frames(bars, config)
    out = ensure_dir(output_dir or _audit_config(config).get("output_dir", "data/reports/event_audit"))
    max_rows = int(_audit_config(config).get("max_rows_per_detail_report", 200))

    daily_flags.to_csv(out / "daily_event_flags.csv", index=False)
    abnormal_minutes.head(max_rows).to_csv(out / "abnormal_minute_bars.csv", index=False)
    write_json(out / "event_audit_summary.json", summary)
    (out / "event_audit.md").write_text(_render_event_audit_markdown(summary, daily_flags, abnormal_minutes, max_rows), encoding="utf-8")
    return EventAuditResult(daily_flags=daily_flags, abnormal_minutes=abnormal_minutes, summary=summary, output_dir=out)


def build_event_audit_frames(bars: pd.DataFrame, config: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    audit_config = _audit_config(config)
    frame = _prepare_bars(bars)
    if frame.empty:
        daily_flags = _empty_daily_flags()
        abnormal_minutes = _empty_abnormal_minutes()
        return daily_flags, abnormal_minutes, _summary(frame, daily_flags, abnormal_minutes, audit_config)

    specs = _symbol_specs(config)
    frame["direction"] = frame["symbol"].map(lambda symbol: specs.get(symbol, (1, 1.0))[0]).astype(float)
    frame["leverage"] = frame["symbol"].map(lambda symbol: specs.get(symbol, (1, 1.0))[1]).astype(float).replace(0, 1.0).abs()
    frame["trade_date"] = frame["timestamp"].dt.date.astype(str)
    frame["return_1m_pct"] = frame.groupby(["symbol", "trade_date"], sort=False)["close"].pct_change() * 100.0
    frame["underlying_return_1m_pct"] = frame["return_1m_pct"] * frame["direction"] / frame["leverage"]
    minute_threshold = float(audit_config.get("abnormal_minute_abs_underlying_return_pct", 1.0))
    frame["abnormal_minute_bar"] = frame["underlying_return_1m_pct"].abs() >= minute_threshold

    daily_flags = _daily_flags(frame, audit_config)
    abnormal_minutes = (
        frame.loc[frame["abnormal_minute_bar"], _ABNORMAL_MINUTE_COLUMNS]
        .sort_values("underlying_return_1m_pct", key=lambda series: series.abs(), ascending=False)
        .reset_index(drop=True)
    )
    summary = _summary(frame, daily_flags, abnormal_minutes, audit_config)
    return daily_flags, abnormal_minutes, summary


def market_event_dates(daily_flags: pd.DataFrame, *, include_corporate_action_candidates: bool = False) -> list[str]:
    if daily_flags.empty or "trade_date" not in daily_flags:
        return []
    mask = daily_flags.get("event_day", pd.Series(False, index=daily_flags.index)).fillna(False).astype(bool)
    if include_corporate_action_candidates:
        mask = mask | daily_flags.get("corporate_action_candidate", pd.Series(False, index=daily_flags.index)).fillna(False).astype(bool)
    return sorted(str(item) for item in daily_flags.loc[mask, "trade_date"].dropna().unique())


def corporate_action_candidate_dates(daily_flags: pd.DataFrame) -> list[str]:
    if daily_flags.empty or "trade_date" not in daily_flags:
        return []
    mask = daily_flags.get("corporate_action_candidate", pd.Series(False, index=daily_flags.index)).fillna(False).astype(bool)
    return sorted(str(item) for item in daily_flags.loc[mask, "trade_date"].dropna().unique())


_DAILY_COLUMNS = [
    "trade_date",
    "symbol",
    "rows",
    "open",
    "high",
    "low",
    "close",
    "prev_close",
    "close_to_close_return_pct",
    "underlying_close_to_close_return_pct",
    "open_gap_pct",
    "underlying_open_gap_pct",
    "open_close_return_pct",
    "intraday_range_pct",
    "underlying_intraday_range_pct",
    "raw_close_jump_ratio",
    "black_swan_event_day",
    "extreme_intraday_range_day",
    "corporate_action_candidate",
    "event_day",
    "training_exclusion_candidate",
]

_ABNORMAL_MINUTE_COLUMNS = [
    "timestamp",
    "trade_date",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "return_1m_pct",
    "underlying_return_1m_pct",
    "leverage",
    "direction",
    "abnormal_minute_bar",
]


def _audit_config(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("historical", {}).get("event_audit", {})


def _prepare_bars(bars: pd.DataFrame) -> pd.DataFrame:
    if bars.empty:
        return pd.DataFrame()
    frame = bars.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    frame["symbol"] = frame["symbol"].astype(str)
    for column in ("open", "high", "low", "close", "volume", "turnover"):
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "symbol", "open", "high", "low", "close"])
    return frame.sort_values(["symbol", "timestamp"]).reset_index(drop=True)


def _symbol_specs(config: dict[str, Any]) -> dict[str, tuple[int, float]]:
    specs: dict[str, tuple[int, float]] = {}
    try:
        instruments = SymbolMapper(config).enabled_instruments(include_disabled=True)
    except Exception:
        instruments = []
    for instrument in instruments:
        specs[str(instrument.symbol)] = (int(instrument.direction or 1), abs(float(instrument.leverage or 1.0)))
    return specs


def _daily_flags(frame: pd.DataFrame, audit_config: dict[str, Any]) -> pd.DataFrame:
    grouped = frame.groupby(["symbol", "trade_date"], sort=True)
    daily = grouped.agg(
        rows=("timestamp", "size"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        direction=("direction", "first"),
        leverage=("leverage", "first"),
    ).reset_index()
    daily["prev_close"] = daily.groupby("symbol")["close"].shift(1)
    daily["close_to_close_return_pct"] = (daily["close"] / daily["prev_close"] - 1.0) * 100.0
    daily["open_gap_pct"] = (daily["open"] / daily["prev_close"] - 1.0) * 100.0
    daily["open_close_return_pct"] = (daily["close"] / daily["open"] - 1.0) * 100.0
    daily["intraday_range_pct"] = (daily["high"] / daily["low"].replace(0, np.nan) - 1.0) * 100.0
    daily["underlying_close_to_close_return_pct"] = daily["close_to_close_return_pct"] * daily["direction"] / daily["leverage"]
    daily["underlying_open_gap_pct"] = daily["open_gap_pct"] * daily["direction"] / daily["leverage"]
    daily["underlying_intraday_range_pct"] = daily["intraday_range_pct"] / daily["leverage"]
    daily["raw_close_jump_ratio"] = [
        _jump_ratio(close, prev_close)
        for close, prev_close in zip(daily["close"], daily["prev_close"])
    ]

    black_swan_threshold = float(audit_config.get("black_swan_abs_underlying_return_pct", 7.5))
    range_threshold = float(audit_config.get("extreme_intraday_underlying_range_pct", 7.5))
    corporate_ratio_threshold = float(audit_config.get("corporate_action_raw_close_jump_ratio", 5.0))
    corporate_return_threshold = float(audit_config.get("corporate_action_abs_close_return_pct", 50.0))
    daily["corporate_action_candidate"] = (
        (daily["raw_close_jump_ratio"] >= corporate_ratio_threshold)
        | (daily["close_to_close_return_pct"].abs() >= corporate_return_threshold)
    ).fillna(False)
    daily["black_swan_event_day"] = (daily["underlying_close_to_close_return_pct"].abs() >= black_swan_threshold).fillna(False)
    daily["extreme_intraday_range_day"] = (daily["underlying_intraday_range_pct"] >= range_threshold).fillna(False)
    daily["event_day"] = (
        ~daily["corporate_action_candidate"]
        & (daily["black_swan_event_day"] | daily["extreme_intraday_range_day"])
    )
    daily["training_exclusion_candidate"] = daily["corporate_action_candidate"]
    return daily.reindex(columns=_DAILY_COLUMNS).reset_index(drop=True)


def _jump_ratio(close: Any, prev_close: Any) -> float:
    try:
        close_value = float(close)
        prev_value = float(prev_close)
    except (TypeError, ValueError):
        return float("nan")
    if not np.isfinite(close_value) or not np.isfinite(prev_value) or close_value <= 0 or prev_value <= 0:
        return float("nan")
    return max(close_value, prev_value) / min(close_value, prev_value)


def _summary(
    frame: pd.DataFrame,
    daily_flags: pd.DataFrame,
    abnormal_minutes: pd.DataFrame,
    audit_config: dict[str, Any],
) -> dict[str, Any]:
    event_dates = market_event_dates(daily_flags)
    corporate_dates = corporate_action_candidate_dates(daily_flags)
    return {
        "rows": int(len(frame)),
        "symbols": sorted(frame["symbol"].astype(str).unique()) if not frame.empty else [],
        "start": str(frame["timestamp"].min()) if not frame.empty else "",
        "end": str(frame["timestamp"].max()) if not frame.empty else "",
        "thresholds": {
            "black_swan_abs_underlying_return_pct": float(audit_config.get("black_swan_abs_underlying_return_pct", 7.5)),
            "extreme_intraday_underlying_range_pct": float(audit_config.get("extreme_intraday_underlying_range_pct", 7.5)),
            "abnormal_minute_abs_underlying_return_pct": float(audit_config.get("abnormal_minute_abs_underlying_return_pct", 1.0)),
            "corporate_action_raw_close_jump_ratio": float(audit_config.get("corporate_action_raw_close_jump_ratio", 5.0)),
            "corporate_action_abs_close_return_pct": float(audit_config.get("corporate_action_abs_close_return_pct", 50.0)),
        },
        "daily_rows": int(len(daily_flags)),
        "event_day_count": int(daily_flags["event_day"].sum()) if "event_day" in daily_flags else 0,
        "event_dates": event_dates,
        "corporate_action_candidate_count": int(daily_flags["corporate_action_candidate"].sum()) if "corporate_action_candidate" in daily_flags else 0,
        "corporate_action_candidate_dates": corporate_dates,
        "abnormal_minute_bar_count": int(len(abnormal_minutes)),
        "training_policy": (
            "Main training keeps real market event days. Corporate action candidates are flagged as data-basis "
            "review items and are excluded only in explicit sensitivity scenarios."
        ),
        "top_daily_moves": _records(
            daily_flags.sort_values(
                "underlying_close_to_close_return_pct",
                key=lambda series: series.abs(),
                ascending=False,
            ).head(10)
        ),
        "top_abnormal_minutes": _records(
            abnormal_minutes.sort_values(
                "underlying_return_1m_pct",
                key=lambda series: series.abs(),
                ascending=False,
            ).head(10)
        ),
    }


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    records = frame.replace({np.nan: None}).to_dict(orient="records")
    return [{str(key): value for key, value in row.items()} for row in records]


def _render_event_audit_markdown(
    summary: dict[str, Any],
    daily_flags: pd.DataFrame,
    abnormal_minutes: pd.DataFrame,
    max_rows: int,
) -> str:
    lines = [
        "# Event And Outlier Audit",
        "",
        "Main training keeps real market event days. This audit separates market events from data-basis anomalies.",
        "",
        "## Summary",
        "",
        f"- Rows: {summary.get('rows', 0)}",
        f"- Symbols: {', '.join(summary.get('symbols', []))}",
        f"- Start: {summary.get('start', '')}",
        f"- End: {summary.get('end', '')}",
        f"- Market event dates: {', '.join(summary.get('event_dates', [])) or 'none'}",
        f"- Corporate action candidate dates: {', '.join(summary.get('corporate_action_candidate_dates', [])) or 'none'}",
        f"- Abnormal minute bars: {summary.get('abnormal_minute_bar_count', 0)}",
        "",
        "## Files",
        "",
        "- daily_event_flags.csv",
        "- abnormal_minute_bars.csv",
        "- event_audit_summary.json",
        "",
        "## Top Daily Moves",
        "",
        _markdown_table(
            daily_flags.sort_values("underlying_close_to_close_return_pct", key=lambda series: series.abs(), ascending=False).head(max_rows),
            [
                "trade_date",
                "symbol",
                "close_to_close_return_pct",
                "underlying_close_to_close_return_pct",
                "intraday_range_pct",
                "raw_close_jump_ratio",
                "event_day",
                "corporate_action_candidate",
            ],
        ),
        "",
        "## Top Abnormal Minute Bars",
        "",
        _markdown_table(
            abnormal_minutes.head(max_rows),
            ["timestamp", "symbol", "return_1m_pct", "underlying_return_1m_pct", "open", "high", "low", "close"],
        ),
    ]
    return "\n".join(lines).rstrip() + "\n"


def _markdown_table(frame: pd.DataFrame, columns: list[str]) -> str:
    if frame.empty:
        return "No rows."
    available = [column for column in columns if column in frame]
    rows = frame.reindex(columns=available).replace({np.nan: ""}).head(50).to_dict(orient="records")
    header = "| " + " | ".join(available) + " |"
    separator = "| " + " | ".join("---" for _ in available) + " |"
    body = [
        "| " + " | ".join(_format_cell(row.get(column, "")) for column in available) + " |"
        for row in rows
    ]
    return "\n".join([header, separator, *body])


def _format_cell(value: Any) -> str:
    if isinstance(value, float):
        if not np.isfinite(value):
            return ""
        return f"{value:.4f}"
    return str(value)


def _empty_daily_flags() -> pd.DataFrame:
    return pd.DataFrame(columns=_DAILY_COLUMNS)


def _empty_abnormal_minutes() -> pd.DataFrame:
    return pd.DataFrame(columns=_ABNORMAL_MINUTE_COLUMNS)
