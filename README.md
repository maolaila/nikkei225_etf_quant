# Nikkei 225 ETF Quant Backtester

Python 3.11 project for historical backtesting, walk-forward validation, CSV replay, and paper-trading simulation for Nikkei 225 related ETFs.

This repository is deliberately safe by default:

- `live_trading.enabled=false`
- `kabustation.live_order_enabled=false`
- no real-money order placement is implemented
- profitability targets require real non-synthetic market data

Historical profit in this project is only evidence for further investigation. It is not a guarantee of future results.

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Real Data Preparation

After configuring `market_data_collector\.env` with the J-Quants API key and enabling minute data, prepare the real data lake:

```powershell
.\Start-RealDataModelSearch.ps1 `
  -MinuteStartDate 2024-05-16 `
  -MinuteEndDate 2026-05-15 `
  -DailyStartDate 2016-05-16 `
  -DailyEndDate 2026-05-15
```

Outputs are written under `data/reports/backtest/`:

- `metrics.json`
- `report.md`
- `equity_curve.csv`
- `signal_log.csv`
- `trade_log.csv`
- `trade_log.html`
- `monthly_returns.csv`
- `monthly_returns.html`

## Start Long-Running Codex CLI Search

If the real data has already been downloaded, start the long-running autonomous regression/search loop with:

```powershell
.\Start-RealDataModelSearch.ps1 `
  -SkipDailyDownload `
  -SkipMinuteDownload `
  -SkipSmokeBacktest `
  -StartCodexSupervisor `
  -UseSearch `
  -DangerouslyBypassApprovalsAndSandbox `
  -SubagentTimeoutMinutes 120 `
  -StalledSubagentMinutes 30 `
  -MinRegressionCycles 100 `
  -RequiredConsecutiveSuccesses 10 `
  -TrainingObjective stable-band `
  -TargetTotalAbsReturnPct 5 `
  -MaxTotalAbsReturnPct 20 `
  -MinStableMonthRatio 0.60 `
  -BatchRiskProfile aggressive `
  -BatchConfigOverrides config/experiments/aggressive_stable_loss.yaml `
  -AllowAutonomousConfigApply `
  -MinTrades 50 `
  -MaxDrawdownPct 20 `
  -MinWalkForwardWindows 6 `
  -DataExpansionEveryCycles 25 `
  -DataStaleCyclesBeforeExpansion 25
```

The default supervisor objective is `stable-band`: positive or negative return direction is acceptable, but the direction must be stable. A passing cycle must land in one signed band, either `+5%..+20%` or `-20%..-5%`, and monthly returns must mostly match that same direction. Consecutive successes must keep the same direction; a switch from positive to negative, or negative to positive, starts a new streak. `MinTrades` remains a sample-size gate, not a forced trade quota. Success still requires real non-synthetic data, multiple walk-forward windows, realistic execution costs, bounded drawdown, and consecutive passing cycles.

`-BatchRiskProfile aggressive` and `-AllowAutonomousConfigApply` give the agent room to test higher-risk historical/paper-mode configurations. Live trading stays disabled.

After each AI adjustment pass, the supervisor automatically commits and pushes
code/config/doc changes to GitHub. This happens after `strategy-optimizer` on
failed cycles and after `regression-audit` on successful cycles. Ignored
runtime artifacts such as `data/` and `.codex_quant_agent/` are not committed.
Use `Start-CodexQuantAgent.ps1 -DisableAutoGitCommit` only when you want to
inspect local AI changes before pushing.

## Stop The Supervisor Gently

Create the stop file from another PowerShell window:

```powershell
Set-Location D:\nikkei225_etf_quant
New-Item -ItemType File -Force .\.codex_quant_agent\STOP
```

The supervisor checks this file from its heartbeat/subagent loop and exits with
`stopped_by_stop_file`. Watch progress with:

```powershell
Get-Content .\.codex_quant_agent\logs\supervisor.log -Tail 40 -Wait
```

