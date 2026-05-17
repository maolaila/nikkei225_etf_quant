from __future__ import annotations

import csv
import html
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


METRIC_KEYS = [
    "total_return_pct",
    "average_monthly_return_pct",
    "median_monthly_return_pct",
    "min_monthly_return_pct",
    "max_monthly_return_pct",
    "total_trades",
    "profit_factor",
    "max_drawdown_pct",
    "positive_active_month_ratio",
    "walk_forward_windows",
    "walk_forward_fallback_used",
    "data_is_synthetic",
    "total_abs_return_pct",
    "positive_month_ratio",
    "negative_month_ratio",
    "flat_month_ratio",
    "dominant_month_ratio",
    "stable_band_direction",
    "direction_month_ratio",
]

HEADER_TOOLTIPS = {
    "cycle": "训练轮次编号。",
    "time": "该轮训练或回归检查完成的时间。",
    "success": "该轮是否达到当前设定的通过条件。",
    "target_total_return_pct": "该轮要求达到的目标总收益率，单位为百分比。",
    "total_return_pct": "回测期间从初始资金到最终资金的总收益率，单位为百分比。",
    "average_monthly_return_pct": "按月收益率计算的平均值，单位为百分比。",
    "median_monthly_return_pct": "按月收益率的中位数，单位为百分比。",
    "min_monthly_return_pct": "表现最差月份的收益率，单位为百分比。",
    "max_monthly_return_pct": "表现最好月份的收益率，单位为百分比。",
    "total_trades": "该回测或候选策略完成的卖出成交次数。",
    "profit_factor": "总盈利除以总亏损绝对值，用于衡量盈亏质量。",
    "max_drawdown_pct": "回测期间最大资金回撤，单位为百分比。",
    "positive_active_month_ratio": "有交易月份中收益为正的月份占比。",
    "walk_forward_windows": "参与验证的 walk-forward 时间窗口数量。",
    "walk_forward_fallback_used": "是否退化使用了简单时间切分，而不是完整 walk-forward 窗口。",
    "data_is_synthetic": "该结果是否包含合成数据；盈利目标只接受真实非合成数据。",
    "year_month": "收益所属年月。",
    "year": "收益所属年份。",
    "month": "收益所属月份或年月。",
    "start_equity_jpy": "该月开始时账户权益，单位为日元。",
    "end_equity_jpy": "该月结束时账户权益，单位为日元。",
    "pnl_jpy": "该月盈亏金额，单位为日元。",
    "return_pct": "该月收益率，单位为百分比。",
    "rank": "候选策略在本轮批量搜索中的排名。",
    "candidate_id": "候选策略编号，对应 batch-search 输出目录。",
    "score": "候选策略综合评分。",
    "passes_target": "候选策略是否达到当前全部目标门槛。",
}

MONEY_KEYS = {
    "start_equity_jpy",
    "end_equity_jpy",
    "pnl_jpy",
}

PERCENT_KEYS = {
    "target_total_return_pct",
    "total_return_pct",
    "average_monthly_return_pct",
    "median_monthly_return_pct",
    "min_monthly_return_pct",
    "max_monthly_return_pct",
    "total_abs_return_pct",
    "max_drawdown_pct",
    "return_pct",
}

ARCHIVED_BACKTEST_FILES = [
    "metrics.json",
    "monthly_returns.csv",
    "monthly_returns.html",
    "report.md",
    "trade_log.csv",
]


def generate_training_cycle_report(
    *,
    state_path: str | Path = ".codex_quant_agent/state/state.json",
    reports_root: str | Path = "data/reports",
    output_path: str | Path | None = None,
    archive_current: bool = False,
    objective_filter: str | None = None,
) -> Path:
    """Write a static HTML dashboard for supervisor regression cycles."""

    state_path = Path(state_path)
    reports_root = Path(reports_root)
    output = Path(output_path) if output_path else reports_root / "backtest" / "training_cycles.html"

    state = _read_json(state_path)
    if archive_current:
        archive_current_cycle(state=state, reports_root=reports_root)

    cycles = _build_cycle_records(state, reports_root)
    batch_runs = _load_batch_runs(reports_root)
    _attach_batch_runs(cycles, batch_runs, state)
    if objective_filter:
        cycles = _filter_cycles_by_batch_objective(cycles, objective_filter)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_render_dashboard(cycles, state_path, reports_root), encoding="utf-8")
    return output


