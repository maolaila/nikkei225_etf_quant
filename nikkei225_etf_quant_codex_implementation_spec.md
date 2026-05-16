# 日经225 ETF 历史回测、模型验证与模拟交易系统：Codex 实现规格书

版本：v2.0  
日期：2026-05-15  
默认仓库名：`nikkei-etf-paper-trader`

---

## 0. 文档目的

本文件用于直接交给 Codex / Cursor / 其他代码生成 AI，实现一个完整的 Python 项目。

项目目标：

1. 获取日经225相关 ETF 的历史数据，优先获取三年 1分钟级数据。
2. 使用历史数据构建多周期特征：1分钟、3分钟、5分钟、30分钟、日线。
3. 基于四类 ETF 交易桶进行模型训练、走步验证和历史回测：
   - 一倍做多 ETF
   - 两倍做多 ETF
   - 一倍做空 ETF
   - 两倍做空 ETF
4. 不直接实盘交易，先做历史回测与 paper trading。
5. 实现真实行情 + 模拟账户 + 每日 AI 复盘功能。
6. 所有参数、标的、数据源、阈值、风控、模型设置均配置化，禁止硬编码。

本项目不是高频交易系统，而是日内中低频模型验证与模拟交易系统。

---

## 1. 核心结论与默认方向

### 1.1 历史验证优先于实时会员

正式花钱开实时行情会员前，先做三年历史数据验证：

```text
历史数据下载 → 数据清洗 → 特征工程 → 标签生成 → walk-forward 验证 → 回测 → 报告 → 决定是否进入实时 paper trading
```

如果三年历史走步验证无法表现出稳定正收益，则不建议进入实时数据会员阶段。

### 1.2 第一阶段不下真钱

所有模式默认禁止真实下单。

```yaml
live_trading:
  enabled: false
```

第一阶段只允许：

```text
historical_backtest
paper_trading
alert_only
```

### 1.3 模型不是直接预测四个 ETF

四个 ETF 高度相关，底层都是日经225方向。模型的核心任务不是“哪个 ETF 会涨”，而是：

```text
未来 5 / 15 / 30 分钟，日经方向是否足够明确，是否值得交易，应该用一倍还是两倍表达。
```

交易动作定义为五类：

```text
0 = flat，空仓
1 = long_1x，一倍做多
2 = long_2x，两倍做多
3 = short_1x，一倍做空
4 = short_2x，两倍做空
```

---

## 2. 默认交易标的配置

代码里只能写四类交易桶，不能硬编码具体 ETF 代码。

具体 ETF 代码从 `config/symbols.yaml` 读取。

默认配置：

```yaml
market:
  timezone: "Asia/Tokyo"
  currency: "JPY"

reference_symbols:
  index:
    symbol: "N225"
    name: "Nikkei 225 Index"
    enabled: true

  proxy_etf:
    symbol: "1321"
    role: "index_proxy"
    enabled: true

etf_universe:
  long_1x:
    description: "一倍做多 ETF"
    direction: 1
    leverage: 1
    candidates:
      - symbol: "1321"
        name: "NEXT FUNDS Nikkei 225 ETF"
        enabled: true
        priority: 1
        provider_symbol:
          jquants: "13210"
          twelvedata: "1321"
          yahoo: "1321.T"
          kabustation: "1321@1"

  long_2x:
    description: "两倍做多 ETF"
    direction: 1
    leverage: 2
    candidates:
      - symbol: "1570"
        name: "NEXT FUNDS Nikkei 225 Leveraged Index ETF"
        enabled: true
        priority: 1
        provider_symbol:
          jquants: "15700"
          twelvedata: "1570"
          yahoo: "1570.T"
          kabustation: "1570@1"
      - symbol: "1458"
        enabled: false
        priority: 2
      - symbol: "1579"
        enabled: false
        priority: 3

  short_1x:
    description: "一倍做空 ETF"
    direction: -1
    leverage: 1
    candidates:
      - symbol: "1571"
        name: "NEXT FUNDS Nikkei 225 Inverse Index ETF"
        enabled: true
        priority: 1
        provider_symbol:
          jquants: "15710"
          twelvedata: "1571"
          yahoo: "1571.T"
          kabustation: "1571@1"
      - symbol: "1456"
        enabled: false
        priority: 2
      - symbol: "1580"
        enabled: false
        priority: 3

  short_2x:
    description: "两倍做空 ETF"
    direction: -1
    leverage: 2
    candidates:
      - symbol: "1357"
        name: "NEXT FUNDS Nikkei 225 Double Inverse Index ETF"
        enabled: true
        priority: 1
        provider_symbol:
          jquants: "13570"
          twelvedata: "1357"
          yahoo: "1357.T"
          kabustation: "1357@1"
      - symbol: "1360"
        enabled: false
        priority: 2
      - symbol: "1459"
        enabled: false
        priority: 3
```

