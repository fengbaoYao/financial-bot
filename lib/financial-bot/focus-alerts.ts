/**
 * Map Monitor / inbox events onto focus points as unread alerts.
 * Minimal: symbol / keyword / monitorId match — not thesis-level evidence diffs.
 */

import {
  loadFocusPoints,
  saveFocusPoints,
  type FocusPoint,
  upsertFocusPoint,
} from "./focus-points";

export type FocusAlert = {
  id: string;
  focusId: string;
  focusQuestion: string;
  title: string;
  body: string;
  status: "unread" | "read";
  source: "notification" | "monitor" | "demo";
  sourceNotificationId?: string | null;
  sourceMonitorId?: string | null;
  symbol?: string | null;
  createdAt: string;
  readAt?: string | null;
  /** True when seeded for empty-inbox demos only */
  demo?: boolean;
};

export type WatchingAlert = {
  id: string;
  title: string;
  body: string;
  status: "unread" | "read";
  created_at: string;
  focusId?: string | null;
  focusQuestion?: string | null;
  sourceNotificationId?: string | null;
  sourceMonitorId?: string | null;
  kind: "server" | "local" | "demo";
  severity?: string;
  bot_id?: string;
  task_id?: string | null;
  monitor_id?: string | null;
};

const ALERTS_KEY = "financial-bot-focus-alerts-v1";
const DEMO_SEED_KEY = "financial-bot-focus-alert-demo-seeded-v1";

type NotificationLike = {
  id: string;
  bot_id?: string;
  monitor_id?: string | null;
  title: string;
  body: string;
  severity?: string;
  status: string;
  task_id?: string | null;
  created_at: string;
};

type MonitorLike = {
  id: string;
  name?: string | null;
  symbol?: string | null;
  condition?: string | null;
  status?: string | null;
  last_triggered_at?: string | null;
  created_at?: string | null;
};

function safeParseAlerts(raw: string | null): FocusAlert[] {
  if (!raw) return [];
  try {
    const data = JSON.parse(raw) as unknown;
    if (!Array.isArray(data)) return [];
    return data
      .filter((item): item is FocusAlert => Boolean(item && typeof item === "object" && typeof (item as FocusAlert).id === "string"))
      .map((item): FocusAlert => {
        const source: FocusAlert["source"] =
          item.source === "monitor" || item.source === "demo" ? item.source : "notification";
        const status: FocusAlert["status"] = item.status === "read" ? "read" : "unread";
        return {
          id: item.id,
          focusId: String(item.focusId || ""),
          focusQuestion: String(item.focusQuestion || ""),
          title: String(item.title || ""),
          body: String(item.body || ""),
          status,
          source,
          sourceNotificationId: item.sourceNotificationId ?? null,
          sourceMonitorId: item.sourceMonitorId ?? null,
          symbol: item.symbol ?? null,
          createdAt: item.createdAt || new Date().toISOString(),
          readAt: item.readAt ?? null,
          demo: Boolean(item.demo),
        };
      })
      .filter((item) => item.focusId && item.title);
  } catch {
    return [];
  }
}

export function loadFocusAlerts(): FocusAlert[] {
  if (typeof window === "undefined") return [];
  try {
    return safeParseAlerts(window.localStorage.getItem(ALERTS_KEY));
  } catch {
    return [];
  }
}

export function saveFocusAlerts(alerts: FocusAlert[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(ALERTS_KEY, JSON.stringify(alerts.slice(0, 80)));
  } catch {
    /* private mode / quota */
  }
}

export function markFocusAlertRead(id: string): FocusAlert | null {
  const current = loadFocusAlerts();
  let found: FocusAlert | null = null;
  const next = current.map((item) => {
    if (item.id !== id || item.status === "read") return item;
    found = { ...item, status: "read", readAt: new Date().toISOString() };
    return found;
  });
  if (found) saveFocusAlerts(next);
  return found;
}

