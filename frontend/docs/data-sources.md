# 数据来源与在线失败排查

本文回答两个问题：

1. 当前项目到底用了哪些数据来源，分别在什么场景使用；
2. 为什么常见“在线数据获取不成功 / 来源不可用 / 数据缺口”，以及怎样定位。

结论先说：**大多数提示不是“整个系统断网”，而是某一条 provider 调用失败、返回空、缺少配置，或该市场本来没有覆盖。** 行情、新闻、SEC、网页搜索、本机研究数据是分开的，任何一条失败都不应该被理解成“所有数据都不可用”。

---

> 数据源质量分级、实测延迟和自动降级规则见
> [data-source-quality.md](data-source-quality.md)。
> 对话托底和模型缺口补全见 [conversation-fallbacks.md](conversation-fallbacks.md)。

## 1. 数据源总表

| 市场 / 场景 | 主来源 | 备选 / 降级 | 需要配置 | 代码入口 |
|---|---|---|---|---|
| A 股日/周/月 K 线 | **Fuyao / 同花顺**（日 K，前复权） | 新浪 `getKLineData`（不复权）→ 腾讯 `fqkline`（qfq）→ akshare `stock_zh_a_hist(qfq)` | `FUYAO_API_KEY`；未配置时自动走 legacy | `api/datasources/fuyao_provider.py`、`ashare/data.py` |
| A 股分钟线 | 腾讯分钟接口（1m） | 新浪分钟 → 腾讯分钟 | 无 | `Ashare.py`、`ashare/data.py` |
| A 股实时快照 / 技术分析 | 上述 K 线 + 本地指标计算 | 无 | 无 | `api/services.py`、`ashare/analyzer.py` |
| A 股代码/名称搜索 | 本地股票池 | 东财 suggest → 新浪 suggest | 无 | `ashare/search.py` |
| A 股公告 | 巨潮资讯（近 7 天） | 东财公告（akshare） | 无 | `ashare/monitor.py`、`api/local_research/providers.py` |
| A 股个股新闻 | 东财 `stock_news_em` | 财联社、财联社快讯、新浪、同花顺（akshare） | 无 | `ashare/monitor.py` |
| A 股资金流 / 人气 / 热点 | akshare 东财接口 | 无 | 无 | `ashare/monitor.py`、`api/services.py` |
| A 股一致预期 EPS | 同花顺 `worth.html` | 无 | 无（本机内部） | `api/local_research/providers.py` |
| A 股结构化财报 | **Fuyao / 同花顺**合并三表 | 新浪财报三表 | `FUYAO_API_KEY`；未配置时自动走 Sina | `api/datasources/fuyao_provider.py`、`api/local_research/providers.py` |
| A 股估值快照 | **Fuyao / 同花顺** PE/PB/PS/PCF | 无 | `FUYAO_API_KEY` | `api/datasources/fuyao_provider.py`、`get_valuations` |
| 指数成分 / 权重 | 中证官网（`csi`） | 国证官网（`cni`）→ Fuyao 指数成分 | 无（本机内部）；Fuyao 需 key | `api/local_research/providers.py`、`api/datasources/fuyao_provider.py` |
| 港股日 K / 快照 | yfinance（Yahoo，`0700.HK`，auto_adjust） | **无港股 Finnhub 兜底** | 无 | `ashare/data.py` |
| 港股新闻 | 同花顺 / akshare 中文源 | 财联社等 topic 级来源 | 无 | `ashare/monitor.py` |
| 美股日 K / 快照 | yfinance（Yahoo） | Finnhub `stock/candle`（需 key） | `FINNHUB_API_KEY` | `ashare/data.py` |
| 美股个股新闻 | Finnhub company-news | 无（未配置时为空） | `FINNHUB_API_KEY` | `ashare/monitor.py` |
| 美股综合新闻 | Finnhub general news | 无（未配置时为空） | `FINNHUB_API_KEY` | `ashare/monitor.py`、`api/services.py` |
| yfinance 统一适配 | yfinance 1.7.0 | A/港/美行情、历史、三表、目标价；新闻当前为空 | 无 key | `api/datasources/yfinance_provider.py` |
| yfinance 美/港财报与目标价 | yfinance | `get_company_fundamentals` / `get_analyst_estimates` | 无 key；`YFINANCE_US_HK_FUNDAMENTALS` 可关 | `api/agent_pydantic.py` |
| 美股财报 / 申报 | SEC EDGAR submissions + companyfacts | 无 | `SEC_USER_AGENT`（含邮箱） | `api/sec.py` |
| 通用网页搜索 | Brave Search API | Google News RSS → Bing RSS → DuckDuckGo HTML | `BRAVE_SEARCH_API_KEY` 可选 | `api/services.py` |
| 公开网页正文 | 直接 HTTP(S) 读取 | 无 | 无 | `api/web_research.py` |
| 美国监管公告（FCC 等） | Federal Register API | 无 | 无 | `api/services.py` |
| 宏观/指数/一致预期等历史扩展 | Vibe-Research 数据层参考 | 只选择性接入，不整包 vendor | 本机内部 | `api/local_research/`、`docs/vibe-research-data-sources.md` |
| iFinD | **主源**（A/H 行情、快照、估值、公告、成分；US 需显式交易所后缀） | Fuyao / 新浪 / 腾讯 / akshare / yfinance | `IFIND_ACCESS_TOKEN`；未配置或额度不足时自动降级 | `api/datasources/ifind_provider.py`、`api/datasources/ifind_work.py`、`ashare/data.py` |

