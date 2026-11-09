# Financial Bot 本机首版：实现、运行与验收边界

状态：本机开发实现已完成并通过自动化测试；**多人试用门禁保持关闭**。未部署、未推送。
上游基线：`359b7676bcb5f7b753a290d26421a2c94e9056f1`（detached HEAD 检出）。
开发分支：`codex/financial-bot-mvp`。

## 1. 交付内容

- 对话首页：`/bot`，自然语言提问 / 研究任务 / 追问；不要求先填档案。
- 真实运行过程：服务端持久化任务与步骤事件，页面区分“已完成 / 正在进行”、失败、部分完成、等待补充、取消、断线未知。
- 右侧（窄屏下移为顶部条）原创 Bot 角色：待命、排队、运行、等待、完成、部分完成、失败、取消、未知；动画绑定服务端状态，`prefers-reduced-motion` 下停止动画且保留文字。
- 长期记忆：显式关注/偏好/研究问题/持仓陈述直接记录；模型观点只能进入待确认候选，逐条确认、修改、忽略后才写入用户观点；乐观锁/409；按 owner 查询与注入。
- 删除：单条与清空全部；立即停止检索；清除记忆本体、版本、候选、引用副本、检索索引、上下文缓存，SQLite `secure_delete + VACUUM + WAL checkpoint`；删除任务逐层记录状态。供应商保留与备份无法证明时状态为 `blocked`，不显示“完全删除”。
- 来源与失败：实际 provider 贯穿 A股/港股/美股；港股弱相关 A 股资讯标记 `topic`/未核实，不充当公司原因。
- 无模型时的腾讯价格场景：确定性来源快照，先测价格窗口并纠正“暴跌”前提，列出公司直接新闻与未核实主题新闻，明确“未配置模型，因果综合分析未运行”。

## 2. 结构

后端新增 `api/financial_bot/`：

| 文件 | 职责 |
| --- | --- |
| `schema.py` | 任务、事件、记忆、候选、索引、删除任务、审计的跨方言表结构 |
| `db.py` | SQLite / PostgreSQL 连接；PG 每事务 `SET LOCAL ROLE authenticated` + JWT `sub`，RLS 为第二边界 |
| `store.py` | 所有读写按 owner 过滤；事件单调 sequence、终态不倒退、候选版本检查与 PG 行锁、删除分层核验 |
| `executor.py` | 线程 + 独立事件循环执行任务；取消、重启恢复 `unknown`、凭据脱敏、结果/用量/候选落库 |
| `fallback_research.py` | 无模型凭证时的价格前提核实与来源快照（不编造原因） |
| `routes.py` | `/api/tasks*`、`/api/memories*`、`/api/memory-candidates*`、`/api/deletion-jobs*`、`/api/bot/status` |
| `auth.py` | 仅服务端解析 owner；本地模式显式标注；托管模式验证 Supabase token |
| `runtime.py` | 进程级 store/executor 生命周期与启动恢复 |

前端新增：

- `app/bot/page.tsx`：对话工作台入口。
- `components/financial-bot/`：工作台、Bot 角色、样式。
- `lib/financial-bot/`：类型、API/SSE 客户端、可测试的状态归约器。
- `supabase/migrations/0006_financial_bot.sql`：托管部署表结构 + 全表 RLS + 最小 grants。
- `tests/test_*`、`tests/financial-bot-state.test.mjs`：行为、并发、越权、删除、真实 PostgreSQL RLS、界面状态测试。

## 3. 合同映射

任务/事件合同（02 §4）直接实现为：

- `POST /api/tasks`（`Idempotency-Key`；返回 `task` 与 `created`）
- `GET /api/tasks?session_id=&limit=`
- `GET /api/tasks/{task_id}`
- `GET /api/tasks/{task_id}/events?after_sequence=N`（`Accept: text/event-stream` 为 SSE，否则 JSON 快照）
- `POST /api/tasks/{task_id}/cancel`

`02` 建议的记忆接口映射：

- `GET /api/memories` → 已确认记忆 + 待确认候选 + 最近删除任务
- `POST /api/memory-candidates/{id}/confirm` / `dismiss`
- `PATCH /api/memories/{id}`（`expected_version`）
- `GET /api/memories/{id}/versions`
- `DELETE /api/memories/{id}`、`DELETE /api/memories`（body `{"confirm":"delete_all"}`）
- `GET /api/deletion-jobs/{id}`、`GET /api/deletion-jobs`
- `GET /api/bot/status`

任务状态：`queued | running | waiting_user | completed | partial | failed | cancelled | unknown`。
事件：`{event_id, task_id, sequence, occurred_at, kind, step_id?, status, display_summary, source_refs?}`。
事件不保存用户原话、模型私有推理、密钥或 provider 错误正文。

## 4. 本地运行

环境实测：macOS / Python **3.12.0**（`/usr/local/bin/python3.12`）/ Node **22.23.3** / npm **10.9.9** / Next **16.3.1** / FastAPI **0.162.0** / pydantic-ai-slim **2.55.0** / SQLite（开发）/ PostgreSQL **17.11**（集成测试）。

