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
  openMuseStatusCopy,
  financialResearchModeErrorCopy,
  openMuseTaskStatus,
  openMuseDeletionPending,
  openMuseDeletionPendingCopy,
  researchModeDisplayName,
  researchModelDisplayName,
  openMuseResearchResult,
} = stateModule;

test("financial bridge errors explain the unavailable source bridge", () => {
  const zh = financialResearchModeErrorCopy("zh");
  const en = financialResearchModeErrorCopy("en");
  assert.match(zh, /金融数据桥当前不可用/);
  assert.match(en, /source bridge is unavailable/);
});

test("OpenMuse scope and model metadata are not attributed to Quick or the selected Financial Bot model", () => {
  assert.equal(researchModeDisplayName("openmuse_shared", "zh"), "自由研究");
  assert.equal(researchModeDisplayName("openmuse_shared", "en"), "Free research");
  assert.equal(researchModeDisplayName("deep", "zh"), "深度研究");
  assert.equal(researchModelDisplayName("openmuse_shared", "ChatGPT", "zh"), "由 OpenMuse 服务端配置");
  assert.equal(researchModelDisplayName("openmuse_shared", "ChatGPT", "en"), "Configured on OpenMuse server");
  assert.equal(researchModelDisplayName("quick", "DeepSeek Flash", "zh"), "DeepSeek Flash");
});

test("OpenMuse result projection retains source metadata and explicit coverage gaps", () => {
  const result = openMuseResearchResult(
    { summary: "Research summary", gaps: ["One region has no current source."], evidence: [{ title: "Annual report", url: "https://example.test/report", retrievedAt: "2026-11-05T07:00:00Z", excerpt: "Revenue grew." }] },
    [{ id: "source-2", source: "Company site", title: "News", url: "https://example.test/news", data_as_of: "2026-11-04" }],
  );
  assert.equal(result.summary, "Research summary");
  assert.deepEqual(result.data_gaps, ["One region has no current source."]);
  assert.equal(result.evidence.length, 2);
  assert.equal(result.evidence[0].url, "https://example.test/report");
  assert.equal(result.evidence[0].source, "__unattributed__");
  assert.equal(result.evidence[0].excerpts[0].text, "Revenue grew.");
  assert.equal(result.evidence[1].data_as_of, "2026-11-04");
  assert.equal(result.meta, undefined, "do not infer a verification basis from source presence");
});

test("choosing OpenMuse shared research routes every query there without keyword auto-switching", async () => {
  const workbench = await readFile(new URL("../components/financial-bot/workbench.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(workbench, /looksLikeFinancialResearch|Switching to Quick|这看起来是金融问题/);
  assert.doesNotMatch(workbench, /Market data and investment research continue through Quick or Deep|行情和投资研究仍走快速或深度模式/);
  assert.match(workbench, /Recognized financial questions use the Financial Bot data bridge/);
  assert.match(workbench, /已识别的金融问题会调用 Financial Bot 数据桥/);
  assert.match(workbench, /未返回结构化来源记录/);
  assert.match(workbench, /未返回结构化缺口清单/);
  assert.match(workbench, /本次使用 OpenMuse 服务端配置的模型/);
  assert.match(workbench, /showAnswer=\{assistantMessages\.length === 0\}/);
  assert.match(workbench, /if \(researchMode === "openmuse_shared"\) \{\s*await startTask\(query, displayQuery, botId, false, "ask"\);\s*return;/);
});

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

test("OpenMuse status copy separates disabled, configuration, connection and health-only states", () => {
  assert.equal(openMuseStatusCopy({ status: "disabled" }, "zh").label, "已关闭");
  assert.equal(openMuseStatusCopy({ status: "configuration_error" }, "zh").label, "配置错误");
  assert.equal(openMuseStatusCopy({ status: "unreachable" }, "zh").label, "无法连接");
  assert.equal(openMuseStatusCopy({ status: "unavailable" }, "zh").label, "不可用");
  const connected = openMuseStatusCopy({ status: "connected" }, "zh");
  assert.equal(connected.label, "已连接");
  assert.match(connected.detail, /仅验证.*服务健康/);
  assert.match(connected.detail, /不代表.*模型.*任务.*电脑沙盒/);
  assert.equal(openMuseStatusCopy({ status: "connected" }, "en").label, "Connected");
});

test("OpenMuse upstream statuses preserve active progress and map scheduled tasks to queued", () => {
  assert.equal(openMuseTaskStatus("scheduled"), "queued");
  assert.equal(openMuseTaskStatus("paused"), "queued");
  assert.equal(openMuseTaskStatus("running"), "running");
  assert.equal(openMuseTaskStatus("waiting_input"), "waiting_user");
  assert.equal(openMuseTaskStatus("succeeded"), "completed");
  assert.equal(openMuseTaskStatus("not_a_status"), "unknown");
});

test("an older status response without OpenMuse data fails closed with a clear unknown state", () => {
  const missing = openMuseStatusCopy(undefined, "zh");
  assert.equal(missing.label, "状态未知");
  assert.match(missing.detail, /当前服务版本未提供 OpenMuse 状态/);
});

test("OpenMuse pending delete errors are recognized only from the complete 409 payload", () => {
  const payload = {
    detail: "openmuse_task_deletion_pending",
    status: "pending",
    session_id: "session-1",
    pending_openmuse_task_ids: ["task-1"],
  };
  assert.deepEqual(openMuseDeletionPending(409, payload), {
    scope: "session",
    deletedSessions: 0,
    pendingSessions: ["session-1"],
    pendingTaskIds: ["task-1"],
  });
  assert.equal(openMuseDeletionPending(500, payload), null);
  assert.equal(openMuseDeletionPending(409, { ...payload, status: "deleted" }), null);
  assert.equal(openMuseDeletionPending(409, { ...payload, pending_openmuse_task_ids: [] }), null);
  assert.equal(openMuseDeletionPending(409, { detail: "openmuse_task_deletion_pending" }), null);
});

test("partial clear history result has an accurate Chinese pending notice", () => {
  const pending = openMuseDeletionPending(409, {
    detail: "openmuse_task_deletion_pending",
    status: "pending",
    sessions: 2,
    pending_sessions: ["session-3"],
    pending_openmuse_task_ids: ["task-3"],
  });
  assert.ok(pending);
  assert.equal(
    openMuseDeletionPendingCopy(pending, "zh"),
    "已删除 2 条对话；另有 1 条因关联的 OpenMuse 任务仍在清理而暂时保留，系统会继续重试。",
  );
  assert.match(openMuseDeletionPendingCopy({ ...pending, deletedSessions: 0 }, "zh"), /删除尚未完成/);
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
