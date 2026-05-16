# market-data-collector

日本日经 225 相关 ETF 历史行情数据采集器，用于后续分钟级量化回测的数据底座。

当前实现优先支持 J-Quants Free Plan 可用的日线数据下载、保存、校验和聚合框架；分钟线 provider 已预留，但 J-Quants 分钟数据需要 Light Plan 或更高计划，并额外开通分足・Tick Add-on。

## 为什么不用网页爬虫

本项目不抓 TradingView，不抓 NEXT FUNDS 页面，不绕登录，不绕付费，也不绕频率限制。原因：

- 页面结构不稳定
- 可能违反服务条款
- 反爬和登录问题会破坏可复现性
- 回测需要稳定、可追溯、可校验的数据
- 优先使用官方 API 或明确授权的数据源

## Free Plan 当前用途

J-Quants Free Plan 适合先做这些事情：

- 验证 J-Quants API key
- 下载日线数据
- 验证数据保存、分年分区和校验流程
- 搭建未来分钟线回测数据管道

Free Plan 的限制：

- 不能正式下载分钟线
- 不能使用 minute add-on 数据
- 请求频率低
- 部分最近数据可能不可用

## minute 数据说明

J-Quants 股票分钟线需要：

- Light Plan 或更高计划
- 分足・Tick Add-on

开通后在 `.env` 中设置：

```env
JQUANTS_ENABLE_MINUTE=true
```

再运行 1m 下载命令。未设置该开关时，请求分钟线会直接报错，不会尝试访问付费 endpoint。

## 安装

Windows PowerShell:

```powershell
cd market_data_collector
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

macOS / Linux:

```bash
cd market_data_collector
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## 配置

```powershell
cp .env.example .env
```

填写：

```env
JQUANTS_API_KEY=your_key_here
```

不要提交 `.env`。日志、README、测试和代码都不应包含真实 API key。

## 检查环境

```powershell
python -m market_data_collector.cli doctor
```

## 查看支持标的

```powershell
python -m market_data_collector.cli list-symbols
```

支持 ETF：

- 1357: NEXT FUNDS Nikkei 225 Double Inverse Index ETF
- 1570: NEXT FUNDS Nikkei 225 Leveraged Index ETF
- 1321: NEXT FUNDS Nikkei 225 Exchange Traded Fund
- 1571: NEXT FUNDS Nikkei 225 Inverse Index ETF

## 下载日线数据

```powershell
python -m market_data_collector.cli download `
  --provider jquants `
  --symbols 1357,1570,1321,1571 `
  --interval 1d `
  --from-date 2024-01-01 `
  --to-date 2024-12-31 `
  --format parquet
```

输出路径：

```text
data/raw/{provider}/{interval}/{symbol}/{year}.parquet
```

例如：

```text
data/raw/jquants/1d/1570/2025.parquet
```

## dry-run 示例

```powershell
python -m market_data_collector.cli download `
  --provider jquants `
  --symbols 1570 `
  --interval 1d `
  --from-date 2024-01-01 `
  --to-date 2024-01-31 `
  --dry-run
```

dry-run 不发请求，不要求 API key。

## 未来开通 minute add-on 后

PowerShell:

```powershell
$env:JQUANTS_ENABLE_MINUTE="true"

python -m market_data_collector.cli download `
  --provider jquants `
  --symbols 1357,1570,1321,1571 `
  --interval 1m `
  --from-date 2025-01-01 `
  --to-date 2025-12-31 `
  --format parquet
```

macOS / Linux:

```bash
export JQUANTS_ENABLE_MINUTE=true

python -m market_data_collector.cli download \
  --provider jquants \
  --symbols 1357,1570,1321,1571 \
  --interval 1m \
  --from-date 2025-01-01 \
  --to-date 2025-12-31 \
  --format parquet
```

## 聚合分钟线

```powershell
python -m market_data_collector.cli resample `
  --provider jquants `
  --symbols 1357,1570,1321,1571 `
  --source-interval 1m `
  --target-intervals 3min,5min,30min,1d `
  --from-date 2025-01-01 `
  --to-date 2025-12-31
```

聚合规则：

- open = first
- high = max
- low = min
- close = last
- volume = sum
- turnover = sum
- 按交易日和 morning / afternoon session 分开聚合，避免跨午休生成假 K 线

东京交易时段当前简单版：

- morning: 09:00-11:30
- afternoon: 12:30-15:30

TODO: 后续接入 JPX trading calendar 或 pandas_market_calendars。

## 数据检查

```powershell
python -m market_data_collector.cli validate `
  --provider jquants `
  --symbols 1357,1570,1321,1571 `
  --interval 1d `
  --from-date 2024-01-01 `
  --to-date 2024-12-31
```

检查报告输出：

```text
data/processed/reports/validation_{provider}_{interval}_{from}_{to}.csv
```

检查项：

- 重复 datetime
- high < low
- open 超出 high/low
- close 超出 high/low
- volume 为负
- turnover 为负
- 每个交易日行数

J-Quants minute 数据可能不记录无成交分钟，所以 minute 缺失不会自动作为 error，只标记 warning。

## 数据限制

- ETF 杠杆和反向产品不适合把长期持有逻辑直接类比指数
- 1357 等产品可能发生受益权併合/拆分
- 回测前必须考虑复权、滑点、手续费、成交额和流动性
- 分钟级策略不能只看历史收益，需要 walk-forward、样本外测试和交易成本建模
- yfinance 仅作为开发 fallback，不作为正式长期回测数据源

如果 Free Plan 因最近 12 周数据限制拿不到数据，请换更早的日期范围测试。

## 后续 TODO

- 接入完整 JPX 交易日历
- 接入复权因子检查
- 接入 DuckDB
- 接入 backtesting.py / vectorbt / 自研回测框架
- 增加策略模块
- 增加交易成本和滑点模型
