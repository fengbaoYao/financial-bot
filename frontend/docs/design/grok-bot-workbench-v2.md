# Financial Bot Workbench v2 — Grok Bot 设计逻辑迁移

参考：
- xAI, *Designing Grok Bot for a world of persistent agents* (2026-09-03)
  https://x.ai/news/designing-grok-bot
- Grok Bot documentation
  https://docs.x.ai/grok-bot/overview

## 0. 一句话设计目标

**用户管理的是“我的 Bot 在替我做什么”，而不是“我昨天聊过什么”。**

Chat 只是进入某个 Bot 的一种方式。Bot 是身份、记忆、Routine、工作区和产物的容器。

## 1. 产品对象

```text
Bot
├── Identity      名称、头像、角色、指令
├── Presence      idle / working / waiting / blocked / done
├── Desk          该 Bot 的默认工作台
├── Chats         与这个 Bot 的多段对话
├── Routines      长期职责：价格、公告、财报日、日报
├── Memory        该 Bot 的长期上下文
└── Artifacts     报告、来源包、导出文件
```

不在主界面暴露：
- 模型参数；
- 工具清单；
- 系统提示；
- 运行环境细节；
- 审批阈值（放入 Bot 设置）。

## 2. 信息架构

```text
Financial Bot
└── Bot roster
    └── Bot
        ├── Desk（默认）
        ├── Chat
        ├── Routine detail
        └── Workspace（preview / takeover）
```

### 桌面布局

```text
┌─────────────────────────────────────────────────────────────────────┐
│ App bar: brand · workspace status · language · classic workspace    │ 56
├──────────────┬──────────────────────────────────┬───────────────────┤
│ Bot roster   │ Bot stage                        │ Bot workspace     │
│ 260          │ minmax(0, 1fr)                   │ 320               │
│              │                                  │                   │
│ Bots         │ [avatar] Bot name   state        │ Presence          │
│ Chats        │                                  │ Tabs              │
│ Routines     │ Transcript                       │ Monitor / Memory  │
│              │  · Routine run                   │ / Status          │
│              │  · User message                  │                   │
│              │  · Research answer               │                   │
│              │  · Artifact / source             │                   │
│              │                                  │                   │
│ Bot settings │ Composer (in flow)               │ Expand → takeover │
└──────────────┴──────────────────────────────────┴───────────────────┘
```

关键：composer 在舞台内部使用 flex 底部区域，不使用 sticky 覆盖内容。

## 3. Bot Desk（默认状态）

当没有选中 Chat 时，不显示一个空洞的聊天框，而显示 Bot 的 Desk：

1. **Header**：头像、名称、presence。
2. **Focus prompt**：今天想让这个 Bot 做什么？
3. **Routines**：下一次运行、最近一次结果。
4. **Recent runs**：Routine 运行卡（可展开、可分析）。
5. **Artifacts**：最近报告/来源包。
6. **Composer**：始终在底部。

Desk 的目标是让用户一眼看到“这个 Bot 正在替我做什么”。

## 4. Bot stage / Chat

### Header

- 头像 + 名称；
- 当前状态（presence）；
- 当前动作一句话；
- `⤢` 打开 workspace；
- `⚙` Bot 设置；
- `新对话`。

### Transcript

混合时间线，按时间顺序：

```text
Routine run card
User message
Bot plan / step group
Research answer
Data card
Sources & gaps
Artifact
```

- 运行步骤默认折叠，只显示摘要；
- 失败/部分完成使用明确状态，不伪装成结论；
- Routine 卡直接出现，不把用户送去另一个通知中心。

### Composer

- 快速 / 深度模式；
- Enter 发送，Shift+Enter 换行；
- 停止按钮在运行中出现；
- 不遮挡 transcript。

## 5. Routine 运行卡

```text
┌──────────────────────────────────────────────────────┐
│ AAPL 波动 · -3.50%                       10:40 PM   │
│ US.AAPL 最新价 350 USD，较前收 -3.50%……             │
│ 数据时间 2026-10-29 · 轮询/延迟数据                  │
│ [详情]                         [分析这次运行]        │
└──────────────────────────────────────────────────────┘
```

- 默认两行摘要；
- 详情展开完整正文；
- 已创建任务时按钮变为「查看研究」；
- 状态：未读 / 已读；
- 失败显示原因和重试入口。

## 6. Workspace：status → preview → takeover

### Status

存在 presence 中：头像状态 + 一行文字。

### Preview

右侧固定 320px：

- Presence；
- Tabs：监控 / 记忆 / 状态；
- 使用低频功能，不打断对话。

### Takeover

全屏工作区：

- 明确标题「Bot 工作区 · 接管」；
- 关闭按钮；
- 导航保持在顶部；
- 内容区可使用多列布局；
- 关闭后回到原 Chat 和滚动位置。

当前实现是全屏单列；后续应改为：

```text
Takeover
├── 顶部：Bot presence + tabs
├── 主列：当前工作（Routine 详情 / 报告 / 通知）
└── 侧列：Routine 列表 / 运行时间线
```

## 7. Avatar / presence 系统

- 统一基础形状：圆角方形 + 两只眼睛 + 嘴；
- 通过颜色、配件和状态动画区分 Bot；
- 不在侧栏重复放状态徽章，状态由头像 + 小圆点承载；
- 状态：
  - idle：静态；
  - working：轻微脉冲；
  - waiting：慢闪；
  - blocked：红色标记；
  - done：绿色标记；
- `prefers-reduced-motion` 下关闭动画，只保留静态状态色。

## 8. 视觉 token

```text
Background      #f7f3ec
Surface         #fffdf9
Ink             #211f1c
Muted           #7f786e
Line            #e4ddd1
Accent          #0f8a7b
Brand           #be5b36
Danger          #b94a3a

Radius          8 / 10 / 14 / 20
Shadow          subtle only on floating surfaces
Type            UI: system sans; numbers: mono
```

原则：
- 纸感暖白，不用大面积渐变 hero；
- 颜色承担状态语义；
- 一屏最多一个主 CTA；
- 设置类控件默认隐藏。

## 9. 响应式

- ≥ 1180：三栏；
- 860–1179：两栏，隐藏 workspace，保留 Expand 入口；
- < 860：单栏；Bot roster 收入抽屉；workspace 变为底部 sheet；
- Composer 固定于舞台底部，不覆盖内容。

## 10. 状态与异常

- Empty：Bot Desk + 示例任务；
- Loading：Bot header presence + 折叠步骤；
- Waiting：澄清卡 + 选项；
- Partial：明确「部分完成」，保留来源与缺口；
- Failed：失败原因 + 重试；
- Cancelled：停止状态 + 已发送外部请求说明；
- No data：显式缺口，不用模型记忆补齐。

## 11. 可访问性

- 所有 icon button 有 aria-label；
- Tabs 使用 role=tablist/tab；
- Focus ring 可见；
- Avatar 状态不只靠颜色，附文字；
- 动画支持 reduced-motion；
- Composer 不被任何浮层遮挡。

## 12. 与当前实现的差距

已实现：
- Bot roster；
- Chat/Routine 左栏；
- Bot stage header；
- Routine transcript cards；
- Workspace preview / takeover；
- Composer 不遮挡。

下一阶段：
1. Bot Desk（默认非空状态）；
2. Routine 卡展开后内嵌完整研报；
3. Takeover 双列布局；
4. Avatar 配件/自定义；
5. 移动端 roster 抽屉和 workspace sheet；
6. Artifact 列表。
