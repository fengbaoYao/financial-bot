# 同花顺 Fuyao A 股数据源接入

## 决策

2026-10-30 明确接入 **同花顺金融数据 API（Fuyao）**，作为 A 股数据源。

此前的“yfinance 是最后一个新增 provider”冻结策略，在本数据源上做一次明确例外：

- Fuyao 只覆盖 A 股；
- yfinance 继续负责美股 / 港股；
- 现有新浪 / 腾讯 / akshare / 巨潮链路保留为 fallback；
- 未配置 `FUYAO_API_KEY` 时，Fuyao 完全不发请求，现有路径不受影响。

## 已接入能力

| 能力 | Fuyao 端点 | 接入点 |
|---|---|---|
| 标的检索 | `GET /api/meta/tickers/search` | `FuyaoProvider.search` |
| 行情快照 | `GET /api/a-share/prices/snapshot` | `FuyaoProvider.quote` |
| 日 K / 历史 | `GET /api/a-share/prices/historical` | `FuyaoProvider.history`、`DataFetcher` |
| 利润表 | `GET /api/a-share/financials/income-statements` | `financial_statements` |
| 资产负债表 | `GET /api/a-share/financials/balance-sheets` | `financial_statements` |
| 现金流量表 | `GET /api/a-share/financials/cash-flow-statements` | `financial_statements` |
| 财务指标 | `GET /api/a-share/financials/indicators` | `FuyaoProvider.financial_indicators` |
| 估值快照 | `GET /api/a-share/valuations/snapshot` | `get_valuations` |
| 交易日历 | `GET /api/a-share/calendar/trading-days` | `FuyaoProvider.trading_days` |
| 指数成分 | `GET /api/a-share-index/constituents/ths-stock-list` | `FuyaoProvider.index_constituents` |

基金、期货、期权、资讯、龙虎榜、涨跌停等端点已在官方文档中存在，但当前不进入 Financial Bot 的默认工具集，避免一次接入过多能力造成语义和授权边界不清。

## 配置

服务端环境变量：

```bash
FUYAO_API_KEY=...          # 必填；X-api-key
FUYAO_ENABLED=1            # 总开关
FUYAO_PRIMARY_A_SHARE=1    # A 股日 K 主源
FUYAO_FINANCIALS=1         # A 股财报主源
```

- key 只存在服务端进程环境；
- 不写入前端、日志、任务事件或数据库；
- 请求头使用 `X-api-key`；
- 429 / `code=4001` 视为限流，不立即连续重试；
- 业务错误 `code` 原样保留，由工具转成明确的 `data_gaps` / `unavailable`。

## 降级顺序

### A 股日 K

```text
Fuyao（配置 key 且 FUYAO_PRIMARY_A_SHARE=1）
  → 新浪
  → 腾讯 qfq
  → akshare
  → yfinance A 股 fallback（仅最后）
```

周 / 月线由 Fuyao 日 K 本地重采样，不另发请求。

### A 股财报

```text
Fuyao（配置 key 且 FUYAO_FINANCIALS=1）
  → 新浪财报三表
```

两条路径返回同一个 `periods / items` 结构，前端卡片不区分供应商。

### 估值

```text
Fuyao 估值快照
  → 未配置：not_configured
  → 无数据：明确 data_gaps
```

不把空结果包装成“低估值/合理估值”。

## 代码位置

```text
api/datasources/fuyao_provider.py     REST 客户端、缓存、字段归一化
api/datasources/flags.py             Fuyao 开关
api/datasources/registry.py          provider 注册
ashare/data.py                       A 股日 K 主链路
api/local_research/providers.py      财报 Fuyao → Sina fallback
api/agent_pydantic.py                get_valuations 工具与数据卡
```

## 验证

测试不访问真实 API，使用注入的 fake session：

```bash
.venv/bin/python -m pytest tests/test_fuyao_provider.py tests/test_fuyao_integration.py -q
```

覆盖：

- 代码映射（sh/sz/bj）；
- 无 key 时不发请求；
- 行情快照字段归一化；
- 历史 K 线字段归一化；
- 财务报表字段映射；
- 估值快照；
- 业务错误 / 429；
- provider registry；
- DataFetcher 优先 Fuyao；
- 财报优先 Fuyao。

## 当前边界

- 没有真实 `FUYAO_API_KEY` 时无法做 live 验证；所有测试都是契约/fake 测试。
- Fuyao 日 K 当前只支持 `1d`；周/月为本地重采样。
- 港股、美股不走 Fuyao。
- 基金、期货、期权、资讯等未默认启用。
- 行情是延迟/最后成交数据，不是交易所实时流。

## Live smoke（2026-10-30，已脱敏）

在本地 `.env`（600 权限、已被 `.gitignore` 忽略）配置真实 key 后验证：

- `600519.SH` 行情快照：`price=1371.51`，`previous_close=1354.0`，`as_of=2026-10-30`；
- 近 45 天日 K：返回 27 根，最后一根 `2026-10-30 close=1371.51`，`adjustment=qfq`；
- 2025/2024 年报利润表：`2025-12-31 revenue=168,838,102,514.79`、`net_profit_parent=82,320,067,101.68`、`eps_basic=65.66`；
- 估值：`600519.SH pe_ttm=21.053856 / pb_mrq=6.823781`，`000858.SZ pe_ttm=23.298526 / pb_mrq=2.398046`；
- 集成路径：`DataFetcher.fetch_stock_data("sh600519", count=5)` 返回 5 行，`provider_info == {"provider": "fuyao", "adjustment": "qfq"}`；
- `financial_statements("sh600519", "income", 2)` 使用 `provider=fuyao`，报告期 `2025-12-31 / 2024-12-31`；
- `/api/bot/status` 显示 `fuyao: true`，响应中不包含 `api_key` 字符串。

真实 key 只存在于本机服务端环境，不写入仓库、前端、任务事件或日志。