Remove the stop file before starting a new run:

```powershell
Remove-Item .\.codex_quant_agent\STOP -Force
```

The strategy uses dynamic risk controls. `max_equity_pct` is the normal
allocation base, while `absolute_max_equity_pct` is the hard cap. Strong
high-confidence trend signals can receive larger allocation and longer
holding time within that hard cap; weak or range signals receive smaller
allocation, shorter holding time, and tighter stop loss. The active
values are recorded in `trade_log.csv` and `trade_log.html`.

The current production baseline also blocks the largest observed
real-data loss clusters before searching for return: `short_1x` entries
are disabled, weak morning-trend entries are filtered, stop losses are
tighter, and daily/serial realized-loss gates are enabled.

For local high-throughput candidate sweeps without invoking AI for every
candidate:

```powershell
python -m src.main batch-search `
  --candidates 48 `
  --target-monthly-return-pct 3 `
  --min-trades 50 `
  --min-profit-factor 1.2 `
  --max-drawdown-pct 15 `
  --min-positive-month-ratio 0.55 `
  --min-monthly-return-floor-pct -8 `
  --min-walk-forward-windows 6
```

Batch outputs are under `data/reports/experiments/batch_search_*/`.

For a research-only aggressive exposure sweep that keeps the current model
direction logic but searches for historically stable losses within a bounded
risk band:

```powershell
python -m src.main batch-search `
  --config-overrides config/experiments/bounded_stable_loss.yaml `
  --objective stable-loss `
  --risk-profile aggressive `
  --target-monthly-loss-pct 5 `
  --target-total-loss-pct 5 `
  --max-total-loss-pct 20 `
  --max-drawdown-pct 20 `
  --min-negative-month-ratio 0.60 `
  --min-loss-month-ratio 0.00 `
  --max-positive-month-ratio 0.20 `
  --min-trades 50 `
  --candidates 48
```

This mode ranks candidates by controlled loss consistency, not profitability.
It requires total loss to be at least `target-total-loss-pct` but no worse than
`max-total-loss-pct`, and drawdown no worse than `max-drawdown-pct`. Monthly
loss ratios are used as stability checks so the search favors a repeatable
negative edge under moderate risk instead of ranking wipeout candidates first.
Passing the stable-loss gates is only historical research evidence, not proof
that reversing live trades would be profitable.

To search for stable return direction and stable return range, use
`stable-band`. This accepts positive or negative total return, but the cycle
must fit one signed band (`+target..+max` or `-max..-target`) and the monthly
returns must mostly match that same direction:

```powershell
python -m src.main batch-search `
  --config-overrides config/experiments/bounded_stable_loss.yaml `
  --objective stable-band `
  --risk-profile aggressive `
  --target-total-abs-return-pct 5 `
  --max-total-abs-return-pct 20 `
  --max-drawdown-pct 20 `
  --min-stable-month-ratio 0.60 `
  --min-trades 50 `
  --candidates 48