补充：

- **iFinD / 同花顺 quantapi** 是 2026-11-02 起的最高优先数据源；`/bot` 工具与 `/work` 研究台共用 `IfindProvider`（见 `ifind_work.py`）。无 token 或接口失败时自动降级，不阻断页面。
- **Fuyao / 同花顺** 是 2026-10-30 明确批准的 A 股专用数据源，覆盖日 K、财报、财务指标、估值、交易日历、指数成分、标的检索；没有 key 时完全不发请求，自动回退到新浪/腾讯/akshare。详见 [fuyao-source-plan.md](fuyao-source-plan.md)。
- **yfinance** 已用于美/港行情（adapter 优先、旧 yf.download 兜底）、美/港财报与目标价；A 股仅在全部主链失败后使用 yfinance 兜底（raw close）。详见 [yfinance-source-plan.md](yfinance-source-plan.md)。
- 股票分析的 `metadata.data_plan` 会展示主源、实际使用、备源、复权和降级规则；所有工具还会生成 `data_plans`。
- `/bot` 运行过程会显示当前接口（来自任务事件 `source_refs`）；财报/目标价/公告/指数成分会以结构化数据卡展示；`/work` 也会在 Research Trace 中显示数据卡。
- **模型**（DeepSeek / OpenAI-compatible）只负责选择工具和综合，不是行情数据源；模型 key 与数据源 key 是两套配置。
- 所有行情都是**历史或延迟**数据，不是交易所实时流；界面会显示“历史／延迟”和 `data_as_of`，这不算失败。
- 数据源配置是**服务端环境变量**；不要把 key 写到前端、日志或仓库。

---

## 2. 调用方式

### 2.1 Agent 对话（`/bot`、`/work` 的自然语言问答）

```text
用户问题
  → 模型选择工具（search_stocks / get_stock_analysis / get_stock_news /
     get_sec_company_data / web_search / read_public_source /
     get_cninfo_announcements / get_consensus_estimates /
     get_index_constituents / get_financial_statements）
  → 工具实时取数
  → 每个工具返回 data + evidence + data_gaps + status
  → 模型综合；最终回答引用 evidence
```

关键点：

- 工具是**按问题选择**的，不会每次把所有来源都跑一遍。
- 工具失败时，模型应把失败写进 `data_gaps`，不能用记忆补造数字。
- 一个工具失败不代表其他工具失败；例如新闻为空但行情正常。

### 2.2 传统工作台（`/work` 的股票分析）

- 股票分析：K 线 + 指标 + 技术信号；
- 新闻：个股新闻 + 公告 + 市场快讯；
- 热点/组合：akshare 热点、港美股热度、组合行情；
- 市场状态：指数行情 + 规则/LLM 分析；
- 失败时页面显示“研究数据暂时不可用”或“数据获取耗时较长”。

### 2.3 Financial Bot 任务（`/bot` 的后台任务）

- 后台任务复用同一套 Agent 工具；
- 没有模型或模型不可用时，`fallback_research.py` 用腾讯价格快照做确定性降级；
- 任务事件流会暴露工具状态和数据缺口，便于回放。

---

## 3. 为什么经常看到“在线数据不成功”

按优先级排列：

### 3.1 可选 provider 没有配置（最常见）

截至 2026-10-29 本机检查：

| 配置 | 当前 | 影响 |
|---|---|---|
| `FINNHUB_API_KEY` | 未配置 | 美股新闻为空；美股行情只有 yfinance，没有 Finnhub 备源 |
| `BRAVE_SEARCH_API_KEY` | 未配置 | 只能用 Google News RSS / Bing RSS / DuckDuckGo 免费通道 |
| `SEC_USER_AGENT` | 未配置（需含邮箱） | 美股财报/申报工具直接不可用 |

