# Real Data Model Search Handoff

This project now has a real-data path for J-Quants paid data and a Codex CLI supervisor path for long-running model search.

## Current Verified Coverage

- J-Quants daily endpoint works for 10-year context data. A 2016-06 sample for `1570` returned successfully.
- J-Quants minute endpoint is enabled and works. A 2024-06-03 sample for all four ETFs returned successfully.
- The minute endpoint rejected 2016-06-01 and reported minute coverage starting at `2024-05-16`.
- Therefore the minute strategy training window should start at `2024-05-16` unless J-Quants changes the add-on coverage.
- Ten-year daily data can still be downloaded and used for long-term regime/context research.

## Verified Small-Sample Pipeline

The following real 1m sample completed end-to-end:

```powershell
cd D:\nikkei225_etf_quant\market_data_collector
python -m market_data_collector.cli download --provider jquants --symbols 1357,1570,1321,1571 --interval 1m --from-date 2024-06-03 --to-date 2024-06-03 --format parquet --max-pages 1 --overwrite

cd D:\nikkei225_etf_quant
python -m src.main import-market-data --provider jquants --interval 1m --from-date 2024-06-03 --to-date 2024-06-03
python -m src.main normalize-data
python -m src.main validate-data
python -m src.main build-features
python -m src.main build-labels
python -m src.main walk-forward --model random_forest
python -m src.main backtest --model latest
python -m src.main report --type backtest
```

The smoke backtest was negative. That is expected for a one-day sample and is useful because it proves the system is not faking profitability.

## Full Real-Data Preparation

Use this when you want to download all available paid minute data and then run the initial smoke pass:

```powershell
cd D:\nikkei225_etf_quant
.\Start-RealDataModelSearch.ps1 `
  -MinuteStartDate 2024-05-16 `
  -MinuteEndDate 2026-05-15 `
  -DailyStartDate 2016-05-16 `
  -DailyEndDate 2026-05-15
```

To skip the long downloads after data already exists:

```powershell
.\Start-RealDataModelSearch.ps1 `
  -SkipDailyDownload `
  -SkipMinuteDownload `
  -MinuteStartDate 2024-05-16 `
  -MinuteEndDate 2026-05-15
```

## Local Batch Candidate Search

During long regressions, run many local candidate backtests first and let
AI inspect the compact ranking before changing code:

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

Outputs:

- `data/reports/experiments/batch_search_*/ranking.csv`
- `data/reports/experiments/batch_search_*/summary.json`
- `data/reports/experiments/batch_search_*/report.md`

`batch-search` reuses the latest walk-forward predictions and varies only
backtest-side parameters such as prediction gates, stop loss, maximum
holding minutes, daily trade limit, and position sizing. This is the
intended hybrid mode: Python does high-frequency candidate evaluation,
and Codex CLI uses AI mainly to review summaries and make bounded
strategy/model changes between batches.

## Start Long-Running Codex CLI Search

After the real-data preparation succeeds:

```powershell
.\Start-RealDataModelSearch.ps1 `
  -SkipDailyDownload `
  -SkipMinuteDownload `
  -SkipSmokeBacktest `
  -StartCodexSupervisor `
  -UseSearch `
  -DangerouslyBypassApprovalsAndSandbox `
  -MinuteStartDate 2024-05-16 `
  -MinuteEndDate 2026-05-15 `
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

The supervisor counts success only when the latest metrics are based on non-synthetic data and pass the return, trade-count, profit-factor, drawdown, positive-active-month-ratio, and worst-month gates.

## Main Edit Surfaces for Codex CLI

- `src/features/feature_pipeline.py`: feature engineering, including multi-timeframe returns, MACD, KDJ, RSI, volume, VWAP, volatility, gap, and opening-range features.
- `src/models/rule_based_model.py`: transparent rule strategy.
- `src/models/sklearn_model.py`: trainable logistic regression and random forest classifiers.
- `config/model.yaml`: model type and hyperparameters.
- `config/strategy.yaml`: signal thresholds and exits.
- `config/labeling.yaml`: future-return label thresholds.
- `config/backtest.yaml`: costs, slippage, risk, sizing, and execution assumptions.

## Safety Rules

- Keep `live_trading.enabled=false`.
- Keep `kabustation.live_order_enabled=false`.
- Do not count synthetic data as a profitability milestone.
- Do not disable costs/slippage to improve returns.
- Do not use future columns in features.
- Do not hard-code profitable dates or symbols.
- Backtest profit is evidence for investigation, not a guarantee of future profit.
