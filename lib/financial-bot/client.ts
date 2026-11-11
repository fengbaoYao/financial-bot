"use client";

import { createClient } from "@/utils/supabase/client";
import type {
  BotStatus,
  BotTask,
  BotMessage,
  TaskWorkflow,
  DeletionJob,
  TaskPlan,
  MemoryCandidate,
  MemoryItem,
  MemoryResponse,
  TaskEvent,
  BotDisposition,
} from "./types";

export class BotApiError extends Error {
  status: number;
  currentVersion?: number;
  /** Raw JSON error body, so callers can read fields such as `bot_id`. */
  payload?: Record<string, unknown>;

  constructor(message: string, status: number, currentVersion?: number, payload?: Record<string, unknown>) {
    super(message);
    this.name = "BotApiError";
    this.status = status;
    this.currentVersion = currentVersion;
    this.payload = payload;
  }

  /** Machine-readable conflict code, e.g. `session_belongs_to_another_bot`. */
  get code(): string {
    return this.message;
  }
}

async function authHeaders(): Promise<Record<string, string>> {
  if (typeof window === "undefined") return {};
  try {
    const { data } = await createClient().auth.getSession();
    const token = data.session?.access_token;
    return token ? { Authorization: `Bearer ${token}` } : {};
  } catch {
    return {};
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      ...(await authHeaders()),
      ...(init?.headers ?? {}),
    },
  });
  const raw = await response.text();
  let payload: unknown = null;
  try {
    payload = raw ? JSON.parse(raw) : null;
  } catch {
    payload = raw;
  }
  if (!response.ok) {
    const detail =
      payload && typeof payload === "object" && "detail" in payload
        ? String((payload as { detail: unknown }).detail)
        : raw || `请求失败 (${response.status})`;
    const currentVersion =
      payload && typeof payload === "object" && "current_version" in payload
        ? Number((payload as { current_version: unknown }).current_version)
        : undefined;
    throw new BotApiError(
      detail,
      response.status,
      currentVersion,
      payload && typeof payload === "object" ? (payload as Record<string, unknown>) : undefined,
    );
  }
  return payload as T;
}

export function getBotStatus(): Promise<BotStatus> {
  return request<BotStatus>("/api/bot/status");
}

export type BotCapabilities = {
  web: boolean;
  filings: boolean;
  market: boolean;
  news: boolean;
  portfolio: boolean;
  watchlist: boolean;
};

export type BotPromptConfig = {
  role: string;
  markets: Array<"us" | "hk" | "cn">;
  output_style: "concise" | "standard" | "detailed";
  capabilities: BotCapabilities;
  memory_enabled: boolean;
  routines_enabled: boolean;
  proactive_enabled: boolean;
};

export type PromptSegment = {
  id: string;
  label: string;
  role: "system" | "user" | "assistant";
  content: string;
  editable: boolean;
  chars: number;
};

export type BotPromptPreview = {
  template: string;
  rendered: string;
  variables: Record<string, string>;
  variable_labels: Record<string, string>;
  known_variables: string[];
  unknown_variables: string[];
  segments: PromptSegment[];
  config: BotPromptConfig;
  enabled_tools: string[];
  disabled_tools: string[];
  all_tools: string[];
  memory_count: number;
  history_count: number;
  total_chars: number;
  estimated_tokens: number;
  template_version: number;
  bot_id: string | null;
};

export type BotProfile = {
  id: string;
  name: string;
  description?: string | null;
  instructions?: string | null;
  prompt_template?: string | null;
  prompt_template_customized?: boolean;
  prompt_config?: BotPromptConfig | null;
  prompt_version?: number;
  is_default: boolean;
  approval_threshold: number;
  market_hours_only?: boolean;
  max_alerts_per_day?: number;
  quiet_hours_start?: string | null;
  quiet_hours_end?: string | null;
  webhook_configured?: boolean;
  notification_email?: string | null;
  report_style?: "brief" | "standard";
  digest_mode?: "immediate" | "daily";
  digest_hour?: string | null;
  session_count?: number;
  last_query?: string | null;
  last_active_at?: string | null;
  created_at?: string;
  updated_at?: string;
};

export function listBots(): Promise<{ bots: BotProfile[] }> {
  return request<{ bots: BotProfile[] }>("/api/bots");
}

