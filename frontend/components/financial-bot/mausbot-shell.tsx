/* eslint-disable @typescript-eslint/no-explicit-any -- OMB SSE/wire payloads are dynamic */
"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import styles from "./financial-bot.module.css";

type OmbTask = {
  threadId: string;
  title?: string;
  busy?: boolean;
  activity?: string;
  unread?: boolean;
  updatedAt?: number;
  lastThreadOrderAt?: number;
};

type OmbBot = {
  id: string;
  name?: string;
  title?: string;
  busy?: boolean;
  activity?: string;
  mainThreadId?: string;
  threadId?: string;
  modelSelection?: { instanceId?: string; model?: string };
  tasks?: OmbTask[];
};

type MsgAttachment = {
  kind: string;
  path: string;
  mime?: string;
  name?: string;
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

type ChatMsg = {
  id: string;
  role: "user" | "assistant" | "system";
  kind?: string;
  text: string;
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
  waitLine?: string;
};

type RailTab = "memory" | "routines" | "settings";
type QueuedPrompt = { id: string; text: string; local?: boolean };

const WEB_THREAD_KEY = "mausbot.webThreadId.v2";
const HISTORY_WINDOW = 36;

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
  return !t || t === "probe" || t.includes("probe") || t === "hi" || t === "ping";
}

