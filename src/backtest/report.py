from __future__ import annotations

import math
from pathlib import Path
from numbers import Integral, Real
from typing import Any
import html

import pandas as pd

from src.utils.paths import ensure_dir
from src.utils.serialization import read_json


MONTHLY_RETURN_COLUMN_LABELS = {
    "year_month": "年月",
    "year": "年份",
    "month": "月份",
    "start_equity_jpy": "月初权益(JPY)",
    "end_equity_jpy": "月末权益(JPY)",
    "pnl_jpy": "月度盈亏(JPY)",
    "return_pct": "月度收益率(%)",
}

TRADE_LOG_COLUMN_LABELS = {
    "timestamp": "时间",
    "action": "操作",
    "symbol": "标的",
    "reference_price": "参考价",
    "price": "价格",
    "quantity": "数量",
    "notional_jpy": "成交金额(JPY)",
    "commission_jpy": "手续费(JPY)",
    "slippage_bps": "滑点(bps)",
    "spread_bps": "点差(bps)",
    "market_impact_bps": "市场冲击(bps)",
    "execution_cost_bps": "执行成本(bps)",
    "bar_turnover_jpy": "分钟成交额(JPY)",
    "reason": "操作理由",
    "exit_reason": "平仓理由",
    "pnl_jpy": "盈亏(JPY)",
    "pnl_pct": "收益率(%)",
    "position_action": "持仓动作",
    "trade_date": "交易日",
    "year": "年份",
    "month": "年月",
    "session": "交易时段",
    "market_regime": "市场状态",
    "confidence": "模型置信度",
    "action_probability": "动作概率",
    "sizing_multiplier": "仓位倍率",
    "base_equity_pct": "基准仓位(%)",
    "target_equity_pct": "目标仓位(%)",
    "absolute_max_equity_pct": "硬上限仓位(%)",
    "max_holding_minutes": "最长持仓(分钟)",
    "stop_loss_pct": "止损比例(%)",
}

TRADE_LOG_COLUMNS = [
    "timestamp",
    "action",
    "symbol",
    "reference_price",
    "price",
    "quantity",
    "notional_jpy",
    "commission_jpy",
    "order_id",
    "order_type",
    "submitted_price",
    "filled_price",
    "bid",
    "ask",
    "mid",
    "slippage_bps",
    "spread_bps",
    "bid_depth",
    "ask_depth",
    "quote_time",
    "last_trade_time",
    "market_impact_bps",
    "execution_cost_bps",
    "bar_turnover_jpy",
    "expected_return_bps",
    "expected_cost_bps",
    "net_edge_bps",
    "recommended_position_size",
    "reason_codes",
    "risk_filter_results",
    "implied_nikkei_1321_bps",
    "implied_nikkei_1570_bps",
    "implied_nikkei_1571_bps",
    "implied_nikkei_1357_bps",
    "implied_nikkei_dispersion_bps",
    "futures_return_1m",
    "index_return_1m",
    "etf_vs_inav_premium_bps",
    "reason",
    "exit_reason",
    "pnl_jpy",
    "pnl_pct",
    "position_action",
    "trade_date",
    "year",
    "month",
    "session",
    "market_regime",
    "confidence",
    "action_probability",
    "sizing_multiplier",
    "base_equity_pct",
    "target_equity_pct",
    "absolute_max_equity_pct",
    "max_holding_minutes",
    "stop_loss_pct",
]

SIGNED_COLUMNS = {"pnl_jpy", "pnl_pct", "return_pct"}


def output_dir(config: dict[str, Any]) -> Path:
    return ensure_dir(config.get("backtest", {}).get("report", {}).get("output_dir", "data/reports/backtest"))