注意：J-Quants 的代码格式通常是 5 位，如日本股票 `7203` 常对应 `72030`。本项目不能假设所有 provider 的代码格式一致，必须通过 `provider_symbol` 和 `symbol_probe` 确认。

---

## 3. 数据源总览

### 3.1 历史数据优先级

用于三年历史回测，推荐顺序：

| 优先级 | 数据源 | 用途 | 说明 |
|---|---|---|---|
| 1 | J-Quants API | 日本 ETF 历史 1分钟 / 日线数据 | 最适合本项目的日本市场历史数据源 |
| 2 | Twelve Data | 云端历史 1分钟 / 实时 WebSocket | 适合云端实时与备用历史数据，XJPX 可能需要 Pro+ |
| 3 | EODHD | 历史 intraday 备选 | 需要验证 TSE ETF 支持情况和延迟属性 |
| 4 | Stooq | 免费历史指数/部分市场数据 | 适合补充日经指数日线/5分钟级别，不建议作为主源 |
| 5 | FRED | 免费日经225日收盘 | 只能做日线或宏观参考 |
| 6 | Yahoo Finance / yfinance | 非正式备选 | 只用于临时测试，不作为正式数据源 |

### 3.2 实时 paper trading 数据源优先级

| 优先级 | 数据源 | 类型 | 说明 |
|---|---|---|---|
| 1 | Twelve Data WebSocket | 云端实时 | 若 XJPX ETF 支持实时，适合不用本地软件 |
| 2 | kabuステーションAPI | 本地 REST + WebSocket | 需本地安装并登录 kabuステーション，适合日本 ETF 实时行情 |
| 3 | CSV replay | 本地回放 | 用历史数据模拟实时推送 |

---

## 4. 历史数据来源与实现细节

### 4.1 J-Quants API：历史数据主源

#### 定位

J-Quants API 是 JPX 面向个人投资者的数据 API，提供历史股价、公司财务等数据。官方说明包括历史股价、上市公司信息、调整前后 OHLC 等数据。2026年起，J-Quants 增强了 CSV 交付、1分钟分足和 tick 数据能力。

#### 用途

本项目中 J-Quants 作为：

```text
primary_historical_provider
```

用于下载：

```text
1321 / 1570 / 1571 / 1357 的 1分钟K线
备用 ETF 的 1分钟K线
日线 OHLCV
交易日历
上市代码映射
```

#### 关键 endpoint

Codex 实现时使用以下接口抽象；实际 endpoint 以最新 J-Quants v2 文档为准：

```text
GET /v2/equities/bars/minute
GET /v2/equities/bars/daily
GET /v2/listed/info
GET /v2/markets/trading_calendar
```

J-Quants 文档显示 `/equities/bars/minute` 提供 1分钟 OHLC、成交量、成交金额；`/equities/bars/daily` 提供日线 OHLC；免费、Light、Standard、Premium 计划的请求频率不同，分足/逐笔 add-on 有单独请求频率。

#### 代码实现要求

实现类：

```python
class JQuantsHistoricalDataProvider(HistoricalDataProvider):
    def list_symbols(self) -> list[Instrument]: ...
    def download_minute_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame: ...
    def download_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame: ...
    def download_trading_calendar(self, start: date, end: date) -> pd.DataFrame: ...
    def probe_symbol(self, symbol: str) -> ProbeResult: ...
```

配置：

```yaml
data_sources:
  historical_primary: "jquants"

jquants:
  enabled: true
  base_url: "https://api.jquants.com/v2"
  api_key_env: "JQUANTS_API_KEY"
  request_timeout_seconds: 20
  max_retries: 3
  retry_backoff_seconds: 2

  rate_limit:
    requests_per_minute: 60
    sleep_when_near_limit: true

  endpoints:
    minute_bars: "/equities/bars/minute"
    daily_bars: "/equities/bars/daily"
    listed_info: "/listed/info"
    trading_calendar: "/markets/trading_calendar"

  download:
    bar_interval: "1m"
    chunk_days: 5
    save_format: "parquet"
    adjust_prices: true
```

#### 请求参数设计

Codex 需要把请求参数做成可配置，不要写死。

示例伪代码：

```python
def download_minute_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
    provider_code = self.symbol_mapper.to_provider_symbol("jquants", symbol)
    params = {
        "code": provider_code,
        "from": start.isoformat(),
        "to": end.isoformat(),
    }
    return self.client.get_dataframe("/equities/bars/minute", params=params)
```

必须实现分页或 chunk 下载：

```text
三年 1分钟数据量较大，不能一次请求全部日期。
按 1~5 个交易日为 chunk 下载，保存到本地 parquet。
```

#### 数据字段规范化

J-Quants 返回字段可能与其他数据源不同，统一规范化为：

```text
timestamp
symbol
provider
open
high
low
close
volume
turnover
adjusted_open
adjusted_high
adjusted_low
adjusted_close
session
source_updated_at
```

#### 数据质量检查

下载后必须检查：

