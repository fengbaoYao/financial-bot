# 数据源质量与降级规则

## 1. 什么算“优质数据源”

不存在一个跨全部能力的绝对排名。**质量必须按能力判断**：官方公告源在公告上很强，但拿不到行情；行情源也不能替代财报。

本项目按以下七维打分（1–5）：

| 维度 | 含义 | 高分标准 |
|---|---|---|
| 权威性 authority | 数据是否来自官方/一手 | 交易所、SEC、巨潮、指数公司、上市公司披露 |
| 结构化 structure | 字段、单位、时间戳、复权语义 | 显式字段、ISO/毫秒时间、currency、adjustment |
| 时效 freshness | 数据新鲜度 | 实时或明确标注延迟/最后成交 |
| 稳定性 stability | 实际成功率、延迟、限流 | 多次实测成功、无交互挑战、无频繁断连 |
| 可追溯 provenance | 是否能解释数据从哪里来 | request_id、data_as_of、原始响应哈希、显式缺口 |
| 覆盖 coverage | 市场、历史深度、品种 | 满足当前 A/港/美股与研究场景 |
| 合规 compliance | key、授权、服务端边界 | 服务端密钥、授权清晰、不把 key 暴露给浏览器 |

加权：

```text
authority 26% + structure 20% + freshness 15% +
stability 15% + provenance 14% + coverage 10%
```

但分数只是参考；**官方性、时效和合规可以一票否决**。例如网页搜索即使响应很快，也不能当财报事实来源。

## 2. 2026-10-30 实测结果

本次共 32 个 provider/能力：26 个成功、3 个失败、3 个未配置；新增的东方财富一致预期/美股财报/港股财报 3 个降级源均成功。

探测脚本对 32 个 provider/能力执行了只读 smoke，记录在：

- [data-source-probe-2026-10-30.json](data-source-probe-2026-10-30.json)

关键结果：

| 数据源 | 能力 | 实测 | 延迟 | 结论 |
|---|---|---|---:|---|
| Fuyao quote | A 股快照 | OK | 144ms | 主源 |
| Fuyao history | A 股日 K | OK，27 根 | 69ms | 主源 |
| Fuyao financials | 利润表 | OK，2025 年报 | 160ms | 主源 |
| Fuyao valuations | PE/PB/PS/PCF | OK，2 只 | 62ms | 唯一结构化估值源 |
| Fuyao ticker search | 标的检索 | OK | 76ms | 主源候选 |
| Fuyao calendar | 交易日历 | OK，9 天 | 64ms | 主源 |
| Fuyao index | 沪深 300 成分 | OK，300 只 | 82ms | 次源（官方 CSI 优先） |
| 新浪 history | A 股日 K | OK，5 行 | 241ms | 降级 |
| 腾讯 history | A 股日 K | OK，6 行 | 259ms | 降级 |
| akshare history | A 股日 K | **失败**，RemoteDisconnected | 306ms | 降级，保留 |
| yfinance US | 美股 | OK，5 行 | 1.9s | 美股权威延迟源 |
| yfinance HK | 港股 | OK，5 行 | 268ms | 港股主源 |
| CNINFO | A 股公告 | OK，3 条 | 5.5s | 公告官方源，慢但优先 |
| 同花顺 worth | 一致预期 | OK，3 年 | 608ms | 一致预期主源 |
| 东方财富盈利预测 | A 股一致预期降级 | OK，4 年 | 2.9s | 二级，缺 min/max |
| 东方财富美股财报 | 美股财报降级 | OK，2 期 | 1.5s | 二级聚合源 |
| 东方财富港股财报 | 港股财报降级 | OK，2 期 | 508ms | 二级聚合源 |
| 中证指数 | 沪深 300 成分 | OK，300 只 | 3.4s | 标准指数官方主源 |
| 国证指数 | 国证成分 | **失败**，Excel 不可解析 | 265ms | 降级，不宣称成功 |
| 新浪财报 | A 股利润表 | OK，2026H1 | 346ms | 财报备源 |
| 东方财富新闻 | A 股个股新闻 | OK，10 条 | 197ms | 新闻次源 |
| 财联社 | 全市场新闻 | OK，20 条 | 324ms | 新闻次源，必须过滤实体 |
| 新浪新闻 | 全市场新闻 | OK，20 条 | 403ms | 新闻降级 |
| 同花顺新闻 | 全市场新闻 | OK，20 条 | 393ms | 新闻降级 |
| 东方财富 suggest | 标的搜索 | OK，1 条 | 3ms | 搜索次源 |
| Google News RSS | 网页发现 | OK，3 条 | 713ms | 发现层 |
| Bing RSS | 网页发现 | OK，3 条 | 253ms | 发现层 |
| DuckDuckGo | 网页发现 | **失败**，交互挑战 | 321ms | 仅发现，默认不依赖 |
| Brave | 网页搜索 | 未配置 | — | 有 key 时最高优先级搜索 |
| Finnhub | 美股新闻/行情备源 | 未配置 | — | 未配置不发请求 |
| SEC EDGAR | 美股 filing | 未配置 | — | 官方 filing，有 contact 才调用 |