def write_backtest_report(
    config: dict[str, Any],
    metrics: dict[str, Any],
    trade_log: pd.DataFrame,
    equity_curve: pd.DataFrame,
    signals: pd.DataFrame,
) -> Path:
    out = output_dir(config)
    trade_log = _with_columns(trade_log, TRADE_LOG_COLUMNS)
    trade_log.to_csv(out / "trade_log.csv", index=False)
    equity_curve.to_csv(out / "equity_curve.csv", index=False)
    signals.to_csv(out / "signal_log.csv", index=False)
    monthly_returns = pd.DataFrame(metrics.get("monthly_returns", []))
    monthly_returns.to_csv(out / "monthly_returns.csv", index=False)
    write_filterable_table_html(
        monthly_returns,
        out / "monthly_returns.html",
        "月度收益率记录",
        column_labels=MONTHLY_RETURN_COLUMN_LABELS,
        description=(
            "字段关系：月度盈亏 = 月末权益 - 月初权益；"
            "月度收益率 = 月度盈亏 / 月初权益 × 100%。"
            "月度收益率逐月记录，可以为正数、负数或 0。"
        ),
    )
    write_filterable_table_html(
        trade_log,
        out / "trade_log.html",
        "模拟交易记录",
        column_labels=TRADE_LOG_COLUMN_LABELS,
        description="每行是一笔模拟买入或卖出记录，包含价格、数量、操作理由、平仓理由以及已知盈亏。",
    )
    lines = [
        "# Backtest Report",
        "",
        "Mode: historical_backtest / paper simulation only.",
        "",
        "Historical backtest profit is evidence to investigate, not a guarantee of future results.",
        "",
        "## Metrics",
        "",
        f"- Total return pct: {metrics['total_return_pct']:.4f}",
        f"- Average monthly return pct: {metrics.get('average_monthly_return_pct', 0.0):.4f}",
        f"- Minimum monthly return pct: {metrics.get('min_monthly_return_pct', 0.0):.4f}",
        f"- Positive month ratio: {metrics.get('positive_month_ratio', 0.0):.4f}",
        f"- Positive active month ratio: {metrics.get('positive_active_month_ratio', 0.0):.4f}",
        f"- Total trades: {metrics['total_trades']}",
        f"- Final equity JPY: {metrics['final_equity_jpy']:.2f}",
        f"- Max drawdown pct: {metrics['max_drawdown_pct']:.4f}",
        f"- Win rate pct: {metrics['win_rate_pct']:.2f}",
        f"- Profit factor: {metrics['profit_factor']}",
        f"- Total commission JPY: {metrics.get('total_commission_jpy', 0.0):.2f}",
        f"- Average execution cost bps: {metrics.get('average_execution_cost_bps', 0.0):.2f}",
        "",
        "## Files",
        "",
        "- metrics.json",
        "- equity_curve.csv",
        "- signal_log.csv",
        "- trade_log.csv",
        "- trade_log.html",
        "- monthly_returns.csv",
        "- monthly_returns.html",
    ]
    report_path = out / "report.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def _with_columns(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=columns)
    extra_columns = [column for column in frame.columns if column not in columns]
    return frame.reindex(columns=[*columns, *extra_columns])


