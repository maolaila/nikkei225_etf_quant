# Backtest Report Template

## Data And Mode

- Historical provider:
- Live provider used in simulation:
- Execution provider/profile:
- Symbols:
- Timeframe:
- Date range:
- Bid/ask availability:
- ETF-only mode: yes/no
- External references available: futures / index / iNAV / USDJPY / TOPIX / US futures

## Model Variants

| Variant | Inputs | Gross return | Net return | Max drawdown | Sharpe | Sortino | Win rate | Profit factor | Turnover | Avg trade | Trades |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A ETF-only | 1321/1570/1571/1357, spread, volume, implied Nikkei | | | | | | | | | | |
| B ETF + index | A + Nikkei 225 index returns/gaps | | | | | | | | | | |
| C ETF + futures | A + futures returns/gaps/lead-lag | | | | | | | | | | |
| D ETF + index + futures + iNAV | Full reference stack | | | | | | | | | | |

## Sensitivity

| Parameter | Values | Net return impact | Drawdown impact | Trade count impact | Notes |
|---|---|---:|---:|---:|---|
| latency_bars | 0 / 1 / 2 / 5 | | | | |
| slippage_bps | 3 / 5 / 10 / 20 | | | | |
| spread_filter_bps | 5 / 10 / 20 / 30 | | | | |
| implied_dispersion_filter_bps | 15 / 30 / 50 | | | | |
| min_edge_bps | 5 / 10 / 15 / 20 | | | | |

## Session Breakdown

| Session bucket | Net return | Drawdown | Trades | False signal rate | Notes |
|---|---:|---:|---:|---:|---|
| 09:00-09:05 opening observation | | | | | |
| Morning continuous auction | | | | | |
| 11:25-11:30 pre-lunch | | | | | |
| 12:30-12:35 afternoon repricing | | | | | |
| Afternoon continuous auction | | | | | |
| 15:25-15:30 pre-close | | | | | |

## Required Verdict

1. Does alpha survive realistic latency, spread, slippage, and commission?
2. Does performance disappear during high ETF implied dispersion?
3. Does adding Nikkei futures materially improve false-signal control?
4. Does iNAV primarily improve risk filtering rather than raw return?
5. Are all signals executed on N+1 or later prices?
6. Are scalers/models fit only on training windows?
7. Is the result production-ready, paper-trade-only, or research-only?