因此，问“苹果最新新闻/财报”时出现“来源不可用/暂无新闻”是**配置结果**，不是代码随机坏掉。

### 3.2 公共网页接口本身不稳定

- 东财 `push2`、部分行情接口会主动断连；本机实测过 `em_stock_info` 返回 `RemoteDisconnected`；
- Yahoo/yfinance 会限流或返回空；
- DuckDuckGo 经常弹交互验证（`ValueError`），Google News/Bing 也可能被限；
- 巨潮、同花顺、中证官网是普通网页/文件接口，可能超时、改结构或做反爬；
- 财经网站正文可能有 WAF、付费墙、登录墙。

这些都属于 provider 侧，不是模型幻觉；代码应把它标成 `provider_error` / `data_gaps`，而不是伪装成“没有这条新闻”。

### 3.3 超时和单次尝试

- 网页搜索默认 `WEB_SEARCH_TIMEOUT=8` 秒；
- SEC 请求 12 秒、限流约 2 次/秒、缓存 1 小时；
- 本机研究接口 15–40 秒；
- 部分工具只有一次尝试，失败即标 unavailable。

网络抖动时会放大失败率。内部多人同时用同一个实例时，外部站点更容易限流。

### 3.4 市场覆盖本来就不完整

- 港股没有 Finnhub 兜底；
- 美股新闻依赖 Finnhub；
- A 股公告只查近 7 天（巨潮路径）；
- 港股/A 股新闻可能只有 topic 级关联，不是公司直接新闻；
- 非交易时段没有新行情，只有最后收盘/延迟数据；
- 指数成分是“最近公布快照”，不是实时篮子。

这些是**覆盖边界**，不是故障。

### 3.5 身份未确认导致工具被拦截

美股代码/公司名不明确时，系统会先要求 `search_stocks`/SEC 确认身份；未确认前，行情、新闻、SEC 工具会被标记 `identity_required`。这看起来像“取数失败”，实际是防止把数据套到错公司。

### 3.6 未配置的 provider 现在会被跳过

为避免把“没有 key”误报成“来源不可用”：

- 美股新闻在未配置 `FINNHUB_API_KEY` 时直接返回 `not_configured`，不再发起网络请求；
- SEC 在未配置 `SEC_USER_AGENT` 时不暴露/不调用 SEC 工具；
- Brave 在没有 key 时本来就不发起请求，继续使用免 key 的搜索通道；
- UI 把这些显示为“未配置（已跳过）”，而不是红色“来源不可用”。

### 3.7 少数调用失败不再拖垮整组

`read_public_source` 这类工具一次会读很多来源。旧逻辑只要最后一次失败，整组显示“来源不可用”。现在按调用次数统计：

- 有成功、有失败 → `部分完成 24/27`，保留已完成结果；
- 全部失败 → `来源不可用`；
- 全部未配置 → `未配置（已跳过）`；
- 最终 `data_gaps` 写成“3 次调用中 1 次失败，2 次成功”，不再写成整体失败。

### 3.8 调用时显示当前接口

研究运行过程中，执行中的工具会显示：

```text
正在获取  [ cninfo.com.cn ]  ●●●
```

完成后的工具组显示 `接口: example.com · example.org +1`，并单独列出失败/跳过次数。接口名来自后端 SSE 的 `meta.endpoint`，不把完整 URL 或第三方正文塞进 UI。

### 3.9 模型结构化输出失败

模型偶尔会输出自然语言而不是结构化 `final_result`，此前会整段丢失。现在会保留可见文本并标为**部分完成**，同时保留来源；这属于研究综合问题，不是数据源失败。

### 3.7 UI 文案把不同情况混在一起

当前界面可能显示：

- `来源不可用`（provider 调用失败）
- `数据缺口`（工具返回的 gap，可能是无覆盖/无 key/空结果）
- `当前数据源暂无新闻`（调用成功但 0 条）
- `历史／延迟`（正常的行情状态）
- `研究综合未完成`（模型/结构化输出问题）

它们不是同一类问题。排查时先看**是哪个工具**、**status/reason_code**、**data_gaps**。

---

## 4. 如何快速定位

### 4.1 看 `/api/bot/status`

```bash
curl -s http://127.0.0.1:8000/api/bot/status | python3 -m json.tool
```

重点看：

- `provider_config.finnhub` / `brave_search` / `sec_contact`：是否配置；
- `local_data.enabled` / `scope`：本机研究数据是否启用；
- `data_sources`：各市场的主源/备源说明；
- `model.configured`：模型是否配置（与数据源无关）。

### 4.2 看一次任务的 `data_gaps` 和 `research_steps`

