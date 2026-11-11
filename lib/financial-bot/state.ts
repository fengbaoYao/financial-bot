import type { BotState, BotStatus, BotTask, MemoryCandidate, SourceEvidence, TaskEvent, TaskResult, TaskStatus } from "./types";

export type RunStep = {
  stepId: string;
  kind: string;
  status: string;
  summary: string;
  sequence: number;
  endpoint?: string | null;
  endpoints?: string[];
};

export type RunState = {
  taskId: string | null;
  status: TaskStatus;
  events: TaskEvent[];
  seen: string[];
  steps: RunStep[];
  lastSequence: number;
  answerReady: boolean;
  streamText: string;
  connection: "idle" | "connecting" | "live" | "reconnecting" | "offline";
};

export const EMPTY_RUN: RunState = {
  taskId: null,
  status: "queued",
  events: [],
  seen: [],
  steps: [],
  lastSequence: 0,
  answerReady: false,
  streamText: "",
  connection: "idle",
};

export function openMuseTaskStatus(status: string): TaskStatus {
  const statusMap: Record<string, TaskStatus> = {
    paused: "queued",
    queued: "queued",
    scheduled: "queued",
    running: "running",
    waiting_approval: "waiting_user",
    waiting_input: "waiting_user",
    succeeded: "completed",
    failed: "failed",
    cancelled: "cancelled",
  };
  return statusMap[status] ?? "unknown";
}

export function financialResearchModeErrorCopy(language: "zh" | "en"): string {
  return language === "en"
    ? "The Financial Bot source bridge is unavailable. Check its server configuration, then try again."
    : "Financial Bot 金融数据桥当前不可用，请检查服务端配置后重试。";
}

export function researchModeDisplayName(mode: string | null | undefined, language: "zh" | "en"): string {
  if (mode === "openmuse_shared") return language === "en" ? "Free research" : "自由研究";
  if (mode === "deep") return language === "en" ? "Deep research" : "深度研究";
  return language === "en" ? "Quick research" : "快速研究";
}

export function researchModelDisplayName(mode: string | null | undefined, configuredModel: string | null | undefined, language: "zh" | "en"): string | null {
  if (mode === "openmuse_shared") {
    return language === "en" ? "Configured on OpenMuse server" : "由 OpenMuse 服务端配置";
  }
  return configuredModel || null;
}

/** Convert explicit OpenMuse result/evidence fields to the shared result view. */
export function openMuseResearchResult(result: unknown, evidence: unknown): TaskResult | null {
  const record = result && typeof result === "object" && !Array.isArray(result)
    ? result as Record<string, unknown>
    : null;
  const summary = typeof result === "string"
    ? result
    : [record?.summary, record?.text, record?.content].find((value) => typeof value === "string") as string | undefined;
  const upstreamEvidence = [
    ...(Array.isArray(record?.evidence) ? record.evidence : []),
    ...(Array.isArray(evidence) ? evidence : []),
  ];
  const sources: SourceEvidence[] = upstreamEvidence.flatMap((item, index) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return [];
    const source = item as Record<string, unknown>;
    const excerpt = [source.excerpt, source.content, source.text, source.snippet].find((value) => typeof value === "string");
    const excerpts = Array.isArray(source.excerpts)
      ? source.excerpts.flatMap((value) => typeof value === "string" ? [{ text: value }] : [])
      : typeof excerpt === "string" ? [{ text: excerpt }] : [];
    return [{
      id: typeof source.id === "string" ? source.id : `openmuse-${index + 1}`,
      source: typeof source.source === "string" ? source.source : typeof source.provider === "string" ? source.provider : "__unattributed__",
      title: typeof source.title === "string" ? source.title : undefined,
      url: typeof source.url === "string" ? source.url : undefined,
      retrieved_at: typeof source.retrievedAt === "string" ? source.retrievedAt : typeof source.retrieved_at === "string" ? source.retrieved_at : undefined,
      data_as_of: typeof source.dataAsOf === "string" ? source.dataAsOf : typeof source.data_as_of === "string" ? source.data_as_of : undefined,
      freshness: typeof source.freshness === "string" ? source.freshness : undefined,
      excerpts,
    }];
  });
  const gapsValue = record?.data_gaps ?? record?.gaps;
  const dataGaps = Array.isArray(gapsValue) ? gapsValue.filter((gap): gap is string => typeof gap === "string" && Boolean(gap.trim())) : [];
  if (!summary && !sources.length && !dataGaps.length) return null;
  return {
    summary: summary || "",
    intent: "openmuse_shared_research",
    evidence: sources,
    data_gaps: dataGaps,
    ...(record && typeof record.answer_basis === "string" ? { meta: { answer_basis: record.answer_basis } } : {}),
  };
}

