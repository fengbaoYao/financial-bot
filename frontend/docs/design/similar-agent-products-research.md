# Similar Agent Products — Verified Research

Date: 2026-10-30

Purpose: verify the repos supplied for the Grok Bot comparison, inspect their
actual UI/product shape, and decide what to port into Financial Bot rather than
blindly copying or replacing the app.

## Link verification

| Supplied link | Result | Actual project |
| --- | --- | --- |
| github.com/milind-soni/MausBot | 404 | `github.com/milind-soni/OpenMausBot` (also called MausBot) |
| github.com/codex-system/codync | 404 | `github.com/leepokai/Codync` |
| github.com/elie222/rakazo | 200 | Rakazo |
| github.com/codeaashu/grokbot-shim | 200 | protocol shim, not a product UI |
| github.com/RongleCat/awesome-grok-bot | 200 | curated list, not runnable |
| github.com/xai-org/grok-build | 200 | coding TUI, not Grok Bot |

## 1. OpenMausBot (closest UI/feature match)

- Repo: https://github.com/milind-soni/OpenMausBot
- License: Apache 2.0
- Stack: TypeScript, React 19, Electron; local harness on `127.0.0.1`.
- Product shape: Telegram/iMessage-like dark chat. Every left-side chat is a
  real agent with a colored character avatar, its own model, computer and
  connected apps. Inline approval/question cards. Top bar has `+ Task` and a
  per-Bot model picker. Bottom has `Automations` and `Plugins`.
- Relevant screenshots: `docs/screenshots/hero.png`,
  `docs/screenshots/approval-card.png`, `docs/screenshots/computer-panel.png`.

Port:
- dark messaging canvas and roster readability;
- explicit model/persona in the Bot header;
- inline approval/clarification cards instead of a detached modal for every action;
- Automations as a first-class left-rail item.

Do not port:
- Electron/local VM harness;
- Claude/Codex CLI process management;
- Composio marketplace.

## 2. Rakazo (best layout reference)

- Repo: https://github.com/elie222/rakazo
- License: Apache 2.0 (README declares it)
- Stack: TypeScript, React, Vite, Tailwind, Hono/oRPC, PostgreSQL/Prisma,
  Docker/E2B/Daytona sandboxes, Electron/Expo.
- Product shape: persistent Bot roster; chat in the center; **right-hand "Bot's
  computer" panel** with live screen preview and "Take control"; Routines listed
  with next run; action/result lines inside the chat.
- Screenshot: `docs/readme-hero.png`.

Port:
- right-side workspace that shows what the Bot is doing and supports
  preview → takeover;
- Routines beside the chat rather than buried in settings;
- compact action/result lines inside the transcript.

Do not port:
- sandbox providers and Docker supervisor;
- Prisma/Postgres stack;
- mobile/Electron shells.

## 3. Codync (best minimal roster + trace reference)

- Repo: https://github.com/leepokai/Codync
- License: Apache 2.0
- Stack: Rust host + SwiftUI/Electron clients + ACP agents.
- Product shape: very dark, minimal Bot roster; when no Bot is selected, a large
  empty state and `New Bot`; one persistent chat per Bot; group chats; approval
  cards; trace sheet with full tool detail; "needs you / done" notifications.
- Screenshot: `docs/screenshots/desktop-macos.png`.

Port:
- roster spacing and empty state;
- one persistent chat per Bot (our current per-chat threads can remain, but the
  default should feel like returning to the same Bot);
- trace sheet: chat shows only final replies/approvals/notices, full tool trace
  behind one tap;
- approval and "needs you" notification semantics.

Do not port:
- coding-agent/runtime, ACP registry, Rust host;
- mobile pairing/remote screen.

## 4. grokbot-shim

- Repo: https://github.com/codeaashu/grokbot-shim
- Not a full UI. It runs the installed Grok Bot desktop app against a local host
  shim and a local noVNC computer on Linux. It depends on undocumented Grok Bot
  internals and extracting the installed application.
- Verdict: useful for studying Grok Bot's agent loop/protocol; not suitable as a
  dependency or replacement for Financial Bot.

## 5. awesome-grok-bot

- Repo: https://github.com/RongleCat/awesome-grok-bot
- A 2,000+ entry curated list, not runnable. Useful for official links, field
  cases, and discovering patterns. Do not treat it as an implementation.

## 6. xAI Grok Build

- Repo: https://github.com/xai-org/grok-build
- Terminal coding agent/TUI in Rust. It is not Grok Bot and has no multi-Bot
  roster or per-Bot chat/computer UI.
- Verdict: not a UI replacement.

## Recommendation

**Do not replace Financial Bot with any of these projects.**

The stack mismatch is large (Electron/Rust/Postgres/sandboxes) and they are
coding-agent products, while Financial Bot is a financial research product with
provenance, regulatory language, model-call approval, and local data sources.

Use them as interaction references:

1. **OpenMausBot** → dark messaging canvas, inline approvals, model/persona in
   the Bot header, Automations in the left rail.
2. **Rakazo** → right-side "Bot workspace" with preview/takeover and Routines
   next to chat.
3. **Codync** → minimal roster, trace sheet, "needs you/done" semantics.

The current Financial Bot direction (Bot-first roster, per-Bot Work cards, Chat vs
Work, preview/takeover) is compatible with all three. The missing pieces are
visual and interaction-level, not a platform replacement.

## What to implement next

- Dark chat surface option (or full dark theme) with the current Bot roster.
- Inline approval/card pattern for model calls and actions.
- "Trace" sheet: transcript shows final result only; tool details one tap away.
- Right-side workspace with live Bot status and a clear takeover control.
- Group-chat/bot-to-bot delegation is a later feature; not needed for the first
  financial workflow.