export function createBot(input: {
  name: string;
  description?: string;
  instructions?: string;
  approvalThreshold?: number;
  promptTemplate?: string;
  promptConfig?: BotPromptConfig;
}): Promise<{ bot: BotProfile }> {
  return request<{ bot: BotProfile }>("/api/bots", {
    method: "POST",
    body: JSON.stringify({
      name: input.name,
      description: input.description || "",
      instructions: input.instructions || "",
      approval_threshold: input.approvalThreshold ?? 4,
      ...(input.promptTemplate !== undefined ? { prompt_template: input.promptTemplate } : {}),
      ...(input.promptConfig !== undefined ? { prompt_config: input.promptConfig } : {}),
    }),
  });
}

export function updateBot(botId: string, input: {
  name?: string;
  description?: string;
  instructions?: string;
  approvalThreshold?: number;
  promptTemplate?: string;
  promptConfig?: BotPromptConfig;
  marketHoursOnly?: boolean;
  maxAlertsPerDay?: number;
  quietHoursStart?: string;
  quietHoursEnd?: string;
  webhookUrl?: string;
  notificationEmail?: string;
  reportStyle?: "brief" | "standard";
  digestMode?: "immediate" | "daily";
  digestHour?: string;
}): Promise<{ bot: BotProfile }> {
  return request<{ bot: BotProfile }>(`/api/bots/${encodeURIComponent(botId)}`, {
    method: "PATCH",
    body: JSON.stringify({
      ...(input.name !== undefined ? { name: input.name } : {}),
      ...(input.description !== undefined ? { description: input.description } : {}),
      ...(input.instructions !== undefined ? { instructions: input.instructions } : {}),
      ...(input.approvalThreshold !== undefined ? { approval_threshold: input.approvalThreshold } : {}),
      ...(input.promptTemplate !== undefined ? { prompt_template: input.promptTemplate } : {}),
      ...(input.promptConfig !== undefined ? { prompt_config: input.promptConfig } : {}),
      ...(input.marketHoursOnly !== undefined ? { market_hours_only: input.marketHoursOnly } : {}),
      ...(input.maxAlertsPerDay !== undefined ? { max_alerts_per_day: input.maxAlertsPerDay } : {}),
      ...(input.quietHoursStart !== undefined ? { quiet_hours_start: input.quietHoursStart } : {}),
      ...(input.quietHoursEnd !== undefined ? { quiet_hours_end: input.quietHoursEnd } : {}),
      ...(input.webhookUrl !== undefined ? { webhook_url: input.webhookUrl } : {}),
      ...(input.notificationEmail !== undefined ? { notification_email: input.notificationEmail } : {}),
      ...(input.reportStyle !== undefined ? { report_style: input.reportStyle } : {}),
      ...(input.digestMode !== undefined ? { digest_mode: input.digestMode } : {}),
      ...(input.digestHour !== undefined ? { digest_hour: input.digestHour } : {}),
    }),
  });
}

export function testBotNotification(botId: string): Promise<{ webhook: boolean; email: boolean; configured: boolean }> {
  return request<{ webhook: boolean; email: boolean; configured: boolean }>(`/api/bots/${encodeURIComponent(botId)}/test-notification`, {
    method: "POST",
  });
}

export function deleteBot(botId: string): Promise<{ deleted: string; moved_sessions: number; moved_to: string }> {
  return request<{ deleted: string; moved_sessions: number; moved_to: string }>(`/api/bots/${encodeURIComponent(botId)}`, {
    method: "DELETE",
  });
}

export type BotPreflight = {
  requires_approval: boolean;
  estimated_credits: number;
  threshold: number;
  model: string | null;
  model_configured: boolean;
  reason: string | null;
};

export function preflightBot(botId: string, query: string): Promise<BotPreflight> {
  const params = new URLSearchParams({ query });
  return request<BotPreflight>(`/api/bots/${encodeURIComponent(botId)}/preflight?${params.toString()}`);
}

export type PromptDefaults = {
  template: string;
  config: BotPromptConfig;
  variables: Record<string, string>;
  known_variables: string[];
  template_version: number;
};

export function getPromptDefaults(): Promise<PromptDefaults> {
  return request<PromptDefaults>("/api/bots/prompt-defaults");
}