export function openMuseStatusCopy(
  openmuse: BotStatus["openmuse"] | null | undefined,
  language: "zh" | "en",
): { label: string; detail: string } {
  if (!openmuse) {
    return language === "en"
      ? { label: "Status unknown", detail: "This server version did not report OpenMuse status; it is treated as unavailable." }
      : { label: "状态未知", detail: "当前服务版本未提供 OpenMuse 状态，按不可用处理。" };
  }
  const copy = {
    zh: {
      disabled: ["已关闭", "OpenMuse 接入当前未启用。"],
      configuration_error: ["配置错误", "OpenMuse 地址或配置无效，请检查服务端配置。"],
      unreachable: ["无法连接", "当前无法确认 OpenMuse 服务状态。"],
      connected: [
        "已连接",
        "此状态仅验证 OpenMuse 服务健康，不代表模型、任务执行器或电脑沙盒可用。",
      ],
      unavailable: ["不可用", "OpenMuse 只允许本机单用户模式连接；当前账号模式不符合要求。"],
    },
    en: {
      disabled: ["Disabled", "The OpenMuse connection is turned off."],
      configuration_error: ["Configuration error", "The OpenMuse address or server configuration is invalid."],
      unreachable: ["Unreachable", "The OpenMuse service could not be confirmed as reachable."],
      connected: [
        "Connected",
        "This confirms only that the OpenMuse service is healthy. It does not confirm model, task worker, or computer sandbox availability.",
      ],
      unavailable: ["Unavailable", "OpenMuse is restricted to local single-user mode; this account mode is not eligible."],
    },
  } as const;
  const [label, detail] = copy[language][openmuse.status];
  return { label, detail };
}

export type OpenMuseDeletionPending = {
  scope: "session" | "bulk";
  deletedSessions: number;
  pendingSessions: string[];
  pendingTaskIds: string[];
};

/** Accept only the server's explicit, identity-bearing partial-delete payload. */
export function openMuseDeletionPending(
  httpStatus: number,
  payload: unknown,
): OpenMuseDeletionPending | null {
  if (
    httpStatus !== 409 ||
    !payload ||
    typeof payload !== "object" ||
    Array.isArray(payload)
  ) return null;
  const body = payload as Record<string, unknown>;
  if (body.detail !== "openmuse_task_deletion_pending" || body.status !== "pending") return null;
  const taskIds = body.pending_openmuse_task_ids;
  if (!Array.isArray(taskIds) || taskIds.length === 0 || taskIds.some((id) => typeof id !== "string" || !id)) {
    return null;
  }
  const singleSession = typeof body.session_id === "string" && body.session_id.length > 0;
  const bulkSessions = body.pending_sessions;
  if (bulkSessions !== undefined && (!Array.isArray(bulkSessions) || bulkSessions.some((id) => typeof id !== "string" || !id))) {
    return null;
  }
  if (!singleSession && (!Array.isArray(bulkSessions) || bulkSessions.length === 0)) return null;
  const deletedSessions = typeof body.sessions === "number" && Number.isInteger(body.sessions) && body.sessions > 0
    ? body.sessions
    : 0;
  return {
    scope: singleSession ? "session" : "bulk",
    deletedSessions,
    pendingSessions: singleSession ? [body.session_id as string] : [...new Set(bulkSessions as string[])],
    pendingTaskIds: [...new Set(taskIds as string[])],
  };
}

export function openMuseDeletionPendingCopy(
  pending: OpenMuseDeletionPending,
  language: "zh" | "en",
): string {
  const pendingCount = pending.pendingSessions.length;
  if (pending.scope === "session") {
    return language === "en"
      ? "This chat is still retained because an associated OpenMuse task is being deleted. The server will retry."
      : "删除尚未完成：关联的 OpenMuse 任务仍在清理，这条对话暂时保留，系统会继续重试。";
  }
  if (language === "en") {
    return pending.deletedSessions > 0
      ? `Deleted ${pending.deletedSessions} chat(s); ${pendingCount} more remain while associated OpenMuse tasks are being deleted. The server will retry.`
      : `Deletion is still pending for ${pendingCount} chat(s) while associated OpenMuse tasks are being deleted. The server will retry.`;
  }
  return pending.deletedSessions > 0
    ? `已删除 ${pending.deletedSessions} 条对话；另有 ${pendingCount} 条因关联的 OpenMuse 任务仍在清理而暂时保留，系统会继续重试。`
    : `删除尚未完成：${pendingCount} 条对话因关联的 OpenMuse 任务仍在清理而暂时保留，系统会继续重试。`;
}

const TERMINAL: TaskStatus[] = ["completed", "partial", "dropped", "failed", "cancelled", "unknown"];
export function isTerminal(status: TaskStatus): boolean {
  return TERMINAL.includes(status);
}