```text
是否有重复 timestamp
是否缺失开盘/收盘关键分钟
是否跨午休产生错误K线
是否 volume 为 null
是否价格为 0
是否存在明显异常跳价
是否 symbol 映射错误
```

---

### 4.2 Twelve Data：云端历史与实时备选

#### 定位

Twelve Data 提供股票、ETF、指数等 API、WebSocket 和 SDK；Tokyo Stock Exchange / XJPX 页面显示日本东京证券交易所，交易时段为 JST 09:00–11:30、12:30–15:30，并标注个人计划通常需要 Pro+ 级别。其股票页面说明 intraday 数据可从 2022 年起提供，区间从 1分钟到 8小时，实际开始日期和 interval 取决于市场。

#### 用途

Twelve Data 在本项目中作为：

```text
secondary_historical_provider
primary_cloud_realtime_provider
```

#### 关键点

1. REST API 使用 credits，每分钟重置。
2. WebSocket 使用 WS credits，通常按订阅 symbol 数量消耗，不是按推送条数消耗。
3. Basic 免费版可用于 `symbol_probe`，但不应假设能长期获取日本 ETF 实时数据。
4. 对 XJPX ETF 必须验证套餐权限、是否实时、是否支持 1分钟历史。

#### 配置

```yaml
twelvedata:
  enabled: true
  api_key_env: "TWELVE_DATA_API_KEY"
  base_url: "https://api.twelvedata.com"

  historical:
    enabled: true
    endpoint: "/time_series"
    interval: "1min"
    outputsize: 5000
    chunk_mode: true
    timezone: "Asia/Tokyo"

  websocket:
    enabled: true
    url: "wss://ws.twelvedata.com/v1/quotes/price"
    max_symbols: 8
    reject_if_delayed: true

  limits:
    api_credits_per_minute: 610
    ws_credits: 500
    stop_on_429: true
```

#### 实现类

```python
class TwelveDataHistoricalProvider(HistoricalDataProvider):
    def download_minute_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame: ...
    def probe_symbol(self, symbol: str) -> ProbeResult: ...

class TwelveDataRealtimeProvider(RealtimeMarketDataProvider):
    def subscribe(self, symbols: list[str]) -> None: ...
    def on_tick(self, callback: Callable[[Tick], None]) -> None: ...
```

#### symbol_probe 要求

对以下标的测试：

```text
1321
1570
1571
1357
1458
1579
1456
1580
1360
1459
```

验证字段：

```text
是否能查到 symbol
是否能返回最新价
是否能返回 1min 历史K线
timestamp 是否为日本交易时段
是否 delayed
是否有 volume
是否支持 WebSocket
```

---

### 4.3 EODHD：历史 intraday 备选

#### 定位

EODHD 提供历史 EOD、实时/延迟、intraday 等 API。官方说明其 intraday historical data 支持主要交易所，interval 包括 1分钟、5分钟和1小时；同时，其 live 示例常说明为 15分钟延迟数据。

#### 用途

本项目只把 EODHD 作为历史数据备选，不作为实时模拟主源，除非验证为实时。

配置：

```yaml
eodhd:
  enabled: false
  api_key_env: "EODHD_API_KEY"
  base_url: "https://eodhd.com/api"
  interval: "1m"
  reject_delayed_realtime: true
```

实现类：

```python
class EodhdHistoricalProvider(HistoricalDataProvider):
    def download_minute_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame: ...
    def probe_symbol(self, symbol: str) -> ProbeResult: ...
```

必须验证 TSE ETF 的代码格式，例如：

```text
1321.TSE
1321.T
1321.JP
```

不得假设代码格式。

---

### 4.4 Stooq：免费历史辅助源

#### 定位

Stooq 提供免费历史市场数据，包含 Nikkei 225 页面与 CSV 下载能力，也有 Daily、Hourly、5 Minutes 等历史数据库。适合作为：

```text
index_daily_backup
free_historical_sanity_check
```

不建议作为本项目核心 1分钟 ETF 数据源。

实现类：

```python
class StooqHistoricalProvider(HistoricalDataProvider):
    def download_index_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame: ...
    def download_index_5m_if_available(self, symbol: str, start: date, end: date) -> pd.DataFrame: ...
```

默认 symbol：

```yaml
stooq:
  enabled: true
  symbols:
    nikkei225: "^NKX"
```

---

### 4.5 FRED：免费日线参考源

#### 定位

FRED 的 `NIKKEI225` 是日经225每日收盘级别数据。适合：

```text
日线趋势参考
长期指数走势校验
历史日线补充
```

不适合日内交易回测。

实现类：

```python
class FredHistoricalProvider(HistoricalDataProvider):
    def download_nikkei_daily_close(self, start: date, end: date) -> pd.DataFrame: ...
```

配置：

```yaml
fred:
  enabled: false
  api_key_env: "FRED_API_KEY"
  series:
    nikkei225: "NIKKEI225"
```

---

### 4.6 kabuステーションAPI：实时本地源，不是历史主源

#### 定位

