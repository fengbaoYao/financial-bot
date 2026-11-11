# yfinance 数据源设计（已确认版）

> 2026-10-30 更新：产品明确批准 **Fuyao / 同花顺 A 股数据源** 作为一次例外接入。
> `FROZEN_AFTER` 已改为 `fuyao`，yfinance 继续负责美股/港股；A 股优先走 Fuyao，
> 现有新浪/腾讯/akshare/yfinance 链路保留为 fallback。详见 `docs/fuyao-source-plan.md`。

## 结论

采用**混合模式**：

- 美股/港股：yfinance 作为主源；
- A 股：保留现有新浪/腾讯/akshare 实时与中文链路，yfinance 只作第二源和交叉校验；
- 新闻：保留现有新闻+公告，yfinance 新闻只作补充；
- **yfinance 是最后一个新增 provider**，之后冻结新增；现有数据源作为 legacy 兜底保留。

本文件记录设计、当前落地范围（Phase 1–2）和后续切换条件。

---

## 1. 目标架构

```text
用户命令
  → 意图/市场/证券解析
  → DataPlan（能力计划）
  → Capability Router：primary → secondary
  → 字段级合并与 provenance
  → 回答 + 数据计划 + 缺口
```

工具绑定**数据能力**，不绑定 vendor：

```text
get_quote / get_history / get_financials / get_estimates /
get_news / get_announcements / get_index_constituents
```

---

## 2. 选择矩阵

| 能力 | A 股 | 港股 | 美股 |
|---|---|---|---|
| 行情/历史 | 新浪 → 腾讯(qfq) → akshare 主 | yfinance 主 | yfinance 主 |
| 财报 | 新浪财报主；yfinance 第二 | yfinance 主 | yfinance 主 |
| 一致预期/目标价 | 同花顺主；yfinance 补充 | yfinance 主 | yfinance 主 |
| 公告 | 巨潮 | 现有中文源 | SEC（配置联系方式时） |
| 新闻 | 现有中文新闻 | 现有中文新闻 | 现有新闻；Finnhub 有 key 时 |
| 指数成分 | 中证/国证 | — | — |
| 指数行情 | 现有链路 | yfinance | yfinance |
| 分红/拆股 | 现有 | yfinance | yfinance |

yfinance 不覆盖 A 股公告、同花顺一致预期、中证/国证成分、中文新闻；这些保留现有实现。

---

## 3. 降级规则

```text
primary
  → same-capability secondary
  → cached last-good（标记 stale）
  → partial fields
  → not_configured / not_supported（中性，不报红）
```

允许：

- 美股/港股行情 yfinance 失败 → Finnhub（有 key）或缓存；
- 美股/港股财报 yfinance 失败 → 缓存；
- A 股财报新浪缺失字段 → yfinance 补字段；
- A 股行情 yfinance 只做第二源/校验，**不把 yahoo adjusted 冒充 qfq**。

禁止：

- 不同币种且没有带日期汇率；
- qfq 与 Yahoo adjusted 混合；
- 不同报告期/财年直接同比；
- 身份未确认；
- 用户明确要求官方公告时用新闻替代。

状态：

| 状态 | UI |
|---|---|
| `not_configured` | 未配置（已跳过） |
| `not_supported` | 该来源不覆盖 |
| `empty` | 成功但无结果 |
| `stale` | 缓存/旧数据，显示日期 |
| `provider_error` / `timeout` / `rate_limited` | 暂时不可用 |
| `partial` | 部分完成，保留成功字段 |

限制：每能力最多 2 个 provider、每个 provider 最多重试 1 次、429 退避/熔断。

---

## 4. yfinance 适配器（已实现）

文件：`api/datasources/yfinance_provider.py`

支持：

- 代码映射：`sh600519 → 600519.SS`、`sz000001 → 000001.SZ`、`HK.00700 → 0700.HK`、`US.AAPL → AAPL`、`^GSPC`；
- 北京证券交易所暂不支持（未验证）；
- `quote()`：`fast_info`，返回价格/昨收/开高低/成交量/市值/币种/交易所；
- `history()`：`auto_adjust=False`，同时保留 `close` 与 `adj_close`；
  - `close` 为原始价；
  - `adj_close` 标记 `yahoo_adjusted`，不映射成 qfq；
- `financials()`：利润表/资产负债表/现金流量表，年度/季度；
- `estimates()`：目标价与评级；
- `news()`：yfinance 新闻；当前实测为空，返回 `empty`，不抛错；
- `actions()`：分红/拆股；
- 进程内 TTL 缓存、并发上限、provenance/SHA-256、结构化错误。

