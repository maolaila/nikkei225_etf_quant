# Codex Quant Supervisor

This workspace contains a Codex CLI supervisor script:

```powershell
.\Start-CodexQuantAgent.ps1
```

It is designed to keep running until the configured historical/paper
regression goal is reached:

```text
monthly return target starts at 3%
total_return_pct > 0
total_trades >= 50
walk_forward_windows >= 6
walk_forward_fallback_used == false
profit_factor >= 1.2
max_drawdown_pct >= -15
positive_active_month_ratio >= 0.55
min_monthly_return_pct >= -8
at least 20 fresh regression cycles
at least 5 consecutive successful cycles
```

The return target is monthly. By default the supervisor uses
`average_monthly_return_pct`; after a stable win at the current monthly
target, it raises the target by `2` percentage points and continues, for
example:

```text
3% -> 5% -> 7% -> 9% ...
```

## Recommended Run

```powershell
.\Start-CodexQuantAgent.ps1 -UseSearch -DangerouslyBypassApprovalsAndSandbox -ResetState
```

For an unattended long run, use explicit long-run settings:

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
  -MonthlyReturnTargetMode average `
  -DataExpansionEveryCycles 25 `
  -DataStaleCyclesBeforeExpansion 25
```

Defaults:

- `-Model gpt-5.5`
- `-ReasoningEffort xhigh`
- Service tier uses the Codex CLI default speed unless `-ServiceTier` is explicitly set
- `-MaxCycles 0`, meaning unlimited cycles
- `-MaxDevelopmentPasses 0`, meaning unlimited development passes
- `-StalledSubagentMinutes 30`, meaning a child agent with no log progress for 30 minutes is killed and handed to repair
- `-MaxConsecutiveFailures 20`, preventing unattended failure loops
- `-MinRegressionCycles 20`, meaning the first positive run is not enough
- `-RequiredConsecutiveSuccesses 5`
- `-TargetTotalReturnPct 3`
- `-TargetReturnIncrementPct 2`
- `-MinTrades 50`
- `-MinProfitFactor 1.2`
- `-MaxDrawdownPct 15`
- `-MinPositiveMonthRatio 0.55`, measured on active non-flat months
- `-MinMonthlyReturnFloorPct -8`
- `-MinWalkForwardWindows 6`
- `-MonthlyReturnTargetMode average`
- `-MaxTargetTotalReturnPct 0`, meaning no target cap
- `-DataExpansionEveryCycles 25`
- `-DataStaleCyclesBeforeExpansion 25`
- auto-repair enabled
- live trading disabled by prompt guardrails

Use `-StopWhenStableTargetReached` only if you want the old behavior of
stopping after the first stable target is reached. Without it, the
supervisor records the win, raises the return target, and keeps running.

Monthly return records are written every backtest:

- `data/reports/backtest/monthly_returns.csv`
- `data/reports/backtest/monthly_returns.html`

The HTML file opens locally in a browser and supports keyword filtering
and clicking column headers to sort. It records every year/month return,
positive or negative.

`MinTrades` is a validation sample-size gate, not a quota. The strategy
should stay flat when conditions are unsuitable; it should not overtrade
just to reach the threshold.

## Dynamic Risk Controls

The backtest now treats configured position limits as a dynamic range:

- `max_equity_pct` is the normal base allocation for an action.
- `absolute_max_equity_pct` is the hard cap that even strong signals
  cannot exceed.
- `backtest.position_sizing.dynamic_enabled=true` scales allocation by
  model confidence, action probability, market regime, and whether the
  selected ETF is leveraged.
- `strategy.exit.dynamic_holding.enabled=true` extends holding time only
  for stronger signals and shortens weak/range signals.
- `strategy.exit.dynamic_stop_loss.enabled=true` widens stops for strong
  trend signals and tightens stops for weaker/range signals.
- `backtest.risk.realized_loss_gate.enabled=true` blocks new entries
  after configured daily realized-loss or consecutive-loss limits.

The active values used for each simulated trade are written to
`trade_log.csv` and `trade_log.html`: `confidence`,
`action_probability`, `target_equity_pct`, `absolute_max_equity_pct`,
`max_holding_minutes`, and `stop_loss_pct`.

The current production risk base intentionally blocks the latest
real-data loss clusters before searching for higher return: `short_1x`
entries are blocked, weak morning-trend entries are filtered, stop losses
are tighter, and daily/serial realized-loss gates are enabled. This is a
risk-adjusted baseline, not a claim that returns are guaranteed.

`MinPositiveMonthRatio` is evaluated as `positive_active_month_ratio`.
Flat months where the strategy deliberately did not trade do not count
against that ratio; they still affect the average monthly return target.

## Hybrid Search Mode

The supervisor now asks optimizer agents to run a local batch sweep before
editing strategy code:

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

`batch-search` reuses the latest real walk-forward predictions and runs
many backtest-only candidate settings locally. It varies prediction
gates, stop loss, max holding time, risk limits, and position sizing.
The AI reads only the compact outputs:

- `data/reports/experiments/batch_search_*/ranking.csv`
- `data/reports/experiments/batch_search_*/summary.json`
- `data/reports/experiments/batch_search_*/report.md`

This keeps the expensive repeated candidate evaluation inside Python.
AI intervention is mainly for reading the ranking, explaining failures,
and making bounded algorithm/config changes when local candidate sweeps
are not enough.

## Data Expansion

The supervisor periodically starts a `data-expander-*` subagent. It must
inspect data coverage and provenance, then acquire or implement more
historical data when the current dataset is synthetic, stale, exhausted,
or too narrow.

Data priority:

- authenticated configured APIs via environment variables
- safe public research sources where permitted
- synthetic/offline data only as a clearly labeled fallback

If new data is added, the data expander should invalidate stale derived
artifacts such as features, labels, model predictions, and backtest
reports so the next cycle produces a fresh regression.

## Stop Without Killing PowerShell

Create this file:

```powershell
New-Item -ItemType File -Force .\.codex_quant_agent\STOP
```

The supervisor checks for it between subagent runs and exits with
`stopped_by_stop_file`.

Remove it before starting again:

```powershell
Remove-Item .\.codex_quant_agent\STOP -Force
```

## State And Logs

Runtime files are written under:

```text
.codex_quant_agent/
  logs/
  prompts/
  outputs/
  state/
```

Important files:

- `.codex_quant_agent/state/state.json`
- `.codex_quant_agent/state/supervisor_config.json`
- `.codex_quant_agent/logs/supervisor.log`

## Safety Boundary

The supervisor and all subagent prompts require historical backtest,
paper trading, or alert-only modes. They explicitly keep:

```text
live_trading.enabled=false
live_order_enabled=false
```

The script is not a real-money trading bot and should not be modified to
place broker orders without a separate manual review.
