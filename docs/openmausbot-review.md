# OpenMausBot review and Financial Bot adoption

Reviewed repository: [`milind-soni/OpenMausBot`](https://github.com/milind-soni/OpenMausBot) at
commit `b1e0ba941e5c0aa5ef4c7543edbab04b98253e80`.

## What it is

OpenMausBot is an Electron/React chat app plus a Node **harness server** that drives local
agent CLIs (Claude, Codex, Grok). Its important architectural choices are:

- every Bot is a long-lived contact with its own identity, model, memory, computer and apps;
- a Bot owns **threads**, not one global chat;
- each thread has its own model/approval preference, unread state, Stop control and run state;
- the harness normalizes provider-native protocols into one **canonical runtime event stream**
  (text deltas, tool steps, approvals, wait state, usage, errors);
- a permission broker turns every risky action into an inline Allow/Deny/answer card;
- routines are normal scheduled/webhook threads with checks-before-model, cursors, dedupe,
  quiet results, backoff and a small structured result contract;
- folders/channels organize threads without cloning Bot memory or identity.

Source anchors:

- `README.md`, `docs/plans/2026-09-09-independent-threads.md`,
  `docs/plans/2026-09-09-thread-aware-bots.md`
- `docs/proactive-routines.md`, `docs/verification/bot-continuity.md`
- `server/contracts.ts`, `shared/runtime-events.ts`,
  `server/harness/live-steps.ts`, `server/routes/threads.ts`
- `src/components/bot-panel/Conversations.tsx`,
  `src/components/thread-approval.tsx`

## Decision

**Do not replace the Python research engine, FastAPI API or Next.js UI with the OpenMausBot
Electron/Node stack.** The stacks solve different problems:

| Area | OpenMausBot | Financial Bot |
|---|---|---|
| Runtime | Electron + Node + local agent CLIs | Next.js + Python/FastAPI |
| Agent | Claude/Codex/Grok CLI, MCP tools | PydanticAI + stock/news/filings tools |
| Data | local files, connected apps, cloud computer | market data, financial statements, evidence, citations |
| Safety | shell/file/computer approvals | read-only market research; model-budget approval |
| Domain | general personal agent | regulated financial research UX |

A wholesale rewrite would discard the evidence pipeline, provider fallbacks, test suite and
financial data contracts. The **interaction architecture** is what is worth rebuilding.

## Adopt — P0 (this/next iteration)

1. **Thread-first model**
   - A Bot owns folders and threads.
   - A thread is a durable conversation with its own messages, tasks, status, unread state,
     pin/archive, model/approval preference and Stop.
   - Current `fb_threads` + `fb_sessions` are the seed; extend them instead of adding another
     conversation system.

2. **Thread lifecycle actions**
   - Pin, rename, archive, delete, mark read.
   - Current implementation already adds pin/rename/archive/delete/read APIs and the
     `⋯` menu in the roster.

3. **Canonical event stream**
   - One event shape for `token`, `step`, `activity`, `answer`, `error`, `waiting`, `usage`.
   - The current `fb_task_events` is close; keep it as the server-owned source of truth and
     never derive run state from React state alone.

4. **Progressive execution**
   - One-line current activity, expandable tool list, approval cards inline.
   - Current `Execution details` and token stream already follow this direction.

5. **Unread / active-in-background**
   - An answer in a non-active thread increments its unread badge; opening clears it.
   - This already lands with `fb_threads.unread` and `last_read_at`.

## Adopt — P1

1. **Approval broker**
   - Structured approval cards with Allow/Deny/answer and a reason.
   - Finance tools are mostly read-only, but model-budget, external delivery and future
     browser/computer actions need the same inline contract.

2. **Routine checks-before-model**
   - Run cheap checks first (price threshold, announcement cursor, earnings date).
   - No new data means no model turn, no run card, only "checked at".
   - Keep cursor, dedupe ids, quiet structured result, backoff and per-day limits.

3. **Folders / channels**
   - Work, Personal, project folders; each has a transcript, instructions and roster.
   - Do not clone Bot memory when creating a folder.

4. **Per-thread model/approval**
   - Thread-level model selection and approval mode, with an explicit "also update Bot
     default" choice—not an implicit global change.

## Do not adopt

- Electron shell, local CLI process supervision, cloud computers, Composio marketplace,
  voice/call stack, team sharing, MCP control plane and enterprise access model as a first
  step. They are real but orthogonal to the financial research product and add security surface.
- The full server/store architecture. Reuse the **contracts and interaction invariants**, not
  the implementation.

## Current Financial Bot mapping

| OpenMausBot pattern | Financial Bot counterpart | Status |
|---|---|---|
| Bot + threads | `fb_bots`, `fb_bot_sessions`, `fb_threads` | partial |
| Thread actions | roster `⋯`: pin/rename/archive/delete/read | implemented |
| Canonical events | `fb_task_events`, `RunState`, SSE | implemented |
| Streaming | token events + validated answer chunks | implemented |
| Unread | `fb_threads.unread`, `last_read_at` | implemented |
| Routines | `fb_monitors` + notifications | basic |
| Approval broker | preflight model budget + inline card | partial |
| Folders | `fb_threads.folder` | data only |
| Per-thread model | not yet | open |
| Per-thread approval | not yet | open |

## Recommended sequence

1. Finish thread settings: folder UI, per-thread model/approval, archive view.
2. Move routine checks into a small Python scheduler contract with cursor/dedupe/quiet/backoff.
3. Introduce a general approval card model for model budget, external delivery and any future
   side-effecting tool.
4. Keep the financial engine and evidence tests untouched while the conversation shell evolves.

This gives the OpenMausBot/Grok Bot shape without replacing the parts that make Financial Bot
a trustworthy research product.

## P1 implementation status

- Thread metadata now includes `approval_mode`, `research_mode`, `folder`, `pinned`,
  `archived`, `unread`, `last_read_at`.
- The roster `⋯` menu exposes pin, rename, archive, delete, approval mode,
  research mode and folder.
- Archived threads can be revealed with `Show archived`.
- Monitor polling now records `failure_count` / `retry_after` and performs
  exponential backoff (15m → 4h) before the next due check. A quiet check
  records `last_checked_at` without creating a notification or model run.
- Per-thread model selection remains a follow-up because the current Python
  runtime owns a single server-level model; research mode is the per-thread
  effort setting that is actually wired through task creation.