kabuステーションAPI 是本地软件开放的 localhost REST + WebSocket 接口，需要本地安装并运行 kabuステーション。它适合实时行情和将来可能的自动下单，但不是三年历史数据主源。

连接模式：

```text
Python 程序
→ localhost REST/WebSocket
→ 本机已登录 kabuステーション
→ 券商系统
```

配置：

```yaml
kabustation:
  enabled: false
  mode: "realtime_data_only"
  rest_base_url: "http://localhost:18080/kabusapi"
  websocket_url: "ws://localhost:18080/kabusapi/websocket"
  api_password_env: "KABUSTATION_API_PASSWORD"
  live_order_enabled: false
```

实现类：

```python
class KabuStationRealtimeProvider(RealtimeMarketDataProvider):
    def authenticate(self) -> str: ...
    def register_symbols(self, symbols: list[str]) -> None: ...
    def connect_websocket(self) -> None: ...
```

项目第一阶段不实现真实下单，只实现行情读取。

---

## 5. 项目结构

Codex 必须生成以下结构：

```text
nikkei-etf-paper-trader/
  README.md
  pyproject.toml
  requirements.txt
  .env.example

  config/
    symbols.yaml
    data_sources.yaml
    historical.yaml
    strategy.yaml
    labeling.yaml
    model.yaml
    backtest.yaml
    paper_account.yaml
    daily_review.yaml
    ai_review.yaml

  src/
    main.py

    config/
      loader.py
      schema.py

    data/
      providers/
        base.py
        jquants_provider.py
        twelvedata_provider.py
        eodhd_provider.py
        stooq_provider.py
        fred_provider.py
        yahoo_provider.py
        kabustation_provider.py
      symbol_mapper.py
      symbol_probe.py
      downloader.py
      cleaner.py
      validator.py
      resampler.py
      data_lake.py
      replay_feed.py

    features/
      feature_pipeline.py
      multi_timeframe.py
      momentum.py
      vwap.py
      volatility.py
      gap.py
      volume.py
      etf_deviation.py
      market_regime.py

    labeling/
      action_labeler.py
      future_return_labeler.py
      cost_aware_labeler.py

    models/
      base.py
      rule_based_model.py
      sklearn_model.py
      lightgbm_model.py
      xgboost_model.py
      model_registry.py
      train.py
      predict.py

    validation/
      walk_forward.py
      purged_split.py
      leakage_checks.py

    backtest/
      engine.py
      broker_simulator.py
      execution_model.py
      cost_model.py
      portfolio.py
      metrics.py
      report.py

    paper/
      account.py
      paper_execution_engine.py
      realtime_runner.py

    review/
      daily_review.py
      monthly_report.py
      ai_review_service.py
      candidate_config_generator.py

    utils/
      calendar.py
      logging.py
      paths.py
      time_utils.py
      serialization.py

  tests/
    test_symbol_mapper.py
    test_data_validator.py
    test_resampler.py
    test_features.py
    test_labeler.py
    test_walk_forward.py
    test_backtest_engine.py
    test_cost_model.py
    test_paper_account.py

  data/
    raw/
    normalized/
    features/
    labels/
    models/
    reports/
```

---

## 6. 数据下载与本地数据湖

### 6.1 数据存储格式

使用 Parquet，按 provider / symbol / interval / date 分区。

```text
data/raw/{provider}/{symbol}/{interval}/date=YYYY-MM-DD/*.parquet
data/normalized/{symbol}/{interval}/date=YYYY-MM-DD/*.parquet
data/features/{feature_set}/{symbol}/date=YYYY-MM-DD/*.parquet
```

### 6.2 标准 OHLCV schema

```python
@dataclass
class Bar:
    timestamp: pd.Timestamp
    symbol: str
    interval: str
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    turnover: float | None
    provider: str
    adjusted: bool
```

### 6.3 下载命令

Codex 要实现 CLI：

```bash
python -m src.main probe-symbols --config config/data_sources.yaml
python -m src.main download-history --provider jquants --start 2023-01-01 --end 2026-05-15 --interval 1m
python -m src.main normalize-data --start 2023-01-01 --end 2026-05-15
python -m src.main validate-data --start 2023-01-01 --end 2026-05-15
```

### 6.4 数据下载器流程

```text
1. 读取 symbols.yaml
2. 展开 enabled=true 的 ETF
3. 根据 provider_symbol 获取各 provider 的代码
4. 对每个 symbol 执行 probe
5. 对每个交易日分块下载
6. 保存 raw parquet
7. 规范化为统一 schema
8. 数据质量检查
9. 生成 data_quality_report.md
```

### 6.5 必须处理午休

东京市场午休：

```text
11:30 - 12:30 JST
```

特征与回测中不能把午休误当成连续分钟。

配置：

```yaml
market_calendar:
  timezone: "Asia/Tokyo"
  sessions:
    morning:
      start: "09:00"
      end: "11:30"
    afternoon:
      start: "12:30"
      end: "15:30"
```

---

## 7. 多周期特征工程

### 7.1 输入数据