/** Pull tickers / short keywords from a research question for matching. */
export function extractFocusHints(question: string): { symbols: string[]; keywords: string[] } {
  const text = question.trim();
  const symbols = new Set<string>();
  const englishNoise = new Set([
    "A", "I", "AM", "AN", "AS", "AT", "BE", "BY", "DO", "DID", "FOR", "FROM", "HAS", "HAVE", "IS", "IT", "ITS",
    "ME", "MY", "NO", "NOT", "OF", "ON", "OR", "SO", "THE", "TO", "UP", "US", "WE", "ANY", "NEW", "ALL", "AND",
    "CEO", "IPO", "ETF", "GDP", "SEC", "AFTER", "WITH", "THAT", "THIS", "WHAT", "WHEN", "YOUR", "ABOUT",
  ]);
  const explicit = text.matchAll(/\b(?:US\.)[A-Z]{1,5}\b|\b\d{5,6}(?:\.S[HZ])?\b|\b\d{4,5}\.HK\b/gi);
  for (const match of explicit) symbols.add(match[0].toUpperCase());
  const known = new Set(["AAPL", "TSLA", "MSFT", "NVDA", "AMD", "BABA", "META", "GOOG", "AMZN"]);
  for (const match of text.matchAll(/\b[A-Z]{2,5}\b/g)) {
    const raw = match[0].toUpperCase();
    if (englishNoise.has(raw)) continue;
    if (known.has(raw)) symbols.add(raw);
  }
  const stop = new Set([
    "最新", "财报", "公告", "公司", "指引", "有没有", "改口", "会不会", "动摇", "当前", "判断",
    "持仓", "波动", "能不能", "用已", "披露", "催化", "解释", "清楚", "跟踪", "研究", "问题",
    "the", "and", "for", "after", "with", "from", "that", "this", "did", "any", "new", "change",
  ]);
  const keywords = text
    .split(/[\s,，。？?、/|：:；;（）()【】\[\]]+/)
    .map((part) => part.trim())
    .filter((part) => part.length >= 2 && !stop.has(part.toLowerCase()) && !stop.has(part))
    .slice(0, 8);
  return { symbols: [...symbols], keywords };
}

function haystack(parts: Array<string | null | undefined>): string {
  return parts.filter(Boolean).join("\n").toLowerCase();
}

function focusMatchers(point: FocusPoint): string[] {
  const hints = extractFocusHints(point.question);
  const bag = new Set<string>();
  if (point.symbol) bag.add(point.symbol.toLowerCase());
  for (const symbol of hints.symbols) bag.add(symbol.toLowerCase());
  for (const keyword of point.keywords || []) bag.add(keyword.toLowerCase());
  for (const keyword of hints.keywords) bag.add(keyword.toLowerCase());
  return [...bag].filter(Boolean);
}

export function matchFocusToEvent(point: FocusPoint, event: {
  monitorId?: string | null;
  symbol?: string | null;
  title?: string;
  body?: string;
}): boolean {
  if (point.monitorId && event.monitorId && point.monitorId === event.monitorId) return true;
  const text = haystack([event.symbol, event.title, event.body]);
  if (!text) return false;
  if (point.symbol && text.includes(point.symbol.toLowerCase())) return true;
  if (event.symbol && point.question.toLowerCase().includes(event.symbol.toLowerCase())) return true;
  for (const token of focusMatchers(point)) {
    if (token.length >= 2 && text.includes(token)) return true;
  }
  return false;
}

function alertTitle(en: boolean, question: string, eventTitle: string): string {
  const shortQ = question.length > 28 ? `${question.slice(0, 28)}…` : question;
  const shortE = eventTitle.length > 36 ? `${eventTitle.slice(0, 36)}…` : eventTitle;
  return en
    ? `Focus update · ${shortQ}`
    : `关注点有变化 · ${shortQ}`;
  // keep shortE available for body; title stays focus-first
  void shortE;
}

function alertBody(en: boolean, question: string, eventTitle: string, eventBody: string): string {
  const summary = (eventBody || eventTitle || "").replace(/\s+/g, " ").trim();
  const clipped = summary.length > 180 ? `${summary.slice(0, 180)}…` : summary;
  return en
    ? `About your focus “${question}”.\nWhat happened: ${clipped || eventTitle}`
    : `对应关注点：「${question}」。\n发生了什么：${clipped || eventTitle}`;
}

function upsertAlert(alerts: FocusAlert[], next: FocusAlert): FocusAlert[] {
  const without = alerts.filter((item) => {
    if (item.id === next.id) return false;
    if (next.sourceNotificationId && item.sourceNotificationId === next.sourceNotificationId) return false;
    if (
      next.sourceMonitorId
      && item.sourceMonitorId === next.sourceMonitorId
      && item.focusId === next.focusId
      && item.source === next.source
      && item.createdAt === next.createdAt
    ) {
      return false;
    }
    return true;
  });
  return [next, ...without].slice(0, 80);
}