```bash
cd /Users/yaoxingyu/文稿/工作面试/financial-bot/Financial Bot
/usr/local/bin/python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements_dev.txt
npm ci
npm run build
```

终端 1：

```bash
.venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8000
```

终端 2：

```bash
npm run start -- -H 127.0.0.1 -p 3000
```

访问：<http://127.0.0.1:3000/bot>。经典工作台仍为 <http://127.0.0.1:3000/work>。

本地数据文件：`data/financial_bot/financial_bot.sqlite3`（已加入 `.gitignore`）。需要重置开发演示数据时，停止服务后删除该目录再启动；这会丢失本机演示任务/记忆，不属于产品删除流程。

### 环境变量（均为可选）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `FINANCIAL_BOT_DATA_DIR` | `data/financial_bot` | SQLite 目录 |
| `DATABASE_URL` | 空 | 设置后使用 PostgreSQL；需先应用 `0006` 迁移 |
| `FINANCIAL_BOT_DAILY_TASK_LIMIT` | `200` | 每 owner 每日任务上限 |
| `FINANCIAL_BOT_DAILY_TOKEN_LIMIT` | `2000000` | 每 owner 每日 token 上限 |
| `FINANCIAL_BOT_DEEP_MODEL_REQUEST_LIMIT` | `12` | 单次深度研究内模型请求次数上限 |
| `FINANCIAL_BOT_DEEP_TOTAL_TOKEN_LIMIT` | 不设/0 | 单次深度研究累计 token 预算；默认不设上限，避免截断综合 |
| `FINANCIAL_BOT_RESEARCH_RATE_LIMIT` | `30` | 跨请求研究/agent 启动频次（滑动窗口）；`0` 关闭 |
| `FINANCIAL_BOT_RESEARCH_RATE_WINDOW_SECONDS` | `3600` | 上述频次窗口秒数 |
| `FINANCIAL_BOT_BACKUP_DIR` | 空 | 独立备份目录；非空且有文件时删除任务保持阻塞 |
| `FINANCIAL_BOT_THIRD_PARTY_RETENTION_VERIFIED` | `false` | 仅运维核实供应商保留后设 true；默认不宣称完全删除 |
| `FINANCIAL_BOT_ALLOW_LOCAL_SINGLE_USER` | `true` | 本机内部模式（可供内部开发共用实例，无账号隔离）；界面上始终显式标注 |

模型凭证仍走既有服务端 `LLM_API_KEY/LLM_BASE_URL/LLM_MODEL` 配置；本文件不读取、不展示、不写入任何密钥。托管模式（Supabase `require_auth`）下，浏览器设置接口继续被既有门禁阻止。

## 5. 测试与验证命令

```bash
# 新增后端测试（不含真实 PG 时 PG 用例 skip；本机实测会执行）
.venv/bin/python -m pytest tests/test_task_events.py tests/test_memory.py \
  tests/test_memory_deletion.py tests/test_market_sources.py \
  tests/test_fallback_research.py tests/test_research_failure_states.py \
  tests/test_financial_bot_limits.py tests/test_financial_bot_postgres.py -q

# 原有后端基线
.venv/bin/python -m pytest tests/test_api_app.py -q

# 全量
.venv/bin/python -m pytest tests -q

# 前端状态归约器与既有 mjs 测试
node --test tests/*.test.mjs

# 生产构建
npm run build
```

已知基线失败（与本次改动无关，已在原始 `359b767` worktree 复现）：

- `tests/test_api_app.py::test_ai_analysis_response_is_private_no_store`
- `tests/test_api_app.py::test_public_analysis_response_keeps_public_cache`

原因：当前安装的 FastAPI 0.162 / Starlette 在 CORS 中间件自动追加 `Vary: Origin`，而旧测试断言 `Vary` 仅为 `Cookie` 或不存在。不是本次业务改动引入。

- `tests/seo.test.mjs` 的“structured data …”用例依赖 `VERCEL_ENV=production`，在默认本地环境失败；与本次改动无关。

## 5.1 本机内部研究数据（可选，默认仅本地）

按 2026-10-28 的 Vibe-Research 尽调（[vibe-research-data-sources.md](vibe-research-data-sources.md)），先接了四个**本机内部研究**端点，不改变试用门禁：

| 工具 | 来源 | 用途 | 约束 |
| --- | --- | --- | --- |
| `get_cninfo_announcements` | 巨潮资讯 | 沪深 A 股官方公告标题/链接 | 只保留标题、日期、链接；不读取正文 |
| `get_consensus_estimates` | 同花顺 | FY 一致预期 EPS（均值/min/max/机构数） | 机构数 < 3 或年度缺失时写入 data_gaps；不得当作确定预测 |
| `get_index_constituents` | 中证/国证指数 | 官方指数成分与权重快照（`csi`/`cni`） | 只接受 6 位指数代码；校验权重合计 99–101%，拒绝混日期/残缺文件 |
| `get_financial_statements` | 新浪财报 | A 股利润表/资产负债表/现金流量表关键科目 | 累计值口径，不拆单季；只保留关键科目，原始响应不落盘 |