def _filter_cycles_by_batch_objective(cycles: list[dict[str, Any]], objective: str) -> list[dict[str, Any]]:
    wanted = str(objective).strip().lower()
    filtered: list[dict[str, Any]] = []
    for cycle in cycles:
        runs = [
            run
            for run in cycle.get("batch_runs", []) or []
            if str((run.get("summary") or {}).get("objective", "")).strip().lower() == wanted
        ]
        if runs:
            clone = dict(cycle)
            clone["batch_runs"] = runs
            filtered.append(clone)
    return filtered


def archive_current_cycle(*, state: dict[str, Any], reports_root: str | Path = "data/reports") -> Path | None:
    reports_root = Path(reports_root)
    source = reports_root / "backtest"
    metrics_path = source / "metrics.json"
    monthly_path = source / "monthly_returns.csv"
    if not metrics_path.exists() and not monthly_path.exists():
        return None

    cycle = _archive_cycle_from_state(state, metrics_path)
    if cycle <= 0:
        return None

    destination = reports_root / "regression_cycles" / f"cycle_{cycle:03d}" / "main_backtest"
    destination.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in ARCHIVED_BACKTEST_FILES:
        source_file = source / name
        if source_file.exists() and source_file.is_file():
            shutil.copy2(source_file, destination / name)
            copied.append(name)

    metadata = {
        "cycle": cycle,
        "archived_at": datetime.now().isoformat(timespec="seconds"),
        "source_dir": _display_path(source),
        "copied_files": copied,
        "state_last_metrics": state.get("last_metrics"),
    }
    (destination / "cycle_metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return destination


def _archive_cycle_from_state(state: dict[str, Any], metrics_path: Path) -> int:
    regression_cycle = _to_int(state.get("regression_cycles"))
    current_cycle = _to_int(state.get("cycle"))
    if current_cycle > regression_cycle and _metrics_newer_than_last_recorded(metrics_path, state.get("last_metrics")):
        return current_cycle
    return regression_cycle or current_cycle


def _metrics_newer_than_last_recorded(metrics_path: Path, last_metrics: Any) -> bool:
    if not isinstance(last_metrics, dict):
        return False
    last_write_time = _parse_datetime(last_metrics.get("last_write_time"))
    if last_write_time is None:
        return False
    try:
        source_write_time = datetime.fromtimestamp(metrics_path.stat().st_mtime)
    except OSError:
        return False
    return source_write_time > last_write_time


def _build_cycle_records(state: dict[str, Any], reports_root: Path) -> list[dict[str, Any]]:
    cycles: list[dict[str, Any]] = []
    for item in state.get("history", []) or []:
        if item.get("event") != "regression-result":
            continue
        data = item.get("data") or {}
        metrics = _stable_metrics(data.get("metrics") or {})
        cycle = _to_int(data.get("regression_cycles")) or len(cycles) + 1
        cycles.append(
            {
                "cycle": cycle,
                "time": item.get("time", ""),
                "success": bool(data.get("success", False)),
                "training_objective": data.get("training_objective", ""),
                "target_total_return_pct": data.get("current_target_total_return_pct"),
                "target_total_abs_return_pct": data.get("target_total_abs_return_pct"),
                "max_total_abs_return_pct": data.get("max_total_abs_return_pct"),
                "min_stable_month_ratio": data.get("min_stable_month_ratio"),
                "stable_band_direction": data.get("stable_band_direction", ""),
                "stable_band_streak_direction": data.get("stable_band_streak_direction", ""),
                "direction_month_ratio": data.get("direction_month_ratio"),
                "metrics": {key: metrics.get(key) for key in METRIC_KEYS},
                "metrics_file": metrics.get("file"),
                "main_backtest": _load_main_archive(reports_root, cycle),
                "batch_runs": [],
            }
        )

    if not cycles and state.get("last_metrics"):
        cycle = _to_int(state.get("regression_cycles")) or _to_int(state.get("cycle")) or 1
        metrics = _stable_metrics(state.get("last_metrics") or {})
        target = state.get("target") or {}
        cycles.append(
            {
                "cycle": cycle,
                "time": state.get("updated_at", ""),
                "success": bool(state.get("success", False)),
                "training_objective": target.get("training_objective", ""),
                "target_total_return_pct": target.get("current_total_return_pct_gt"),
                "target_total_abs_return_pct": target.get("target_total_abs_return_pct"),
                "max_total_abs_return_pct": target.get("max_total_abs_return_pct"),
                "min_stable_month_ratio": target.get("min_stable_month_ratio"),
                "stable_band_direction": target.get("stable_band_direction", ""),
                "stable_band_streak_direction": target.get("stable_band_direction", ""),
                "direction_month_ratio": None,
                "metrics": {key: metrics.get(key) for key in METRIC_KEYS},
                "metrics_file": metrics.get("file"),
                "main_backtest": _load_main_archive(reports_root, cycle),
                "batch_runs": [],
            }
        )

    cycles.sort(key=lambda row: row["cycle"])
    return cycles


def _stable_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    clone = dict(metrics)
    total_return = _to_float(clone.get("total_return_pct"))
    if total_return is not None:
        clone["total_abs_return_pct"] = abs(total_return)

    returns = [
        value
        for value in (_to_float((row or {}).get("return_pct")) for row in clone.get("monthly_returns", []) or [] if isinstance(row, dict))
        if value is not None
    ]
    if not returns:
        returns_by_month = clone.get("returns_by_month_pct") or {}
        if isinstance(returns_by_month, dict):
            returns = [value for value in (_to_float(value) for value in returns_by_month.values()) if value is not None]

    if returns:
        count = len(returns)
        positive = sum(1 for value in returns if value > 0)
        negative = sum(1 for value in returns if value < 0)
        flat = sum(1 for value in returns if value == 0)
        positive_ratio = positive / count
        negative_ratio = negative / count
        direction = "positive" if (total_return or 0.0) > 0 else ("negative" if (total_return or 0.0) < 0 else "")
        clone["positive_month_ratio"] = positive_ratio
        clone["negative_month_ratio"] = negative_ratio
        clone["flat_month_ratio"] = flat / count
        clone["dominant_month_ratio"] = max(positive_ratio, negative_ratio)
        clone["stable_band_direction"] = direction
        clone["direction_month_ratio"] = positive_ratio if direction == "positive" else (negative_ratio if direction == "negative" else 0.0)
    return clone


def _load_main_archive(reports_root: Path, cycle: int) -> dict[str, Any]:
    archive = reports_root / "regression_cycles" / f"cycle_{cycle:03d}" / "main_backtest"
    monthly = _read_csv_records(archive / "monthly_returns.csv")
    metrics = _read_json(archive / "metrics.json")
    return {
        "path": _display_path(archive) if archive.exists() else "",
        "monthly_returns": monthly,
        "metrics": metrics,
        "available": bool(monthly or metrics),
    }


def _load_batch_runs(reports_root: Path) -> list[dict[str, Any]]:
    experiments = reports_root / "experiments"
    if not experiments.exists():
        return []

    runs: list[dict[str, Any]] = []
    for batch_dir in sorted(experiments.glob("batch_search_*")):
        if not batch_dir.is_dir():
            continue
        batch_time = _batch_time(batch_dir)
        ranking = _read_csv_records(batch_dir / "ranking.csv")
        summary = _read_json(batch_dir / "summary.json")
        best = ranking[0] if ranking else {}
        best_id = str(best.get("candidate_id", "")).strip()
        best_dir = batch_dir / best_id if best_id else None
        runs.append(
            {
                "name": batch_dir.name,
                "path": _display_path(batch_dir),
                "time": batch_time.isoformat(sep=" ") if batch_time else "",
                "_time": batch_time,
                "summary": summary,
                "top_candidates": ranking[:10],
                "best_candidate": best,
                "best_monthly_returns": _read_csv_records(best_dir / "monthly_returns.csv") if best_dir else [],
                "best_metrics": _read_json(best_dir / "metrics.json") if best_dir else {},
                "inferred_phase": "unassigned",
            }
        )
    return runs


def _attach_batch_runs(cycles: list[dict[str, Any]], batch_runs: list[dict[str, Any]], state: dict[str, Any]) -> None:
    if not cycles or not batch_runs:
        return

    role_events = _role_events(state)
    by_cycle = {int(cycle["cycle"]): cycle for cycle in cycles}
    assigned: set[str] = set()

    for event in role_events:
        candidates = [
            run
            for run in batch_runs
            if run["name"] not in assigned
            and run.get("_time")
            and event.get("time")
            and 0 <= (event["time"] - run["_time"]).total_seconds() <= 60 * 60
        ]
        if not candidates:
            continue
        run = min(candidates, key=lambda item: abs((event["time"] - item["_time"]).total_seconds()))
        run["inferred_phase"] = event["phase"]
        by_cycle.get(event["cycle"], cycles[-1])["batch_runs"].append(run)
        assigned.add(run["name"])

    if len(assigned) == len(batch_runs):
        return

    cycle_times = [
        (_to_int(cycle["cycle"]), _parse_datetime(cycle.get("time")))
        for cycle in cycles
        if _parse_datetime(cycle.get("time")) is not None
    ]
    for run in batch_runs:
        if run["name"] in assigned:
            continue
        run_time = run.get("_time")
        target_cycle = cycles[-1]["cycle"]
        if run_time and cycle_times:
            before = [(cycle, time) for cycle, time in cycle_times if time and time <= run_time]
            if before:
                target_cycle = before[-1][0]
            else:
                after = [(cycle, time) for cycle, time in cycle_times if time and time > run_time]
                if after:
                    target_cycle = after[0][0]
                    run["inferred_phase"] = "runner"
        by_cycle.get(int(target_cycle), cycles[-1])["batch_runs"].append(run)


def _role_events(state: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in state.get("history", []) or []:
        if item.get("event") not in {"backtest-runner", "strategy-optimizer", "regression-audit"}:
            continue
        data = item.get("data") or {}
        role = str(data.get("role", ""))
        cycle = _cycle_from_role(role)
        when = _parse_datetime(item.get("time"))
        if cycle <= 0 or when is None:
            continue
        if role.startswith("backtest-runner"):
            phase = "runner"
        elif role.startswith("strategy-optimizer"):
            phase = "optimizer"
        else:
            phase = "audit"
        events.append({"cycle": cycle, "time": when, "phase": phase})
    events.sort(key=lambda event: event["time"])
    return events


def _render_dashboard(cycles: list[dict[str, Any]], state_path: Path, reports_root: Path) -> str:
    payload = json.dumps(_json_ready(cycles), ensure_ascii=False, allow_nan=False).replace("</", "<\\/")
    header_tooltips = json.dumps(HEADER_TOOLTIPS, ensure_ascii=False, allow_nan=False).replace("</", "<\\/")
    money_keys = json.dumps(sorted(MONEY_KEYS), ensure_ascii=False).replace("</", "<\\/")
    percent_keys = json.dumps(sorted(PERCENT_KEYS), ensure_ascii=False).replace("</", "<\\/")
    options_count = len(cycles)
    note = (
        "Cycle metrics are read from the Codex supervisor state. Main backtest monthly returns "
        "are available only for cycles archived after this feature was enabled. Older cycle-level "
        "main monthly returns may have been overwritten, but batch-search candidate monthly returns "
        "remain available under data/reports/experiments when those runs exist."
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Training Cycle Results</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; color: #111; }}
    h1 {{ font-size: 22px; margin: 0 0 12px; }}
    h2 {{ font-size: 17px; margin: 24px 0 10px; }}
    .description {{ color: #333; line-height: 1.55; max-width: 1120px; margin-bottom: 14px; }}
    .controls {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin: 12px 0 18px; }}
    label {{ font-weight: 600; }}
    select, input {{ padding: 8px 10px; min-width: 260px; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; margin-bottom: 14px; }}
    th, td {{ border: 1px solid #ddd; padding: 6px 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f3f4f6; position: sticky; top: 0; }}
    tr:nth-child(even) {{ background: #fafafa; }}
    .number {{ font-variant-numeric: tabular-nums; text-align: right; }}
    .positive {{ color: #047857; }}
    .negative {{ color: #b91c1c; }}
    .muted {{ color: #666; }}
    .pill {{ display: inline-block; padding: 2px 8px; border: 1px solid #d1d5db; border-radius: 999px; background: #f9fafb; }}
    .section {{ margin-top: 14px; }}
  </style>
</head>
<body>
  <h1>Training Cycle Results</h1>
  <div class="description">{html.escape(note)}</div>
  <div class="muted">State: {html.escape(_display_path(state_path))} | Reports root: {html.escape(_display_path(reports_root))} | Cycles: {options_count}</div>
  <div class="controls">
    <label for="cycleSelect">Cycle</label>
    <select id="cycleSelect"></select>
    <input id="cycleFilter" type="search" placeholder="Filter all-cycle table...">
  </div>
  <div id="cycleSummary"></div>
  <div class="section">
    <h2>Main Backtest Monthly Returns</h2>
    <div id="mainMonthlyNote" class="muted"></div>
    <div id="mainMonthlyTable"></div>
  </div>
  <div class="section">
    <h2>Batch Search Runs</h2>
    <div id="batchRuns"></div>
  </div>
  <div class="section">
    <h2>All Cycles</h2>
    <div class="muted">Rows: <span id="visibleCount">0</span> / <span id="totalCount">0</span></div>
    <div id="allCyclesTable"></div>
  </div>
  <script>
    const cycles = {payload};
    const metricKeys = {json.dumps(METRIC_KEYS)};
    const headerTooltips = {header_tooltips};
    const moneyKeys = new Set({money_keys});
    const percentKeys = new Set({percent_keys});
    const signedKeys = new Set(['total_return_pct', 'average_monthly_return_pct', 'median_monthly_return_pct', 'min_monthly_return_pct', 'max_monthly_return_pct', 'max_drawdown_pct', 'return_pct', 'pnl_jpy']);

    function formatValue(value, key) {{
      if (value === null || value === undefined || value === '') return '';
      const numeric = typeof value === 'number' ? value : Number(String(value).replaceAll(',', ''));
      if (Number.isFinite(numeric) && (moneyKeys.has(key) || String(key).endsWith('_jpy'))) return numeric.toFixed(2);
      if (Number.isFinite(numeric) && (percentKeys.has(key) || String(key).endsWith('_pct'))) return numeric.toFixed(2);
      if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(2);
      return String(value);
    }}

    function td(value, key) {{
      const text = formatValue(value, key);
      const n = Number(text);
      let cls = Number.isFinite(n) ? 'number' : '';
      if (Number.isFinite(n) && signedKeys.has(key)) cls += n > 0 ? ' positive' : (n < 0 ? ' negative' : '');
      return `<td class="${{cls.trim()}}">${{escapeHtml(text)}}</td>`;
    }}

    function renderTable(rows, columns) {{
      if (!rows || rows.length === 0) return '<div class="muted">No rows available.</div>';
      const head = columns.map(column => {{
        const label = column.label || column.key;
        const title = column.title || headerTooltips[column.key] || label;
        return `<th title="${{escapeHtml(title)}}">${{escapeHtml(label)}}</th>`;
      }}).join('');
      const body = rows.map(row => `<tr>${{columns.map(column => td(row[column.key], column.key)).join('')}}</tr>`).join('');
      return `<table><thead><tr>${{head}}</tr></thead><tbody>${{body}}</tbody></table>`;
    }}

    function escapeHtml(value) {{
      return String(value)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
    }}

    function cycleMetricRow(cycle) {{
      const row = {{
        cycle: cycle.cycle,
        time: cycle.time,
        success: cycle.success,
        training_objective: cycle.training_objective,
        target_total_return_pct: cycle.target_total_return_pct,
        target_total_abs_return_pct: cycle.target_total_abs_return_pct,
        max_total_abs_return_pct: cycle.max_total_abs_return_pct,
        min_stable_month_ratio: cycle.min_stable_month_ratio,
        stable_band_direction: cycle.stable_band_direction,
        stable_band_streak_direction: cycle.stable_band_streak_direction,
        direction_month_ratio: cycle.direction_month_ratio,
      }};
      for (const key of metricKeys) row[key] = cycle.metrics ? cycle.metrics[key] : '';
      return row;
    }}

    function renderSelectedCycle() {{
      const selected = Number(document.getElementById('cycleSelect').value);
      const cycle = cycles.find(item => Number(item.cycle) === selected) || cycles[cycles.length - 1];
      if (!cycle) return;
      const summaryColumns = [
        {{key: 'cycle', label: 'Cycle'}},
        {{key: 'time', label: 'Completed At'}},
        {{key: 'success', label: 'Success'}},
        {{key: 'training_objective', label: 'Objective'}},
        {{key: 'stable_band_direction', label: 'Direction'}},
        {{key: 'target_total_abs_return_pct', label: 'Target Abs %'}},
        {{key: 'max_total_abs_return_pct', label: 'Max Abs %'}},
        {{key: 'total_return_pct', label: 'Total Return %'}},
        {{key: 'total_abs_return_pct', label: 'Total Abs %'}},
        {{key: 'average_monthly_return_pct', label: 'Avg Monthly %'}},
        {{key: 'total_trades', label: 'Trades'}},
        {{key: 'max_drawdown_pct', label: 'Max DD %'}},
        {{key: 'direction_month_ratio', label: 'Direction Month Ratio'}},
        {{key: 'min_stable_month_ratio', label: 'Target Stable Ratio'}},
        {{key: 'walk_forward_windows', label: 'WF Windows'}},
      ];
      document.getElementById('cycleSummary').innerHTML = renderTable([cycleMetricRow(cycle)], summaryColumns);

      const main = cycle.main_backtest || {{}};
      if (main.available && main.monthly_returns && main.monthly_returns.length) {{
        document.getElementById('mainMonthlyNote').innerHTML = `Archived source: <span class="pill">${{escapeHtml(main.path || '')}}</span>`;
        document.getElementById('mainMonthlyTable').innerHTML = renderTable(main.monthly_returns, monthlyColumns(main.monthly_returns));
      }} else {{
        document.getElementById('mainMonthlyNote').textContent = 'No archived main monthly return table for this cycle. Older main monthly_returns.csv files were overwritten before per-cycle archiving existed.';
        document.getElementById('mainMonthlyTable').innerHTML = '';
      }}

      const runs = cycle.batch_runs || [];
      if (!runs.length) {{
        document.getElementById('batchRuns').innerHTML = '<div class="muted">No batch-search run was linked to this cycle.</div>';
        return;
      }}
      document.getElementById('batchRuns').innerHTML = runs.map(run => {{
        const rankingCols = [
          {{key: 'rank', label: 'Rank'}},
          {{key: 'candidate_id', label: 'Candidate'}},
          {{key: 'score', label: 'Score'}},
          {{key: 'passes_target', label: 'Passes'}},
          {{key: 'average_monthly_return_pct', label: 'Avg Monthly %'}},
          {{key: 'total_return_pct', label: 'Total Return %'}},
          {{key: 'total_trades', label: 'Trades'}},
          {{key: 'profit_factor', label: 'Profit Factor'}},
          {{key: 'max_drawdown_pct', label: 'Max DD %'}},
        ];
        const bestId = run.best_candidate ? run.best_candidate.candidate_id : '';
        const monthly = run.best_monthly_returns || [];
        return `
          <div class="section">
            <div><strong>${{escapeHtml(run.name)}}</strong> <span class="pill">${{escapeHtml(run.inferred_phase || 'unassigned')}}</span> <span class="muted">${{escapeHtml(run.path || '')}}</span></div>
            <h2>Top Candidates</h2>
            ${{renderTable(run.top_candidates || [], rankingCols)}}
            <h2>Best Candidate Monthly Returns: ${{escapeHtml(bestId || 'n/a')}}</h2>
            ${{renderTable(monthly, monthlyColumns(monthly))}}
          </div>`;
      }}).join('');
    }}

    function monthlyColumns(rows) {{
      if (!rows || !rows.length) return [];
      return Object.keys(rows[0]).map(key => ({{key, label: key}}));
    }}

    function renderAllCycles() {{
      const rows = cycles.map(cycleMetricRow);
      const columns = [
        {{key: 'cycle', label: 'Cycle'}},
        {{key: 'time', label: 'Completed At'}},
        {{key: 'success', label: 'Success'}},
        {{key: 'training_objective', label: 'Objective'}},
        {{key: 'stable_band_direction', label: 'Direction'}},
        {{key: 'total_return_pct', label: 'Total Return %'}},
        {{key: 'total_abs_return_pct', label: 'Total Abs %'}},
        {{key: 'average_monthly_return_pct', label: 'Avg Monthly %'}},
        {{key: 'total_trades', label: 'Trades'}},
        {{key: 'max_drawdown_pct', label: 'Max DD %'}},
        {{key: 'direction_month_ratio', label: 'Direction Month Ratio'}},
      ];
      document.getElementById('allCyclesTable').innerHTML = renderTable(rows, columns);
      document.getElementById('totalCount').textContent = String(rows.length);
      document.getElementById('visibleCount').textContent = String(rows.length);
    }}

    function applyFilter() {{
      const query = document.getElementById('cycleFilter').value.toLowerCase();
      const table = document.querySelector('#allCyclesTable table');
      if (!table) return;
      let count = 0;
      for (const row of table.tBodies[0].rows) {{
        const show = row.innerText.toLowerCase().includes(query);
        row.style.display = show ? '' : 'none';
        if (show) count++;
      }}
      document.getElementById('visibleCount').textContent = String(count);
    }}

    const select = document.getElementById('cycleSelect');
    for (const cycle of cycles) {{
      const option = document.createElement('option');
      option.value = String(cycle.cycle);
      const m = cycle.metrics || {{}};
      option.textContent = `Cycle ${{cycle.cycle}} | total ${{formatValue(m.total_return_pct, 'total_return_pct')}}% | avg monthly ${{formatValue(m.average_monthly_return_pct, 'average_monthly_return_pct')}}% | trades ${{formatValue(m.total_trades, 'total_trades')}}`;
      select.appendChild(option);
    }}
    if (cycles.length) select.value = String(cycles[cycles.length - 1].cycle);
    select.addEventListener('change', renderSelectedCycle);
    document.getElementById('cycleFilter').addEventListener('input', applyFilter);
    renderAllCycles();
    renderSelectedCycle();
  </script>
</body>
</html>
"""


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file() or path.stat().st_size == 0:
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return {}


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items() if not str(key).startswith("_")}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return value


def _read_csv_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or not path.is_file() or path.stat().st_size == 0:
        return []
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            return list(csv.DictReader(handle))
    except OSError:
        return []


def _batch_time(path: Path) -> datetime | None:
    prefix = "batch_search_"
    if path.name.startswith(prefix):
        stamp = path.name[len(prefix) :]
        try:
            return datetime.strptime(stamp, "%Y%m%d_%H%M%S")
        except ValueError:
            pass
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        return parsed.replace(tzinfo=None)
    except ValueError:
        return None


def _cycle_from_role(role: str) -> int:
    marker = "cycle-"
    if marker not in role:
        return 0
    try:
        return int(role.rsplit(marker, 1)[1].split()[0])
    except ValueError:
        return 0


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)