- `research_steps[].tool`：哪个工具；
- `status=unavailable` + `reason_code=provider_error`：provider 失败；
- `reason_code=no_results`：调用成功但没有结果；
- `reason_code=identity_required`：身份未确认；
- `data_gaps`：具体缺口文本。

### 4.3 看服务端日志

```bash
# 前台启动时直接看终端；后台任务可用 job_output 或日志文件
grep -E "failed|unavailable|timeout|RemoteDisconnected|429|403" <log>
```

日志只应保留异常类型/状态码，不应打印 key 或完整第三方正文。

### 4.4 直接跑单源探针

```bash
cd /Users/yaoxingyu/文稿/工作面试/financial-bot/Financial Bot
.venv/bin/python - <<'PY'
from api.services import StockAnalysisService, NewsService, WebSearchService
from ashare.config import Config

for code in ("sh600519", "00700.HK", "US.AAPL"):
    try:
        r = StockAnalysisService().get_stock_analysis(code, include_ai=False)
        print(code, "quote OK", r.metadata.get("provider"), r.metadata.get("currency"), len(r.chart_series))
    except Exception as e:
        print(code, "quote FAIL", type(e).__name__, str(e)[:120])

for code in ("sh600519", "00700.HK", "US.AAPL"):
    try:
        rows = NewsService().get_stock_news(code, limit=5)
        print(code, "news", len(rows))
    except Exception as e:
        print(code, "news FAIL", type(e).__name__, str(e)[:120])

print(WebSearchService().search_report("贵州茅台 2026 半年报", limit=5))
PY
```

### 4.5 建议的配置方式

在启动 API 的进程环境里设置（推荐用仓库根目录 `.env`，由 `scripts/run_api.sh` 加载；**不要提交**）：

```bash
FUYAO_API_KEY=...                   # 同花顺 Fuyao，A 股专用；服务端环境变量
FUYAO_ENABLED=1                     # 总开关；0 时即使有 key 也不用
FUYAO_PRIMARY_A_SHARE=1             # A 股日 K 主源；0 时只用 legacy
FUYAO_FINANCIALS=1                  # A 股财报主源；0 时只用 Sina
IFIND_ACCESS_TOKEN=...              # 同花顺 iFinD quantapi；/bot 与 /work 共用
IFIND_ENABLED=1                     # 总开关；0 时即使有 token 也不用
IFIND_PRIMARY=1                     # 能力优先走 iFinD；0 时跳过
FINNHUB_API_KEY=...                 # 免费/付费 Finnhub key
BRAVE_SEARCH_API_KEY=...            # 可选，提升网页搜索稳定性
SEC_USER_AGENT="YourName your@email"  # SEC 要求，不是 key；必须是真实联系方式
WEB_SEARCH_TIMEOUT=12               # 可选，网络慢时提高
```

修改后重启 API；`/api/bot/status` 里对应布尔值应变为 `true`。不要把 key 发给模型或写进前端。

---

## 5. 当前设计上的已知弱点

1. **没有统一的重试/退避**：部分工具一次失败即 unavailable。
2. **没有统一健康检查页面**：只能从任务事件和服务日志看。
3. **caching 不统一**：新闻/搜索/SEC 有 TTL，行情缓存是进程内且无 TTL，本机研究数据暂无 TTL。
4. **失败文案太粗**：`provider_error` 不区分“未配置 / 超时 / 反爬 / 空结果”。
5. **美股/港股覆盖依赖单一 provider**：没有 Finnhub 时，美股新闻为空、美股行情无备源；港股无备源。
6. **多开发共用一个本地实例**：没有账号隔离；并发高时更容易触发外部限流。

---

## 6. 建议的改进顺序

1. **配置 Finnhub / Brave / SEC contact**：成本最低，直接消除三类“来源不可用”。
2. **统一 provider 健康检查**：启动时/每日一次检查配置与连通性，结果只读展示，不阻塞主流程。
3. **把失败原因写进 UI**：区分“未配置”“超时”“反爬/403”“空结果”“覆盖不支持”。
4. **对本机研究数据加 5–15 分钟 TTL 缓存**，降低多人重复请求。
5. **对 A 股/港股/美股新闻增加明确覆盖矩阵**，没有覆盖就显示“未覆盖”，不再显示“失败”。
6. **给行情加最后成功快照回退**：显示“上次成功数据 + 时间”，而不是空白。
7. **多用户前先做限流和 provider 配额**；当前只适合内部少量开发低频使用。

---

## 7. 相关文档

- [Vibe-Research 数据源尽调](vibe-research-data-sources.md)
- [Financial Bot MVP](financial-bot-mvp.md)
- [Pydantic AI 美股研究](pydantic-us-research.md)
