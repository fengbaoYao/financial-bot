# 对话托底与缺口处理

## 1. 托底目标

当模型或数据源失败时，用户应该得到以下之一：

1. 有来源的事实快照；
2. 明确说明缺什么，而不是编造；
3. 可执行的下一步（重试/续跑/补充标的）。

“有提示但无内容”不算托底。

## 2. 已补齐

### 2.1 无模型时的财务/估值兜底

[`api/financial_bot/finance_fallback.py`](../api/financial_bot/finance_fallback.py)

- 识别财报、营收、利润、现金流、资产负债、PE/PB/PS/PCF 等问题；
- 解析公司，支持单公司和对比；
- 财报走 `financial_statements`（Fuyao → Sina）；
- 估值走 Fuyao 估值快照；
- 输出报告期、原币金额、来源、`data_as_of`、缺口；
- 不计算合理价值，不编造数字。

### 2.2a 缺失字段的模型数据补齐

[`api/financial_bot/data_gap_fill.py`](../api/financial_bot/data_gap_fill.py)

数据源全部或部分失败后，默认调用**当前所选模型**（`chatgpt` / `deepseek`）尝试补齐结构化字段：

- 环境变量 `LLM_DATA_GAP_FILL`（未设时回退 `LLM_GAP_FILL`，默认开）；
- 补齐写入 `research_steps` 工具行 `model_knowledge_fill`（主展示）与 `evidence`（`freshness=unverified_model`）；不再用答案下蓝卡 `data_cards[kind=model_fill]` 作主入口；
- **不会**覆盖已有 provider 数据，**不会**编造 URL/引用 ID；
- `data_gaps` / `_meta.llm_data_gap_fill` 明确标注“模型知识补齐，未经数据源核验”。

### 2.2b 工具失败 / 综合中止时的研究回答兜底

[`api/financial_bot/gap_synthesis.py`](../api/financial_bot/gap_synthesis.py) 的 `recover_incomplete_synthesis`

- 任意工具失败、空结果、超时、不可用，或研究综合中止（「研究综合未完成」）时触发；
- 调用**当前所选模型**再写一次可用回答；
- 保留已成功工具证据，不覆盖；不编造 URL / 引用 ID；
- 摘要标明「模型知识补齐（未核验）」；`_meta.llm_research_recovery=true`；
- deep / quick 均适用；`LLM_DATA_GAP_FILL` / `LLM_GAP_FILL=0` 可关闭。

### 2.2 无证据时的模型兜底

[`api/financial_bot/gap_synthesis.py`](../api/financial_bot/gap_synthesis.py)

只在“模型/工具失败且没有任何 evidence”时触发，`LLM_GAP_FILL=0` 可关闭。

严格约束：

- 不编造数字、日期、公司事实、来源或因果；
- 只解释缺少什么、通用分析框架、需要哪些一手来源；
- 响应标记 `synthesis_incomplete` 和 `unverified`；
- 任务状态为 partial，不显示成 completed；
- 如果模型不可用，退回 evidence fallback 或明确错误。

### 2.3 指数成分兜底

当官方 CSI/CNI 失败时，`get_index_constituents` 尝试 Fuyao 指数成分：

- 保留 official-first；
- Fuyao 只给成员清单，权重缺失会明确写入 `data_gaps`；
- 不把无权重结果说成加权指数。

### 2.4 持久化 last-good

[`api/datasources/last_good.py`](../api/datasources/last_good.py)

- 成功行情、A 股财报、Fuyao 估值写入 `data/last-good-cache.json`（mode 600，已 gitignore）；
- 所有对应实时源失败时返回最近一次成功快照；
- 明确标记 `stale=true`、原始 `provider`、`data_as_of`；
- 行情：`StockAnalysisService.metadata.price_status = "stale_cache"`；
- 财报/估值：`data_gaps` 明确写“last-good 缓存”，财务兜底摘要也显示警告；
- 行情默认最大 30 天（`LAST_GOOD_MAX_AGE_HOURS`），财报默认 180 天（`LAST_GOOD_FINANCIALS_MAX_AGE_HOURS`）；
- 最多 200 个键，只是读数兜底，不伪装成实时数据。

### 2.5 计划未完成自动重试一次

[`api/financial_bot/executor.py`](../api/financial_bot/executor.py)

