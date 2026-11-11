# 数据落在哪（Data layout）

> 一页说清楚：哪些文件是核心数据、哪些可以删、哪些删了会丢什么。
> 所有路径相对源码根目录（磁盘文件夹名仍为 `OpenAshare/`，待手动改名为 `financial-bot-app` 或同类）。

## 一张表

| 路径 | 内容 | 删掉会怎样 |
|---|---|---|
| `data/financial_bot/financial_bot.sqlite3` | **核心库**：对话（threads）、任务（tasks）、消息（messages）、事件、记忆（memories）、提醒（notifications）、投递（deliveries）、产出（artifacts）、Bot 配置与「会话属于哪个 Bot」的绑定表 | 所有对话与研究记录消失，Bot 回到默认配置 |
| `data/user_settings.json` | 服务端模型配置：`llm_api_key`、`llm_base_url`、`updated_at`（Key 不回显） | 需要重新在 `/settings` 填 Key |
| `data/agent_memory.db` | 旧版 Agent 记忆（`/agent` 页面用） | 旧版页面的记忆丢失，不影响 `/bot` |
| `data/monitor.db` | 监控调度（旧版） | 监控记录丢失 |
| `data/portfolio.db` | 持仓数据 | 持仓消失 |
| `data/workspace_watchlist.db` | 自选股 | 自选股消失 |
| `data/last-good-cache.json` / `data/sec_cache/` | 数据源缓存，可重建 | 首次请求变慢 |
| `.env` | 数据源 Key：同花顺 Fuyao、iFinD 等 | 对应数据源不可用，其余功能不受影响 |
| `.next/` `.next-dev/` | 构建产物 | 重新 `npm run build` / `npm run dev` 自动重建 |
| `output/` | 截图与证据脚本产物 | 无影响 |

## 核心库里的三张关键表

| 表 | 说明 |
|---|---|
| `fb_threads` | 对话本身（标题、是否置顶、审批模式、研究模式、所属 Bot） |
| `fb_tasks` + `fb_task_events` | 每次研究任务与它的运行快照（范围、模型、数据源） |
| `fb_bot_sessions` | **会话归属**：一条会话由哪个 Bot 创建。写入永远只允许 Owner Bot，切助手不会搬历史 |

> 一条会话只在**真正产生内容**（发起任务 / 建独立话题）时才落库。
> 打开页面、读状态、标记已读都不会创建空会话。

## 备份 / 迁移

停掉前后端后整目录拷贝即可（SQLite 单文件，WAL 已包含）：

```bash
cd "$(git rev-parse --show-toplevel)"  # 当前文件夹名仍为 OpenAshare
tar czf ~/Desktop/financial-bot-data-$(date +%Y%m%d).tgz data/ .env
```

恢复：解压回源码根目录后重启前后端（文件夹名仍为 OpenAshare）。

## 重置

| 想重置什么 | 做什么 |
|---|---|
| 只清对话 | `rm data/financial_bot/financial_bot.sqlite3`（下次启动自动重建空库） |
| 清对话但保留 Bot 配置 | 界面上「清空全部对话」，或逐个 Bot 清空 |
| 只换模型 Key | `/settings` 重新填，或删 `data/user_settings.json` 后重启 |
| 全清 | `rm -rf data/*`（会同时清掉持仓、自选股、缓存） |

## 测试与脚本的隔离

- Playwright 脚本（`scripts/fb-*-evidence.mjs`、`scripts/fb-bot-scroll-check.mjs`）跑在**临时浏览器上下文**里，localStorage 不落到你的日常浏览器；
- 页面不再因为「打开一次」而建会话（`fb-bot-scroll-check.mjs` 里有断言守着）；
- 但脚本如果点了「加一条盯梢 / 新对话」之类仍然会写真实库 —— 跑完请检查 `GET /api/sessions`，必要时手动删除。