function attachmentBasename(path: string): string {
  const clean = String(path || "").split(/[?#]/, 1)[0] || "";
  const parts = clean.split(/[/\\]/);
  return parts[parts.length - 1] || clean;
}

function attachmentProxyUrl(path: string): string | null {
  const name = attachmentBasename(path);
  if (!/^[\w.-]+$/.test(name)) return null;
  return `/api/mausbot/attachments/${encodeURIComponent(name)}`;
}

function mapAttachments(raw: unknown): MsgAttachment[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  const out: MsgAttachment[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const a = item as Record<string, unknown>;
    if (!a.kind || !a.path) continue;
    out.push({
      kind: String(a.kind),
      path: String(a.path),
      mime: a.mime ? String(a.mime) : undefined,
      name: a.name ? String(a.name) : undefined,
    });
  }
  return out.length ? out : undefined;
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
  if (!title && !subtitle && !options.length && !c.requestId) return undefined;
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

function wireToChatMsg(m: any): ChatMsg | null {
  if (!m || !(m.role === "user" || m.role === "bot" || m.role === "assistant")) return null;
  if (m.kind === "digest") return null;
  const text = String(m.text || "");
  if (text.startsWith("[digest]")) return null;
  const tool = mapTool(m.tool);
  const card = mapCard(m.card);
  const attachments = mapAttachments(m.attachments);
  const kind = m.kind ? String(m.kind) : "text";
  // Keep activity rows even with empty text when tool is present.
  if (!text.trim() && !(attachments && attachments.length) && kind !== "activity" && !tool && kind !== "options") {
    return null;
  }
  return {
    id: String(m.id),
    role: m.role === "user" ? "user" : "assistant",
    kind,
    text,
    at: m.at,
    helperIntro: Boolean(m.helperIntro),
    attachments,
    tool,
    steered: m.steered != null ? Boolean(m.steered) : undefined,
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
    waitLine: waitLineText(m.waitLine),
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
    const label = ev.title || ev.summary || ev.tool || ev.itemType || (zh ? "步骤" : "step");
    return zh ? `执行中：${label}` : `Running: ${label}`;
  }
  if (type === "item.output") {
    const tail = String(ev.text || "").trim().split(/\n/).filter(Boolean).slice(-1)[0] || "";
    if (!tail) return null;
    return zh ? `输出：${tail.slice(0, 120)}` : `Output: ${tail.slice(0, 120)}`;
  }
  if (type === "item.completed" && ev.itemType === "tool") {
    const ok = ev.ok !== false;
    const label = ev.title || ev.summary || (zh ? "工具" : "tool");
    return ok ? (zh ? `完成：${label}` : `Done: ${label}`) : (zh ? `失败：${label}` : `Failed: ${label}`);
  }
  if (type === "turn.progress") {
    const act = ev.activity || ev.tool || "";
    const target = ev.target || ev.file || ev.detail || "";
    const phrase = [act, target].filter(Boolean).join(" · ");
    if (!phrase) return zh ? "进行中…" : "Working…";
    return zh ? `进行中：${phrase}` : `Working: ${phrase}`;
  }
  if (type === "turn.started") return zh ? "回合开始…" : "Turn started…";
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
      /!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g,
      (_m, alt: string, url: string) => {
        let href = url;
        const attach = url.match(/\/api\/attachments\/([^/?#]+)/);
        if (attach) {
          const proxy = attachmentProxyUrl(attach[1]);
          if (proxy) href = proxy;
        } else if (url.startsWith("/api/mausbot/attachments/")) {
          href = url;
        } else if (/^https?:\/\//i.test(url)) {
          href = url;
        } else {
          const proxy = attachmentProxyUrl(url);
          if (proxy) href = proxy;
        }
        return `<img class="mausMdImg" src="${escapeHtml(href)}" alt="${escapeHtml(alt || "")}" loading="lazy" />`;
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

function MessageBody({ msg, botName }: { msg: ChatMsg; botName: string }) {
  const images = (msg.attachments || []).filter((a) => a.kind === "image");
  const html = useMemo(() => renderLightMarkdown(msg.text || ""), [msg.text]);
  return (
    <>
      {msg.text?.trim() ? (
        <div className={styles.mausBubbleMd} dangerouslySetInnerHTML={{ __html: html }} />
      ) : msg.pending ? (
        <div className={styles.mausBubbleText}>{botName}…</div>
      ) : null}
      {images.length ? (
        <div className={styles.mausAttachGallery}>
          {images.map((img, idx) => {
            const src = attachmentProxyUrl(img.path);
            if (!src) return null;
            return (
              <a key={`${msg.id}-img-${idx}`} href={src} target="_blank" rel="noreferrer" className={styles.mausAttachLink}>
                <img src={src} alt={img.name || "image"} className={styles.mausAttachImg} loading="lazy" />
              </a>
            );
          })}
        </div>
      ) : null}
    </>
  );
}

function ActivityRow({ msg, zh }: { msg: ChatMsg; zh: boolean }) {
  const label = msg.tool?.label || msg.tool?.name || msg.text || "activity";
  const ok = msg.tool?.ok;
  const mark = ok === true ? "✓" : ok === false ? "✗" : "…";
  const hasDetails = Boolean(msg.tool?.summary || msg.tool?.input || msg.tool?.output || msg.tool?.error || msg.waitLine);
  const status = ok === true ? (zh ? "已完成" : "Done") : ok === false ? (zh ? "失败" : "Failed") : (zh ? "状态未知" : "Unknown");
  const details = (
    <div className={styles.mausActivityDetails}>
      <span className={styles.mausActivityStatus} data-ok={ok === true ? "1" : ok === false ? "0" : ""}>{status}</span>
      {msg.tool?.summary ? <p>{msg.tool.summary}</p> : null}
      {msg.waitLine ? <p>{msg.waitLine}</p> : null}
      {msg.tool?.input ? <><strong>{zh ? "输入" : "Input"}</strong><pre dir="ltr">{msg.tool.input}</pre></> : null}
      {msg.tool?.output ? <><strong>{zh ? "输出" : "Output"}</strong><pre dir="ltr">{msg.tool.output}</pre></> : null}
      {msg.tool?.error ? <><strong>{zh ? "错误" : "Error"}</strong><pre dir="ltr">{msg.tool.error}</pre></> : null}
      {msg.tool?.clipped ? <span className={styles.mausActivityPartial}>{zh ? "内容已截断" : "Clipped"}</span> : null}
    </div>
  );
  if (hasDetails) {
    return (
      <details className={styles.mausActivityDisclosure}>
        <summary className={styles.mausActivitySummary}>
          <span className={styles.mausActivityIcon} aria-hidden="true">⚙</span>
          <span className={styles.mausActivityLabel}>{label}</span>
          {ok !== undefined && ok !== null ? <span className={styles.mausActivityMark} data-ok={ok ? "1" : "0"}>{mark}</span> : null}
          {msg.tool?.partial ? <span className={styles.mausActivityPartial}>partial</span> : null}
          <span className={styles.mausActivityChevron} aria-hidden="true">›</span>
        </summary>
        {details}
      </details>
    );
  }
  return (
    <div className={styles.mausActivityRow} title={msg.waitLine || msg.text || label}>
      <span className={styles.mausActivityIcon} aria-hidden="true">⚙</span>
      <span className={styles.mausActivityLabel}>{label}</span>
      {ok !== undefined && ok !== null ? <span className={styles.mausActivityMark} data-ok={ok ? "1" : "0"}>{mark}</span> : null}
      {msg.tool?.partial ? <span className={styles.mausActivityPartial}>partial</span> : null}
    </div>
  );
}

function OptionsRow({ msg, botName, threadId, zh }: { msg: ChatMsg; botName: string; threadId: string | null; zh: boolean }) {
  const [decision, setDecision] = useState(msg.cardAnswered || (msg.cardDismissed ? "dismissed" : ""));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const inflightRef = useRef(false);
  // Upstream cards usually set requestType; fall back from shape only — same respond API.
  const requestType =
    msg.cardRequestType ||
    (msg.cardOptions?.length ? "question" : msg.cardRequestId ? "permission" : undefined);
  const isOpen = Boolean(msg.cardRequestId) && !decision && !msg.cardExpired && !msg.cardStopped;
  const canRespond = Boolean(msg.cardRequestId && threadId && isOpen && !pending && !msg.cardHeld);

  useEffect(() => {
    setDecision(msg.cardAnswered || (msg.cardDismissed ? "dismissed" : ""));
  }, [msg.cardAnswered, msg.cardDismissed]);

  async function respond(behavior: "allow" | "deny" | "answer", message?: string) {
    if (!msg.cardRequestId || !threadId || pending || inflightRef.current) return;
    if (behavior === "answer" && !(message || "").trim()) return;
    inflightRef.current = true;
    setPending(true);
    setError("");
    try {
      const res = await fetch(`/api/mausbot/threads/${encodeURIComponent(threadId)}/respond`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ requestId: msg.cardRequestId, behavior, ...(message ? { message } : {}) }),
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

  const settledLabel = decision === "allow"
    ? (zh ? "已允许" : "Allowed")
    : decision === "deny"
      ? (zh ? "已拒绝" : "Denied")
      : decision === "dismissed"
        ? (zh ? "已取消" : "Dismissed")
        : decision
          ? (zh ? `已回复：${decision}` : `Answered: ${decision}`)
          : "";
  return (
    <div className={styles.mausCardMsg} role="group" aria-label={zh ? "待确认请求" : "Pending request"}>
      <strong>{botName}</strong>
      {msg.cardTitle ? <p><em>{msg.cardTitle}</em></p> : null}
      {msg.text?.trim() ? <p>{msg.text}</p> : null}
      {msg.cardTool ? <p className={styles.mausApprovalTool}>{zh ? "请求工具：" : "Requested tool: "}{msg.cardTool}</p> : null}
      {msg.cardSubtitle ? <p className={styles.mausApprovalSubtitle}>{msg.cardSubtitle}</p> : null}
      {msg.cardHeld ? <p className={styles.mausApprovalHeld}>{msg.cardHeld}</p> : null}
      {settledLabel ? <p className={styles.mausApprovalStatus} role="status">{settledLabel}</p> : null}
      {msg.cardExpired ? <p className={styles.mausApprovalHeld} role="status">{zh ? "此请求已过期，不能再处理" : "This request expired and can no longer be answered"}</p> : null}
      {msg.cardStopped ? <p className={styles.mausApprovalHeld} role="status">{zh ? "相关回合已停止" : "The related turn was stopped"}</p> : null}
      {isOpen && !msg.cardHeld && requestType === "permission" ? (
        <div className={styles.mausApprovalActions} role="group" aria-label={zh ? "审批操作" : "Approval actions"}>
          <button type="button" disabled={!canRespond} aria-busy={pending || undefined} onClick={() => void respond("allow")}>{pending ? (zh ? "提交中…" : "Sending…") : (zh ? "允许" : "Allow")}</button>
          <button type="button" disabled={!canRespond} aria-busy={pending || undefined} onClick={() => void respond("deny")}>{zh ? "拒绝" : "Deny"}</button>
        </div>
      ) : null}
      {isOpen && !msg.cardHeld && requestType === "question" && msg.cardOptions?.length ? (
        <div className={styles.mausApprovalActions} role="group" aria-label={zh ? "问题选项" : "Question options"}>
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
      {error ? <p className={styles.mausApprovalError} role="alert">{error}</p> : null}
      {msg.cardOptions?.length && !(isOpen && requestType === "question") ? (
        <ul className={styles.mausOptionsList}>
          {msg.cardOptions.map((opt, i) => (
            <li key={`${msg.id}-opt-${i}`}>{opt}</li>
          ))}
        </ul>
      ) : null}
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
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [liveStatus, setLiveStatus] = useState<string | null>(null);
  const [turnEndNotice, setTurnEndNotice] = useState<string | null>(null);
  const [queued, setQueued] = useState<QueuedPrompt[]>([]);
  const [loadingBots, setLoadingBots] = useState(true);
  const [loadingMsgs, setLoadingMsgs] = useState(false);
  const [bootstrapping, setBootstrapping] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rail, setRail] = useState<RailTab>("memory");
  const [memoryText, setMemoryText] = useState("");
  const [routines, setRoutines] = useState<any[]>([]);
  const [routineRuns, setRoutineRuns] = useState<any[]>([]);
  const [config, setConfig] = useState<any>(null);
  const [health, setHealth] = useState<any>(null);
  const [panelOpen, setPanelOpen] = useState(true);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const bootOnce = useRef(false);
  const streamingRef = useRef(false);
  const queueRef = useRef<QueuedPrompt[]>([]);
  const queueOpsRef = useRef<Set<string>>(new Set());
  const abortRef = useRef<AbortController | null>(null);
  const botIdRef = useRef<string | null>(null);
  const threadIdRef = useRef<string | null>(null);
  const liveTurnIdRef = useRef<string | null>(null);
  const turnEndTimerRef = useRef<number | null>(null);
  const endNoticeShownRef = useRef(false);

  useEffect(() => {
    botIdRef.current = botId;
  }, [botId]);
  useEffect(() => {
    threadIdRef.current = threadId;
  }, [threadId]);

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
  const threads = useMemo(() => {
    const list = activeBot?.tasks || [];
    return [...list].sort(
      (a, b) => (b.lastThreadOrderAt || b.updatedAt || 0) - (a.lastThreadOrderAt || a.updatedAt || 0),
    );
  }, [activeBot]);

  const visibleMessages = useMemo(() => {
    if (showAllHistory || messages.length <= HISTORY_WINDOW) return messages;
    return messages.slice(-HISTORY_WINDOW);
  }, [messages, showAllHistory]);

  const loadBots = useCallback(async () => {
    setLoadingBots(true);
    try {
      const res = await fetch("/api/mausbot/bots", { cache: "no-store" });
      const data = await readJson(res);
      if (!res.ok) throw new Error(String(data?.detail || data?.error || "load bots failed"));
      const next = (data?.bots || []) as OmbBot[];
      setBots(next);
      return next;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return [] as OmbBot[];
    } finally {
      setLoadingBots(false);
    }
  }, []);

  const loadMessages = useCallback(async (tid: string) => {
    setLoadingMsgs(true);
    try {
      const res = await fetch(`/api/mausbot/threads/${encodeURIComponent(tid)}/messages`, { cache: "no-store" });
      const data = await readJson(res);
      if (!res.ok) throw new Error(String(data?.detail || data?.error || "load messages failed"));
      const raw = (data?.messages || []) as any[];
      const mapped: ChatMsg[] = raw
        .map((m) => wireToChatMsg(m))
        .filter((m): m is ChatMsg => Boolean(m));
      setHistoryTotal(mapped.length);
      setMessages(mapped);
      setShowAllHistory(false);
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
        fetch("/api/mausbot/ask", { cache: "no-store" }),
      ]);
      const mem = await readJson(memRes);
      const rou = await readJson(rouRes);
      const cfg = await readJson(cfgRes);
      const hel = await readJson(healthRes);
      if (memRes.ok) setMemoryText(String(mem?.text || ""));
      if (rouRes.ok) {
        setRoutines(rou?.routines || []);
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
    const bot = data?.bot as OmbBot | undefined;
    const tid = bot?.threadId || bot?.mainThreadId;
    if (!tid) throw new Error("missing new thread id");
    return tid;
  }, []);

  useEffect(() => {
    if (bootOnce.current) return;
    bootOnce.current = true;
    (async () => {
      setBootstrapping(true);
      setError(null);
      try {
        const next = await loadBots();
        const bot = next[0];
        if (!bot) {
          setError(zh ? "还没有可用的 MausBot" : "No MausBot available");
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
    window.localStorage.setItem(WEB_THREAD_KEY, threadId);
    if (!streamingRef.current) {
      void loadMessages(threadId);
    }
  }, [threadId, loadMessages]);

  useEffect(() => {
    const el = transcriptRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
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
      const localId = `local-q-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
      const chipId = `q-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
      setMessages((prev) => [
        ...prev,
        { id: localId, role: "user", kind: "text", text, at: Date.now(), steered: false, queued: true },
      ]);
      setQueued((prev) => [...prev, { id: chipId, text, local: true }]);
      try {
        const res = await fetch("/api/mausbot/message", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text, botId: bid, threadId: tid }),
          cache: "no-store",
        });
        const data = await readJson(res);
        if (!res.ok) throw new Error(String(data?.detail || data?.error || `message failed (${res.status})`));
        if (data?.message?.id) {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === localId
                ? {
                    ...m,
                    id: String(data.message.id),
                    text: String(data.message.text || m.text),
                    steered: data.message.steered != null ? Boolean(data.message.steered) : false,
                    queued: data.queued != null ? Boolean(data.queued) : true,
                  }
                : m,
            ),
          );
        }
        if (data?.queueId) {
          setQueued((prev) => prev.map((q) => (q.id === chipId ? { ...q, id: String(data.queueId), local: false } : q)));
        } else if (data?.queued === false) {
          setQueued((prev) => prev.filter((q) => q.id !== chipId));
        }
      } catch (e) {
        queueRef.current = [...queueRef.current, { id: chipId, text, local: true }];
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
      if (streamingRef.current) {
        void queueMidTurn(text);
        return;
      }

      streamingRef.current = true;
      setStreaming(true);
      setLiveStatus(zh ? "正在…" : "Working…");
      setError(null);
      endNoticeShownRef.current = false;
      liveTurnIdRef.current = null;
      const localUserId = `local-${Date.now()}`;
      const provisionalId = `stream-${Date.now()}`;
      let streamMsgId: string | null = null;
      let streamText = "";
      let stoppedLocally = false;

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

      try {
        const res = await fetch("/api/mausbot/ask/stream", {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
          body: JSON.stringify({ text, botId: bid, threadId: tid }),
          cache: "no-store",
          signal: ac.signal,
        });

        if (!res.ok || !res.body) {
          const data = await readJson(res);
          throw new Error(String(data?.detail || data?.error || `ask failed (${res.status})`));
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";

        const handleEvent = (ev: any) => {
          const kind = ev?.kind;
          if (kind === "hello" && ev.threadId && ev.threadId !== threadIdRef.current) {
            setThreadId(String(ev.threadId));
          }
          if (kind === "delta" && typeof ev.delta === "string") {
            if (ev.streamKind && ev.streamKind !== "assistant_text") return;
            streamText += ev.delta;
            const id = streamMsgId || provisionalId;
            ensureBubble(id, { text: streamText, role: "assistant" });
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
            if (msg.kind === "digest" || String(msg.text || "").startsWith("[digest]")) return;
            const mapped = wireToChatMsg({ ...msg, role: "assistant" });
            if (!mapped) return;
            const msgid = String(msg.id || streamMsgId || provisionalId);
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
                setLiveStatus(zh ? `执行中：${mapped.tool.label || mapped.tool.name}` : `Running: ${mapped.tool.label || mapped.tool.name}`);
              }
              return;
            }
            if (mapped.kind === "options") {
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
            streamMsgId = msgid;
            const nextText = String(msg.text || "");
            if (nextText && nextText.length >= streamText.length) streamText = nextText;
            ensureBubble(msgid, {
              text: streamText || nextText,
              kind: mapped.kind || "text",
              at: msg.at,
              helperIntro: Boolean(msg.helperIntro),
              attachments: mapAttachments(msg.attachments),
              tool: mapped.tool,
            });
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
            if (line) setLiveStatus(line);
            if (runtime?.type === "content.delta" && typeof runtime.delta === "string") {
              if (!runtime.streamKind || runtime.streamKind === "assistant_text") {
                streamText += runtime.delta;
                const id = streamMsgId || provisionalId;
                ensureBubble(id, { text: streamText, role: "assistant" });
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
                  setLiveStatus(zh ? `正在：${bot.activity}` : `Busy: ${bot.activity}`);
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
                id: String(it?.id || `srv-q-${idx}`),
                text: String(it?.text || "").trim(),
                local: false,
              }))
              .filter((q: QueuedPrompt) => q.text);
            const localOnly = queueRef.current.filter((q) => !chips.some((c) => c.id === q.id || c.text === q.text));
            setQueued([...chips, ...localOnly]);
          }
          if (kind === "turn") {
            if (ev.phase === "started" && ev.turnId) {
              liveTurnIdRef.current = String(ev.turnId);
              setLiveStatus(zh ? "回合开始…" : "Turn started…");
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
            setError(String(ev.error || "MausBot error"));
          }
          if (kind === "done") {
            if (Array.isArray(ev.answers) && ev.answers.length) {
              setMessages((prev) => {
                const withoutProv = prev.filter((m) => m.id !== provisionalId && !(m.pending && m.role === "assistant"));
                const known = new Set(withoutProv.map((m) => m.id));
                const extras: ChatMsg[] = [];
                for (const a of ev.answers) {
                  const id = String(a.id || "");
                  if (!id) continue;
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
        if (queueRef.current.length) {
          drainQueue();
        } else {
          setQueued((prev) => prev.filter((q) => queueRef.current.some((lq) => lq.id === q.id)));
        }
      }
    },
    [drainQueue, loadBots, markBotIdle, queueMidTurn, showTurnEnd, zh],
  );

  useEffect(() => {
    runAskRef.current = runAsk;
  }, [runAsk]);

  async function onNewThread() {
    if (!botId || streaming) return;
    setError(null);
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

  function onSubmit(e?: FormEvent) {
    e?.preventDefault();
    const text = input.trim();
    if (!text || !botId || !threadId || bootstrapping) return;
    setInput("");
    if (streamingRef.current) {
      void queueMidTurn(text);
      return;
    }
    void runAsk(text);
  }

  async function onStop() {
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

  const modelLabel = activeBot?.modelSelection
    ? `${activeBot.modelSelection.instanceId || "model"} / ${activeBot.modelSelection.model || "?"}`
    : "—";

  const activeTitle =
    threads.find((t) => t.threadId === threadId)?.title ||
    (zh ? "网页对话" : "Web chat");

  return (
    <div
      className={`${styles.shell} ${styles.mausShell}`}
      data-theme="light"
      data-locale={zh ? "zh" : "en"}
      data-product-mode="use"
      data-panel-open={panelOpen ? "true" : "false"}
      data-mausbot="1"
    >
      <div className={styles.appWindow}>
        <nav className={styles.productCrumb} aria-label={zh ? "产品层级" : "Product hierarchy"}>
          <a href="/landing" className={styles.productCrumbLink}>{zh ? "主页" : "Home"}</a>
          <span className={styles.productCrumbSep} aria-hidden="true">›</span>
          <span className={styles.productCrumbCurrent} aria-current="page">MausBot</span>
        </nav>

        {error ? (
          <div className={styles.errorBanner} role="alert">
            <span>{error}</span>
            <button type="button" onClick={() => setError(null)} aria-label={zh ? "关闭" : "Dismiss"}>×</button>
          </div>
        ) : null}

        <main className={`${styles.botLayout} ${styles.mausLayout} ${panelOpen ? "" : styles.botLayoutPanelClosed}`}>
          <aside className={`${styles.sessionSidebar} ${styles.mausSidebar}`} aria-label={zh ? "机器人与话题" : "Bots and threads"}>
            <div className={styles.mausSideSearch} aria-hidden="true">
              <span>{zh ? "搜索" : "Search"}</span>
            </div>

            <div className={styles.sessionSidebarHead}>
              <strong>{zh ? "收件箱" : "Inbox"}</strong>
              <button type="button" onClick={() => void loadBots()} disabled={loadingBots}>
                {zh ? "刷新" : "Refresh"}
              </button>
            </div>

            <div className={styles.botRoster}>
              {bots.map((bot) => (
                <button
                  key={bot.id}
                  type="button"
                  className={styles.botRosterItem}
                  data-active={bot.id === botId ? "true" : "false"}
                  onClick={() => {
                    setBotId(bot.id);
                    const preferred =
                      window.localStorage.getItem(WEB_THREAD_KEY) ||
                      bot.tasks?.find((t) => !isNoisyTitle(t.title))?.threadId ||
                      bot.mainThreadId ||
                      bot.threadId ||
                      null;
                    setThreadId(preferred);
                    void loadRail(bot.id);
                  }}
                >
                  <span className={styles.mausBotName}>{bot.name || "Bot"}</span>
                  <small>{bot.busy ? (zh ? "忙碌" : "busy") : bot.activity || "idle"}</small>
                </button>
              ))}
            </div>

            <div className={styles.sessionSidebarHead}>
              <strong>{zh ? "话题" : "Threads"}</strong>
              <button type="button" onClick={() => void onNewThread()} disabled={!botId || streaming}>
                {zh ? "新建" : "New"}
              </button>
            </div>
            <div className={styles.sessionList}>
              {threads
                .filter((t) => !isNoisyTitle(t.title) || t.threadId === threadId)
                .map((t) => (
                <button
                  key={t.threadId}
                  type="button"
                  className={styles.sessionItem}
                  data-active={t.threadId === threadId ? "true" : "false"}
                  onClick={() => setThreadId(t.threadId)}
                >
                  <span>{t.title || (zh ? "未命名话题" : "Untitled")}</span>
                  <small>{t.busy ? (zh ? "进行中" : "running") : t.activity || "idle"}</small>
                </button>
              ))}
              {!threads.length ? <p className={styles.muted}>{zh ? "暂无话题" : "No threads"}</p> : null}
            </div>

            <div className={styles.mausSideFoot}>
              <button type="button" className={styles.mausFootLink} onClick={() => { setRail("routines"); setPanelOpen(true); }}>{zh ? "自动化" : "Automation"}</button>
              <button type="button" className={styles.mausFootLink} onClick={() => { setRail("settings"); setPanelOpen(true); }}>{zh ? "设置" : "Settings"}</button>
            </div>
          </aside>

          <section className={`${styles.stageColumn} ${styles.mausStage}`} aria-label={zh ? "对话" : "Chat"}>
            <header className={styles.stageHeader}>
              <div className={styles.mausStageTitle}>
                <strong>{activeBot?.name || "MausBot"}</strong>
                <span className={styles.muted}> · {activeTitle}</span>
              </div>
              <div className={styles.stageActions}>
                <span className={styles.mausModelChip}>{modelLabel}</span>
                <button type="button" className={styles.stageIconButton} onClick={() => setPanelOpen((v) => !v)}>
                  {panelOpen ? (zh ? "收起" : "Hide") : (zh ? "面板" : "Panel")}
                </button>
              </div>
            </header>

            <div ref={transcriptRef} className={`${styles.transcript} ${styles.mausTranscript}`} aria-live="polite">
              {bootstrapping || loadingMsgs ? (
                <p className={styles.muted}>{zh ? "加载中…" : "Loading…"}</p>
              ) : null}

              {!bootstrapping && !loadingMsgs && historyTotal > HISTORY_WINDOW && !showAllHistory ? (
                <button type="button" className={styles.mausLoadEarlier} onClick={() => setShowAllHistory(true)}>
                  {zh ? `显示更早的 ${historyTotal - HISTORY_WINDOW} 条` : `Show earlier ${historyTotal - HISTORY_WINDOW}`}
                </button>
              ) : null}

              {!bootstrapping && !loadingMsgs && !messages.length ? (
                <div className={styles.welcomeCard}>
                  <h2>{zh ? "新的网页对话" : "Fresh web chat"}</h2>
                  <p>
                    {zh
                      ? "支持流式回复；回合进行中再发会进入服务端排队（当前线上无双击插话/steer，仅排队）。左侧切换话题；右侧是记忆 / 例行 / 设置。"
                      : "Streaming replies; messages sent mid-turn go to the server queue (no live steer online—queue only). Switch threads on the left; memory, routines and settings on the right."}
                  </p>
                </div>
              ) : null}

              {visibleMessages.map((m) => {
                if (m.kind === "activity") {
                  return <ActivityRow key={m.id} zh={zh} msg={m} />;
                }
                if (m.helperIntro || m.kind === "options") {
                  return <OptionsRow key={m.id} msg={m} botName={activeBot?.name || "MausBot"} threadId={threadId} zh={zh} />;
                }
                const mine = m.role === "user";
                return (
                  <div key={m.id} className={styles.mausRow} data-role={m.role}>
                    {!mine ? <div className={styles.mausAvatar} aria-hidden="true">{(activeBot?.name || "M").slice(0, 1)}</div> : <div className={styles.mausAvatarSpacer} />}
                    <div className={`${styles.mausBubble} ${mine ? styles.mausBubbleUser : styles.mausBubbleBot} ${m.pending ? styles.mausBubblePending : ""}`}>
                      {(m.steered || m.queued) ? <div className={styles.mausSteeredTag}>{m.steered ? (zh ? "插话" : "steered") : (zh ? "排队" : "queued")}</div> : null}
                      <MessageBody msg={m} botName={activeBot?.name || "MausBot"} />
                    </div>
                    {mine ? <div className={styles.mausAvatarSpacer} /> : null}
                  </div>
                );
              })}
              {(liveStatus || streaming) ? (
                <div className={styles.mausLiveStatus} role="status" aria-live="polite">
                  <span className={styles.mausLiveDots} aria-hidden="true">
                    <i /><i /><i />
                  </span>
                  <span>{liveStatus || (zh ? "正在…" : "Working…")}</span>
                </div>
              ) : null}
              {turnEndNotice ? (
                <div className={styles.mausTurnEndBanner} role="status" aria-live="polite">
                  {turnEndNotice}
                </div>
              ) : null}
              <div ref={bottomRef} />
            </div>

            <form className={`${styles.composerWrap} ${styles.mausComposerWrap}`} onSubmit={onSubmit}>
              {queued.length ? (
                <div className={styles.mausQueue} aria-label={zh ? "排队中的消息" : "Queued messages"}>
                  {queued.map((q, idx) => (
                    <div key={q.id} className={styles.mausQueueChip}>
                      <span className={styles.mausQueueIdx} aria-hidden="true">{idx + 1}</span>
                      <span
                        className={styles.mausQueueText}
                        title={zh ? "双击编辑排队内容（不会插话/steer）" : "Double-click to edit queued text (queue only, not steer)"}
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
                        className={styles.mausQueueEdit}
                        aria-label={zh ? `编辑排队消息 ${idx + 1}` : `Edit queued message ${idx + 1}`}
                        onClick={() => void editQueued(q.id)}
                      >{zh ? "编辑" : "Edit"}</button>
                      <button type="button" className={styles.mausQueueRemove} aria-label={zh ? `移除排队消息 ${idx + 1}` : `Remove queued message ${idx + 1}`} onClick={() => void removeQueued(q.id)}>×</button>
                    </div>
                  ))}
                </div>
              ) : null}
              <div className={`${styles.composer} ${styles.mausComposer}`}>
                <textarea
                  ref={composerRef}
                  value={input}
                  onChange={(ev) => setInput(ev.target.value)}
                  placeholder={
                    streaming
                      ? (zh ? "回合进行中，发送将排队…" : "Turn running — send to queue…")
                      : (zh ? `给 ${activeBot?.name || "MausBot"} 发消息` : `Message ${activeBot?.name || "MausBot"}`)
                  }
                  rows={2}
                  disabled={!threadId || bootstrapping}
                  onKeyDown={(ev) => {
                    if (ev.key === "Enter" && !ev.shiftKey) {
                      ev.preventDefault();
                      onSubmit();
                    }
                  }}
                />
                <div className={styles.composerRow}>
                  <span className={styles.muted}>
                    {streaming
                      ? (zh ? "流式输出中 · 可继续输入排队" : "Streaming · extra sends queue")
                      : (zh ? "Enter 发送 · Shift+Enter 换行" : "Enter send · Shift+Enter newline")}
                  </span>
                  <div className={styles.mausComposerActions}>
                    {streaming ? (
                      <button type="button" className={styles.mausStopBtn} onClick={onStop}>
                        {zh ? "停止" : "Stop"}
                      </button>
                    ) : null}
                    <button type="submit" disabled={!input.trim() || !threadId || bootstrapping}>
                      {streaming ? (zh ? "排队" : "Queue") : (zh ? "发送" : "Send")}
                    </button>
                  </div>
                </div>
              </div>
            </form>
          </section>

          <aside className={`${styles.contextRail} ${styles.mausRail} ${panelOpen ? "" : styles.contextRailClosed}`} aria-label={zh ? "功能板块" : "Panels"}>
            <div className={styles.sideTabs} role="tablist">
              {(["memory", "routines", "settings"] as RailTab[]).map((tab) => (
                <button
                  key={tab}
                  type="button"
                  role="tab"
                  aria-selected={rail === tab}
                  data-active={rail === tab ? "true" : "false"}
                  onClick={() => setRail(tab)}
                >
                  {tab === "memory" ? (zh ? "记忆" : "Memory") : tab === "routines" ? (zh ? "例行" : "Routines") : (zh ? "设置" : "Settings")}
                </button>
              ))}
            </div>

            <div className={`${styles.workspaceBody} ${styles.mausRailBody}`}>
              {rail === "memory" ? (
                <section className={styles.memoryPanel}>
                  <strong>{zh ? "机器人记忆" : "Bot memory"}</strong>
                  <pre className={styles.mausPre}>{memoryText || (zh ? "（空）" : "(empty)")}</pre>
                </section>
              ) : null}

              {rail === "routines" ? (
                <section className={styles.routinesPanel}>
                  <strong>{zh ? "例行任务" : "Routines"}</strong>
                  {!routines.length ? (
                    <p className={styles.muted}>{zh ? "暂无例行。可在对话里让 MausBot 创建。" : "No routines yet."}</p>
                  ) : (
                    <ul className={styles.mausList}>
                      {routines.map((r: any, idx: number) => (
                        <li key={r.id || idx}>
                          <strong>{r.name || r.title || (zh ? "例行" : "Routine")}</strong>
                          <div className={styles.muted}>{r.schedule || r.cron || r.status || ""}</div>
                        </li>
                      ))}
                    </ul>
                  )}
                  {routineRuns?.length ? (
                    <>
                      <strong className={styles.mausSubhead}>{zh ? "最近运行" : "Recent runs"}</strong>
                      <ul className={styles.mausList}>
                        {routineRuns.slice(0, 8).map((run: any, idx: number) => (
                          <li key={run.id || idx} className={styles.muted}>
                            {run.status || "run"} · {run.routineName || run.routineId || ""}
                          </li>
                        ))}
                      </ul>
                    </>
                  ) : null}
                </section>
              ) : null}

              {rail === "settings" ? (
                <section className={styles.memoryPanel}>
                  <strong>{zh ? "连接与引擎" : "Connection & engines"}</strong>
                  <p className={styles.muted}>
                    {zh ? "健康" : "Health"}: {health?.ok ? "ok" : "down"}
                    {health?.upstream_status ? ` · upstream ${health.upstream_status}` : ""}
                  </p>
                  <p className={styles.muted}>{zh ? "当前模型" : "Model"}: {modelLabel}</p>
                  <pre className={styles.mausPre}>
                    {config
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
                      : zh ? "加载中…" : "Loading…"}
                  </pre>
                </section>
              ) : null}
            </div>
          </aside>
        </main>
      </div>
    </div>
  );
}
