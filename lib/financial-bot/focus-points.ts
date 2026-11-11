/**
 * Lightweight local "focus points" (research questions I am watching).
 * Maps onto monitors / memories when present; otherwise localStorage.
 */

export type FocusPoint = {
  id: string;
  question: string;
  /** Optional ticker or label, not required for UI copy */
  symbol?: string | null;
  /** Extra match tokens for mapping monitor/inbox events */
  keywords?: string[];
  /** Linked monitor id when created from / mapped to a monitor */
  monitorId?: string | null;
  /** Linked session if we opened a thread for this focus */
  sessionId?: string | null;
  source: "local" | "monitor" | "memory";
  createdAt: string;
  updatedAt: string;
};

const STORAGE_KEY = "financial-bot-focus-points-v1";

function safeParse(raw: string | null): FocusPoint[] {
  if (!raw) return [];
  try {
    const data = JSON.parse(raw) as unknown;
    if (!Array.isArray(data)) return [];
    return data
      .filter((item): item is FocusPoint => Boolean(item && typeof item === "object" && typeof (item as FocusPoint).id === "string" && typeof (item as FocusPoint).question === "string"))
      .map((item): FocusPoint => {
        const source: FocusPoint["source"] =
          item.source === "monitor" || item.source === "memory" ? item.source : "local";
        return {
          id: item.id,
          question: item.question.trim(),
          symbol: item.symbol ?? null,
          keywords: Array.isArray(item.keywords)
            ? item.keywords.map((part) => String(part).trim()).filter(Boolean).slice(0, 12)
            : [],
          monitorId: item.monitorId ?? null,
          sessionId: item.sessionId ?? null,
          source,
          createdAt: item.createdAt || new Date().toISOString(),
          updatedAt: item.updatedAt || item.createdAt || new Date().toISOString(),
        };
      })
      .filter((item) => item.question.length > 0);
  } catch {
    return [];
  }
}

export function loadFocusPoints(): FocusPoint[] {
  if (typeof window === "undefined") return [];
  try {
    return safeParse(window.localStorage.getItem(STORAGE_KEY));
  } catch {
    return [];
  }
}

export function saveFocusPoints(points: FocusPoint[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(points.slice(0, 40)));
  } catch {
    /* private mode / quota */
  }
}

export function upsertFocusPoint(input: {
  question: string;
  symbol?: string | null;
  keywords?: string[];
  monitorId?: string | null;
  sessionId?: string | null;
  source?: FocusPoint["source"];
  id?: string;
}): FocusPoint {
  const now = new Date().toISOString();
  const current = loadFocusPoints();
  const normalized = input.question.trim();
  const existing = input.id
    ? current.find((item) => item.id === input.id)
    : current.find((item) => item.question === normalized);
  const mergedKeywords = [...new Set([
    ...(input.keywords || []),
    ...(existing?.keywords || []),
  ].map((part) => part.trim()).filter(Boolean))].slice(0, 12);
  const next: FocusPoint = {
    id: existing?.id || input.id || `focus-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`,
    question: normalized,
    symbol: input.symbol ?? existing?.symbol ?? null,
    keywords: mergedKeywords,
    monitorId: input.monitorId ?? existing?.monitorId ?? null,
    sessionId: input.sessionId ?? existing?.sessionId ?? null,
    source: input.source || existing?.source || "local",
    createdAt: existing?.createdAt || now,
    updatedAt: now,
  };
  const others = current.filter((item) => item.id !== next.id && item.question !== normalized);
  saveFocusPoints([next, ...others]);
  return next;
}

export function removeFocusPoint(id: string): void {
  saveFocusPoints(loadFocusPoints().filter((item) => item.id !== id));
}

type MonitorLike = {
  id: string;
  name?: string | null;
  symbol?: string | null;
  condition?: string | null;
  status?: string | null;
  created_at?: string | null;
};

type MemoryLike = {
  id: string;
  kind: string;
  text: string;
  created_at?: string;
  updated_at?: string;
  confirmed_at?: string | null;
};

function monitorQuestion(monitor: MonitorLike, en: boolean): string {
  const symbol = (monitor.symbol || monitor.name || "").trim();
  const cond = monitor.condition || "price";
  if (cond === "announcement") {
    return en
      ? (symbol ? `Watch CNINFO announcements for ${symbol}` : "Watch CNINFO announcements")
      : (symbol ? `盯着 ${symbol} 的巨潮公告有没有新变化` : "盯着巨潮公告有没有新变化");
  }
  if (cond === "earnings") {
    return en
      ? (symbol ? `Watch earnings date for ${symbol}` : "Watch upcoming earnings dates")
      : (symbol ? `盯着 ${symbol} 的财报日` : "盯着即将到来的财报日");
  }
  if (cond === "report") {
    return en ? "Watch my watchlist report for changes" : "盯着自选股报告有没有新变化";
  }
  return en
    ? (symbol ? `Watch price moves on ${symbol}` : "Watch price moves on a name I care about")
    : (symbol ? `盯着 ${symbol} 的价格异动` : "盯着我关心标的的价格异动");
}

/** Merge local focus points with monitors + research memories for use-mode UI. */
export function mergeWatchingList(input: {
  en: boolean;
  local?: FocusPoint[];
  monitors?: MonitorLike[];
  memories?: MemoryLike[];
}): FocusPoint[] {
  const local = input.local ?? (typeof window !== "undefined" ? loadFocusPoints() : []);
  const fromMonitors = (input.monitors || []).map((monitor) => ({
    id: `monitor:${monitor.id}`,
    question: monitorQuestion(monitor, input.en),
    symbol: monitor.symbol || monitor.name || null,
    keywords: [monitor.symbol, monitor.name, monitor.condition].filter(Boolean).map((part) => String(part)) as string[],
    monitorId: monitor.id,
    sessionId: null,
    source: "monitor" as const,
    createdAt: monitor.created_at || new Date(0).toISOString(),
    updatedAt: monitor.created_at || new Date(0).toISOString(),
  }));
  const fromMemories = (input.memories || [])
    .filter((item) => item.kind === "research_question" || item.kind === "watch_object")
    .map((item) => ({
      id: `memory:${item.id}`,
      question: item.text.trim(),
      symbol: null,
      keywords: [],
      monitorId: null,
      sessionId: null,
      source: "memory" as const,
      createdAt: item.created_at || item.updated_at || new Date(0).toISOString(),
      updatedAt: item.confirmed_at || item.updated_at || item.created_at || new Date(0).toISOString(),
    }))
    .filter((item) => item.question.length > 0);

  const seen = new Set<string>();
  const out: FocusPoint[] = [];
  for (const item of [...local, ...fromMemories, ...fromMonitors]) {
    const key = item.question.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(item);
  }
  return out.sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1));
}

/** Example research questions for the track template. */
export function exampleTrackQuestions(en: boolean): string[] {
  if (en) {
    return [
      "Did company guidance change after the latest filing?",
      "Any new CNINFO / SEC filing that changes the thesis?",
      "Is the holding move explained by a disclosed catalyst?",
    ];
  }
  return [
    "最新财报/公告后，公司指引有没有改口？",
    "有没有新的公告会动摇当前判断？",
    "持仓波动能不能用已披露催化解释清楚？",
  ];
}