export function previewBotPrompt(input: {
  botId?: string | null;
  name: string;
  description?: string;
  instructions?: string;
  promptTemplate?: string;
  promptConfig: BotPromptConfig;
  query?: string;
}): Promise<BotPromptPreview> {
  return request<BotPromptPreview>("/api/bots/prompt-preview", {
    method: "POST",
    body: JSON.stringify({
      bot_id: input.botId || undefined,
      name: input.name,
      description: input.description || "",
      instructions: input.instructions || "",
      ...(input.promptTemplate !== undefined ? { prompt_template: input.promptTemplate } : {}),
      prompt_config: input.promptConfig,
      query: input.query || "",
    }),
  });
}

export type PortfolioPosition = {
  id?: number | null;
  stock_code: string;
  stock_name: string;
  cost_price: number;
  quantity: number;
  weight_pct?: number | null;
};

export function listPortfolioPositions(): Promise<PortfolioPosition[]> {
  return request<PortfolioPosition[]>("/api/portfolio");
}

export type BotSessionSummary = {
  id: string;
  /** Owning Bot: a conversation can only be written by the Bot that started it. */
  bot_id?: string | null;
  title: string;
  updated_at?: string | null;
  task_count: number;
  last_status?: string | null;
  kind?: "main" | "side";
  parent_session_id?: string | null;
  folder?: string | null;
  approval_mode?: "ask" | "auto";
  research_mode?: "quick" | "deep";
  pinned?: boolean;
  archived?: boolean;
  unread?: number;
};

export function patchThread(sessionId: string, patch: {
  title?: string;
  pinned?: boolean;
  archived?: boolean;
  folder?: string | null;
  unread?: number;
}): Promise<{ thread: BotThread }> {
  return request<{ thread: BotThread }>(`/api/threads/${encodeURIComponent(sessionId)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export type ThreadState = {
  workflow: TaskWorkflow | null;
  task_id?: string | null;
  updated_at?: string | null;
};

export function getThreadState(sessionId: string): Promise<ThreadState> {
  return request<ThreadState>(`/api/sessions/${encodeURIComponent(sessionId)}/state`);
}

export function readThread(sessionId: string): Promise<{ thread: BotThread | null; created?: boolean }> {
  return request<{ thread: BotThread | null; created?: boolean }>(`/api/threads/${encodeURIComponent(sessionId)}/read`, { method: "POST" });
}

export type BotThread = {
  id: string;
  session_id: string;
  bot_id: string;
  parent_session_id?: string | null;
  title: string;
  kind: "main" | "side";
  folder?: string | null;
  approval_mode?: "ask" | "auto";
  research_mode?: "quick" | "deep";
  pinned?: boolean;
  archived?: boolean;
  unread?: number;
  created_at: string;
  updated_at: string;
};

export function createThread(input: {
  botId: string;
  parentSessionId?: string | null;
  title?: string;
}): Promise<{ thread: BotThread; session: BotThread }> {
  return request<{ thread: BotThread; session: BotThread }>("/api/threads", {
    method: "POST",
    body: JSON.stringify({
      bot_id: input.botId,
      parent_session_id: input.parentSessionId || undefined,
      title: input.title,
    }),
  });
}

export function deleteSession(sessionId: string): Promise<{ session: number; tasks: number; messages: number }> {
  return request<{ session: number; tasks: number; messages: number }>(`/api/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
}

export function clearBotSessions(botId: string): Promise<{ sessions: number; tasks: number; messages: number }> {
  return request<{ sessions: number; tasks: number; messages: number }>(`/api/bots/${encodeURIComponent(botId)}/sessions`, { method: "DELETE" });
}

export function listSessions(botId?: string, limit = 30): Promise<{ sessions: BotSessionSummary[] }> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (botId) params.set("bot_id", botId);
  return request<{ sessions: BotSessionSummary[] }>(`/api/sessions?${params.toString()}`);
}

export type OnboardPlan = {
  greeting: string;
  options: Array<{
    label: string;
    ack: string;
    templates: Array<{ label: string; prompt: string }>;
  }>;
};