/** Sync local focus alerts from server notifications / monitor triggers. Idempotent. */
export function syncFocusAlertsFromSources(input: {
  en: boolean;
  focusPoints?: FocusPoint[];
  notifications?: NotificationLike[];
  monitors?: MonitorLike[];
  allowDemo?: boolean;
}): FocusAlert[] {
  // Prefer caller list when non-empty; otherwise read localStorage (avoid [] wiping hydrated focuses).
  const focusPoints = (input.focusPoints && input.focusPoints.length)
    ? input.focusPoints
    : loadFocusPoints();
  const localOnly = focusPoints.filter((item) => item.source === "local" || Boolean(item.question));
  let alerts = loadFocusAlerts();
  const monitorsById = new Map((input.monitors || []).map((item) => [item.id, item]));

  // Enrich local focus points with symbol/monitor links when a clear monitor match exists.
  for (const point of localOnly) {
    if (point.monitorId || point.symbol) continue;
    const hints = extractFocusHints(point.question);
    const hit = (input.monitors || []).find((monitor) => {
      const symbol = (monitor.symbol || "").toUpperCase();
      return Boolean(symbol && hints.symbols.some((item) => item === symbol || symbol.includes(item) || item.includes(symbol)));
    });
    if (hit) {
      upsertFocusPoint({
        id: point.id,
        question: point.question,
        symbol: hit.symbol || point.symbol,
        monitorId: hit.id,
        source: point.source,
      });
    }
  }
  const refreshedPoints = loadFocusPoints();
  const points = refreshedPoints.length ? refreshedPoints : focusPoints;

  for (const note of input.notifications || []) {
    const monitor = note.monitor_id ? monitorsById.get(note.monitor_id) : undefined;
    const matched = points.find((point) => matchFocusToEvent(point, {
      monitorId: note.monitor_id,
      symbol: monitor?.symbol,
      title: note.title,
      body: note.body,
    }));
    if (!matched) continue;
    const existing = alerts.find((item) => item.sourceNotificationId === note.id);
    if (existing) {
      // Keep read state; refresh copy if still unread.
      if (existing.status === "unread") {
        alerts = upsertAlert(alerts, {
          ...existing,
          focusId: matched.id,
          focusQuestion: matched.question,
          title: alertTitle(input.en, matched.question, note.title),
          body: alertBody(input.en, matched.question, note.title, note.body),
          sourceMonitorId: note.monitor_id ?? existing.sourceMonitorId,
          symbol: matched.symbol || monitor?.symbol || existing.symbol,
        });
      }
      continue;
    }
    const created: FocusAlert = {
      id: `focus-alert-${note.id}`,
      focusId: matched.id,
      focusQuestion: matched.question,
      title: alertTitle(input.en, matched.question, note.title),
      body: alertBody(input.en, matched.question, note.title, note.body),
      status: note.status === "read" ? "read" : "unread",
      source: "notification",
      sourceNotificationId: note.id,
      sourceMonitorId: note.monitor_id ?? null,
      symbol: matched.symbol || monitor?.symbol || null,
      createdAt: note.created_at || new Date().toISOString(),
      readAt: note.status === "read" ? new Date().toISOString() : null,
    };
    alerts = upsertAlert(alerts, created);
  }

  // If a monitor triggered but notification list is empty/partial, still surface a local ping.
  for (const monitor of input.monitors || []) {
    if (!monitor.last_triggered_at) continue;
    const matched = points.find((point) => matchFocusToEvent(point, {
      monitorId: monitor.id,
      symbol: monitor.symbol,
      title: monitor.name || monitor.symbol || "",
      body: monitor.condition || "",
    }));
    if (!matched) continue;
    const dedupeId = `focus-monitor-${monitor.id}-${monitor.last_triggered_at}`;
    if (alerts.some((item) => item.id === dedupeId || (item.sourceMonitorId === monitor.id && item.createdAt === monitor.last_triggered_at))) {
      continue;
    }
    // Skip if a notification alert already covers this monitor recently.
    if (alerts.some((item) => item.sourceMonitorId === monitor.id && item.source === "notification" && item.status === "unread")) {
      continue;
    }
    const eventTitle = input.en
      ? `${monitor.symbol || monitor.name || "Monitor"} triggered`
      : `${monitor.symbol || monitor.name || "盯梢"} 有新触发`;
    alerts = upsertAlert(alerts, {
      id: dedupeId,
      focusId: matched.id,
      focusQuestion: matched.question,
      title: alertTitle(input.en, matched.question, eventTitle),
      body: alertBody(
        input.en,
        matched.question,
        eventTitle,
        input.en
          ? `Monitor “${monitor.name || monitor.symbol}” fired at ${monitor.last_triggered_at}. Open to review.`
          : `盯梢「${monitor.name || monitor.symbol}」在 ${monitor.last_triggered_at} 触发了。点开可看详情。`,
      ),
      status: "unread",
      source: "monitor",
      sourceNotificationId: null,
      sourceMonitorId: monitor.id,
      symbol: matched.symbol || monitor.symbol || null,
      createdAt: monitor.last_triggered_at,
    });
  }

  const hasFocusLinkedUnread = alerts.some((item) => item.status === "unread" && !item.demo);
  const hasLocalFocus = points.some((item) => item.source === "local");
  // Demo only when nothing focus-linked is waiting. Unrelated server noise should not block a sample for a new focus.
  if (input.allowDemo && hasLocalFocus && !hasFocusLinkedUnread) {
    let seeded = false;
    try {
      seeded = window.localStorage.getItem(DEMO_SEED_KEY) === "1";
    } catch {
      seeded = false;
    }
    if (!seeded) {
      const target = points.find((item) => item.source === "local") || points[0];
      if (target) {
        alerts = upsertAlert(alerts, {
          id: `focus-demo-${target.id}`,
          focusId: target.id,
          focusQuestion: target.question,
          title: input.en
            ? `Sample alert · ${target.question.slice(0, 24)}${target.question.length > 24 ? "…" : ""}`
            : `示例提醒 · ${target.question.slice(0, 24)}${target.question.length > 24 ? "…" : ""}`,
          body: input.en
            ? `About your focus “${target.question}”.\nWhat happened: this is a sample unread so you can see how alerts show up. Real monitor / filing events will replace samples when they arrive.`
            : `对应关注点：「${target.question}」。\n发生了什么：这是一条示例未读，用来演示提醒长什么样。真有公告/财报日/例行触发时，会用真实事件替换。`,
          status: "unread",
          source: "demo",
          demo: true,
          createdAt: new Date().toISOString(),
        });
        try {
          window.localStorage.setItem(DEMO_SEED_KEY, "1");
        } catch {
          /* ignore */
        }
      }
    }
  }

  saveFocusAlerts(alerts);
  // Persist any enrichment done via upsertFocusPoint already.
  saveFocusPoints(loadFocusPoints());
  return alerts;
}