优先使用：

```text
1321, 1570, 1571, 1357 的 1分钟K线
```

备用特征：

```text
日经225指数日线
1321 作为 index_proxy
USD/JPY，如有数据
日经期货，如有数据
```

### 7.2 周期

从 1分钟原始数据生成：

```text
1m
3m
5m
15m
30m
1d
```

### 7.3 特征列表

#### 动量特征

```text
return_1m
return_3m
return_5m
return_15m
return_30m
return_1d_prev
```

#### VWAP 特征

```text
intraday_vwap
price_vs_vwap_pct
vwap_cross_direction
vwap_cross_count_30m
```

#### 波动率特征

```text
range_5m_pct
range_15m_pct
range_30m_pct
realized_vol_15m
realized_vol_30m
candle_body_pct
upper_shadow_pct
lower_shadow_pct
```

#### 成交量特征

```text
volume_ratio_5m
volume_ratio_20m
turnover_ratio_20m
volume_spike_flag
```

#### 开盘区间特征

```text
opening_range_high_30m
opening_range_low_30m
breakout_opening_high
breakdown_opening_low
```

#### 缺口特征

```text
gap_pct
gap_direction
gap_filled_flag
gap_fill_distance_pct
```

#### ETF 偏离特征

理论涨跌幅：

```text
expected_etf_return = reference_return * direction * leverage
actual_etf_return = etf_current_price / etf_previous_close - 1
deviation = actual_etf_return - expected_etf_return
```

生成：

```text
long_2x_deviation_pct
short_1x_deviation_pct
short_2x_deviation_pct
```

---

## 8. 标签生成

### 8.1 标签目标

标签用于预测未来交易动作，不是简单预测涨跌。

动作类别：

```yaml
actions:
  flat: 0
  long_1x: 1
  long_2x: 2
  short_1x: 3
  short_2x: 4
```

### 8.2 未来收益标签

用参考标的计算未来收益，默认使用 1321 或日经225指数代理。

```text
future_return_h = reference_close[t+h] / reference_close[t] - 1
```

配置：

```yaml
labeling:
  reference_symbol: "1321"
  horizons_minutes: [5, 15, 30]
  primary_horizon_minutes: 15

  thresholds:
    weak_long_return_pct: 0.20
    strong_long_return_pct: 0.45
    weak_short_return_pct: -0.20
    strong_short_return_pct: -0.45
    neutral_abs_return_pct: 0.15
```

规则：

```text
future_return >= strong_long_return_pct  -> long_2x
future_return >= weak_long_return_pct    -> long_1x
future_return <= strong_short_return_pct -> short_2x
future_return <= weak_short_return_pct   -> short_1x
otherwise                                -> flat
```

### 8.3 成本感知标签

标签阈值必须大于交易成本。

配置：

```yaml
cost_aware_labeling:
  enabled: true
  estimated_round_trip_cost_pct:
    long_1x: 0.10
    long_2x: 0.16
    short_1x: 0.12
    short_2x: 0.20
```

如果未来收益不足以覆盖成本，则标记为 flat。

---

## 9. 模型设计

### 9.1 第一版模型

第一版至少实现：

```text
RuleBasedModel
LogisticRegressionModel
RandomForestModel
LightGBMModel，可选
XGBoostModel，可选
```

若 LightGBM / XGBoost 未安装，程序不能崩溃，应自动跳过。

### 9.2 不建议第一版上深度学习

第一版不要实现 LSTM / Transformer。原因：

```text
样本量不一定足够
过拟合风险高
解释性差
调参成本高
```

### 9.3 模型输出

模型每个 timestamp 输出：

```text
timestamp
predicted_action
prob_flat
prob_long_1x
prob_long_2x
prob_short_1x
prob_short_2x
confidence
```

只有 `confidence` 大于配置阈值才允许交易。

配置：

```yaml
model:
  type: "lightgbm"
  fallback_type: "random_forest"
  random_state: 42

  prediction:
    min_confidence: 0.55
    min_action_probability: 0.50

  features:
    exclude_future_columns: true
    drop_na: true
```

---

## 10. Walk-forward 验证

禁止用三年全部数据训练再回测三年。

必须实现走步验证：

```yaml
walk_forward:
  enabled: true
  train_window_months: 12
  test_window_months: 1
  step_months: 1
  start_date: "2023-01-01"
  end_date: "2026-05-15"
  retrain_each_window: true
  purge_minutes: 60
```

示例：

```text
2023-01 ~ 2023-12 训练，2024-01 测试
2023-02 ~ 2024-01 训练，2024-02 测试
2023-03 ~ 2024-02 训练，2024-03 测试
```

### 10.1 防止未来函数

必须实现检查：

```text
特征不能使用 t 之后数据
标签只能用于训练，不能进入特征
resample 不能把未来分钟聚合到当前分钟
日线特征只能使用昨日或更早日线，不能使用当天收盘日线
```

实现：