export function botStateFromTask(
  status: TaskStatus | null,
  connection: RunState["connection"],
  waitingReason?: string | null,
): BotState {
  if (!status) return "idle";
  // A broken or reconnecting stream cannot prove the task is still running.
  // Terminal results already received remain truthful.
  if ((connection === "offline" || connection === "reconnecting") && !isTerminal(status)) {
    return "unknown";
  }
  switch (status) {
    case "queued":
      return "queued";
    case "running":
      return "running";
    case "waiting_user":
      return "waiting";
    case "completed":
      return "completed";
    case "partial":
      return "partial";
    case "dropped":
      return "ended";
    case "failed":
      return "failed";
    case "cancelled":
      return "cancelled";
    default:
      return "unknown";
  }
}

/** Apply one authoritative server event, ignoring duplicates and stale sequences. */
export function applyTaskEvent(state: RunState, event: TaskEvent): RunState {
  if (!event || typeof event.sequence !== "number") return state;
  if (state.seen.includes(event.event_id)) return state;
  if (state.taskId && event.task_id !== state.taskId) return state;
  if (event.sequence <= state.lastSequence) return state;

  let status = state.status;
  if (event.kind === "status") {
    // Once a task reaches a terminal status, a delayed running event must not
    // revive it. The server enforces the same rule.
    if (!isTerminal(state.status)) status = event.status as TaskStatus;
  }
  if (event.kind === "error" && !isTerminal(status)) {
    status = "failed";
  }

  const streamText = event.kind === "token" ? state.streamText + event.display_summary : state.streamText;
  let steps = state.steps;
  if (event.kind === "step" && event.step_id) {
    const index = steps.findIndex((step) => step.stepId === event.step_id);
    const endpoint = event.source_refs.find((ref) => ref?.type === "endpoint" && typeof ref.name === "string")?.name as string | undefined;
    const prior = steps.find((step) => step.stepId === event.step_id);
    const endpoints = endpoint
      ? Array.from(new Set([...(prior?.endpoints ?? []), endpoint])).slice(-6)
      : prior?.endpoints;
    const nextStep: RunStep = {
      stepId: event.step_id,
      kind: event.kind,
      status: event.status,
      summary: event.display_summary,
      sequence: event.sequence,
      endpoint: endpoint ?? prior?.endpoint,
      endpoints,
    };
    steps = index < 0 ? [...steps, nextStep] : steps.map((step, i) => (i === index ? nextStep : step));
  }

  return {
    ...state,
    taskId: event.task_id,
    status,
    events: [...state.events, event],
    seen: [...state.seen, event.event_id],
    steps,
    lastSequence: event.sequence,
    answerReady: state.answerReady || event.kind === "answer",
    streamText,
    connection: state.connection === "idle" ? "live" : state.connection,
  };
}

export function startRun(taskId: string): RunState {
  return { ...EMPTY_RUN, taskId, status: "queued", connection: "connecting" };
}

export function setRunStatus(state: RunState, status: TaskStatus): RunState {
  if (TERMINAL.includes(state.status) && !TERMINAL.includes(status)) return state;
  return { ...state, status };
}

/** Findings already surfaced during a run, oldest first. */
export function findingNotices(state: RunState): Array<{ id: string; text: string }> {
  return state.events
    .filter((event) => event.kind === "notice" && event.status !== "failed" && String(event.display_summary || "").trim())
    .map((event) => ({ id: event.event_id, text: String(event.display_summary).trim() }));
}

export function completedSteps(state: RunState): RunStep[] {
  return state.steps.filter((step) => step.status === "completed");
}

export function activeSteps(state: RunState): RunStep[] {
  return state.steps.filter((step) => step.status === "running");
}

export function statusLabel(status: TaskStatus, locale: "zh" | "en", waitingReason?: string | null): string {
  if (status === "waiting_user") {
    const waiting: Record<string, [string, string]> = {
      question: ["等你补充", "Waiting for your answer"],
      confirmation: ["等你确认观点", "Waiting for your confirmation"],
      permission: ["等你授权", "Waiting for your permission"],
    };
    const pair = waiting[waitingReason || "question"] ?? waiting.question;
    return pair[locale === "en" ? 1 : 0];
  }
  const labels: Record<TaskStatus, [string, string]> = {
    queued: ["等待执行", "Queued"],
    running: ["正在执行", "Running"],
    waiting_user: ["等待你补充", "Waiting for you"],
    completed: ["已完成", "Completed"],
    partial: ["部分完成", "Partially completed"],
    dropped: ["无需处理", "Nothing to do"],
    failed: ["失败", "Failed"],
    cancelled: ["已取消", "Cancelled"],
    unknown: ["状态未知", "Unknown"],
  };
  return labels[status]?.[locale === "en" ? 1 : 0] ?? status;
}

