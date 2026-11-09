# Bot Prompt Studio

## 目标

把 Bot 从“一个名字 + 一段 instructions”升级为可配置、可预览、可执行的独立 Agent：

- 每个 Bot 有自己的系统提示词模板、能力权限、长期记忆、Routine 和投递设置。
- 用户改配置时能实时看到最终系统提示词，而不是保存后猜效果。
- 能力开关必须真正影响工具列表，不能只改提示词措辞。
- 用户输入永远作为独立的 `user` 消息发送，不拼进系统提示词。

## 架构决定：服务端同源渲染

GrokBot 风格的产品体验是“前端模板 + 变量插值 + 实时预览”。本项目在此基础上做了一个安全取舍：

- **前端负责编排体验**：模板编辑、变量插入、能力表单、预览面板、保存/校验反馈。
- **服务端负责最终渲染**：`api/financial_bot/prompt.py` 是唯一渲染器；预览接口
  `POST /api/bots/prompt-preview` 和真实执行路径调用同一个函数。
- 这样浏览器不能通过篡改本地状态让“预览”和“实际执行”不一致，也不能绕过
  服务端的权限与安全边界。

## Prompt 分层

真实执行时按以下顺序进入模型：

1. 平台系统规则：`SYSTEM_PROMPT`，包含真实性、证据、市场覆盖和安全规则。
2. 运行时指令：语言、Quick/Deep 研究模式、截止时间与工具策略（由服务端追加）。
3. Bot 系统提示词：本 Studio 渲染的 Bot 层。
4. 长期记忆：仅当前 Bot 的已确认记忆，作为独立 system 区块。
5. 对话历史：当前会话最近若干轮摘要。
6. 用户本轮输入：独立 `user` 消息。

Studio 预览展示 1、3、4、5、6，并明确标注语言/模式指令由运行时追加。

## 模板变量

| 变量 | 来源 |
| --- | --- |
| `{{bot_name}}` / `{{bot_id}}` | Bot 配置 |
| `{{bot_role}}` | “身份与职责” |
| `{{markets}}` | 覆盖市场 |
| `{{capabilities}}` | 已开启的数据能力 |
| `{{permissions}}` | 记忆 / Routine / 主动提醒开关 |
| `{{restrictions}}` | 固定硬边界 |
| `{{now}}` | 服务端当前 UTC 时间 |
| `{{routines}}` | Routine 开关与数量 |
| `{{memory_mode}}` / `{{memory_rule}}` | 长期记忆规则 |
| `{{output_style}}` | 简洁 / 标准 / 详细 |

未知变量会保留为字面文本，并在预览中提示；模板上限 8000 字符。

## 能力即工具权限

`prompt.py` 维护工具到能力组的映射：

| 能力 | 工具 |
| --- | --- |
| 联网检索 | `web_search`, `read_public_source`, `research_company`, `research_comparison` |
| 财报与公告 | SEC、A股公告、财务报表、目标价、一致预期、财务比较 |
| 行情与热点 | 行情技术面、指数成分、市场热点 |
| 公司新闻 | 公司新闻、全球新闻 |
| 只读持仓 | `get_portfolio_analysis` |
| 自选股读写 | `manage_watchlist` |

`agent_pydantic.create_agent()` 注册 `PrepareTools` capability，在每次模型请求前按
`deps.bot_capabilities` 过滤工具定义。关闭一项能力会真正从该次运行的工具列表中移除，
而不是只写一句“不要使用”。

`search_stocks` 属于 core：即使关闭其他能力，也保留标的核验，避免模型靠记忆猜代码。

Routine 与主动提醒开关也会被执行层检查：关闭 Routine 后，新建监控返回
`409 routines_disabled`，后台 worker 跳过该 Bot 的定时检查；关闭主动提醒后，
worker 不再生成新的主动通知。这样开关不是只影响提示词措辞。

## 数据与 API

新增列（SQLite / PostgreSQL 同构）：

- `fb_bots.prompt_template text`
- `fb_bots.prompt_config text`（JSON）
- `fb_bots.prompt_version integer`

接口：

- `GET /api/bots/prompt-defaults`：默认模板、默认配置、变量目录。
- `POST /api/bots/prompt-preview`：用当前草稿渲染预览；可带 `bot_id` 读取该 Bot 的记忆、
  历史轮数和 Routine 数量。
- `POST /api/bots` / `PATCH /api/bots/{id}`：保存模板与结构化配置。

## 前端结构

`components/financial-bot/bot-studio.tsx`：

- 身份：名称、描述、职责、市场、默认回答风格、创建预设。
- 提示词：模板编辑器、变量插入、恢复默认、未知变量提示。
- 能力与权限：能力开关、记忆/Routine/主动提醒开关、固定硬边界。
- 记忆：当前 Bot 记忆统计与最近条目，跳转完整记忆管理。
- 投递：Webhook、邮箱、摘要、免打扰、临时休市、发送测试。
- 高级：审批阈值、Bot 元数据、删除 Bot。
- 右侧固定预览：SYSTEM/USER 分段、字符/token 估算、工具数量、可试输入。

`components/financial-bot/workbench.tsx` 的对话记录新增 `ToolRunCards`：每次工具调用以
可展开卡片展示工具名、状态、来源端点、证据数和获取时间；实时步骤在运行时展开。

## 安全边界

- 用户输入不进入系统模板；预演输入只渲染到 USER 层。
- 模板不能关闭平台保护规则；证据、引用、禁止交易和 Bot 隔离在模板之外执行。
- 预览接口按 owner 读取 Bot；不能预览其他所有者的 Bot。
- 没有交易执行；能力开关只影响只读研究工具。


UI 展示块、UI 会话状态和 Trace 调试面板的实现边界，见
[ui-ux-prompt-protocol.md](ui-ux-prompt-protocol.md)。