/** The first conversation this Bot should have, tailored to its own instructions. */
export function getBotOnboarding(
  botId: string,
  language: "zh-CN" | "en-US",
): Promise<{ onboarding: OnboardPlan | null }> {
  return request<{ onboarding: OnboardPlan | null }>(`/api/bots/${encodeURIComponent(botId)}/onboarding`, {
    method: "POST",
    body: JSON.stringify({ language }),
  });
}

export function ensureWelcome(
  sessionId: string,
  botId: string,
  persona: "beginner" | "ticker" | "filings" | "portfolio" | "news" | "pro" | "watchlist",
  language: "zh-CN" | "en-US",
): Promise<{ created: boolean }> {
  return request<{ created: boolean }>(`/api/sessions/${encodeURIComponent(sessionId)}/welcome`, {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, bot_id: botId, persona, language }),
  });
}

export function listMessages(sessionId: string, limit = 200): Promise<{ messages: BotMessage[] }> {
  const params = new URLSearchParams({ limit: String(limit) });
  return request<{ messages: BotMessage[] }>(`/api/sessions/${encodeURIComponent(sessionId)}/messages?${params.toString()}`);
}

export function listTasks(sessionId?: string, limit = 30): Promise<{ tasks: BotTask[] }> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (sessionId) params.set("session_id", sessionId);
  return request<{ tasks: BotTask[] }>(`/api/tasks?${params.toString()}`);
}

export function getTask(taskId: string): Promise<{ task: BotTask }> {
  return request<{ task: BotTask }>(`/api/tasks/${encodeURIComponent(taskId)}`);
}

export function getTaskPlan(taskId: string): Promise<{ plan: TaskPlan | null }> {
  return request<{ plan: TaskPlan | null }>(`/api/tasks/${encodeURIComponent(taskId)}/plan`);
}

export function resumeTask(taskId: string): Promise<{ task: BotTask; plan: TaskPlan }> {
  return request<{ task: BotTask; plan: TaskPlan }>(`/api/tasks/${encodeURIComponent(taskId)}/resume`, {
    method: "POST",
  });
}

export type BotUIContext = {
  page: "bot" | "work";
  last_action:
    | "ask"
    | "confirm_model_budget"
    | "retry"
    | "follow_up"
    | "open_artifact"
    | "confirm_memory"
    | "run_routine"
    | "other";
  task_id?: string | null;
  task_status?: BotTask["status"] | null;
  view?: "transcript" | "trace" | "studio";
};

export function getAgentResearchCapabilities() {
  return request<{
    quick_available: boolean;
    deep_available: boolean;
    deep_unavailable_reason?: string | null;
    deep_unavailable_message?: string | null;
  }>("/api/agent/capabilities");
}

export function getModelProviders() {
  return request<{ deepseek: { available: boolean }; chatgpt: { available: boolean; connected: boolean } }>("/api/model-providers");
}

export function createTask(input: {
  query: string;
  sessionId: string;
  botId?: string;
  modelApproved?: boolean;
  language: "zh-CN" | "en-US";
  researchMode: "quick" | "deep";
  modelProvider: "deepseek" | "chatgpt";
  idempotencyKey: string;
  uiContext?: BotUIContext;
}): Promise<{ task: BotTask; created: boolean }> {
  return request<{ task: BotTask; created: boolean }>("/api/tasks", {
    method: "POST",
    headers: { "Idempotency-Key": input.idempotencyKey },
    body: JSON.stringify({
      query: input.query,
      session_id: input.sessionId,
      bot_id: input.botId,
      model_approved: Boolean(input.modelApproved),
      language: input.language,
      research_mode: input.researchMode,
      model_provider: input.modelProvider,
      idempotency_key: input.idempotencyKey,
      ...(input.uiContext ? { ui_context: input.uiContext } : {}),
    }),
  });
}

export function createOpenMuseSharedResearch(input: {
  query: string;
  sessionId: string;
  botId: string;
  language: "zh-CN" | "en-US";
  idempotencyKey: string;
}): Promise<{ task: BotTask; coordination: { state: string; reason?: string | null; upstream_status?: string | null } }> {
  return request("/api/tasks/openmuse-shared", {
    method: "POST",
    headers: { "Idempotency-Key": input.idempotencyKey },
    body: JSON.stringify({
      query: input.query,
      session_id: input.sessionId,
      bot_id: input.botId,
      language: input.language,
      idempotency_key: input.idempotencyKey,
    }),
  });
}