```python
class LeakageChecker:
    def assert_no_future_timestamps(self, features: pd.DataFrame, labels: pd.DataFrame): ...
    def assert_daily_features_lagged(self, features: pd.DataFrame): ...
```

---

## 11. 回测引擎

### 11.1 执行规则

不能用当前 K 线收盘价完美成交。

默认：

```text
信号在 t 生成
交易在 t+1 根K线开盘价成交
买入加滑点
卖出减滑点
```

配置：

```yaml
backtest:
  initial_cash_jpy: 1000000

  execution:
    signal_delay_bars: 1
    use_next_bar_open: true
    slippage_bps: 3
    fallback_spread_bps: 8
    allow_partial_fill: false

  cost:
    commission_enabled: true
    commission_rate_pct: 0.00
    fixed_commission_jpy: 0
```

### 11.2 仓位与风控

```yaml
risk:
  max_risk_per_trade_pct: 0.50
  max_daily_loss_pct: 1.50
  max_trades_per_day: 3
  max_consecutive_losses: 2

position_limits:
  long_1x:
    max_equity_pct: 60
  long_2x:
    max_equity_pct: 35
  short_1x:
    max_equity_pct: 60
  short_2x:
    max_equity_pct: 30

exit:
  stop_loss_pct:
    long_1x: 0.50
    long_2x: 1.00
    short_1x: 0.50
    short_2x: 1.00

  take_profit_r:
    weak_signal: 1.20
    strong_signal: 1.50

  max_holding_minutes: 60
  exit_if_no_profit_after_minutes: 30
  force_exit_time: "15:10"
  exit_on_opposite_signal: true
  exit_on_neutral_signal: true
```

### 11.3 交易桶选择

模型给出 action 后，`ETFSelector` 根据配置选具体 ETF：

```text
long_1x  -> 从 long_1x candidates 选 enabled=true 的最优 ETF
long_2x  -> 从 long_2x candidates 选 enabled=true 的最优 ETF
short_1x -> 从 short_1x candidates 选 enabled=true 的最优 ETF
short_2x -> 从 short_2x candidates 选 enabled=true 的最优 ETF
```

选择规则：

```text
1. enabled=true
2. 数据存在
3. 成交量/成交额足够
4. 理论偏离不超过阈值
5. priority 更高
```

### 11.4 回测指标

必须输出：

```text
总收益率
年化收益率，可选
最大回撤
胜率
盈亏比
Profit Factor
平均单笔收益
平均单笔亏损
最大单笔亏损
最大连续亏损次数
总交易次数
日均交易次数
盈利天数
亏损天数
空仓天数
按 action 分组收益
按 ETF 分组收益
按年份分组收益
按月份分组收益
按上午/下午分组收益
按趋势市/震荡市分组收益
```

---

## 12. Paper trading 模式

历史验证通过后，才能进入实时 paper trading。

### 12.1 模拟账户

```yaml
paper_account:
  enabled: true
  base_currency: "JPY"
  initial_cash: 1000000
  reset_policy: "never"

  execution_model:
    use_bid_ask: true
    fallback_spread_bps: 8
    buy_slippage_bps: 3
    sell_slippage_bps: 3

  portfolio:
    allow_multiple_positions: false
    max_open_positions: 1
    allow_opposite_position: false
```

### 12.2 Paper trading 数据流

```text
实时数据源 WebSocket / CSV replay
→ 1分钟 bar 聚合
→ 特征生成
→ 模型预测
→ 风控
→ 模拟成交
→ 持仓更新
→ 日志记录
```

### 12.3 模拟成交价格

买入：

```text
ask * (1 + buy_slippage_bps / 10000)
```

卖出：

```text
bid * (1 - sell_slippage_bps / 10000)
```

如果没有 bid/ask：

```text
买入 = close * (1 + fallback_spread_bps / 2 / 10000 + buy_slippage_bps / 10000)
卖出 = close * (1 - fallback_spread_bps / 2 / 10000 - sell_slippage_bps / 10000)
```

---

## 13. 每日 AI 复盘

### 13.1 功能

每天收盘后自动生成：

```text
daily_review.md
daily_review.json
signal_log.csv
trade_log.csv
account_state.json
config_snapshot.yaml
ai_review.md
ai_review_raw_response.json
candidate_config.yaml
```

### 13.2 AI 不能自动改正式参数

禁止：

```text
AI 建议参数 → 自动修改 strategy.yaml → 次日直接使用
```

允许：

```text
AI 建议参数 → 生成 candidate_config.yaml → 人工确认后才应用
```

### 13.3 AI prompt 模板

实现文件：

```text
src/review/ai_review_service.py
```

Prompt：

```text
你是一个量化交易系统复盘助手。

请根据以下数据，复盘今天的日经225 ETF 模拟交易。

要求：
1. 所有交易都是模拟交易。
2. 不要给出保证盈利的结论。
3. 区分单日偶然结果和统计上可能有意义的问题。
4. 不允许建议直接自动应用参数。
5. 所有优化建议必须说明原因、预期改善和潜在副作用。

输出结构：
# 今日总结
# 交易质量分析
# 信号分析
# 风控分析
# 参数优化候选
# 明日观察重点

今日数据：
{{daily_data_json}}
```

