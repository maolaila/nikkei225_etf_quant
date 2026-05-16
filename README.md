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
  -TargetTotalReturnPct 3 `
  -TargetReturnIncrementPct 2 `
  -MinTrades 50 `
  -MinProfitFactor 1.2 `
  -MaxDrawdownPct 15 `
  -MinPositiveMonthRatio 0.55 `
  -MinMonthlyReturnFloorPct -8 `
  -MinWalkForwardWindows 6 `
  -DataExpansionEveryCycles 25 `
  -DataStaleCyclesBeforeExpansion 25
```

The supervisor treats `MinTrades` as a sample-size gate, not a forced trade quota. It counts success only on real non-synthetic data with multiple walk-forward windows, realistic execution costs, sufficient profit factor, bounded drawdown, acceptable active-month win ratio, and monthly return targets.

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