/** Build the inbox list shown in use-mode: focus-linked local alerts first, then unmatched server unread. */
export function buildWatchingInbox(input: {
  en: boolean;
  notifications: NotificationLike[];
  focusPoints?: FocusPoint[];
  monitors?: MonitorLike[];
  allowDemo?: boolean;
}): WatchingAlert[] {
  const alerts = syncFocusAlertsFromSources(input);
  const linkedIds = new Set(
    alerts
      .map((item) => item.sourceNotificationId)
      .filter((id): id is string => Boolean(id)),
  );

  const local: WatchingAlert[] = alerts
    .filter((item) => item.status === "unread")
    .map((item) => ({
      id: item.id,
      title: item.title,
      body: item.body,
      status: item.status,
      created_at: item.createdAt,
      focusId: item.focusId,
      focusQuestion: item.focusQuestion,
      sourceNotificationId: item.sourceNotificationId,
      sourceMonitorId: item.sourceMonitorId,
      kind: item.demo || item.source === "demo" ? "demo" : "local",
      monitor_id: item.sourceMonitorId,
    }));

  const serverExtra: WatchingAlert[] = (input.notifications || [])
    .filter((item) => item.status !== "read" && !linkedIds.has(item.id))
    .map((item) => ({
      id: item.id,
      title: item.title,
      body: item.body,
      status: item.status === "read" ? "read" : "unread",
      created_at: item.created_at,
      focusId: null,
      focusQuestion: null,
      sourceNotificationId: item.id,
      sourceMonitorId: item.monitor_id ?? null,
      kind: "server",
      severity: item.severity,
      bot_id: item.bot_id,
      task_id: item.task_id,
      monitor_id: item.monitor_id ?? null,
    }));

  return [...local, ...serverExtra].sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
}

export function ensureFocusHints(point: FocusPoint): FocusPoint {
  const hints = extractFocusHints(point.question);
  const keywords = [...new Set([...(point.keywords || []), ...hints.keywords])].slice(0, 10);
  const symbol = point.symbol || hints.symbols[0] || null;
  if (symbol === point.symbol && keywords.join("|") === (point.keywords || []).join("|")) return point;
  return upsertFocusPoint({
    id: point.id,
    question: point.question,
    symbol,
    monitorId: point.monitorId,
    sessionId: point.sessionId,
    source: point.source,
    keywords,
  });
}