def write_filterable_table_html(
    frame: pd.DataFrame,
    path: Path,
    title: str,
    column_labels: dict[str, str] | None = None,
    description: str | None = None,
    signed_columns: set[str] | None = None,
) -> Path:
    columns = list(frame.columns)
    rows = frame.to_dict(orient="records") if not frame.empty else []
    labels = column_labels or {}
    signed = SIGNED_COLUMNS if signed_columns is None else signed_columns
    header = "".join(f"<th>{html.escape(labels.get(str(column), str(column)))}</th>" for column in columns)
    body_rows: list[str] = []
    for row in rows:
        cells = "".join(
            _render_cell(row.get(column, ""), str(column) in signed)
            for column in columns
        )
        body_rows.append(f"<tr>{cells}</tr>")
    body = "\n".join(body_rows)
    description_html = f'  <div class="description">{html.escape(description)}</div>\n' if description else ""
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; color: #111; }}
    h1 {{ font-size: 20px; margin-bottom: 12px; }}
    .description {{ color: #333; line-height: 1.6; margin-bottom: 12px; max-width: 980px; }}
    input {{ width: min(520px, 100%); padding: 8px 10px; margin-bottom: 12px; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border: 1px solid #ddd; padding: 6px 8px; text-align: left; }}
    th {{ background: #f3f4f6; cursor: pointer; position: sticky; top: 0; }}
    tr:nth-child(even) {{ background: #fafafa; }}
    .meta {{ color: #555; margin-bottom: 12px; }}
    .number {{ font-variant-numeric: tabular-nums; text-align: right; }}
    .positive {{ color: #047857; }}
    .negative {{ color: #b91c1c; }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
{description_html}  <div class="meta">记录数：<span id="visibleCount">{len(rows)}</span> / {len(rows)}。输入关键字筛选；点击表头排序。</div>
  <input id="filterInput" type="search" placeholder="输入关键字筛选...">
  <table id="dataTable">
    <thead><tr>{header}</tr></thead>
    <tbody>{body}</tbody>
  </table>
  <script>
    const input = document.getElementById('filterInput');
    const table = document.getElementById('dataTable');
    const visibleCount = document.getElementById('visibleCount');
    input.addEventListener('input', () => {{
      const query = input.value.toLowerCase();
      let count = 0;
      for (const row of table.tBodies[0].rows) {{
        const show = row.innerText.toLowerCase().includes(query);
        row.style.display = show ? '' : 'none';
        if (show) count++;
      }}
      visibleCount.textContent = String(count);
    }});
    for (const [index, th] of Array.from(table.tHead.rows[0].cells).entries()) {{
      th.dataset.direction = 'asc';
      th.addEventListener('click', () => {{
        const direction = th.dataset.direction === 'asc' ? 1 : -1;
        const rows = Array.from(table.tBodies[0].rows);
        rows.sort((a, b) => {{
          const av = a.cells[index].innerText.trim();
          const bv = b.cells[index].innerText.trim();
          const an = Number(av.replace(/[,%]/g, ''));
          const bn = Number(bv.replace(/[,%]/g, ''));
          if (!Number.isNaN(an) && !Number.isNaN(bn)) return (an - bn) * direction;
          return av.localeCompare(bv) * direction;
        }});
        th.dataset.direction = th.dataset.direction === 'asc' ? 'desc' : 'asc';
        rows.forEach(row => table.tBodies[0].appendChild(row));
      }});
    }}
  </script>
</body>
</html>
"""
    path.write_text(document, encoding="utf-8")
    return path


def _render_cell(value: Any, signed: bool) -> str:
    text, number = _format_cell(value, signed)
    classes = []
    if number is not None:
        classes.append("number")
        if number > 0 and signed:
            classes.append("positive")
        elif number < 0:
            classes.append("negative")
    class_attr = f' class="{" ".join(classes)}"' if classes else ""
    return f"<td{class_attr}>{html.escape(text)}</td>"


def _format_cell(value: Any, signed: bool = False) -> tuple[str, float | None]:
    if value is None:
        return "", None
    try:
        if pd.isna(value):
            return "", None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.isoformat(sep=" "), None
    if isinstance(value, bool):
        return str(value), None
    if isinstance(value, Integral) and not signed:
        number = float(value)
        return str(int(value)), number
    if isinstance(value, Real):
        number = float(value)
        if not math.isfinite(number):
            return str(value), None
        prefix = "+" if signed and number > 0 else ""
        return f"{prefix}{number:.2f}", number
    return str(value), None


def render_existing_report(config: dict[str, Any], report_type: str = "backtest") -> Path:
    if report_type != "backtest":
        raise ValueError("Only backtest reports are implemented in pass 1")
    out = output_dir(config)
    metrics = read_json(out / "metrics.json")
    trade_log = _read_csv_or_empty(out / "trade_log.csv")
    equity_curve = _read_csv_or_empty(out / "equity_curve.csv")
    signals = _read_csv_or_empty(out / "signal_log.csv")
    return write_backtest_report(config, metrics, trade_log, equity_curve, signals)


def _read_csv_or_empty(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()