export type OpenMuseSharedTaskSnapshot = {
  coordination: { state: string; reason?: string | null; upstream_status?: string | null };
  upstream_task?: { status: string; result?: unknown; evidence?: unknown; plan?: unknown; error?: unknown; question?: string | null; updatedAt?: string | null } | null;
};

export function syncOpenMuseSharedResearch(taskId: string): Promise<OpenMuseSharedTaskSnapshot> {
  return request(`/api/tasks/${encodeURIComponent(taskId)}/openmuse-shared`);
}

export function cancelOpenMuseSharedResearch(taskId: string): Promise<{ upstream_task: { status: string } }> {
  return request(`/api/tasks/${encodeURIComponent(taskId)}/openmuse-shared/cancel`, { method: "POST" });
}

export function answerOpenMuseSharedResearch(taskId: string, answer: string): Promise<{ upstream_task: { status: string; question?: string | null } }> {
  return request(`/api/tasks/${encodeURIComponent(taskId)}/openmuse-shared/answer`, {
    method: "POST",
    body: JSON.stringify({ answer }),
  });
}

export function cancelTask(taskId: string): Promise<{
  task: BotTask;
  cancel: {
    requested: boolean;
    already_terminal: boolean;
    execution_signal_sent: boolean;
    note: string;
  };
}> {
  return request<{
    task: BotTask;
    cancel: {
      requested: boolean;
      already_terminal: boolean;
      execution_signal_sent: boolean;
      note: string;
    };
  }>(`/api/tasks/${encodeURIComponent(taskId)}/cancel`, { method: "POST" });
}

/**
 * Follow server events from a sequence cursor. Each event carries the
 * authoritative state; callers must not invent progress between events.
 */