- 首次回答后，如果任务清单仍是 `partial/failed`，自动用 `resume=True` 重跑一次；
- 保留首次部分结果和计划快照；
- 重试后再次按真实工具结果校验计划；
- 只自动重试一次，不会无限循环；
- `AUTO_RETRY_PLAN=0` 可关闭；
- 自定义 `agent_runner` 不自动重试，避免测试替身或嵌入方被意外调用两次。

### 2.6 Agent 直接异常重试与用量合并

- 生产 Agent 在返回响应前直接抛异常时，也会自动重试一次；
- 第一次异常不会直接把任务标成 failed，重试成功可继续完成；
- 两次调用的 `input_tokens / output_tokens / cost_usd` 会合并写入任务用量；
- 如果重试失败，保留原始异常链并记录失败事件；
- 取消操作不会被自动重试。

### 2.7 价格/新闻/搜索原有兜底

- A 股日 K：Fuyao → 新浪 → 腾讯 → akshare → yfinance；
- A 股财报：Fuyao → 新浪；
- 新闻：巨潮/东财/财联社/新浪/同花顺；
- 搜索：Brave → Google/Bing/DDG discovery；
- 熔断：3 次失败/180 秒；
- 结构校验：坏 OHLC / 空财报期不进入回答。

## 3. 仍然没有完全托底的情况

| 场景 | 现状 | 说明 |
|---|---|---|
| 巨潮公告工具失败 | 已自动降级东方财富公告 | 东财是二级聚合源，会保留 provider 与缺口 |
| 同花顺一致预期失败 | 已接东方财富盈利预测 fallback | 东财缺 min/max，保留 provider 和缺口 |
| SEC filing 失败/未配置 | 无替代 | 需 SEC contact 或 web 原文 |
| 美股/港股财报 | 已接东方财富财报 fallback | 二级聚合源，不能替代 SEC/HKEX |
| 美股/港股目标价 | 仍只有 yfinance | 无可靠第二结构化源 |
| 估值 | 只有 Fuyao | 本地无法安全计算 PE/PB |
| 所有价格源都失败 | 已接持久化 last-good，返回 stale 快照 | 若缓存也过期则无行情 |
| 财报/估值源失败 | 已接持久化 last-good | 若缓存过期则明确缺口 |
| 计划任务失败 | 自动重试一次；仍失败才转 partial/failed | 没有自动换模型 |
| Agent 直接异常 | 自动重试一次，合并两次用量 | 不会切换模型供应商 |
| 公司身份不确定 | 澄清 | 这是有意暂停，不是失败 |

## 4. 原则

**模型可补齐缺失字段，但必须标明未核验；不能伪装成数据源证据。**

数据源失败时允许一次所选模型补齐；主展示为工具时间线里的 `model_knowledge_fill`（未核验），并进入缺口说明。recovery / gap-fill 允许继续发起工具调用，结果并入同一工具轨迹。若模型也不确定，保留“无法确认 + 缺哪个来源”。禁止把模型记忆写成 iFinD/Fuyao/yfinance/SEC，禁止伪造 URL。

## 5. 配置

```bash
LLM_GAP_FILL=1          # 无证据时允许模型做未核验的缺口说明；0 关闭
LLM_DATA_GAP_FILL=1     # 部分/全部数据缺失时用所选模型补齐字段；未设则跟随 LLM_GAP_FILL
FUYAO_FINANCIALS=1
FUYAO_PRIMARY_A_SHARE=1
```

## 6. 验证

```bash
.venv/bin/python -m pytest tests/test_finance_fallback.py tests/test_data_source_quality.py -q
```

全量后端：423 passed / 2 failed（既有 CORS `Vary: Origin`）。

## 7. 模型 failover 与一手单源边界

### 模型 failover

当前没有自动切换模型供应商：

- 自动重试仍调用同一个 `llm_base_url / llm_model / key`；
- 这是有意的成本与密钥边界；
- 如需 failover，必须单独配置第二套模型凭据，并确认双倍调用成本；
- 不能在没有第二凭据时假装已经切换。

### 一手单源能力

以下能力没有可靠的第二结构化来源，正确行为是明确缺口：

- **SEC EDGAR filing**：官方一手；聚合源不能替代。
- **HKEX 公告/披露**：当前没有接入第二个官方结构化源。
- **美股/港股分析师目标价**：目前只有 yfinance；没有可靠第二源。
- **A 股官方公告**：巨潮优先；东财只是二级聚合。

这些边界不是“忘记做”，而是避免把二手网页或模型记忆伪装成一手事实。
