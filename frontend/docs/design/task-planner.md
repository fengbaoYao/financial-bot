# Persistent Task Planner

## 要解决的问题

金融研究经常需要多个串行步骤：核验标的 -> 取行情/财报/新闻 -> 比较 -> 综合。
单次 Agent 调用容易在工具返回后主观认为“已经完成”，或者长上下文把原始需求挤出窗口。

本轮增加一个**独立于对话上下文的持久化任务栈**：

- Planner 生成结构化步骤；
- 任务栈存 SQLite/PostgreSQL，不放在聊天历史里；
- Executor 仍是现有研究 Agent，但只能读取计划，不能修改计划；
- 服务端从真实工具结果校验步骤，未通过就不能把任务标成 `completed`；
- 前端任务卡片显示每一步状态、校验错误和重试次数。

## 数据

`fb_task_plans`（迁移 `0018_task_plans.sql`）：

| 字段 | 含义 |
| --- | --- |
| `task_id` | 与 `fb_tasks.id` 对应 |
| `owner_id` / `bot_id` / `session_id` | 隔离与归属 |
| `original_query` | 固定保留的原始请求 |
| `status` | planning / running / completed / partial / failed / cancelled |
| `source` | template / llm / fallback |
| `steps_json` | 步骤数组、状态、校验规则、尝试次数、错误 |
| `termination_condition` | 终止条件 |

步骤不依赖聊天历史；刷新页面、重开会话或服务重启后仍能读取。

## Planner

`api/financial_bot/planner.py`：

- 只有复杂/多步骤请求才调用 LLM Planner（减少额外成本）；
- Planner **不挂任何工具**，只输出结构化 `PlanOutput`；
- 最多 6 步；
- 只允许当前系统真实存在的工具；
- 不能规划文件导出、Shell、交易等不存在的能力；
- LLM 失败、超时、输出非法工具时回退到确定性模板计划。

`api/financial_bot/plan.py` 提供确定性计划作为兜底：

- 核验标的；
- 行情 / 财报 / 新闻 / 热点 / 持仓；
- 比较；
- synthesis。

## Executor 与硬校验

现有研究 Agent 继续执行工具，但系统提示词里加入权威任务清单：

- 不能修改、跳过或宣称完成步骤；
- 只有服务端校验全部步骤通过，任务才算完成；
- 缺数据要明确说明。

执行结束后 `evaluate_plan()` 按真实 `research_steps` 校验：

| 校验 | 规则 |
| --- | --- |
| `tool_success` | 指定工具至少成功一次（用于标的核验等） |
| `evidence` | 工具成功且返回实质证据 |
| `min_evidence` | 证据数达到阈值 |
| `summary` | 仅供 synthesis，要求最终回答非空 |

未通过：

- 步骤标记 `retry` 或 `failed`；
- 任务从 `completed` 降级为 `partial`；
- 在 `data_gaps` 写入具体未完成步骤；
- 前端显示“未完成”和重新执行入口。

这一步是防止“模型看到空数组也当成功”的核心 guardrail。

## UI

`TaskPlanCard` 在对话记录中展示：

- Planner 来源（LLM/模板）；
- 原始请求；
- 每一步状态、工具、校验说明、尝试次数、错误；
- 完成数 / 总数；
- 未完成时提示任务按部分完成处理，并提供重新执行任务。

工具事件到来时，匹配的计划步骤会从 `pending` 变为 `running`；最终状态由服务端校验写入。

`GET /api/tasks/{task_id}/plan` 按 owner 返回计划；Trace 的“结构化输出”视图也包含计划 JSON。

## 断点续跑

### 服务重启

启动时 `recover_interrupted_tasks()` 不再一律把运行中的任务标成 `unknown`：

- 有持久化计划、且计划未完成的任务：任务改为 `queued`，原因 `resume_pending`；
  正在运行/重试/失败的步骤回到 `pending`，已完成步骤保留；
- 没有计划的任务：保持原来的 `unknown` 安全行为。

本地内网实例默认自动恢复（`FINANCIAL_BOT_AUTO_RESUME=1`；设为 `0` 可关闭自动恢复、只保留手动按钮）：
`runtime.initialize()` 会对本地 owner 的 `resume_pending` 任务调用
`executor.start(..., resume=True)`。托管模式不自动猜测用户身份，任务留在
`queued`，由已登录用户点击“从断点继续”触发。

### 手动恢复

```text
POST /api/tasks/{task_id}/resume
```

服务端会：

1. 校验 owner 和任务/计划存在；
2. 已完成/取消/正在运行的任务拒绝恢复；
3. 把未完成步骤重置为 `pending`，清空失败尝试次数和错误；
4. 任务改为 `queued`，原因 `resume_requested`；
5. 用 `resume=True` 启动执行器。

恢复时**不会重新调用 Planner**；直接读取 `fb_task_plans` 的权威清单。上次的部分回答
会作为“可能不完整”的上下文注入，最终仍以步骤校验为准。

### 前端

任务清单卡片在未完成时提供“从断点继续”（`Resume from checkpoint`）。
本地自动恢复时任务已经是 `queued/running`，按钮不会重复触发；托管实例由用户手动恢复。

### 边界

- 已完成的步骤会保留，但中断前没有落库的原始工具数据不会凭空恢复；
- 当前恢复是“任务清单级”的，不是每个 step 单独调用一次 LLM；
- 自动恢复只面向本地 owner；托管用户必须显式恢复；
- 并行步骤、每步独立 Executor 仍属于下一阶段。