- 默认在**本机单实例模式**启用；内部几个开发可以共用同一个本机服务/端口，但它没有账号隔离，仍是同一 owner 工作区。Supabase 托管认证模式强制关闭（`local_research_enabled()` 检查 `_cloud_auth_enabled()`）。
- 可用 `FINANCIAL_BOT_LOCAL_RESEARCH_DATA=0` 关闭。
- 数据授权口径：仅限本机内部研究，**未取得多用户展示、缓存或再分发授权**；不写入仓库、不向试用用户开放。
- 原始第三方正文不落盘，只保存响应 SHA-256 与来源/时间；避免把公告/一致预期全文长期保存。
- 新增 `ResearchEvidence` 字段：`provider_endpoint`、`currency`、`adjustment`、`raw_ref`、`raw_sha256`、`record_key`；任务结果与 `/bot` 来源卡片会展示币种/复权口径（若存在）。
- A 股复权 provenance 已拆开：新浪 K 线标 `sina/none`，腾讯 K 线标 `tencent/qfq`，旧式 `Ashare.get_price` 兜底才标 `ashare/unknown`。

## 6. 真实与模拟边界

已真实执行：

- 本机 SQLite 持久化、任务/事件、SSE、取消、重启 `unknown`、A/B 越权拒绝。
- 本机 PostgreSQL 17 临时数据库：应用 owner 过滤 + **真实 RLS**（`SET ROLE authenticated` + JWT claims）跨 owner 读写拒绝；并发确认候选只产生一份观点。
- 真实行情/新闻网络请求：腾讯控股 `00700.HK` → `yfinance(0700.HK)`、HKD、`auto_adjust`、数据截至 2026-10-28；20 日约 -11.9%，1 日约 +0.6%，页面/接口纠正“暴跌”表述并只列主题级未核实新闻。
- 浏览器（Playwright Chromium）：桌面/窄屏、任务提交、运行步骤、完成、候选确认、修改、单条删除、断线未知、重连恢复、`prefers-reduced-motion`、无横向溢出、无 console error。

未真实验证（阻塞/限制）：

- **真实大模型**：本机无模型凭证；`model.configured=false`。候选观点提取、模型综合、模型失败/超时/预算消耗未做真实模型验收。模型观点字段与确认链路仅用确定性响应验证。
- **Supabase Auth / GoTrue 端到端**：本机无 Supabase 项目；JWT 中间件与 RLS 用模拟 auth 函数 + 真实 PostgreSQL 验证，不能替代 hosted 环境验收。
- **多人试用**：`GET /api/bot/status` 固定返回 `multi_user_open=false`；身份、数据授权、删除范围、供应商保留未全部验收。
- **完全删除**：本机受控层已清除并有物理文件检查；模型供应商输入/输出保留未核实，备份未配置，删除任务按合同显示 `blocked`。恢复备份不复活未在真实备份系统验证。
- **数据授权**：A股/港股/美股行情与新闻的多人展示、缓存、引用授权未核实；iFinD 已作为主源接入（`IFIND_ACCESS_TOKEN`）；额度不足时降级 Fuyao/公开源。
- **研究质量**：三年样本不代表全市场覆盖；“腾讯为什么暴跌”仅在无模型确定性路径验证，真实模型路径未验证；历史 `UnexpectedModelBehavior` 未复现，不能声称修复根因。
- **未构建**：`我的研究`独立页面、持续跟踪任务、提醒、决策/复盘；研究结果不写入长期记忆（仅任务结果与会话）。
- **未部署、未推送、未合并**。

## 7. 本地验收方式

1. 启动 §4 两个服务，打开 <http://127.0.0.1:3000/bot>。
2. 观察顶部明确标注“本机内部开发模式”；右/上 Bot 显示待命。
3. 输入“腾讯股票最近为什么暴跌？”→ 观察“运行过程 / 已完成 / 正在进行”、来源与数据缺口、价格窗口和前提纠正；确认没有把主题级 A 股新闻写成原因。
4. 输入“我关注腾讯，帮我持续跟踪腾讯财报”等显式陈述 → 长期记忆出现关注对象/研究问题；普通提问不生成持仓或动机。
5. 有待确认候选时（真实模型或测试夹具）→ 编辑/确认/忽略；确认后跨会话刷新仍存在；再次修改产生新版本。
6. 删除单条 / 清空全部 → 查看逐层状态；默认应显示阻塞原因（供应商保留 / 备份），不得显示“完全删除”。
7. 运行中点击“停止” → 状态取消并提示外部请求可能已发生；断开事件流 → Bot 显示未知，不继续运行；点击重连后按服务端快照恢复。
8. 窄屏（≤680px）→ Bot 变为顶部紧凑条，输入框可用，无横向滚动；系统“减少动态效果”下动画停止、文字状态保留。

更完整的验收勾选与签收格式见外层 `05_首版验收清单.md`；本文件不代替独立验收。
