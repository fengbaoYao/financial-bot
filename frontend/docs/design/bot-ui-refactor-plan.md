# Bot UI Refactor — Implementation Plan

Status: approved direction; implement in order T1 → T5.
Baseline: `29f3ab5` (research doc committed).
Scope: `/bot` only. Do not remove `/work` yet in this refactor; that is a separate
product migration after Work cards are stable.

## Product decisions already made

- Bot is the primary object; Chat is one view of a Bot.
- Work is per Bot, not a global page. Cards are durable research, routine runs,
  approvals and exports only.
- Card actions return to the source Bot conversation.
- Do not replace Financial Bot with OpenMausBot/Rakazo/Codync. Port interaction
  patterns only; keep FastAPI + financial provenance/approval backend.

## Reference sources (verify before assuming)

- Design essay: https://x.ai/news/designing-grok-bot
- OpenMausBot: https://github.com/milind-soni/OpenMausBot
  - `docs/screenshots/hero.png`, `approval-card.png`, `computer-panel.png`
- Rakazo: https://github.com/elie222/rakazo
  - `docs/readme-hero.png`
- Codync: https://github.com/leepokai/Codync
  - `docs/screenshots/desktop-macos.png`

Correction: the supplied `milind-soni/MausBot` and `codex-system/codync` URLs are
404; the actual repositories are OpenMausBot and leepokai/Codync.

## Current implementation map

- Page shell: `components/financial-bot/workbench.tsx`
- Styles: `components/financial-bot/financial-bot.module.css`
- API client: `lib/financial-bot/client.ts`
- State helpers: `lib/financial-bot/state.ts`
- Task executor: `api/financial_bot/executor.py`
- Routes: `api/financial_bot/routes.py`
- Tests: `tests/financial-bot-state.test.mjs`, `tests/test_research_failure_states.py`

Current approval flow:
1. `submit()` calls `preflightBot()`.
2. If approval is required, `pendingApproval` state opens a modal.
3. Confirm calls `startTask(..., modelApproved=true)`.

Current trace flow:
- `RunTimeline` is expanded inside `TaskView`; steps can be long.
- `TaskView` owns run/status/evidence rendering.

## T1 — Inline approval / clarification cards

Goal: approval and clarification happen in the transcript, not in a blocking
modal. No model call before confirmation.

Implementation:
- Create a reusable `AgentDecisionCard` component with variants:
  `approval`, `clarification`, `error`.
- Replace `pendingApproval` modal with an inline card rendered at the end of the
  transcript (or immediately after the user message once messages exist).
- Keep the existing `pendingApproval` data shape for the first slice; no backend
  migration is required for T1.
- Card actions:
  - `允许本次调用` → `startTask(query, undefined, botId, true)`
  - `取消` → clear pending state, restore input if needed
- Prevent duplicate submission while the card is open.
- Clarification cards already exist in `/bot`; migrate their visual shell to the
  same card component so the interaction is consistent.
- Do not implement external-send approvals in T1.

Acceptance:
- No blocking modal for model approval.
- Pending card shows Bot, query, model, estimated credits, threshold.
- Confirm starts task; cancel does not call the model.
- Works at 1440 and mobile widths.
- `npm run build`, Node tests, and a browser smoke test pass.

## T2 — Trace Sheet

Goal: the main transcript shows final answer/status/approval; full run detail is
one tap away.

Implementation:
- Add `TraceSheet` component, side panel on desktop, bottom sheet under 860px.
- Task card gets `查看过程` action.
- Move `RunTimeline` content into `TraceSheet`; keep a one-line current status and
  compact completed count in the task card.
- Preserve live updates from the existing `run` state and stop/cancel controls.
- On close, restore focus and scroll.

Acceptance:
- Long runs no longer dominate the transcript.
- Trace can be opened/closed during a run.
- Failed/partial reasons remain visible in Trace.
- No duplicated run event logic.

## T3 — Bot workspace

Goal: right rail answers “what is this Bot doing?” not “where are settings?”.

Implementation:
- Presence strip: avatar, state, current action.
- `Routine` list with next run/status.
- `Current run` summary.
- `Recent outputs` (artifacts).
- Preview / Takeover; takeover two columns.
- No real VM/computer; this is a research workspace.

Acceptance:
- Workspace reads as a work surface, not a settings panel.
- Routine and output entries open their source conversation/task.
- Mobile uses a sheet.

## T4 — Bot Home / roster empty state

Goal: no empty chat box. When no Bot/session is selected, show a roster/home
state with `New Bot` and recent Bots.

Implementation:
- Add `BotHome` empty state.
- Keep current Bot roster.
- If there are no Bots, show a single primary CTA.
- Do not remove the existing default Bot migration.

Acceptance:
- New user sees a meaningful next step.
- `New Bot` opens the create dialog and selects the new Bot.
- Existing sessions still load.

## T5 — Dark chat theme

Goal: optional dark messaging canvas, consistent with the reference products,
without changing the backend.

Implementation:
- Theme values in CSS variables; scope to the `/bot` shell.
- Modes: system / light / dark; persist preference.
- Apply to app bar, roster, stage, cards, composer, trace sheet.
- Ensure contrast for status, danger, citations, disabled controls.
- Do not force `/work` into the theme.

Acceptance:
- All three modes work and persist.
- Light remains the current paper style.
- Dark transcript remains readable; source/citation chips have contrast.

## Test and delivery rules

- After each task: build, Node tests, backend tests if touched, browser screenshot.
- Do not start the next task until the current one is verified.
- Do not copy upstream code/assets; reproduce interaction patterns with our own
  components and tokens.
- Keep `data/stock_pool.json` restored after test queries (generated cache).