```

For `stable-band`, direction stability and range stability are both primary.
The ranking gives comparable weight to staying inside the signed return band
and to the monthly returns matching the selected direction, then penalizes
drawdown, volatility, synthetic data, fallback validation, and low trade count.

Generate the cycle dashboard with a cycle dropdown:

```powershell
python -m src.main training-cycle-report
```

The dashboard is written to `data/reports/backtest/training_cycles.html`.
It reads supervisor cycle metrics from `.codex_quant_agent/state/state.json`
and batch candidate monthly returns from `data/reports/experiments/batch_search_*`.

Use this HTML file as the primary training progress view. For stable-band runs,
the dashboard shows total return, absolute total return, drawdown, trade count,
selected direction, and monthly-direction stability fields for each cycle.

For future supervisor runs, each completed cycle also archives the main
backtest files under `data/reports/regression_cycles/cycle_*/main_backtest/`
and refreshes `training_cycles.html`. Main `data/reports/backtest/monthly_returns.csv`
is overwritten by each new run, so historical main monthly returns from cycles
before this archive step may not exist. Batch-search candidate monthly returns
remain available in their timestamped experiment directories.

## Manual Multi-Timeframe Long Run

The direct long-run entrypoint is `train-until-target`. It rebuilds the
derived training artifacts, trains walk-forward predictions, runs a baseline
backtest, then repeatedly searches backtest parameters until a candidate meets
the gates or the cycle cap is reached.

Current model inputs are still historical ETF-only J-Quants 1-minute OHLCV.
The feature set adds trailing 3-minute, 5-minute, 15-minute, and lagged daily
context features. Because the historical data has no real bid/ask/depth,
backtest results remain research evidence, not live-trading proof.

Start manually:

```powershell
python -m src.main train-until-target `
  --force-rebuild `
  --max-cycles 100 `
  --candidates-per-cycle 48 `
  --seed 42 `
  --target-monthly-return-pct 3 `
  --min-trades 50 `
  --min-profit-factor 1.2 `
  --max-drawdown-pct 15 `
  --min-positive-month-ratio 0.55 `
  --min-monthly-return-floor-pct -8 `
  --min-walk-forward-windows 6 `
  --output-root data/reports/long_run
```

Use `--force-rebuild` after changing feature, label, model, or walk-forward
logic. It deletes only derived artifacts such as `data/features`,
`data/labels`, `data/models`, and the latest backtest report. It does not
delete `data/raw`, `data/normalized`, or `market_data_collector/data/raw`.

Outputs:

- `data/features/features.parquet`
- `data/labels/labels.parquet`
- `data/models/walk_forward_predictions.*`
- `data/models/walk_forward_summary.json`
- `data/reports/long_run/train_until_target_*/status.json`
- `data/reports/long_run/train_until_target_*/report.md`
- `data/reports/long_run/train_until_target_*/cycles/*/ranking.csv`

Check progress:

```powershell
$latest = Get-ChildItem data/reports/long_run/train_until_target_* |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

Get-Content "$($latest.FullName)\status.json"
```

If you start it as a background process, keep the process id and stop it with:

```powershell
Stop-Process -Id <PID>
```

The target gates are intentionally conservative. A passing run means the
candidate survived the configured historical walk-forward and execution-cost
checks. It does not mean the strategy is ready for live trading without real
bid/ask/depth, quote-staleness checks, futures/index/iNAV inputs, and paper
execution review.

## Event And Outlier Audit

Training keeps real market event days by default. Before long-run training,
the pipeline now writes an event audit that separates market stress days from
data-basis anomalies such as suspected ETF split or adjustment discontinuities.

Run the audit directly:

```powershell
python -m src.main event-audit
```

Outputs:

- `data/reports/event_audit/daily_event_flags.csv`
- `data/reports/event_audit/abnormal_minute_bars.csv`
- `data/reports/event_audit/event_audit_summary.json`
- `data/reports/event_audit/event_audit.md`

The main policy is conservative: true event days remain in training, while
corporate action candidates are flagged for review. To check event dependence
without changing the main dataset, run sensitivity backtests:

```powershell
python -m src.main event-sensitivity
```

This writes `data/reports/event_sensitivity/` with scenarios that block new
entries on market event days, allow entries only on market event days, and
temporarily retrain walk-forward predictions with event or corporate-action
candidate dates removed from training. Temporary retraining restores the
original `data/models/walk_forward_predictions.*` artifacts after each
scenario. The
`train-until-target` flow runs this sensitivity package after the baseline
backtest unless `--skip-event-sensitivity` is supplied.

## Data Providers

The real-data path uses `market_data_collector` with J-Quants daily and paid minute bars. Synthetic/offline data is allowed only for plumbing tests and does not count toward profitability milestones.

## Tests

```powershell
pytest
```

## Safety Notes

The project supports only:

- `historical_backtest`
- `paper_trading`
- `alert_only`

Paper trading is simulated accounting only. Broker order APIs are not used for order placement.
