# Nikkei 225 ETF Intraday Architecture Review

Date: 2026-05-16

## A. Current Issues

| Area | File / function | Risk | Finding | Change made |
|---|---|---:|---|---|
| Data source boundary | `src/data/providers/base.py`, `config/data_sources.yaml` | High | Historical providers and future live/execution providers were not represented as separate contracts. | Added `HistoricalDataProvider`, `LiveMarketDataProvider`, `ExecutionProvider` contracts and safe Tachibana/KabuStation/IBKR adapter stubs. Marked J-Quants as historical-only. |
| Feature engineering | `src/features/feature_pipeline.py` | High | Features were built from one reference ETF and did not measure ETF market distortion across 1321/1570/1571/1357. | Added cross-ETF implied Nikkei features, dispersion, max gap, pair gaps, and historical bid/ask limitation flags. |
| Price basis | `src/features/feature_pipeline.py`, `src/labeling/future_return_labeler.py` | Medium | Historical bars used close as the effective model price without naming the bid/ask limitation. | Added `mid_price_proxy`; live quote modules require bid/ask and historical data is explicitly flagged as close proxy. |
| Look-ahead risk | `src/labeling/future_return_labeler.py` | Medium | Labels shifted by horizon globally, so horizons could cross lunch or session boundaries. | Labels now shift within symbol/date/session and generate N+1/N+3/N+5/N+15 target returns. |
| Execution assumptions | `src/backtest/engine.py` | Medium | Backtest used next bar open with cost model, but lacked configurable bid/ask execution references and market quality filters. | Added latency alias, optional bid/ask or mid reference price, session/spread/depth/stale quote/ETF dispersion filters. |
| ETF distortion control | `src/backtest/engine.py` | High | No execution gate for excessive implied Nikkei dispersion. | Added `max_implied_dispersion_bps` filter and tests. |
| Risk management | `src/risk/manager.py` | High | There was no production-facing risk manager contract. | Added `RiskManager.approve_signal()` with quote, session, dispersion, position, daily loss, consecutive loss, and net-edge checks. |
| Live bar construction | missing | High | No live minute bar builder existed for broker quote/tick streams. | Added `src/market/bar_builder.py` supporting exchange timestamps, Tokyo sessions, quote/trade updates, duplicates, stale quote counts, missing bid/ask counts, and out-of-order tick drops. |
| Audit log | `src/backtest/report.py`, `src/backtest/engine.py` | Medium | Trade log missed expected return/cost/net edge, bid/ask/depth, implied Nikkei, and risk-filter results. | Added audit columns and propagation from model signals to simulated trades. |
| Metrics | `src/backtest/metrics.py` | Medium | Required metrics such as Sortino, turnover, average holding time, exposure, largest win/loss, and capacity estimate were missing. | Added the missing metrics. |

## B. Code Modification Summary

New files:

- `src/market/session.py`
- `src/market/quote.py`
- `src/market/bar_builder.py`
- `src/features/implied_nikkei.py`
- `src/data/providers/adapters.py`
- `src/risk/manager.py`
- `docs/backtest_report_template.md`
- Tests for implied Nikkei, bar builder, provider interfaces, and ETF dispersion risk filter.

Modified files:

- `src/features/feature_pipeline.py`
- `src/labeling/future_return_labeler.py`
- `src/models/rule_based_model.py`
- `src/models/sklearn_model.py`
- `src/backtest/engine.py`
- `src/backtest/report.py`
- `src/backtest/metrics.py`
- `src/data/providers/base.py`
- `src/config/loader.py`
- `config/data_sources.yaml`
- `config/backtest.yaml`
- `config/labeling.yaml`
- `config/strategy.yaml`

## C. Training Pipeline Standard

1. Load J-Quants historical ETF minute bars as historical data only.
2. Clean and align 1321 / 1570 / 1571 / 1357 by timestamp.
3. Build `mid_price_proxy`; use bid/ask mid when available, otherwise mark historical close proxy limitation.
4. Build ETF implied Nikkei features and dispersion filters.
5. Add optional Nikkei index, Nikkei futures, iNAV, USDJPY, TOPIX, and US futures features when datasets exist.
6. Generate only trailing features; labels remain separate.
7. Generate session-contained N+1 / N+3 / N+5 / N+15 ETF target returns.
8. Deduct estimated cost and require positive net edge before labels/signals are tradable.
9. Train with walk-forward or purged time-series split only.
10. Backtest with latency/slippage/spread/dispersion sensitivity.
11. Mark ETF-only results as limited until index/futures/iNAV ablations are populated.

## E. Current Conclusion

- ETF-only monitoring is now supported as a baseline, not as the final production assumption.
- The largest ETF-only risk is ETF market distortion: spread, depth, market-maker quote behavior, and leveraged/inverse ETF path dependence.
- Nikkei 225 futures should be treated as the highest-priority external direction input when data is available.
- Nikkei 225 index remains useful as a reference baseline, but not as an execution price.
- ETF implied Nikkei checks are implemented.
- Current backtest is more conservative than before, but still not production-complete without real bid/ask/depth and external futures/index/iNAV datasets.
- No known future-function issue remains in the touched label path; labels now stay within trading sessions.
- Not suitable for real-money live deployment yet. Minimum path: add real live adapters, quote persistence, futures/index/iNAV ingestion, live bar replay tests, and broker paper/live dry-run certification.