---

## 14. 配置文件完整清单

### 14.1 data_sources.yaml

```yaml
data_sources:
  historical_primary: "jquants"
  historical_fallback:
    - "twelvedata"
    - "eodhd"
    - "stooq"

  realtime_primary: "twelvedata"
  realtime_fallback:
    - "kabustation"
    - "csv_replay"

  reject_delayed_realtime: true

jquants:
  enabled: true
  api_key_env: "JQUANTS_API_KEY"
  base_url: "https://api.jquants.com/v2"

  rate_limit:
    requests_per_minute: 60

  download:
    chunk_days: 5
    save_format: "parquet"

  endpoints:
    minute_bars: "/equities/bars/minute"
    daily_bars: "/equities/bars/daily"
    listed_info: "/listed/info"
    trading_calendar: "/markets/trading_calendar"

twelvedata:
  enabled: true
  api_key_env: "TWELVE_DATA_API_KEY"
  base_url: "https://api.twelvedata.com"

  historical:
    enabled: true
    interval: "1min"
    timezone: "Asia/Tokyo"

  websocket:
    enabled: true
    max_symbols: 8
    reject_if_delayed: true

eodhd:
  enabled: false
  api_key_env: "EODHD_API_KEY"

stooq:
  enabled: true
  symbols:
    nikkei225: "^NKX"

fred:
  enabled: false
  api_key_env: "FRED_API_KEY"
  series:
    nikkei225: "NIKKEI225"

kabustation:
  enabled: false
  rest_base_url: "http://localhost:18080/kabusapi"
  websocket_url: "ws://localhost:18080/kabusapi/websocket"
  api_password_env: "KABUSTATION_API_PASSWORD"
  live_order_enabled: false
```

### 14.2 historical.yaml

```yaml
historical:
  start_date: "2023-01-01"
  end_date: "2026-05-15"
  intervals: ["1m", "3m", "5m", "15m", "30m", "1d"]
  base_interval: "1m"
  symbols_from_config: true
  include_disabled_candidates: false

  data_quality:
    fail_on_duplicate_timestamps: true
    fail_on_missing_core_symbols: true
    allow_missing_volume: false
    max_missing_bar_pct_per_day: 2.0
```

### 14.3 model.yaml

```yaml
model:
  type: "lightgbm"
  fallback_type: "random_forest"
  random_state: 42

  training:
    class_weight: "balanced"
    drop_na: true
    scale_features: false

  prediction:
    min_confidence: 0.55
    min_action_probability: 0.50
```

### 14.4 backtest.yaml

```yaml
backtest:
  initial_cash_jpy: 1000000

  execution:
    signal_delay_bars: 1
    use_next_bar_open: true
    slippage_bps: 3
    fallback_spread_bps: 8

  risk:
    max_trades_per_day: 3
    max_daily_loss_pct: 1.5
    max_consecutive_losses: 2

  report:
    output_dir: "data/reports/backtest"
    save_trades_csv: true
    save_equity_curve_csv: true
    save_markdown: true
```

---

## 15. CLI 命令要求

Codex 必须实现以下命令：

```bash
# 1. 检查数据源和 symbol
python -m src.main probe-symbols --config config/data_sources.yaml

# 2. 下载三年历史数据
python -m src.main download-history --start 2023-01-01 --end 2026-05-15 --provider jquants

# 3. 清洗并规范化数据
python -m src.main normalize-data --start 2023-01-01 --end 2026-05-15

# 4. 生成多周期特征
python -m src.main build-features --start 2023-01-01 --end 2026-05-15

# 5. 生成标签
python -m src.main build-labels --start 2023-01-01 --end 2026-05-15

# 6. 走步训练和验证
python -m src.main walk-forward --start 2023-01-01 --end 2026-05-15

# 7. 运行历史回测
python -m src.main backtest --model latest

# 8. 生成三年历史验证报告
python -m src.main report --type backtest

# 9. 用历史数据模拟实时回放
python -m src.main replay --date 2025-05-15

# 10. 实时 paper trading
python -m src.main paper-trade --provider twelvedata
```

---

## 16. 验收标准

Codex 生成的项目必须满足：

```text
1. 所有 ETF 代码从 symbols.yaml 读取。
2. 所有数据源配置从 data_sources.yaml 读取。
3. 所有模型、标签、回测、风控参数从配置文件读取。
4. 不允许在策略代码里硬编码 1321 / 1570 / 1571 / 1357。
5. 支持 J-Quants 历史数据下载器。
6. 支持 Twelve Data symbol probe。
7. 支持本地 parquet 数据湖。
8. 支持 1m → 3m/5m/15m/30m/1d 重采样。
9. 支持未来收益标签生成。
10. 支持 walk-forward 验证。
11. 支持防未来函数检查。
12. 支持成本、滑点、价差、下一根K线成交。
13. 支持按 action / ETF / 年月 / 市场状态统计收益。
14. 支持 paper trading 模拟账户。
15. live trading 默认关闭。
16. AI 只能生成候选优化建议，不能自动修改正式配置。
17. 单元测试覆盖数据、特征、标签、回测、风控。
18. README 包含完整安装、配置、运行说明。
```

