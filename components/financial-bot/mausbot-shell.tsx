/* eslint-disable @typescript-eslint/no-explicit-any -- OMB SSE/wire payloads are dynamic */
"use client";

import { FormEvent, SyntheticEvent, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { isTranscriptNearBottom, scrollTranscriptToBottom } from "../../lib/bot-transcript-scroll";
import styles from "./mausbot-shell.module.css";

type OmbTask = {
  threadId: string;
  title?: string;
  busy?: boolean;
  activity?: string;
  unread?: boolean;
  updatedAt?: number;
  lastThreadOrderAt?: number;
  pinned?: boolean;
  archivedAt?: number | null;
  modelSelection?: { instanceId?: string; model?: string; effort?: string };
  followsBotModel?: boolean;
};

type OmbBot = {
  id: string;
  name?: string;
  title?: string;
  busy?: boolean;
  activity?: string;
  mainThreadId?: string;
  threadId?: string;
  modelSelection?: { instanceId?: string; model?: string; effort?: string };
  /** Works on: absent/null = Auto */
  computer?: "cloud" | "vm" | "local" | "browser" | "off" | null;
  alwaysAllow?: string[];
  tasks?: OmbTask[];
};

type MsgAttachment = {
  kind: string;
  path: string;
  mime?: string;
  name?: string;
  durationMs?: number;
  text?: string;
};

type ChatMsgTool = {
  name?: string;
  label?: string;
  summary?: string;
  input?: string;
  output?: string;
  error?: string;
  ok?: boolean | null;
  partial?: boolean;
  clipped?: boolean;
};

type ModelProvenance = {
  actualProvider?: string;
  actualModel?: string;
  requestedProvider?: string;
  requestedModel?: string;
  fallbackReason?: string;
};

type ChatMsg = {
  id: string;
  role: "user" | "assistant" | "system";
  kind?: string;
  text: string;
  modelProvenance?: ModelProvenance;
  at?: number;
  helperIntro?: boolean;
  attachments?: MsgAttachment[];
  pending?: boolean;
  tool?: ChatMsgTool;
  steered?: boolean;
  queued?: boolean;
    cardTitle?: string;
  cardSubtitle?: string;
  cardOptions?: string[];
  cardRequestId?: string;
  cardRequestType?: "permission" | "question";
  cardTool?: string;
  cardAnswered?: string;
  cardAnsweredText?: string;
  cardDismissed?: boolean;
  cardExpired?: boolean;
  cardStopped?: boolean;
  cardHeld?: string;
  cardHeldCode?: string;
  cardAllowKey?: string;
  cardAllowSession?: boolean;
  cardApprovalScope?: string;
  cardFixedOptions?: boolean;
  cardAutoApplied?: boolean;
  cardUndone?: boolean;
  cardCommandAllowlist?: { command?: string; cwd?: string; providerInstanceId?: string };
  cardComputerAsk?: { reason?: string; device?: string; app?: string; want?: string };
  cardYourComputer?: { app?: string; site?: string };
  cardSkillRequest?: { action?: string; name?: string; path?: string; present?: boolean };
  cardModelRequest?: { botId?: string; targetBotId?: string; targetName?: string; selection?: { instanceId?: string; model?: string }; present?: boolean };
  cardQuestionRequest?: { version?: string; present?: boolean };
  cardProfileRequest?: { botId?: string; targetBotId?: string; targetName?: string; present?: boolean };
  cardRoutineRequest?: { action?: string; name?: string; present?: boolean };
  cardAccessRequest?: { botId?: string; botName?: string; email?: string; name?: string; present?: boolean };
  cardOutsideWrite?: { kind?: string; action?: string; roomId?: string; text?: string; bulletin?: string; present?: boolean };
  cardMcpRequest?: { action?: string; name?: string; present?: boolean };
  cardLendingLift?: { requestMessageId?: string; outside?: boolean; botOpened?: boolean; present?: boolean };
  waitLine?: string;
  secret?: {
    target?: string;
    label?: string;
    description?: string;
    placeholder?: string;
    helpUrl?: string;
    delivery?: string;
    reason?: string;
    requestKey?: string;
    provided?: boolean;
    dismissed?: boolean;
    superseded?: boolean;
    resumed?: boolean;
    stopped?: boolean;
    error?: string;
    savedAt?: number;
  };
  signIn?: {
    url?: string;
    site?: string;
    reason?: string;
    place?: string;
    done?: boolean;
    cancelled?: boolean;
    superseded?: boolean;
    resumed?: boolean;
    stopped?: boolean;
    answeredInChat?: boolean;
    savedLogin?: string;
    error?: string;
  };
  connector?: {
    source?: string;
    slug?: string;
    label?: string;
    description?: string;
    status?: string;
    resumeKey?: string;
    alias?: string;
    error?: string;
    dismissed?: boolean;
    superseded?: boolean;
    resumed?: boolean;
    stopped?: boolean;
  };
  widget?: any;
  app?: {
    source?: string;
    title?: string;
    doc?: string;
    rev?: number;
    bytes?: number;
    height?: number;
    border?: boolean;
    blocked?: string;
    live?: boolean;
    format?: string;
    mcp?: { server?: string; tool?: string; resourceUri?: string };
  };
  fromApp?: { messageId?: string; title?: string };
  pointed?: Array<{ label?: string; text?: string; selector?: string }>;
  routineRun?: {
    runId?: string;
    routineId?: string;
    routineName?: string;
    status?: string;
    goalStatus?: string;
    executionThreadId?: string;
    summary?: string;
    error?: string;
    scheduledFor?: number;
    deferredAt?: number;
  };
  automation?: {
    routineId?: string;
    runId?: string;
    name?: string;
    trigger?: string;
    needsYou?: boolean;
    failed?: boolean;
  };
  goalRun?: {
    runId?: string;
    goal?: string;
    status?: string;
    coordinatorName?: string;
    turnCount?: number;
    maxTurns?: number;
    detail?: string;
    startedAt?: number;
    finishedAt?: number;
  };
  compaction?: {
    summary?: string;
    firstKeptId?: string;
    foldedThroughId?: string;
    tokensBefore?: number;
    by?: string;
  };
  call?: {
    callId?: string;
    client?: string;
    startedAt?: number;
    endedAt?: number;
    seconds?: number;
    endReason?: string;
    title?: string;
    error?: string;
    status?: string;
  };
  digest?: {
    turnId?: string;
    reply?: string;
    durationMs?: number;
    toolCalls?: number;
    tools?: Array<{ name?: string; count?: number; failed?: number; sample?: string }>;
    usage?: { input?: number; output?: number; cachedInput?: number; costUsd?: number | null };
    hookCoverage?: string;
  };
  png?: string;
  hasImage?: boolean;
  pngOmitted?: boolean;
  mime?: string;
  serverCard?: boolean;
  aside?: boolean;
  waitRequest?: { waitId?: string; label?: string; followUp?: string; settled?: string };
  wait?: { id?: string; status?: string };
};

type RailTab = "screen" | "routines" | "browser" | "data";
type WorkLocation = "auto" | "cloud" | "private" | "this" | "browser" | "off";

/** Map UI work-loc chips to upstream bot.computer (null = Auto). */
function workLocToComputer(loc: WorkLocation): "cloud" | "vm" | "local" | "browser" | "off" | null {
  if (loc === "auto") return null;
  if (loc === "cloud") return "cloud";
  if (loc === "private") return "vm";
  if (loc === "this") return "local";
  if (loc === "browser") return "browser";
  return "off";
}

function computerToWorkLoc(computer: OmbBot["computer"]): WorkLocation {
  if (computer == null || computer === undefined) return "auto";
  if (computer === "cloud") return "cloud";
  if (computer === "vm") return "private";
  if (computer === "local") return "this";
  if (computer === "browser") return "browser";
  if (computer === "off") return "off";
  return "auto";
}
type QueuedPrompt = { id: string; text: string; local?: boolean };

const WEB_THREAD_KEY = "mausbot.webThreadId.v2";
const HISTORY_WINDOW = 36;

function formatWhen(at: number | undefined, zh: boolean) {
  if (!at) return "";
  const diff = Date.now() - at;
  if (!Number.isFinite(diff)) return "";
  if (diff < 60_000) return zh ? "刚刚" : "Just now";
  if (diff < 3_600_000) {
    const mins = Math.max(1, Math.floor(diff / 60_000));
    return zh ? `${mins} 分钟前` : `${mins}m ago`;
  }
  if (diff < 86_400_000) {
    const hours = Math.floor(diff / 3_600_000);
    return zh ? `${hours} 小时前` : `${hours}h ago`;
  }
  return new Date(at).toLocaleDateString(zh ? "zh-CN" : "en", { month: "numeric", day: "numeric" });
}

function threadMeta(task: OmbTask, zh: boolean) {
  if (task.busy) return zh ? "进行中" : "Running";
  const activity = (task.activity || "").trim();
  if (activity && activity !== "idle") return activity;
  return formatWhen(task.lastThreadOrderAt || task.updatedAt, zh) || (zh ? "空闲" : "Idle");
}

function readinessFrom(config: unknown): { computer: string | null; skills: string[] } {
  const skills: string[] = [];
  let computer: string | null = null;
  const pushSkill = (value: unknown) => {
    if (typeof value === "string" && value.trim()) skills.push(value.trim());
    else if (value && typeof value === "object") {
      const rec = value as Record<string, unknown>;
      const name = rec.name || rec.id || rec.title || rec.skill;
      if (typeof name === "string" && name.trim()) skills.push(name.trim());
    }
  };
  const walk = (node: unknown, key: string, depth: number) => {
    if (node == null || depth > 4) return;
    if (Array.isArray(node)) {
      if (/skill/i.test(key)) node.slice(0, 24).forEach(pushSkill);
      return;
    }
    if (typeof node !== "object") {
      if (/^(computer|vm|localVm|local_vm|sandbox)$/i.test(key) && (typeof node === "string" || typeof node === "boolean")) {
        computer = typeof node === "boolean" ? (node ? "ready" : "off") : String(node);
      }
      return;
    }
    const rec = node as Record<string, unknown>;
    if (/computer|localVm|local_vm|sandbox|^vm$/i.test(key)) {
      const status = rec.status ?? rec.state ?? rec.ready ?? rec.available;
      if (typeof status === "string" || typeof status === "boolean") {
        computer = typeof status === "boolean" ? (status ? "ready" : "off") : status;
      }
    }
    for (const [childKey, child] of Object.entries(rec)) walk(child, childKey, depth + 1);
  };
  walk(config, "", 0);
  return { computer, skills: [...new Set(skills)].slice(0, 12) };
}

const PREVIEW_BOTS: OmbBot[] = [
  {
    id: "bot-preview",
    name: "financial-bot",
    busy: true,
    activity: "整理研究笔记",
    modelSelection: { instanceId: "openaiCompat", model: "deepseek-chat" },
    tasks: [
      { threadId: "t-web", title: "网页对话", busy: true, activity: "执行中", unread: false, updatedAt: Date.now() },
      { threadId: "t-notes", title: "研究备忘", busy: false, activity: "idle", unread: true, updatedAt: Date.now() - 3_600_000 },
      { threadId: "t-quiet", title: "例行检查", busy: false, activity: "idle", unread: false, updatedAt: Date.now() - 86_400_000 },
    ],
  },
  {
    id: "bot-research",
    name: "Research",
    busy: false,
    activity: "idle",
    modelSelection: { instanceId: "openaiCompat", model: "deepseek-chat" },
    tasks: [{ threadId: "t-r1", title: "市场快报", unread: false, updatedAt: Date.now() - 7_200_000 }],
  },
];

const PREVIEW_MEMORY = "偏好：先给结论，再列依据。\n研究线程与网页线程分开。\n没有实时行情源时要直接说明，不要编造价格。";

const PREVIEW_MESSAGES: ChatMsg[] = [
  {
    id: "p-user-1",
    role: "user",
    kind: "text",
    text: "请确认 ifind-data-api 是否已启用。只回答是否启用和名称，不要取真实行情。",
    at: Date.now() - 120_000,
  },
  {
    id: "p-bot-1",
    role: "assistant",
    kind: "text",
    text: "ifind-data-api 未出现在当前可用技能列表里。\n\n| 项 | 状态 |\n| --- | --- |\n| 技能 | 未启用 |\n| 名称 | ifind-data-api |\n\n可以继续问配置，这一步没有取行情。",
    at: Date.now() - 110_000,
  },
  {
    id: "p-user-2",
    role: "user",
    kind: "text",
    text: "帮我看下这轮研究做到哪了。",
    at: Date.now() - 40_000,
    queued: true,
  },
  {
    id: "p-bot-2",
    role: "assistant",
    kind: "text",
    text: "这轮还停在技能核对。实时行情没有接到可执行环境，所以没有价格。\n\n下一步可以二选一：把取数环境挂上，或者改成公开资料的说明。",
    at: Date.now() - 20_000,
  },
  ...Array.from({ length: 8 }, (_, i) => ({
    id: `p-vm-${i}`,
    role: "assistant" as const,
    kind: "activity",
    text: "agents_vm_exec",
    tool: {
      name: "agents_vm_exec",
      label: "agents_vm_exec",
      ok: true as const,
      summary: i === 7 ? "正在进行 ifind_api 行情查询" : `step ${i + 1}`,
      output: i === 7 ? '{\n  "tool": "ifind_api",\n  "status": "running",\n  "note": "collapsed until opened"\n}' : undefined,
    },
  })),
  {
    id: "p-img",
    role: "assistant",
    kind: "text",
    text: "走势用一张图更容易看清。",
    attachments: [{ kind: "image", path: "/logo.png", mime: "image/png", name: "logo.png" }],
  },
  {
    id: "p-card",
    role: "assistant",
    kind: "options",
    text: "继续之前需要你确认这次工具调用。",
    cardTitle: "允许读取本地技能说明？",
    cardSubtitle: "只读技能目录，不会下单或外发。",
    cardRequestId: "preview-req",
    cardRequestType: "permission",
    cardTool: "agents_skills_list",
  },
];

const PREVIEW_BY_THREAD: Record<string, ChatMsg[]> = {
  "t-web": PREVIEW_MESSAGES,
  "t-notes": [
    {
      id: "n1",
      role: "assistant",
      kind: "text",
      text: "这条备忘是空的。把想记住的研究约束发过来就行。",
    },
  ],
  "t-quiet": [],
  "t-r1": [
    {
      id: "r1",
      role: "assistant",
      kind: "text",
      text: "Research 这条线程还没有新的运行记录。",
    },
  ],
};

function useZh() {
  if (typeof window === "undefined") return true;
  const sp = new URLSearchParams(window.location.search);
  const lang = (sp.get("lang") || "").toLowerCase();
  if (lang.startsWith("en")) return false;
  if (lang.startsWith("zh")) return true;
  return true;
}

async function readJson(res: Response) {
  const raw = await res.text();
  try {
    return raw ? JSON.parse(raw) : null;
  } catch {
    return { raw };
  }
}

function isNoisyTitle(title?: string) {
  const t = (title || "").trim().toLowerCase();
  return (
    !t ||
    t === "probe" ||
    t.includes("probe") ||
    t.startsWith("audit-tmp") ||
    t === "hi" ||
    t === "ping"
  );
}

function attachmentBasename(path: string): string {
  const clean = String(path || "").split(/[?#]/, 1)[0] || "";
  const parts = clean.split(/[/\\]/);
  return decodeAttachmentName(parts[parts.length - 1] || clean);
}

function decodeAttachmentName(name: string): string {
  try {
    return decodeURIComponent(name);
  } catch {
    return name;
  }
}

/** Single path segment. Unicode names are allowed; slashes and `..` are not. */
function isSafeAttachmentName(name: string): boolean {
  if (!name || name.length > 180) return false;
  if (name === "." || name === ".." || name.includes("..")) return false;
  if (/[\\/\0\s<>:"|?*]/.test(name)) return false;
  return true;
}

function isImageLike(mime: string | undefined, fileName: string | undefined): boolean {
  if (mime && /^image\//i.test(mime)) return true;
  return /\.(png|jpe?g|gif|webp)$/i.test(fileName || "");
}

function isWorkspaceImageName(name: string): boolean {
  return isSafeAttachmentName(name) && /\.(png|jpe?g|gif|webp)$/i.test(name);
}

function isWorkspaceMediaName(name: string): boolean {
  return isSafeAttachmentName(name) && /\.(png|jpe?g|gif|webp|pdf)$/i.test(name);
}

function isPdfHref(url: string): boolean {
  return /\.pdf(?:$|[?#])/i.test(String(url || "").trim());
}

function safeHttpUrl(url: string): string | null {
  const raw = String(url || "").trim();
  if (!/^https?:\/\//i.test(raw)) return null;
  try {
    const parsed = new URL(raw);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
    return parsed.href;
  } catch {
    return null;
  }
}

function markdownPdfCard(href: string, label: string): string {
  const safeHref = escapeHtml(href);
  const safeLabel = label || escapeHtml("PDF");
  // Safari can replace the whole tab when an unsandboxed external PDF iframe
  // starts loading after chat history arrives. Keep a clickable link instead.
  return `<span class="mausPdfCard">${markdownHttpLink(safeHref, safeLabel)}</span>`;
}

function markdownHttpLink(safeHref: string, safeLabel: string): string {
  return `<a class="mausMdLink" href="${safeHref}" target="_blank" rel="noopener noreferrer">${safeLabel}</a>`;
}

function workspaceImageProxyUrl(path: string): string | null {
  let raw = String(path || "").trim();
  if (!raw) return null;
  if (/^file:/i.test(raw)) {
    try {
      raw = new URL(raw).pathname;
    } catch {
      raw = raw.replace(/^file:\/\//i, "");
      if (!raw.startsWith("/")) raw = `/${raw}`;
    }
  }
  const clean = (raw.split(/[?#]/, 1)[0] || "").replace(/\\/g, "/");
  if (clean.startsWith("/api/mausbot/workspace-images/")) {
    const parts = clean.split("/").filter(Boolean);
    if (parts.length !== 6) return null;
    const botId = decodeAttachmentName(parts[3]);
    const threadId = decodeAttachmentName(parts[4]);
    const name = decodeAttachmentName(parts[5]);
    if (!isSafeAttachmentName(botId) || !isSafeAttachmentName(threadId) || !isWorkspaceMediaName(name)) return null;
    return `/api/mausbot/workspace-images/${encodeURIComponent(botId)}/${encodeURIComponent(threadId)}/${encodeURIComponent(name)}`;
  }
  const marker = "/.openmausbot/task-workspaces/";
  const idx = clean.indexOf(marker);
  if (idx < 0) return null;
  if (clean.slice(0, idx) !== "/home/maus") return null;
  const segs = clean.slice(idx + marker.length).split("/").filter(Boolean);
  if (segs.length !== 3) return null;
  const botId = decodeAttachmentName(segs[0]);
  const threadId = decodeAttachmentName(segs[1]);
  const name = decodeAttachmentName(segs[2]);
  if (!isSafeAttachmentName(botId) || !isSafeAttachmentName(threadId) || !isWorkspaceMediaName(name)) return null;
  return `/api/mausbot/workspace-images/${encodeURIComponent(botId)}/${encodeURIComponent(threadId)}/${encodeURIComponent(name)}`;
}

function attachmentProxyUrl(path: string): string | null {
  const raw = String(path || "").trim();
  if (!raw) return null;
  if (raw.startsWith("data:image/")) return raw;
  const workspace = workspaceImageProxyUrl(raw);
  if (workspace) return workspace;
  if (raw.startsWith("/api/mausbot/attachments/")) return raw.split(/[?#]/, 1)[0];
  if (/^https?:\/\//i.test(raw)) {
    const remote = raw.match(/\/api\/attachments\/([^/?#]+)/);
    if (!remote) return raw;
    const name = decodeAttachmentName(remote[1]);
    return isSafeAttachmentName(name) ? `/api/mausbot/attachments/${encodeURIComponent(name)}` : null;
  }
  const localApi = raw.match(/\/api\/attachments\/([^/?#]+)/);
  if (localApi) {
    const name = decodeAttachmentName(localApi[1]);
    return isSafeAttachmentName(name) ? `/api/mausbot/attachments/${encodeURIComponent(name)}` : null;
  }
  // Same-origin public files such as /logo.png. Filesystem paths still proxy by basename.
  if (/^\/[^/]+\.(png|jpe?g|gif|webp|svg)$/i.test(raw)) return raw;
  const name = attachmentBasename(raw);
  if (!isSafeAttachmentName(name)) return null;
  return `/api/mausbot/attachments/${encodeURIComponent(name)}`;
}

function mapAttachments(raw: unknown): MsgAttachment[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  const out: MsgAttachment[] = [];
  for (const item of raw.slice(0, 24)) {
    if (!item || typeof item !== "object") continue;
    const a = item as Record<string, unknown>;
    const kind = a.kind != null ? String(a.kind) : "";
    const p = a.path != null ? String(a.path) : "";
    if (!kind || !p) continue;
    const entry: MsgAttachment = { kind, path: p.slice(0, 500) };
    if (a.mime != null) entry.mime = String(a.mime).slice(0, 120);
    if (a.name != null) entry.name = String(a.name).slice(0, 200);
    if (kind === "audio") {
      if (a.durationMs != null && Number.isFinite(Number(a.durationMs))) entry.durationMs = Number(a.durationMs);
      if (a.text != null) entry.text = String(a.text).slice(0, 400);
    }
    out.push(entry);
  }
  return out.length ? out : undefined;
}

function isImageAttachment(a: MsgAttachment): boolean {
  return a.kind === "image" || isImageLike(a.mime, a.name || attachmentBasename(a.path));
}

function mapTool(raw: unknown): ChatMsgTool | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const t = raw as Record<string, unknown>;
  const out: ChatMsgTool = {};
  if (t.name != null) out.name = String(t.name);
  if (t.label != null) out.label = String(t.label);
  if (t.summary != null) out.summary = String(t.summary);
  if (t.input != null) out.input = String(t.input);
  if (t.output != null) out.output = String(t.output);
  if (t.error != null) out.error = String(t.error);
  if ("ok" in t) out.ok = t.ok == null ? null : Boolean(t.ok);
  if (t.partial != null) out.partial = Boolean(t.partial);
  if (t.clipped != null) out.clipped = Boolean(t.clipped);
  return out.name || out.label || out.ok !== undefined ? out : undefined;
}

function mapCard(raw: unknown): {
  title?: string;
  subtitle?: string;
  options?: string[];
  requestId?: string;
  requestType?: "permission" | "question";
  tool?: string;
  answered?: string;
  answeredText?: string;
  dismissed?: boolean;
  expired?: boolean;
  stopped?: boolean;
  held?: string;
  heldCode?: string;
  allowKey?: string;
  allowSession?: boolean;
  approvalScope?: string;
  fixedOptions?: boolean;
  autoApplied?: boolean;
  undone?: boolean;
  commandAllowlist?: { command?: string; cwd?: string; providerInstanceId?: string };
  computerAsk?: { reason?: string; device?: string; app?: string; want?: string };
  yourComputer?: { app?: string; site?: string };
  skillRequest?: { action?: string; name?: string; path?: string; present?: boolean };
  modelRequest?: { botId?: string; targetBotId?: string; targetName?: string; selection?: { instanceId?: string; model?: string }; present?: boolean };
  questionRequest?: { version?: string; present?: boolean };
  profileRequest?: { botId?: string; targetBotId?: string; targetName?: string; present?: boolean };
  routineRequest?: { action?: string; name?: string; present?: boolean };
  accessRequest?: { botId?: string; botName?: string; email?: string; name?: string; present?: boolean };
  outsideWrite?: { kind?: string; action?: string; roomId?: string; text?: string; bulletin?: string; present?: boolean };
  mcpRequest?: { action?: string; name?: string; present?: boolean };
  lendingLift?: { requestMessageId?: string; outside?: boolean; botOpened?: boolean; present?: boolean };
  waitRequest?: { waitId?: string; label?: string; followUp?: string; settled?: string };
} | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const c = raw as Record<string, unknown>;
  const title = c.title != null ? String(c.title) : c.question != null ? String(c.question) : undefined;
  const subtitle = c.subtitle != null ? String(c.subtitle) : undefined;
  const options: string[] = [];
  if (Array.isArray(c.options)) {
    for (const opt of c.options.slice(0, 24)) {
      if (typeof opt === "string") options.push(opt);
      else if (opt && typeof opt === "object") {
        const o = opt as Record<string, unknown>;
        options.push(String(o.label || o.text || o.id || ""));
      }
    }
  }
  const hasSubtype = Boolean(
    c.computerAsk || c.yourComputer || c.skillRequest || c.modelRequest || c.questionRequest
    || c.profileRequest || c.routineRequest || c.accessRequest || c.outsideWrite || c.mcpRequest
    || c.lendingLift || c.commandAllowlist || c.allowKey || c.waitRequest,
  );
  if (!title && !subtitle && !options.length && !c.requestId && !hasSubtype) return undefined;
  const waitRequest = (c.waitRequest && typeof c.waitRequest === "object") ? {
    waitId: (c.waitRequest as any).waitId != null ? String((c.waitRequest as any).waitId).slice(0, 120) : undefined,
    label: (c.waitRequest as any).label != null ? String((c.waitRequest as any).label).slice(0, 200) : undefined,
    followUp: (c.waitRequest as any).followUp != null ? String((c.waitRequest as any).followUp).slice(0, 800) : undefined,
    settled: (c.waitRequest as any).settled != null ? String((c.waitRequest as any).settled).slice(0, 40) : undefined,
  } : undefined;
  const nest = (key: string, fields: string[]) => {
    const n = c[key];
    if (!n || typeof n !== "object") return undefined;
    const src = n as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const f of fields) {
      if (src[f] != null) out[f] = typeof src[f] === "boolean" ? Boolean(src[f]) : String(src[f]).slice(0, 400);
    }
    if (key === "modelRequest" && src.selection && typeof src.selection === "object") {
      const sel = src.selection as Record<string, unknown>;
      out.selection = {
        instanceId: sel.instanceId != null ? String(sel.instanceId).slice(0, 80) : undefined,
        model: sel.model != null ? String(sel.model).slice(0, 200) : undefined,
      };
    }
    if (!Object.keys(out).length) out.present = true;
    return out;
  };
  const commandAllowlist = (c.commandAllowlist && typeof c.commandAllowlist === "object") ? {
    command: (c.commandAllowlist as any).command != null ? String((c.commandAllowlist as any).command).slice(0, 500) : undefined,
    cwd: (c.commandAllowlist as any).cwd != null ? String((c.commandAllowlist as any).cwd).slice(0, 300) : undefined,
    providerInstanceId: (c.commandAllowlist as any).providerInstanceId != null ? String((c.commandAllowlist as any).providerInstanceId).slice(0, 120) : undefined,
  } : undefined;
  return {
    title,
    subtitle,
    options: options.filter(Boolean),
    requestId: c.requestId != null ? String(c.requestId) : undefined,
    requestType: c.requestType === "permission" || c.requestType === "question" ? c.requestType : undefined,
    tool: c.tool != null ? String(c.tool) : undefined,
    answered: c.answered != null ? String(c.answered) : undefined,
    answeredText: c.answeredText != null ? String(c.answeredText) : undefined,
    dismissed: c.dismissed != null ? Boolean(c.dismissed) : undefined,
    expired: c.expired != null ? Boolean(c.expired) : undefined,
    stopped: c.stopped != null ? Boolean(c.stopped) : undefined,
    held: c.held != null ? String(c.held) : undefined,
    heldCode: c.heldCode != null ? String(c.heldCode) : undefined,
    allowKey: c.allowKey != null ? String(c.allowKey) : undefined,
    allowSession: c.allowSession != null ? Boolean(c.allowSession) : undefined,
    approvalScope: c.approvalScope != null ? String(c.approvalScope) : undefined,
    fixedOptions: c.fixedOptions != null ? Boolean(c.fixedOptions) : undefined,
    autoApplied: c.autoApplied != null ? Boolean(c.autoApplied) : undefined,
    undone: c.undone != null ? Boolean(c.undone) : undefined,
    commandAllowlist,
    computerAsk: nest("computerAsk", ["reason", "device", "app", "want"]) as any,
    yourComputer: nest("yourComputer", ["app", "site"]) as any,
    skillRequest: nest("skillRequest", ["action", "name", "path", "present"]) as any,
    modelRequest: nest("modelRequest", ["botId", "targetBotId", "targetName", "present"]) as any,
    questionRequest: nest("questionRequest", ["version", "present"]) as any,
    profileRequest: nest("profileRequest", ["botId", "targetBotId", "targetName", "present"]) as any,
    routineRequest: nest("routineRequest", ["action", "name", "present"]) as any,
    accessRequest: nest("accessRequest", ["botId", "botName", "email", "name", "present"]) as any,
    outsideWrite: nest("outsideWrite", ["kind", "action", "roomId", "text", "bulletin", "present"]) as any,
    mcpRequest: nest("mcpRequest", ["action", "name", "present"]) as any,
    lendingLift: nest("lendingLift", ["requestMessageId", "outside", "botOpened", "present"]) as any,
    waitRequest,
  };
}

function waitLineText(raw: unknown): string | undefined {
  if (raw == null) return undefined;
  if (typeof raw === "string") return raw;
  if (typeof raw === "object") {
    const w = raw as Record<string, unknown>;
    return String(w.text || w.line || w.message || JSON.stringify(w)).slice(0, 500);
  }
  return String(raw);
}


function mapSecret(raw: unknown): ChatMsg["secret"] | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const s = raw as Record<string, unknown>;
  const out: NonNullable<ChatMsg["secret"]> = {};
  if (s.target != null) out.target = String(s.target).slice(0, 80);
  if (s.label != null) out.label = String(s.label).slice(0, 120);
  if (s.description != null) out.description = String(s.description).slice(0, 1200);
  if (s.placeholder != null) out.placeholder = String(s.placeholder).slice(0, 120);
  if (s.helpUrl != null) out.helpUrl = String(s.helpUrl).slice(0, 500);
  if (s.delivery != null) out.delivery = String(s.delivery).slice(0, 40);
  if (s.reason != null) out.reason = String(s.reason).slice(0, 500);
  if (s.requestKey != null) out.requestKey = String(s.requestKey).slice(0, 160);
  if (s.error != null) out.error = String(s.error).slice(0, 500);
  for (const key of ["provided", "dismissed", "superseded", "resumed", "stopped"] as const) {
    if (s[key] != null) out[key] = Boolean(s[key]);
  }
  if (s.savedAt != null && Number.isFinite(Number(s.savedAt))) out.savedAt = Number(s.savedAt);
  return out.label || out.target || out.requestKey ? out : undefined;
}

function mapSignIn(raw: unknown): ChatMsg["signIn"] | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const s = raw as Record<string, unknown>;
  const out: NonNullable<ChatMsg["signIn"]> = {};
  for (const [key, lim] of [["url", 500], ["site", 200], ["reason", 800], ["place", 40], ["savedLogin", 200], ["error", 500]] as const) {
    if (s[key] != null) (out as any)[key] = String(s[key]).slice(0, lim);
  }
  for (const key of ["done", "cancelled", "superseded", "resumed", "stopped", "answeredInChat"] as const) {
    if (s[key] != null) out[key] = Boolean(s[key]);
  }
  return out.site || out.url ? out : undefined;
}

function mapConnector(raw: unknown): ChatMsg["connector"] | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const s = raw as Record<string, unknown>;
  const out: NonNullable<ChatMsg["connector"]> = {};
  for (const [key, lim] of [["source", 40], ["slug", 120], ["label", 160], ["description", 1200], ["status", 40], ["resumeKey", 160], ["alias", 120], ["error", 500]] as const) {
    if (s[key] != null) (out as any)[key] = String(s[key]).slice(0, lim);
  }
  for (const key of ["dismissed", "superseded", "resumed", "stopped"] as const) {
    if (s[key] != null) out[key] = Boolean(s[key]);
  }
  return out.slug || out.label ? out : undefined;
}

function mapApp(raw: unknown): ChatMsg["app"] | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const s = raw as Record<string, unknown>;
  const out: NonNullable<ChatMsg["app"]> = {};
  for (const [key, lim] of [["source", 20], ["title", 120], ["doc", 160], ["blocked", 40], ["format", 20]] as const) {
    if (s[key] != null) (out as any)[key] = String(s[key]).slice(0, lim);
  }
  for (const key of ["rev", "bytes", "height"] as const) {
    if (s[key] != null && Number.isFinite(Number(s[key]))) (out as any)[key] = Number(s[key]);
  }
  for (const key of ["border", "live"] as const) {
    if (s[key] != null) out[key] = Boolean(s[key]);
  }
  if (s.mcp && typeof s.mcp === "object") {
    const mcp = s.mcp as Record<string, unknown>;
    out.mcp = {};
    if (mcp.server != null) out.mcp.server = String(mcp.server).slice(0, 120);
    if (mcp.tool != null) out.mcp.tool = String(mcp.tool).slice(0, 120);
    if (mcp.resourceUri != null) out.mcp.resourceUri = String(mcp.resourceUri).slice(0, 300);
  }
  return out.title || out.doc || out.source ? out : undefined;
}

function mapRoutineRun(raw: unknown): ChatMsg["routineRun"] | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const s = raw as Record<string, unknown>;
  const out: NonNullable<ChatMsg["routineRun"]> = {};
  for (const [key, lim] of [["runId", 120], ["routineId", 120], ["routineName", 200], ["status", 40], ["goalStatus", 40], ["executionThreadId", 120], ["summary", 1200], ["error", 800]] as const) {
    if (s[key] != null) (out as any)[key] = String(s[key]).slice(0, lim);
  }
  for (const key of ["scheduledFor", "deferredAt"] as const) {
    if (s[key] != null && Number.isFinite(Number(s[key]))) out[key] = Number(s[key]);
  }
  return out.runId || out.routineName || out.status ? out : undefined;
}

function mapGoalRun(raw: unknown): ChatMsg["goalRun"] | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const s = raw as Record<string, unknown>;
  const out: NonNullable<ChatMsg["goalRun"]> = {};
  for (const [key, lim] of [["runId", 120], ["goal", 800], ["status", 40], ["coordinatorName", 120], ["detail", 800]] as const) {
    if (s[key] != null) (out as any)[key] = String(s[key]).slice(0, lim);
  }
  for (const key of ["turnCount", "maxTurns", "startedAt", "finishedAt"] as const) {
    if (s[key] != null && Number.isFinite(Number(s[key]))) out[key] = Number(s[key]);
  }
  return out.runId || out.goal || out.status ? out : undefined;
}

function mapCompaction(raw: unknown): ChatMsg["compaction"] | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const s = raw as Record<string, unknown>;
  const out: NonNullable<ChatMsg["compaction"]> = {};
  if (s.summary != null) out.summary = String(s.summary).slice(0, 4000);
  for (const [key, lim] of [["firstKeptId", 120], ["foldedThroughId", 120], ["by", 40]] as const) {
    if (s[key] != null) (out as any)[key] = String(s[key]).slice(0, lim);
  }
  if (s.tokensBefore != null && Number.isFinite(Number(s.tokensBefore))) out.tokensBefore = Number(s.tokensBefore);
  return out.summary || out.firstKeptId ? out : undefined;
}

function mapCall(raw: unknown): ChatMsg["call"] | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const s = raw as Record<string, unknown>;
  const out: NonNullable<ChatMsg["call"]> = {};
  for (const [key, lim] of [["callId", 120], ["client", 40], ["endReason", 40], ["title", 200], ["error", 400], ["status", 40]] as const) {
    if (s[key] != null) (out as any)[key] = String(s[key]).slice(0, lim);
  }
  for (const key of ["startedAt", "endedAt", "seconds"] as const) {
    if (s[key] != null && Number.isFinite(Number(s[key]))) out[key] = Number(s[key]);
  }
  return out.callId || out.title || out.endReason ? out : undefined;
}

function mapDigest(raw: unknown): ChatMsg["digest"] | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const s = raw as Record<string, unknown>;
  const out: NonNullable<ChatMsg["digest"]> = {};
  if (s.turnId != null) out.turnId = String(s.turnId).slice(0, 120);
  if (s.reply != null) out.reply = String(s.reply).slice(0, 500);
  if (s.hookCoverage != null) out.hookCoverage = String(s.hookCoverage).slice(0, 20);
  for (const key of ["durationMs", "toolCalls"] as const) {
    if (s[key] != null && Number.isFinite(Number(s[key]))) out[key] = Number(s[key]);
  }
  if (Array.isArray(s.tools)) {
    out.tools = s.tools.slice(0, 12).map((t) => {
      const tool = (t && typeof t === "object") ? t as Record<string, unknown> : {};
      return {
        name: tool.name != null ? String(tool.name).slice(0, 120) : undefined,
        count: tool.count != null ? Number(tool.count) : undefined,
        failed: tool.failed != null ? Number(tool.failed) : undefined,
        sample: tool.sample != null ? String(tool.sample).slice(0, 200) : undefined,
      };
    });
  }
  if (s.usage && typeof s.usage === "object") {
    const u = s.usage as Record<string, unknown>;
    out.usage = {
      input: u.input != null ? Number(u.input) : undefined,
      output: u.output != null ? Number(u.output) : undefined,
      cachedInput: u.cachedInput != null ? Number(u.cachedInput) : undefined,
      costUsd: u.costUsd == null ? null : Number(u.costUsd),
    };
  }
  return out.turnId || out.reply || out.tools?.length ? out : undefined;
}

function dropSupersededReplies(raw: any[]): any[] {
  // 插话可能让同一回合出现“重写的整段答案”或“补充的一短句”。
  // 历史里只保留最新那份完整答案，并把短句并回上一条，避免看起来回复了两次。
  const out: any[] = [];
  let lastIdx = -1;
  let lastTurn = "";
  let lastOpening = "";
  for (const m of raw) {
    if (!m || typeof m !== "object") { out.push(m); continue; }
    const role = m.role;
    if (role === "user") { lastIdx = -1; lastTurn = ""; lastOpening = ""; out.push(m); continue; }
    if ((role === "bot" || role === "assistant") && (m.kind || "text") === "text" && m.turnId) {
      const turn = String(m.turnId);
      const opening = String(m.text || "").replace(/\s+/g, "").slice(0, 20);
      if (lastIdx >= 0 && lastTurn === turn) {
        if (opening && lastOpening && opening === lastOpening) {
          out[lastIdx] = m;
          lastOpening = opening;
          continue;
        }
        const prevText = String(out[lastIdx]?.text || "");
        const nextText = String(m.text || "").trim();
        if (nextText && nextText.length <= 40 && prevText.length > 120) {
          out[lastIdx] = { ...out[lastIdx], text: `${prevText.replace(/\s+$/, "")}\n\n${nextText}` };
          continue;
        }
      }
      lastIdx = out.length;
      lastTurn = turn;
      lastOpening = opening;
    }
    out.push(m);
  }
  return out;
}

function modelProvenanceFrom(value: any): ModelProvenance | undefined {
  if (!value || typeof value !== "object") return undefined;
  const candidates = [
    value,
    value.modelProvenance,
    value.model_provenance,
    value._meta,
    value.meta,
    value.metadata?._meta,
    value.metadata,
    value.modelInfo,
    value.model_info,
  ].filter((item) => item && typeof item === "object" && !Array.isArray(item));
  const result: ModelProvenance = {};
  const text = (source: any, keys: string[]) => {
    for (const key of keys) {
      const candidate = source?.[key];
      if (typeof candidate === "string" && candidate.trim()) return candidate.trim().slice(0, 160);
    }
    return undefined;
  };
  for (const meta of candidates) {
    const requestedProvider = text(meta, ["requested_model_provider", "requestedModelProvider", "requestedProvider"]);
    const fallbackReason = text(meta, ["model_fallback", "modelFallback", "fallback_reason", "fallbackReason"]);
    const explicitProvider = text(meta, ["actual_model_provider", "actualModelProvider", "actualProvider"]);
    const actualProvider = explicitProvider || ((requestedProvider || fallbackReason)
      ? text(meta, ["model_provider", "modelProvider"])
      : undefined);
    const actualModel = text(meta, ["actual_model", "actualModel"])
      || (actualProvider && (requestedProvider || fallbackReason) ? text(meta, ["model"]) : undefined);
    const requestedModel = text(meta, ["requested_model", "requestedModel"]);
    if (actualProvider) result.actualProvider = actualProvider;
    if (actualModel) result.actualModel = actualModel;
    if (requestedProvider) result.requestedProvider = requestedProvider;
    if (requestedModel) result.requestedModel = requestedModel;
    if (fallbackReason) result.fallbackReason = fallbackReason;
  }
  return Object.values(result).some(Boolean) ? result : undefined;
}

function modelProvenanceLabel(info: ModelProvenance | undefined, zh: boolean): string {
  if (!info?.actualProvider && !info?.actualModel) {
    return info?.fallbackReason
      ? (zh ? `已报告降级，实际模型未知（${info.fallbackReason.replace(/_/g, " ")}）` : `Fallback reported; actual model unknown (${info.fallbackReason.replace(/_/g, " ")})`)
      : "";
  }
  const provider = (value?: string) => {
    const normalized = (value || "").trim().toLowerCase();
    if (normalized === "deepseek") return "DeepSeek";
    if (normalized === "chatgpt" || normalized === "gpt") return "ChatGPT";
    if (normalized === "openaicompat") return "OpenAI-compatible";
    return value?.trim() || "";
  };
  const actual = [provider(info.actualProvider), info.actualModel].filter(Boolean).join(" · ");
  const requested = [provider(info.requestedProvider), info.requestedModel].filter(Boolean).join(" · ");
  if (info.fallbackReason) {
    const reason = info.fallbackReason.replace(/_/g, " ");
    return zh
      ? `已降级：${requested || "请求模型"} → ${actual || "实际模型未知"} · ${reason}`
      : `Fallback: ${requested || "requested model"} → ${actual || "actual model unknown"} · ${reason}`;
  }
  const requestedNote = requested && requested.toLowerCase() !== actual.toLowerCase()
    ? ` · ${zh ? "请求" : "requested"}: ${requested}`
    : "";
  return `${zh ? "实际模型" : "Actual model"}: ${actual}${requestedNote}`;
}

function wireToChatMsg(m: any): ChatMsg | null {
  if (!m || !(m.role === "user" || m.role === "bot" || m.role === "assistant")) return null;
  const text = String(m.text || "");
  const tool = mapTool(m.tool);
  const card = mapCard(m.card);
  const secret = mapSecret(m.secret);
  const signIn = mapSignIn(m.signIn || m.signin);
  const connector = mapConnector(m.connector);
  const app = mapApp(m.app);
  const routineRun = mapRoutineRun(m.routineRun);
  const goalRun = mapGoalRun(m.goalRun);
  const compaction = mapCompaction(m.compaction);
  const call = mapCall(m.call);
  const digest = mapDigest(m.digest);
  const attachments = mapAttachments(m.attachments);
  const kind = m.kind ? String(m.kind) : "text";
  const keepKinds = new Set([
    "activity", "options", "secret", "signin", "connector", "widget", "app",
    "screen", "routine.run", "goal.run", "compaction", "call", "digest",
  ]);
  if (
    !text.trim()
    && !(attachments && attachments.length)
    && !keepKinds.has(kind)
    && !tool
    && !secret
    && !signIn
    && !connector
    && !app
    && !routineRun
    && !goalRun
    && !compaction
    && !call
    && !digest
    && !m.widget
    && !m.png
  ) {
    return null;
  }
  let resolvedKind = kind;
  if (secret && kind === "text") resolvedKind = "secret";
  if (signIn && (kind === "text" || !kind)) resolvedKind = "signin";
  if (connector && (kind === "text" || !kind)) resolvedKind = "connector";
  return {
    id: String(m.id),
    role: m.role === "user" ? "user" : "assistant",
    kind: resolvedKind,
    text,
    modelProvenance: modelProvenanceFrom(m),
    at: m.at,
    helperIntro: Boolean(m.helperIntro),
    attachments,
    tool,
    steered: m.steered != null ? Boolean(m.steered) : undefined,
    queued: m.queued != null ? Boolean(m.queued) : undefined,
    aside: m.aside != null ? Boolean(m.aside) : undefined,
    serverCard: m.serverCard != null ? Boolean(m.serverCard) : undefined,
    cardTitle: card?.title,
    cardSubtitle: card?.subtitle,
    cardOptions: card?.options,
    cardRequestId: card?.requestId,
    cardRequestType: card?.requestType,
    cardTool: card?.tool,
    cardAnswered: card?.answered,
    cardAnsweredText: card?.answeredText,
    cardDismissed: card?.dismissed,
    cardExpired: card?.expired,
    cardStopped: card?.stopped,
    cardHeld: card?.held,
    cardHeldCode: card?.heldCode,
    cardAllowKey: card?.allowKey,
    cardAllowSession: card?.allowSession,
    cardApprovalScope: card?.approvalScope,
    cardFixedOptions: card?.fixedOptions,
    cardAutoApplied: card?.autoApplied,
    cardUndone: card?.undone,
    cardCommandAllowlist: card?.commandAllowlist,
    cardComputerAsk: card?.computerAsk,
    cardYourComputer: card?.yourComputer,
    cardSkillRequest: card?.skillRequest,
    cardModelRequest: card?.modelRequest,
    cardQuestionRequest: card?.questionRequest,
    cardProfileRequest: card?.profileRequest,
    cardRoutineRequest: card?.routineRequest,
    cardAccessRequest: card?.accessRequest,
    cardOutsideWrite: card?.outsideWrite,
    cardMcpRequest: card?.mcpRequest,
    cardLendingLift: card?.lendingLift,
    waitLine: waitLineText(m.waitLine),
    waitRequest: card && (card as any).waitRequest ? (card as any).waitRequest : (m.card?.waitRequest || undefined),
    wait: m.wait && typeof m.wait === "object" ? { id: m.wait.id, status: m.wait.status } : undefined,
    secret,
    signIn,
    connector,
    widget: m.widget && typeof m.widget === "object" ? m.widget : undefined,
    app,
    fromApp: m.fromApp && typeof m.fromApp === "object" ? { messageId: m.fromApp.messageId, title: m.fromApp.title } : undefined,
    pointed: Array.isArray(m.pointed) ? m.pointed.slice(0, 12) : undefined,
    routineRun,
    automation: m.automation && typeof m.automation === "object" ? m.automation : undefined,
    goalRun,
    compaction,
    call,
    digest,
    png: typeof m.png === "string" ? m.png : undefined,
    hasImage: m.hasImage != null ? Boolean(m.hasImage) : undefined,
    pngOmitted: m.pngOmitted != null ? Boolean(m.pngOmitted) : undefined,
    mime: m.mime ? String(m.mime) : undefined,
  };
}

function runtimeStatusLine(ev: any, zh: boolean): string | null {
  if (!ev || typeof ev !== "object") return null;
  const type = String(ev.type || "");
  if (type === "turn.wait_started") {
    const resource = String(ev.resource || (zh ? "资源" : "resource"));
    const pos = ev.position != null ? String(ev.position) : "?";
    const holder = ev.holder?.task || ev.holder?.name;
    const base = zh ? `等待执行：${resource}（排队#${pos}）` : `Waiting: ${resource} (queue #${pos})`;
    return holder ? `${base} · ${holder}` : base;
  }
  if (type === "turn.wait_ended") {
    const outcome = String(ev.outcome || "");
    if (zh) {
      if (outcome === "acquired") return "等待结束，开始执行";
      if (outcome === "parked") return "等待已挂起，稍后继续";
      if (outcome === "stopped") return "等待已停止";
      return "等待结束";
    }
    return `Wait ended (${outcome || "done"})`;
  }
  if (type === "item.started") {
    const label = localizeActivityPhrase(String(ev.title || ev.summary || ev.tool || ev.itemType || ""), zh);
    return zh ? (label.startsWith("正在") ? label : `执行中：${label}`) : `Running: ${label}`;
  }
  if (type === "item.output") {
    const tail = String(ev.text || "").trim().split(/\n/).filter(Boolean).slice(-1)[0] || "";
    if (!tail) return null;
    return zh ? `输出：${tail.slice(0, 120)}` : `Output: ${tail.slice(0, 120)}`;
  }
  if (type === "item.completed" && ev.itemType === "tool") {
    const ok = ev.ok !== false;
    const label = localizeActivityPhrase(String(ev.title || ev.summary || ""), zh);
    return ok ? (zh ? `完成：${label}` : `Done: ${label}`) : (zh ? `失败：${label}` : `Failed: ${label}`);
  }
  if (type === "turn.progress") {
    const act = localizeActivityPhrase(String(ev.activity || ev.tool || ""), zh);
    if (!act) return zh ? THINKING_ZH : THINKING_EN;
    return act;
  }
  if (type === "turn.started") return zh ? THINKING_ZH : THINKING_EN;
  if (type === "request.opened") {
    const summary = ev.summary || ev.tool || "";
    return zh ? `需要确认：${summary}` : `Needs approval: ${summary}`;
  }
  if (type === "turn.completed") return null;
  return null;
}

function escapeHtml(s: string) {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Light markdown → HTML for bubble body (bold / lists / tables / images). */
function renderLightMarkdown(text: string): string {
  const lines = String(text || "").replace(/\r\n/g, "\n").split("\n");
  const html: string[] = [];
  let i = 0;
  let inUl = false;
  let inOl = false;

  const closeLists = () => {
    if (inUl) {
      html.push("</ul>");
      inUl = false;
    }
    if (inOl) {
      html.push("</ol>");
      inOl = false;
    }
  };

  const inline = (s: string) => {
    let out = escapeHtml(s);
    out = out.replace(
      /!\[([^\]]*)\]\(\s*([^)\s]+)\s*(?:\s+"[^"]*")?\)/g,
      (_m, alt: string, url: string) => {
        const rawUrl = String(url || "").trim();
        const resolved = attachmentProxyUrl(rawUrl);
        const href = resolved || ((safeHttpUrl(rawUrl) || (rawUrl.startsWith("/api/mausbot/") ? rawUrl : "")) || "");
        if (href && (isPdfHref(href) || isPdfHref(rawUrl))) {
          return markdownPdfCard(href, escapeHtml(alt || "PDF"));
        }
        if (resolved) {
          const safeSrc = escapeHtml(resolved);
          return `<span class="mausMdImgFrame"><img class="mausMdImg" src="${safeSrc}" alt="${escapeHtml(alt || "")}" tabindex="0" /><span class="mausMdImgFallback" role="status">图片加载失败 · <a href="${safeSrc}" target="_blank" rel="noopener noreferrer">打开图片</a></span></span>`;
        }
        // Safe http(s) or already-proxied URLs still render; filesystem paths do not leak into src.
        if (href && (/^https?:\/\//i.test(href) || href.startsWith("/api/mausbot/"))) {
          const safeSrc = escapeHtml(href);
          return `<span class="mausMdImgFrame"><img class="mausMdImg" src="${safeSrc}" alt="${escapeHtml(alt || "")}" tabindex="0" /><span class="mausMdImgFallback" role="status">图片加载失败 · <a href="${safeSrc}" target="_blank" rel="noopener noreferrer">打开图片</a></span></span>`;
        }
        return "";
      },
    );
    out = out.replace(
      /\[([^\]]+)\]\(\s*([^)\s]+)\s*(?:\s+"[^"]*")?\)/g,
      (_m, label: string, url: string) => {
        const rawUrl = String(url || "").trim();
        const httpHref = safeHttpUrl(rawUrl);
        const workspaceHref = workspaceImageProxyUrl(rawUrl);
        const href = httpHref || workspaceHref;
        if (!href) return String(label || "");
        const safeLabel = String(label || href);
        if (isPdfHref(href) || isPdfHref(rawUrl)) return markdownPdfCard(href, safeLabel);
        return markdownHttpLink(escapeHtml(href), safeLabel);
      },
    );
    out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
    return out;
  };

  while (i < lines.length) {
    const line = lines[i];
    const tableHeader = line.match(/^\|(.+)\|$/);
    const tableSep = i + 1 < lines.length ? lines[i + 1].match(/^\|[\s:\-|]+\|$/) : null;
    if (tableHeader && tableSep) {
      closeLists();
      const headers = tableHeader[1].split("|").map((c) => c.trim());
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && /^\|(.+)\|$/.test(lines[i])) {
        rows.push(lines[i].slice(1, -1).split("|").map((c) => c.trim()));
        i += 1;
      }
      html.push('<div class="mausTableWrap"><table><thead><tr>');
      headers.forEach((h) => html.push(`<th>${inline(h)}</th>`));
      html.push("</tr></thead><tbody>");
      rows.forEach((row) => {
        html.push("<tr>");
        row.forEach((c) => html.push(`<td>${inline(c)}</td>`));
        html.push("</tr>");
      });
      html.push("</tbody></table></div>");
      continue;
    }

    const ul = line.match(/^\s*[-*]\s+(.+)$/);
    if (ul) {
      if (inOl) {
        html.push("</ol>");
        inOl = false;
      }
      if (!inUl) {
        html.push("<ul>");
        inUl = true;
      }
      html.push(`<li>${inline(ul[1])}</li>`);
      i += 1;
      continue;
    }

    const ol = line.match(/^\s*\d+\.\s+(.+)$/);
    if (ol) {
      if (inUl) {
        html.push("</ul>");
        inUl = false;
      }
      if (!inOl) {
        html.push("<ol>");
        inOl = true;
      }
      html.push(`<li>${inline(ol[1])}</li>`);
      i += 1;
      continue;
    }

    closeLists();
    if (!line.trim()) {
      html.push("<br/>");
    } else {
      html.push(`<p>${inline(line)}</p>`);
    }
    i += 1;
  }
  closeLists();
  return html.join("");
}

function AttachImage({ src, alt }: { src: string; alt: string }) {
  const [attempt, setAttempt] = useState(0);
  const [failed, setFailed] = useState(false);
  const loadedRef = useRef(false);

  useEffect(() => {
    loadedRef.current = false;
    setAttempt(0);
    setFailed(false);
  }, [src]);

  if (failed) {
    return (
      <a className={styles.mausAttachFallback} href={src} target="_blank" rel="noreferrer">
        {alt || "图片附件"}
      </a>
    );
  }
  const url = attempt ? `${src}${src.includes("?") ? "&" : "?"}retry=${attempt}` : src;
  return (
    <img
      src={url}
      alt={alt}
      className={styles.mausAttachImg}
      onLoad={() => {
        loadedRef.current = true;
      }}
      onError={() => {
        if (loadedRef.current) return;
        if (attempt < 2) window.setTimeout(() => setAttempt((n) => n + 1), 700);
        else setFailed(true);
      }}
    />
  );
}

function MessageBody({ msg, botName, zh }: { msg: ChatMsg; botName: string; zh: boolean }) {
  const html = useMemo(() => renderLightMarkdown(String(msg.text || "").replace(/<attached-(?:image|file)[^>]*>/gi, "")), [msg.text]);
  const [zoomed, setZoomed] = useState<string | null>(null);

  useEffect(() => {
    if (!zoomed) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setZoomed(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [zoomed]);

  const retryInlineImage = (event: SyntheticEvent<HTMLDivElement>) => {
    const image = event.target as HTMLImageElement;
    if (image.tagName !== "IMG" || !image.classList.contains("mausMdImg")) return;
    const frame = image.parentElement;
    const attempt = Number(image.dataset.retryAttempt || 0);
    if (attempt >= 2) {
      frame?.setAttribute("data-failed", "true");
      return;
    }
    image.dataset.retryAttempt = String(attempt + 1);
    window.setTimeout(() => {
      if (!image.isConnected) return;
      frame?.removeAttribute("data-failed");
      const retryUrl = new URL(image.src);
      retryUrl.searchParams.set("retry", String(attempt + 1));
      image.src = retryUrl.href;
    }, 500 * (attempt + 1));
  };

  const clearInlineImageFailure = (event: SyntheticEvent<HTMLDivElement>) => {
    const image = event.target as HTMLImageElement;
    if (image.tagName === "IMG" && image.classList.contains("mausMdImg")) {
      image.parentElement?.removeAttribute("data-failed");
    }
  };

  const inlineImageKeys = new Set(
    Array.from(html.matchAll(/<img\b[^>]*\bsrc="([^"]*)"/gi), (match) => {
      const source = (match[1] || "").replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/&#39;/g, "'");
      try {
        const url = new URL(source, "https://financial-bot.invalid");
        return `${url.origin}${url.pathname}`;
      } catch {
        return source.split(/[?#]/, 1)[0];
      }
    }),
  );

  const openZoom = (target: EventTarget | null) => {
    const el = target as HTMLElement | null;
    if (el && el.tagName === "IMG" && el.classList.contains("mausMdImg")) {
      const img = el as HTMLImageElement;
      setZoomed(img.currentSrc || img.src);
    }
  };
  const images = (msg.attachments || []).filter((a) => {
    const source = attachmentProxyUrl(a.path);
    if (!isImageAttachment(a) || !source) return false;
    try {
      const url = new URL(source, "https://financial-bot.invalid");
      return !inlineImageKeys.has(`${url.origin}${url.pathname}`);
    } catch {
      return !inlineImageKeys.has(source.split(/[?#]/, 1)[0]);
    }
  });
  const audios = (msg.attachments || []).filter((a) => a.kind === "audio");
  const files = (msg.attachments || []).filter((a) => !isImageAttachment(a) && a.kind !== "audio");
  return (
    <>
      {msg.fromApp?.title ? <div className={styles.tag}>Sent from {msg.fromApp.title}</div> : null}
      {msg.aside ? <div className={styles.tag}>Aside</div> : null}
      {msg.pointed?.length ? (
        <div className={styles.pointedChips}>
          {msg.pointed.map((p, i) => (
            <span key={`${msg.id}-pt-${i}`} className={styles.pointedChip}>{p.label || p.text || "pointed"}</span>
          ))}
        </div>
      ) : null}
      {msg.text?.trim() ? (
        <div
          className={styles.mausBubbleMd}
          onClick={(e) => openZoom(e.target)}
          onErrorCapture={retryInlineImage}
          onLoadCapture={clearInlineImageFailure}
          onKeyDown={(e) => {
            if (e.key !== "Enter" && e.key !== " ") return;
            if ((e.target as HTMLElement | null)?.tagName !== "IMG") return;
            e.preventDefault();
            openZoom(e.target);
          }}
          dangerouslySetInnerHTML={{ __html: html }}
        />
      ) : msg.pending ? (
        <div className={styles.pendingLine}>
          <span className={styles.liveDots} aria-hidden="true"><i /><i /><i /></span>
          <span className={styles.srOnly}>{botName}</span>
        </div>
      ) : null}
      {images.length ? (
        <div className={styles.mausAttachGallery}>
          {images.map((img, idx) => {
            const src = attachmentProxyUrl(img.path);
            if (!src) return null;
            return (
              <a key={`${msg.id}-img-${idx}`} href={src} target="_blank" rel="noreferrer" className={styles.mausAttachLink}>
                <AttachImage src={src} alt={img.name || attachmentBasename(img.path) || "image"} />
              </a>
            );
          })}
        </div>
      ) : null}
      {audios.length ? (
        <div className={styles.audioList}>
          {audios.map((audio, idx) => {
            const src = attachmentProxyUrl(audio.path);
            const secs = audio.durationMs != null ? Math.round(audio.durationMs / 1000) : null;
            return (
              <div key={`${msg.id}-audio-${idx}`} className={styles.audioCard} data-open="true">
                <div className={styles.audioHead}>
                  <strong>{audio.name || (zh ? "语音条" : "Voice note")}</strong>
                  <span>{secs != null ? `${secs}s` : (zh ? "时长未知" : "duration n/a")}</span>
                </div>
                {src ? <audio controls preload="metadata" src={src} /> : <p className={styles.kindMeta}>{zh ? "无法播放" : "No audio source"}</p>}
                {audio.text ? <p className={styles.audioTranscript}>{audio.text}</p> : null}
              </div>
            );
          })}
        </div>
      ) : null}
      {files.length ? (
        <ul className={styles.fileList}>
          {files.map((file, idx) => {
            const src = attachmentProxyUrl(file.path);
            const label = file.name || attachmentBasename(file.path) || file.path;
            return src ? (
              <li key={`${msg.id}-file-${idx}`}><a href={src} target="_blank" rel="noreferrer">{label}</a></li>
            ) : (
              <li key={`${msg.id}-file-${idx}`}>{label}</li>
            );
          })}
        </ul>
      ) : null}
      {zoomed ? (
        <div
          className={styles.mausLightbox}
          role="dialog"
          aria-modal="true"
          aria-label={zh ? "放大查看图片" : "Image preview"}
          onClick={() => setZoomed(null)}
        >
          {/* eslint-disable-next-line @next/next/no-img-element -- full-size preview of an image already rendered in the bubble */}
          <img src={zoomed} alt="" />
          <button
            type="button"
            className={styles.mausLightboxClose}
            aria-label={zh ? "关闭" : "Close"}
            onClick={() => setZoomed(null)}
          >
            ×
          </button>
        </div>
      ) : null}
    </>
  );
}

const PRODUCT_NAME = "financial-bot";
const THINKING_ZH = "正在仔细思考";
const THINKING_EN = "Thinking carefully";
const GPT_MODEL_KEY = "financial-bot.gptModel.v1";
const DEEPSEEK_SELECTION = { instanceId: "openaiCompat", model: "deepseek-chat" } as const;
const GPT_SELECTION_DEFAULT = { instanceId: "codex", model: "gpt-6.1-terra", effort: "medium" } as const;

type ModelFamily = "deepseek" | "gpt";
type ModelSel = { instanceId?: string; model?: string; effort?: string };

function isGenericThinking(raw: string | null | undefined, zh: boolean) {
  const src = String(raw || "").trim();
  if (!src) return true;
  return src === THINKING_ZH || src === THINKING_EN || src === "Thinking carefully" || src === "Thinking";
}

function hasVisibleAnswer(msgs: ChatMsg[]) {
  let visible = false;
  for (const m of msgs) {
    if (m.role === "user") {
      visible = false;
      continue;
    }
    const kind = String(m.kind || "text");
    if (m.role === "assistant" && (kind === "text" || kind === "markdown") && String(m.text || "").trim()) {
      visible = true;
    }
  }
  return visible;
}

function modelFamilyOf(sel?: ModelSel | null): ModelFamily {
  const inst = String(sel?.instanceId || "").toLowerCase();
  const model = String(sel?.model || "").toLowerCase();
  if (inst === "openaicompat" || inst === "deepseek" || model.includes("deepseek")) return "deepseek";
  return "gpt";
}

function readStoredGptSelection(): { instanceId: string; model: string; effort?: string } {
  try {
    const raw = window.localStorage.getItem(GPT_MODEL_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as ModelSel;
      if (parsed?.instanceId && parsed?.model) {
        return { instanceId: String(parsed.instanceId), model: String(parsed.model), effort: parsed.effort ? String(parsed.effort) : undefined };
      }
    }
  } catch {
    /* ignore */
  }
  return { ...GPT_SELECTION_DEFAULT };
}

function storeGptSelection(sel: ModelSel | null | undefined) {
  if (!sel?.instanceId || !sel?.model) return;
  if (modelFamilyOf(sel) !== "gpt") return;
  try {
    window.localStorage.setItem(GPT_MODEL_KEY, JSON.stringify({
      instanceId: sel.instanceId,
      model: sel.model,
      ...(sel.effort ? { effort: sel.effort } : {}),
    }));
  } catch {
    /* ignore */
  }
}

function displayBotName(name?: string | null) {
  const n = (name || "").trim();
  if (!n || /mausbot/i.test(n)) return PRODUCT_NAME;
  return n;
}


function routineScheduleLabel(routine: any, zh: boolean): string {
  const schedule = routine?.schedule;
  if (typeof schedule === "string" && schedule.trim()) return schedule.trim();
  if (schedule && typeof schedule === "object") {
    const expr = String((schedule as { expression?: unknown; cron?: unknown }).expression
      || (schedule as { cron?: unknown }).cron
      || "").trim();
    const tz = String((schedule as { timeZone?: unknown; timezone?: unknown }).timeZone
      || (schedule as { timezone?: unknown }).timezone
      || "").trim();
    if (expr && tz) return `${expr}（${tz}）`;
    if (expr) return expr;
  }
  if (typeof routine?.cron === "string" && routine.cron.trim()) return routine.cron.trim();
  if (routine?.enabled === false) return zh ? "已暂停" : "Paused";
  if (typeof routine?.status === "string" && routine.status.trim()) return routine.status.trim();
  return zh ? "未排期" : "Unscheduled";
}

function isToolStopError(raw: unknown) {
  const src = String(raw || "").toLowerCase();
  return src.includes("stopped because the tool call")
    || src.includes("isn't proof the work was done")
    || src.includes("isnt proof the work was done")
    || src.includes("the bot's reply isn't proof");
}

function localizeActivityPhrase(raw: string, zh: boolean): string {
  const src = String(raw || "").trim();
  if (!src) return zh ? THINKING_ZH : THINKING_EN;
  if (isToolStopError(src)) {
    return zh ? "取数未全部成功，已给出可用结果" : "Some steps failed; the answer above is still usable";
  }
  const lower = src.toLowerCase();
  if (
    lower === "activity" ||
    lower === "working…" ||
    lower === "working..." ||
    lower === "working" ||
    lower === "thinking" ||
    src === "正在…" ||
    src === "进行中…" ||
    src.startsWith("Busy:") ||
    src.startsWith("正在：")
  ) {
    return zh ? THINKING_ZH : THINKING_EN;
  }
  if (/ifind|history_data|real_time|ths_|行情|取数|quotation|high_frequency|call_data_api|run_script/i.test(src)) {
    return zh ? "正在取数" : "Fetching market data";
  }
  if (/matplotlib|pyplot|plot|chart|走势图|出图|png|savefig/i.test(src)) {
    return zh ? "正在生成图片" : "Generating chart";
  }
  if (/read_file|read |打开文件|reading/i.test(src)) {
    return zh ? "正在阅读资料" : "Reading";
  }
  if (/write_file|write |写入|saving/i.test(src)) {
    return zh ? "正在写入" : "Writing";
  }
  if (/shell|bash|exec|命令|terminal/i.test(src)) {
    return zh ? "正在执行命令" : "Running a command";
  }
  if (/docker|local vm|start docker|cua|computer/i.test(src)) {
    return zh ? "正在准备工具" : "Preparing tools";
  }
  if (/search|web_search|浏览/i.test(src)) {
    return zh ? "正在检索" : "Searching";
  }
  if (/wait|等待/i.test(src)) {
    return zh ? "等待中" : "Waiting";
  }
  if (/approval|确认|permission/i.test(src)) {
    return zh ? "需要你确认" : "Needs your confirmation";
  }
  // Strip raw English tool dumps; never echo MCP / function names.
  if (/^[A-Za-z0-9_./:\- ]+$/.test(src) && /[A-Za-z]/.test(src)) {
    return zh ? THINKING_ZH : THINKING_EN;
  }
  if (zh && /[A-Za-z]{4,}/.test(src) && !/[\u4e00-\u9fff]/.test(src)) {
    return THINKING_ZH;
  }
  return src;
}

function activityLabel(msg: ChatMsg, zh = true) {
  const raw = String(msg.tool?.label || msg.tool?.name || msg.text || "");
  if (isToolStopError(raw) || Boolean((msg.tool as { terminalError?: boolean } | undefined)?.terminalError)) {
    return zh ? "取数未全部成功，已给出可用结果" : "Some steps failed; the answer above is still usable";
  }
  return localizeActivityPhrase(raw, zh);
}

function ActivityCapsule({ msgs, zh }: { msgs: ChatMsg[]; zh: boolean }) {
  const [open, setOpen] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const failed = msgs.some((m) => m.tool?.ok === false);
  const pending = msgs.some((m) => m.tool?.ok == null);
  const names = [...new Set(msgs.map((m) => activityLabel(m, zh)).filter(Boolean))];
  const latest = activityLabel(msgs[msgs.length - 1], zh);
  const countLabel = zh ? `${msgs.length} 步` : `${msgs.length} steps`;
  const title = names.length === 1 ? `${names[0]} · ${countLabel}` : `${latest} · ${countLabel}`;
  const tone = failed ? "bad" : pending ? "run" : "ok";
  const headline = title;
  return (
    <section className={styles.capsule} data-open={open ? "true" : "false"} data-tone={tone}>
      <button
        type="button"
        className={styles.capsuleToggle}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className={tone === "run" ? styles.liveDots : styles.activityDot} data-ok={failed ? "0" : pending ? "" : "1"} aria-hidden="true">
          {tone === "run" ? <><i /><i /><i /></> : null}
        </span>
        <span className={styles.capsuleTitle}>{headline}</span>
        <span className={styles.capsuleCount}>{msgs.length}</span>
        <span className={styles.activityChevron} aria-hidden="true">{open ? "▾" : "›"}</span>
      </button>
      {open ? (
        <div className={styles.capsuleScroll}>
          {msgs.map((msg) => {
            const ok = msg.tool?.ok;
            const mark = ok === true ? "✓" : ok === false ? "✗" : "…";
            const hasDetails = Boolean(msg.tool?.summary || msg.tool?.input || msg.tool?.output || msg.tool?.error || msg.waitLine || (msg.text && msg.text !== activityLabel(msg, zh)));
            const expanded = openId === msg.id;
            return (
              <div key={msg.id} className={styles.capsuleStep}>
                <button
                  type="button"
                  className={styles.capsuleStepBtn}
                  aria-expanded={hasDetails ? expanded : undefined}
                  disabled={!hasDetails}
                  onClick={() => setOpenId((cur) => (cur === msg.id ? null : msg.id))}
                >
                  <span className={styles.activityDot} data-ok={ok === true ? "1" : ok === false ? "0" : ""} />
                  <span className={styles.activityLabel}>{activityLabel(msg, zh)}</span>
                  {ok !== undefined && ok !== null ? <span className={styles.activityMark} data-ok={ok ? "1" : "0"}>{mark}</span> : null}
                  {msg.tool?.partial ? <span className={styles.activityPartial}>{zh ? "部分" : "partial"}</span> : null}
                  {hasDetails ? <span className={styles.activityChevron} aria-hidden="true">{expanded ? "▾" : "›"}</span> : null}
                </button>
                {expanded && hasDetails ? (
                  <div className={styles.activityDetails}>
                    <span className={styles.activityStatus} data-ok={ok === true ? "1" : ok === false ? "0" : ""}>
                      {ok === true ? (zh ? "已完成" : "Done") : ok === false ? (zh ? "失败" : "Failed") : (zh ? "状态未知" : "Unknown")}
                    </span>
                    {msg.tool?.summary ? <p>{msg.tool.summary}</p> : null}
                    {msg.waitLine ? <p>{msg.waitLine}</p> : null}
                    {msg.text && msg.text !== activityLabel(msg, zh) ? <p>{localizeActivityPhrase(msg.text, zh)}</p> : null}
                    {msg.tool?.input ? <><strong>{zh ? "输入" : "Input"}</strong><pre dir="ltr">{msg.tool.input}</pre></> : null}
                    {msg.tool?.output ? <><strong>{zh ? "输出" : "Output"}</strong><pre dir="ltr">{msg.tool.output}</pre></> : null}
                    {msg.tool?.error ? <><strong>{zh ? "错误" : "Error"}</strong><pre dir="ltr">{msg.tool.error}</pre></> : null}
                    {msg.tool?.clipped ? <span className={styles.activityPartial}>{zh ? "内容已截断" : "Clipped"}</span> : null}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : null}
    </section>
  );
}

function LiveLine({ text, zh }: { text: string; zh: boolean }) {
  const [open, setOpen] = useState(false);
  const display = localizeActivityPhrase(text, zh);
  const long = display.length > 96;
  return (
    <div className={styles.liveStatus} data-open={open ? "true" : "false"} data-tone="think" role="status" aria-live="polite">
      <span className={styles.livePlane} aria-hidden="true">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
          <path d="M3.5 11.5 20 4l-6.5 16-2.2-6.2L3.5 11.5Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round"/>
          <path d="M11.3 13.8 20 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/>
        </svg>
      </span>
      <span className={styles.liveText}>{display}</span>
      {long ? (
        <button type="button" className={styles.textButton} aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? (zh ? "收起" : "Hide") : (zh ? "展开" : "Show")}
        </button>
      ) : null}
    </div>
  );
}

function OptionsRow({ msg, botName, botId, threadId, zh }: { msg: ChatMsg; botName: string; botId: string | null; threadId: string | null; zh: boolean }) {
  const [decision, setDecision] = useState(msg.cardAnswered || (msg.cardDismissed ? "dismissed" : ""));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const inflightRef = useRef(false);
  const requestType =
    msg.cardRequestType ||
    (msg.cardOptions?.length ? "question" : msg.cardRequestId ? "permission" : undefined);
  const isOpen = Boolean(msg.cardRequestId) && !decision && !msg.cardExpired && !msg.cardStopped;
  const canRespond = Boolean(msg.cardRequestId && threadId && isOpen && !pending && !msg.cardHeld);

  useEffect(() => {
    setDecision(msg.cardAnswered || (msg.cardDismissed ? "dismissed" : ""));
  }, [msg.cardAnswered, msg.cardDismissed]);

  async function respond(
    behavior: "allow" | "deny" | "answer",
    message?: string,
    extras?: { always?: boolean; rememberCommand?: boolean; alwaysAllow?: { botId: string; key: string } },
  ) {
    if (!msg.cardRequestId || !threadId || pending || inflightRef.current) return;
    if (behavior === "answer" && !(message || "").trim()) return;
    if (msg.cardRequestId === "preview-req") {
      setDecision(behavior === "answer" ? message || "answered" : behavior);
      return;
    }
    inflightRef.current = true;
    setPending(true);
    setError("");
    try {
      if (extras?.alwaysAllow && botId) {
        const grant = await fetch(`/api/mausbot/bots/${encodeURIComponent(botId)}/always-allow`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ threadId, allowKey: extras.alwaysAllow.key }),
          cache: "no-store",
        });
        const gd = await readJson(grant);
        if (!grant.ok) throw new Error(String(gd?.detail || gd?.error || `always-allow failed (${grant.status})`));
      }
      const body: Record<string, unknown> = {
        requestId: msg.cardRequestId,
        behavior,
        ...(message ? { message } : {}),
      };
      if (extras?.always) body.always = true;
      if (extras?.rememberCommand) body.rememberCommand = true;
      const res = await fetch(`/api/mausbot/threads/${encodeURIComponent(threadId)}/respond`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        cache: "no-store",
      });
      const data = await readJson(res);
      if (!res.ok) throw new Error(String(data?.detail || data?.error || `response failed (${res.status})`));
      setDecision(behavior === "answer" ? message || "answered" : behavior);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      inflightRef.current = false;
      setPending(false);
      return;
    }
    setPending(false);
    inflightRef.current = false;
  }

  const subtypeBits: string[] = [];
  if (msg.cardComputerAsk) {
    subtypeBits.push(zh
      ? `借用电脑：${msg.cardComputerAsk.reason || msg.cardComputerAsk.want || "请求"}${msg.cardComputerAsk.device ? ` · ${msg.cardComputerAsk.device}` : ""}`
      : `Computer ask: ${msg.cardComputerAsk.reason || msg.cardComputerAsk.want || "request"}${msg.cardComputerAsk.device ? ` · ${msg.cardComputerAsk.device}` : ""}`);
  }
  if (msg.cardYourComputer) {
    subtypeBits.push(zh
      ? `本机应用${msg.cardYourComputer.app ? `：${msg.cardYourComputer.app}` : ""}${msg.cardYourComputer.site ? ` · ${msg.cardYourComputer.site}` : ""}`
      : `Your computer${msg.cardYourComputer.app ? `: ${msg.cardYourComputer.app}` : ""}${msg.cardYourComputer.site ? ` · ${msg.cardYourComputer.site}` : ""}`);
  }
  if (msg.cardSkillRequest) {
    subtypeBits.push(zh
      ? `技能：${msg.cardSkillRequest.action || "变更"} ${msg.cardSkillRequest.name || ""}`.trim()
      : `Skill: ${msg.cardSkillRequest.action || "change"} ${msg.cardSkillRequest.name || ""}`.trim());
  }
  if (msg.cardModelRequest) {
    const model = msg.cardModelRequest.selection?.model || "";
    subtypeBits.push(zh ? `模型：${model || msg.cardModelRequest.targetName || "切换"}` : `Model: ${model || msg.cardModelRequest.targetName || "switch"}`);
  }
  if (msg.cardProfileRequest) {
    subtypeBits.push(zh ? `资料变更 · ${msg.cardProfileRequest.targetName || ""}` : `Profile · ${msg.cardProfileRequest.targetName || ""}`);
  }
  if (msg.cardRoutineRequest) {
    subtypeBits.push(zh
      ? `例行：${msg.cardRoutineRequest.action || ""} ${msg.cardRoutineRequest.name || ""}`.trim()
      : `Routine: ${msg.cardRoutineRequest.action || ""} ${msg.cardRoutineRequest.name || ""}`.trim());
  }
  if (msg.cardOutsideWrite) {
    subtypeBits.push(zh
      ? `外部写入：${msg.cardOutsideWrite.kind || msg.cardOutsideWrite.action || "请求"}`
      : `Outside write: ${msg.cardOutsideWrite.kind || msg.cardOutsideWrite.action || "request"}`);
  }
  if (msg.cardAccessRequest) {
    subtypeBits.push(zh ? `访问请求 · ${msg.cardAccessRequest.botName || msg.cardAccessRequest.name || ""}` : `Access · ${msg.cardAccessRequest.botName || msg.cardAccessRequest.name || ""}`);
  }
  if (msg.cardMcpRequest) {
    subtypeBits.push(zh ? `MCP：${msg.cardMcpRequest.action || ""} ${msg.cardMcpRequest.name || ""}`.trim() : `MCP: ${msg.cardMcpRequest.action || ""} ${msg.cardMcpRequest.name || ""}`.trim());
  }
  if (msg.cardLendingLift) {
    subtypeBits.push(zh ? "借用权限提升" : "Lending lift");
  }
  if (msg.cardQuestionRequest) {
    subtypeBits.push(zh ? "结构化提问" : "Structured question");
  }
  if (msg.cardCommandAllowlist?.command) {
    subtypeBits.push(zh ? `命令：${msg.cardCommandAllowlist.command}` : `Command: ${msg.cardCommandAllowlist.command}`);
  }
  if (msg.cardAllowKey) {
    subtypeBits.push(zh ? `授权键：${msg.cardAllowKey}` : `Allow key: ${msg.cardAllowKey}`);
  }
  if (msg.cardApprovalScope) {
    subtypeBits.push(zh ? `范围：${msg.cardApprovalScope}` : `Scope: ${msg.cardApprovalScope}`);
  }

  const canAlways = Boolean(isOpen && !msg.cardHeld && requestType === "permission" && msg.cardAllowKey && botId);
  const canRememberCmd = Boolean(isOpen && !msg.cardHeld && requestType === "permission" && !msg.cardAllowKey && msg.cardCommandAllowlist?.command);
  const canSession = Boolean(isOpen && !msg.cardHeld && requestType === "permission" && !msg.cardAllowKey && msg.cardAllowSession);

  const settledLabel = decision === "allow"
    ? (zh ? "已允许" : "Allowed")
    : decision === "deny"
      ? (zh ? "已拒绝" : "Denied")
      : decision === "dismissed"
        ? (zh ? "已取消" : "Dismissed")
        : decision
          ? (zh ? `已回复：${decision}` : `Answered: ${decision}`)
          : "";
  const kicker = requestType === "permission"
    ? (zh ? "需要确认" : "Approval")
    : requestType === "question"
      ? (zh ? "需要回答" : "Question")
      : botName;
  return (
    <div className={styles.card} data-open={isOpen ? "true" : "false"} role="group" aria-label={zh ? "待确认请求" : "Pending request"}>
      <p className={styles.cardKicker}>{kicker}</p>
      <strong className={styles.cardTitle}>{msg.cardTitle || botName}</strong>
      {msg.text?.trim() ? <p>{msg.text}</p> : null}
      {msg.cardTool ? <p className={styles.approvalTool}>{zh ? "请求工具：" : "Requested tool: "}{msg.cardTool}</p> : null}
      {msg.cardSubtitle ? <p className={styles.approvalSubtitle}>{msg.cardSubtitle}</p> : null}
      {subtypeBits.map((bit, i) => (
        <p key={`${msg.id}-sub-${i}`} className={styles.approvalSubtype}>{bit}</p>
      ))}
      {msg.cardHeld ? <p className={styles.approvalHeld}>{msg.cardHeld}{msg.cardHeldCode ? ` (${msg.cardHeldCode})` : ""}</p> : null}
      {msg.cardAutoApplied ? <p className={styles.approvalStatus} role="status">{zh ? "已自动应用" : "Auto-applied"}{msg.cardUndone ? (zh ? " · 已撤销" : " · Undone") : ""}</p> : null}
      {settledLabel ? <p className={styles.approvalStatus} role="status">{settledLabel}</p> : null}
      {msg.cardExpired ? <p className={styles.approvalHeld} role="status">{zh ? "此请求已过期，不能再处理" : "This request expired and can no longer be answered"}</p> : null}
      {msg.cardStopped ? <p className={styles.approvalHeld} role="status">{zh ? "相关回合已停止" : "The related turn was stopped"}</p> : null}
      {isOpen && !msg.cardHeld && requestType === "permission" ? (
        <div className={styles.approvalActions} data-kind="permission" role="group" aria-label={zh ? "审批操作" : "Approval actions"}>
          <button type="button" disabled={!canRespond} aria-busy={pending || undefined} onClick={() => void respond("allow")}>{pending ? (zh ? "提交中…" : "Sending…") : (zh ? "允许一次" : "Allow once")}</button>
          <button type="button" disabled={!canRespond} aria-busy={pending || undefined} onClick={() => void respond("deny")}>{zh ? "拒绝" : "Deny"}</button>
          {canAlways ? (
            <button
              type="button"
              disabled={!canRespond}
              title={zh ? `以后都允许 ${msg.cardAllowKey}` : `Always allow ${msg.cardAllowKey}`}
              onClick={() => void respond("allow", undefined, { alwaysAllow: { botId: botId!, key: msg.cardAllowKey! } })}
            >{zh ? "始终允许此类操作" : "Always allow this action"}</button>
          ) : null}
          {canRememberCmd ? (
            <button
              type="button"
              disabled={!canRespond}
              title={msg.cardCommandAllowlist?.cwd || ""}
              onClick={() => void respond("allow", undefined, { rememberCommand: true })}
            >{zh ? "始终允许此命令" : "Always allow command"}</button>
          ) : null}
          {canSession ? (
            <button
              type="button"
              disabled={!canRespond}
              onClick={() => void respond("allow", undefined, { always: true })}
            >{zh ? "本会话始终允许" : "Always this session"}</button>
          ) : null}
        </div>
      ) : null}
      {isOpen && !msg.cardHeld && requestType === "question" && msg.cardOptions?.length ? (
        <div className={styles.approvalActions} data-kind="question" role="group" aria-label={zh ? "问题选项" : "Question options"}>
          {msg.cardOptions.map((opt, i) => (
            <button
              key={`${msg.id}-opt-${i}`}
              type="button"
              disabled={!canRespond}
              aria-busy={pending || undefined}
              onClick={() => void respond("answer", opt)}
            >{opt}</button>
          ))}
        </div>
      ) : null}
      {error ? <p className={styles.approvalError} role="alert">{error}</p> : null}
      {msg.cardOptions?.length && !(isOpen && requestType === "question") ? (
        <ul className={styles.optionsList}>
          {msg.cardOptions.map((opt, i) => (
            <li key={`${msg.id}-opt-${i}`}>{opt}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}



function statusTone(status?: string): string {
  const s = (status || "").toLowerCase();
  if (["completed", "connected", "done", "fired"].includes(s)) return "ok";
  if (["failed", "cancelled", "blocked", "stopped", "missed"].includes(s)) return "bad";
  if (["running", "working", "authorizing", "queued", "waiting", "needs-input", "paused"].includes(s)) return "busy";
  return "neutral";
}

function KindCardShell({
  title,
  icon,
  children,
  tone,
  open = true,
}: {
  title: string;
  icon?: string;
  children: any;
  tone?: string;
  open?: boolean;
}) {
  return (
    <div className={styles.kindCard} data-tone={tone || "neutral"} data-open={open ? "true" : "false"} role="group" aria-label={title}>
      <div className={styles.kindHead}>
        {icon ? <span className={styles.kindIcon} aria-hidden="true">{icon}</span> : null}
        <strong className={styles.kindTitle}>{title}</strong>
      </div>
      {children}
    </div>
  );
}

function SignInRequestRow({
  msg,
  botId,
  threadId,
  zh,
}: {
  msg: ChatMsg;
  botId: string | null;
  threadId: string | null;
  zh: boolean;
}) {
  const signIn = msg.signIn;
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(signIn?.error || "");
  const [doneLocal, setDoneLocal] = useState(Boolean(signIn?.done));
  const [cancelledLocal, setCancelledLocal] = useState(Boolean(signIn?.cancelled));
  if (!signIn) return null;
  const settled = doneLocal || cancelledLocal || Boolean(signIn.superseded) || Boolean(signIn.stopped) || Boolean(signIn.answeredInChat);
  const isOpen = !settled;
  async function act(action: "done" | "cancel") {
    if (!botId || !threadId || pending) return;
    setPending(true);
    setError("");
    try {
      const res = await fetch(`/api/mausbot/bots/${encodeURIComponent(botId)}/sign-in-cards/${encodeURIComponent(msg.id)}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ threadId }),
        cache: "no-store",
      });
      const data = await readJson(res);
      if (!res.ok) throw new Error(String(data?.detail || data?.error || `request failed (${res.status})`));
      if (action === "done") setDoneLocal(true);
      else setCancelledLocal(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(false);
    }
  }
  const title = signIn.site || (zh ? "网站登录" : "Sign in");
  const status = doneLocal
    ? (zh ? "已完成登录" : "Signed in")
    : cancelledLocal
      ? (zh ? "已取消" : "Cancelled")
      : signIn.superseded
        ? (zh ? "已被新请求替换" : "Replaced")
        : signIn.stopped
          ? (zh ? "回合已停止" : "Turn stopped")
          : "";
  return (
    <KindCardShell title={title} icon="🌐" tone={settled ? (doneLocal ? "ok" : "neutral") : "busy"} open={isOpen}>
      <p className={styles.kindDesc}>
        {signIn.reason || (zh ? "请在页面中自行登录；机器人看不到密码。" : "Sign in on the site yourself — the bot never sees the password.")}
      </p>
      {signIn.place ? <p className={styles.kindMeta}>{zh ? `位置：${signIn.place}` : `Place: ${signIn.place}`}</p> : null}
      {signIn.url ? (
        <p className={styles.kindMeta}>
          <a href={signIn.url} target="_blank" rel="noreferrer">{signIn.url}</a>
        </p>
      ) : null}
      {status ? <p className={styles.approvalStatus} role="status">{status}</p> : null}
      {isOpen ? (
        <div className={styles.approvalActions}>
          <button type="button" disabled={!botId || !threadId || pending} onClick={() => void act("done")}>
            {pending ? (zh ? "提交中…" : "Sending…") : (zh ? "已登录完成" : "Done")}
          </button>
          <button type="button" disabled={!botId || !threadId || pending} onClick={() => void act("cancel")}>
            {zh ? "取消" : "Cancel"}
          </button>
        </div>
      ) : null}
      {error ? <p className={styles.approvalError} role="alert">{error}</p> : null}
    </KindCardShell>
  );
}

function ConnectorRequestRow({
  msg,
  botId,
  threadId,
  zh,
}: {
  msg: ChatMsg;
  botId: string | null;
  threadId: string | null;
  zh: boolean;
}) {
  const connector = msg.connector;
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(connector?.error || "");
  const [status, setStatus] = useState(connector?.status || "required");
  const [dismissed, setDismissed] = useState(Boolean(connector?.dismissed));
  const settled = dismissed || Boolean(connector?.superseded) || Boolean(connector?.stopped) || status === "connected";
  const isOpen = !settled;
  async function post(action: "authorize" | "dismiss" | "resume") {
    if (!botId || !threadId || pending) return;
    setPending(true);
    setError("");
    try {
      const res = await fetch(`/api/mausbot/bots/${encodeURIComponent(botId)}/connector-cards/${encodeURIComponent(msg.id)}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ threadId }),
        cache: "no-store",
      });
      const data = await readJson(res);
      if (!res.ok) throw new Error(String(data?.detail || data?.error || `request failed (${res.status})`));
      if (action === "authorize") {
        const url = data?.url || data?.authUrl || data?.redirectUrl;
        if (typeof url === "string" && url) window.open(url, "_blank", "noopener,noreferrer");
        setStatus("authorizing");
      } else if (action === "dismiss") {
        setDismissed(true);
      } else if (action === "resume") {
        setStatus("connected");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(false);
    }
  }
  async function refreshStatus() {
    if (!botId || !threadId) return;
    try {
      const res = await fetch(`/api/mausbot/bots/${encodeURIComponent(botId)}/connector-cards/${encodeURIComponent(msg.id)}/status?threadId=${encodeURIComponent(threadId)}`, { cache: "no-store" });
      const data = await readJson(res);
      if (res.ok && data?.connected) setStatus("connected");
      else if (res.ok && data?.pending) setStatus("authorizing");
      else if (res.ok && data?.status) setStatus(String(data.status));
    } catch {
      /* ignore poll errors */
    }
  }
  useEffect(() => {
    if (status !== "authorizing" || !isOpen) return;
    const t = window.setInterval(() => { void refreshStatus(); }, 4000);
    return () => window.clearInterval(t);
  }, [status, isOpen, botId, threadId, msg.id]);
  if (!connector) return null;
  return (
    <KindCardShell title={connector.label || connector.slug || (zh ? "连接器授权" : "Connect app")} icon="🔌" tone={statusTone(status)} open={isOpen}>
      <p className={styles.kindDesc}>{connector.description || (zh ? "安全连接后机器人才能使用该应用。" : "Connect securely so the bot can use this app.")}</p>
      <p className={styles.kindMeta}>{zh ? `状态：${status}` : `Status: ${status}`}{connector.source ? ` · ${connector.source}` : ""}</p>
      {isOpen ? (
        <div className={styles.approvalActions}>
          <button type="button" disabled={!botId || !threadId || pending} onClick={() => void post("authorize")}>
            {pending ? (zh ? "打开中…" : "Opening…") : (zh ? "安全连接" : "Connect securely")}
          </button>
          {status === "authorizing" || status === "connected" ? (
            <button type="button" disabled={!botId || !threadId || pending} onClick={() => void post("resume")}>
              {zh ? "继续" : "Resume"}
            </button>
          ) : null}
          <button type="button" disabled={!botId || !threadId || pending} onClick={() => void post("dismiss")}>
            {zh ? "拒绝" : "Dismiss"}
          </button>
        </div>
      ) : null}
      {error ? <p className={styles.approvalError} role="alert">{error}</p> : null}
    </KindCardShell>
  );
}

function widgetNodeLabel(node: any): string {
  if (!node || typeof node !== "object") return "";
  return String(node.label || node.value || node.title || node.caption || node.type || "");
}

function WidgetTree({
  node,
  depth = 0,
  onAction,
  disabled,
}: {
  node: any;
  depth?: number;
  onAction?: (ref: string) => void;
  disabled?: boolean;
}) {
  if (!node || typeof node !== "object" || depth > 6) return null;
  const kids = Array.isArray(node.children) ? node.children : [];
  const label = widgetNodeLabel(node);
  const ref = node.id != null ? String(node.id) : node.ref != null ? String(node.ref) : "";
  if (node.type === "Text" || node.type === "Caption" || node.type === "Badge" || node.type === "Markdown") {
    return <div className={styles.widgetLeaf} data-type={node.type}>{label}</div>;
  }
  if (node.type === "Link" && node.href) {
    return <a className={styles.widgetLeaf} data-type="Link" href={String(node.href)} target="_blank" rel="noreferrer">{label || String(node.href)}</a>;
  }
  if (node.type === "Button" || node.type === "ListViewItem" || node.type === "Toggle" || node.type === "Chip") {
    if (onAction && ref) {
      return (
        <button
          type="button"
          className={styles.widgetAction}
          data-type={node.type}
          disabled={disabled}
          onClick={() => onAction(ref)}
        >{label || ref}</button>
      );
    }
    return <div className={styles.widgetLeaf} data-type={node.type}>• {label || node.id}</div>;
  }
  if (node.type === "Input" || node.type === "TextField") {
    return <div className={styles.widgetLeaf} data-type={node.type}>{zhLabelPlaceholder(label)}</div>;
  }
  return (
    <div className={styles.widgetNode} data-type={node.type || "node"}>
      {label && node.type !== "Card" && node.type !== "Col" && node.type !== "Row" ? <div className={styles.widgetLeaf}>{label}</div> : null}
      {kids.map((child: any, i: number) => (
        <WidgetTree key={child?.id || `${depth}-${i}`} node={child} depth={depth + 1} onAction={onAction} disabled={disabled} />
      ))}
    </div>
  );
}

function zhLabelPlaceholder(label: string) {
  return label || "…";
}

function WidgetRequestRow({
  msg,
  botId,
  threadId,
  zh,
}: {
  msg: ChatMsg;
  botId: string | null;
  threadId: string | null;
  zh: boolean;
}) {
  const widget = msg.widget;
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const settled = widget?.settled;
  async function press(ref: string) {
    if (!botId || !threadId || pending || settled) return;
    setPending(true);
    setError("");
    try {
      const sendId = `web-w-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
      const res = await fetch("/api/mausbot/message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          botId,
          threadId,
          sendId,
          widgetAction: { messageId: msg.id, ref, rev: widget?.rev },
        }),
        cache: "no-store",
      });
      const data = await readJson(res);
      if (!res.ok) throw new Error(String(data?.detail || data?.error || `request failed (${res.status})`));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(false);
    }
  }
  const title = msg.text?.trim() || (zh ? "机器人卡片" : "Bot widget");
  return (
    <KindCardShell title={title.slice(0, 80)} icon="🧩" tone={settled ? "ok" : "neutral"} open={!settled}>
      {widget?.root ? <div className={styles.widgetTree}><WidgetTree node={widget.root} onAction={press} disabled={pending || Boolean(settled)} /></div> : null}
      {settled ? (
        <p className={styles.approvalStatus} role="status">{zh ? `已回答：${settled.label || settled.ref}` : `Answered: ${settled.label || settled.ref}`}</p>
      ) : (
        <div className={styles.approvalActions}>
          {widget?.confirm?.label ? (
            <button type="button" disabled={!botId || !threadId || pending} onClick={() => void press("confirm")}>{widget.confirm.label}</button>
          ) : null}
          {widget?.cancel?.label ? (
            <button type="button" disabled={!botId || !threadId || pending} onClick={() => void press("cancel")}>{widget.cancel.label}</button>
          ) : null}
          {!widget?.confirm && !widget?.cancel ? (
            <button type="button" disabled={!botId || !threadId || pending} onClick={() => void press("confirm")}>
              {zh ? "确认" : "Confirm"}
            </button>
          ) : null}
        </div>
      )}
      {error ? <p className={styles.approvalError} role="alert">{error}</p> : null}
    </KindCardShell>
  );
}

function AppRequestRow({
  msg,
  botId,
  threadId,
  zh,
}: {
  msg: ChatMsg;
  botId: string | null;
  threadId: string | null;
  zh: boolean;
}) {
  const app = msg.app;
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [previewOpen, setPreviewOpen] = useState(false);
  if (!app) return null;
  const previewUrl = threadId ? `/api/mausbot/threads/${encodeURIComponent(threadId)}/messages/${encodeURIComponent(msg.id)}/app${app.rev != null ? `?rev=${app.rev}` : ""}` : null;
  async function sendFromApp() {
    if (!botId || !threadId || !draft.trim() || pending) return;
    setPending(true);
    setError("");
    try {
      const sendId = `web-a-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
      const res = await fetch("/api/mausbot/message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          botId,
          threadId,
          sendId,
          text: draft.trim(),
          appMessage: { messageId: msg.id, text: draft.trim() },
        }),
        cache: "no-store",
      });
      const data = await readJson(res);
      if (!res.ok) throw new Error(String(data?.detail || data?.error || `request failed (${res.status})`));
      setDraft("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(false);
    }
  }
  return (
    <KindCardShell title={app.title || (zh ? "应用页面" : "App page")} icon="📄" tone={app.blocked ? "bad" : "neutral"}>
      {msg.text ? <p className={styles.kindDesc}>{msg.text}</p> : null}
      <p className={styles.kindMeta}>
        {[app.source, app.blocked ? `blocked:${app.blocked}` : null, app.bytes != null ? `${app.bytes}B` : null].filter(Boolean).join(" · ")}
      </p>
      {previewUrl && !app.blocked ? (
        <div className={styles.approvalActions}>
          <a className={styles.kindLinkBtn} href={previewUrl} target="_blank" rel="noreferrer">{zh ? "打开预览" : "Open preview"}</a>
          <button type="button" onClick={() => setPreviewOpen((v) => !v)}>{previewOpen ? (zh ? "收起" : "Hide") : (zh ? "内嵌预览" : "Inline preview")}</button>
        </div>
      ) : null}
      {previewOpen && previewUrl ? (
        <>
          <iframe className={styles.appFrame} title={app.title || "app"} src={previewUrl} sandbox="allow-scripts allow-forms allow-same-origin" />
          <div className={styles.appRpcBar}>
            <button
              type="button"
              disabled={!botId || !threadId || pending}
              onClick={() => void (async () => {
                if (!botId || !threadId || pending) return;
                setPending(true); setError("");
                try {
                  const res = await fetch(`/api/mausbot/bots/${encodeURIComponent(botId)}/apps/${encodeURIComponent(msg.id)}/rpc`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ threadId, method: "ping", params: {} }),
                    cache: "no-store",
                  });
                  const data = await readJson(res);
                  if (!res.ok) throw new Error(String(data?.detail || data?.error || `rpc failed (${res.status})`));
                  setError(zh ? `RPC ping 成功：${JSON.stringify(data).slice(0, 160)}` : `RPC ping ok: ${JSON.stringify(data).slice(0, 160)}`);
                } catch (e) {
                  setError(e instanceof Error ? e.message : String(e));
                } finally {
                  setPending(false);
                }
              })()}
            >{zh ? "RPC ping" : "RPC ping"}</button>
          </div>
        </>
      ) : null}
      <div className={styles.appSendRow}>
        <input
          className={styles.secureInput}
          value={draft}
          placeholder={zh ? "从应用发回一句话…" : "Send a line from this app…"}
          onChange={(ev) => setDraft(ev.target.value)}
          disabled={!botId || !threadId || pending}
        />
        <button type="button" disabled={!draft.trim() || !botId || !threadId || pending} onClick={() => void sendFromApp()}>
          {pending ? (zh ? "发送中…" : "Sending…") : (zh ? "回传" : "Send")}
        </button>
      </div>
      {error ? <p className={styles.approvalError} role="alert">{error}</p> : null}
    </KindCardShell>
  );
}


function screenImageSrc(msg: ChatMsg, threadId: string | null): string | undefined {
  if (msg.png) {
    return msg.png.startsWith("data:") ? msg.png : `data:${msg.mime || "image/png"};base64,${msg.png}`;
  }
  if ((msg.kind === "screen" || msg.hasImage) && threadId && msg.id) {
    return `/api/mausbot/threads/${encodeURIComponent(threadId)}/messages/${encodeURIComponent(msg.id)}/image`;
  }
  return undefined;
}

function ScreenFrameRow({ msg, threadId, zh }: { msg: ChatMsg; threadId: string | null; zh: boolean }) {
  const src = screenImageSrc(msg, threadId) || null;
  return (
    <KindCardShell title={zh ? "屏幕帧" : "Screen frame"} icon="🖥️" tone="neutral">
      {src ? <img className={styles.screenFrame} src={src} alt={zh ? "屏幕截图" : "Screen capture"} /> : null}
      {!src && msg.pngOmitted ? <p className={styles.kindMeta}>{zh ? "帧过大，已省略内联图（可看右栏/附件）" : "Frame too large for inline preview"}</p> : null}
      {msg.text ? <p className={styles.kindDesc}>{msg.text}</p> : null}
    </KindCardShell>
  );
}

function RoutineRunRow({ msg, zh }: { msg: ChatMsg; zh: boolean }) {
  const run = msg.routineRun;
  if (!run) return null;
  return (
    <KindCardShell title={run.routineName || (zh ? "例行运行" : "Routine run")} icon="⏱️" tone={statusTone(run.status)}>
      <p className={styles.kindMeta}>{zh ? `状态：${run.status || "?"}` : `Status: ${run.status || "?"}`}{run.goalStatus ? ` · goal:${run.goalStatus}` : ""}</p>
      {run.summary ? <p className={styles.kindDesc}>{run.summary}</p> : null}
      {run.error ? <p className={styles.approvalError} role="alert">{run.error}</p> : null}
    </KindCardShell>
  );
}

function GoalRunRow({ msg, zh }: { msg: ChatMsg; zh: boolean }) {
  const run = msg.goalRun;
  if (!run) return null;
  return (
    <KindCardShell title={zh ? "目标运行" : "Goal run"} icon="🎯" tone={statusTone(run.status)}>
      <p className={styles.kindDesc}>{run.goal || msg.text}</p>
      <p className={styles.kindMeta}>
        {(zh ? `状态：${run.status || "?"}` : `Status: ${run.status || "?"}`)}
        {run.coordinatorName ? ` · ${run.coordinatorName}` : ""}
        {run.turnCount != null && run.maxTurns != null ? ` · ${run.turnCount}/${run.maxTurns}` : ""}
      </p>
      {run.detail ? <p className={styles.kindDesc}>{run.detail}</p> : null}
    </KindCardShell>
  );
}

function CompactionRow({ msg, zh }: { msg: ChatMsg; zh: boolean }) {
  const c = msg.compaction;
  return (
    <details className={styles.compactionRow} open>
      <summary>{zh ? "已折叠旧上下文" : "Earlier context folded"}{c?.tokensBefore ? ` · ~${c.tokensBefore} tok` : ""}{c?.by ? ` · ${c.by}` : ""}</summary>
      {c?.summary ? <pre className={styles.compactionBody}>{c.summary}</pre> : (msg.text ? <pre className={styles.compactionBody}>{msg.text}</pre> : null)}
    </details>
  );
}

function CallRow({ msg, zh }: { msg: ChatMsg; zh: boolean }) {
  const call = msg.call;
  const secs = call?.seconds != null ? call.seconds : null;
  return (
    <div className={styles.callRow} role="status">
      <span aria-hidden="true">📞</span>
      <span>
        {call?.title || (zh ? "Live 通话" : "Live call")}
        {secs != null ? ` · ${secs}s` : ""}
        {call?.endReason ? ` · ${call.endReason}` : ""}
        {call?.client ? ` · ${call.client}` : ""}
      </span>
    </div>
  );
}

function DigestRow({ msg, zh }: { msg: ChatMsg; zh: boolean }) {
  const d = msg.digest;
  const duration = d?.durationMs != null ? ` · ${Math.round(d.durationMs / 1000)}s` : "";
  // Do not render digest.reply or [digest] text — that duplicates the answer bubble.
  return (
    <details className={styles.digestRow}>
      <summary>
        {zh ? "回合结束" : "Turn ended"}
        {duration}
      </summary>
      <p className={styles.kindMeta}>{zh ? "详细步骤已折叠，答案见上方。" : "Steps collapsed; the answer is in the bubble above."}</p>
    </details>
  );
}

function WaitCardExtra({
  msg,
  botId,
  threadId,
  zh,
  onFollowUp,
}: {
  msg: ChatMsg;
  botId: string | null;
  threadId: string | null;
  zh: boolean;
  onFollowUp?: (text: string) => void;
}) {
  const wr = msg.waitRequest;
  const wait = msg.wait;
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [cancelledLocal, setCancelledLocal] = useState(false);
  if (!wr && !wait) return null;
  // OMB marks active waits as answered=allow + dismissed/autoApplied; only settled/undone ends them.
  const settled = cancelledLocal
    || Boolean(wr?.settled)
    || Boolean(wait?.status)
    || Boolean((msg as any).cardUndone)
    || msg.cardAnswered === "deny";
  const canCancel = Boolean(threadId && msg.cardRequestId && wr && !settled && !pending);

  async function cancelWait() {
    if (!threadId || !msg.cardRequestId || pending) return;
    setPending(true);
    setError("");
    try {
      const res = await fetch(`/api/mausbot/threads/${encodeURIComponent(threadId)}/undo`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ requestId: msg.cardRequestId }),
        cache: "no-store",
      });
      const data = await readJson(res);
      // 409 = already settled; treat as done.
      if (!res.ok && res.status !== 409) {
        throw new Error(String(data?.detail || data?.error || `cancel failed (${res.status})`));
      }
      setCancelledLocal(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className={styles.waitExtra}>
      {wr ? (
        <p className={styles.kindMeta}>
          {zh ? "等待卡" : "Wait"}: {wr.label || wr.waitId || "?"}
          {settled ? ` · ${wr.settled || (cancelledLocal ? "cancelled" : wait?.status || "done")}` : (zh ? " · 进行中" : " · waiting")}
        </p>
      ) : null}
      {wr?.followUp ? (
        <p className={styles.waitFollowUp}>
          <strong>{zh ? "跟进：" : "Follow-up: "}</strong>{wr.followUp}
        </p>
      ) : null}
      {wait ? <p className={styles.kindMeta}>{zh ? `等待结束：${wait.status}` : `Wait ended: ${wait.status}`}</p> : null}
      {!settled ? (
        <div className={styles.waitActions}>
          <button type="button" disabled={!canCancel} onClick={() => void cancelWait()}>
            {pending ? (zh ? "取消中…" : "Cancelling…") : (zh ? "解除等待" : "Cancel wait")}
          </button>
          {wr?.followUp && onFollowUp ? (
            <button type="button" disabled={pending} onClick={() => onFollowUp(wr.followUp!)}>
              {zh ? "现在跟进" : "Follow up now"}
            </button>
          ) : null}
        </div>
      ) : null}
      {error ? <p className={styles.approvalError} role="alert">{error}</p> : null}
    </div>
  );
}

function SecretRequestRow({
  msg,
  botId,
  botName,
  threadId,
  zh,
}: {
  msg: ChatMsg;
  botId: string | null;
  botName: string;
  threadId: string | null;
  zh: boolean;
}) {
  const secret = msg.secret;
  const [value, setValue] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(secret?.error || "");
  const [providedLocal, setProvidedLocal] = useState(Boolean(secret?.provided));
  const [dismissedLocal, setDismissedLocal] = useState(Boolean(secret?.dismissed));
  const inflightRef = useRef(false);

  useEffect(() => {
    setProvidedLocal(Boolean(secret?.provided));
    setDismissedLocal(Boolean(secret?.dismissed));
    if (secret?.error) setError(secret.error);
  }, [secret?.provided, secret?.dismissed, secret?.error, msg.id]);

  if (!secret) return null;
  if (dismissedLocal && !error) return null;

  const label = secret.label || (zh ? "安全授权" : "Secure authorization");
  const placeholder = secret.placeholder || (zh ? "password" : "password");
  const description = [secret.reason, secret.description].filter(Boolean).join(" ")
    || (zh ? "请在安全卡片中提交，不要发到聊天。" : "Submit it in this secure card — never in chat.");
  const delivery = secret.delivery || "file";
  const footer = delivery === "ref"
    ? (zh
      ? "会安全保管，只供自带工具使用一次。机器人看不到它，也不会出现在聊天中。"
      : "Kept securely for a one-time tool use. The bot never sees it, and it is not added to chat.")
    : (zh
      ? "保存在一个私密文件中，你的机器人的命令可在 24 小时内读取，之后删除。它不会出现在聊天中。"
      : "Saved to a private file your bot's commands can read for 24 hours, then deleted. It is never added to chat.");
  const settled = providedLocal || Boolean(secret.superseded) || Boolean(secret.stopped);
  const isOpen = !settled && !dismissedLocal && !secret.superseded && !secret.stopped;
  const canSubmit = Boolean(botId && threadId && isOpen && !pending);

  async function postAction(action: "save" | "dismiss" | "resume" | "provided", body: Record<string, unknown>) {
    if (!botId || !threadId || pending || inflightRef.current) return;
    inflightRef.current = true;
    setPending(true);
    setError("");
    try {
      const res = await fetch(
        `/api/mausbot/bots/${encodeURIComponent(botId)}/secret-cards/${encodeURIComponent(msg.id)}/${action}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          cache: "no-store",
        },
      );
      const data = await readJson(res);
      if (!res.ok) throw new Error(String(data?.detail || data?.error || `request failed (${res.status})`));
      if (action === "save" || action === "provided") {
        setProvidedLocal(true);
        setValue("");
      } else if (action === "dismiss") {
        setDismissedLocal(true);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(false);
      inflightRef.current = false;
    }
  }

  const statusLabel = secret.superseded
    ? (zh ? "已被更新的授权请求替换" : "Replaced by a newer request")
    : providedLocal
      ? secret.stopped
        ? (zh ? "已安全保存" : "Saved securely")
        : secret.resumed
          ? (zh ? "已安全保存，机器人正在继续" : "Saved securely. Bot is continuing.")
          : (zh ? "已安全保存，机器人将在当前回合结束后继续" : "Saved securely. Bot will continue when the turn settles.")
      : dismissedLocal
        ? (zh ? "已取消授权" : "Authorization declined")
        : "";

  return (
    <div className={styles.secureCard} data-open={isOpen ? "true" : "false"} role="group" aria-label={zh ? "安全授权卡" : "Secure authorization card"}>
      <div className={styles.secureHead}>
        <div className={styles.secureTitleWrap}>
          <span className={styles.secureKeyIcon} aria-hidden="true">🔑</span>
          <strong className={styles.secureTitle}>{label}</strong>
        </div>
        {isOpen ? (
          <button
            type="button"
            className={styles.secureClose}
            aria-label={zh ? "关闭" : "Dismiss"}
            disabled={!canSubmit}
            onClick={() => void postAction("dismiss", { threadId })}
          >
            ×
          </button>
        ) : null}
      </div>
      <p className={styles.secureDesc}>{description}</p>
      {statusLabel ? <p className={styles.approvalStatus} role="status">{statusLabel}</p> : null}
      {isOpen ? (
        <div className={styles.secureForm} role="form">
          <input
            type="password"
            autoComplete="new-password"
            spellCheck={false}
            dir="ltr"
            className={styles.secureInput}
            value={value}
            placeholder={placeholder}
            disabled={!canSubmit}
            aria-label={label}
            onChange={(ev) => setValue(ev.target.value)}
            onKeyDown={(ev) => {
              if (ev.key !== "Enter") return;
              ev.preventDefault();
              if (!value.trim()) return;
              void postAction("save", { threadId, value: value.trim() });
            }}
          />
          <button
            type="button"
            className={styles.secureSave}
            disabled={!canSubmit || !value.trim()}
            aria-busy={pending || undefined}
            onClick={() => {
              if (!value.trim()) return;
              void postAction("save", { threadId, value: value.trim() });
            }}
          >
            <span aria-hidden="true">🔒</span>
            {pending ? (zh ? "保存中…" : "Saving…") : (zh ? "安全保存" : "Save securely")}
          </button>
        </div>
      ) : null}
      {isOpen && secret.target && secret.target !== "custom" ? (
        <div className={styles.approvalActions}>
          <button
            type="button"
            disabled={!canSubmit}
            onClick={() => void postAction("provided", { threadId })}
          >
            {zh ? "已在设置中配置，继续" : "Already configured — continue"}
          </button>
        </div>
      ) : null}
      {providedLocal && !secret.resumed && !secret.stopped && botId && threadId ? (
        <div className={styles.approvalActions}>
          <button type="button" disabled={pending} onClick={() => void postAction("resume", { threadId })}>
            {zh ? "继续回合" : "Continue turn"}
          </button>
        </div>
      ) : null}
      <p className={styles.secureFoot}>
        <span aria-hidden="true">🔒</span>
        <span>{footer}</span>
      </p>
      {error ? <p className={styles.approvalError} role="alert">{error}</p> : null}
      {!botName ? null : <span className={styles.srOnly}>{botName}</span>}
    </div>
  );
}

export function MausbotShell() {
  const zh = useZh();
  const [bots, setBots] = useState<OmbBot[]>([]);
  const [botId, setBotId] = useState<string | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [showAllHistory, setShowAllHistory] = useState(false);
  const [hasMoreHistory, setHasMoreHistory] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [liveStatus, setLiveStatus] = useState<string | null>(null);
  const [turnEndNotice, setTurnEndNotice] = useState<string | null>(null);
  const [queued, setQueued] = useState<QueuedPrompt[]>([]);
  const [loadingBots, setLoadingBots] = useState(true);
  const [botsLoadError, setBotsLoadError] = useState("");
  const [loadingMsgs, setLoadingMsgs] = useState(false);
  const [bootstrapping, setBootstrapping] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rail, setRail] = useState<RailTab>("screen");
  const [workLoc, setWorkLoc] = useState<WorkLocation>("auto");
  const [memoryText, setMemoryText] = useState("");
  const [routines, setRoutines] = useState<any[]>([]);
  const [computerStatus, setComputerStatus] = useState<{
    status?: string;
    reason?: string | null;
    container?: string | null;
    ready?: boolean | null;
    desktopReady?: boolean | null;
    platform?: string | null;
    localVmMode?: string | null;
  } | null>(null);
  const [screenFrame, setScreenFrame] = useState<{ src?: string; at?: number; surface?: string } | null>(null);
  const [workLocBusy, setWorkLocBusy] = useState(false);
  const [workLocNote, setWorkLocNote] = useState<string>("");
  const [routineFormOpen, setRoutineFormOpen] = useState(false);
  const [routineName, setRoutineName] = useState("");
  const [routinePrompt, setRoutinePrompt] = useState("");
  const [routineBusy, setRoutineBusy] = useState(false);
  const [routineRuns, setRoutineRuns] = useState<any[]>([]);
  const [config, setConfig] = useState<any>(null);
  const [health, setHealth] = useState<any>(null);
  const [panelOpen, setPanelOpen] = useState(true);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [inboxOnly, setInboxOnly] = useState(false);
  const [dictating, setDictating] = useState(false);
  const [engineBusy, setEngineBusy] = useState(false);
  const [browserState, setBrowserState] = useState<{ url?: string; tabs?: any[] } | null>(null);
  const [browserUrl, setBrowserUrl] = useState("");
  const [browserShot, setBrowserShot] = useState("");
  const [browserBusy, setBrowserBusy] = useState(false);
  const browserEditingRef = useRef(false);
  const dictationRef = useRef<any>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const [origin, setOrigin] = useState("");
  const [pendingFiles, setPendingFiles] = useState<Array<{ path: string; mime: string; name: string; bytes: number; kind?: string }>>([]);
  const pendingFilesRef = useRef<Array<{ path: string; mime: string; name: string; bytes: number; kind?: string }>>([]);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => { setOrigin(window.location.origin); }, []);
  const [modelBusy, setModelBusy] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const [threadQuery, setThreadQuery] = useState("");
  const [previewMode, setPreviewMode] = useState(false);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const composingRef = useRef(false);
  const imeJustEndedRef = useRef(0);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const shouldFollowTranscriptRef = useRef(true);
  const bootOnce = useRef(false);
  const streamingRef = useRef(false);
  const queueRef = useRef<QueuedPrompt[]>([]);
  const messagesRef = useRef<ChatMsg[]>([]);
  const hasMoreHistoryRef = useRef(false);
  const loadingOlderRef = useRef(false);
  const queueOpsRef = useRef<Set<string>>(new Set());
  const abortRef = useRef<AbortController | null>(null);
  const botIdRef = useRef<string | null>(null);
  const threadIdRef = useRef<string | null>(null);
  const liveTurnIdRef = useRef<string | null>(null);
  const turnEndTimerRef = useRef<number | null>(null);
  const endNoticeShownRef = useRef(false);
  const previewRef = useRef(false);
  const previewTimerRef = useRef<number | null>(null);

  useLayoutEffect(() => {
    if (window.matchMedia("(max-width: 1100px)").matches) setPanelOpen(false);
  }, []);


  useEffect(() => {
    const onSubmit = (event: Event) => {
      event.preventDefault();
      event.stopPropagation();
    };
    document.addEventListener("submit", onSubmit, true);

    const navigation = (window as Window & {
      navigation?: {
        addEventListener: (type: string, listener: (event: Event) => void) => void;
        removeEventListener: (type: string, listener: (event: Event) => void) => void;
      };
    }).navigation;
    const onNavigate = (event: Event) => {
      try {
        const dest = new URL(String((event as unknown as { destination?: { url?: string } }).destination?.url || ""), window.location.href);
        const cur = new URL(window.location.href);
        if (dest.origin !== cur.origin || dest.pathname !== "/bot" || cur.pathname !== "/bot") return;
        const droppingMode = cur.searchParams.get("mode") === "use" && dest.searchParams.get("mode") !== "use";
        const emptyingQuery = dest.search === "" || dest.search === "?";
        const navType = String((event as unknown as { navigationType?: string }).navigationType || "");
        if ((droppingMode || emptyingQuery) && navType !== "reload" && event.cancelable) {
          event.preventDefault();
        }
      } catch {
        /* keep the current /bot page */
      }
    };
    navigation?.addEventListener("navigate", onNavigate);

    return () => {
      document.removeEventListener("submit", onSubmit, true);
      navigation?.removeEventListener("navigate", onNavigate);
    };
  }, []);

  useEffect(() => {
    botIdRef.current = botId;
  }, [botId]);
  useEffect(() => {
    threadIdRef.current = threadId;
  }, [threadId]);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    hasMoreHistoryRef.current = hasMoreHistory;
  }, [hasMoreHistory]);

  const showTurnEnd = useCallback((notice: string) => {
    endNoticeShownRef.current = true;
    setTurnEndNotice(notice);
    if (turnEndTimerRef.current) window.clearTimeout(turnEndTimerRef.current);
    turnEndTimerRef.current = window.setTimeout(() => {
      setTurnEndNotice(null);
      turnEndTimerRef.current = null;
    }, 2500);
  }, []);

  const markBotIdle = useCallback(() => {
    const bid = botIdRef.current;
    if (!bid) return;
    setBots((prev) =>
      prev.map((b) => (b.id === bid ? { ...b, busy: false, activity: "idle" } : b)),
    );
  }, []);

  const finishLocalStream = useCallback(
    (notice?: string) => {
      streamingRef.current = false;
      setStreaming(false);
      setLiveStatus(null);
      liveTurnIdRef.current = null;
      markBotIdle();
      if (notice) showTurnEnd(notice);
    },
    [markBotIdle, showTurnEnd],
  );

  useEffect(() => {
    return () => {
      if (turnEndTimerRef.current) window.clearTimeout(turnEndTimerRef.current);
    };
  }, []);

  const activeBot = useMemo(() => bots.find((b) => b.id === botId) || null, [bots, botId]);
  useEffect(() => {
    if (!activeBot) return;
    setWorkLoc(computerToWorkLoc(activeBot.computer));
  }, [activeBot?.id, activeBot?.computer]);

  // Latest screen frame from chat (static preview for right rail).
  useEffect(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.kind === "screen" || m.png || m.hasImage) {
        const src = screenImageSrc(m, threadId);
        if (src) {
          setScreenFrame({ src, at: m.at, surface: undefined });
          return;
        }
      }
    }
  }, [messages, threadId]);

  const threads = useMemo(() => {
    const list = activeBot?.tasks || [];
    return [...list].sort(
      (a, b) => (b.lastThreadOrderAt || b.updatedAt || 0) - (a.lastThreadOrderAt || a.updatedAt || 0),
    );
  }, [activeBot]);

  const visibleMessages = useMemo(() => {
    const source = (!showAllHistory && messages.length > HISTORY_WINDOW)
      ? messages.slice(-HISTORY_WINDOW)
      : messages;
    const out: ChatMsg[] = [];
    let seenAssistantText = false;
    let lastAssistantOpening = "";
    const opening = (v: string) => v.replace(/\s+/g, "").slice(0, 24);
    for (const m of source) {
      if (m.role === "user") {
        seenAssistantText = false;
        lastAssistantOpening = "";
        out.push(m);
        continue;
      }
      if (m.kind === "digest" || (m.text || "").trim().startsWith("[digest]")) {
        // Hide digest rows: they clone the answer and leak English tool names.
        continue;
      }
      if (m.kind === "activity") {
        const raw = `${m.tool?.label || ""} ${m.tool?.name || ""} ${m.text || ""}`;
        const stop = isToolStopError(raw) || Boolean((m.tool as { terminalError?: boolean } | undefined)?.terminalError);
        if (stop && seenAssistantText) continue;
        out.push(m);
        continue;
      }
      if ((m.kind || "text") === "text" && m.role === "assistant" && (m.text || "").trim()) {
        const op = opening(m.text || "");
        if (seenAssistantText && op && op === lastAssistantOpening) {
          // Same-turn reprint of the same quote/table: keep the latest only.
          for (let i = out.length - 1; i >= 0; i -= 1) {
            const x = out[i];
            if (x.role === "assistant" && (x.kind || "text") === "text" && opening(x.text || "") === op) {
              out.splice(i, 1);
              break;
            }
          }
        }
        seenAssistantText = true;
        lastAssistantOpening = op || lastAssistantOpening;
      }
      out.push(m);
    }
    return out;
  }, [messages, showAllHistory]);

  const loadBots = useCallback(async () => {
    if (previewRef.current) {
      setLoadingBots(false);
      setBotsLoadError("");
      setBots(PREVIEW_BOTS);
      return PREVIEW_BOTS;
    }
    setLoadingBots(true);
    setBotsLoadError("");
    try {
      const res = await fetch("/api/mausbot/bots", { cache: "no-store" });
      const data = await readJson(res);
      if (!res.ok) throw new Error(String(data?.detail || data?.error || "load bots failed"));
      if (!Array.isArray(data?.bots)) throw new Error("invalid bots response");
      const next = data.bots as OmbBot[];
      setBots(next);
      return next;
    } catch (e) {
      setBotsLoadError(e instanceof Error ? e.message : String(e));
      return [] as OmbBot[];
    } finally {
      setLoadingBots(false);
    }
  }, []);

  const loadMessages = useCallback(async (tid: string) => {
    if (previewRef.current) {
      const mapped = PREVIEW_BY_THREAD[tid] || [];
      setHistoryTotal(mapped.length);
      setMessages(mapped);
      setShowAllHistory(false);
      setHasMoreHistory(false);
      hasMoreHistoryRef.current = false;
      setLoadingMsgs(false);
      return;
    }
    setLoadingMsgs(true);
    try {
      const res = await fetch(`/api/mausbot/threads/${encodeURIComponent(tid)}/messages`, { cache: "no-store" });
      const data = await readJson(res);
      if (!res.ok) throw new Error(String(data?.detail || data?.error || "load messages failed"));
      const raw = dropSupersededReplies((data?.messages || []) as any[]);
      const mapped: ChatMsg[] = raw
        .map((m) => wireToChatMsg(m))
        .filter((m): m is ChatMsg => Boolean(m));
      setHistoryTotal(mapped.length);
      setMessages(mapped);
      setShowAllHistory(false);
      const more = Boolean(data?.hasMore);
      setHasMoreHistory(more);
      hasMoreHistoryRef.current = more;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingMsgs(false);
    }
  }, []);

  const loadRail = useCallback(async (bid: string) => {
    try {
      const [memRes, rouRes, cfgRes, healthRes] = await Promise.all([
        fetch(`/api/mausbot/bots/${encodeURIComponent(bid)}/memory`, { cache: "no-store" }),
        fetch("/api/mausbot/routines", { cache: "no-store" }),
        fetch("/api/mausbot/config", { cache: "no-store" }),
        fetch("/api/mausbot/health", { cache: "no-store" }),
      ]);
      const mem = await readJson(memRes);
      const rou = await readJson(rouRes);
      const cfg = await readJson(cfgRes);
      const hel = await readJson(healthRes);
      if (memRes.ok) setMemoryText(String(mem?.text || ""));
      if (rouRes.ok) {
        setRoutines(rou?.routines || []);
        void refreshComputer();
        setRoutineRuns(rou?.runs || []);
      }
      if (cfgRes.ok) setConfig(cfg);
      if (healthRes.ok) setHealth(hel);
    } catch {
      /* rail best-effort */
    }
  }, []);

  const createThread = useCallback(async (bid: string, title: string) => {
    const res = await fetch("/api/mausbot/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ botId: bid, title }),
    });
    const data = await readJson(res);
    if (!res.ok) throw new Error(String(data?.detail || "create thread failed"));
    const taskTid = data?.task?.threadId ? String(data.task.threadId) : "";
    const bot = data?.bot as OmbBot | undefined;
    const tid = taskTid || bot?.threadId || bot?.mainThreadId;
    if (!tid) throw new Error("missing new thread id");
    return tid;
  }, []);

  const markThreadRead = useCallback(async (bid: string, tid: string) => {
    if (previewRef.current || !bid || !tid) return;
    setBots((prev) =>
      prev.map((b) =>
        b.id !== bid
          ? b
          : {
              ...b,
              tasks: (b.tasks || []).map((task) =>
                task.threadId === tid ? { ...task, unread: false } : task,
              ),
            },
      ),
    );
    try {
      await fetch(`/api/mausbot/bots/${encodeURIComponent(bid)}/read`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ threadId: tid }),
        cache: "no-store",
      });
    } catch {
      /* mark-read best-effort */
    }
  }, []);

  const loadOlderMessages = useCallback(async () => {
    const tid = threadIdRef.current;
    if (!tid || previewRef.current || loadingOlderRef.current) return;
    if (!hasMoreHistoryRef.current) {
      setShowAllHistory(true);
      return;
    }
    const oldest = messagesRef.current[0]?.id;
    if (!oldest) {
      setShowAllHistory(true);
      return;
    }
    loadingOlderRef.current = true;
    setLoadingOlder(true);
    try {
      const res = await fetch(
        `/api/mausbot/threads/${encodeURIComponent(tid)}/messages?before=${encodeURIComponent(oldest)}`,
        { cache: "no-store" },
      );
      const data = await readJson(res);
      if (!res.ok) throw new Error(String(data?.detail || data?.error || "load older failed"));
      const raw = dropSupersededReplies((data?.messages || []) as any[]);
      const mapped: ChatMsg[] = raw
        .map((m) => wireToChatMsg(m))
        .filter((m): m is ChatMsg => Boolean(m));
      if (!mapped.length) {
        setHasMoreHistory(false);
        hasMoreHistoryRef.current = false;
        setShowAllHistory(true);
        return;
      }
      setMessages((prev) => {
        const known = new Set(prev.map((m) => m.id));
        const older = mapped.filter((m) => !known.has(m.id));
        const next = [...older, ...prev];
        setHistoryTotal(next.length);
        messagesRef.current = next;
        return next;
      });
      const more = Boolean(data?.hasMore);
      setHasMoreHistory(more);
      hasMoreHistoryRef.current = more;
      setShowAllHistory(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      loadingOlderRef.current = false;
      setLoadingOlder(false);
    }
  }, []);


  useEffect(() => {
    if (bootOnce.current) return;
    bootOnce.current = true;
    const preview = new URLSearchParams(window.location.search).get("preview") === "1";
    previewRef.current = preview;
    if (preview) {
      document.title = "financial-bot";
      setPreviewMode(true);
      setBots(PREVIEW_BOTS);
      setBotId(PREVIEW_BOTS[0].id);
      setThreadId("t-web");
      setMessages(PREVIEW_MESSAGES);
      setHistoryTotal(PREVIEW_MESSAGES.length);
      setMemoryText(PREVIEW_MEMORY);
      setRoutines([
        { id: "r-open", name: "开盘前检查", schedule: "工作日 09:00", status: "idle" },
        { id: "r-close", name: "收盘摘要", schedule: "工作日 15:10", status: "idle" },
      ]);
      setRoutineRuns([{ id: "run-1", status: "quiet", routineName: "开盘前检查" }]);
      setConfig({
        cloudStarter: false,
        pausedBots: [],
        computer: { status: "unavailable" },
        skills: ["ifind-data-api"],
        deepseek: { configured: true },
      });
      setHealth({ ok: true, upstream_status: 200 });
      queueRef.current = [{ id: "preview-q1", text: "如果技能可用，再补一句数据源限制。", local: true }];
      setQueued(queueRef.current);
      setLoadingBots(false);
      setBootstrapping(false);
      return;
    }
    document.title = "financial-bot";
    (async () => {
      setBootstrapping(true);
      setError(null);
      try {
        const next = await loadBots();
        const bot = next[0];
        if (!bot) {
          setError(zh ? "还没有可用的 financial-bot" : "No financial-bot available");
          return;
        }
        setBotId(bot.id);
        void loadRail(bot.id);

        const saved = window.localStorage.getItem(WEB_THREAD_KEY);
        const tasks = bot.tasks || [];
        const savedOk = saved && tasks.some((t) => t.threadId === saved);
        let tid: string | null = savedOk ? saved : null;

        if (!tid) {
          const webNamed = [...tasks]
            .filter((t) => {
              const title = (t.title || "").trim();
              return title === "网页对话" || title.toLowerCase() === "web chat";
            })
            .sort((a, b) => (b.lastThreadOrderAt || b.updatedAt || 0) - (a.lastThreadOrderAt || a.updatedAt || 0))[0];
          if (webNamed) {
            tid = webNamed.threadId;
          } else {
            tid = await createThread(bot.id, zh ? "网页对话" : "Web chat");
            await loadBots();
          }
        }

        if (tid) {
          window.localStorage.setItem(WEB_THREAD_KEY, tid);
          setThreadId(tid);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBootstrapping(false);
      }
    })();
  }, [createThread, loadBots, loadRail, zh]);

  useEffect(() => {
    if (!threadId) return;
    shouldFollowTranscriptRef.current = true;
    if (previewRef.current) {
      if (!streamingRef.current) void loadMessages(threadId);
      return;
    }
    window.localStorage.setItem(WEB_THREAD_KEY, threadId);
    if (!streamingRef.current) {
      void loadMessages(threadId);
    }
    const bid = botIdRef.current;
    if (bid) void markThreadRead(bid, threadId);
  }, [threadId, loadMessages, markThreadRead]);

  useEffect(() => {
    const el = transcriptRef.current;
    scrollTranscriptToBottom(el, shouldFollowTranscriptRef.current);
  }, [visibleMessages, streaming, queued.length, liveStatus, turnEndNotice]);

  const drainQueue = useCallback(() => {
    const next = queueRef.current[0];
    if (!next) return;
    queueRef.current = queueRef.current.slice(1);
    setQueued([...queueRef.current]);
    void runAskRef.current?.(next.text);
  }, []);

  const runAskRef = useRef<((text: string) => Promise<void>) | null>(null);

  const queueMidTurn = useCallback(
    async (text: string) => {
      const bid = botIdRef.current;
      const tid = threadIdRef.current;
      if (!text.trim() || !bid || !tid) return;
      if (previewRef.current) {
        const localId = `local-q-${Date.now()}`;
        const chipId = `q-${Date.now()}`;
        setMessages((prev) => [
          ...prev,
          { id: localId, role: "user", kind: "text", text, at: Date.now(), queued: true },
        ]);
        queueRef.current = [...queueRef.current, { id: chipId, text, local: true }];
        setQueued([...queueRef.current]);
        return;
      }
      const localId = `local-q-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
      const chipId = `q-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
      setMessages((prev) => [
        ...prev,
        { id: localId, role: "user", kind: "text", text, at: Date.now(), steered: false, queued: true },
      ]);
      // Optimistic local chip; replace id with upstream queueId as soon as it returns.
      queueRef.current = [...queueRef.current, { id: chipId, text, local: true }];
      setQueued([...queueRef.current]);
      try {
        const res = await fetch("/api/mausbot/message", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text, botId: bid, threadId: tid }),
          cache: "no-store",
        });
        const data = await readJson(res);
        if (!res.ok) throw new Error(String(data?.detail || data?.error || `message failed (${res.status})`));
        const steered = data?.steered === true || data?.message?.steered === true;
        const upstreamQueueId = data?.queueId != null
          ? String(data.queueId)
          : data?.queued === true && data?.message?.id
            ? String(data.message.id)
            : "";
        if (data?.message?.id) {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === localId
                ? {
                    ...m,
                    id: String(data.message.id),
                    text: String(data.message.text || m.text),
                    steered,
                    queued: data.queued != null ? Boolean(data.queued) : true,
                  }
                : m,
            ),
          );
        }
        if (data?.queued !== true && !upstreamQueueId) {
          // 插话已进入当前回合，或消息已直接开始：都不是排队项，没有可取消的东西。
          // 旧代码把 message.id 当 queueId 显示成排队项，删除时上游只能回 404。
          queueRef.current = queueRef.current.filter((q) => q.id !== chipId);
          setQueued([...queueRef.current]);
          return;
        }
        if (upstreamQueueId) {
          queueRef.current = queueRef.current.map((q) =>
            q.id === chipId ? { ...q, id: upstreamQueueId, local: false } : q,
          );
          setQueued([...queueRef.current]);
        }
      } catch (e) {
        // Keep the local chip as a fallback so the person can still edit/delete it in the shell.
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [],
  );

  const runAsk = useCallback(
    async (text: string) => {
      const bid = botIdRef.current;
      const tid = threadIdRef.current;
      if (!text.trim() || !bid || !tid) return;
      const attachmentMarkers = pendingFilesRef.current.map((f) => f.kind === "file" ? `\n<attached-file path="${f.path}" name="${f.name}">` : `\n<attached-image path="${f.path}" name="${f.name}">`).join("");
      const sendText = text + attachmentMarkers;
      if (streamingRef.current) {
        void queueMidTurn(text);
        return;
      }
      if (previewRef.current) {
        const localUserId = `local-${Date.now()}`;
        const provisionalId = `stream-${Date.now()}`;
        streamingRef.current = true;
        setStreaming(true);
        setLiveStatus(zh ? "预览输出…" : "Preview…");
        setError(null);
        endNoticeShownRef.current = false;
        setMessages((prev) => [
          ...prev,
          { id: localUserId, role: "user", kind: "text", text, at: Date.now() },
          { id: provisionalId, role: "assistant", kind: "text", text: "", at: Date.now() + 1, pending: true },
        ]);
        if (previewTimerRef.current) window.clearTimeout(previewTimerRef.current);
        previewTimerRef.current = window.setTimeout(() => {
          previewTimerRef.current = null;
          if (!streamingRef.current) return;
          setMessages((prev) =>
            prev.map((m) =>
              m.id === provisionalId
                ? {
                    ...m,
                    pending: false,
                    text: zh ? "这是预览回复。线上发送仍走原来的流式通道。" : "Preview reply. Live sends still use the stream.",
                  }
                : m,
            ),
          );
          streamingRef.current = false;
          setStreaming(false);
          setLiveStatus(null);
          showTurnEnd(zh ? "✓ 回合结束" : "✓ Turn ended");
          if (queueRef.current.length) drainQueue();
        }, 700);
        return;
      }

      streamingRef.current = true;
      setStreaming(true);
      setLiveStatus(zh ? THINKING_ZH : THINKING_EN);
      setError(null);
      endNoticeShownRef.current = false;
      liveTurnIdRef.current = null;
      const localUserId = `local-${Date.now()}`;
      const provisionalId = `stream-${Date.now()}`;
      let streamMsgId: string | null = null;
      let streamText = "";
      let stoppedLocally = false;
      // 代理会把同一段文本发两次：runtime 里的 content.delta 和归一化的 delta。
      // 只消费 runtime 这一路，否则每个分片都会追加两遍，整段回复和表格成倍重复。
      let sawRuntimeTextDelta = false;
      // 插话后模型可能把整段答案重写一遍（同一回合内出现第二条消息）。
      // 记录被替代的旧消息，只在界面保留最新那份，避免“回复两次”。
      const supersededIds = new Set<string>();
      let turnModelProvenance: ModelProvenance | undefined;

      setMessages((prev) => [
        ...prev,
        { id: localUserId, role: "user", kind: "text", text, at: Date.now() },
        { id: provisionalId, role: "assistant", kind: "text", text: "", at: Date.now() + 1, pending: true },
      ]);

      const ac = new AbortController();
      abortRef.current = ac;

      const ensureBubble = (id: string, patch: Partial<ChatMsg>) => {
        setMessages((prev) => {
          const byReal = prev.findIndex((m) => m.id === id);
          if (byReal >= 0) {
            const next = [...prev];
            next[byReal] = { ...next[byReal], ...patch, id, pending: false };
            return next;
          }
          const byProv = prev.findIndex((m) => m.id === provisionalId || m.id === streamMsgId);
          if (byProv >= 0) {
            const next = [...prev];
            next[byProv] = { ...next[byProv], ...patch, id, pending: false };
            return next;
          }
          return [...prev, { id, role: "assistant", kind: "text", text: "", at: Date.now(), ...patch, pending: false }];
        });
      };
      const attachModelProvenance = (value: any) => {
        const next = modelProvenanceFrom(value);
        if (!next) return;
        turnModelProvenance = { ...turnModelProvenance, ...next };
        setMessages((prev) => prev.map((message) => (
          message.id === provisionalId || message.id === streamMsgId
            ? { ...message, modelProvenance: turnModelProvenance }
            : message
        )));
      };

      try {
        const res = await fetch("/api/mausbot/ask/stream", {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
          body: JSON.stringify({ text: sendText, botId: bid, threadId: tid }),
          cache: "no-store",
          signal: ac.signal,
        });

        if (!res.ok || !res.body) {
          const data = await readJson(res);
          throw new Error(String(data?.detail || data?.error || `ask failed (${res.status})`));
        }
        if (pendingFilesRef.current.length) setPending([]);

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";

        const handleEvent = (ev: any) => {
          attachModelProvenance(ev);
          attachModelProvenance(ev?.event);
          attachModelProvenance(ev?.message);
          const kind = ev?.kind;
          if (kind === "hello" && ev.threadId && ev.threadId !== threadIdRef.current) {
            setThreadId(String(ev.threadId));
          }
          if (kind === "delta" && typeof ev.delta === "string") {
            if (ev.streamKind && ev.streamKind !== "assistant_text") return;
            // 这段内容已经通过 runtime 事件到达，或代理标明是归一化副本，跳过。
            if (sawRuntimeTextDelta || ev.synthetic === true) return;
            streamText += ev.delta;
            const id = streamMsgId || provisionalId;
            ensureBubble(id, { text: streamText, role: "assistant", modelProvenance: turnModelProvenance });
            if (streamText.trim()) {
              setLiveStatus((prev) => (isGenericThinking(prev, zh) ? null : prev));
            }
          }
          if (kind === "message" || kind === "message.patch") {
            const msg = ev.message || {};
            if (!msg || typeof msg !== "object") return;
            const role = msg.role;
            if (role === "user") {
              const mid = msg.id ? String(msg.id) : "";
              setMessages((prev) => {
                const byLocal = prev.findIndex((m) => m.id === localUserId);
                if (byLocal >= 0 && mid && !prev.some((m) => m.id === mid) && prev[byLocal].text === String(msg.text || prev[byLocal].text)) {
                  const next = [...prev];
                  next[byLocal] = {
                    ...next[byLocal],
                    id: mid,
                    text: String(msg.text || next[byLocal].text),
                    attachments: mapAttachments(msg.attachments) || next[byLocal].attachments,
                    steered: msg.steered != null ? Boolean(msg.steered) : next[byLocal].steered,
                  };
                  return next;
                }
                if (mid) {
                  const idx = prev.findIndex(
                    (m) => m.id === mid || (m.role === "user" && m.id.startsWith("local-q-") && m.text === String(msg.text || "")),
                  );
                  if (idx >= 0) {
                    const next = [...prev];
                    next[idx] = {
                      ...next[idx],
                      id: mid,
                      text: String(msg.text || next[idx].text),
                      attachments: mapAttachments(msg.attachments) || next[idx].attachments,
                      steered: msg.steered != null ? Boolean(msg.steered) : next[idx].steered,
                    };
                    return next;
                  }
                  return [
                    ...prev,
                    {
                      id: mid,
                      role: "user" as const,
                      kind: "text",
                      text: String(msg.text || ""),
                      at: msg.at || Date.now(),
                      attachments: mapAttachments(msg.attachments),
                      steered: Boolean(msg.steered),
                    },
                  ];
                }
                return prev;
              });
              return;
            }
            if (role !== "bot" && role !== "assistant") return;
            const mapped = wireToChatMsg({ ...msg, role: "assistant" });
            if (!mapped) return;
            if (mapped.modelProvenance) {
              turnModelProvenance = { ...turnModelProvenance, ...mapped.modelProvenance };
            }
            const msgid = String(msg.id || streamMsgId || provisionalId);
            if (supersededIds.has(msgid)) return;
            if (mapped.kind === "activity") {
              setMessages((prev) => {
                const idx = prev.findIndex((m) => m.id === msgid);
                if (idx >= 0) {
                  const next = [...prev];
                  next[idx] = { ...next[idx], ...mapped, id: msgid, pending: false };
                  return next;
                }
                const prov = prev.findIndex((m) => m.id === provisionalId || (m.pending && m.role === "assistant"));
                if (prov >= 0) {
                  const next = [...prev];
                  next.splice(prov, 0, { ...mapped, id: msgid, pending: false });
                  return next;
                }
                return [...prev, { ...mapped, id: msgid, pending: false }];
              });
              if (mapped.tool?.label || mapped.tool?.name) {
                setLiveStatus(localizeActivityPhrase(String(mapped.tool.label || mapped.tool.name || ""), zh));
              }
              return;
            }
            if (["options", "secret", "signin", "connector", "widget", "app", "screen", "routine.run", "goal.run", "compaction", "call", "digest"].includes(String(mapped.kind || ""))) {
              setMessages((prev) => {
                const idx = prev.findIndex((m) => m.id === msgid);
                if (idx >= 0) {
                  const next = [...prev];
                  next[idx] = { ...next[idx], ...mapped, id: msgid, pending: false };
                  return next;
                }
                return [...prev.filter((m) => m.id !== provisionalId || Boolean(streamText)), { ...mapped, id: msgid, pending: false }];
              });
              return;
            }
            const prevStreamId = streamMsgId;
            const nextText = String(msg.text || "");
            const opening = (v: string) => v.replace(/\s+/g, "").slice(0, 20);
            const shortFollowUp = Boolean(prevStreamId) && prevStreamId !== msgid
              && nextText.trim().length > 0 && nextText.trim().length <= 40
              && streamText.trim().length > 120;
            if (shortFollowUp) {
              // 插话后模型常把补充的一句话单独发一条消息；并回上一段答案，只显示一条回复。
              supersededIds.add(msgid);
              streamText = `${streamText.replace(/\s+$/, "")}\n\n${nextText.trim()}`;
              ensureBubble(String(prevStreamId), { text: streamText, kind: mapped.kind || "text", at: msg.at });
              return;
            }
            const reAnswer = Boolean(prevStreamId) && prevStreamId !== msgid
              && opening(streamText).length >= 10 && opening(streamText) === opening(nextText);
            if (reAnswer) {
              // 同一回合里重写的整段答案：移除旧气泡，只保留新答案。
              supersededIds.add(String(prevStreamId));
              setMessages((prev) => prev.filter((m) => m.id !== prevStreamId));
              streamText = "";
            }
            streamMsgId = msgid;
            if (nextText && nextText.length >= streamText.length) streamText = nextText;
            ensureBubble(msgid, {
              text: streamText || nextText,
              kind: mapped.kind || "text",
              at: msg.at,
              helperIntro: Boolean(msg.helperIntro),
              attachments: mapAttachments(msg.attachments),
              tool: mapped.tool,
              modelProvenance: mapped.modelProvenance ?? turnModelProvenance,
            });
            if (String(streamText || nextText).trim()) {
              setLiveStatus((prev) => (isGenericThinking(prev, zh) ? null : prev));
            }
          }
          if (kind === "runtime") {
            const runtime = ev.event || {};
            if (runtime?.type === "turn.started" && runtime.turnId) {
              liveTurnIdRef.current = String(runtime.turnId);
            }
            if (runtime?.type === "turn.completed") {
              setLiveStatus(null);
            }
            const line = runtimeStatusLine(runtime, zh);
            if (line) {
              if (!(streamText.trim() && isGenericThinking(line, zh))) setLiveStatus(line);
            }
            if (runtime?.type === "content.delta" && typeof runtime.delta === "string" && runtime.delta) {
              if (!runtime.streamKind || runtime.streamKind === "assistant_text") {
                sawRuntimeTextDelta = true;
                streamText += runtime.delta;
                const id = streamMsgId || provisionalId;
                ensureBubble(id, { text: streamText, role: "assistant" });
                if (streamText.trim()) {
                  setLiveStatus((prev) => (isGenericThinking(prev, zh) ? null : prev));
                }
              }
            }
          }
          if (kind === "bot") {
            const bot = ev.bot || {};
            if (bot.id && bot.id === botIdRef.current) {
              setBots((prev) =>
                prev.map((b) =>
                  b.id === bot.id
                    ? {
                        ...b,
                        busy: bot.busy != null ? Boolean(bot.busy) : b.busy,
                        activity: bot.activity != null ? String(bot.activity) : b.activity,
                      }
                    : b,
                ),
              );
              if (streamingRef.current) {
                if (bot.busy && bot.activity) {
                  setLiveStatus(localizeActivityPhrase(String(bot.activity || ""), zh));
                } else if (bot.busy === false) {
                  setLiveStatus(null);
                }
              }
            }
          }
          if (kind === "bot.queued") {
            const tidNow = threadIdRef.current;
            const queues = ev.queues || {};
            const items = tidNow && Array.isArray(queues[tidNow]) ? queues[tidNow] : [];
            const chips: QueuedPrompt[] = items
              .map((it: any, idx: number) => ({
                id: String(it?.queueId || it?.id || it?.messageId || `srv-q-${idx}`),
                text: String(it?.text || "").trim(),
                local: false,
              }))
              .filter((q: QueuedPrompt) => q.text);
            // Prefer upstream ids; keep only local chips that have not been acknowledged yet.
            const localOnly = queueRef.current.filter(
              (q) => q.local && !chips.some((c) => c.id === q.id || c.text === q.text),
            );
            queueRef.current = [...chips, ...localOnly];
            setQueued([...queueRef.current]);
          }
          
          if (kind === "screen") {
            const png = typeof ev.png === "string" ? ev.png : "";
            if (png || ev.pngOmitted) {
              if (png) {
                const src = png.startsWith("data:") ? png : `data:${ev.mime || "image/png"};base64,${png}`;
                setScreenFrame({ src, at: Date.now(), surface: ev.surface ? String(ev.surface) : undefined });
              }
              const mid = `screen-live-${ev.threadId || threadIdRef.current || "x"}`;
              setMessages((prev) => {
                const mapped: ChatMsg = {
                  id: mid,
                  role: "assistant",
                  kind: "screen",
                  text: "",
                  at: Date.now(),
                  png: png || undefined,
                  mime: ev.mime ? String(ev.mime) : undefined,
                  pngOmitted: Boolean(ev.pngOmitted),
                };
                const idx = prev.findIndex((m) => m.id === mid || m.kind === "screen");
                if (idx >= 0) {
                  const next = [...prev];
                  next[idx] = { ...next[idx], ...mapped };
                  return next;
                }
                return [...prev, mapped];
              });
              setLiveStatus(zh ? "屏幕已更新" : "Screen updated");
            }
            return;
          }
          if (kind === "computer") {
            setLiveStatus(zh ? `电脑：${ev.state || "?"}` : `Computer: ${ev.state || "?"}`);
            return;
          }
          if (kind === "computer-control") {
            setLiveStatus(ev.held ? (zh ? "电脑控制：被占用" : "Computer held") : (zh ? "电脑控制：可用" : "Computer free"));
            return;
          }
          if (kind === "routine.run") {
            const run = ev.run || {};
            setLiveStatus(zh ? `例行：${run.routineName || ""} ${run.status || ""}`.trim() : `Routine: ${run.routineName || ""} ${run.status || ""}`.trim());
            return;
          }
          if (kind === "notify") {
            const n = ev.notification || {};
            if (n.title || n.body) setLiveStatus(String(n.title || n.body));
            return;
          }
if (kind === "turn") {
            if (ev.phase === "started" && ev.turnId) {
              liveTurnIdRef.current = String(ev.turnId);
              if (!streamText.trim()) setLiveStatus(zh ? THINKING_ZH : THINKING_EN);
            }
            if (ev.phase === "completed") {
              setLiveStatus(null);
            }
          }
          if (kind === "interrupted") {
            setLiveStatus(null);
            if (!endNoticeShownRef.current) showTurnEnd(zh ? "✓ 已停止" : "✓ Stopped");
          }
          if (kind === "error") {
            setError(String(ev.error || "financial-bot error"));
          }
          if (kind === "done") {
            if (Array.isArray(ev.answers) && ev.answers.length) {
              setMessages((prev) => {
                const withoutProv = prev.filter((m) => m.id !== provisionalId && !(m.pending && m.role === "assistant"));
                const known = new Set(withoutProv.map((m) => m.id));
                const extras: ChatMsg[] = [];
                for (const a of ev.answers) {
                  const id = String(a.id || "");
                  if (!id || supersededIds.has(id)) continue;
                  if (known.has(id)) {
                    const idx = withoutProv.findIndex((m) => m.id === id);
                    if (idx >= 0) {
                      withoutProv[idx] = {
                        ...withoutProv[idx],
                        text: String(a.text || withoutProv[idx].text),
                        kind: String(a.kind || withoutProv[idx].kind || "text"),
                        attachments: mapAttachments(a.attachments) || withoutProv[idx].attachments,
                        pending: false,
                      };
                    }
                  } else if (streamMsgId && id === streamMsgId) {
                    /* already present */
                  } else {
                    extras.push({
                      id,
                      role: "assistant",
                      kind: String(a.kind || "text"),
                      text: String(a.text || ""),
                      at: a.at || Date.now(),
                      attachments: mapAttachments(a.attachments),
                    });
                  }
                }
                const cleaned = withoutProv.filter((m) => m.id !== provisionalId);
                if (streamMsgId && !cleaned.some((m) => m.id === streamMsgId) && streamText) {
                  cleaned.push({
                    id: streamMsgId,
                    role: "assistant",
                    kind: "text",
                    text: streamText,
                    at: Date.now(),
                  });
                }
                return [...cleaned, ...extras];
              });
            } else if (!streamText) {
              setMessages((prev) => prev.filter((m) => m.id !== provisionalId));
            } else {
              setMessages((prev) =>
                prev.map((m) => (m.id === provisionalId || m.id === streamMsgId ? { ...m, pending: false, text: streamText } : m)),
              );
            }
            if (ev.error && !ev.ok) setError(String(ev.error));
            if (ev.threadId && ev.threadId !== threadIdRef.current) setThreadId(String(ev.threadId));
            streamingRef.current = false;
            setStreaming(false);
            setLiveStatus(null);
            liveTurnIdRef.current = null;
            markBotIdle();
            if (!endNoticeShownRef.current) {
              showTurnEnd(zh ? "✓ 回合结束" : "✓ Turn ended");
            }
          }
        };

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          while (true) {
            const idx = buf.indexOf("\n\n");
            if (idx < 0) break;
            const block = buf.slice(0, idx);
            buf = buf.slice(idx + 2);
            const dataLines = block
              .split("\n")
              .filter((ln) => ln.startsWith("data: "))
              .map((ln) => ln.slice(6));
            if (!dataLines.length) continue;
            const raw = dataLines.join("\n").trim();
            if (!raw || raw === "ping") continue;
            try {
              handleEvent(JSON.parse(raw));
            } catch {
              /* ignore bad event */
            }
          }
        }
        void loadBots();
      } catch (err) {
        if ((err as any)?.name === "AbortError") {
          stoppedLocally = true;
          setMessages((prev) =>
            prev.map((m) =>
              m.id === provisionalId || m.pending
                ? { ...m, pending: false, text: m.text || (zh ? "（已停止）" : "(stopped)") }
                : m,
            ),
          );
          if (!endNoticeShownRef.current) showTurnEnd(zh ? "✓ 已停止" : "✓ Stopped");
        } else {
          setError(err instanceof Error ? err.message : String(err));
          setMessages((prev) => prev.filter((m) => m.id !== provisionalId));
          setInput((cur) => cur || text);
        }
      } finally {
        streamingRef.current = false;
        setStreaming(false);
        setLiveStatus(null);
        liveTurnIdRef.current = null;
        abortRef.current = null;
        markBotIdle();
        if (!endNoticeShownRef.current) {
          showTurnEnd(
            stoppedLocally ? (zh ? "✓ 已停止" : "✓ Stopped") : (zh ? "✓ 回合结束" : "✓ Turn ended"),
          );
        }
        composerRef.current?.focus();
        if (!stoppedLocally && !previewRef.current) {
          // 插话后的补充消息可能在上游流关闭后才落库；稍后对齐一次，避免要手动刷新才看得到。
          window.setTimeout(() => {
            if (!streamingRef.current) void loadMessages(tid);
          }, 1500);
        }
        if (queueRef.current.length) {
          drainQueue();
        } else {
          setQueued((prev) => prev.filter((q) => queueRef.current.some((lq) => lq.id === q.id)));
        }
      }
    },
    [drainQueue, loadBots, loadMessages, markBotIdle, queueMidTurn, showTurnEnd, zh],
  );

  useEffect(() => {
    runAskRef.current = runAsk;
  }, [runAsk]);

  async function onNewThread() {
    if (!botId || streaming) return;
    setError(null);
    if (previewRef.current) {
      const tid = `preview-${Date.now()}`;
      setBots((prev) =>
        prev.map((b) =>
          b.id === botId
            ? {
                ...b,
                tasks: [{ threadId: tid, title: zh ? "网页对话" : "Web chat", updatedAt: Date.now() }, ...(b.tasks || [])],
              }
            : b,
        ),
      );
      setMessages([]);
      setHistoryTotal(0);
      setThreadId(tid);
      setNavOpen(false);
      return;
    }
    try {
      const tid = await createThread(botId, zh ? "网页对话" : "Web chat");
      await loadBots();
      setMessages([]);
      setHistoryTotal(0);
      setThreadId(tid);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  function setPending(next: Array<{ path: string; mime: string; name: string; bytes: number; kind?: string }>) {
    pendingFilesRef.current = next;
    setPendingFiles(next);
  }

  async function attachFiles(list: FileList | null) {
    if (!list?.length) return;
    if (streamingRef.current) {
      setError(zh ? "当前回合结束后再添加附件" : "Add attachments after this turn ends");
      return;
    }
    for (const file of Array.from(list).slice(0, 4)) {
      const isImage = file.type.startsWith("image/");
      const endpoint = isImage ? "/api/mausbot/attachments" : "/api/mausbot/files";
      try {
        const res = await fetch(`${endpoint}?name=${encodeURIComponent(file.name)}`, {
          method: "POST",
          headers: { "Content-Type": file.type || "application/octet-stream" },
          body: file,
          cache: "no-store",
        });
        const data = await readJson(res);
        if (!res.ok || !data?.path) throw new Error(String(data?.detail || data?.error || `upload failed (${res.status})`));
        setPending([...pendingFilesRef.current, { path: String(data.path), mime: String(data.mime || file.type), name: String(data.name || file.name), bytes: Number(data.bytes || file.size), kind: isImage ? "image" : "file" }]);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    }
  }

  const refreshBrowser = useCallback(async () => {
    try {
      const res = await fetch("/api/mausbot/browser/state", { cache: "no-store" });
      const data = await readJson(res);
      if (!res.ok) {
        throw new Error(String(data?.detail || data?.error || "browser unavailable"));
      }
      setBrowserState(data);
      if (!browserEditingRef.current) setBrowserUrl(String(data?.url || ""));
      setBrowserShot(`/api/mausbot/browser/screenshot?t=${Date.now()}`);
    } catch {
      setBrowserState(null);
    }
  }, []);

  async function browserOpenUrl(url?: string) {
    const target = String(url ?? browserUrl).trim();
    if (!target) return;
    setBrowserBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/mausbot/browser/open", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: target }), cache: "no-store" });
      const data = await readJson(res);
      if (!res.ok) {
        throw new Error(String(data?.detail || data?.error || `open failed (${res.status})`));
      }
      await refreshBrowser();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBrowserBusy(false);
    }
  }

  async function browserAct(action: string, index?: number, tabId?: string) {
    setBrowserBusy(true);
    try {
      const res = await fetch("/api/mausbot/browser/action", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action, index, tabId }), cache: "no-store" });
      const data = await readJson(res);
      if (!res.ok) {
        throw new Error(String(data?.detail || data?.error || `action failed (${res.status})`));
      }
      await refreshBrowser();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBrowserBusy(false);
    }
  }

  async function installBrowserEngine() {
    setEngineBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/mausbot/browser-engine/install", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}", cache: "no-store" });
      const data = await readJson(res);
      if (!res.ok) {
        throw new Error(String(data?.detail || data?.error || `install failed (${res.status})`));
      }
      if (botId) await loadRail(botId);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setEngineBusy(false);
    }
  }

  async function toggleDictation() {
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) {
      setError(zh ? "当前浏览器不支持听写" : "Dictation is not supported in this browser");
      return;
    }
    if (dictating) {
      dictationRef.current?.stop();
      setDictating(false);
      return;
    }
    const rec = new SR();
    rec.lang = zh ? "zh-CN" : "en-US";
    rec.continuous = true;
    rec.interimResults = false;
    rec.onresult = (ev: any) => {
      let add = "";
      for (let i = ev.resultIndex; i < ev.results.length; i += 1) add += ev.results[i][0]?.transcript || "";
      if (add) setInput((prev) => (prev ? prev + add : add));
    };
    rec.onend = () => { setDictating(false); dictationRef.current = null; };
    rec.onerror = () => { setDictating(false); };
    dictationRef.current = rec;
    try { rec.start(); setDictating(true); } catch { setDictating(false); }
  }

  function switchLang(next: string) {
    const url = new URL(window.location.href);
    url.searchParams.set("lang", next);
    window.location.href = url.toString();
  }

  function onSubmit(e?: FormEvent) {
    e?.preventDefault();
    const typed = input.trim();
    const hasFiles = pendingFilesRef.current.length > 0;
    const text = typed || (hasFiles ? (zh ? "请看附件" : "Please look at the attachment") : "");
    if (!text || !botId || !threadId || bootstrapping) return;
    setInput("");
    if (streamingRef.current) {
      void queueMidTurn(text);
      return;
    }
    void runAsk(text);
  }

  async function onStop() {
    if (previewRef.current) {
      if (previewTimerRef.current) {
        window.clearTimeout(previewTimerRef.current);
        previewTimerRef.current = null;
      }
      setMessages((prev) =>
        prev.map((m) => (m.pending ? { ...m, pending: false, text: m.text || (zh ? "（已停止）" : "(stopped)") } : m)),
      );
      finishLocalStream(zh ? "✓ 已停止" : "✓ Stopped");
      if (queueRef.current.length) drainQueue();
      return;
    }
    const bid = botIdRef.current;
    const tid = threadIdRef.current;
    const turnId = liveTurnIdRef.current;
    if (bid && tid) {
      try {
        await fetch("/api/mausbot/interrupt", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ botId: bid, threadId: tid, ...(turnId ? { turnId } : {}) }),
          cache: "no-store",
        });
      } catch {
        /* interrupt best-effort; still abort local reader */
      }
    }
    abortRef.current?.abort();
    finishLocalStream(zh ? "✓ 已停止" : "✓ Stopped");
  }

  async function cancelQueued(id: string): Promise<QueuedPrompt | null> {
    const item = queueRef.current.find((q) => q.id === id) || queued.find((q) => q.id === id);
    if (!item) return null;
    if (queueOpsRef.current.has(id)) {
      throw new Error(zh ? "该排队消息正在处理" : "That queued message is already being updated");
    }
    queueOpsRef.current.add(id);
    try {
      if (!item.local) {
        const bid = botIdRef.current;
        const tid = threadIdRef.current;
        if (!bid || !tid) throw new Error(zh ? "当前话题不可用" : "Current thread unavailable");
        const res = await fetch(`/api/mausbot/bots/${encodeURIComponent(bid)}/queue/${encodeURIComponent(id)}`, {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ threadId: tid }),
          cache: "no-store",
        });
        const data = await readJson(res);
        if (!res.ok) throw new Error(String(data?.detail || data?.error || `queue update failed (${res.status})`));
      }
      queueRef.current = queueRef.current.filter((q) => q.id !== id);
      setQueued((prev) => prev.filter((q) => q.id !== id));
      return item;
    } finally {
      queueOpsRef.current.delete(id);
    }
  }

  async function removeQueued(id: string) {
    try {
      await cancelQueued(id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function editQueued(id: string) {
    const item = queueRef.current.find((q) => q.id === id) || queued.find((q) => q.id === id);
    if (!item) return;
    try {
      const cancelled = await cancelQueued(id);
      if (!cancelled) return;
      setInput(cancelled.text);
      requestAnimationFrame(() => {
        composerRef.current?.focus();
        composerRef.current?.setSelectionRange(cancelled.text.length, cancelled.text.length);
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }


  const refreshComputer = useCallback(async () => {
    if (previewRef.current) {
      setComputerStatus({ status: "unavailable", reason: zh ? "预览模式" : "Preview mode" });
      return;
    }
    try {
      const res = await fetch("/api/mausbot/computer", { cache: "no-store" });
      const data = await readJson(res);
      if (!res.ok) throw new Error(String(data?.detail || data?.error || `computer failed (${res.status})`));
      const c = data?.computer || {};
      setComputerStatus({
        status: String(c.status || "unavailable"),
        reason: c.reason != null ? String(c.reason) : null,
        container: c.container != null ? String(c.container) : null,
        ready: c.ready == null ? null : Boolean(c.ready),
        desktopReady: c.desktopReady == null ? null : Boolean(c.desktopReady),
        platform: c.platform != null ? String(c.platform) : null,
        localVmMode: data?.localVm?.mode != null ? String(data.localVm.mode) : null,
      });
    } catch (e) {
      setComputerStatus({
        status: "unavailable",
        reason: e instanceof Error ? e.message : String(e),
      });
    }
  }, [zh]);


  useEffect(() => {
    if (rail === "screen") void refreshComputer();
  }, [rail, refreshComputer]);



  async function refreshRoutines() {
    const rr = await fetch("/api/mausbot/routines", { cache: "no-store" });
    const rj = await readJson(rr);
    if (rr.ok) {
      setRoutines(rj?.routines || []);
      if (Array.isArray(rj?.runs)) setRoutineRuns(rj.runs);
    }
  }

  async function toggleRoutine(routineId: string, currentlyEnabled: boolean) {
    if (previewRef.current) return;
    try {
      const res = await fetch(`/api/mausbot/routines/${encodeURIComponent(routineId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !currentlyEnabled }),
        cache: "no-store",
      });
      const data = await readJson(res);
      if (!res.ok) throw new Error(String(data?.detail || data?.error || `routine update failed (${res.status})`));
      await refreshRoutines();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function runRoutineNow(routineId: string) {
    if (previewRef.current || routineBusy) return;
    setRoutineBusy(true);
    try {
      const res = await fetch(`/api/mausbot/routines/${encodeURIComponent(routineId)}/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
        cache: "no-store",
      });
      const data = await readJson(res);
      if (!res.ok) throw new Error(String(data?.detail || data?.error || `routine run failed (${res.status})`));
      const run = data?.run || data;
      const runThread = run?.threadId || run?.task?.threadId || run?.resultsThreadId;
      await refreshRoutines();
      if (runThread) {
        setThreadId(String(runThread));
      }
      setLiveStatus(zh
        ? (runThread ? "例行已触发，已跳到运行话题" : "例行已触发立即跑")
        : (runThread ? "Routine started — jumped to its thread" : "Routine run started"));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRoutineBusy(false);
    }
  }

  async function deleteRoutine(routineId: string) {
    if (previewRef.current) return;
    if (!window.confirm(zh ? "删除这个例行？" : "Delete this routine?")) return;
    try {
      const res = await fetch(`/api/mausbot/routines/${encodeURIComponent(routineId)}`, {
        method: "DELETE",
        cache: "no-store",
      });
      const data = await readJson(res);
      if (!res.ok) throw new Error(String(data?.detail || data?.error || `routine delete failed (${res.status})`));
      await refreshRoutines();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function createRoutineSimple() {
    if (previewRef.current || routineBusy) return;
    const name = routineName.trim();
    const prompt = routinePrompt.trim();
    if (!name || !prompt) {
      setError(zh ? "例行需要名称和提示词" : "Routine needs a name and prompt");
      return;
    }
    setRoutineBusy(true);
    try {
      const res = await fetch("/api/mausbot/routines", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, prompt, botId: botId || undefined, everyMinutes: 1440 }),
        cache: "no-store",
      });
      const data = await readJson(res);
      if (!res.ok) throw new Error(String(data?.detail || data?.error || `routine create failed (${res.status})`));
      setRoutineName("");
      setRoutinePrompt("");
      setRoutineFormOpen(false);
      await refreshRoutines();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRoutineBusy(false);
    }
  }

  /** Surfaces the remote web shell can meaningfully switch. local (= this Mac) is read-only here. */
  function workLocDisabled(loc: WorkLocation): { disabled: boolean; reason?: string } {
    if (loc === "this") {
      return { disabled: true, reason: zh ? "网页端不能切到「这台电脑」（需桌面端）" : "Web shell cannot switch to This computer (use desktop)" };
    }
    // This ~2GB VPS cannot safely run the official Local VM (4GB reserved). Keep Off.
    if (loc === "private" || loc === "cloud" || loc === "auto" || loc === "browser") {
      return {
        disabled: true,
        reason: zh
          ? "这台约 2GB，官方 Local VM 预留 4GB，暂不能开桌面/云桌面/自动/浏览器；请保持「关闭」。普通对话和 iFinD 不需要 Docker。"
          : "This ~2GB host cannot safely run the official Local VM (4GB reserved). Keep Off. Chat and iFinD do not need Docker.",
      };
    }
    return { disabled: false };
  }

  async function setWorkLocation(loc: WorkLocation) {
    if (previewRef.current || workLocBusy) return;
    const gate = workLocDisabled(loc);
    if (gate.disabled) {
      setWorkLocNote(gate.reason || "");
      return;
    }
    const bid = botIdRef.current;
    if (!bid) {
      setWorkLocNote(zh ? "还没有机器人" : "No bot yet");
      return;
    }
    const prev = workLoc;
    setWorkLoc(loc);
    setWorkLocBusy(true);
    setWorkLocNote("");
    try {
      const computer = workLocToComputer(loc);
      const body = computer == null ? { clearComputer: true } : { computer };
      const sendPatch = () => fetch(`/api/mausbot/bots/${encodeURIComponent(bid)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        cache: "no-store",
      });
      let res = await sendPatch();
      if (res.status === 502 || res.status === 503) {
        // 代理重启或隧道抖动会返回瞬时 502；给一次重试，别把一次抖动显示成配置失败。
        await new Promise((r) => setTimeout(r, 800));
        res = await sendPatch();
      }
      const data = await readJson(res);
      if (!res.ok) throw new Error(String(data?.detail || data?.error || `work location failed (${res.status})`));
      const bot = data?.bot || data;
      if (bot && typeof bot === "object") {
        setBots((prevBots) => prevBots.map((b) => (b.id === bid ? { ...b, computer: bot.computer ?? null } : b)));
        setWorkLoc(computerToWorkLoc(bot.computer ?? null));
      }
      setWorkLocNote(zh ? "已同步到上游" : "Synced upstream");
    } catch (e) {
      setWorkLoc(prev);
      setWorkLocNote(e instanceof Error ? e.message : String(e));
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setWorkLocBusy(false);
    }
  }

  async function deleteThreadHard(thread: string) {
    const bid = botIdRef.current;
    if (!bid || previewRef.current) return;
    if (!window.confirm(zh ? "硬删除此话题？不可恢复。" : "Permanently delete this thread?")) return;
    try {
      const res = await fetch(`/api/mausbot/bots/${encodeURIComponent(bid)}/tasks/${encodeURIComponent(thread)}`, {
        method: "DELETE",
        cache: "no-store",
      });
      const data = await readJson(res);
      if (!res.ok) throw new Error(String(data?.detail || data?.error || `delete failed (${res.status})`));
      const br = await fetch("/api/mausbot/bots", { cache: "no-store" });
      const bj = await readJson(br);
      if (br.ok && Array.isArray(bj?.bots)) setBots(bj.bots);
      if (threadIdRef.current === thread) {
        const other = (bj?.bots || []).flatMap((b: any) => b.tasks || []).find((t: any) => t.threadId && t.threadId !== thread);
        if (other?.threadId) setThreadId(other.threadId);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function patchTask(thread: string, body: Record<string, unknown>) {
    const bid = botIdRef.current;
    if (!bid || previewRef.current) return;
    const res = await fetch(`/api/mausbot/bots/${encodeURIComponent(bid)}/tasks/${encodeURIComponent(thread)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    });
    const data = await readJson(res);
    if (!res.ok) throw new Error(String(data?.detail || data?.error || `task update failed (${res.status})`));
    // Refresh bots list for new titles / pins
    try {
      const br = await fetch("/api/mausbot/bots", { cache: "no-store" });
      const bj = await readJson(br);
      if (br.ok && Array.isArray(bj?.bots)) setBots(bj.bots);
    } catch { /* ignore */ }
  }

  async function renameThread(thread: string, currentTitle?: string) {
    const next = window.prompt(zh ? "话题名称" : "Thread title", currentTitle || "");
    if (next == null) return;
    const title = next.trim();
    if (!title) return;
    try {
      await patchTask(thread, { title });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function togglePinThread(thread: string, pinned?: boolean) {
    try {
      await patchTask(thread, { pinned: !pinned });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function archiveThread(thread: string) {
    try {
      await patchTask(thread, { archivedAt: Date.now() });
      if (threadIdRef.current === thread) {
        const other = threads.find((t) => t.threadId !== thread)?.threadId;
        if (other) setThreadId(other);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  function openRail(tab: RailTab) {
    setRail(tab);
    setPanelOpen(true);
    setNavOpen(false);
  }

  function selectBot(bot: OmbBot) {
    setBotId(bot.id);
    const preferred = previewRef.current
      ? (bot.tasks?.find((t) => !isNoisyTitle(t.title))?.threadId || bot.tasks?.[0]?.threadId || null)
      : (
          window.localStorage.getItem(WEB_THREAD_KEY) ||
          bot.tasks?.find((t) => !isNoisyTitle(t.title))?.threadId ||
          bot.mainThreadId ||
          bot.threadId ||
          null
        );
    setThreadId(preferred);
    setNavOpen(false);
    if (previewRef.current) {
      setMemoryText(
        bot.id === "bot-preview"
          ? PREVIEW_MEMORY
          : (zh ? "这个机器人还没有记忆。" : "No memory for this bot yet."),
      );
      return;
    }
    void loadRail(bot.id);
  }

  const activeModelSel: ModelSel | undefined = (
    threads.find((item) => item.threadId === threadId)?.modelSelection
    || activeBot?.modelSelection
  );
  const modelFamily = modelFamilyOf(activeModelSel);
  const modelLabel = modelFamily === "deepseek" ? "DeepSeek" : "GPT";

  async function applyModelFamily(next: ModelFamily) {
    if (modelBusy || next === modelFamily) return;
    if (modelFamily === "gpt") storeGptSelection(activeModelSel);
    const selection = next === "deepseek"
      ? { ...DEEPSEEK_SELECTION }
      : readStoredGptSelection();
    if (previewRef.current) {
      setBots((prev) => prev.map((b) => (
        b.id === botId ? { ...b, modelSelection: selection, tasks: (b.tasks || []).map((task) => (
          task.threadId === threadId ? { ...task, modelSelection: selection, followsBotModel: true } : task
        )) } : b
      )));
      return;
    }
    if (!botId) return;
    setModelBusy(true);
    try {
      const body: Record<string, unknown> = { modelSelection: selection };
      if (threadId) body.threadId = threadId;
      const res = await fetch(`/api/mausbot/bots/${encodeURIComponent(botId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        cache: "no-store",
      });
      const data = await readJson(res);
      if (!res.ok) throw new Error(String(data?.detail || data?.error || "model switch failed"));
      const bot = data?.bot as OmbBot | undefined;
      if (bot?.id) {
        setBots((prev) => prev.map((b) => (b.id === bot.id ? { ...b, ...bot, tasks: bot.tasks || b.tasks } : b)));
        if (modelFamilyOf(bot.modelSelection) === "gpt") storeGptSelection(bot.modelSelection);
      } else {
        setBots((prev) => prev.map((b) => (b.id === botId ? { ...b, modelSelection: selection } : b)));
        if (next === "gpt") storeGptSelection(selection);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setModelBusy(false);
    }
  }


  const activeTitle =
    threads.find((t) => t.threadId === threadId)?.title ||
    (zh ? "网页对话" : "Web chat");

  const shownThreads = threads.filter((t) => {
    if (isNoisyTitle(t.title) && t.threadId !== threadId) return false;
    if (inboxOnly && !t.unread) return false;
    const q = threadQuery.trim().toLowerCase();
    if (!q) return true;
    return (t.title || "").toLowerCase().includes(q);
  });

  useEffect(() => {
    if (rail !== "browser") return;
    void refreshBrowser();
    const timer = window.setInterval(() => { void refreshBrowser(); }, 2500);
    return () => window.clearInterval(timer);
  }, [rail, refreshBrowser]);

  const unreadCount = threads.filter((t) => t.unread).length;

  const presence = (() => {
    const openSecret = messages.some(
      (m) =>
        (m.kind === "secret" || m.secret)
        && m.secret
        && !m.secret.provided
        && !m.secret.dismissed
        && !m.secret.superseded
        && !m.secret.stopped,
    );
    const line = liveStatus || "";
    const lower = line.toLowerCase();
    if (openSecret || line.includes("需要确认") || lower.includes("needs approval") || line.includes("安全授权")) return "approval";
    if (line.includes("等待") || lower.includes("waiting")) return "waiting";
    if (streaming || activeBot?.busy) return "working";
    return "idle";
  })();

  const presenceLabel = presence === "approval"
    ? (zh ? "待确认" : "Needs you")
    : presence === "waiting"
      ? (zh ? "等待中" : "Waiting")
      : presence === "working"
        ? (zh ? "进行中" : "Working")
        : (zh ? "空闲" : "Idle");

  const cues = readinessFrom(config);
  const healthLabel = previewMode
    ? (zh ? "界面预览" : "Preview")
    : health == null
      ? (zh ? "正在连接" : "Connecting")
      : health.ok
        ? (zh ? "已连接" : "Connected")
        : (zh ? "未连接" : "Offline");

  const enginePreview = config
    ? JSON.stringify(
        {
          cloudStarter: config.cloudStarter,
          pausedBots: config.pausedBots,
          engines: Object.fromEntries(
            Object.entries(config)
              .filter(([, v]) => typeof v === "object" && v && "configured" in (v as object))
              .slice(0, 12),
          ),
        },
        null,
        2,
      )
    : "";

  return (
    <div
      className={styles.shell}
      data-theme="light"
      data-locale={zh ? "zh" : "en"}
      data-panel={panelOpen ? "open" : "closed"}
      data-nav={navOpen ? "open" : "closed"}
      data-presence={presence}
      data-mausbot="1"
    >
      {error ? (
        <div className={styles.errorBanner} role="alert">
          <span>{error}</span>
          <button type="button" onClick={() => setError(null)} aria-label={zh ? "关闭" : "Dismiss"}>×</button>
        </div>
      ) : null}

      <div className={styles.layout}>
        <aside className={styles.sidebar} aria-label={zh ? "机器人与话题" : "Bots and threads"}>
          <div className={styles.brand}>
            <span className={styles.brandMark} aria-hidden="true">M</span>
            <span className={styles.brandCopy}>
              <span className={styles.brandName}>financial-bot</span>
              <span className={styles.brandMeta}>
                <span className={styles.healthDot} data-ok={health == null ? "" : health.ok ? "1" : "0"} />
                {healthLabel}
              </span>
            </span>
            <button
              type="button"
              className={styles.sidebarClose}
              aria-label={zh ? "关闭话题侧栏" : "Close thread sidebar"}
              onClick={() => { setNavOpen(false); setPanelOpen(false); }}
            >
              <span aria-hidden="true">✕</span>
            </button>
          </div>

          <div className={styles.topActions}>
            <button type="button" className={styles.topAction} data-active={inboxOnly ? "true" : "false"} title={zh ? "收件箱：只看未读" : "Inbox: unread only"} onClick={() => setInboxOnly((v) => !v)}>
              <span aria-hidden="true">◔</span>
              {unreadCount > 0 ? <span className={styles.topBadge}>{unreadCount}</span> : null}
            </button>
            <button type="button" className={styles.topAction} title={zh ? "应用设置" : "App settings"} onClick={() => setSettingsOpen(true)}><span aria-hidden="true">⚙</span></button>
            <button type="button" className={styles.topAction} title={zh ? "搜索机器人和消息" : "Search bots and messages"} onClick={() => searchRef.current?.focus()}><span aria-hidden="true">⌕</span></button>
            <button type="button" className={styles.topAction} title={zh ? "新建话题" : "New thread"} onClick={() => void onNewThread()} disabled={!botId || streaming}><span aria-hidden="true">＋</span></button>
          </div>

          <label className={styles.search}>
            <span className={styles.srOnly}>{zh ? "筛选话题" : "Filter threads"}</span>
            <input
              ref={searchRef}
              className={styles.searchInput}
              value={threadQuery}
              onChange={(ev) => setThreadQuery(ev.target.value)}
              placeholder={zh ? "筛选话题" : "Filter threads"}
            />
          </label>

          <div className={styles.sideSection}>
            <div className={styles.sideSectionHead}>
              <span className={styles.sideSectionLabel}>{zh ? "收件箱" : "Inbox"}</span>
              <button type="button" className={styles.textButton} onClick={() => void loadBots()} disabled={loadingBots}>
                {loadingBots ? (zh ? "刷新中" : "Refreshing") : (zh ? "刷新" : "Refresh")}
              </button>
            </div>
            <div className={styles.botList}>
              {bots.map((bot) => (
                <button
                  key={bot.id}
                  type="button"
                  className={styles.botItem}
                  data-active={bot.id === botId ? "true" : "false"}
                  onClick={() => selectBot(bot)}
                >
                  <span className={styles.botAvatar} aria-hidden="true">{(bot.name || "M").slice(0, 1)}</span>
                  <span className={styles.botCopy}>
                    <span className={styles.botName}>{bot.name || "Bot"}</span>
                    <span className={styles.botMeta}>
                      {bot.busy
                        ? (bot.activity && bot.activity !== "idle" ? bot.activity : (zh ? "忙碌" : "Busy"))
                        : (bot.activity && bot.activity !== "idle" ? bot.activity : (zh ? "空闲" : "Idle"))}
                    </span>
                  </span>
                  <span className={styles.presenceDot} data-presence={bot.busy ? "working" : "idle"} />
                </button>
              ))}
              {!bots.length ? (
                loadingBots ? (
                  <p className={styles.emptyHint} role="status" aria-live="polite" aria-busy="true">
                    {zh ? "正在同步机器人…" : "Loading bots…"}
                  </p>
                ) : botsLoadError ? (
                  <div className={styles.emptyHint} role="alert">
                    <span>{zh ? "机器人列表加载失败" : "Could not load bots"}</span>
                    <button type="button" className={styles.textButton} onClick={() => void loadBots()}>
                      {zh ? "重试" : "Retry"}
                    </button>
                  </div>
                ) : (
                  <p className={styles.emptyHint}>{zh ? "还没有机器人" : "No bots yet"}</p>
                )
              ) : null}
            </div>
          </div>

          <div className={`${styles.sideSection} ${styles.sideSectionGrow}`}>
            <div className={styles.sideSectionHead}>
              <span className={styles.sideSectionLabel}>{zh ? "话题" : "Threads"}</span>
              <button type="button" className={styles.textButton} onClick={() => void onNewThread()} disabled={!botId || streaming}>
                {zh ? "新建" : "New"}
              </button>
            </div>
            <div className={styles.threadList}>
              {shownThreads.map((t) => (
                <div key={t.threadId} className={styles.threadRow} data-active={t.threadId === threadId ? "true" : "false"}>
                  <button
                    type="button"
                    className={styles.threadItem}
                    data-active={t.threadId === threadId ? "true" : "false"}
                    data-busy={t.busy ? "true" : "false"}
                    aria-current={t.threadId === threadId ? "true" : undefined}
                    onClick={() => {
                      setThreadId(t.threadId);
                      setNavOpen(false);
                    }}
                  >
                    <span className={styles.threadCopy}>
                      <span className={styles.threadTitle}>{t.pinned ? "📌 " : ""}{t.title || (zh ? "未命名话题" : "Untitled")}</span>
                      <span className={styles.threadMeta}>{threadMeta(t, zh)}</span>
                    </span>
                    {t.unread && t.threadId !== threadId ? <span className={styles.unreadDot} aria-label={zh ? "未读" : "Unread"} /> : null}
                  </button>
                  <details className={styles.threadMenu}>
                    <summary aria-label={zh ? "话题操作" : "Thread actions"}>⋯</summary>
                    <div className={styles.threadMenuPanel} role="menu">
                      <button type="button" role="menuitem" onClick={() => void renameThread(t.threadId, t.title)}>{zh ? "重命名" : "Rename"}</button>
                      <button type="button" role="menuitem" onClick={() => void togglePinThread(t.threadId, Boolean(t.pinned))}>{t.pinned ? (zh ? "取消置顶" : "Unpin") : (zh ? "置顶" : "Pin")}</button>
                      <button type="button" role="menuitem" onClick={() => void archiveThread(t.threadId)}>{zh ? "归档" : "Archive"}</button>
                      <button type="button" role="menuitem" onClick={() => void deleteThreadHard(t.threadId)}>{zh ? "硬删除" : "Delete"}</button>
                    </div>
                  </details>
                </div>
              ))}
              {!shownThreads.length ? (
                loadingBots ? (
                  <p className={styles.emptyHint} role="status" aria-live="polite" aria-busy="true">
                    {zh ? "正在同步话题…" : "Loading threads…"}
                  </p>
                ) : botsLoadError ? (
                  <div className={styles.emptyHint} role="alert">
                    <span>{zh ? "话题加载失败" : "Could not load threads"}</span>
                    <button type="button" className={styles.textButton} onClick={() => void loadBots()}>
                      {zh ? "重试" : "Retry"}
                    </button>
                  </div>
                ) : (
                  <p className={styles.emptyHint} role="status" aria-live="polite">
                    {threadQuery.trim()
                      ? (zh ? "没有匹配的话题" : "No matching threads")
                      : (zh ? "暂无话题" : "No threads")}
                  </p>
                )
              ) : botsLoadError ? (
                <p className={styles.emptyHint} role="alert">{zh ? "话题刷新失败，显示的是上次加载结果。" : "Thread refresh failed; showing the last loaded list."}</p>
              ) : null}
            </div>
          </div>

          <div className={styles.sideFoot}>
            <button type="button" className={styles.footButton} data-active={panelOpen && rail === "data" ? "true" : "false"} onClick={() => openRail("data")}>{zh ? "工具" : "Tools"}</button>
            <button type="button" className={styles.footButton} data-active={panelOpen && rail === "routines" ? "true" : "false"} onClick={() => openRail("routines")}>{zh ? "自动化" : "Automate"}</button>
            <button type="button" className={styles.footButton} data-active={settingsOpen ? "true" : "false"} onClick={() => setSettingsOpen(true)}>{zh ? "设置" : "Settings"}</button>
          </div>
          <div className={styles.userChip} title={zh ? "当前用户" : "Current user"}>
            <span className={styles.userChipDot} aria-hidden="true">姚</span>
            <span>{zh ? "姚星宇" : "Xingyu"}</span>
          </div>
        </aside>

        <section className={styles.stage} aria-label={zh ? "对话" : "Chat"}>
          <header className={styles.stageHeader}>
            <div className={styles.stageTitleBlock}>
              <button
                type="button"
                className={`${styles.iconButton} ${styles.navToggle}`}
                aria-label={zh ? "打开话题列表" : "Open threads"}
                aria-expanded={navOpen}
                onClick={() => {
                  setNavOpen((open) => !open);
                  setPanelOpen(false);
                }}
              >
                <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
                  <path d="M2.5 4.25h11M2.5 8h11M2.5 11.75h11" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
                </svg>
              </button>
              <span className={styles.avatar} aria-hidden="true">{(displayBotName(activeBot?.name).slice(0, 1) || "f").slice(0, 1)}</span>
              <div className={styles.stageTitleWrap}>
                <p className={styles.stageKicker}>{displayBotName(activeBot?.name)}</p>
                <h1 className={styles.stageTitle}>{activeTitle}</h1>
              </div>
            </div>
            <div className={styles.stageActions}>
              <span className={styles.statusPill} data-presence={presence}>
                <span className={styles.presenceDot} data-presence={presence} />
                {presenceLabel}
              </span>
              <label className={styles.modelChip} title={zh ? "选择模型" : "Choose model"}>
                <span className={styles.srOnly}>{zh ? "模型" : "Model"}</span>
                <select
                  className={styles.modelSelect}
                  value={modelFamily}
                  disabled={modelBusy || streaming}
                  aria-label={zh ? "选择模型" : "Choose model"}
                  onChange={(ev) => void applyModelFamily(ev.target.value as ModelFamily)}
                >
                  <option value="deepseek">DeepSeek</option>
                  <option value="gpt">GPT</option>
                </select>
              </label>
              <button
                type="button"
                className={styles.textButton}
                aria-pressed={panelOpen}
                onClick={() => {
                  setPanelOpen((open) => !open);
                  setNavOpen(false);
                }}
              >
                {panelOpen ? (zh ? "收起" : "Hide") : (zh ? "面板" : "Panel")}
              </button>
            </div>
          </header>

          <div
            ref={transcriptRef}
            className={styles.transcript}
            aria-live="polite"
            onScroll={(event) => {
              shouldFollowTranscriptRef.current = isTranscriptNearBottom(event.currentTarget);
            }}
          >
            <div className={styles.stream}>
              {bootstrapping || loadingMsgs ? (
                <div className={styles.skeleton} aria-busy="true">
                  <span />
                  <span />
                  <span />
                  <p>{zh ? "正在同步话题…" : "Syncing this thread…"}</p>
                </div>
              ) : null}

              {!bootstrapping && !loadingMsgs && ((historyTotal > HISTORY_WINDOW && !showAllHistory) || hasMoreHistory) ? (
                <button
                  type="button"
                  className={styles.loadEarlier}
                  disabled={loadingOlder}
                  onClick={() => {
                    if (hasMoreHistory) void loadOlderMessages();
                    else setShowAllHistory(true);
                  }}
                >
                  {loadingOlder
                    ? (zh ? "加载更早消息…" : "Loading earlier…")
                    : hasMoreHistory
                      ? (zh ? "加载更早消息" : "Load earlier messages")
                      : (zh ? `显示更早的 ${historyTotal - HISTORY_WINDOW} 条` : `Show earlier ${historyTotal - HISTORY_WINDOW}`)}
                </button>
              ) : null}

              {!bootstrapping && !loadingMsgs && !messages.length ? (
                <div className={styles.emptyState}>
                  <p className={styles.emptyKicker}>FINANCIAL-BOT</p>
                  <h2>{zh ? `和 ${displayBotName(activeBot?.name)} 开始` : `Start with ${displayBotName(activeBot?.name)}`}</h2>
                  <p>
                    {zh
                      ? "回复会逐字出现。这一轮还在跑时，新消息进入队列；停止会中断当前回合。"
                      : "Replies stream in. Messages sent mid-turn join the queue. Stop interrupts the current turn."}
                  </p>
                </div>
              ) : null}

              {(() => {
                const blocks: Array<{ kind: "activity"; id: string; msgs: ChatMsg[] } | { kind: "msg"; msg: ChatMsg }> = [];
                for (const m of visibleMessages) {
                  if (m.kind === "activity") {
                    const last = blocks[blocks.length - 1];
                    if (last && last.kind === "activity") last.msgs.push(m);
                    else blocks.push({ kind: "activity", id: m.id, msgs: [m] });
                  } else {
                    blocks.push({ kind: "msg", msg: m });
                  }
                }
                const answerVisible = hasVisibleAnswer(visibleMessages);
                const liveText = (() => {
                  if (!liveStatus) return null;
                  if (answerVisible && isGenericThinking(liveStatus, zh)) return null;
                  return liveStatus;
                })();
                return (
                  <>
                    {blocks.map((block) => {
                      if (block.kind === "activity") {
                        return (
                          <ActivityCapsule key={block.id} msgs={block.msgs} zh={zh} />
                        );
                      }
                      const m = block.msg;
                      if (m.kind === "secret" || m.secret) {
                        return (
                          <SecretRequestRow
                            key={m.id}
                            msg={m}
                            botId={botId}
                            botName={displayBotName(activeBot?.name)}
                            threadId={threadId}
                            zh={zh}
                          />
                        );
                      }
                      if (m.kind === "signin" || m.signIn) {
                        return <SignInRequestRow key={m.id} msg={m} botId={botId} threadId={threadId} zh={zh} />;
                      }
                      if (m.kind === "connector" || m.connector) {
                        return <ConnectorRequestRow key={m.id} msg={m} botId={botId} threadId={threadId} zh={zh} />;
                      }
                      if (m.kind === "widget" || m.widget) {
                        return <WidgetRequestRow key={m.id} msg={m} botId={botId} threadId={threadId} zh={zh} />;
                      }
                      if (m.kind === "app" || m.app) {
                        return <AppRequestRow key={m.id} msg={m} botId={botId} threadId={threadId} zh={zh} />;
                      }
                      if (m.kind === "screen" || m.png) {
                        return <ScreenFrameRow key={m.id} msg={m} threadId={threadId} zh={zh} />;
                      }
                      if (m.kind === "routine.run" || m.routineRun) {
                        return <RoutineRunRow key={m.id} msg={m} zh={zh} />;
                      }
                      if (m.kind === "goal.run" || m.goalRun) {
                        return <GoalRunRow key={m.id} msg={m} zh={zh} />;
                      }
                      if (m.kind === "compaction" || m.compaction) {
                        return <CompactionRow key={m.id} msg={m} zh={zh} />;
                      }
                      if (m.kind === "call" || m.call) {
                        return <CallRow key={m.id} msg={m} zh={zh} />;
                      }
                      if (m.kind === "digest" || m.digest) {
                        return <DigestRow key={m.id} msg={m} zh={zh} />;
                      }
                      if (m.helperIntro || m.kind === "options" || m.cardRequestId) {
                        return (
                          <div key={m.id}>
                            <OptionsRow msg={m} botName={displayBotName(activeBot?.name)} botId={botId} threadId={threadId} zh={zh} />
                            <WaitCardExtra msg={m} botId={botId} threadId={threadId} zh={zh} onFollowUp={(t) => { setInput(t); }} />
                          </div>
                        );
                      }
                      const mine = m.role === "user";
                      const provenanceLabel = modelProvenanceLabel(m.modelProvenance, zh);
                      return (
                        <div key={m.id} className={styles.row} data-role={m.role}>
                          {!mine ? <div className={styles.avatar} aria-hidden="true">{(displayBotName(activeBot?.name).slice(0, 1) || "f").slice(0, 1)}</div> : <div className={styles.avatarSpacer} />}
                          <div className={`${styles.bubble} ${mine ? styles.bubbleUser : styles.bubbleBot} ${m.pending ? styles.bubblePending : ""}`}>
                            {(m.steered || m.queued) ? <div className={styles.tag}>{m.steered ? (zh ? "插话" : "Steered") : (zh ? "排队" : "Queued")}</div> : null}
                            {!mine && !m.pending && provenanceLabel ? (
                              <div className={styles.tag} title={zh ? "仅显示运行时明确回传的模型信息" : "Only model metadata explicitly returned by the runtime is shown"}>
                                {provenanceLabel}
                              </div>
                            ) : null}
                            <MessageBody msg={m} botName={displayBotName(activeBot?.name)}  zh={zh} />
                          </div>
                        </div>
                      );
                    })}
                    {liveText ? <LiveLine text={liveText} zh={zh} /> : null}
                  </>
                );
              })()}
              {turnEndNotice ? (
                <div className={styles.turnEnd} role="status" aria-live="polite">{turnEndNotice}</div>
              ) : null}
              <div ref={bottomRef} />
            </div>
          </div>

          <div className={styles.composerWrap} role="form">
            {pendingFiles.length ? (
              <div className={styles.attachChips}>
                {pendingFiles.map((f, i) => (
                  <span key={f.path} className={styles.attachChip}>
                    <span>{f.name}</span>
                    <button type="button" aria-label={zh ? "移除附件" : "Remove attachment"} onClick={() => setPending(pendingFilesRef.current.filter((_, j) => j !== i))}>✕</button>
                  </span>
                ))}
              </div>
            ) : null}
            {queued.length ? (
              <div className={styles.queue} aria-label={zh ? "排队中的消息（回合未完时发送会进队）" : "Queued messages (sends enqueue while a turn runs)"}>
                <div className={styles.queueHead}>
                  <span>{zh ? "排队" : "Queue"}</span>
                  <span>{queued.length}</span>
                </div>
                {queued.map((q, idx) => (
                  <div key={q.id} className={styles.queueItem}>
                    <span className={styles.queueIdx} aria-hidden="true">{idx + 1}</span>
                    <span
                      className={styles.queueText}
                      title={zh ? "双击编辑排队内容" : "Double-click to edit queued text"}
                      onDoubleClick={() => void editQueued(q.id)}
                      onKeyDown={(ev) => {
                        if (ev.key === "Enter" || ev.key === " ") {
                          ev.preventDefault();
                          void editQueued(q.id);
                        }
                      }}
                      role="button"
                      tabIndex={0}
                      aria-label={zh ? `排队消息 ${idx + 1}，按 Enter 编辑` : `Queued message ${idx + 1}, press Enter to edit`}
                    >{q.text}</span>
                    <button
                      type="button"
                      className={styles.queueEdit}
                      aria-label={zh ? `编辑排队消息 ${idx + 1}` : `Edit queued message ${idx + 1}`}
                      onClick={() => void editQueued(q.id)}
                    >{zh ? "编辑" : "Edit"}</button>
                    <button type="button" className={styles.queueRemove} aria-label={zh ? `移除排队消息 ${idx + 1}` : `Remove queued message ${idx + 1}`} onClick={() => void removeQueued(q.id)}>×</button>
                  </div>
                ))}
              </div>
            ) : null}
            <div className={styles.composerTools} aria-hidden="true">
              <input ref={fileInputRef} type="file" multiple hidden onChange={(e) => { void attachFiles(e.currentTarget.files); e.currentTarget.value = ""; }} />
              <button type="button" className={styles.composerToolBtn} tabIndex={-1} disabled={streaming} title={zh ? "附件（图片）" : "Attach image"} onClick={() => fileInputRef.current?.click()}>
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M7.2 12.4 11.6 8a2.6 2.6 0 0 0-3.7-3.7L4.2 8a1.5 1.5 0 0 0 2.1 2.1l3.2-3.2" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/></svg>
              </button>
              <button type="button" className={styles.composerToolBtn} tabIndex={-1} title={zh ? "听写" : "Dictation"} disabled={streaming} data-active={dictating ? "true" : "false"} onClick={() => void toggleDictation()}>
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><rect x="6" y="2.2" width="4" height="7.4" rx="2" stroke="currentColor" strokeWidth="1.3"/><path d="M3.6 7.6a4.4 4.4 0 0 0 8.8 0M8 12v1.8" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/></svg>
              </button>
              <button type="button" className={styles.composerToolBtn} tabIndex={-1} title={zh ? "清空" : "Clear"} onClick={() => setInput("")}>
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M4 5.5h8M6.2 5.5V4.4c0-.5.4-.9.9-.9h1.8c.5 0 .9.4.9.9v1.1M5.2 5.5l.5 6.2c0 .5.4.8.9.8h3c.5 0 .8-.3.9-.8l.5-6.2" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/></svg>
              </button>
              <button type="button" className={styles.composerToolBtn} tabIndex={-1} title={zh ? "目标" : "Target"} onClick={() => openRail("data")}>
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="5.2" stroke="currentColor" strokeWidth="1.3"/><circle cx="8" cy="8" r="2" stroke="currentColor" strokeWidth="1.3"/><path d="M8 1.5v1.6M8 12.9v1.6M1.5 8h1.6M12.9 8h1.6" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/></svg>
              </button>
            </div>
            <div className={styles.composer}>
              <textarea
                ref={composerRef}
                value={input}
                onChange={(ev) => setInput(ev.target.value)}
                placeholder={
                  streaming
                    ? (zh ? "这一轮还在跑，发送后会排队" : "This turn is running — send to queue")
                    : (zh ? `给 ${displayBotName(activeBot?.name)} 发消息` : `Message ${displayBotName(activeBot?.name)}`)
                }
                rows={1}
                disabled={!threadId || bootstrapping}
                onCompositionStart={() => { composingRef.current = true; }}
                onCompositionEnd={() => { composingRef.current = false; imeJustEndedRef.current = Date.now(); }}
                onKeyDown={(ev) => {
                  const ne = ev.nativeEvent as KeyboardEvent;
                  if (ev.key === "Enter" && !ev.shiftKey) {
                    const composing = composingRef.current || ne.isComposing || ev.keyCode === 229 || (ne as any).keyCode === 229;
                    if (composing) {
                      return;
                    }
                    ev.preventDefault();
                    if (Date.now() - imeJustEndedRef.current < 120) {
                      return;
                    }
                    onSubmit();
                  }
                }}
                aria-label={
                  streaming
                    ? (zh ? "正在输出 · 继续输入会排队" : "Streaming · extra sends queue")
                    : (zh ? "Enter 发送 · Shift+Enter 换行" : "Enter to send · Shift+Enter for a new line")
                }
              />
              <div className={styles.composerPillActions}>
                {streaming ? (
                  <button type="button" className={styles.stopBtn} onClick={onStop}>
                    {zh ? "停止" : "Stop"}
                  </button>
                ) : null}
                <button type="button" className={styles.sendBtn} disabled={(!input.trim() && pendingFiles.length === 0) || !threadId || bootstrapping} onClick={() => onSubmit()}>
                  {streaming ? (zh ? "排队" : "Queue") : (zh ? "发送" : "Send")}
                </button>
              </div>
              <div className={styles.composerRow} aria-hidden="true">
                <span className={styles.composerHint} />
                <div className={styles.composerActions} />
              </div>
            </div>
          </div>
        </section>

        <aside className={styles.rail} aria-label={zh ? "功能板块" : "Panels"}>
          <div className={styles.tabs} role="tablist">
            {([
              ["screen", zh ? "屏幕" : "Screen"],
              ["routines", zh ? "例行任务" : "Routines"],
              ["browser", zh ? "浏览器" : "Browser"],
              ["data", zh ? "数据" : "Data"],
            ] as Array<[RailTab, string]>).map(([tab, label]) => (
              <button
                key={tab}
                type="button"
                className={styles.tab}
                role="tab"
                aria-selected={rail === tab}
                data-active={rail === tab ? "true" : "false"}
                onClick={() => setRail(tab)}
              >
                {label}
              </button>
            ))}
          </div>
          <div className={styles.railBody}>
            {rail === "screen" ? (
              <section className={styles.desktopPanel}>
                <strong className={styles.panelTitle}>{zh ? "屏幕" : "Screen"}</strong>
                {computerStatus?.desktopReady ? (
                  <iframe
                    className={styles.desktopFrame}
                    src="/desktop-viewer#target=local%2Fshared"
                    title={zh ? "桌面" : "Desktop"}
                    allow="clipboard-read; clipboard-write"
                  />
                ) : (
                  <p className={styles.desktopNote}>
                    {computerStatus?.reason || (zh
                      ? "桌面正在准备中，稍后会自动显示。普通对话和取数不受影响。"
                      : "Preparing the desktop. It will appear here shortly.")}
                  </p>
                )}
              </section>
            ) : null}
            {rail === "routines" ? (
              <section>
                <strong className={styles.panelTitle}>{zh ? "例行任务" : "Routines"}</strong>
                <div className={styles.routineToolbar}>
                  <button type="button" className={styles.textButton} onClick={() => setRoutineFormOpen((v) => !v)}>
                    {routineFormOpen ? (zh ? "收起" : "Close") : (zh ? "新建例行" : "New routine")}
                  </button>
                </div>
                {routineFormOpen ? (
                  <div className={styles.routineForm}>
                    <input
                      value={routineName}
                      onChange={(e) => setRoutineName(e.target.value)}
                      placeholder={zh ? "名称" : "Name"}
                    />
                    <textarea
                      value={routinePrompt}
                      onChange={(e) => setRoutinePrompt(e.target.value)}
                      placeholder={zh ? "提示词 / 要做的事" : "Prompt / what to do"}
                      rows={3}
                    />
                    <button type="button" disabled={routineBusy} onClick={() => void createRoutineSimple()}>
                      {routineBusy ? (zh ? "创建中…" : "Creating…") : (zh ? "创建（每日）" : "Create (daily)")}
                    </button>
                  </div>
                ) : null}
                {!routines.length ? (
                  <p className={styles.emptyHint}>{zh ? "还没有例行。可在上方新建，或让 financial-bot 在对话里创建。" : "No routines yet. Create one above or ask financial-bot in chat."}</p>
                ) : (
                  <ul className={styles.routineList}>
                    {routines.map((r: any, idx: number) => (
                      <li key={r.id || idx} className={styles.routineItem}>
                        <div className={styles.routineMain}>
                          <strong>{r.name || r.title || (zh ? "例行" : "Routine")}</strong>
                          <span>{routineScheduleLabel(r, zh)}</span>
                        </div>
                        {r.id ? (
                          <div className={styles.routineActions}>
                            <button
                              type="button"
                              className={styles.textButton}
                              disabled={routineBusy}
                              onClick={() => void runRoutineNow(String(r.id))}
                            >
                              {zh ? "立即跑" : "Run now"}
                            </button>
                            <button
                              type="button"
                              className={styles.textButton}
                              onClick={() => void toggleRoutine(String(r.id), r.enabled !== false)}
                            >
                              {r.enabled === false ? (zh ? "恢复" : "Resume") : (zh ? "暂停" : "Pause")}
                            </button>
                            <button
                              type="button"
                              className={styles.textButton}
                              onClick={() => void deleteRoutine(String(r.id))}
                            >
                              {zh ? "删除" : "Delete"}
                            </button>
                          </div>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                )}
                {routineRuns?.length ? (
                  <>
                    <span className={styles.subhead}>{zh ? "最近运行" : "Recent runs"}</span>
                    <ul className={styles.routineList}>
                      {routineRuns.slice(0, 8).map((run: any, idx: number) => (
                        <li key={run.id || idx} className={styles.routineItem}>
                          <strong>{run.routineName || run.routineId || (zh ? "运行" : "Run")}</strong>
                          <span>{run.status || "run"}</span>
                          {run.threadId ? (
                            <button type="button" className={styles.textButton} onClick={() => { setThreadId(String(run.threadId)); }}>
                              {zh ? "打开话题" : "Open thread"}
                            </button>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  </>
                ) : null}
              </section>
            ) : null}
            {rail === "browser" ? (
              <section className={styles.browserPanel}>
                <div className={styles.browserBar}>
                  <button type="button" title={zh ? "后退" : "Back"} onClick={() => void browserAct("back")}>‹</button>
                  <button type="button" title={zh ? "前进" : "Forward"} onClick={() => void browserAct("forward")}>›</button>
                  <button type="button" title={zh ? "刷新" : "Reload"} onClick={() => void browserAct("reload")}>⟳</button>
                  <input
                    className={styles.browserAddress}
                    value={browserUrl}
                    onChange={(e) => setBrowserUrl(e.target.value)}
                    onFocus={() => { browserEditingRef.current = true; }}
                    onBlur={() => { browserEditingRef.current = false; }}
                    onKeyDown={(e) => { if (e.key === "Enter") void browserOpenUrl(); }}
                    placeholder={zh ? "输入网址" : "Enter address"}
                    aria-label={zh ? "浏览器地址" : "Browser address"}
                  />
                  <button type="button" disabled={browserBusy} onClick={() => void browserOpenUrl()}>{zh ? "前往" : "Go"}</button>
                </div>
                <div className={styles.browserTabs}>
                  {(browserState?.tabs || []).map((t, i) => (
                    <span key={t.tabId || i} className={styles.browserTab} data-active={t.active ? "true" : "false"}>
                      <button type="button" onClick={() => void browserAct("tab-select", i, t.tabId)}>{t.title || t.url || `${zh ? "标签" : "Tab"} ${i + 1}`}</button>
                      <button type="button" aria-label={zh ? "关闭标签" : "Close tab"} onClick={() => void browserAct("tab-close", i, t.tabId)}>×</button>
                    </span>
                  ))}
                  <button type="button" className={styles.browserTabNew} title={zh ? "新建标签页" : "New tab"} onClick={() => void browserAct("tab-new")}>＋</button>
                </div>
                {browserShot ? (
                  <img className={styles.browserShot} src={browserShot} alt={zh ? "浏览器画面" : "Browser view"} />
                ) : (
                  <p className={styles.emptyHint}>{zh ? "浏览器未启动或暂不可用。" : "Browser is not running or unavailable."}</p>
                )}
                {config?.browserEngine?.kind === "unavailable" && config.browserEngine.installable ? (
                  <button type="button" className={styles.textButton} disabled={engineBusy} onClick={() => void installBrowserEngine()}>
                    {engineBusy ? (zh ? "安装中…" : "Installing…") : (zh ? "安装浏览器引擎" : "Install browser engine")}
                  </button>
                ) : null}
                <details className={styles.engineDetails}>
                  <summary>{zh ? "连接与引擎" : "Connection & engine"}</summary>
                  <ul className={styles.settingsList}>
                    <li className={styles.settingsRow}><strong>{zh ? "入口" : "Entry"}</strong><span>{origin || "—"}</span></li>
                    <li className={styles.settingsRow}><strong>{zh ? "引擎" : "Engine"}</strong><span>{config?.browserEngine?.kind === "engine" ? `${zh ? "已就绪" : "ready"}${config.browserEngine.version ? ` · ${config.browserEngine.version}` : ""}` : (zh ? "未就绪" : "not ready")}</span></li>
                    <li className={styles.settingsRow}><strong>{zh ? "桌面" : "Desktop"}</strong><span>{computerStatus?.desktopReady ? (zh ? "已连接" : "connected") : (computerStatus?.status || (zh ? "未连接" : "off"))}</span></li>
                  </ul>
                </details>
              </section>
            ) : null}
            {rail === "data" ? (
              <section>
                <strong className={styles.panelTitle}>{zh ? "数据 / 记忆" : "Data / Memory"}</strong>
                <pre className={styles.prose}>{memoryText || (zh ? "还没有记忆。" : "No memory yet.")}</pre>
                <span className={styles.subhead}>{zh ? "技能" : "Skills"}</span>
                {cues.skills.length ? (
                  <ul className={styles.skillList}>
                    {cues.skills.map((name) => <li key={name}>{name}</li>)}
                  </ul>
                ) : (
                  <p className={styles.emptyHint}>{zh ? "代理未上报技能。" : "No skills reported by agent."}</p>
                )}
              </section>
            ) : null}
          </div>
        </aside>
      </div>
      {settingsOpen ? (
        <div className={styles.modalBackdrop} onClick={() => setSettingsOpen(false)}>
          <div className={styles.modal} role="dialog" aria-modal="true" aria-label={zh ? "设置" : "Settings"} onClick={(e) => e.stopPropagation()}>
            <div className={styles.modalHead}>
              <strong>{zh ? "设置" : "Settings"}</strong>
              <button type="button" className={styles.iconButton} aria-label={zh ? "关闭" : "Close"} onClick={() => setSettingsOpen(false)}>✕</button>
            </div>
            <ul className={styles.settingsList}>
              <li className={styles.settingsRow}><strong>{zh ? "账号" : "Account"}</strong><span>{zh ? "姚星宇" : "Xingyu"}</span></li>
              <li className={styles.settingsRow}><strong>{zh ? "模型" : "Model"}</strong><span>{modelLabel}</span></li>
              <li className={styles.settingsRow}><strong>{zh ? "连接" : "Link"}</strong><span>{health?.ok ? (zh ? "正常" : "ok") : (zh ? "不可用" : "down")}</span></li>
              <li className={styles.settingsRow}><strong>{zh ? "入口" : "Entry"}</strong><span>{origin || "—"}</span></li>
              <li className={styles.settingsRow}><strong>{zh ? "桌面" : "Desktop"}</strong><span>{computerStatus?.desktopReady ? (zh ? "已连接" : "connected") : (computerStatus?.status || (zh ? "未连接" : "off"))}</span></li>
              <li className={styles.settingsRow}><strong>{zh ? "语言" : "Language"}</strong><span className={styles.langSwitch}>
                <button type="button" data-active={zh ? "true" : "false"} onClick={() => switchLang("zh")}>中文</button>
                <button type="button" data-active={!zh ? "true" : "false"} onClick={() => switchLang("en")}>English</button>
              </span></li>
            </ul>
            <div className={styles.modalActions}>
              <button type="button" className={styles.textButton} onClick={() => { setSettingsOpen(false); openRail("screen"); }}>{zh ? "打开屏幕" : "Open screen"}</button>
              <button type="button" className={styles.textButton} onClick={() => { setSettingsOpen(false); openRail("data"); }}>{zh ? "记忆与技能" : "Memory & skills"}</button>
            </div>
          </div>
        </div>
      ) : null}
      <button
        type="button"
        className={styles.backdrop}
        aria-label={zh ? "关闭侧栏" : "Close sidebar"}
        onClick={() => {
          setNavOpen(false);
          setPanelOpen(false);
        }}
      />
    </div>
  );
}