export async function streamTaskEvents(
  taskId: string,
  afterSequence: number,
  handlers: {
    signal: AbortSignal;
    onEvent: (event: TaskEvent) => void;
    onDone?: (info: { status: string; last_sequence: number }) => void;
  },
): Promise<void> {
  const response = await fetch(
    `/api/tasks/${encodeURIComponent(taskId)}/events?after_sequence=${afterSequence}`,
    {
      headers: { Accept: "text/event-stream", ...(await authHeaders()) },
      cache: "no-store",
      signal: handlers.signal,
    },
  );
  if (!response.ok) {
    const text = await response.text();
    throw new BotApiError(text || `事件流连接失败 (${response.status})`, response.status);
  }
  if (!response.body) {
    throw new BotApiError("浏览器未提供事件流响应体", 0);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      while (true) {
        const boundary = buffer.indexOf("\n\n");
        if (boundary < 0) break;
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const lines = frame.split("\n");
        let eventName = "message";
        const dataLines: string[] = [];
        for (const line of lines) {
          if (line.startsWith(":")) continue;
          if (line.startsWith("event:")) eventName = line.slice(6).trim();
          else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
        }
        if (!dataLines.length) continue;
        let payload: unknown;
        try {
          payload = JSON.parse(dataLines.join("\n"));
        } catch {
          continue;
        }
        if (eventName === "task_event") {
          handlers.onEvent(payload as TaskEvent);
        } else if (eventName === "done") {
          handlers.onDone?.(payload as { status: string; last_sequence: number });
          return;
        } else if (eventName === "error") {
          const detail =
            payload && typeof payload === "object" && "detail" in payload
              ? String((payload as { detail: unknown }).detail)
              : "事件流失败";
          throw new BotApiError(detail, 0);
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

export function getMemories(botId?: string, includeLegacy = false): Promise<MemoryResponse> {
  const params = new URLSearchParams();
  if (botId) params.set("bot_id", botId);
  if (includeLegacy) params.set("include_legacy", "true");
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return request<MemoryResponse>(`/api/memories${suffix}`);
}

export function confirmCandidate(
  candidateId: string,
  input: { expectedVersion: number; finalText: string; idempotencyKey: string },
): Promise<{ candidate: MemoryCandidate; memory: MemoryItem }> {
  return request(`/api/memory-candidates/${encodeURIComponent(candidateId)}/confirm`, {
    method: "POST",
    headers: { "Idempotency-Key": input.idempotencyKey },
    body: JSON.stringify({
      expected_version: input.expectedVersion,
      final_text: input.finalText,
      idempotency_key: input.idempotencyKey,
    }),
  });
}

export function dismissCandidate(
  candidateId: string,
  input: { expectedVersion: number; idempotencyKey: string },
): Promise<{ candidate: MemoryCandidate }> {
  return request(`/api/memory-candidates/${encodeURIComponent(candidateId)}/dismiss`, {
    method: "POST",
    headers: { "Idempotency-Key": input.idempotencyKey },
    body: JSON.stringify({
      expected_version: input.expectedVersion,
      idempotency_key: input.idempotencyKey,
    }),
  });
}

export function patchMemory(
  memoryId: string,
  input: { expectedVersion: number; text: string },
): Promise<{ memory: MemoryItem }> {
  return request(`/api/memories/${encodeURIComponent(memoryId)}`, {
    method: "PATCH",
    body: JSON.stringify({ expected_version: input.expectedVersion, text: input.text }),
  });
}

export function deleteMemory(memoryId: string, requestId: string): Promise<{ deletion: DeletionJob }> {
  return request(
    `/api/memories/${encodeURIComponent(memoryId)}?request_id=${encodeURIComponent(requestId)}`,
    { method: "DELETE", headers: { "Idempotency-Key": requestId } },
  );
}

export function deleteAllMemories(requestId: string): Promise<{ deletion: DeletionJob }> {
  return request("/api/memories", {
    method: "DELETE",
    headers: { "Idempotency-Key": requestId },
    body: JSON.stringify({ confirm: "delete_all", request_id: requestId }),
  });
}

export type BotMonitor = {
  id: string;
  bot_id: string;
  name: string;
  symbol: string;
  condition: string;
  threshold: number;
  direction: "up" | "down" | "both";
  interval_minutes: number;
  cooldown_minutes: number;
  status: "active" | "paused";
  last_checked_at?: string | null;
  last_triggered_at?: string | null;
  last_error?: string | null;
  muted_until?: string | null;
  created_at?: string;
};

export type BotNotification = {
  id: string;
  bot_id: string;
  monitor_id?: string | null;
  title: string;
  body: string;
  severity: "info" | "warning" | string;
  status: "unread" | "read" | string;
  task_id?: string | null;
  created_at: string;
};

export function listMonitors(botId?: string): Promise<{ monitors: BotMonitor[] }> {
  const params = new URLSearchParams();
  if (botId) params.set("bot_id", botId);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return request<{ monitors: BotMonitor[] }>(`/api/monitors${suffix}`);
}

export function createMonitor(input: {
  botId: string;
  name: string;
  symbol: string;
  condition?: "price" | "announcement" | "earnings" | "report";
  threshold: number;
  direction: "up" | "down" | "both";
  intervalMinutes?: number;
  cooldownMinutes?: number;
  sessionId?: string | null;
}): Promise<{ monitor: BotMonitor }> {
  return request<{ monitor: BotMonitor }>("/api/monitors", {
    method: "POST",
    body: JSON.stringify({
      bot_id: input.botId,
      name: input.name,
      symbol: input.symbol,
      condition: input.condition ?? "price",
      threshold: input.threshold,
      direction: input.direction,
      interval_minutes: input.intervalMinutes ?? 15,
      cooldown_minutes: input.cooldownMinutes ?? 120,
      session_id: input.sessionId || undefined,
    }),
  });
}

export function updateMonitor(monitorId: string, input: { status?: "active" | "paused"; name?: string; threshold?: number }): Promise<{ monitor: BotMonitor }> {
  return request<{ monitor: BotMonitor }>(`/api/monitors/${encodeURIComponent(monitorId)}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function deleteMonitor(monitorId: string): Promise<{ deleted: string }> {
  return request<{ deleted: string }>(`/api/monitors/${encodeURIComponent(monitorId)}`, { method: "DELETE" });
}

export function listNotifications(botId?: string, unreadOnly = false, limit = 50): Promise<{ notifications: BotNotification[] }> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (botId) params.set("bot_id", botId);
  if (unreadOnly) params.set("unread_only", "true");
  return request<{ notifications: BotNotification[] }>(`/api/notifications?${params.toString()}`);
}

export function readNotification(notificationId: string): Promise<{ notification: BotNotification }> {
  return request<{ notification: BotNotification }>(`/api/notifications/${encodeURIComponent(notificationId)}/read`, {
    method: "POST",
  });
}

export function analyzeNotification(notificationId: string, modelApproved = false): Promise<{ task: BotTask; created: boolean; bot_id: string; session_id?: string }> {
  return request<{ task: BotTask; created: boolean; bot_id: string; session_id?: string }>(`/api/notifications/${encodeURIComponent(notificationId)}/analyze`, {
    method: "POST",
    body: JSON.stringify({ model_approved: modelApproved }),
  });
}

export type BotDelivery = {
  id: string;
  bot_id: string;
  notification_id?: string | null;
  channel: string;
  status: "sent" | "failed" | string;
  error?: string | null;
  attempts: number;
  created_at: string;
  sent_at?: string | null;
};

export function listDeliveries(botId?: string, limit = 20, status?: string, channel?: string): Promise<{ deliveries: BotDelivery[] }> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (botId) params.set("bot_id", botId);
  if (status) params.set("status", status);
  if (channel) params.set("channel", channel);
  return request<{ deliveries: BotDelivery[] }>(`/api/deliveries?${params.toString()}`);
}

export function retryDelivery(deliveryId: string): Promise<{ delivery: BotDelivery; retried: boolean; sent?: boolean }> {
  return request<{ delivery: BotDelivery; retried: boolean; sent?: boolean }>(`/api/deliveries/${encodeURIComponent(deliveryId)}/retry`, {
    method: "POST",
  });
}

export type DigestPreview = {
  title: string;
  body: string;
  bot_id: string;
  bot_name?: string | null;
  unread_count: number;
  created_at: string;
};

export function previewDigest(botId: string): Promise<{ digest: DigestPreview }> {
  return request<{ digest: DigestPreview }>(`/api/bots/${encodeURIComponent(botId)}/digest-preview`);
}

export function sendDigestNow(botId: string): Promise<{ digest: DigestPreview; configured: boolean; sent: { webhook: boolean; email: boolean } }> {
  return request<{ digest: DigestPreview; configured: boolean; sent: { webhook: boolean; email: boolean } }>(`/api/bots/${encodeURIComponent(botId)}/digest/send`, {
    method: "POST",
  });
}


export type MarketClosure = {
  id: string;
  market: "us" | "hk" | "cn" | string;
  closure_date: string;
  reason?: string | null;
  created_at: string;
};

export function listMarketClosures(): Promise<{ closures: MarketClosure[] }> {
  return request<{ closures: MarketClosure[] }>("/api/market-closures");
}

export function addMarketClosure(input: { market: "us" | "hk" | "cn"; date: string; reason?: string }): Promise<{ closure: MarketClosure }> {
  return request<{ closure: MarketClosure }>("/api/market-closures", {
    method: "POST",
    body: JSON.stringify({ market: input.market, date: input.date, reason: input.reason || "" }),
  });
}

export function deleteMarketClosure(closureId: string): Promise<{ deleted: string }> {
  return request<{ deleted: string }>(`/api/market-closures/${encodeURIComponent(closureId)}`, { method: "DELETE" });
}

export function muteMonitor(monitorId: string, muteMinutes: number): Promise<{ monitor: BotMonitor }> {
  return request<{ monitor: BotMonitor }>(`/api/monitors/${encodeURIComponent(monitorId)}/mute`, {
    method: "POST",
    body: JSON.stringify({ mute_minutes: muteMinutes }),
  });
}


export type BotArtifact = {
  id: string;
  session_id: string | null;
  title: string;
  status: string;
  created_at: string;
  summary: string;
  evidence_count: number;
  data_gap_count: number;
};

export function listArtifacts(botId?: string, limit = 20): Promise<{ artifacts: BotArtifact[] }> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (botId) params.set("bot_id", botId);
  return request<{ artifacts: BotArtifact[] }>(`/api/artifacts?${params.toString()}`);
}

export function listDispositions(sessionId?: string, limit = 50): Promise<{ dispositions: BotDisposition[] }> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (sessionId) params.set("session_id", sessionId);
  return request<{ dispositions: BotDisposition[] }>(`/api/dispositions?${params.toString()}`);
}