---

## 17. 给 Codex 的最终实现提示词

将以下内容直接交给 Codex：

```text
请根据本规格书生成完整 Python 3.11 项目：nikkei-etf-paper-trader。

项目用于日经225相关 ETF 的三年历史数据验证、模型训练、walk-forward 回测、真实行情 paper trading 和每日 AI 复盘。

第一阶段禁止实盘下单。live_trading.enabled 必须默认 false。

请实现：
1. 配置化项目结构。
2. J-Quants 历史数据下载器，优先下载日本 ETF 1分钟分足和日线。
3. Twelve Data 历史/实时适配器，用于云端备选数据和 symbol probe。
4. EODHD、Stooq、FRED 的备选适配器。
5. 本地 parquet 数据湖。
6. 数据清洗、规范化、质量检查。
7. 多周期特征工程：1m、3m、5m、15m、30m、1d。
8. 标签生成：flat、long_1x、long_2x、short_1x、short_2x。
9. 模型训练：RuleBased、LogisticRegression、RandomForest，LightGBM/XGBoost 可选。
10. walk-forward 验证，禁止未来函数。
11. 回测引擎，包含滑点、价差、手续费、下一根K线成交、强制平仓、止损止盈。
12. paper trading 模拟账户。
13. 每日复盘和 AI 复盘服务。
14. CLI 命令。
15. 单元测试。
16. README。

所有参数必须从 YAML 配置读取，不允许硬编码交易标的、阈值、时间、数据源、模型参数。

生成代码时，请优先保证可运行、模块化、可测试。若某外部 API 的具体字段和文档不确定，请实现 provider 抽象和清晰 TODO，并通过 symbol_probe 输出需要人工确认的字段，不要在策略逻辑中做假设。
```

---

## 18. 参考资料与实现依据

以下链接供开发时核对 API 与数据范围。实际调用前必须重新查看官方文档和套餐权限。

1. J-Quants API / JPX：历史股价、上市公司信息、API 服务说明  
   https://www.jpx.co.jp/english/markets/other-data-services/j-quants-api/index.html

2. J-Quants API：1分钟分足 `/equities/bars/minute`  
   https://jpx-jquants.com/en/spec/eq-bars-minute

3. J-Quants API：日线 `/equities/bars/daily`  
   https://jpx-jquants.com/en/spec/eq-bars-daily

4. J-Quants API：请求频率限制  
   https://jpx-jquants.com/en/spec/rate-limits

5. J-Quants API：计划与数据期间  
   https://jpx.gitbook.io/j-quants-ja/outline/data-spec

6. JPX：J-Quants 2026 年 CSV、分钟线、tick 数据增强公告  
   https://www.jpx.co.jp/english/corporate/news/news-releases/6020/20260119.html

7. Twelve Data：Tokyo Stock Exchange / XJPX  
   https://twelvedata.com/exchanges/XJPX

8. Twelve Data：ETF API  
   https://twelvedata.com/etf

9. Twelve Data：credits / WebSocket credits  
   https://support.twelvedata.com/en/articles/5615854-credits

10. Twelve Data：WebSocket FAQ  
    https://support.twelvedata.com/en/articles/5194610-websocket-faq

11. EODHD：Intraday Historical Data API  
    https://eodhd.com/financial-apis/intraday-historical-data-api

12. EODHD：15分钟延迟实时数据示例  
    https://eodhd.com/financial-academy/how-to-get-stocks-data-examples/how-to-get-real-time-data-delayed-by-15-minutes-using-eodhd-apis-python-financial-library

13. Stooq：Nikkei 225 `^NKX`  
    https://stooq.com/q/d/?s=%5Enkx

14. Stooq：Free Historical Market Data  
    https://stooq.com/db/h/

15. FRED：NIKKEI225 daily close  
    https://fred.stlouisfed.org/series/NIKKEI225

16. kabuステーションAPI：开发者门户  
    https://kabucom.github.io/kabusapi/ptal/

17. kabuステーションAPI：WebSocket PUSH 说明  
    https://kabucom.github.io/kabusapi/ptal/push.html

18. 三菱UFJ eスマート証券：kabuステーションAPI 页面  
    https://kabu.com/item/kabustation_api/default.html

---

## 19. 风险说明

历史回测盈利不等于未来一定盈利。三年历史验证只能说明该模型在历史样本中具备继续测试价值，不能证明未来稳定盈利。上线真钱前必须经过：

```text
历史 walk-forward 验证
实时 paper trading
小资金人工跟单
再考虑自动化实盘
```

本项目所有默认设置均应防止误触发真实交易。
