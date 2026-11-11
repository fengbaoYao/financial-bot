# Vibe-Research 数据源尽调：能否接入 Financial Bot

- **尽调对象**：[`simonlin1212/Vibe-Research`](https://github.com/simonlin1212/Vibe-Research)
- **固定引用点（commit SHA）**：`db3a7c60afcb643abdf36933294765d1cbfc2b9c`（`release: v2.2.4`，2026-10-26 16:01:52 +0800）
- **分析方式**：只读克隆到 `/tmp/vibe-research/Vibe-Research`（`git clone --depth 1`），静态代码/文档分析 + 2026-10-28 本机 10 次**零鉴权**探针（8 个成功、2 个失败；§2.7）；**未调用任何需付费/需凭据的端点、未读取或输出任何真实 key；报告撰写阶段未修改业务代码，后续落地见 §11.1**
- **报告日期**：2026-10-28
- **本文所有 GitHub 链接均指向上述 SHA**，行号以该 commit 为准；Financial Bot 侧链接为仓库内相对路径。

---

## 0. 结论（先看这一节）

### 0.1 一句话判断

**可以接，但只能按「参考实现 + 选择性移植」接，不能整包 vendor。**
本机 2026-10-28 对 10 次零鉴权调用做了探针：8 个成功（腾讯行情/HK 报价/同花顺一致预期/腾讯 K 线/巨潮公告/FRED/Yahoo 一致预期/Yahoo K 线），东财 `em_stock_info` 因网络断连失败，legacy 端点经通用入口调用失败；**技术可接，但可用性必须逐端点实测，且数据授权未解决。**
Vibe-Research 的数据层是把 30 多个公开站点/接口的**抓取代码**封装成 168 个端点，其中 159 个零鉴权；它的最大价值不是"多了几个数据源"，而是**一整套数字可回溯的证据信封（`source / endpoint / as_of / currency / adjustment / raw_ref / sha256`）和 fail-open 规则**——这恰是 Financial Bot 目前缺的那一层。
它的最大风险是**授权**：代码是 MIT，但**数据不是**；上游自述"各源服务条款/再分发/商用许可尚未审核"，且 registry 里 2 个端点标为"C = 仅个人研究"、13 个标为"B = 非官方/个人研究"。这些等级在**单用户本地工具**里成立，在**托管多用户产品**里不成立。

### 0.2 三条必须记住的结论

1. **代码许可 ≠ 数据授权。** Vibe-Research 本身 MIT（[LICENSE#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/LICENSE#L1)），但它整包移植了 `a-stock-data`（**Apache-2.0**）与 `global-stock-data`（**Apache-2.0**）的代码，仓库里**没有对应的 NOTICE / Apache 许可副本**（只有 `backtest/NOTICE.md` 覆盖另一个 MIT 上游）。直接复制进 Financial Bot 会同时带进 Apache-2.0 的署名义务和第三方数据的授权义务。
2. **它自己明确说"不许据此主张可发布或再分发"。** 数据层手册写死：这些端点是"公开网页/接口的零鉴权用法，**仅限 Phase 0 内部验证**；各源的服务条款/再分发/商用许可尚未审核，不得据此主张可发布或再分发"（[SKILL.md#L68](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/SKILL.md#L68)）。这是本次尽调最重要的单条证据。
3. **接入点是"适配器 + 证据映射"，不是"换数据源"。** Financial Bot 已有的行情链路（新浪 K 线 → 腾讯 → akshare；yfinance → Finnhub）与 Vibe-Research 高度重叠，**边际价值集中在它有的、Financial Bot 没有的那 8 类**：一致预期、财报三表/关键指标、公告结构化+PDF、研报列表、宏观官方序列、指数成分/权重/估值、港股财报与派息、期权与另类数据。

### 0.3 适合接 / 不建议接

| 类别 | 判断 | 理由 |
|---|---|---|
| 公告（巨潮/深交所/东财 np-anotice） | ✅ **优先接** | 官方或准官方、结构化、带 PDF 直链、可追溯到 `announcementId`；Financial Bot 目前只有 akshare 封装的公告列表 |
| 财报三表 / 关键指标（新浪、东财港美股） | ✅ 接 | Financial Bot A 股只有日线，没有结构化财报；这是能力空白 |
| 一致预期 EPS（同花顺/东财研报） | ✅ 接，但必须报机构数与 min/max | 免费源里少见的"预期"数据；口径脆弱，必须带元数据 |
| 指数成分/权重/估值（中证/国证官网文件） | ✅ 接 | 官方文件，风险最低，适合做股票池 |
| 宏观官方序列（中债/货币网/人行/统计局/FRED/财政部/CFTC） | ✅ 接 | `S` 级官方源，限流明确，许可最清晰 |
| 日 K / 快照交叉校验（腾讯/新浪/baostock） | ⚠️ 只做**校验源** | Financial Bot 已有主链路，重复接入只增加故障面 |
| 研报**全文 PDF**（东财 `pdf.dfcfw.com`） | ⚠️ 只存链接与元数据 | 研报版权属券商，落盘再展示=再分发 |
| Yahoo / FINRA / Nasdaq（`B` 级） | ⚠️ 内部用，标注"非官方" | 非官方接口，条款未核，随时可能失效 |
| CBOE 期权链（`C` 级） | ⛔ 不接 | registry 自己标"仅个人研究" |
| 雪球/股吧正文 | ⛔ 不接 | 源站 WAF 拦截，只能拿索引元数据；UGC 版权与内容风险高 |
| iWencai 问财（需 key） | ⛔ 暂不接 | 唯一需要真实 API key 的商业接口，配额/资费/商用条款全部未知 |
| DRAM 现货（GitHub 社区转录 DRAMeXchange） | ⛔ 不接 | 二手转录第三方付费数据，商用风险最高 |
| RSS 106 源全量搬运 | ⚠️ 只做"标题+链接+时间" | 正文版权归各媒体；需逐源看 robots/条款 |

---

## 1. 本次尽调做了什么、没做什么

| 项目 | 状态 |
|---|---|
| 只读克隆到 `/tmp/vibe-research/Vibe-Research` | ✅ 完成，`git status` 干净 |
| 读 README / LICENSE / 依赖清单 / 上游对账单 / 端点目录 | ✅ 完成 |
| 逐文件读 `sources/*.py`、`core/*.py`、`datasources/registry.json` | ✅ 完成 |
| 统计端点、source 标识、主机名、上游服务组 | ✅ 完成（口径见 §2.1） |
| 读 Financial Bot 数据层 4 个文件 + 相关服务/证据模块 | ✅ 完成 |
| 真实调用 provider | ✅ **10 次零鉴权探针**（8 个成功、2 个失败；§2.7）；SEC/FINRA/CBOE/需 key 端点未调用 |
| 读取/输出/猜测任何真实 API key | ❌ 全程未读取任何 `.env`、密钥文件或环境变量值 |
| 修改 Financial Bot 业务代码 / 部署 / 推送 | 报告阶段未做；小范围本地适配器见 §11.1，未部署/推送 |

---

## 2. 仓库基本面

### 2.1 规模与结构（可复核口径）

| 指标 | 数值 | 复核方式 |
|---|---|---|
| 端点总数 | **168** | `datasources/registry.json` → `endpoints` 长度 |
| 数据层（layer） | **35** | `set(endpoint.layer)` |
| `source` 标识 | **52** 个不同字符串 | `set(endpoint.source)` |
| 实际主机名 | **88** 个 | `sources/` + `core/` 里 `https?://` 去重 |
| 归并后的上游服务组 | **约 49 个** | 按主机名归并（含 5 家期货交易所、106 个 RSS 源、2 个零 key 检索/取页通道） |
| 合规分级 | `cn-public` 129 / `S` 11 / `B` 13 / `C` 2 / 其他公开免费 13 | `endpoint.compliance` 计数 |
| 需要凭据的端点 | **9**（`VRA_SEC_CONTACT` 7 + `IWENCAI_API_KEY` 2） | `endpoint.auth_env` 计数 |
| 零鉴权端点 | **159** | 同上 |
| legacy 独立脚本端点 | 8 | `endpoint.module == "legacy"` |
| 进六阶段研究计划的端点 | 62 | `endpoint.stages` 非空 |
| 默认禁用端点 | 0 | `enabled == false` 计数 |

> 口径说明：52 是 registry 里的 `source` 字符串数（含 `tencent+baostock`、`szse+eastmoney` 这类组合源）；88/49 是我按主机名归并的结果，用于说明"到底碰了多少家外部站点"，不是仓库自述数字。仓库对外自述只有"168 个端点、35 层"（[README.md#L243](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/README.md#L243)）。

### 2.2 代码许可与上游

| 层 | 许可 | 证据 |
|---|---|---|
| Vibe-Research 主仓 | **MIT**，`Copyright (c) 2026 simonlin1212` | [LICENSE#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/LICENSE#L1)、[README.md#L629](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/README.md#L629) |
| `backtest/` 引擎 | MIT（上游 HKUDS/Vibe-Trading），**有** NOTICE | [backtest/NOTICE.md#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/backtest/NOTICE.md#L1) |
| 数据层（`sources/`、`astock_ported.py`） | 移植自 `a-stock-data` **v3.10.1 (`8f6a6a5`)**、`global-stock-data` **v2.0.3**、`investment-news` **v1.0.3** | [datasources/UPSTREAM.md#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/datasources/UPSTREAM.md#L1) |

上游仓库的公开许可（2026-10-28 经 GitHub API 核对）：`a-stock-data` = **Apache-2.0**，`global-stock-data` = **Apache-2.0**，`investment-news` = **MIT**。

⚠️ **缺口**：仓库里**没有**为 Apache-2.0 移植代码保留 NOTICE/License 副本；`astock_ported.py`（约 3000 行、由 `datasources/port_astock.py` 生成）文件头只写了上游版本与 commit，没有 Apache 许可声明。按 Apache-2.0 §4，分发衍生作品需随附许可副本并保留声明。**这条要在 Financial Bot 里补，或者干脆不复制那份文件。**

### 2.3 依赖（决定"能不能塞进 Financial Bot"）

| 清单 | 内容 | 证据 |
|---|---|---|
| 后端编排器 | Node ≥ 22.18；`@modelcontextprotocol/sdk`、`ajv`、`mammoth`、`pdfjs-dist`、`zod` | [orchestrator/package.json#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/orchestrator/package.json#L1) |
| 取数脚本 | Python ≥ 3.11；`requests`、`pandas`、`lxml`、`akshare>=1.12`、`baostock>=0.9`、`mootdx==0.11.7` | [requirements.txt#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/requirements.txt#L1) |
| 前端 | React + Vite（`desktop/`，只是目录名，不是原生客户端） | [docs/source-delivery.md#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/docs/source-delivery.md#L1) |

与 Financial Bot 的依赖冲突评估：本次实测 Financial Bot 环境为 Python **3.12.0**、pandas **3.1.0**、akshare **1.20.1**、yfinance **1.7.0**；仓库声明的是下限 `pandas>=2.2.0`、`akshare>=1.17.24`、`yfinance>=0.2.36`（[requirements_api.txt](../requirements_api.txt)）。Vibe-Research 的 `akshare>=1.12` 与这些下限**不冲突**；`lxml`、`baostock` 是新增依赖，`mootdx==0.11.7` 仅通达信两个端点用（可不装）。**没有 Node 依赖是必需的**——168 个端点全部由 Python 脚本完成，编排器只是调度与校验。

### 2.4 架构定位：本地单用户工具

这是**全部授权判断的前提**，必须写清楚：

- 默认绑定 `127.0.0.1`，写请求要鉴权（[README.md#L436](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/README.md#L436)）；
- 浏览器里填的 key 只存在**当前浏览器 localStorage**，经本机后端转发给模型服务商；后端默认 key 只走环境变量；
- 研究阶段的取数由编排器用受控脚本完成，原始响应落盘并记 hash；
- 数据根目录 `.local/` 是本机私有目录（[vibe-research.config.json#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/vibe-research.config.json#L1)）。

**结论**：它的合规模型是"一个人、一台机器、自己看"。搬到 Financial Bot 这种有 Supabase 用户体系、可托管、可多人的形态，`cn-public / B / C` 三级标记的含义都会变，必须逐源重新判。

### 2.5 两套取数路径：`legacy` 脚本 vs `registry` 通用取数器

这是接入时最容易走错的一处——**168 个端点不是一种写法**，分成两条完全不同的路径，只有第二条适合移植。

| | 路径 A：`legacy`（Phase 0 遗留） | 路径 B：`registry` 通用取数器（Phase 1 起） |
|---|---|---|
| 端点数量 | **8 个** | **160 个** |
| 清单 | `fetch_quote` / `fetch_profile` / `fetch_financials` / `fetch_estimates` / `fetch_pe_history` / `fetch_announcements` / `fetch_kline` / `fetch_trade_calendar` | 其余全部（东财 60、新浪 12、Yahoo 9、腾讯 8、SEC 7…） |
| 代码 | 独立脚本 `.agents/skills/data-access/scripts/fetch_*.py`，`module="legacy"` | `sources/<module>.<function>` + `sources/mappers*.py`，由 `fetch_endpoint.py` 动态导入 |
| 入口 | 直接 `python3 fetch_quote.py --symbol … --out-dir …` | `python3 fetch_endpoint.py --endpoint <id> --symbol … [--args '{…}'] --out-dir …` |
| 取数实现 | `common.py`（`em_get`/`save_raw`）+ **akshare** 封装 | `sources/_http.py`（`http_get`/`em`/`official_get`/`yahoo_get`，全部带 raw 捕获） |
| 映射/证据 | 脚本内部直接产出 evidence | 源函数只返结构化结果，evidence 由 mapper 统一生成 |
| 注册表登记 | `module="legacy"`，`function="fetch_xxx.py"` | `module`/`function` 指向真实 Python 函数 |
| 进研究计划 | 是（`stages` 多为 `required`） | 仅 62 个端点有 `stages`，其余按需调用 |
| 同源异构 | `fetch_financials` 走 akshare 的新浪摘要 | `sina_income_statement` 直连新浪 `getFinanceReport2022` |

**两条路径的共同契约**：都输出同一个信封
`script / symbol / market / status(ok|partial|failed) / fetched_at / primary_source / used_sources[] / evidence[] / extra{} / errors[] / missing[]`，
退出码统一 `0=ok / 2=partial / 3=failed`（[SKILL.md#L79](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/SKILL.md#L79)）。

**接入建议**：
- **只移植路径 B**。路径 A 的 8 个端点（行情/概况/财务/预期/PE 历史/公告/K 线/日历）与 Financial Bot 现有链路几乎完全重叠，移植过来只是把 akshare 换成直连，收益低于成本。
- 路径 B 里**再筛 6 个 provider、8–12 个端点**（见 §6 Phase 1），不要按 160 个全接。
- 路径 A 的 `fetch_estimates`（同花顺一致预期）是唯一有独立价值的 legacy 脚本——它没有对应的 registry 端点，接的时候要把它的解析逻辑搬成路径 B 的 `sources/ths.py` 风格。

### 2.6 可用性：已有证据 vs 本次实测

§2.7 是本机 10 次零鉴权探针（8 个成功、2 个失败）；下表是 Vibe-Research 作者自己记录的探针结果，只能当"作者在某天某网络下跑过"的补充证据，不能替代 Financial Bot 环境的逐端点验收。

| 时间 | 范围 | 结果（作者自述） | 来源 |
|---|---|---|---|
| 2026-10-08 上午（交易时段） | a-stock-data v3.10.1 新增 33 端点 | 30 个 ok；`tx_ticks` partial（盘中无法核对完整）；`st_stock_list` partial（东财列表连不上，改 baostock）；`sse_e_interaction` partial（"近 1 个月暂无回复"是真实空结果） | [UPSTREAM.md#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/datasources/UPSTREAM.md#L1) |
| 2026-10-08 | 产品自接的 12 个港美股/事件端点 | 逐个 `fetch_endpoint` 实测 + `health.py --only` 全部 ok | 同上 |
| 2026-10-09 | `hkex_results_dates` / `us_earnings_8k_dates` / `sec_earnings_8k_dates` | 全部 ok（`sec_earnings_8k_dates` 用测试联系方式实跑：苹果 2004 年以来 94 份、摩根大通 2015 年以来 47 份、阿里 0 份——ADR 用 6-K，符合预期） | 同上 |

**已知失效/受限（同样是作者记录，可直接当风险清单）**：

| 端点/源 | 状态 | 影响 |
|---|---|---|
| 通达信 K 线 / 五档 / 逐笔（`tdx_bars`/`tdx_quotes`/`tdx_transaction`） | 2026-09 起公开服务器返回空，2026-10-10 **整段删除** | 无五档替代；逐笔改腾讯 `tx_ticks` |
| `margin_backup`（上交所分支） | 上交所返回 pageSize 2000 / 共 2002 条 2 页，上游要求单页完整 → 报错 | **沪市代码当前取不到两融备源**；深市正常 |
| 百度股市通 | 返回 `ResultCode 403`，源已收紧 | `baidu_pe_history_3y` / `baidu_valuation_global` 随时可能整体失效 |
| 东财 `push2` | 部分网络被重置 | 统一回退 `push2delay`；`push2his` 不通时日级资金流只回落到最新一日 |
| 申万分类站点 | 证书链不完整 | 代码降级为不校验 TLS —— **托管环境不可接受** |
| mootdx（通达信 TCP） | 海外网络通常全部超时 | `tdx_finance` / `tdx_f10` 只适合国内网络 |
| `tx_ticks` | 15:31 前取到的标 partial（盘后定价段未收完） | 盘中不能当完整逐笔 |

→ **结论**：这些数字只能说明"作者跑通过"，**不等于 Financial Bot 生产环境可用**。Phase 1 的每个端点都必须自己跑一遍 `health.py` 等价物（见 §9 第 1 条）。

### 2.7 本次本机零鉴权探针（2026-10-28）

做法：不安装 Vibe 的完整桌面产品，直接用 Financial Bot 的 Python 3.12 虚拟环境运行数据层脚本；所有 `--out-dir` 指向 `/tmp`，不进入 Financial Bot 仓库，不读取任何 key。命令形态：

```bash
PY=/Users/yaoxingyu/文稿/工作面试/financial-bot/Financial Bot/.venv/bin/python
V=/tmp/vibe-research
$PY $V/.agents/skills/data-access/scripts/fetch_quote.py --symbol 600519 --out-dir /tmp/vibe-run
$PY $V/.agents/skills/data-access/scripts/fetch_endpoint.py --endpoint global_quote_ref --symbol 00700.HK --out-dir /tmp/vibe-run3
$PY $V/.agents/skills/data-access/scripts/fetch_estimates.py --symbol 600519 --out-dir /tmp/vibe-run4
# 其余端点同理，只换 --endpoint / --symbol / --args
```

| 端点 / 脚本 | 输入 | 结果 | 观察到的关键字段 |
|---|---|---|---|
| `fetch_quote.py`（legacy） | `600519` | ✅ `ok` | provider=`tencent`；CNY；as_of=2026-10-28；price / PE_TTM 等字段带 raw_ref |
| `global_quote_ref` | `00700.HK` | ✅ `ok` | provider=`tencent`；HKD；price 386.6；带基准指数与交易所时间戳 |
| `fetch_estimates.py`（legacy） | `600519` | ✅ `ok` | provider=`ths`；FY2026 EPS 均值 67.16，预测机构数 44，带 min/max |
| `tx_kline` | `600519` | ✅ `ok` | provider=`tencent`；adjustment=`qfq`；日 K 序列 |
| `cninfo_announcements` | `600519` | ✅ `ok` | provider=`cninfo`；公告标题 + 2026 半年报详情链接 |
| `fred_us_macro` | 默认 12 序列 | ✅ `ok` | provider=`fred`；2026-09 CPI=336.298；CSV raw_ref |
| `yahoo_consensus` | `AAPL` | ✅ `ok` | provider=`yahoo`；USD；FY2026 EPS 8.83476；注意 compliance=`B` |
| `yahoo_kline` | `AAPL` | ✅ `ok` | provider=`yahoo`；2026-10-27 close 347.1；`adjustment` 字段为 `not_applicable`，**接入前必须核对是否需自行复权** |
| `em_stock_info` | `600519` | ❌ `failed` | 东财 `push2` 在本机网络 `RemoteDisconnected`；证明"端点登记在册 ≠ 当前可用" |
| `fetch_endpoint --endpoint fetch_quote` | `600519` | ❌ `failed` | 报 `No module named sources.legacy`；证明 legacy 与 registry 必须走两套路径，适配器不能只写一种 |

**能下的结论**：

- 零鉴权链路确实能跑通，且证据信封已经带 `source/endpoint/as_of/currency/adjustment/raw_ref`，**技术上门槛不大**。
- 但同一台机器上东财已经失败，说明可用性必须按端点、按网络实测，不能按仓库自述的 168 个端点整体承诺。
- 本次探针覆盖 10 次调用 / 8 个成功端点（约占 5%），**不能外推为 168 个全部可用**；SEC/FINRA/CBOE/需 key 端点均未验证。

---

## 3. 数据源清单

### 3.1 A 股 / 中国市场（零鉴权为主）

| Provider | 用途（端点/字段） | 认证 | 端点 / 域名 | 代码位置 | 授权与风险 | 与 Financial Bot 现有源重叠 |
|---|---|---|---|---|---|---|
| **腾讯财经** | 实时快照（现价/昨收/涨跌幅/换手/PE_TTM/PE 静/PB/流通市值/总市值/成交额/量比/涨跌停价/报价时间）；批量指数；AH 溢价；汇率；日/周/月/分钟 K；当日逐笔 | 无 | `qt.gtimg.cn/q=`、`web.ifzq.gtimg.cn`、`ifzq.gtimg.cn`、`proxy.finance.qq.com` | [tencent.py#L64](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/tencent.py#L64)、[tencent.py#L107](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/tencent.py#L107)、[astock_ported.py#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/astock_ported.py#L1) | `cn-public`；**字段靠下标**（44=流通市值/45=总市值，网上大量写反）；有 `is_stale` 僵尸报价判定；条款未核 | **高**：Financial Bot 已用腾讯做 A 股 K 线兜底与港股日线（[Ashare.py#L11](../Ashare.py#L11)） |
| **新浪财经** | 复权因子；财报三表（近 8 期，含同比）；个股资金流；板块资金；ETF 期权链（含希腊字母/IV）；美股日 K；成交额榜 | 无 | `quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022`、`hq.sinajs.cn`、`finance.sina.com.cn`、`stock.finance.sina.com.cn` | [sina.py#L78](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/sina.py#L78)、[sina.py#L24](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/sina.py#L24) | `cn-public`；口径是"新浪自己的"（板块资金与东财主力净额不可比）；条款未核 | **高**：Financial Bot A 股日线**主源**就是新浪 `getKLineData`（[Ashare.py#L46](../Ashare.py#L46)），另有 sinajs 联想词（[ashare/search.py#L28](../ashare/search.py#L28)） |
| **东方财富** | **60 个端点**：研报列表/行业研报、板块归属、资金流（分钟/日）、龙虎榜、解禁、两融、大宗、股东户数、分红、个股基本面、新闻（个股/全球 7×24）、涨停/炸板/跌停/昨涨停池、人气榜、港美股财报、南向资金、十大股东、主营构成、停复牌、业绩预告、机构调研、股东增减持、回购、质押、新股日历、可转债 | 无 | `datacenter-web.eastmoney.com/api/data/v1/get`、`reportapi.eastmoney.com/report/list`、`push2*.eastmoney.com`、`np-anotice-stock.eastmoney.com`、`pdf.dfcfw.com` 等 18 个主机 | [eastmoney.py#L39](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/eastmoney.py#L39)、[eastmoney.py#L80](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/eastmoney.py#L80) | `cn-public`；**自设**跨进程串行锁 + ≥1s 间隔；403 不重试；`push2` 断连自动回退 `push2delay`；条款未核（东财对抓取的态度需法务确认） | **中**：Financial Bot 经 akshare 间接用了东财（新闻、资金流、人气榜），但无研报/公告/龙虎榜 |
| **同花顺** | 一致预期 EPS（FY T/T+1/T+2，均值/min/max/机构数）；强势股题材归因；涨停揭秘（封板率/板型）；热榜；沪深股通分钟净买入 | 无 | `data.10jqka.com.cn/dataapi/limit_up/limit_up_pool`、`zx.10jqka.com.cn/event/api/getharden`、`data.hexin.cn/market/hsgtApi` | [ths.py#L16](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/ths.py#L16)、[ths.py#L32](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/ths.py#L32) | `cn-public`；一致预期是**页面解析**，无机构覆盖时返回空表（代码会给 warning）；条款未核 | **低**：Financial Bot 无一致预期能力 |
| **百度股市通** | 港/美股 PE(TTM)、PE(静)、PB、PCF、总市值历史序列 | 无 | `gushitong.baidu.com/opendata`（`resource_id=51171`） | [baidu.py#L15](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/baidu.py#L15) | `cn-public`；**源已收紧**（历史 403，见 SKILL.md 已知限制）；港股周末沿用前值需去重 | 无 |
| **巨潮资讯** | 公告全文检索（动态 orgId）；互动易问答 | 无 | `www.cninfo.com.cn/new/hisAnnouncement/query`、`irm.cninfo.com.cn/newircs/*` | [cninfo.py#L37](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/cninfo.py#L37)、[cninfo.py#L21](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/cninfo.py#L21) | `cn-public`；orgId 映射表 `szse_stock.json` 缺失就报错、**不回退硬编码**（好设计）；条款未核 | **低**：Financial Bot 仅经 akshare 取公告列表 |
| **深交所** | 龙虎榜（结构化）、公告、ETF 份额 | 无 | `www.szse.cn`、`disc.static.szse.cn`、`fund.szse.cn` | [exchange.py#L44](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/exchange.py#L44) | 官方站点，条款相对清晰；仍需看 robots | 低 |
| **上交所** | 龙虎榜（全文）、公告、上证 e 互动 | 无 | `query.sse.com.cn`、`www.sse.com.cn`、`sns.sseinfo.com` | [exchange.py#L65](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/exchange.py#L65) | 官方站点；上证 e 互动常返回"近 1 个月暂无回复"（真实空结果，不是失败） | 低 |
| **北交所** | 单只行情快照（开高低收+量额+五档） | 无 | `www.bse.cn` | [astock_defaults.py#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/astock_defaults.py#L1) | 官方；快照日期对不上直接报错，不冒充 | 无 |
| **中证指数 / 国证指数** | 指数成分股、权重、估值（PE/股息率） | 无 | `oss-ch.csindex.com.cn`、`www.cnindex.com.cn` | [astock_ported.py#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/astock_ported.py#L1) | 官方 Excel/CSV 文件；解析依赖文件格式，格式变了会静默少行（需断言行数） | 无 |
| **中债估值** | 国债/AAA 中短票收益率曲线（3M–30Y） | 无 | `yield.chinabond.com.cn` | [astock_defaults.py#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/astock_defaults.py#L1) | 官方；无风险利率基准，许可风险低 | 无 |
| **中国货币网** | 回购定盘利率 FR/FDR001/007/014 | 无 | `www.chinamoney.com.cn` | [astock_ported.py#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/astock_ported.py#L1) | 官方；许可风险低 | 无 |
| **人民银行** | 社会融资规模增量（月度） | 无 | `www.pbc.gov.cn` | [macro.py#L30](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/macro.py#L30) | 官方；页面改版风险 | 无 |
| **国家统计局** | PMI（制造业/非制造业/综合，大中小型） | 无 | `www.stats.gov.cn/sj/zxfb/` | [macro.py#L80](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/macro.py#L80) | 官方；HTML 解析，改版即失效 | 无 |
| **财联社** | 电报全市场快讯 | 无 | `www.cls.cn` | [cls.py#L14](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/cls.py#L14) | `cn-public`；快讯正文版权属财联社，**只宜存标题+链接+时间** | **高**：Financial Bot 用 `ak.stock_info_global_cls`（[ashare/monitor.py#L398](../ashare/monitor.py#L398)） |
| **华尔街见闻** | 7×24 快讯；全球宏观日历（重要度/实际/预期/前值） | 无 | `api-one-wscn.awtmt.com` | [astock_ported.py#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/astock_ported.py#L1) | `cn-public`；条款未核 | 无 |
| **央视** | 新闻联播文字稿（标题+正文） | 无 | `tv.cctv.com` | [astock_defaults.py#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/astock_defaults.py#L1) | 官方媒体；正文转载需授权 | 无 |
| **通达信官网盘后包** | 某交易日全市场日线（含成交额，沪深京），约 2.7MB 压缩包 | 无 | `www.tdx.com.cn` | [astock_defaults.py#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/astock_defaults.py#L1) | HTTP 下载、非 API；带宽成本在客户端；条款未核 | 无（可补 Financial Bot 缺的 `amount`） |
| **通达信行情服务器（TCP）** | 财务快照 37 字段；F10 文本 | 无 | mootdx 服务器（TCP，非 HTTP） | [mootdx_src.py#L84](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/mootdx_src.py#L84) | **不稳定**：2026-09 起 K 线/五档/逐笔命令普遍返空（已删端点）；F10 只剩"最新提示"；海外网络常全部超时 | 无 |
| **baostock** | 前复权日 K、PE/PB/PS/PCF 历史、标的基本信息、筹码分布 CYQ | 无 | baostock TCP 服务 | [baostock_src.py#L57](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/baostock_src.py#L57) | 零鉴权 TCP；**不支持北交所**；需锁 + 分页（有专门测试）；许可条款未核 | 无 |
| **5 家期货交易所官网**（上期所/能源中心/郑商所/中金所/广期所） | 期货日线、商品/股指期权（含 IV）、会员持仓排名 | 无 | `www.shfe.com.cn`、`www.ine.cn`、`www.czce.com.cn`、`www.cffex.com.cn`、`www.gfex.com.cn` | [astock_defaults.py#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/astock_defaults.py#L1) | 官方；**注意：不含大商所**（registry 未登记 DCE） | 无 |
| **上海黄金交易所** | 黄金/白银/铂日线 | 无 | `www.sge.com.cn` | [astock_ported.py#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/astock_ported.py#L1) | 官方 | 无 |
| **申万研究** | 行业分类 + 归属变迁史（消除前视偏差） | 无 | `www.swsresearch.com` | [sources/sw.py](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/sw.py) | **证书链不完整**，代码降级为不校验 TLS 并在证据里注明——这在托管环境不可接受 | 无 |
| **iWencai 问财** | NL 语义搜索（研报/公告/新闻，按段落打分去重）；NL 结构化数据查询 | **需 `IWENCAI_API_KEY`** | `openapi.iwencai.com/v1/comprehensive/search`、`/v1/query2data` | [iwencai.py#L12](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/iwencai.py#L12)、[iwencai.py#L16](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/iwencai.py#L16) | **唯一商业 key 接口**；配额/资费/商用条款未知；缺失时端点直接 failed 并明示 | 无 |
| **RSS 策展源（106 个 × 12 行业）** | 产业资讯标题/摘要/时间 + 红线关键词命中 | 无 | 各媒体 RSS（清单见 `datasources/rss_sources.json`） | [rss.py#L87](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/rss.py#L87) | `rss-public`；**逐源版权/robots 未核**；只宜存标题+链接 | **中**：Financial Bot 走 Google News RSS，覆盖不同 |

### 3.2 美股 / 港股 / 海外官方源

| Provider | 用途 | 认证 | 端点 / 域名 | 代码位置 | 授权与风险 | 与 Financial Bot 现有源重叠 |
|---|---|---|---|---|---|---|
| **SEC EDGAR** | ticker→CIK；申报列表（10-K/10-Q/8-K/4/13F…）；XBRL companyfacts；每日申报流；全文检索；frames 横截面；8-K 业绩日 | 无 key，但**必须**在 UA 声明真实姓名+邮箱（`VRA_SEC_CONTACT`） | `www.sec.gov/files/company_tickers.json`、`data.sec.gov`、`efts.sec.gov`、`www.sec.gov/Archives/edgar/data/...` | [sec.py#L34](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/sec.py#L34)、[sec.py#L201](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/sec.py#L201) | `S` 级官方；自设 8 req/s；UA 不合规直接失败（不把填错的值拼进错误信息，防泄漏） | **高**：Financial Bot 已接 `company_tickers.json` + `data.sec.gov` + Archives（[api/sec.py#L14](../api/sec.py#L14)） |
| **Yahoo Finance** | chart K 线；关键指标（PE/PB/EV/ROE/Beta）；分析师预期；机构持仓；财务时间序列；分红拆股；期权链；新闻搜索 | 无（cookie+crumb 会话） | `query2.finance.yahoo.com/v8/finance/chart`、`/v10/finance/quoteSummary`、`/ws/fundamentals-timeseries` | [yahoo.py#L26](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/yahoo.py#L26)、[_http.py#L288](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/_http.py#L288) | **`B` 级：非官方接口，个人研究用**；crumb 握手不落盘（只落业务响应）；条款明确禁止抓取，商用风险高 | **中**：Financial Bot US/HK 主源是 `yfinance`（同一上游，库封装版），[ashare/data.py#L283](../ashare/data.py#L283) |
| **FINRA** | Reg SHO 单票日度空头成交占比；全市场空头排行 | 无 | `cdn.finra.org` | [finra.py](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/finra.py) | `B` 级；自设 4 req/s；CDN 文件格式变更风险 | 无 |
| **CBOE** | 官方延时期权链（按到期筛选、成交量前 N）；个股快照含 IV30 | 无 | `cdn.cboe.com` | [cboe.py](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/cboe.py) | **`C` 级：仅个人研究**；自设 4 req/s。**不建议接** | 无 |
| **美国财政部** | 收益率曲线（1M–30Y，每日） | 无 | `home.treasury.gov` | [macro_us.py](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/macro_us.py) | `S` 级官方，许可最清晰 | 无 |
| **CFTC** | COT 持仓报告 | 无 | `publicreporting.cftc.gov` | [macro_us.py](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/macro_us.py) | `S` 级官方 | 无 |
| **Nasdaq** | 财报日历（锚定公司下一财报日） | 无 | `api.nasdaq.com/api/analyst/{ticker}/earnings-date` | [datacal.py#L22](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/datacal.py#L22) | `B` 级；**主机限流 2 req/s**（registry 自述）；非官方 | 无 |
| **FRED（圣路易斯联储）** | CPI/核心 PCE/失业率/非农/联邦基金利率/实际 GDP 等序列 | 无（免 key CSV 下载） | `fred.stlouisfed.org` | [macro_us.py](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/macro_us.py) | `S` 级官方；注意 FRED 对再分发有单独要求 | 无 |
| **港交所披露易** | 港股业绩公告日（末期/中期/季度，带发布时间） | 无 | `www1.hkexnews.hk` | [hkex.py](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/hkex.py) | `S` 级官方 | **低**：Financial Bot 港股只取行情 |
| **FinMind** | 台股法定月营收（台光/台燿/金像电/联亚…） | 无 | `api.finmindtrade.com/api/v4/data` | [industry.py#L27](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/industry.py#L27) | 公开 API；免费额度未核；台股月营收是公开信息 | 无 |
| **雪球 / 股吧** | 讨论帖标题/作者/日期/链接（**经 Exa 索引，不直连**） | 无 | `xueqiu.com`、`guba.eastmoney.com`（源站 WAF 拦截） | [exa.py#L137](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/exa.py#L137) | UGC，版权与内容风险高；正文取不到，只能元数据 | 无 |

### 3.3 检索、取页与另类数据

| Provider | 用途 | 认证 | 端点 / 域名 | 代码位置 | 授权与风险 | 与 Financial Bot 现有源重叠 |
|---|---|---|---|---|---|---|
| **Exa（免 key MCP）** | 全网语义搜索（新闻/深度文/KOL 帖），市场声音层 | 免 key（`VRA_EXA_API_KEY` 有占位但**未注册实现**） | `https://mcp.exa.ai/mcp`（JSON-RPC / Streamable HTTP / SSE 解析） | [exa_client.py#L26](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/core/exa_client.py#L26)、[retrieval.py#L392](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/core/retrieval.py#L392) | 免费额度与商用条款未核；**结果只当线索，不当事实**（有专门的不可信文本纪律） | **中**：Financial Bot 有 Brave/Google News/DDG/Bing 四路关键词搜索 |
| **Jina Reader** | 任意网页正文提取（零 key） | 免 key，**必须带 UA** | `https://r.jina.ai/{url}` | [retrieval.py#L401](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/core/retrieval.py#L401) | **隐私**：目标 URL（含 query）会发给第三方；README 明确警告不要提交含令牌的链接；拦截页默认失败（可显式 opt-in） | 无（Financial Bot 自建 `PublicDocumentReader`，SSRF 防护更好） |
| **本地 Chrome CDP** | 渲染 JS 页面 | 本机 | `127.0.0.1:9222` | [core/cdp.py](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/core/cdp.py) | 本机进程，非外部服务；托管环境不可用 | 无 |
| **Federal Register / FCC / 商务部** | 管制与准入：1260H 全文检索、BIS 规则提及、FCC Covered List、中方出口管制公告 | 无 | `federalregister.gov/api/v1/documents.json`、`fcc.gov/supplychain/coveredlist`、`exportcontrol.mofcom.gov.cn` | [policy.py#L24](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/policy.py#L24) | 官方；FCC/商务部页面**经 Jina 转取 HTML**（引入第三方转发） | **高**：Financial Bot 已接 Federal Register（[api/services.py#L1385](../api/services.py#L1385)） |
| **Greenhouse / Ashby** | 产业锚点公司公开在招岗位（招聘信号） | 无 | `boards-api.greenhouse.io`、`api.ashbyhq.com` | [hiring.py#L32](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/hiring.py#L32) | 只读公开岗位、不碰简历（代码注释明确）；条款未核 | 无 |
| **Kalshi / Polymarket** | 宏观事件定价概率（只读行情，不下单） | 无 | `api.elections.kalshi.com/trade-api/v2`、`gamma-api.polymarket.com` | [probability.py#L46](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/probability.py#L46) | 预测市场在部分司法辖区受限；只读行情，但**展示给用户**前需法务确认 | 无 |
| **500.farm** | GPU 租金（近一年逐日中位 + 现货 + Kalshi 远期） | 无 | `500.farm/vastai/grafana.v2/api/datasources/proxy/...` | [industry.py#L327](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/industry.py#L327) | 第三方 Grafana 代理，**无 SLA、无条款**，随时可能断 | 无 |
| **Techmeme** | 海外科技头条（按产业标签标相关性） | 无 | `www.techmeme.com/river` | [headlines.py#L29](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/headlines.py#L29) | 聚合站，链接回原文；只存标题+链接 | 无 |
| **GitHub raw（DRAM 现货）** | DRAM/NAND 现货均价（社区仓转录 DRAMeXchange） | 无 | `raw.githubusercontent.com` | [commodity.py#L196](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/commodity.py#L196) | **二次转录第三方付费数据**（DRAMeXchange），商用风险最高 | 无 |
| **新浪期货（akshare 封装）** | 沪铜/沪锡/沪铝/沪镍/工业硅连续合约 | 无 | 新浪期货接口（经 akshare） | [commodity.py#L159](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/commodity.py#L159) | `cn-public`；口径依赖 akshare 版本 | 无 |

### 3.4 认证与密钥（逐项核实）

- **唯一真实 API key**：`IWENCAI_API_KEY`，只影响 2 个端点；缺失时 `fetch_endpoint.py` 直接 `failed` 并写明缺哪个变量（[fetch_endpoint.py#L94](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/fetch_endpoint.py#L94)）。
- **`VRA_SEC_CONTACT`**：不是 key，是 SEC 要求的 UA 声明（`Name email@domain`）。代码会规范化空白、只接受可打印英文字符，格式不对就报错且**不回显用户填的值**（[_http.py#L218](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/_http.py#L218)）。
- **`VRA_EXA_API_KEY` / `VRA_TAVILY_API_KEY` / `VRA_FIRECRAWL_API_KEY`**：只有环境变量名和注释，**实现没有注册**（`_SEARCH_IMPL` 只有 `exa_free`，`_PAGE_IMPL` 只有 `jina`/`cdp`，见 [retrieval.py#L420](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/core/retrieval.py#L420)）。配了这些 key **不会升级通道**，仍走免费通道——这一点要写进 Financial Bot 的接入说明，避免运维误以为"配了就有配额"。
- **`VRA_ALLOW_INSECURE_TLS=1`**：允许证书校验失败时降级，仅研究用、默认关闭（[_http.py#L83](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/_http.py#L83)）。**托管环境必须禁止**。
- 所有需要凭据的端点都在 registry 里显式声明 `auth_env`，159 个零鉴权端点**不读任何环境变量中的密钥**（[SKILL.md#L68](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/SKILL.md#L68)）。

### 3.5 限流、缓存与落盘（这是可以照着抄的部分）

| 机制 | 实现 | 证据 |
|---|---|---|
| 东财串行锁 | **跨进程**文件锁覆盖"等间隔→请求→收响应→写时间戳"全程；最小间隔 1.0s + 0.1–0.5s 随机抖动；403 不重试；代理失败自动直连重试；`push2` 断连轮询 `push2delay` | [common.py#L33](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/common.py#L33)、[common.py#L160](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/common.py#L160) |
| 官方源限流 | `sec.gov` 8/s、`finra.org` 4/s、`cboe.com` 4/s、`nasdaq.com` 2/s、其他 5/s（**自设**，不是源方公布值） | [_http.py#L207](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/_http.py#L207) |
| 无 HTTP 缓存 | 每次调用都真取；没有 ETag/If-Modified-Since 复用 | 代码里无响应缓存层 |
| 快照层（跨重启） | ① 内存 60s TTL ② 磁盘快照（key = endpoint+symbol+args）③ 只有用户显式刷新才真取；**失败与空结果不写快照**；端点可用 `cache_max_age_sec` 声明时效上限，`0` = 从不缓存 | [snapshot.ts#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/orchestrator/src/snapshot.ts#L1)、[snapshot.ts#L104](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/orchestrator/src/snapshot.ts#L104)、[service.ts#L290](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/orchestrator/src/service.ts#L290) |
| raw 归档 | 每次传输层响应落 `raw/`，文件名唯一（微秒+pid+随机）、排他创建、绝不覆盖、同时算 sha256；SDK/DataFrame 产物以 `extracted_` 前缀区分，不冒充原始响应 | [common.py#L233](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/common.py#L233) |
| 进程内小缓存 | 巨潮 orgId、SEC CIK 映射表、Yahoo crumb 会话、上证 e 互动 uid | [cninfo.py#L21](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/cninfo.py#L21)、[sec.py#L34](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/sec.py#L34) |
| RSS 并发 | 8 线程，单次最多 40 源，每源 10 条，3 日窗口 | [rss.py#L87](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/rss.py#L87) |

> **对 Financial Bot 的直接含义**：Vibe-Research 的限流是**为单机单用户设计的**（东财锁是本机临时文件锁）。搬到多实例部署，这把锁不存在，东财会被打爆。必须换成 Redis/DB 级分布式限流，或把取数收进单一取数服务。

### 3.6 返回字段与证据信封（最值得抄的一层）

取数层统一产出一个信封，核心是 `evidence[]`，每条字段固定：

```text
id            ev-<sha1(script|source|endpoint|symbol|field|period|record_key)[:12]>
symbol        标的（全市场级 = MARKET）
market        CN / US / HK / SH / SZ / BJ
field         字段名（如 net_profit_attr / pe_ttm / announcement_title）
value         值（原样，不换算）
unit          单位（元 / 亿元 / 股 / 手 / % / 元/股 …）
currency      CNY / HKD / USD
period        报告期或交易日（FY2026 / 2026Q3 / 2026-10-09）
as_of         数据截止日
source        tencent / eastmoney / cninfo / sec / yahoo …
endpoint      具体接口路径（溯源到"哪个 URL 的哪个参数"）
fetched_at    取数时刻
adjustment    none / qfq / hfq / not_applicable   ← 复权口径
raw_ref       raw/xxx.json（本次原始响应）
note          口径提示（如"东财 f116/f117 与腾讯 44/45 方向相反"）
record_key    同一端点多条记录的稳定主键（art_code / infoCode）
```

证据（[common.py#L264](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/common.py#L264)）+ 信封（`script / symbol / market / status / fetched_at / primary_source / used_sources[] / evidence[] / extra{} / errors[] / missing[]`，[SKILL.md#L79](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/SKILL.md#L79)）。

**三个设计决定值得原样搬**：
1. **`adjustment` 和 `currency` 是必填字段**，不是可选项——Financial Bot 现在只在 `metadata` 里给整个响应一个值，个股/多标的场景会丢。
2. **`raw_ref` + sha256 是"证据"的成立条件**：没有 raw 就不算证据。Financial Bot 的 `ResearchEvidence` 没有这个字段（见 §5.2）。
3. **单位不换算**：取数层原样输出，跨单位运算只在计算层做一次。这条能消掉一整类"亿元/元混用"的 bug。

---

## 4. Financial Bot 现状与重叠分析

### 4.1 现在的数据层（逐文件核实）

| 能力 | 现状 | 代码位置 |
|---|---|---|
| A 股日/周/月 K | 新浪 `getKLineData` **主源** → 腾讯 `fqkline`（仅 qfq）**备源** → akshare `stock_zh_a_hist(adjust="qfq")` **再备** | [Ashare.py#L46](../Ashare.py#L46)、[Ashare.py#L56](../Ashare.py#L56)、[ashare/data.py#L131](../ashare/data.py#L131)、[ashare/data.py#L391](../ashare/data.py#L391) |
| 港股日 K | 腾讯 `hk` 前缀日线（`Ashare.get_price` 港股分支） | [Ashare.py#L56](../Ashare.py#L56)、[ashare/data.py#L274](../ashare/data.py#L274) |
| 美股 / 恒指 | `yfinance`（`auto_adjust=True`）→ Finnhub `stock/candle`（需 key） | [ashare/data.py#L262](../ashare/data.py#L262)、[ashare/data.py#L333](../ashare/data.py#L333) |
| 行情 provenance | `_mark_source(provider, adjustment)`，只记最后一次成功源；`provider_info(code)` 按代码取回 | [ashare/data.py#L85](../ashare/data.py#L85)、[ashare/data.py#L89](../ashare/data.py#L89) |
| 缓存 | `DataFetcher.data_cache`（进程内、无 TTL、key=code_count_frequency） | [ashare/data.py#L76](../ashare/data.py#L76)、[ashare/data.py#L111](../ashare/data.py#L111) |
| 新闻 | akshare `stock_news_em`（东财个股）、`stock_info_global_cls`（财联社）、`stock_zh_a_disclosure_report_cninfo`（巨潮公告）、Finnhub 公司/一般新闻 | [ashare/monitor.py#L375](../ashare/monitor.py#L375)、[ashare/monitor.py#L398](../ashare/monitor.py#L398)、[ashare/monitor.py#L517](../ashare/monitor.py#L517)、[ashare/monitor.py#L524](../ashare/monitor.py#L524) |
| 资金流 / 人气 | akshare `stock_individual_fund_flow`、`stock_fund_flow_individual`、`stock_hk_hot_rank_em` | [ashare/monitor.py](../ashare/monitor.py) |
| 网页搜索 | Brave（服务端 key）→ Google News RSS → DuckDuckGo HTML → Bing RSS → Federal Register | [api/services.py#L1161](../api/services.py#L1161)、[api/services.py#L1246](../api/services.py#L1246) |
| 公网文档读取 | 自建 `PublicDocumentReader`：只允许 HTTPS/443、解析并**锁定公网 IP**、25s 截止、SEC 走单独 throttle | [api/web_research.py#L34](../api/web_research.py#L34) |
| SEC | `company_tickers.json` + `data.sec.gov` submissions/companyfacts + Archives | [api/sec.py#L14](../api/sec.py#L14)、[api/sec.py#L80](../api/sec.py#L80) |
| 标的识别 | 东财 suggest + 新浪 suggest | [ashare/search.py#L27](../ashare/search.py#L27) |

### 4.2 能力缺口（Vibe-Research 能补的）

| 能力 | Financial Bot | Vibe-Research | 建议 |
|---|---|---|---|
| 结构化财报（三表/关键指标，A/港/美） | ❌ 无 | ✅ 新浪三表、东财港美股财报、SEC XBRL | **优先** |
| 一致预期 EPS | ❌ 无 | ✅ 同花顺 worth + 东财研报 | **优先**（必须带机构数/min/max） |
| 公告结构化 + PDF 直链 | ⚠️ 仅 akshare 列表 | ✅ 巨潮/深交所/东财（`announcementId`/`art_code`/PDF） | **优先** |
| 研报列表（标题/机构/评级/三年 EPS/PDF） | ❌ 无 | ✅ 东财 reportapi + `pdf.dfcfw.com` | 接列表，**不存 PDF 正文** |
| 资金/龙虎榜/两融/大宗/解禁/股东户数/打板/人气 | ⚠️ 部分（akshare 资金流+人气） | ✅ 60 个东财端点 | 按需挑 3–5 个 |
| 宏观（中债/货币网/人行/统计局/FRED/财政部/CFTC） | ❌ 无 | ✅ | **优先**（官方源） |
| 指数成分/权重/估值 | ❌ 无 | ✅ 中证/国证官网 | **优先** |
| 期货/大宗/上金所 | ❌ 无 | ✅ | 二期 |
| 期权（ETF/CBOE/Yahoo） | ❌ 无 | ✅（CBOE 为 C 级） | 只接新浪 ETF 期权 |
| 港股财报/派息/业绩公告 | ❌ 无 | ✅ 东财港股 + 港交所 | 二期 |
| 跨市场微秒级 provenance（raw_ref+sha256） | ❌ 无 | ✅ | **架构上必须补** |
| 逐笔 / 五档 | ❌ 无 | ⚠️ 逐笔有、五档无（通达信已失效） | 低优先 |

### 4.3 Financial Bot 的数据契约（接入必须对齐的三处）

**① 响应级 provenance（现状：只有一个 provider）**

```python
metadata = {
    "data_points": ..., "source": provider_info["provider"], "provider": provider_info["provider"],
    "adjustment": provider_info["adjustment"], "coverage": "single_symbol_history",
    "generated_at": ..., "retrieved_at": ..., "currency": currency,
    "data_as_of": chart_series[-1]["date"], "price_status": "historical_or_delayed",
}
```

见 [api/services.py#L657](../api/services.py#L657)。`adjustment` 来自 `DataFetcher.adjustment_by_code`，A 股经 Ashare 路线时是 `"unknown"`（[ashare/data.py#L135](../ashare/data.py#L135)）。更具体地说，`Ashare.get_price` 的 A 股路径是**先新浪 `get_price_sina`（不复权）、失败才腾讯 `get_price_day_tx`（qfq）**（[Ashare.py#L78](../Ashare.py#L78)、[Ashare.py#L80](../Ashare.py#L80)），但两路都统一标成 `provider="ashare" / adjustment="unknown"`。因此同一标签下可能混用不同复权口径，系统无法区分；这不是说现有数字一定错，而是**接入任何新 K 线源前必须先拆清 source/adjustment，或强制统一 qfq**。`fallback_research` 会主动追加一条调整口径缺口（[fallback_research.py#L245](../api/financial_bot/fallback_research.py#L245)）。

**② 证据对象（现状：无 raw、无 currency、无 adjustment）**

```python
class ResearchEvidence(BaseModel):
    id: str; source: str; url: Optional[str]; retrieved_at: str
    data_as_of: Optional[str]; freshness: str = "unknown"
    title: Optional[str]; excerpts: List[Dict[str, str]]
```

见 [api/schemas.py#L393](../api/schemas.py#L393)。由 `sources_for_tool()` 按工具类型分派构造，`id = "ev_" + sha256(tool,url,row)[:16]`（[api/evidence.py#L20](../api/evidence.py#L20)）。

**③ 工具上下文（现状：数字必须能追到工具结果）**

`retrieve()` 做同轮去重（`retrieval_cache`，key=`[tool,args]`）→ `record_tool()` 补 `reason_code`/`retrieved_at`/`evidence` → `tool_context()` 输出 `{tool,data,evidence,status,reason_code,data_gaps}`（[api/agent_pydantic.py#L692](../api/agent_pydantic.py#L692)、[#L726](../api/agent_pydantic.py#L726)、[#L737](../api/agent_pydantic.py#L737)）。

**结论**：Financial Bot 的契约是"**工具 → 证据 → 数字**"，Vibe-Research 是"**端点 → evidence 字段 → raw 文件**"。前者的粒度是"一次工具调用"，后者是"一个字段"。接入时必须把后者的字段级证据**折叠**成前者的条目级证据（或扩展 `ResearchEvidence`，见 §6.3）。

---

## 5. 架构契合度：Python 适配器、独立服务，还是仅参考？

### 5.1 三种方案的取舍

| 方案 | 做法 | 优点 | 缺点 | 适用 |
|---|---|---|---|---|
| **A. Python 适配器（推荐）** | 在 Financial Bot 内建 `api/datasources/` 子包，只移植需要的 `source` 函数 + mapper，保留 evidence 信封；配 `NOTICE` + 上游 SHA | 无额外进程；证据字段可直接进 `ResearchEvidence`；复用 Financial Bot 现有异步/缓存/限流 | 需要自己承担 Apache-2.0 署名；移植 12–15 个函数≈1500 行；上游更新需手动对账 | 首批 8–12 个端点 |
| **B. 独立 sidecar** | 把 Vibe-Research 的 `fetch_endpoint.py` 当子进程/HTTP 服务跑，Financial Bot 调 CLI 读 JSON 信封 | 依赖隔离（`mootdx`/`baostock` 不污染主环境）；许可证边界清晰（整目录 + NOTICE） | 多一套 Python 3.11 环境；进程冷启动 1–3s；分布式限流仍要自己做；跨进程传 raw_ref 要挂共享卷 | 需要 20+ 端点、或要跑通达信/baostock 这类有状态客户端 |
| **C. 仅参考** | 不复制代码，照它的信封/fail-open 设计重写 5–8 个 provider | 零许可证负担；代码量最小；可按 Financial Bot 风格写 | 重复劳动；容易漏掉它踩过的坑（字段下标、市值得方向、orgId 映射） | 只要 3–5 个官方源（公告/指数/宏观） |

### 5.2 推荐：A 为主、C 兜底，B 只在需要 TCP 客户端时启用

理由：
1. Financial Bot 已是 Python/FastAPI，**没有跨语言成本**；
2. 真正要接的端点集中在 6 个 provider（巨潮、东财、同花顺、中证/国证、中债、SEC），代码量可控；
3. baostock/mootdx 这类**有状态 TCP 客户端**只在需要筹码/财务快照时才用，那时再走 B（子进程隔离），避免把 `mootdx==0.11.7` 塞进主环境；
4. 许可证上，A 方案必须建 `api/datasources/vendor/NOTICE.md`，写明移植自 `a-stock-data v3.10.1 (8f6a6a5)` / `global-stock-data v2.0.3`、Apache-2.0、附许可全文。

### 5.3 建议的目录与接口

```text
api/datasources/
  __init__.py
  registry.py          # 只登记启用的端点：id/layer/source/compliance/auth_env/fields
  http.py              # 串行锁 + 限流 + raw 落盘 + 脱敏（移植 _http.py 的精华，不整包复制）
  envelope.py          # Evidence / Envelope pydantic 模型（对齐 schemas.ResearchEvidence）
  providers/
    cninfo.py          # 公告 + 互动易
    eastmoney.py       # 研报列表 / 公告备源 / 关键指标
    ths.py             # 一致预期
    index_official.py  # 中证 / 国证成分与估值
    chinabond.py       # 收益率曲线（+ 货币网回购利率）
    sec_xbrl.py        # 若要与现有 api/sec.py 合并，只补 frames/全文检索
  NOTICE.md            # Apache-2.0 归属 + 上游 SHA
```

接口固定成一个函数签名，便于 fail-open 与测试：

```python
async def fetch(endpoint_id: str, *, symbol: str, args: dict,
                consistency: Literal["fresh", "prefer_cache", "cache_only"] = "fresh") -> Envelope:
    """永远返回 Envelope，不抛异常；失败时 status=failed + errors[]，绝不返回编造值。"""
```

---

## 6. 分阶段接入建议

### Phase 0（1 周）：只做"证据层"，不接新源

**目标**：让 Financial Bot 的 `ResearchEvidence` 能承载 Vibe-Research 级别的溯源信息。**先改契约，再谈数据源**。

1. 给 `ResearchEvidence` 增加**可选**字段（向后兼容，不动现有调用）：
   - `provider_endpoint: Optional[str]`（如 `datacenter-web.eastmoney.com/api/data/v1/get?reportName=...`）
   - `currency: Optional[str]`、`adjustment: Optional[str]`（`none|qfq|hfq|not_applicable`）
   - `raw_ref: Optional[str]`、`raw_sha256: Optional[str]`
   - `record_key: Optional[str]`
2. 新增 `api/datasources/envelope.py`，把 Vibe-Research 的 `evidence()` 语义映射进来（见 §6.3）。
3. 把 `get_stock_analysis` 的 `adjustment="unknown"` 修掉：Ashare 路线实际是**前复权**（[Ashare.py#L11](../Ashare.py#L11) 腾讯 qfq、[Ashare.py#L46](../Ashare.py#L46) 新浪未复权→注意），必须逐链路标清楚，否则"复权口径未知"会一直挂在数据缺口里。
4. **验收**：任取一条已有证据，能从 `evidence.id` → `raw_ref` → sha256 → 原始响应全链路复现；`adjustment`/`currency` 非 `unknown` 的比例 100%。

> ⚠️ Phase 0 有一个必须先查清的点：新浪 `getKLineData` 返回的是**不复权**价格，而腾讯分支是 qfq。现在 `_mark_source` 在 Ashare 路线上统一记 `"unknown"`，等于把两种口径混在一起。这是**现有 bug 级问题**，比接新源更该先修。

### Phase 1（2–3 周）：接 6 个"高价值 / 低风险"provider

| 顺序 | Provider | 端点 | 为什么先接 | 验证什么 |
|---|---|---|---|---|
| 1 | 巨潮资讯 | `cninfo_announcements` | 官方、结构化、有 PDF 直链 | orgId 映射覆盖率；公告时间戳时区（北京时间→UTC） |
| 2 | 中证/国证 | `index_constituents` / `index_weights` / `index_valuation` | 官方文件，做股票池必需 | 文件行数断言（防静默少行）；成分变更日 |
| 3 | 东财 | `em_reports`（研报列表） | Financial Bot 完全空白 | `infoCode` → PDF 链接可用性；只存元数据不存正文 |
| 4 | 同花顺 | `fetch_estimates`（一致预期） | 免费源里稀缺 | 机构数 < 3 时的降级；与东财逐篇预测的差异要显式报告 |
| 5 | 新浪/东财 | `sina_income_statement` / `em_global_key_indicators` | 结构化财报 | 单位（元 vs 万元）；累计值 vs 单季（Vibe 明确不拆，交给 calc） |
| 6 | 中债 + 货币网 | `chinabond_yield_curve` / `repo_fixing_rates` | 官方、许可最清晰 | 曲线日期（中债 T 日 18:00 后才有） |

**每个 provider 的接入检查单**：
- [ ] 端点域名写进 Financial Bot 的出站白名单（现在 `PublicDocumentReader` 只允许 HTTPS，抓取层可复用）
- [ ] 限流：分布式（Redis 或 DB 行锁），**不能**照搬 Vibe 的本机文件锁
- [ ] 超时：连接 5s / 读 15s；单端点总预算 ≤ 30s；批量端点分页上限
- [ ] 失败：`status=unavailable` + `reason_code=provider_error`，**绝不**回退到"模型记忆"或旧值
- [ ] 空结果 vs 失败：0 条要能区分（Vibe 的 `DataNotAvailable` vs `ResourceUnavailable` 分得很清，值得抄）
- [ ] 证据字段：`source/endpoint/as_of/currency/adjustment/raw_ref/sha256/record_key` 齐全
- [ ] 单测：假响应夹具 + 畸形响应 + 超时 + 429/403

### Phase 2（1–2 月）：宏观与产业，走"只读快照 + 显式刷新"

- 宏观官方序列（FRED / 财政部 / CFTC / 人行 / 统计局）→ 日更一次，进快照层，页面上标 `data_as_of`。
- 期货/大宗（5 家交易所 + 上金所 + 新浪连续合约）→ 收盘后跑。
- 港股财报/派息/业绩公告（东财 + 港交所）→ 与现有 `api/sec.py` 的美股路径对称。
- 另类数据（台股月营收 / GPU 租金 / Kalshi-Polymarket / Techmeme / 招聘）→ **只进内部研究模式**，不进对外报告，避免"预测市场/二手转录数据"出现在客户可见内容里。

### Phase 3（按需）：sidecar

只在需要 `baostock`（筹码/估值历史）或 `mootdx`（通达信财务快照）时启用；以子进程 + JSON 信封方式调用，raw 目录挂共享卷，主进程只读信封。

### 6.1 fail-open / 降级规则（照抄 Vibe-Research 的语义，落到 Financial Bot 的状态机）

| Vibe 状态 | 含义 | Financial Bot 映射 | 禁止行为 |
|---|---|---|---|
| `ok`（exit 0） | 主源成功 | `status=completed` | — |
| `partial`（exit 2） | 走了备源或部分字段缺失 | `status=completed` + `data_gaps[]` + `freshness=provider_partial` | 不得把 partial 写成 ok |
| `failed`（exit 3） | 关键数据全失败 | `status=unavailable` + `reason_code=provider_error` | **不得**用旧值/记忆补数（Vibe 宪法 §0 第 1 条，Financial Bot `fallback_research` 已是同一立场） |
| `DataNotAvailable` | 该日/该标的确实无数据 | `status=completed` + `data_gaps=["non_trading_day"]` | 不得当成 provider 故障 |
| `ResourceUnavailable` | 资源没取到，**不能**推断"无数据" | `status=unavailable` | 不得推断"没发生" |

### 6.2 快照与刷新

- 端点级 `cache_max_age_sec`：行情 0–15s、公告 300s、财报 1 天、宏观 1 天、指数成分 7 天。
- 快照 key 必须含 `endpoint + symbol + args + provider`；**失败与空结果不写快照**。
- 页面必须显示 `data_as_of` 与 `retrieved_at`（Vibe 的信用模型就是靠这个；Financial Bot 的 `price_status="historical_or_delayed"` 已经是同方向）。

### 6.3 provenance 映射（可直接落代码）

| Vibe evidence 字段 | Financial Bot 落点 | 转换规则 |
|---|---|---|
| `source`（tencent/eastmoney/cninfo…） | `ResearchEvidence.source` | 直接用；但 `source` 现在是"人类可读名"，建议统一为 `provider:eastmoney` 形式 |
| `endpoint` | **新增** `provider_endpoint` | 存"主机+路径"，不带 query 里的敏感参数 |
| `as_of` / `period` | `data_as_of` | 统一 `YYYY-MM-DD`；`FY2026` 这类期间放 `record_key`/`excerpts` |
| `fetched_at` | `retrieved_at` | ISO8601 带时区；Financial Bot 现在用 UTC |
| `currency` | **新增** `currency` | 缺失时不要默认 CNY，写 `unknown` 并进 `data_gaps` |
| `adjustment` | **新增** `adjustment` | `none/qfq/hfq/not_applicable`；`unknown` 进 `data_gaps` |
| `raw_ref` + sha256 | **新增** `raw_ref` / `raw_sha256` | 对象存储 key（按 `run_id/owner` 分区，见 §7.3） |
| `record_key` | **新增** `record_key` | 公告 `announcementId`、研报 `infoCode`、财报 `REPORT_DATE+REPORT_TYPE` |
| `value/unit/field` | `excerpts[]`（`{"location": field, "text": f"{value} {unit}"}`） | 或扩展成结构化 `facts[]`，长期更好 |

---

## 7. License 与数据授权

### 7.1 代码许可链

| 环节 | 许可 | 对 Financial Bot 的要求 |
|---|---|---|
| Vibe-Research 主仓 | MIT | 保留版权与许可声明（若复制代码） |
| `sources/`（移植自 a-stock-data v3.10.1 / global-stock-data v2.0.3） | **Apache-2.0** | 随附 Apache-2.0 全文、保留版权/NOTICE、标注修改（"只改联网入口"） |
| `sources/rss_sources.json`（来自 investment-news） | MIT | 保留声明 |
| `backtest/`（HKUDS/Vibe-Trading） | MIT + NOTICE | 有现成 NOTICE，照抄 |
| 依赖 | `requests`(Apache-2.0)、`pandas`(BSD-3)、`lxml`(BSD-3)、`akshare`(MIT)、`baostock`(BSD?)、`mootdx`(MIT)、`zod`(MIT)、`pdfjs-dist`(Apache-2.0) | 常规，无 copyleft 污染 |

⚠️ **MIT 不能"洗掉"Apache-2.0**：把 Apache-2.0 代码放进 MIT 仓库再分发，仍受 Apache-2.0 §4 约束。Vibe-Research 自己在这个 SHA 上没有做 NOTICE，**不能作为 Financial Bot 也不做的理由**。

### 7.2 数据授权（与代码许可完全无关）

四条硬事实：

1. **上游自己声明未审核**："各源的服务条款/再分发/商用许可尚未审核，不得据此主张可发布或再分发"（[SKILL.md#L68](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/SKILL.md#L68)）。
2. **registry 的合规分级是作者自评**，不是法律意见；`cn-public` 的注释就是"国内公开网页接口（**仅限研究，许可待审**）"（[registry.json](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/datasources/registry.json)、[CATALOG.md#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/datasources/CATALOG.md#L1)）。
3. **B/C 级是作者按"个人研究"划的**：`B` = 非官方/个人研究（Yahoo、FINRA、Nasdaq）；`C` = 仅个人研究（CBOE）。这两个等级**天然不适合多用户商业产品**。
4. **数据内容另有版权**：研报（券商）、快讯（财联社/华尔街见闻）、新闻正文（媒体）、RSS 全文、DRAM 现货（DRAMeXchange）——这些的授权与"接口能不能访问"是两件事。

### 7.3 逐源风险矩阵（按 Financial Bot 的部署形态分档）

| 风险档 | Provider | 内部单用户 | 托管多用户 / 对外 |
|---|---|---|---|
| 低 | SEC、财政部、CFTC、FRED、中债、货币网、人行、统计局、中证/国证、交易所官网（沪深北/5 家期货/上金所）、港交所 | ✅ 可用 | ✅ 可用（注明来源与日期） |
| 中 | 腾讯、新浪、东财、同花顺、百度、巨潮、财联社、华尔街见闻、央视、通达信盘后包、Techmeme | ✅ 可用 | ⚠️ 需逐源确认商用/再分发条款；**只存标题+链接+时间**，不缓存正文 |
| 高 | Yahoo、FINRA、Nasdaq、RSS 106 源、Exa 索引、Jina 转发、雪球/股吧元数据 | ⚠️ 标注"非官方/未核实" | ⛔ 默认关闭，或只做"线索"不进报告结论 |
| 禁止 | CBOE（C 级）、DRAM 现货（二手转录）、研报 PDF 全文再分发 | ⚠️ 仅个人 | ⛔ 不接 |

### 7.4 需要法务确认的 5 个具体问题

1. 东财/同花顺/新浪/腾讯/百度的**用户协议是否允许服务端抓取 + 向第三方展示**（注意：不是"能不能访问"，是"能不能再展示"）。
2. 研报元数据（标题/机构/评级/EPS 预测）是否构成"数据库权利"意义上的复制；只存链接是否足够。
3. `VRA_SEC_CONTACT` 在托管环境下应该填**谁**：SEC 要求真实联系人，产品方填自己的运维联系人是否满足"declare identity"的语义。
4. 中证/国证指数文件的再分发条款（指数公司通常对"指数值+成分"有单独授权要求）。
5. Apache-2.0 NOTICE 义务的履行方式（是否需要在每个文件头加声明，还是顶层 NOTICE 足够）。

---

## 8. 密钥、用户隔离、多用户展示与缓存授权风险

### 8.1 服务端密钥

| 风险 | 现状 | 建议 |
|---|---|---|
| 把 iWencai / Exa / Brave / Finnhub key 放服务端 | Financial Bot 已有 Brave、Finnhub 的服务端 key；Vibe 的模型是"用户自己的 key 在 localStorage" | 新增 provider 一律**服务端单 key + 每用户配额**；key 只从环境变量/密钥管理读，**不进** registry、日志、证据、前端 |
| 错误信息泄漏 key | Vibe 的 `_redact()` 做了两档脱敏（自己配置的 key 精确替换 + 第三方凭据模式匹配），并明确"不承诺绝不泄漏"（[retrieval.py#L363](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/core/retrieval.py#L363)） | 移植这套脱敏；Financial Bot 的 `record_tool`/`data_gaps` 也应过一遍 |
| SEC UA 里的联系人 | Vibe 存 `sec-contact.json`（仅本机、仅本人可读） | 托管环境改成**部署级**配置，且只发给 sec.gov，不写进证据 |

### 8.2 用户隔离

- Vibe-Research **没有用户概念**：`data_cache`、快照目录、raw 目录都是全局的。照搬到 Financial Bot，两个用户查同一只股票会命中同一份缓存——对公开行情无害，但**一旦缓存里混入持仓/自选参数就会串号**。
- Financial Bot 已有 owner 过滤 + PG RLS（[api/financial_bot/db.py](../api/financial_bot/db.py)、[api/supabase_store.py#L123](../api/supabase_store.py#L123)），这是优势，**新 provider 的缓存必须沿用**：
  - 缓存 key 加 `owner_id`（或对纯公开数据显式标注 `public=true` 并**禁止**带用户参数）；
  - raw 落盘目录按 `owner_id/run_id` 分区；
  - 证据查询必须带 owner 过滤（现有 `store.py` 已按 owner 过滤，别在新路径绕过）。

### 8.3 多用户展示与缓存授权

- **"缓存 = 再分发"**：把第三方响应缓存 5 分钟再展示给 100 个用户，法律上仍是"向 100 个用户展示第三方内容"。所以 §7.3 的"中/高风险"源，缓存策略要与展示策略绑定：
  - 低风险源：可缓存、可展示、可进分享卡；
  - 中风险源：缓存 TTL 短（≤5 分钟）、只展示"标题+链接+时间+来源"、**不展示正文**；
  - 高风险源：不进分享卡/公开报告页，只在登录后的研究视图里以"线索（未核实）"呈现。
- **分享卡/公开报告**（Vibe 有 share card 功能）必须显示 `provider + as_of + currency + adjustment`，否则用户会把"昨天的港股收盘价"当成今天的。
- **删除权**：Financial Bot 的 financial_bot 有"删除任务"并核验供应商保留；新增的 raw 归档也要进删除范围（否则"已删除"是假的）。

---

## 9. 必须验证但本次无法验证的事项

按"影响决策的程度"排序：

1. **每个端点在 2026-10-28 之后是否仍可用**。本机已做 10 次零鉴权探针（§2.7：8 个 ok、东财 `em_stock_info` 失败、legacy 通用入口失败）；其余 160 个端点未验证。已知历史失效点（Vibe 自己记录）：通达信 K 线/五档/逐笔（已删）、百度 `ResultCode 403`、东财 `push2` 部分网络断连、申万证书链不完整、`margin_backup` 上交所分页不全、`tx_ticks` 盘中不完整。→ 用 `datasources/health.py --only <id>` 逐端点实跑，产出可用率表。
2. **各源 ToS 是否允许商用/再分发**。Vibe 自述未审核；本次也无法替代法务。→ 需要逐源拉条款、留档。
3. **iWencai 的申请条件、配额、资费、商用许可**。→ 需向同花顺开放平台确认；在此之前不接。
4. **Exa MCP 免费额度与商用条款、Jina Reader 的数据处理与保留政策**。→ 需读两家条款；Jina 还会拿到目标 URL（隐私）。
5. **RSS 106 源的版权与 robots 政策**。→ 逐源检查成本高，建议先只做"标题+链接"，不做全文。
6. **数据准确性交叉验证**。Vibe 自己记录了腾讯 44/45 市值、东财 `f116/f117` 方向相反、新浪同比是比率不是百分数等坑；本次未抽样对账。→ 用 10 只票 × 5 个字段与 Wind/东财网页人工对账。
7. **港股/美股财报 PIT 的公告日是否被改写**。Vibe README 自述：东财会把旧报告期的公告日改成之后再次披露的日期，按公告日过滤会保守丢期。→ 需实测。
8. **Financial Bot 的最终部署形态**（单用户私有 / 小范围托管 / 公开 SaaS）。本次只看到"多人试用门禁保持关闭"（[docs/financial-bot-mvp.md](financial-bot-mvp.md)）。**这个答案直接决定 §7.3 的档位**。
9. **上游 Apache-2.0 NOTICE 义务的履行方式**。→ 法务确认。
10. **性能与第三方压力**。168 端点全量跑一轮的时延/失败率未知；Financial Bot 若按需并发拉取，需先压测并做服务端配额。

---

## 10. 引用链接汇总（全部固定到 `db3a7c6`）

### Vibe-Research

- 许可：[LICENSE#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/LICENSE#L1)、[README.md#L629](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/README.md#L629)
- 数据与隐私：[README.md#L243](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/README.md#L243)、[README.md#L436](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/README.md#L436)
- 依赖：[requirements.txt#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/requirements.txt#L1)、[orchestrator/package.json#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/orchestrator/package.json#L1)
- 数据层手册：[SKILL.md#L68](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/SKILL.md#L68)（安全边界/许可声明）、[#L79](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/SKILL.md#L79)（注册表与信封契约）
- 端点目录：[registry.json](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/datasources/registry.json)、[CATALOG.md#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/datasources/CATALOG.md#L1)、[README.md#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/datasources/README.md#L1)
- 上游对账：[UPSTREAM.md#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/datasources/UPSTREAM.md#L1)、[backtest/NOTICE.md#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/backtest/NOTICE.md#L1)
- 限流/落盘：[common.py#L33](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/common.py#L33)、[common.py#L160](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/common.py#L160)、[common.py#L233](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/common.py#L233)、[common.py#L264](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/common.py#L264)（证据字段）
- 官方源限流/UA：[_http.py#L207](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/_http.py#L207)、[_http.py#L218](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/_http.py#L218)、[_http.py#L288](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/_http.py#L288)
- 检索/取页：[retrieval.py#L70](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/core/retrieval.py#L70)、[#L376](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/core/retrieval.py#L376)、[#L392](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/core/retrieval.py#L392)、[#L401](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/core/retrieval.py#L401)、[#L420](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/core/retrieval.py#L420)、[exa_client.py#L26](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/core/exa_client.py#L26)
- 快照层：[snapshot.ts#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/orchestrator/src/snapshot.ts#L1)、[snapshot.ts#L104](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/orchestrator/src/snapshot.ts#L104)、[service.ts#L290](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/orchestrator/src/service.ts#L290)
- 主要 provider 源码：[tencent.py#L64](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/tencent.py#L64)、[sina.py#L78](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/sina.py#L78)、[eastmoney.py#L39](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/eastmoney.py#L39)、[eastmoney.py#L80](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/eastmoney.py#L80)、[ths.py#L16](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/ths.py#L16)、[cninfo.py#L37](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/cninfo.py#L37)、[sec.py#L34](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/sec.py#L34)、[yahoo.py#L26](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/yahoo.py#L26)、[baidu.py#L15](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/baidu.py#L15)、[iwencai.py#L12](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/iwencai.py#L12)、[rss.py#L87](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/rss.py#L87)、[exchange.py#L44](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/exchange.py#L44)、[macro.py#L30](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/macro.py#L30)、[industry.py#L327](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/industry.py#L327)、[policy.py#L24](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/policy.py#L24)、[hiring.py#L32](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/hiring.py#L32)、[probability.py#L46](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/probability.py#L46)、[headlines.py#L29](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/headlines.py#L29)、[commodity.py#L196](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/commodity.py#L196)、[baostock_src.py#L57](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/baostock_src.py#L57)、[mootdx_src.py#L84](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/mootdx_src.py#L84)、[astock_ported.py#L1](https://github.com/simonlin1212/Vibe-Research/blob/db3a7c60afcb643abdf36933294765d1cbfc2b9c/.agents/skills/data-access/scripts/sources/astock_ported.py#L1)

### 上游仓库（许可核对，2026-10-28 GitHub API）

- [`simonlin1212/a-stock-data`](https://github.com/simonlin1212/a-stock-data) — Apache-2.0
- [`simonlin1212/global-stock-data`](https://github.com/simonlin1212/global-stock-data) — Apache-2.0
- [`simonlin1212/investment-news`](https://github.com/simonlin1212/investment-news) — MIT
- [`HKUDS/Vibe-Trading`](https://github.com/HKUDS/Vibe-Trading) — MIT（`backtest/` 上游）

### Financial Bot（本仓库）

- [ashare/data.py#L76](../ashare/data.py#L76)（缓存）、[#L85](../ashare/data.py#L85)（provenance）、[#L96](../ashare/data.py#L96)（A 股链路）、[#L262](../ashare/data.py#L262)（美股）、[#L333](../ashare/data.py#L333)（Finnhub）、[#L391](../ashare/data.py#L391)（akshare 备源）
- [Ashare.py#L11](../Ashare.py#L11)（腾讯 qfq）、[#L46](../Ashare.py#L46)（新浪不复权 K 线）
- [api/services.py#L299](../api/services.py#L299)（TTL 缓存）、[#L386](../api/services.py#L386)（`StockAnalysisService`）、[#L657](../api/services.py#L657)（metadata 构造）、[#L688](../api/services.py#L688)（`NewsService`）、[#L1161](../api/services.py#L1161)（`WebSearchService`）、[#L1246](../api/services.py#L1246)（搜索级联）
- [api/schemas.py#L393](../api/schemas.py#L393)（`ResearchEvidence`）、[#L410](../api/schemas.py#L410)（`AgentResponse`）
- [api/evidence.py#L20](../api/evidence.py#L20)（`sources_for_tool`）
- [api/agent_pydantic.py#L692](../api/agent_pydantic.py#L692)（`retrieve`）、[#L726](../api/agent_pydantic.py#L726)（`record_tool`）、[#L737](../api/agent_pydantic.py#L737)（`tool_context`）、[#L973](../api/agent_pydantic.py#L973)（`validate_sources`）、[#L1459](../api/agent_pydantic.py#L1459)（`web_search`）、[#L1503](../api/agent_pydantic.py#L1503)（`read_public_source`）
- [api/financial_bot/fallback_research.py#L148](../api/financial_bot/fallback_research.py#L148)（metadata 读取）、[#L226](../api/financial_bot/fallback_research.py#L226)（证据构造）、[#L245](../api/financial_bot/fallback_research.py#L245)（复权缺口）
- [api/sec.py#L14](../api/sec.py#L14)、[api/web_research.py#L34](../api/web_research.py#L34)（`PublicDocumentReader`）、[ashare/monitor.py#L375](../ashare/monitor.py#L375)（akshare 新闻）、[ashare/search.py#L27](../ashare/search.py#L27)（suggest）
- [docs/financial-bot-mvp.md](financial-bot-mvp.md)（多人试用门禁关闭）

---

## 11. 复现方式

```bash
# 1) 只读克隆（不要放进 Financial Bot 源码目录）
mkdir -p /tmp/vibe-research && cd /tmp/vibe-research
git clone --depth 1 https://github.com/simonlin1212/Vibe-Research.git
cd Vibe-Research && git rev-parse HEAD
# => db3a7c60afcb643abdf36933294765d1cbfc2b9c

# 2) 端点统计（本报告 §2.1 的数字）
python3 - <<'PY'
import json, collections
r = json.load(open('datasources/registry.json')); eps = r['endpoints']
print(len(eps), len({e['layer'] for e in eps}), len({e['source'] for e in eps}))
print(collections.Counter(e.get('compliance') for e in eps))
print(collections.Counter(str(e.get('auth_env')) for e in eps))
PY

# 3) 主机名统计
grep -rhoE "https?://[a-zA-Z0-9._-]+" .agents/skills/data-access/scripts/{sources,core} \
  | sed 's|https\?://||' | tr 'A-Z' 'a-z' | sort -u | wc -l   # 88

# 4) 逐端点可用性（需联网，本次未执行；只读，不写 Financial Bot）
# python3 .agents/skills/data-access/scripts/fetch_endpoint.py \
#   --endpoint cninfo_announcements --symbol 600519 --out-dir /tmp/vra-probe
# python3 datasources/health.py --only cninfo_announcements,em_reports
```

---

## 11.1 已按本报告落地的改动（2026-10-28）

本次只做本地/内部研究范围，没有开放试用门禁：

- **Phase 0 证据契约**：`ResearchEvidence` 增加 `provider_endpoint / currency / adjustment / raw_ref / raw_sha256 / record_key`（[api/schemas.py](../api/schemas.py)）；`sources_for_tool` 对新字段做字段级映射（[api/evidence.py](../api/evidence.py)）；任务结果同步携带这些字段（[api/financial_bot/executor.py](../api/financial_bot/executor.py)）。
- **A 股复权 provenance**：`DataFetcher` 直接调用新浪/腾讯路径并分别标 `sina/none`、`tencent/qfq`，不再把两者混在 `ashare/unknown` 下（[ashare/data.py](../ashare/data.py)）。
- **Phase 1 四个本机端点**：
  - `get_cninfo_announcements`：巨潮官方公告标题/链接；
  - `get_consensus_estimates`：同花顺 FY 一致预期 EPS（均值/min/max/机构数）；
  - `get_index_constituents`：中证/国证官方指数成分/权重（校验权重合计与单日快照）；
  - `get_financial_statements`：新浪 A 股三表关键科目。
  - 实现位于 [api/local_research/](../api/local_research/)；Agent 工具已注册（[api/agent_pydantic.py](../api/agent_pydantic.py)）。原始正文不落盘，只记 SHA-256。
- **本地范围**：默认本机启用；Supabase 托管认证模式强制关闭；`FINANCIAL_BOT_LOCAL_RESEARCH_DATA=0` 可关闭；`/api/bot/status` 返回 `local_data.scope=local_internal_only`。
- **仍未做**：中证指数成分、东财研报列表、中债/货币网、SEC 等其余 Phase 1 provider；多用户授权、限流分布式化、raw_ref 落盘与删除合同尚未接入。

## 12. 一页纸决策建议

| 问题 | 答案 |
|---|---|
| 能不能接？ | 能，但**只接代码 + 只接指定 provider**，不整包 vendor，不接 C 级与二手转录数据 |
| 先接什么？ | Phase 0 先把 `ResearchEvidence` 扩到能装 `provider/as_of/currency/adjustment/raw_ref`；Phase 1 接巨潮公告、中证成分、东财研报列表、同花顺一致预期、新浪/东财财报、中债曲线 |
| 谁来接？ | Python 适配器（`api/datasources/`），6 个 provider 约 1500 行；baostock/mootdx 走子进程 |
| 最大的坑？ | 不是技术，是**授权**：上游自己写"不得据此主张可发布或再分发"，且 B/C 级源在托管多用户下不成立 |
| 最该抄的？ | 证据信封（`raw_ref + sha256 + as_of + currency + adjustment + record_key`）、`ok/partial/failed` 三态、失败与空结果不写快照 |
| 最该改的（Financial Bot 自身）？ | `Ashare.get_price` 的 A 股路径先新浪不复权、后备腾讯 qfq，但统一标 `ashare/unknown`；应拆开 source/adjustment。`ResearchEvidence` 没有 currency/adjustment/raw，接任何新源都补不上溯源 |