实测（2026-10-29，yfinance 1.7.0）：

| 标的 | 行情 | 历史 | 三表 | 目标价 | 新闻 |
|---|---|---|---|---|---|
| 600519.SS | ✅ CNY | ✅ | ✅ | ✅ | 0 条 |
| 0700.HK | ✅ HKD | ✅ | ✅ | ✅ | 0 条 |
| AAPL | ✅ USD | ✅ | ✅ | ✅ | 0 条 |

结论：yfinance 适合做美/港行情、历史、财报、目标价；**不能单独承担新闻**。

---

## 5. Shadow 对比（已实现）

文件：`api/datasources/shadow.py`

- 默认**关闭**，不改变任何回答；
- 开启：

```bash
YFINANCE_SHADOW=1
YFINANCE_SHADOW_DIR=output/yfinance-shadow   # 可选
YFINANCE_SHADOW_SAMPLE=1                     # 可选，0–1
```

- 在 `StockAnalysisService.get_stock_analysis` 生成新响应后异步运行；
- 使用主源 `data_as_of` 对齐同一天，不使用“最新一天”硬比；
- 记录：
  - 主源 provider / currency / adjustment / as_of / close；
  - yfinance symbol / currency / as_of / compare_date / close；
  - 币种、复权、日期是否对齐；
  - `close_diff_pct`；
  - 错误类型。
- 写入 `output/yfinance-shadow/YYYY-MM-DD.jsonl`；不进入用户答案。

本地实测（2026-10-29）：

- AAPL：主源 `yfinance(AAPL)`，yfinance 对比 **0.0000%**；
- 00700.HK：主源 `yfinance(0700.HK)`，**0.0000%**；
- 600519：主源 `sina`，按 2026-10-28 对齐后 **0.0000%**；
- 多只 A 股/港股对比同样对齐到同日，未发现价格差异；
- 说明“按日期对齐”是必要的：早上 A 股主源可能还是前一交易日，而 yfinance 已有次日数据。

---

## 6. 当前执行范围

已完成：

- [x] Phase 1：yfinance adapter、符号映射、缓存、provenance、单元测试；
- [x] Phase 2：shadow 对比、JSONL、feature flag、状态展示；
- [x] Phase 3a（增量能力，不切行情主源）：
  - 美股/港股财报：`get_company_fundamentals` 走 yfinance；
  - 美股/港股目标价/评级：`get_analyst_estimates` 走 yfinance；
  - A 股仍走新浪财报/同花顺一致预期；
  - 开关：`YFINANCE_US_HK_FUNDAMENTALS=1`（默认开，设 `0` 关闭）；
- [x] Phase 3b：美股/港股行情优先走 yfinance adapter；旧 `yf.download` 保留为兜底；
  - 开关：`YFINANCE_PRIMARY_US_HK=1`（默认开）；
  - 解决 DST/时区混用：adapter 输出按交易所本地日期归一；
- [x] Phase 4：A 股 yfinance 第二源 + DataPlan UI；
  - A 股主链仍是新浪 → 腾讯(qfq) → akshare；
  - 只有全部主链失败后，才尝试 yfinance adapter（raw close，`adjustment=none`）；
  - 开关：`YFINANCE_A_SHARE_FALLBACK=1`（默认开）；
  - 股票分析 `metadata.data_plan` 与 `/bot` 的“数据计划”展示主源/实际使用/备源/复权/规则；
  - 所有工具生成 `data_plans`，财报/目标价/公告/指数成分生成结构化 `data_cards`；
  - `/bot` 任务事件的 `source_refs` 携带 `endpoint`，运行过程显示接口；
- [x] Phase 5：`api/datasources/registry.py` 冻结注册表，`FROZEN_AFTER=yfinance`，测试防止未登记新 provider；
- [x] 文档与测试。

**未做/保持**：

- [ ] 删除任何 legacy 数据源（按产品决定保留）；
- [ ] 美股/港股行情完全移除旧 `yf.download` 回退（当前保留为故障兜底）；
- [ ] 对 yfinance 财报币种/报告期做更深的自动校验（当前标记 proxy）。

---

## 7. 后续切换条件

进入 Phase 3 前需要满足：

1. shadow 至少覆盖 50 个美股/港股标的，日期/币种对齐率 100%；
2. 价格差异 > 0.5% 的记录全部人工复核；
3. 财报报告期/币种差异可解释；
4. 429/限流率在可接受范围；
5. 现有新闻/公告回退不受影响；
6. 用户确认切换窗口。

Phase 3 只切换美股/港股主源；A 股仍以现有实时链路为主。