export function botCopy(state: BotState, locale: "zh" | "en"): { title: string; detail: string } {
  // Short title = presence line by the avatar; detail stays for tooltips / screen reader.
  const zh: Record<BotState, [string, string]> = {
    idle: ["闲着", "有问题就直接问；我会自己选工具。"],
    queued: ["排队中", "任务已记下，还没开始查。"],
    running: ["正在核对来源", "在调真实工具、核对证据。"],
    waiting: ["在等你", "需要你选一下才能继续。"],
    completed: ["做完了", "回答与来源已写入记录。"],
    partial: ["做了一部分", "只完成部分研究；缺口已标出。"],
    ended: ["已结束", "没有要继续的动作；原因已记下。"],
    failed: ["卡住了", "没有把失败包装成结论；可以重试。"],
    cancelled: ["已取消", "后续动作已停；外部请求无法保证撤销。"],
    unknown: ["连不上", "暂时确认不了服务端状态。"],
  };
  const en: Record<BotState, [string, string]> = {
    idle: ["Idle", "Ask directly; I choose the tools."],
    queued: ["Queued", "Registered but not executing yet."],
    running: ["Checking sources", "Calling real tools and verifying evidence."],
    waiting: ["Waiting on you", "One choice is needed to continue."],
    completed: ["Done", "The answer and sources are stored server-side."],
    partial: ["Partly done", "Only part of the research finished; gaps are shown."],
    ended: ["Stopped here", "Nothing needed doing; the reason is recorded."],
    failed: ["Stuck", "No failure disguised as a conclusion; retry is available."],
    cancelled: ["Cancelled", "Remaining actions stopped; external calls cannot be guaranteed revoked."],
    unknown: ["Disconnected", "The server state is unknown, so no running state is shown."],
  };
  const pair = (locale === "en" ? en : zh)[state];
  return { title: pair[0], detail: pair[1] };
}

/**
 * One item in the "needs you" summary. Every item maps to a real action the
 * person can take, so the summary never reports a metric nobody can act on.
 */
export type AttentionItem = {
  /** Stable key: `candidate:<id>` or `task:<id>`. */
  id: string;
  taskId: string | null;
  candidateId?: string;
  kind: "confirmation" | "question" | "permission" | "failed" | "partial";
  /** Short label naming what is waited on, from `statusLabel`. */
  title: string;
  /** One-line excerpt so the list is readable without opening the task. */
  detail: string;
};

export type AttentionSummary = {
  /**
   * Exactly what the list renders. The summary count is derived from this
   * array, so a badge can never disagree with the list behind it.
   */
  items: AttentionItem[];
  /** Short empty state; no counters and no zero-valued metrics. */
  empty: string;
};

const RETRYABLE: TaskStatus[] = ["failed", "partial"];

function brief(text: string | null | undefined, max = 88): string {
  const flat = (text || "").replace(/\s+/g, " ").trim();
  if (flat.length <= max) return flat;
  return `${flat.slice(0, max - 1)}…`;
}

/**
 * Tasks and candidates that are blocked on a decision by the person, not on us.
 *
 * Order is by what unblocks the most work: candidate decisions and explicit
 * questions first, then retryable outcomes. Terminal-and-acted-on tasks
 * (`completed`, `dropped`, `cancelled`) never appear.
 */
export function needsAttention(
  tasks: BotTask[],
  candidates: MemoryCandidate[],
  locale: "zh" | "en",
  dismissed: readonly string[] = [],
): AttentionSummary {
  const hidden = new Set(dismissed);
  const en = locale === "en";
  const items: AttentionItem[] = [];

  for (const candidate of candidates) {
    if (candidate.status !== "pending") continue;
    items.push({
      id: `candidate:${candidate.id}`,
      taskId: candidate.task_id ?? null,
      candidateId: candidate.id,
      kind: "confirmation",
      title: en ? "Confirm a proposed viewpoint" : "确认一条观点",
      detail: brief(candidate.proposed_text),
    });
  }

  for (const task of tasks) {
    if (task.status === "waiting_user") {
      const reason = task.waiting_reason || "question";
      items.push({
        id: `task:${task.id}`,
        taskId: task.id,
        kind: reason,
        title: statusLabel(task.status, locale, reason),
        detail: brief(task.attention?.question || task.query),
      });
      continue;
    }
    if (RETRYABLE.includes(task.status)) {
      items.push({
        id: `task:${task.id}`,
        taskId: task.id,
        kind: task.status === "failed" ? "failed" : "partial",
        title: statusLabel(task.status, locale),
        detail: brief(task.error_message || task.query),
      });
    }
  }

  return {
    items: items.filter((item) => !hidden.has(item.id)),
    empty: en ? "Nothing needs your decision right now." : "现在没有需要你决定的事。",
  };
}

