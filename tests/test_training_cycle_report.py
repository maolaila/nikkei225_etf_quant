from __future__ import annotations

import csv
import json
from pathlib import Path

from src.review.training_cycle_report import generate_training_cycle_report


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_training_cycle_report_archives_current_cycle_and_renders_dropdown(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state_path = tmp_path / ".codex_quant_agent" / "state" / "state.json"
    reports_root = tmp_path / "data" / "reports"
    backtest_dir = reports_root / "backtest"
    batch_dir = reports_root / "experiments" / "batch_search_20260517_010000"
    candidate_dir = batch_dir / "candidate_001"

    state = {
        "cycle": 2,
        "regression_cycles": 2,
        "last_metrics": {"total_return_pct": 1.25, "total_trades": 52},
        "history": [
            {
                "time": "2026-05-17T01:05:00+09:00",
                "event": "backtest-runner",
                "data": {"role": "backtest-runner-cycle-2"},
            },
            {
                "time": "2026-05-17T01:05:30+09:00",
                "event": "regression-result",
                "data": {
                    "regression_cycles": 2,
                    "success": True,
                    "current_target_total_return_pct": 3,
                    "metrics": {
                        "file": str(backtest_dir / "metrics.json"),
                        "total_return_pct": 1.25,
                        "average_monthly_return_pct": 0.50,
                        "total_trades": 52,
                        "profit_factor": 1.30,
                        "max_drawdown_pct": -2.0,
                        "positive_active_month_ratio": 0.75,
                        "walk_forward_windows": 12,
                        "data_is_synthetic": False,
                    },
                },
            },
        ],
    }
    _write_json(state_path, state)
    _write_json(backtest_dir / "metrics.json", state["history"][1]["data"]["metrics"])
    _write_csv(
        backtest_dir / "monthly_returns.csv",
        [{"year_month": "2026-05", "return_pct": 0.75, "pnl_jpy": 7500}],
    )
    _write_csv(
        batch_dir / "ranking.csv",
        [
            {
                "rank": 1,
                "candidate_id": "candidate_001",
                "score": 12.5,
                "passes_target": True,
                "average_monthly_return_pct": 0.7,
                "total_return_pct": 1.5,
                "total_trades": 60,
                "profit_factor": 1.4,
                "max_drawdown_pct": -1.5,
            }
        ],
    )
    _write_json(batch_dir / "summary.json", {"candidate_count": 1})
    _write_json(candidate_dir / "metrics.json", {"total_return_pct": 1.5})
    _write_csv(
        candidate_dir / "monthly_returns.csv",
        [{"year_month": "2026-05", "return_pct": 0.9, "pnl_jpy": 9000}],
    )

    path = generate_training_cycle_report(state_path=state_path, reports_root=reports_root, archive_current=True)
    text = path.read_text(encoding="utf-8")

    assert path == reports_root / "backtest" / "training_cycles.html"
    assert (reports_root / "regression_cycles" / "cycle_002" / "main_backtest" / "monthly_returns.csv").exists()
    assert '<select id="cycleSelect">' in text
    assert "Cycle ${cycle.cycle}" in text
    assert "candidate_001" in text
    assert "Main Backtest Monthly Returns" in text
    assert "Batch Search Runs" in text
    assert '"return_pct": "该月收益率，单位为百分比。"' in text
    assert 'title="${escapeHtml(title)}"' in text
    assert "numeric.toFixed(2)" in text


def test_training_cycle_report_can_filter_batch_objective(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state_path = tmp_path / ".codex_quant_agent" / "state" / "state.json"
    reports_root = tmp_path / "data" / "reports"
    state = {
        "history": [
            {
                "time": "2026-05-17T14:00:00+09:00",
                "event": "regression-result",
                "data": {
                    "regression_cycles": 31,
                    "metrics": {"total_return_pct": -1.0, "total_trades": 50},
                },
            }
        ]
    }
    _write_json(state_path, state)

    profit_dir = reports_root / "experiments" / "batch_search_profit"
    stable_dir = reports_root / "experiments" / "batch_search_stable"
    _write_json(profit_dir / "summary.json", {"objective": "profit", "best_candidate": {"candidate_id": "old"}})
    _write_csv(profit_dir / "ranking.csv", [{"rank": 1, "candidate_id": "old"}])
    _write_json(stable_dir / "summary.json", {"objective": "stable-band", "best_candidate": {"candidate_id": "new"}})
    _write_csv(stable_dir / "ranking.csv", [{"rank": 1, "candidate_id": "new"}])

    path = generate_training_cycle_report(
        state_path=state_path,
        reports_root=reports_root,
        objective_filter="stable-band",
    )
    text = path.read_text(encoding="utf-8")

    assert "stable-band" in text
    assert "new" in text
    assert '"candidate_id": "old"' not in text