## 3. 按能力的优先级

优先级定义在：

[`api/datasources/quality.py`](../api/datasources/quality.py)

| 能力 | 第一优先 | 第二 | 降级/兜底 |
|---|---|---|---|
| A 股日 K | Fuyao | 新浪 → 腾讯 | akshare → yfinance |
| A 股快照 | Fuyao | 新浪/腾讯 | — |
| A 股财报 | Fuyao | 新浪 | — |
| A 股估值 | Fuyao | — | — |
| A 股公告 | 巨潮 CNINFO | 东方财富 | — |
| A 股一致预期 | 同花顺 worth | 东方财富盈利预测 | — |
| 指数成分 | 中证 CSI | 国证 CNI → Fuyao | — |
| A 股新闻 | 巨潮公告（若有） | 东财 | 财联社 → 新浪 → 同花顺 |
| 美股/港股行情 | yfinance | Finnhub（美股，需 key） | — |
| 美股/港股财报 | yfinance | 东方财富财报 | — |
| 美股 filing | SEC EDGAR | — | — |
| 网页搜索 | Brave（需 key） | Google News RSS | Bing RSS → DuckDuckGo（仅发现） |
| 标的搜索 | 本地股票池 | Fuyao | 东财 → 新浪 |

## 4. 自动处理规则

### 4.1 配置门

- 没有 `FUYAO_API_KEY`：Fuyao 请求数 = 0，直接走新浪/腾讯/akshare；
- 没有 `FINNHUB_API_KEY`：美股新闻不请求 Finnhub，返回 `not_configured`；
- 没有 `BRAVE_SEARCH_API_KEY`：不请求 Brave，走 RSS；
- 没有 SEC contact：不请求 SEC。

“未配置”不是“来源不可用”，不能伪装成红错。

### 4.2 熔断

[`api/datasources/health.py`](../api/datasources/health.py)：

```text
连续 3 次 provider/capability 失败
  → 打开 180 秒熔断
  → 该能力直接跳过此 provider
  → 成功一次立即关闭
```

- 只对真正的 provider 故障（超时、429、5xx、非 JSON、非法结构）计数；
- 业务性“无数据”不应当被当作供应商宕机，但会触发 capability 的 fallback；
- 进程内状态，重启清零；不是外部监控替代品。

### 4.3 结构校验

[`api/datasources/validation.py`](../api/datasources/validation.py)：

- 行情：日期有效、按时间升序、OHLC 数值有限、high/low 与 OHLC 不矛盾、成交量非负；
- 财报：报告期有效且不重复、items 非空、不能全为 null、数值可解析；
- 校验失败 → 抛 provider 错误 → 下一数据源；不把坏数据写入回答。

### 4.4 严格降级顺序

- 高优先级成功时，不再调用低优先级；
- 高优先级超时/空/结构错误时，才降级；
- 每个结果保留 `provider / endpoint / data_as_of / adjustment / raw_sha256`；
- 不允许“用了新浪却标成 Fuyao”；不允许把低优先级结果包装成官方结论。

### 4.5 事实与发现分层

- CNINFO/SEC/CSI/CNI/Fuyao 结构化数据可作为事实；
- 东财/财联社/新浪/同花顺新闻可作为线索，但必须保留媒体名与链接；
- Google/Bing/DDG/Brave 的结果只是 discovery，必须继续读取原文；
- 搜索摘要不能直接当财报、公告或行情数字。

## 5. 基于实测的调整

1. **Fuyao 提升为 A 股行情/财报/估值第一优先**：延迟 62–160ms，字段完整，有 key 时直接使用。
2. **akshare 降级**：本次实测 RemoteDisconnected；仍保留，但不作为默认主源。
3. **国证 CNI 降级**：本次 Excel 无法解析；标准指数先 CSI，再 Fuyao，不宣称 CNI 成功。
4. **DuckDuckGo 降为 discovery**：交互挑战；默认不依赖，Bing/Google 优先。
5. **CNINFO 不因 5.5s 降级**：公告官方性优先；但设置更长超时，并在失败时明确缺口。
6. **Brave/Finnhub/SEC 保持高优先级但受配置门控制**：未配置时不发请求。
7. **网页搜索不直接当证据**：即使 Brave 命中，也必须读取公开原文。

## 6. 当前边界

- 实测是本机 2026-10-30 单次快照，不能代表长期 SLA；
- 外部网页接口随时可能改结构；
- 没有真实 Fuyao key 时无法验证 live；
- 东方财富一致预期/美股财报/港股财报均为二级降级源，不能替代一手披露；
- SEC filing、HKEX 公告仍是一手单源，不能用聚合源伪装官方；
- 美股/港股分析师目标价仍只有 yfinance；
- 本规则是产品内部分级，不等于数据供应商的官方 SLA 或授权结论；
- 对外 hosted/商业使用前仍需单独确认数据授权。

## 7. 边界

- 模型供应商没有自动 failover；自动重试仍使用同一供应商。
- SEC/HKEX 官方披露、分析师目标价仍是一手单源；聚合源不能替代。
- 东方财富一致预期/美股财报/港股财报只作为二级降级源，必须保留 provider 和缺口。
