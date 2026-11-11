import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";

const source = await readFile(new URL("../lib/financial-bot/state.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const stateModule = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);
const {
  EMPTY_RUN,
  applyTaskEvent,
  botStateFromTask,
  isTerminal,
  startRun,
  completedSteps,
  activeSteps,
  statusLabel,
  botCopy,
  needsAttention,
  findingNotices,
} = stateModule;

let eventCounter = 0;
function event(sequence, kind, status, overrides = {}) {
  eventCounter += 1;
  return {
    event_id: `evt-${eventCounter}`,
    task_id: "task-1",
    sequence,
    occurred_at: "2026-10-28T05:00:00+00:00",
    kind,
    step_id: null,
    status,
    display_summary: `${kind}:${status}`,
    source_refs: [],
    ...overrides,
  };
}

test("duplicate and out-of-order events are ignored", () => {
  let run = startRun("task-1");
  const first = event(1, "status", "queued");
  run = applyTaskEvent(run, first);
  run = applyTaskEvent(run, first);
  assert.equal(run.events.length, 1);
  run = applyTaskEvent(run, event(3, "step", "running", { step_id: "tool:web_search" }));
  run = applyTaskEvent(run, event(2, "step", "running", { step_id: "tool:get_stock_news" }));
  assert.deepEqual(run.events.map((item) => item.sequence), [1, 3]);
  assert.equal(run.lastSequence, 3);
});

test("a terminal task never returns to running from a late event", () => {
  let run = startRun("task-1");
  run = applyTaskEvent(run, event(1, "status", "running"));
  run = applyTaskEvent(run, event(2, "status", "completed"));
  assert.ok(isTerminal(run.status));
  run = applyTaskEvent(run, event(3, "status", "running"));
  assert.equal(run.status, "completed");
  run = applyTaskEvent(run, event(4, "step", "running", { step_id: "tool:web_search" }));
  assert.equal(run.status, "completed");
  assert.equal(run.steps.length, 1);
});

test("steps update in place instead of duplicating", () => {
  let run = startRun("task-1");
  run = applyTaskEvent(run, event(1, "step", "running", { step_id: "tool:web_search" }));
  run = applyTaskEvent(
    run,
    event(2, "step", "completed", { step_id: "tool:web_search", display_summary: "检索完成" }),
  );
  assert.equal(run.steps.length, 1);
  assert.equal(run.steps[0].status, "completed");
  assert.equal(run.steps[0].summary, "检索完成");
  assert.equal(completedSteps(run).length, 1);
  assert.equal(activeSteps(run).length, 0);
});

test("the answer event is recorded without inventing completion", () => {
  let run = startRun("task-1");
  run = applyTaskEvent(run, event(1, "status", "running"));
  run = applyTaskEvent(run, event(2, "answer", "ready"));
  assert.equal(run.answerReady, true);
  assert.equal(run.status, "running");
  run = applyTaskEvent(run, event(3, "status", "partial"));
  assert.equal(run.status, "partial");
});

test("bot state is derived from authoritative status and connection", () => {
  assert.equal(botStateFromTask(null, "idle"), "idle");
  assert.equal(botStateFromTask("queued", "connecting"), "queued");
  assert.equal(botStateFromTask("running", "live"), "running");
  assert.equal(botStateFromTask("waiting_user", "live"), "waiting");
  assert.equal(botStateFromTask("completed", "live"), "completed");
  assert.equal(botStateFromTask("partial", "live"), "partial");
  assert.equal(botStateFromTask("failed", "live"), "failed");
  assert.equal(botStateFromTask("cancelled", "live"), "cancelled");
  // A broken connection must not keep showing a running character.
  assert.equal(botStateFromTask("running", "offline"), "unknown");
  assert.equal(botStateFromTask("running", "reconnecting"), "unknown");
  assert.equal(botStateFromTask("unknown", "live"), "unknown");
});

test("another task's events never leak into the current run", () => {
  let run = startRun("task-1");
  run = applyTaskEvent(run, event(1, "status", "running"));
  run = applyTaskEvent(run, event(2, "step", "running", { task_id: "task-2" }));
  assert.equal(run.events.length, 1);
  assert.equal(EMPTY_RUN.taskId, null);
});

test("task step endpoint is carried for the live interface display", () => {
  let run = startRun("task-1");
  run = applyTaskEvent(run, event(1, "step", "running", {
    step_id: "tool:get_stock_analysis",
    source_refs: [{ type: "endpoint", name: "yfinance_adapter(AAPL)" }],
  }));
  assert.equal(run.steps[0].endpoint, "yfinance_adapter(AAPL)");
  run = applyTaskEvent(run, event(2, "step", "completed", {
    step_id: "tool:get_stock_analysis",
    source_refs: [{ type: "endpoint", name: "yfinance_adapter(AAPL)" }],
  }));
  assert.equal(run.steps[0].endpoint, "yfinance_adapter(AAPL)");
});

test("dropped is a neutral terminal state, never a completion", () => {
  assert.equal(isTerminal("dropped"), true);
  assert.equal(botStateFromTask("dropped", "live"), "ended");
  assert.equal(botStateFromTask("dropped", "offline"), "ended");
  assert.equal(statusLabel("dropped", "zh"), "无需处理");
  assert.equal(statusLabel("dropped", "en"), "Nothing to do");
  assert.equal(botCopy("ended", "zh").title, "已结束");
});

test("waiting labels name what the person must do", () => {
  assert.equal(statusLabel("waiting_user", "zh", "question"), "等你补充");
  assert.equal(statusLabel("waiting_user", "zh", "confirmation"), "等你确认观点");
  assert.equal(statusLabel("waiting_user", "zh", "permission"), "等你授权");
  assert.equal(statusLabel("waiting_user", "en", "confirmation"), "Waiting for your confirmation");
  // An unknown or missing reason still says something actionable.
  assert.equal(statusLabel("waiting_user", "zh", null), "等你补充");
  assert.equal(botStateFromTask("waiting_user", "live", "permission"), "waiting");
});

function attentionTask(overrides = {}) {
  return {
    id: "task-1",
    session_id: "session-1",
    query: "研究腾讯",
    language: "zh",
    research_mode: "quick",
    status: "completed",
    result: null,
    error_code: null,
    error_message: null,
    cancel_requested: false,
    input_tokens: 0,
    output_tokens: 0,
    cost_usd: 0,
    created_at: "2026-11-02T05:00:00+00:00",
    updated_at: "2026-11-02T05:00:00+00:00",
    sequence: 1,
    ...overrides,
  };
}

function attentionCandidate(overrides = {}) {
  return {
    id: "candidate-1",
    task_id: "task-1",
    proposed_text: "腾讯的广告收入增速在放缓",
    status: "pending",
    version: 1,
    created_at: "2026-11-02T05:00:00+00:00",
    ...overrides,
  };
}

test("needs-you summary is empty and short when nothing is blocked", () => {
  const summary = needsAttention(
    [attentionTask({ status: "completed" }), attentionTask({ id: "task-2", status: "dropped" })],
    [attentionCandidate({ status: "confirmed" })],
    "zh",
  );
  assert.deepEqual(summary.items, []);
  assert.equal(summary.empty, "现在没有需要你决定的事。");
  assert.equal(needsAttention([], [], "en").empty, "Nothing needs your decision right now.");
});

test("needs-you count always equals the list it points at", () => {
  const summary = needsAttention(
    [
      attentionTask({ id: "task-q", status: "waiting_user", waiting_reason: "question", attention: { question: "要比较哪两只股票？" } }),
      attentionTask({ id: "task-p", status: "waiting_user", waiting_reason: "permission", query: "读取我的持仓" }),
      attentionTask({ id: "task-f", status: "failed", error_message: "数据源超时" }),
      attentionTask({ id: "task-pt", status: "partial", query: "对比腾讯与阿里" }),
      // None of these need the person, so none may be counted.
      attentionTask({ id: "task-ok", status: "completed" }),
      attentionTask({ id: "task-end", status: "dropped" }),
      attentionTask({ id: "task-cancel", status: "cancelled" }),
    ],
    [attentionCandidate(), attentionCandidate({ id: "candidate-2" }), attentionCandidate({ id: "candidate-old", status: "dismissed" })],
    "zh",
  );
  // The badge renders summary.items.length, so asserting the list length is
  // asserting the count. 2 candidates + 2 waiting + 2 retryable = 6.
  assert.equal(summary.items.length, 6);
  assert.equal(summary.items.filter((item) => item.kind === "confirmation").length, 2);
  assert.equal(summary.items.filter((item) => item.kind === "question").length, 1);
  assert.equal(summary.items.filter((item) => item.kind === "permission").length, 1);
  assert.equal(summary.items.filter((item) => item.kind === "failed").length, 1);
  assert.equal(summary.items.filter((item) => item.kind === "partial").length, 1);
  // Every row is unique and actionable.
  assert.equal(new Set(summary.items.map((item) => item.id)).size, summary.items.length);
  assert.ok(summary.items.every((item) => item.title && item.detail));
});

test("needs-you rows reuse the shared labels and jump targets", () => {
  const summary = needsAttention(
    [
      attentionTask({ id: "task-q", status: "waiting_user", waiting_reason: "question", attention: { question: "  要比较\n哪两只股票？ " } }),
      attentionTask({ id: "task-f", status: "failed", error_message: "数据源超时" }),
    ],
    [attentionCandidate({ id: "candidate-1", task_id: "task-q" })],
    "zh",
  );
  const question = summary.items.find((item) => item.taskId === "task-q" && item.kind === "question");
  assert.equal(question.title, statusLabel("waiting_user", "zh", "question"));
  assert.equal(question.detail, "要比较 哪两只股票？");
  const failure = summary.items.find((item) => item.kind === "failed");
  assert.equal(failure.title, "失败");
  assert.equal(failure.detail, "数据源超时");
  // Confirming a candidate takes the person back to the card in the thread.
  const proposal = summary.items.find((item) => item.candidateId === "candidate-1");
  assert.equal(proposal.taskId, "task-q");
});

test("findings surface in order while a run is live", () => {
  let run = startRun("task-1");
  run = applyTaskEvent(run, event(1, "notice", "completed", { display_summary: "估值：PE(TTM) 21.9" }));
  run = applyTaskEvent(run, event(2, "notice", "completed", { display_summary: "公告：4 条，最新 2026-10-31" }));
  run = applyTaskEvent(run, event(3, "notice", "failed", { display_summary: "不该出现" }));
  const notices = findingNotices(run);
  assert.deepEqual(notices.map((item) => item.text), [
    "估值：PE(TTM) 21.9",
    "公告：4 条，最新 2026-10-31",
  ]);
});

test("dismissed items leave the needs-you list without touching the task", () => {
  const waiting = { id: "task-a", status: "waiting_user", waiting_reason: "question", query: "茅台", attention: { question: "哪只？" } };
  const failed = { id: "task-b", status: "failed", query: "腾讯", error_message: "超时" };
  const all = needsAttention([waiting, failed], [], "zh");
  assert.equal(all.items.length, 2);
  const hidden = needsAttention([waiting, failed], [], "zh", ["task:task-b"]);
  assert.deepEqual(hidden.items.map((item) => item.id), ["task:task-a"]);
  assert.deepEqual(needsAttention([waiting], [], "zh", ["task:task-a"]).items, []);
});
