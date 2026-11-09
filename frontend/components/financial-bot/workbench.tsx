"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { GrokAvatar } from "./grok-avatar";
import { isCurrentBotResponse } from "@/lib/financial-bot/bot-switch-scope";
import { OnboardingReplay, WelcomeThread, type OnboardingRecord, type ResearchPersona } from "./welcome-thread";
import { successPathsFor } from "@/lib/financial-bot/onboarding";
import {
  loadFocusPoints,
  mergeWatchingList,
  upsertFocusPoint,
  type FocusPoint,
} from "@/lib/financial-bot/focus-points";
import {
  buildWatchingInbox,
  ensureFocusHints,
  markFocusAlertRead,
  type WatchingAlert,
} from "@/lib/financial-bot/focus-alerts";
import type { OnboardPlan } from "@/lib/financial-bot/client";

import { ResearchMarkdown } from "@/components/research-markdown";
import { LanguageSwitcher, useLanguage } from "@/components/language-provider";
import {
  BotApiError,
  cancelTask,
  confirmCandidate,
  createTask,
  createOpenMuseSharedResearch,
  syncOpenMuseSharedResearch,
  cancelOpenMuseSharedResearch,
  answerOpenMuseSharedResearch,
  deleteAllMemories,
  deleteMemory,
  deleteSession as deleteSessionApi,
  clearBotSessions,
  deleteBot,
  dismissCandidate,
  getBotStatus,
  getModelProviders,
  getAgentResearchCapabilities,
  getMemories,
  getTask,
  getTaskPlan,
  resumeTask as resumeTaskApi,
  listBots,
  listSessions,
  listTasks,
  listDispositions,
  listMessages,
  ensureWelcome,
  getBotOnboarding,
  createThread,
  patchThread,
  readThread,
  getThreadState,
  type ThreadState,
  listPortfolioPositions,
  type PortfolioPosition,
  preflightBot,
  listMonitors,
  createMonitor,
  updateMonitor,
  deleteMonitor,
  listNotifications,
  readNotification,
  analyzeNotification,
  listDeliveries,
  retryDelivery,
  listArtifacts,
  muteMonitor,
  type BotPreflight,
  type BotMonitor,
  type BotNotification,
  type BotDelivery,
  type BotArtifact,
  patchMemory,
  streamTaskEvents,
} from "@/lib/financial-bot/client";
import {
  EMPTY_RUN,
  activeSteps,
  applyTaskEvent,
  botStateFromTask,
  completedSteps,
  findingNotices,
  isTerminal,
  setRunStatus,
  startRun,
  statusLabel,
  botCopy,
  financialResearchModeErrorCopy,
  researchModeDisplayName,
  researchModelDisplayName,
  openMuseStatusCopy,
  openMuseTaskStatus,
  openMuseResearchResult,
  openMuseDeletionPending,
  openMuseDeletionPendingCopy,
  type RunState,
  type RunStep,
} from "@/lib/financial-bot/state";
import type {
  BotStatus,
  BotTask,
  BotMessage,
  DeletionJob,
  MemoryCandidate,
  MemoryItem,
  MemoryResponse,
  SourceEvidence,
  TaskStatus,
  BotState,
  UIBlock,
  TaskPlan,
  BotDisposition,
} from "@/lib/financial-bot/types";
import type { BotProfile, BotSessionSummary } from "@/lib/financial-bot/client";
import { BotStudio } from "./bot-studio";
import styles from "./financial-bot-styles";

/** The watch list ("我在盯的事") is switched OFF while its product story is being
 *  reworked — it merged three sources and confused more than it helped.
 *  Flip to `true` to bring back: the rail section, the header chip, the inline
 *  "start a watch" card and the add-watch dialog. Stored data is left untouched. */
const WATCH_ENABLED = false;
const SESSION_KEY = "financial-bot-session-v1";
/** sessionId → botId. A conversation belongs to the Bot that started it, so we
 *  remember the pair locally instead of probing the server with a 409. */
const SESSION_BOT_KEY = "financial-bot-session-bot-v1";
const PERSONA_KEY = "financial-bot-persona-v1";
/** Plugins catalog is not wired — keep entry chrome off until this flips. */
const PLUGINS_READY = false;

async function withStartupRetry<T>(task: () => Promise<T>, attempts = 2): Promise<T> {
  let lastError: unknown;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      return await task();
    } catch (error) {
      lastError = error;
      if (attempt < attempts - 1) await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }
  throw lastError;
}

/** Record which Bot owns a conversation (bounded, best-effort). */
function rememberSessionBot(sessionId: string | null | undefined, botId: string | null | undefined) {
  if (typeof window === "undefined" || !sessionId || !botId) return;
  try {
    const raw = window.localStorage.getItem(SESSION_BOT_KEY);
    const map = (raw ? JSON.parse(raw) : {}) as Record<string, string>;
    map[sessionId] = botId;
    const entries = Object.entries(map).slice(-100);
    window.localStorage.setItem(SESSION_BOT_KEY, JSON.stringify(Object.fromEntries(entries)));
  } catch {
    // Storage can be disabled; the 409 fallback still repairs the pair.
  }
}

/** Which Bot owns this conversation, if this browser has seen it. */
function sessionBotOwner(sessionId: string | null | undefined): string | null {
  if (typeof window === "undefined" || !sessionId) return null;
  try {
    const raw = window.localStorage.getItem(SESSION_BOT_KEY);
    const map = (raw ? JSON.parse(raw) : {}) as Record<string, string>;
    return map[sessionId] || null;
  } catch {
    return null;
  }
}

function uid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

const kindLabels: Record<string, [string, string]> = {
  watch_object: ["关注对象", "Watched"],
  preference: ["明确偏好", "Preference"],
  research_question: ["研究问题", "Research question"],
  holding: ["用户陈述的持仓", "Stated holding"],
  confirmed_viewpoint: ["已确认观点", "Confirmed view"],
};

function formatTime(iso?: string | null, locale: "zh" | "en" = "zh"): string {
  if (!iso) return locale === "en" ? "unknown" : "未知";
  try {
    const date = new Date(iso);
    const sameYear = date.getFullYear() === new Date().getFullYear();
    return new Intl.DateTimeFormat(locale === "en" ? "en-US" : "zh-CN", {
      ...(sameYear ? {} : { year: "numeric" as const }),
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  } catch {
    return iso;
  }
}

const TOOL_META: Record<string, { icon: string; zh: string; en: string }> = {
  web_search: { icon: "⌁", zh: "联网检索", en: "Web search" },
  read_public_source: { icon: "▤", zh: "读取原始来源", en: "Read source" },
  research_company: { icon: "◈", zh: "公司深度研究", en: "Company research" },
  research_comparison: { icon: "◈", zh: "公司对比研究", en: "Company comparison" },
  search_stocks: { icon: "⌕", zh: "核验标的", en: "Resolve ticker" },
  get_stock_analysis: { icon: "◴", zh: "行情与技术面", en: "Market data" },
  get_stock_news: { icon: "≋", zh: "公司新闻", en: "Company news" },
  get_global_news: { icon: "≋", zh: "全球新闻", en: "Global news" },
  get_sec_company_data: { icon: "▤", zh: "SEC 财报与申报", en: "SEC filings" },
  get_cninfo_announcements: { icon: "▥", zh: "A股公告", en: "CN announcements" },
  get_financial_statements: { icon: "▦", zh: "财务报表", en: "Financial statements" },
  get_company_fundamentals: { icon: "▦", zh: "公司基本面", en: "Fundamentals" },
  get_analyst_estimates: { icon: "◇", zh: "分析师目标价", en: "Analyst targets" },
  get_consensus_estimates: { icon: "◇", zh: "一致预期", en: "Consensus" },
  compare_companies: { icon: "⇄", zh: "财务口径比较", en: "Financial comparison" },
  get_index_constituents: { icon: "▩", zh: "指数成分", en: "Index constituents" },
  list_hotspots: { icon: "◴", zh: "市场热点", en: "Market themes" },
  ifind_smart_query: { icon: "⌕", zh: "同花顺智能查询", en: "iFinD smart query" },
  get_hotspot_detail: { icon: "◴", zh: "热点详情", en: "Theme detail" },
  get_portfolio_analysis: { icon: "◫", zh: "持仓分析", en: "Portfolio analysis" },
  manage_watchlist: { icon: "☆", zh: "自选股操作", en: "Watchlist" },
  model_knowledge_fill: { icon: "✦", zh: "模型补齐（未核验）", en: "Model fill (unverified)" },
};

function toolLabel(tool: string, en: boolean): { icon: string; label: string } {
  const meta = TOOL_META[tool];
  return { icon: meta?.icon || "⌘", label: meta ? (en ? meta.en : meta.zh) : tool };
}

function stepStatusLabel(status: string, en: boolean): string {
  const labels: Record<string, [string, string]> = {
    running: ["执行中", "Running"],
    completed: ["已完成", "Done"],
    failed: ["失败", "Failed"],
    skipped: ["已跳过", "Skipped"],
    unavailable: ["不可用", "Unavailable"],
  };
  return labels[status]?.[en ? 1 : 0] ?? status;
}

/** The research prompt a saved watch sends when it is followed up. */
function promptForFocus(en: boolean, question: string): string {
  return en
    ? `Follow up on the research question I am watching: ${question}

Summarize any new evidence since last time, mark gaps, and say whether I should keep watching. Do not give trading advice.`
    : `继续跟踪我正在盯的研究问题：${question}

请汇总相较上次有没有新证据，标明缺口，并说明是否值得继续盯。不要给买卖建议。`;
}

/** A conversation is bound to the Bot that started it; the backend refuses
 *  foreign writes with this 409 instead of hijacking the history. */
function foreignSessionOwner(caught: unknown): string | null {
  if (!(caught instanceof BotApiError) || caught.code !== "session_belongs_to_another_bot") return null;
  const owner = caught.payload?.bot_id;
  return typeof owner === "string" && owner ? owner : null;
}

/** Backend conflicts carry machine codes; users must never see the raw code. */
function describeBotError(caught: unknown, en: boolean): string {
  if (caught instanceof BotApiError) {
    switch (caught.code) {
      case "session_belongs_to_another_bot":
        return en
          ? "This conversation belongs to another assistant. It was switched back to its owner."
          : "这段对话属于另一个助手，已自动切回该助手。";
      case "model_approval_required":
        return en ? "Confirm the model budget before this run starts." : "开始前需要先确认模型调用预算。";
      case "financial_research_requires_quick_or_deep":
        return financialResearchModeErrorCopy(en ? "en" : "zh");
      case "continued_task_belongs_to_another_thread":
        return en
          ? "That follow-up belongs to a different conversation."
          : "这条追问属于另一个对话，不能在当前对话里继续。";
      default:
        break;
    }
  }
  return caught instanceof Error ? caught.message : String(caught);
}

export function FinancialBotWorkbench() {
  const { locale } = useLanguage();
  const en = locale === "en";
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [tasks, setTasks] = useState<BotTask[]>([]);
  const [messages, setMessages] = useState<BotMessage[]>([]);
  const [portfolioPositions, setPortfolioPositions] = useState<PortfolioPosition[] | null>(null);
  const [emptyPortfolioPrompt, setEmptyPortfolioPrompt] = useState<string | null>(null);
  const [threadState, setThreadState] = useState<ThreadState | null>(null);
  const [persona, setPersona] = useState<ResearchPersona | null>(null);
  const [onboardPlan, setOnboardPlan] = useState<OnboardPlan | null>(null);
  const [onboardingRecord, setOnboardingRecord] = useState<OnboardingRecord | null>(null);
  const [welcomeDismissed, setWelcomeDismissed] = useState(false);
  const [sessions, setSessions] = useState<BotSessionSummary[]>([]);
  const [pendingSessionDeletions, setPendingSessionDeletions] = useState<Array<{ sessionId: string; botId: string | null }>>([]);
  const [bots, setBots] = useState<BotProfile[]>([]);
  const [activeBotId, setActiveBotId] = useState<string | null>(null);
  const [studioOpen, setStudioOpen] = useState(false);
  const [studioBot, setStudioBot] = useState<BotProfile | null>(null);
  const [pendingApproval, setPendingApproval] = useState<{ query: string; preflight: BotPreflight; botId: string; modelProvider: "deepseek" | "chatgpt" } | null>(null);
  const [monitors, setMonitors] = useState<BotMonitor[]>([]);
  const [focusPoints, setFocusPoints] = useState<FocusPoint[]>([]);
  const [watchingAlerts, setWatchingAlerts] = useState<WatchingAlert[]>([]);
  const [notifications, setNotifications] = useState<BotNotification[]>([]);
  const [deliveries, setDeliveries] = useState<BotDelivery[]>([]);
  const [artifacts, setArtifacts] = useState<BotArtifact[]>([]);
  const [monitorDialogOpen, setMonitorDialogOpen] = useState(false);
  const [focusDraft, setFocusDraft] = useState<{ open: boolean; text: string }>({ open: false, text: "" });
  /** True while `selectBot()` is moving bot + conversation together. Without it
   *  the welcome effect fires mid-switch with (new bot, old conversation), gets a
   *  409 and "helpfully" adopts the old bot back — the switch visibly snaps back. */
  const [switchingBot, setSwitchingBot] = useState(false);
  const [monitorSaving, setMonitorSaving] = useState(false);
  const [monitorDraft, setMonitorDraft] = useState({ symbol: "", condition: "price" as "price" | "announcement" | "earnings" | "report", threshold: 3, direction: "both" as "up" | "down" | "both", interval: 15 });
  const [pendingNotification, setPendingNotification] = useState<BotNotification | null>(null);
  const [memories, setMemories] = useState<MemoryResponse | null>(null);
  const [run, setRun] = useState<RunState>(EMPTY_RUN);
  const [input, setInput] = useState("");
  const [queuedPrompts, setQueuedPrompts] = useState<string[]>([]);
  const [researchMode, setResearchMode] = useState<"quick" | "deep" | "openmuse_shared">("quick");
  const [modelProvider, setModelProvider] = useState<"deepseek" | "chatgpt">("deepseek");
  const [modelProviderTouched, setModelProviderTouched] = useState(false);
  const [chatgptStatus, setChatgptStatus] = useState({ available: false, connected: false });
  const [deepAvailable, setDeepAvailable] = useState(true);
  const [deepUnavailableMessage, setDeepUnavailableMessage] = useState<string | null>(null);
  const [notificationsError, setNotificationsError] = useState<string | null>(null);
  const [deliveriesError, setDeliveriesError] = useState<string | null>(null);
  const [runStuck, setRunStuck] = useState(false);

  useEffect(() => {
    let active = true;
    const refresh = () => {
      void getModelProviders()
        .then((status) => {
          if (!active) return;
          setChatgptStatus(status.chatgpt);
          // Prefer ChatGPT when the subscription is available, but do not override a
          // DeepSeek choice the user already made in this session.
          if (status.chatgpt.available && !modelProviderTouched) {
            setModelProvider("chatgpt");
          }
        })
        .catch(() => {});
    };
    refresh();
    window.addEventListener("focus", refresh);
    return () => { active = false; window.removeEventListener("focus", refresh); };
  }, [modelProviderTouched]);

  useEffect(() => {
    let active = true;
    void getAgentResearchCapabilities()
      .then((capabilities) => {
        if (!active) return;
        setDeepAvailable(capabilities.deep_available === true);
        setDeepUnavailableMessage(capabilities.deep_unavailable_message || null);
        if (capabilities.deep_available !== true) setResearchMode("quick");
      })
      .catch(() => {
        /* Keep composer usable if capabilities endpoint is briefly unavailable. */
      });
    return () => { active = false; };
  }, []);
  const [sending, setSending] = useState(false);
  const [stoppedTaskId, setStoppedTaskId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [deletion, setDeletion] = useState<DeletionJob | null>(null);
  const [sideTab, setSideTab] = useState<"monitor" | "memory" | "status" | "artifacts">("monitor");
  const [opsMoreOpen, setOpsMoreOpen] = useState(false);
  const [workspaceExpanded, setWorkspaceExpanded] = useState(false);
  const [panelOpen, setPanelOpen] = useState(true);
  const [workspaceLevel, setWorkspaceLevel] = useState<"status" | "preview" | "takeover">("preview");
  const [mobileScreenOpen, setMobileScreenOpen] = useState(false);
  const railAutoOpened = useRef(false);
  const [mobileRosterOpen, setMobileRosterOpen] = useState(false);
  const [stageMoreOpen, setStageMoreOpen] = useState(false);
  const [botSwitcherOpen, setBotSwitcherOpen] = useState(false);
  const [botsLoading, setBotsLoading] = useState(true);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  /** task_id -> disposition row from /api/dispositions (honest; may be sparse). */
  const [dispositionsByTask, setDispositionsByTask] = useState<Record<string, BotDisposition>>({});
  const [appDialog, setAppDialog] = useState<null | {
    kind: "confirm" | "prompt";
    title: string;
    body?: string;
    confirmLabel?: string;
    cancelLabel?: string;
    danger?: boolean;
    allowEmpty?: boolean;
    initial?: string;
    placeholder?: string;
    onConfirm: (value?: string) => void;
  }>(null);
  const [promptDraft, setPromptDraft] = useState("");
  const [dialogConfirming, setDialogConfirming] = useState(false);
  const dialogLockRef = useRef(false);
  const [railCueVisible, setRailCueVisible] = useState(false);
  const [traceTaskId, setTraceTaskId] = useState<string | null>(null);
  const [theme, setTheme] = useState<"system" | "light" | "dark">("system");
  const [productMode, setProductMode] = useState<"use" | "build">("use");
  const [systemDark, setSystemDark] = useState(false);
  const [candidateDrafts, setCandidateDrafts] = useState<Record<string, string>>({});
  const [editingMemory, setEditingMemory] = useState<{ id: string; text: string } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [clearConfirm, setClearConfirm] = useState("");
  const [clearing, setClearing] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const runRef = useRef<RunState>(run);
  const activeBotRef = useRef<string | null>(null);
  const sessionRef = useRef<string | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  const chatRef = useRef<HTMLDivElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const autoScrollRef = useRef(false);
  const stickToBottom = useRef(true);
  runRef.current = run;
  sessionRef.current = sessionId;

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem("financial-bot-theme");
      if (stored === "light" || stored === "dark" || stored === "system") setTheme(stored);
    } catch {
      // Keep system default.
    }
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    setSystemDark(query.matches);
    const onChange = (event: MediaQueryListEvent) => setSystemDark(event.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k" && !studioOpen) {
        event.preventDefault();
        setBotSwitcherOpen((current) => !current);
      }
      if (event.key === "Escape") {
        setBotSwitcherOpen(false);
        setMobileScreenOpen(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [studioOpen]);

  const effectiveTheme = theme === "system" ? (systemDark ? "dark" : "light") : theme;
  function cycleTheme() {
    const next = theme === "light" ? "dark" : theme === "dark" ? "system" : "light";
    setTheme(next);
    try { window.localStorage.setItem("financial-bot-theme", next); } catch { /* Ignore storage. */ }
  }
  function openBotScreen() {
    setPanelOpen(true);
    setWorkspaceLevel("preview");
    if (window.matchMedia("(max-width: 980px)").matches) setMobileScreenOpen(true);
    else setWorkspaceExpanded(false);
    setRailCueVisible(false);
    try { sessionStorage.setItem("financial-bot-rail-cue-seen", "1"); } catch { /* private mode */ }
  }
  function closeBotScreen() {
    if (window.matchMedia("(max-width: 980px)").matches) {
      setPanelOpen(false);
      setMobileScreenOpen(false);
      setWorkspaceExpanded(false);
      setWorkspaceLevel("status");
      return;
    }
    setWorkspaceLevel("status");
    setPanelOpen(true);
    setWorkspaceExpanded(false);
    setMobileScreenOpen(false);
  }
  function expandWorkspaceTakeover() {
    setPanelOpen(true);
    setWorkspaceLevel("takeover");
    setWorkspaceExpanded(true);
    setMobileScreenOpen(false);
  }
  function collapseWorkspaceToPreview() {
    setPanelOpen(true);
    setWorkspaceLevel("preview");
    setWorkspaceExpanded(false);
  }

  // Product hierarchy: 主页 → 使用(/bot) → 调整能力(/bot?mode=build).
  // URL ?mode= wins over localStorage so landing "开始使用" always opens 使用.
  useEffect(() => {
    try {
      const params = new URLSearchParams(window.location.search);
      const fromUrl = params.get("mode");
      if (fromUrl === "use" || fromUrl === "build") {
        setProductMode(fromUrl);
        try { window.localStorage.setItem("financial-bot-product-mode-v1", fromUrl); } catch { /* private mode */ }
      } else {
        const stored = window.localStorage.getItem("financial-bot-product-mode-v1");
        if (stored === "use" || stored === "build") setProductMode(stored);
      }
    } catch { /* private mode */ }
    setFocusPoints(loadFocusPoints());
  }, []);

  useEffect(() => {
    const before = JSON.stringify(loadFocusPoints());
    const inbox = buildWatchingInbox({
      en,
      focusPoints,
      notifications,
      monitors,
      allowDemo: WATCH_ENABLED,
    });
    setWatchingAlerts(inbox);
    const after = loadFocusPoints();
    if (JSON.stringify(after) !== before) setFocusPoints(after);
  }, [en, focusPoints, notifications, monitors]);

  // One-shot discoverability cue for collapsed right rail — only meaningful in 打造模式.
  useEffect(() => {
    if (productMode !== "build") {
      setRailCueVisible(false);
      return;
    }
    try {
      if (sessionStorage.getItem("financial-bot-rail-cue-seen") === "1") return;
    } catch { /* private mode */ }
    setRailCueVisible(true);
  }, [productMode]);

  useEffect(() => {
    if (appDialog) {
      dialogLockRef.current = false;
      setDialogConfirming(false);
    }
  }, [appDialog]);

  // First empty session stays conversation-first; ops rail auto-open only in 打造模式.
  // Leftover artifacts alone should not reopen the console over a welcome thread.
  useEffect(() => {
    if (productMode !== "build") return;
    if (railAutoOpened.current) return;
    const hasLiveOps = monitors.length > 0
      || notifications.length > 0
      || deliveries.length > 0
      || Boolean(run.taskId && !isTerminal(run.status));
    if (!hasLiveOps) return;
    railAutoOpened.current = true;
    setPanelOpen(true);
  }, [productMode, monitors.length, notifications.length, deliveries.length, run.taskId, run.status]);

  // Keep the three-column shell (roster | chat | workspace) in 使用模式.
  useEffect(() => {
    if (productMode === "use") {
      setPanelOpen(true);
      setWorkspaceLevel((level) => (level === "takeover" ? "preview" : level));
      setMobileScreenOpen(false);
      setMonitorDialogOpen(false);
    }
  }, [productMode]);

  const refreshMemories = useCallback(async (botId?: string | null, includeLegacy?: boolean) => {
    const requestedBotId = botId ?? activeBotId;
    const selected = bots.find((bot) => bot.id === requestedBotId);
    const data = await getMemories(requestedBotId ?? undefined, includeLegacy ?? Boolean(selected?.is_default));
    if (requestedBotId && !isCurrentBotResponse(requestedBotId, activeBotRef.current)) return;
    setMemories(data);
    if (data.latest_deletion) setDeletion(data.latest_deletion);
  }, [activeBotId, bots]);

  const refreshSessions = useCallback(async (botId?: string | null) => {
    setSessionsLoading(true);
    try {
      const requestedBotId = botId ?? activeBotId;
      const data = await listSessions(requestedBotId ?? undefined, 50);
      if (requestedBotId && !isCurrentBotResponse(requestedBotId, activeBotRef.current)) return;
      setSessions(data.sessions);
    } catch {
      // Session history is optional; keep the active conversation usable.
    } finally {
      setSessionsLoading(false);
    }
  }, [activeBotId]);

  const refreshDispositions = useCallback(async (sid?: string | null) => {
    const session = sid ?? sessionId;
    if (!session) return;
    try {
      const data = await listDispositions(session, 80);
      const map: Record<string, BotDisposition> = {};
      for (const item of data.dispositions || []) {
        if (item.task_id) map[item.task_id] = item;
      }
      setDispositionsByTask(map);
    } catch {
      // Disposition is honesty chrome; missing it must not block the transcript.
    }
  }, [sessionId]);

  const refreshMonitors = useCallback(async (botId?: string | null) => {
    if (!botId) return;
    try {
      const data = await listMonitors(botId);
      if (!isCurrentBotResponse(botId, activeBotRef.current)) return;
      setMonitors(data.monitors);
      const before = JSON.stringify(loadFocusPoints());
      setWatchingAlerts(buildWatchingInbox({
        en,
        focusPoints: loadFocusPoints(),
        notifications,
        monitors: data.monitors,
        allowDemo: WATCH_ENABLED,
      }));
      const after = loadFocusPoints();
      if (JSON.stringify(after) !== before) setFocusPoints(after);
    } catch {
      // The monitor panel must not block the research conversation.
    }
  }, [en, notifications]);

  const refreshNotifications = useCallback(async (botId?: string | null) => {
    if (!botId) return;
    try {
      const data = await listNotifications(botId, false, 20);
      if (!isCurrentBotResponse(botId, activeBotRef.current)) return;
      setNotifications(data.notifications);
      setNotificationsError(null);
      const before = JSON.stringify(loadFocusPoints());
      setWatchingAlerts(buildWatchingInbox({
        en,
        focusPoints: loadFocusPoints(),
        notifications: data.notifications,
        monitors,
        allowDemo: WATCH_ENABLED,
      }));
      const after = loadFocusPoints();
      if (JSON.stringify(after) !== before) setFocusPoints(after);
    } catch {
      setNotificationsError(en ? "Notifications are temporarily unreachable. Try again later." : "通知服务暂时连不上，可稍后再试");
    }
  }, [en, monitors]);

  const refreshDeliveries = useCallback(async (botId?: string | null) => {
    if (!botId) return;
    try {
      const data = await listDeliveries(botId, 20);
      if (!isCurrentBotResponse(botId, activeBotRef.current)) return;
      setDeliveries(data.deliveries);
      setDeliveriesError(null);
    } catch {
      setDeliveriesError(en ? "Delivery history is temporarily unreachable. Try again later." : "投递记录暂时连不上，可稍后再试");
    }
  }, [en]);

  const refreshArtifacts = useCallback(async (botId?: string | null) => {
    if (!botId) return;
    try {
      const data = await listArtifacts(botId, 20);
      if (!isCurrentBotResponse(botId, activeBotRef.current)) return;
      setArtifacts(data.artifacts);
    } catch {
      // Artifacts are optional for the conversation flow.
    }
  }, []);

  const refreshTasks = useCallback(async (session: string) => {
    const data = await listTasks(session, 40);
    if (session === sessionRef.current) setTasks(data.tasks);
    return data.tasks;
  }, []);

  const refreshMessages = useCallback(async (session: string) => {
    try {
      const data = await listMessages(session, 300);
      if (session === sessionRef.current) {
        setMessages(data.messages);
        void readThread(session).then(() => {
          setSessions((current) => current.map((item) => item.id === session ? { ...item, unread: 0 } : item));
        }).catch(() => undefined);
      }
    } catch {
      // Older local databases may not have the message table yet; task cards
      // remain the fallback transcript.
    }
  }, []);

  useEffect(() => {
    if (!activeBotId) return;
    const timer = window.setInterval(() => {
      void refreshMonitors(activeBotId);
      void refreshNotifications(activeBotId);
      void refreshDeliveries(activeBotId);
      void refreshArtifacts(activeBotId);
      if (sessionId) void refreshMessages(sessionId);
    }, 30_000);
    return () => window.clearInterval(timer);
  }, [activeBotId, refreshArtifacts, refreshDeliveries, refreshMessages, refreshMonitors, refreshNotifications, sessionId]);

  const refreshAll = useCallback(
    async (session: string) => {
      await Promise.allSettled([
        refreshTasks(session),
        refreshMessages(session),
        refreshMemories(),
        refreshDispositions(session),
        getBotStatus().then(setStatus),
        refreshSessions(),
      ]);
    },
    [refreshMemories, refreshMessages, refreshTasks, refreshSessions, refreshDispositions],
  );

  const followTask = useCallback(
    async (taskId: string) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      const existing = runRef.current;
      setRun(
        existing.taskId === taskId
          ? { ...existing, connection: "connecting" }
          : { ...startRun(taskId), connection: "connecting" },
      );
      let after = existing.taskId === taskId ? existing.lastSequence : 0;
      const session = sessionRef.current;

      for (let attempt = 0; attempt < 5; attempt += 1) {
        if (controller.signal.aborted) return;
        try {
          await streamTaskEvents(taskId, after, {
            signal: controller.signal,
            onEvent: (event) => {
              after = Math.max(after, event.sequence);
              setRun((previous) => {
                const base = previous.taskId === taskId ? previous : startRun(taskId);
                return applyTaskEvent(base, event);
              });
            },
            onDone: (info) => {
              setRun((previous) => ({
                ...setRunStatus(previous, info.status as TaskStatus),
                connection: "live",
              }));
            },
          });
          const snapshot = await getTask(taskId).catch(() => null);
          if (snapshot) {
            setRun((previous) => ({
              ...setRunStatus(previous, snapshot.task.status),
              connection: "live",
            }));
          }
          if (session) await refreshAll(session);
          return;
        } catch {
          if (controller.signal.aborted) return;
          if (attempt >= 4) {
            setRun((previous) => ({ ...previous, connection: "offline" }));
            setError(
              en
                ? "Event stream disconnected. The server state is unknown; no running state is shown."
                : "事件流连接中断，无法确认服务端状态，界面不再显示运行中。",
            );
            return;
          }
          setRun((previous) => ({
            ...previous,
            connection: "reconnecting",
            status: isTerminal(previous.status) ? previous.status : "unknown",
          }));
          await new Promise((resolve) => setTimeout(resolve, 600 * 2 ** attempt));
          try {
            const snapshot = await getTask(taskId);
            if (isTerminal(snapshot.task.status)) {
              setRun((previous) => ({
                ...setRunStatus(previous, snapshot.task.status),
                connection: "live",
              }));
              if (session) await refreshAll(session);
              return;
            }
            // Keep the existing cursor so no skipped event is fabricated.
            after = runRef.current.lastSequence;
          } catch {
            // Retry the stream with the same cursor.
          }
        }
      }
    },
    [en, refreshAll],
  );

  useEffect(() => {
    if (!activeBotId) {
      setPersona(null);
      return;
    }
    const stored = window.localStorage.getItem(`${PERSONA_KEY}:${activeBotId}`);
    const known = ["beginner", "ticker", "filings", "portfolio", "news", "pro", "watchlist"];
    setPersona(stored && known.includes(stored) ? (stored as ResearchPersona) : null);
  }, [activeBotId]);

  useEffect(() => {
    if (!sessionId) {
      setOnboardingRecord(null);
      return;
    }
    try {
      const raw = window.localStorage.getItem(`financial-bot-onboarding-v1:${sessionId}`);
      setOnboardingRecord(raw ? (JSON.parse(raw) as OnboardingRecord) : null);
    } catch {
      setOnboardingRecord(null);
    }
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId) return;
    void refreshDispositions(sessionId);
  }, [sessionId, refreshDispositions]);

  useEffect(() => {
    if (!sessionId || !activeBotId) return;
    if (switchingBot) return;
    void ensureWelcome(
      sessionId,
      activeBotId,
      persona ?? "beginner",
      en ? "en-US" : "zh-CN",
    ).then(() => {
      rememberSessionBot(sessionId, activeBotId);
    }).catch((caught) => {
      // Welcome persistence is additive; the local onboarding layer still works.
      // Except when the stored conversation belongs to another Bot: then the
      // header and the transcript disagree, and every send would 409. Follow
      // the conversation's owner instead.
      const ownerBotId = foreignSessionOwner(caught);
      if (ownerBotId) void adoptBotForSession(ownerBotId);
    });
    // Adoption is guarded by `ownerBotId === activeBotId`, so this cannot loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeBotId, en, persona, sessionId, switchingBot]);

  useEffect(() => {
    let sid = window.localStorage.getItem(SESSION_KEY);
    if (!sid) {
      sid = uid();
      window.localStorage.setItem(SESSION_KEY, sid);
    }
    setSessionId(sid);
    void (async () => {
      const [statusResult, taskResult, botResult, messageResult, portfolioResult, threadStateResult] = await Promise.allSettled([
        withStartupRetry(getBotStatus),
        listTasks(sid, 40),
        withStartupRetry(listBots),
        listMessages(sid, 300),
        listPortfolioPositions(),
        getThreadState(sid),
      ]);
      if (statusResult.status === "fulfilled") setStatus(statusResult.value);
      let loaded: BotTask[] = [];
      if (taskResult.status === "fulfilled") {
        loaded = taskResult.value.tasks;
        setTasks(loaded);
        void refreshDispositions(sid);
      } else if (taskResult.status === "rejected") {
        setError(taskResult.reason instanceof Error ? taskResult.reason.message : String(taskResult.reason));
      }
      if (messageResult.status === "fulfilled") setMessages(messageResult.value.messages);
      if (portfolioResult.status === "fulfilled") setPortfolioPositions(portfolioResult.value);
      if (threadStateResult.status === "fulfilled") setThreadState(threadStateResult.value);
      if (botResult.status === "fulfilled") {
        setBots(botResult.value.bots);
        const stored = window.localStorage.getItem("financial-bot-active-bot-v1");
        let selected = botResult.value.bots.find((bot) => bot.id === stored) || botResult.value.bots.find((bot) => bot.is_default) || botResult.value.bots[0];
        // The conversation wins: another tab may have switched the Bot while this
        // one still holds an older thread. Follow the thread's real owner instead
        // of showing it under the wrong assistant.
        let knownOwner = sessionBotOwner(sid);
        if (!knownOwner && sid) {
          // Ask the server once; `bot_id` is part of the session summary now, so
          // the mismatch is resolved before the welcome probe can 409.
          try {
            const all = await listSessions(undefined, 100);
            knownOwner = all.sessions.find((item) => item.id === sid)?.bot_id ?? null;
            if (knownOwner) rememberSessionBot(sid, knownOwner);
          } catch {
            // Offline: the welcome probe below still repairs the pair.
          }
        }
        const ownerBot = knownOwner ? botResult.value.bots.find((bot) => bot.id === knownOwner) : null;
        if (ownerBot && selected && ownerBot.id !== selected.id) {
          selected = ownerBot;
          setNotice(
            en
              ? `This conversation belongs to ${ownerBot.name}; switched back to it.`
              : `这段对话属于「${ownerBot.name}」，已自动切回该助手。`,
          );
        }
        if (selected) {
          activeBotRef.current = selected.id;
          setActiveBotId(selected.id);
          window.localStorage.setItem("financial-bot-active-bot-v1", selected.id);
          void refreshSessions(selected.id);
          void refreshMemories(selected.id, selected.is_default);
          void refreshMonitors(selected.id);
          void refreshNotifications(selected.id);
          void refreshDeliveries(selected.id);
          void refreshArtifacts(selected.id);
        }
      } else if (botResult.status === "rejected") {
        setError(botResult.reason instanceof Error ? botResult.reason.message : String(botResult.reason));
      }
      setBotsLoading(false);
      const active = loaded.find((task) => !isTerminal(task.status));
      if (active) {
        setRun({ ...EMPTY_RUN, taskId: active.id, status: active.status, connection: "connecting" });
        if (active.research_mode === "openmuse_shared") void followOpenMuseSharedTask(active.id);
        else void followTask(active.id);
      }
    })();
    return () => abortRef.current?.abort();
    // Mount only: the session id is stable for the component lifetime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const scrollToLatest = useCallback((behavior: ScrollBehavior = "smooth") => {
    const element = chatRef.current;
    if (!element || !stickToBottom.current) return;
    autoScrollRef.current = true;
    element.scrollTo({ top: element.scrollHeight, behavior });
    window.setTimeout(() => {
      autoScrollRef.current = false;
    }, behavior === "smooth" ? 420 : 80);
  }, []);

  useEffect(() => {
    if (!stickToBottom.current) return;
    const frame = window.requestAnimationFrame(() => scrollToLatest("smooth"));
    return () => window.cancelAnimationFrame(frame);
  }, [tasks.length, run.lastSequence, run.answerReady, pendingApproval, queuedPrompts.length, scrollToLatest]);

  // Cards, markdown blocks and streamed text can change the scroll height
  // without changing the event sequence. Observe the transcript subtree so the
  // view follows actual DOM growth, not only task/event count.
  useEffect(() => {
    if (tasks.length === 0 && !pendingApproval && !welcomeDismissed) {
      stickToBottom.current = false;
      window.requestAnimationFrame(() => chatRef.current?.scrollTo({ top: 0, behavior: "auto" }));
    }
  }, [pendingApproval, sessionId, tasks.length, welcomeDismissed]);

  useEffect(() => {
    if (!stickToBottom.current) return;
    const frame = window.requestAnimationFrame(() => scrollToLatest("auto"));
    return () => window.cancelAnimationFrame(frame);
  }, [emptyPortfolioPrompt, notifications.length, pendingApproval, scrollToLatest]);

  useEffect(() => {
    const element = chatRef.current;
    if (!element) return;
    const observer = new MutationObserver(() => {
      if (!stickToBottom.current) return;
      window.requestAnimationFrame(() => scrollToLatest("auto"));
    });
    observer.observe(element, { childList: true, subtree: true, characterData: true });
    const onResize = () => {
      if (stickToBottom.current) scrollToLatest("auto");
    };
    window.addEventListener("resize", onResize);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", onResize);
    };
  }, [scrollToLatest]);

  const showLocalWelcome = !welcomeDismissed && tasks.length === 0;
  const orphanMessages = messages.filter(
    (message) => !message.task_id
      && message.role === "assistant"
      && message.kind !== "welcome",
  );

  useEffect(() => {
    const current = sessions.find((item) => item.id === sessionId);
    if (current?.kind === "side") setWelcomeDismissed(true);
  }, [sessionId, sessions]);

  const activeTask = tasks.find((task) => task.id === run.taskId) ?? null;
  const busy = Boolean(activeTask && !isTerminal(activeTask.status) && run.connection !== "offline") || Boolean(pendingApproval);
  const botState = useMemo(
    () => botStateFromTask(
      run.taskId ? run.status : activeTask?.status ?? null,
      run.connection,
      activeTask?.waiting_reason ?? null,
    ),
    [activeTask?.status, activeTask?.waiting_reason, run.connection, run.status, run.taskId],
  );
  // The avatar carries the lifecycle, not just the HTTP task status.  A task
  // that is running but has not emitted a tool step is still "thinking";
  // as soon as a real tool step appears it switches to "working".
  const avatarState = useMemo(() => {
    if (botState === "running" && !run.steps.some((step) => step.stepId.startsWith("tool:"))) {
      return "queued";
    }
    return botState;
  }, [botState, run.steps]);
  const pendingCandidates = memories?.pending_candidates ?? [];
  const activeBot = bots.find((item) => item.id === activeBotId) ?? null;

  useEffect(() => {
    if (!showLocalWelcome || !activeBotId || !activeBot) {
      setOnboardPlan(null);
      return;
    }
    const version = `${activeBot.updated_at || activeBot.prompt_version || "1"}`;
    const cacheKey = `financial-bot-onboarding-v1:${activeBotId}:${version}:${en ? "en" : "zh"}`;
    try {
      const cached = window.localStorage.getItem(cacheKey);
      if (cached) {
        setOnboardPlan(JSON.parse(cached) as OnboardPlan);
        return;
      }
    } catch {
      // Corrupt cache should never block the page.
    }
    let cancelled = false;
    const timer = window.setTimeout(() => cancelled = true, 6000);
    void getBotOnboarding(activeBotId, en ? "en-US" : "zh-CN")
      .then((data) => {
        if (cancelled || !data.onboarding) return;
        setOnboardPlan(data.onboarding);
        try {
          window.localStorage.setItem(cacheKey, JSON.stringify(data.onboarding));
        } catch {
          // Storage is a cache, not a requirement.
        }
      })
      .catch(() => {
        // The capability-based list stays as the fallback.
      })
      .finally(() => window.clearTimeout(timer));
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeBotId, activeBot?.updated_at, activeBot?.prompt_version, en, showLocalWelcome]);


  function choosePersona(next: ResearchPersona | null) {
    setPersona(next);
    if (!activeBotId) return;
    const key = `${PERSONA_KEY}:${activeBotId}`;
    if (next) window.localStorage.setItem(key, next);
    else window.localStorage.removeItem(key);
  }

  function focusComposer() {
    composerRef.current?.focus();
  }


  async function mausbotAskLocal(query: string, displayQuery: string | undefined, botId: string) {
    const currentSessionId = sessionId || "local-mausbot";
    const taskId = `maus-${uid()}`;
    const now = new Date().toISOString();
    const shown = displayQuery || query;
    setInput("");
    setWelcomeDismissed(true);
    setTasks((previous) => [
      ...previous.filter((item) => item.id !== taskId),
      {
        id: taskId,
        session_id: currentSessionId,
        query: shown,
        language: en ? "en-US" : "zh-CN",
        research_mode: "quick",
        status: "running",
        result: null,
        cancel_requested: false,
        input_tokens: 0,
        output_tokens: 0,
        cost_usd: 0,
        created_at: now,
        updated_at: now,
        started_at: now,
        sequence: previous.length + 1,
        ui_context: { page: "bot", last_action: "mausbot_ask", view: "transcript" },
      },
    ]);
    setMessages((previous) => [
      ...previous,
      {
        id: `maus-user-${taskId}`,
        bot_id: botId,
        session_id: currentSessionId,
        role: "user",
        kind: "text",
        content: shown,
        task_id: taskId,
        sequence: previous.length + 1,
        created_at: now,
      },
    ]);
    setRun({ ...startRun(taskId), status: "running", connection: "live" });
    try {
      const response = await fetch("/api/mausbot/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: query }),
        cache: "no-store",
      });
      const raw = await response.text();
      let payload: any = null;
      try { payload = raw ? JSON.parse(raw) : null; } catch { payload = null; }
      if (!response.ok) {
        const detail = payload?.detail || raw || `MausBot error (${response.status})`;
        throw new Error(String(detail));
      }
      const answer = String(payload?.answer || "").trim();
      if (!answer) throw new Error(en ? "Empty MausBot reply" : "MausBot 没有返回内容");
      const doneAt = new Date().toISOString();
      setMessages((previous) => [
        ...previous,
        {
          id: `maus-assistant-${taskId}`,
          bot_id: botId,
          session_id: currentSessionId,
          role: "assistant",
          kind: "answer",
          content: answer,
          task_id: taskId,
          sequence: previous.length + 1,
          created_at: doneAt,
          metadata: { engine: "mausbot-deepseek" },
        },
      ]);
      setTasks((previous) => previous.map((task) => task.id === taskId ? {
        ...task,
        status: "completed",
        updated_at: doneAt,
        finished_at: doneAt,
        result: {
          summary: answer,
          answer,
          citations: [],
          steps: [],
        } as any,
      } : task));
      setRun((current) => ({ ...setRunStatus(current.taskId === taskId ? current : startRun(taskId), "completed"), connection: "live" }));
      setNotice(en ? "Answered via NAS MausBot (DeepSeek)." : "已由 NAS MausBot（DeepSeek）作答。");
    } catch (caught) {
      const detail = caught instanceof Error ? caught.message : String(caught);
      const failAt = new Date().toISOString();
      setTasks((previous) => previous.map((task) => task.id === taskId ? {
        ...task,
        status: "failed",
        error_message: detail,
        updated_at: failAt,
        finished_at: failAt,
      } : task));
      setRun((current) => ({ ...setRunStatus(current.taskId === taskId ? current : startRun(taskId), "failed"), connection: "offline" }));
      setError(detail);
      setInput(query);
    }
  }

  async function submit(rawQuery: string, displayQuery?: string) {
    const query = rawQuery.trim();
    if (!query || !sessionId) return;
    // Keep the composer usable while the current task is running. The follow-up
    // is queued as a separate task; it is not silently merged into the current
    // plan (that requires a backend replan endpoint).
    if (busy || sending) {
      setQueuedPrompts((current) => [...current, query]);
      setInput("");
      setNotice(en ? "Added to the follow-up queue." : "已加入后续任务队列。");
      return;
    }
    const botId = activeBotId;
    if (!botId) {
      setError(en ? "Select an assistant first." : "请先选择一个助手。");
      return;
    }
    if (
      portfolioPositions !== null
      && portfolioPositions.length === 0
      && /(持仓|组合|portfolio|holdings)/i.test(query)
    ) {
      setEmptyPortfolioPrompt(query);
      setInput("");
      setError(null);
      setNotice(null);
      return;
    }
    setSending(true);
    setError(null);
    setNotice(null);
    try {
      const useMausbot =
        process.env.NEXT_PUBLIC_USE_MAUSBOT === "1"
        || (typeof window !== "undefined" && new URLSearchParams(window.location.search).get("mausbot") === "1");
      if (useMausbot) {
        await mausbotAskLocal(query, displayQuery, botId);
        return;
      }
      if (researchMode === "openmuse_shared") {
        await startTask(query, displayQuery, botId, false, "ask");
        return;
      }
      const preflight = await preflightBot(botId, query);
      if (preflight.requires_approval) {
        const thread = sessions.find((item) => item.id === sessionId);
        if (thread?.approval_mode !== "auto") {
          setPendingApproval({ query, preflight, botId, modelProvider });
          return;
        }
      }
      await startTask(query, displayQuery, botId, preflight.requires_approval, "ask", modelProvider);
    } catch (caught) {
      setError(describeBotError(caught, en));
    } finally {
      setSending(false);
    }
  }

  const followOpenMuseSharedTask = useCallback(async (taskId: string) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    for (let attempt = 0; attempt < 160 && !controller.signal.aborted; attempt += 1) {
      try {
        const snapshot = await syncOpenMuseSharedResearch(taskId);
        const upstream = snapshot.upstream_task;
        if (upstream) {
          const status = openMuseTaskStatus(upstream.status);
          const researchResult = openMuseResearchResult(upstream.result, upstream.evidence);
          setTasks((current) => current.map((task) => task.id === taskId ? {
            ...task,
            status,
            attention: upstream.status === "waiting_input" && upstream.question
              ? { question: upstream.question, allow_free_text: true }
              : null,
            result: researchResult ?? task.result,
            error_message: typeof upstream.error === "string" ? upstream.error : task.error_message,
            updated_at: upstream.updatedAt || new Date().toISOString(),
          } : task));
          setRun((current) => ({ ...setRunStatus(current.taskId === taskId ? current : startRun(taskId), status), connection: "live" }));
          if (["succeeded", "failed", "cancelled"].includes(upstream.status)) return;
        }
        if (snapshot.coordination.reason) {
          setNotice(snapshot.coordination.reason);
          if (snapshot.coordination.state === "blocked") return;
        }
      } catch {
        setRun((current) => ({ ...current, connection: "reconnecting" }));
      }
      await new Promise((resolve) => window.setTimeout(resolve, 2500));
    }
    if (!controller.signal.aborted) setRun((current) => ({ ...current, connection: "offline", status: "unknown" }));
  }, []);

  const answerSharedResearch = useCallback(async (taskId: string, answer: string): Promise<boolean> => {
    try {
      const response = await answerOpenMuseSharedResearch(taskId, answer);
      const status = response.upstream_task.status === "queued" ? "queued" : "unknown";
      setTasks((current) => current.map((task) => task.id === taskId ? {
        ...task,
        status,
        attention: null,
        updated_at: new Date().toISOString(),
      } : task));
      setRun((current) => ({ ...setRunStatus(current.taskId === taskId ? current : startRun(taskId), status), connection: "live" }));
      void followOpenMuseSharedTask(taskId);
      return true;
    } catch (caught) {
      setError(describeBotError(caught, en));
      return false;
    }
  }, [en, followOpenMuseSharedTask]);

  async function startTask(
    query: string,
    displayQuery: string | undefined,
    botId: string,
    modelApproved = false,
    lastAction: "ask" | "confirm_model_budget" = "ask",
    selectedProvider: "deepseek" | "chatgpt" = modelProvider,
    attempt = 0,
    forceQuick = false,
  ) {
    const currentSessionId = sessionId;
    if (!currentSessionId) return;
    setSending(true);
    setStoppedTaskId(null);
    setError(null);
    setNotice(null);
    stickToBottom.current = true;
    try {
      const requestedMode = forceQuick ? "quick" as const : researchMode;
      const effectiveMode = requestedMode === "deep" && !deepAvailable ? "quick" as const : requestedMode;
      if (requestedMode === "deep" && !deepAvailable) {
        setResearchMode("quick");
        setNotice(en ? "Deep research unavailable; started as Quick." : "深度研究暂不可用，已改为快速研究启动。");
      }
      if (effectiveMode === "openmuse_shared") {
        const { task, coordination } = await createOpenMuseSharedResearch({
          query,
          sessionId: currentSessionId,
          botId,
          language: en ? "en-US" : "zh-CN",
          idempotencyKey: uid(),
        });
        setInput("");
        setResearchMode("quick");
        setTasks((previous) => [...previous.filter((item) => item.id !== task.id), { ...task, query: displayQuery || task.query }]);
        setSessions((current) => [
          { id: currentSessionId, title: displayQuery || task.query, updated_at: task.updated_at ?? null, task_count: (current.find((item) => item.id === currentSessionId)?.task_count ?? 0) + 1, last_status: task.status },
          ...current.filter((item) => item.id !== currentSessionId),
        ]);
        setRun({ ...startRun(task.id), status: task.status, connection: "connecting" });
        setNotice(coordination.reason || (en
          ? "OpenMuse research started. Recognized financial questions use the Financial Bot data bridge; recognition and source coverage may be incomplete."
          : "已启动 OpenMuse 自由研究。已识别的金融问题会调用 Financial Bot 数据桥；问题识别与数据覆盖可能不完整。"));
        void refreshMessages(currentSessionId);
        void followOpenMuseSharedTask(task.id);
        void refreshSessions(botId);
        return;
      }
      const { task } = await createTask({
        query,
        sessionId: currentSessionId,
        botId,
        modelApproved,
        language: en ? "en-US" : "zh-CN",
        researchMode: effectiveMode,
        modelProvider: selectedProvider,
        idempotencyKey: uid(),
        uiContext: {
          page: "bot",
          last_action: lastAction,
          task_id: run.taskId && !isTerminal(run.status) ? run.taskId : null,
          task_status: run.taskId && !isTerminal(run.status) ? run.status : null,
          view: traceTaskId ? "trace" : "transcript",
        },
      });
      setInput("");
      setTasks((previous) => {
        const without = previous.filter((item) => item.id !== task.id);
        return [...without, { ...task, query: displayQuery || task.query }];
      });
      setSessions((current) => [
        { id: currentSessionId, title: displayQuery || task.query, updated_at: task.updated_at ?? null, task_count: current.find((item) => item.id === currentSessionId)?.task_count ? (current.find((item) => item.id === currentSessionId)?.task_count ?? 0) + 1 : 1, last_status: task.status },
        ...current.filter((item) => item.id !== currentSessionId),
      ]);
      rememberSessionBot(currentSessionId, botId);
      setRun({ ...startRun(task.id), status: task.status, connection: "connecting" });
      void refreshMessages(currentSessionId);
      void followTask(task.id);
      void refreshSessions(botId);
      void refreshDispositions(currentSessionId);
    } catch (caught) {
      // A concurrent tab may have switched assistants, leaving this thread
      // owned by another Bot. Follow the conversation and retry once inside it
      // instead of leaving the message stranded on a 409.
      const ownerBotId = foreignSessionOwner(caught);
      if (attempt === 0 && ownerBotId && ownerBotId !== botId) {
        await adoptBotForSession(ownerBotId, false);
        await startTask(query, displayQuery, ownerBotId, modelApproved, lastAction, selectedProvider, 1, forceQuick);
        const owner = bots.find((bot) => bot.id === ownerBotId);
        setNotice(
          en
            ? `This conversation belongs to ${owner?.name || "another assistant"}; the message went there.`
            : `这段对话属于「${owner?.name || "另一个助手"}」，消息已发到该助手的对话里。`,
        );
        return;
      }
      setError(describeBotError(caught, en));
      setInput(query);
    } finally {
      setSending(false);
    }
  }

  async function resumeExecution(taskId: string) {
    try {
      const result = await resumeTaskApi(taskId);
      setTasks((current) => current.map((item) => item.id === taskId ? { ...item, ...result.task } : item));
      setRun({ ...startRun(result.task.id), status: result.task.status, connection: "connecting" });
      setStoppedTaskId(null);
      setNotice(en ? "Resuming from the saved task plan." : "已从保存的任务清单继续执行。");
      void followTask(result.task.id);
    } catch (caught) {
      setError(describeBotError(caught, en));
    }
  }

  useEffect(() => {
    if (!queuedPrompts.length || busy || sending || !sessionId) return;
    const [next, ...rest] = queuedPrompts;
    setQueuedPrompts(rest);
    void submit(next, next);
    // submit intentionally uses the latest component state; the queue is the
    // only trigger and must not re-run on unrelated renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy, sending, queuedPrompts, sessionId]);

  /** Follow a conversation back to the Bot that owns it.
   *
   * A thread can only be written by its owner, so a stale `active-bot` key
   * (another tab switched assistants) must never strand the current thread:
   * move the Bot selection onto the real owner and keep the messages where
   * they are instead of 409ing on every send.
   */
  async function adoptBotForSession(ownerBotId: string, announce = true) {
    if (!ownerBotId || ownerBotId === activeBotId) return;
    activeBotRef.current = ownerBotId;
    setActiveBotId(ownerBotId);
    setArtifacts([]); setMonitors([]); setNotifications([]); setDeliveries([]); setMemories(null);
    try {
      window.localStorage.setItem("financial-bot-active-bot-v1", ownerBotId);
    } catch {
      // Private mode: in-memory selection still works for this tab.
    }
    rememberSessionBot(sessionId, ownerBotId);
    const owner = bots.find((bot) => bot.id === ownerBotId);
    if (announce) {
      setNotice(
        en
          ? `This conversation belongs to ${owner?.name || "another assistant"}; switched back to it.`
          : `这段对话属于「${owner?.name || "另一个助手"}」，已自动切回该助手。`,
      );
    }
    await refreshSessions(ownerBotId);
    await refreshMemories(ownerBotId, Boolean(owner?.is_default));
    await refreshMonitors(ownerBotId);
    await refreshNotifications(ownerBotId);
    await refreshDeliveries(ownerBotId);
    await refreshArtifacts(ownerBotId);
  }

  async function selectBot(botId: string) {
    if (!botId || botId === activeBotId) return;
    setQueuedPrompts([]);
    stickToBottom.current = true;
    if (busy) return;
    abortRef.current?.abort();
    // Bot and conversation must move together; suppress the welcome probe (and
    // its "wrong owner" repair) until both are in place.
    setSwitchingBot(true);
    activeBotRef.current = botId;
    setActiveBotId(botId);
    window.localStorage.setItem("financial-bot-active-bot-v1", botId);
    setSessions([]);
    setTasks([]);
    setArtifacts([]); setMonitors([]); setNotifications([]); setDeliveries([]); setMemories(null);
    setRun(EMPTY_RUN);
    try {
      await refreshSessions(botId);
      await refreshMemories(botId, Boolean(bots.find((bot) => bot.id === botId)?.is_default));
      await refreshMonitors(botId);
      await refreshNotifications(botId);
      await refreshDeliveries(botId);
      await refreshArtifacts(botId);
      const latest = (await listSessions(botId, 50)).sessions[0];
      if (!isCurrentBotResponse(botId, activeBotRef.current)) return;
      // Never carry the previous Bot's conversation into a new Bot: with no
      // session of its own, start a clean one instead of reusing a foreign id.
      if (latest) await selectSession(latest.id);
      else newSession();
    } finally {
      // Re-arms the welcome effect for the final (bot, conversation) pair.
      if (isCurrentBotResponse(botId, activeBotRef.current)) setSwitchingBot(false);
    }
  }

  function openCreateBot() {
    setProductMode("build");
    persistProductMode("build");
    setBotSwitcherOpen(false);
    setStudioBot(null);
    setStudioOpen(true);
  }

  function openEditBot() {
    const bot = bots.find((item) => item.id === activeBotId);
    if (!bot) return;
    setProductMode("build");
    persistProductMode("build");
    setBotSwitcherOpen(false);
    setStudioBot(bot);
    setStudioOpen(true);
  }

  function openCreateBotFromUse() {
    setProductModeSafe("build");
    openCreateBot();
  }

  function persistProductMode(next: "use" | "build") {
    try { window.localStorage.setItem("financial-bot-product-mode-v1", next); } catch { /* private mode */ }
  }


  /** Create a watch. Returns the stored point, or null when it already existed.
   *
   * `run` is opt-in: the `＋` / "加一条盯梢" entry point only records the watch,
   * it must never bill a research run the user did not ask for.
   */
  function createFocus(question: string, options: { run?: boolean } = {}): FocusPoint | null {
    const text = question.replace(/^\s*(关注点|盯梢)[:：]\s*/, "").trim();
    const normalized = text.replace(/\s+/g, " ");
    if (normalized.length < 4) {
      setError(en ? "Write what you want to keep watching (at least 4 characters)." : "写一下你要盯什么（至少 4 个字）。");
      return null;
    }
    const existing = loadFocusPoints().find((item) => item.question.replace(/\s+/g, " ").toLowerCase() === normalized.toLowerCase());
    if (existing) {
      setFocusDraft({ open: false, text: "" });
      setNotice(en ? `Already watching: “${existing.question}”.` : `已经在盯了：「${existing.question}」。`);
      return null;
    }
    const created = upsertFocusPoint({ question: normalized, source: "local" });
    const point = ensureFocusHints(created);
    // Try to bind an existing monitor with the same symbol.
    const symbol = (point.symbol || "").toUpperCase();
    const linked = symbol
      ? monitors.find((monitor) => (monitor.symbol || "").toUpperCase() === symbol || (monitor.symbol || "").toUpperCase().includes(symbol))
      : undefined;
    const bound = linked
      ? upsertFocusPoint({ id: point.id, question: point.question, symbol: linked.symbol || point.symbol, monitorId: linked.id, keywords: point.keywords, source: "local" })
      : point;
    setFocusPoints(loadFocusPoints());
    setWatchingAlerts(buildWatchingInbox({
      en,
      focusPoints: loadFocusPoints(),
      notifications,
      monitors,
      allowDemo: WATCH_ENABLED,
    }));
    setFocusDraft({ open: false, text: "" });
    setError(null);
    setNotice(en
      ? `Saved focus: “${bound.question}”. Alerts for matching filings / moves will show up unread.`
      : `已记下关注点：「${bound.question}」。有匹配的公告/异动时会出现在未读提醒。`);
    if (options.run) {
      void submit(promptForFocus(en, bound.question));
    }
    return bound;
  }

  /** Entry point for every "add a watch" affordance.
   *
   * With an explicit question (template cards) it creates + researches in one
   * step; otherwise it opens a small composer so the user writes their own.
   */
  function startTrackFocus(prompt?: string) {
    if (!WATCH_ENABLED) {
      // Watch list disabled: a "track this" template is just a research question.
      if (prompt && prompt.trim()) void submit(prompt);
      return;
    }
    if (prompt && prompt.trim()) {
      createFocus(prompt, { run: true });
      return;
    }
    setFocusDraft({ open: true, text: "" });
  }

  function submitFocusDraft() {
    const created = createFocus(focusDraft.text, { run: false });
    if (created) {
      setWatchingAlerts(buildWatchingInbox({ en, focusPoints: loadFocusPoints(), notifications, monitors, allowDemo: false }));
    }
  }

  function openFocusPoint(point: FocusPoint) {
    const linkedUnread = watchingAlerts.find((item) => item.focusId === point.id && item.status === "unread");
    if (linkedUnread) {
      void openWatchingAlert(linkedUnread);
      return;
    }
    if (point.monitorId) {
      const note = notifications.find((item) => item.monitor_id === point.monitorId && item.status !== "read");
      if (note) {
        void analyzeAlert(note);
        return;
      }
      setPanelOpen(true);
      setSideTab("monitor");
      setOpsMoreOpen(true);
      setNotice(en
        ? `Opened watching list for: ${point.question}`
        : `已打开盯梢：${point.question}`);
      return;
    }
    void submit(promptForFocus(en, point.question));
  }

  async function openWatchingAlert(alert: WatchingAlert) {
    // Mark local/demo unread first so the badge drops immediately.
    if (alert.kind === "local" || alert.kind === "demo") {
      markFocusAlertRead(alert.id);
    }
    if (alert.sourceNotificationId && alert.kind !== "demo") {
      const serverNote = notifications.find((item) => item.id === alert.sourceNotificationId)
        || {
          id: alert.sourceNotificationId,
          bot_id: alert.bot_id || activeBotId || "",
          monitor_id: alert.sourceMonitorId || alert.monitor_id || null,
          title: alert.title,
          body: alert.body,
          severity: alert.severity || "info",
          status: "unread",
          task_id: alert.task_id || null,
          created_at: alert.created_at,
        } satisfies BotNotification;
      try {
        if (serverNote.status !== "read") {
          await readNotification(serverNote.id);
        }
      } catch {
        // Local read state still updates below.
      }
      setNotifications((current) => current.map((item) => item.id === serverNote.id ? { ...item, status: "read" } : item));
      setWatchingAlerts(buildWatchingInbox({
        en,
        focusPoints: loadFocusPoints(),
        notifications: notifications.map((item) => item.id === serverNote.id ? { ...item, status: "read" } : item),
        monitors,
        allowDemo: false,
      }));
      setNotice(en
        ? `Opened alert for focus: ${alert.focusQuestion || "watching item"}`
        : `已打开提醒，对应关注点：${alert.focusQuestion || "我在盯的事"}`);
      void analyzeAlert(serverNote);
      return;
    }

    setWatchingAlerts(buildWatchingInbox({
      en,
      focusPoints: loadFocusPoints(),
      notifications,
      monitors,
      allowDemo: false,
    }));
    const focusLine = alert.focusQuestion
      ? (en ? `Focus: ${alert.focusQuestion}` : `关注点：${alert.focusQuestion}`)
      : (en ? "Focus" : "关注点");
    setNotice(en
      ? `Read alert · ${focusLine}`
      : `已读提醒 · ${focusLine}`);
    void submit(en
      ? `I got an alert on a research focus I am watching.
${focusLine}
What happened: ${alert.body}

Please summarize what changed, what is confirmed vs still a gap, and whether I should keep watching. Do not give trading advice.`
      : `我收到一条关于正在跟踪的关注点的提醒。
${focusLine}
发生了什么：${alert.body}

请说明发生了什么变化、哪些已证实/仍缺什么，以及是否值得继续盯。不要给买卖建议。`);
  }

  function setProductModeSafe(next: "use" | "build") {
    setProductMode(next);
    persistProductMode(next);
    try {
      const url = new URL(window.location.href);
      url.searchParams.set("mode", next);
      window.history.replaceState({}, "", `${url.pathname}?${url.searchParams.toString()}${url.hash}`);
    } catch { /* ignore */ }
    if (next === "use") {
      setStudioOpen(false);
      setStudioBot(null);
    }
  }

  async function onStudioSaved(bot: BotProfile, created: boolean) {
    setBots((current) => {
      const exists = current.some((item) => item.id === bot.id);
      return exists ? current.map((item) => item.id === bot.id ? { ...item, ...bot } : item) : [...current, bot];
    });
    setStudioOpen(false);
    setStudioBot(null);
    if (created) {
      await selectBot(bot.id);
    } else {
      setNotice(en ? "Assistant settings saved." : "助手设置已保存。");
    }
  }

  async function onStudioDeleted(movedTo: string, movedSessions: number) {
    const refreshed = await listBots();
    setBots(refreshed.bots);
    setStudioOpen(false);
    setStudioBot(null);
    const fallback = refreshed.bots.find((item) => item.id === movedTo) || refreshed.bots.find((item) => item.is_default) || refreshed.bots[0];
    if (fallback) await selectBot(fallback.id);
    setNotice(en ? `Assistant deleted; ${movedSessions} conversation(s) moved.` : `助手已删除；${movedSessions} 个对话已转移。`);
  }

  async function saveMonitor() {
    if (!activeBotId || (monitorDraft.condition !== "report" && !monitorDraft.symbol.trim())) return;
    setMonitorSaving(true);
    try {
      const { monitor } = await createMonitor({
        botId: activeBotId,
        name: monitorDraft.condition === "report" ? (en ? "Watchlist report" : "自选股报告") : monitorDraft.symbol.trim().toUpperCase(),
        symbol: monitorDraft.condition === "report" ? "WATCHLIST" : monitorDraft.symbol.trim(),
        condition: monitorDraft.condition,
        threshold: monitorDraft.condition === "price" || monitorDraft.condition === "earnings" ? monitorDraft.threshold : 0,
        direction: monitorDraft.direction,
        intervalMinutes: monitorDraft.condition === "report" ? monitorDraft.interval : undefined,
        sessionId,
      });
      setMonitors((current) => [monitor, ...current]);
      setMonitorDialogOpen(false);
      setMonitorDraft({ symbol: "", condition: "price", threshold: 3, direction: "both", interval: 15 });
      setNotice(en ? "Monitor created. It checks on the next polling cycle." : "监控已创建，将在下一轮巡检时检查。");
    } catch (caught) {
      setError(describeBotError(caught, en));
    } finally {
      setMonitorSaving(false);
    }
  }

  async function toggleMonitor(monitor: BotMonitor) {
    try {
      const next = monitor.status === "active" ? "paused" : "active";
      const { monitor: updated } = await updateMonitor(monitor.id, { status: next });
      setMonitors((current) => current.map((item) => item.id === updated.id ? updated : item));
    } catch (caught) {
      setError(describeBotError(caught, en));
    }
  }

  function removeMonitor(monitor: BotMonitor) {
    setAppDialog({
      kind: "confirm",
      title: en ? "Delete monitor" : "删除监控",
      body: en ? `Delete monitor ${monitor.name}?` : `删除监控 ${monitor.name}？`,
      confirmLabel: en ? "Delete" : "删除",
      danger: true,
      onConfirm: () => {
        void (async () => {
          try {
            await deleteMonitor(monitor.id);
            setMonitors((current) => current.filter((item) => item.id !== monitor.id));
          } catch (caught) {
            setError(describeBotError(caught, en));
          }
        })();
      },
    });
  }

  async function markRead(notification: BotNotification) {
    if (notification.status === "read") return;
    try {
      await readNotification(notification.id);
      const nextNotes = notifications.map((item) => item.id === notification.id ? { ...item, status: "read" as const } : item);
      setNotifications(nextNotes);
      // Mirror into focus-linked local alerts when present.
      const linked = watchingAlerts.find((item) => item.sourceNotificationId === notification.id);
      if (linked) markFocusAlertRead(linked.id);
      setWatchingAlerts(buildWatchingInbox({
        en,
        focusPoints: loadFocusPoints(),
        notifications: nextNotes,
        monitors,
        allowDemo: false,
      }));
    } catch {
      // Keep the inbox usable if the read receipt fails.
    }
  }

  async function analyzeAlert(notification: BotNotification, approved = false) {
    try {
      const result = await analyzeNotification(notification.id, approved);
      const sid = result.session_id || result.task.session_id;
      if (!sid) return;
      activeBotRef.current = result.bot_id;
      setActiveBotId(result.bot_id);
      window.localStorage.setItem("financial-bot-active-bot-v1", result.bot_id);
      setSessionId(sid);
      sessionRef.current = sid;
      window.localStorage.setItem(SESSION_KEY, sid);
      const data = await listTasks(sid, 40);
      setTasks(data.tasks);
      setRun({ ...startRun(result.task.id), status: result.task.status, connection: "connecting" });
      setStoppedTaskId(null);
      void followTask(result.task.id);
      await refreshSessions(result.bot_id);
      await refreshNotifications(result.bot_id);
      setNotice(en ? "Monitoring alert opened as a research task." : "已把监控异动转为研究任务。");
    } catch (caught) {
      if (caught instanceof BotApiError && caught.status === 409) {
        setPendingNotification(notification);
        return;
      }
      setError(describeBotError(caught, en));
    }
  }

  async function retryFailedDelivery(delivery: BotDelivery) {
    try {
      const result = await retryDelivery(delivery.id);
      setDeliveries((current) => current.map((item) => item.id === result.delivery.id ? result.delivery : item));
      setNotice(result.sent ? (en ? "Delivery retried successfully." : "发送重试成功。") : (en ? "Retry failed again." : "重试仍然失败。"));
    } catch (caught) {
      setError(describeBotError(caught, en));
    }
  }

  async function toggleMonitorMute(monitor: BotMonitor) {
    try {
      const minutes = monitor.muted_until ? 0 : 120;
      const { monitor: updated } = await muteMonitor(monitor.id, minutes);
      setMonitors((current) => current.map((item) => item.id === updated.id ? updated : item));
    } catch (caught) {
      setError(describeBotError(caught, en));
    }
  }

  async function openArtifact(artifact: BotArtifact) {
    if (!artifact.session_id) return;
    await selectSession(artifact.session_id);
    window.setTimeout(() => scrollToTask(artifact.id), 350);
  }

  async function onCancel() {
    const taskId = run.taskId;
    if (!taskId) return;
    const sharedResearch = tasks.find((task) => task.id === taskId)?.research_mode === "openmuse_shared";
    if (sharedResearch) {
      abortRef.current?.abort();
      setStoppedTaskId(taskId);
      try {
        const response = await cancelOpenMuseSharedResearch(taskId);
        const cancelledStatus = response.upstream_task.status === "cancelled" ? "cancelled" : "unknown";
        setTasks((previous) => previous.map((task) => task.id === taskId ? { ...task, status: cancelledStatus } : task));
        setRun((previous) => ({ ...setRunStatus(previous, cancelledStatus), connection: "live" }));
        setNotice(en ? `OpenMuse task ${response.upstream_task.status}.` : `OpenMuse 任务状态：${response.upstream_task.status}。`);
      } catch (caught) {
        setRun((previous) => ({ ...previous, status: "unknown", connection: "offline" }));
        setError(describeBotError(caught, en));
      }
      return;
    }
    // Stop the local stream immediately; the server cancel is authoritative but
    // must not leave the composer stuck if a network call is still unwinding.
    abortRef.current?.abort();
    setStoppedTaskId(taskId);
    setTasks((previous) => previous.map((task) => task.id === taskId ? { ...task, status: "cancelled" as TaskStatus } : task));
    setRun((previous) => ({ ...previous, status: "cancelled", connection: "live" }));
    setNotice(en ? "Stop requested. Already-sent external requests may still finish." : "已请求停止；已经发出的外部请求可能仍会完成。");
    try {
      const response = await cancelTask(taskId);
      setNotice(response.cancel.note);
      setRun((previous) => ({ ...previous, status: "cancelled", connection: "live" }));
    } catch (caught) {
      setError(describeBotError(caught, en));
    }
  }

  async function onConfirmCandidate(candidate: MemoryCandidate) {
    const text = (candidateDrafts[candidate.id] ?? candidate.proposed_text).trim();
    if (!text) return;
    try {
      await confirmCandidate(candidate.id, {
        expectedVersion: candidate.version,
        finalText: text,
        idempotencyKey: uid(),
      });
      setNotice(en ? "Viewpoint confirmed and stored." : "观点已由你确认并写入长期记忆。");
      await refreshMemories();
    } catch (caught) {
      const conflict = caught instanceof BotApiError && caught.status === 409;
      setError(
        conflict
          ? en
            ? "This candidate changed in another tab. Refresh and review the latest version."
            : "候选观点版本已变化（可能来自其他窗口），请刷新后按最新版本处理。"
          : caught instanceof Error
            ? caught.message
            : String(caught),
      );
      await refreshMemories().catch(() => undefined);
    }
  }

  async function onDismissCandidate(candidate: MemoryCandidate) {
    try {
      await dismissCandidate(candidate.id, {
        expectedVersion: candidate.version,
        idempotencyKey: uid(),
      });
      setNotice(en ? "Candidate ignored; no memory was written." : "已忽略该候选，没有写入长期记忆。");
      await refreshMemories();
    } catch (caught) {
      setError(describeBotError(caught, en));
    }
  }

  async function onSaveMemory(memory: MemoryItem, text: string) {
    try {
      await patchMemory(memory.id, { expectedVersion: memory.version, text });
      setEditingMemory(null);
      setNotice(en ? "Memory corrected with a new version." : "记忆已更正并生成新版本。");
      await refreshMemories();
    } catch (caught) {
      setError(
        caught instanceof BotApiError && caught.status === 409
          ? en
            ? "Memory version changed. Refresh before editing."
            : "记忆版本已变化，请刷新后再修改。"
          : caught instanceof Error
            ? caught.message
            : String(caught),
      );
    }
  }

  async function onDeleteMemory(memoryId: string) {
    try {
      const response = await deleteMemory(memoryId, uid());
      setDeletion(response.deletion);
      setDeleteTarget(null);
      setNotice(
        response.deletion.status === "blocked"
          ? en
            ? "Local controlled layers were cleared, but the job is blocked because provider retention or backups are unverified."
            : "本机受控数据已按范围清除；但模型供应商保留范围或备份未核实，任务标记为阻塞，不能宣称完全删除。"
          : en
            ? "Deletion completed for the verified layers."
            : "已验证的层已完成删除。",
      );
      await refreshMemories();
    } catch (caught) {
      setError(describeBotError(caught, en));
    }
  }

  async function onClearAll() {
    if (clearConfirm !== (en ? "DELETE ALL" : "删除全部")) return;
    setClearing(true);
    try {
      const response = await deleteAllMemories(uid());
      setDeletion(response.deletion);
      setClearConfirm("");
      setNotice(
        response.deletion.status === "blocked"
          ? en
            ? "Selected local data was cleared; provider/backup layers remain unverified."
            : "所选本机数据已清除；供应商与备份层仍未核实。"
          : en
            ? "Deletion completed for the verified layers."
            : "已验证的层已完成删除。",
      );
      await refreshMemories();
    } catch (caught) {
      setError(describeBotError(caught, en));
    } finally {
      setClearing(false);
    }
  }

  function newSession() {
    setQueuedPrompts([]);
    stickToBottom.current = false;
    if (busy) return;
    abortRef.current?.abort();
    const sid = uid();
    window.localStorage.setItem(SESSION_KEY, sid);
    setSessionId(sid);
    sessionRef.current = sid;
    setStoppedTaskId(null);
    setSessions((current) => [{ id: sid, title: en ? "New conversation" : "新对话", updated_at: null, task_count: 0, last_status: null }, ...current.filter((item) => item.id !== sid)]);
    setTasks([]);
    setMessages([]);
    setEmptyPortfolioPrompt(null);
    setThreadState(null);
    setRun(EMPTY_RUN);
    setInput("");
    setError(null);
    setNotice(null);
    setWelcomeDismissed(false);
    setOnboardingRecord(null);
    window.requestAnimationFrame(() => chatRef.current?.scrollTo({ top: 0, behavior: "auto" }));
  }

  async function newSideThread() {
    if (!activeBotId || busy) return;
    abortRef.current?.abort();
    try {
      const { thread } = await createThread({
        botId: activeBotId,
        parentSessionId: sessionId,
        title: en ? "Side thread" : "独立话题",
      });
      const sid = thread.session_id;
      stickToBottom.current = false;
      sessionRef.current = sid;
      setSessionId(sid);
      window.localStorage.setItem(SESSION_KEY, sid);
      setStoppedTaskId(null);
      setSessions((current) => [{
        id: sid,
        title: thread.title,
        updated_at: thread.updated_at,
        task_count: 0,
        last_status: null,
        kind: "side",
        parent_session_id: thread.parent_session_id,
      }, ...current.filter((item) => item.id !== sid)]);
      setTasks([]);
      setMessages([]);
      setRun(EMPTY_RUN);
      setInput("");
      setError(null);
      setNotice(en ? "Side thread opened. The main thread is unchanged." : "独立话题已打开，主线程保持原样。");
      setWelcomeDismissed(true);
      window.requestAnimationFrame(() => chatRef.current?.scrollTo({ top: 0, behavior: "auto" }));
    } catch (caught) {
      setError(describeBotError(caught, en));
    }
  }

  function removeChat(nextSessionId: string) {
    setAppDialog({
      kind: "confirm",
      title: en ? "Delete chat" : "删除对话",
      body: en ? "Delete this chat and its research history? This cannot be undone." : "删除这条对话及其研究记录？此操作不可撤销。",
      confirmLabel: en ? "Delete" : "删除",
      danger: true,
      onConfirm: () => {
        void (async () => {
          try {
            await deleteSessionApi(nextSessionId);
            setPendingSessionDeletions((current) => current.filter((item) => item.sessionId !== nextSessionId));
            setSessions((current) => current.filter((item) => item.id !== nextSessionId));
            // Deleting a conversation also removes its research outputs; the right
            // rail must not keep showing what was just deleted.
            if (activeBotId) {
              await Promise.all([
                refreshArtifacts(activeBotId),
                refreshNotifications(activeBotId),
                refreshDeliveries(activeBotId),
              ]);
            }
            if (nextSessionId === sessionId) {
              newSession();
            } else {
              setNotice(en ? "Chat deleted." : "对话已删除。");
            }
          } catch (caught) {
            const pending = caught instanceof BotApiError
              ? openMuseDeletionPending(caught.status, caught.payload)
              : null;
            if (pending?.scope === "session" && pending.pendingSessions.includes(nextSessionId)) {
              await refreshSessions(activeBotId);
              setPendingSessionDeletions((current) => {
                const merged = [...current, ...pending.pendingSessions.map((sessionId) => ({ sessionId, botId: activeBotId }))];
                return merged.filter((item, index) => merged.findIndex((candidate) => candidate.sessionId === item.sessionId) === index);
              });
              setError(null);
              setNotice(openMuseDeletionPendingCopy(pending, en ? "en" : "zh"));
              return;
            }
            setError(describeBotError(caught, en));
          }
        })();
      },
    });
  }

  async function updateThreadMeta(nextSessionId: string, patch: { title?: string; pinned?: boolean; archived?: boolean; folder?: string | null; approval_mode?: "ask" | "auto"; research_mode?: "quick" | "deep" }) {
    try {
      const { thread } = await patchThread(nextSessionId, patch);
      setSessions((current) => current.map((item) => item.id === nextSessionId
        ? {
            ...item,
            title: thread.title,
            pinned: Boolean(thread.pinned),
            archived: Boolean(thread.archived),
            folder: thread.folder ?? item.folder,
            approval_mode: thread.approval_mode ?? item.approval_mode,
            research_mode: thread.research_mode ?? item.research_mode,
            updated_at: thread.updated_at,
          }
        : item));
      if (patch.archived) setNotice(en ? "Chat archived." : "对话已归档。");
      else if (patch.pinned !== undefined) setNotice(patch.pinned ? (en ? "Chat pinned." : "对话已置顶。") : (en ? "Chat unpinned." : "已取消置顶。"));
      else setNotice(en ? "Chat renamed." : "对话已重命名。");
    } catch (caught) {
      setError(describeBotError(caught, en));
    }
  }

  function renameThread(nextSessionId: string, currentTitle: string) {
    setPromptDraft(currentTitle);
    setAppDialog({
      kind: "prompt",
      title: en ? "Rename chat" : "重命名对话",
      body: en ? "Chat name" : "对话名称",
      initial: currentTitle,
      confirmLabel: en ? "Save" : "保存",
      onConfirm: (value) => {
        const title = (value ?? "").trim();
        if (!title) return;
        void updateThreadMeta(nextSessionId, { title });
      },
    });
  }

  function moveThreadFolder(nextSessionId: string, currentFolder: string) {
    setPromptDraft(currentFolder);
    setAppDialog({
      kind: "prompt",
      title: en ? "Move to folder" : "移动到文件夹",
      body: en ? "Folder name (empty to clear)" : "文件夹名称（留空清除）",
      initial: currentFolder,
      allowEmpty: true,
      confirmLabel: en ? "Save" : "保存",
      onConfirm: (value) => {
        void updateThreadMeta(nextSessionId, { folder: (value ?? "").trim() || null });
      },
    });
  }

  function clearHistory() {
    if (!activeBotId) return;
    setAppDialog({
      kind: "confirm",
      title: en ? "Clear all chats" : "清空全部对话",
      body: en ? "Delete all chats for this assistant? Memory stays; this cannot be undone." : "删除这个助手的全部对话？长期记忆会保留；此操作不可撤销。",
      confirmLabel: en ? "Delete all" : "全部删除",
      danger: true,
      onConfirm: () => {
        void (async () => {
          try {
            const result = await clearBotSessions(activeBotId);
            setPendingSessionDeletions([]);
            setSessions([]);
            newSession();
            setNotice(en ? `Deleted ${result.sessions} chat(s).` : `已删除 ${result.sessions} 条对话。`);
          } catch (caught) {
            const pending = caught instanceof BotApiError
              ? openMuseDeletionPending(caught.status, caught.payload)
              : null;
            if (pending?.scope === "bulk") {
              await refreshSessions(activeBotId);
              setPendingSessionDeletions((current) => {
                const merged = [...current, ...pending.pendingSessions.map((sessionId) => ({ sessionId, botId: activeBotId }))];
                return merged.filter((item, index) => merged.findIndex((candidate) => candidate.sessionId === item.sessionId) === index);
              });
              setError(null);
              setNotice(openMuseDeletionPendingCopy(pending, en ? "en" : "zh"));
              return;
            }
            setError(describeBotError(caught, en));
          }
        })();
      },
    });
  }

  function removeBot() {
    if (!activeBotId) return;
    const bot = bots.find((item) => item.id === activeBotId);
    if (!bot || bot.is_default) return;
    setAppDialog({
      kind: "confirm",
      title: en ? "Delete assistant" : "删除助手",
      body: en ? `Delete ${bot.name}? Its chats and memories move to the default Bot.` : `删除 ${bot.name}？它的对话和记忆会转移到默认 Bot。`,
      confirmLabel: en ? "Delete" : "删除",
      danger: true,
      onConfirm: () => {
        void (async () => {
          try {
            const result = await deleteBot(bot.id);
            await onStudioDeleted(result.moved_to, result.moved_sessions);
          } catch (caught) {
            setError(describeBotError(caught, en));
          }
        })();
      },
    });
  }

  function scrollToTask(taskId: string) {
    document.getElementById(`task-${taskId}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function selectSession(nextSessionId: string) {
    if (!nextSessionId || nextSessionId === sessionId) return;
    stickToBottom.current = true;
    abortRef.current?.abort();
    sessionRef.current = nextSessionId;
    setSessionId(nextSessionId);
    setStoppedTaskId(null);
    window.localStorage.setItem(SESSION_KEY, nextSessionId);
    void readThread(nextSessionId).then(() => {
      setSessions((current) => current.map((item) => item.id === nextSessionId ? { ...item, unread: 0 } : item));
    }).catch(() => undefined);
    const targetSession = sessions.find((item) => item.id === nextSessionId);
    if (targetSession?.bot_id) rememberSessionBot(nextSessionId, targetSession.bot_id);
    setWelcomeDismissed(targetSession?.kind === "side");
    if (targetSession?.research_mode === "deep" || targetSession?.research_mode === "quick") {
      setResearchMode(targetSession.research_mode);
    }
    setTasks([]);
    setMessages([]);
    setThreadState(null);
    setRun(EMPTY_RUN);
    setError(null);
    try {
      const [tasks] = await Promise.all([
        refreshTasks(nextSessionId),
        refreshMessages(nextSessionId),
      ]);
      if (sessionRef.current !== nextSessionId) return;
      void getThreadState(nextSessionId).then((state) => {
        if (sessionRef.current === nextSessionId) setThreadState(state);
      }).catch(() => {
        if (sessionRef.current === nextSessionId) setThreadState(null);
      });
      stickToBottom.current = true;
      window.requestAnimationFrame(() => scrollToLatest("auto"));
      const active = tasks.find((task) => !isTerminal(task.status));
      if (active) {
        setRun({ ...startRun(active.id), status: active.status, connection: "connecting" });
        void followTask(active.id);
      }
    } catch (caught) {
      if (sessionRef.current === nextSessionId) setError(describeBotError(caught, en));
    }
  }

  const successPaths = successPathsFor(en).filter((path) => WATCH_ENABLED || path.id !== "track");
  const examplePrompts = successPaths.map((path) => path.label);
  useEffect(() => {
    if (!run.taskId || isTerminal(run.status)) {
      setRunStuck(false);
      return;
    }
    setRunStuck(false);
    const timer = window.setTimeout(() => setRunStuck(true), 90000);
    return () => window.clearTimeout(timer);
  }, [run.taskId, run.status, run.lastSequence]);

  const unreadCount = watchingAlerts.filter((item) => item.status === "unread").length;

  // Banners float over the shell now; a transient notice should clear itself so
  // it does not sit on top of the header until the user hunts for the ×.
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 6000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const pendingDeletionKey = JSON.stringify(pendingSessionDeletions);
  useEffect(() => {
    const pendingTargets = JSON.parse(pendingDeletionKey) as Array<{ sessionId: string; botId: string | null }>;
    if (pendingTargets.length === 0) return;
    let attempts = 0;
    let cancelled = false;
    const timer = window.setInterval(() => {
      attempts += 1;
      void (async () => {
        try {
          const remaining: typeof pendingTargets = [];
          for (const botId of new Set(pendingTargets.map((target) => target.botId))) {
            const data = await listSessions(botId ?? undefined, 100);
            if (cancelled) return;
            if (botId === activeBotId && (!botId || isCurrentBotResponse(botId, activeBotRef.current))) {
              setSessions(data.sessions);
            }
            const currentIds = new Set(data.sessions.map((item) => item.id));
            remaining.push(...pendingTargets.filter((target) => target.botId === botId && currentIds.has(target.sessionId)));
          }
          if (remaining.length < pendingTargets.length) {
            setPendingSessionDeletions(remaining);
            if (remaining.length === 0) {
              setNotice(en ? "Chat deletion completed." : "对话删除已完成。");
            }
            return;
          }
          if (attempts >= 12) setPendingSessionDeletions([]);
        } catch {
          if (attempts >= 12) setPendingSessionDeletions([]);
        }
      })();
    }, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [pendingDeletionKey, activeBotId, en]);

  return (
    <div className={styles.shell} data-theme={effectiveTheme} data-locale={en ? "en" : "zh"} data-product-mode={productMode} data-workspace-level={workspaceLevel} data-panel-open={panelOpen ? "true" : "false"} data-copy-shell="true">
      <div className={styles.appWindow}>
      <nav className={styles.productCrumb} aria-label={en ? "Product hierarchy" : "产品层级"}>
        <a href="/landing" className={styles.productCrumbLink} aria-label={en ? "Back to home" : "回主页"}>
          {en ? "Home" : "主页"}
        </a>
        <span className={styles.productCrumbSep} aria-hidden="true">›</span>
        {productMode === "build" ? (
          <>
            <button type="button" className={styles.productCrumbLink} onClick={() => setProductModeSafe("use")}>
              {en ? "Use" : "使用"}
            </button>
            <span className={styles.productCrumbSep} aria-hidden="true">›</span>
            <span className={styles.productCrumbCurrent} aria-current="page">{en ? "Adjust capabilities" : "调整能力"}</span>
          </>
        ) : (
          <span className={styles.productCrumbCurrent} aria-current="page">{en ? "Use" : "使用"}</span>
        )}
      </nav>




      {error ? (
        <div className={styles.errorBanner} role="alert">
          <span>{error}</span>
          <button type="button" onClick={() => setError(null)} aria-label={en ? "Dismiss" : "关闭"}>
            ×
          </button>
        </div>
      ) : null}
      {notice ? (
        <div className={styles.noticeBanner} role="status">
          <span>{notice}</span>
          <button type="button" onClick={() => setNotice(null)} aria-label={en ? "Dismiss" : "关闭"}>
            ×
          </button>
        </div>
      ) : null}

      <main className={`${styles.botLayout} ${panelOpen ? "" : styles.botLayoutPanelClosed} ${workspaceLevel === "takeover" ? styles.botLayoutTakeover : ""} ${workspaceLevel === "status" ? styles.botLayoutStatus : ""}`} data-copy-shell="true">
        <SessionSidebar
          bots={bots}
          botsLoading={botsLoading}
          sessionsLoading={sessionsLoading}
          activeBotId={activeBotId}
          activeState={botState}
          theme={theme}
          onCycleTheme={cycleTheme}
          onSelectBot={(id) => void selectBot(id)}
          onCreateBot={productMode === "build" ? openCreateBot : openCreateBotFromUse}
          onEditBot={openEditBot}
          productMode={productMode}
          onProductMode={setProductModeSafe}
          sessions={sessions}
          activeId={sessionId}
          onSelect={(id) => void selectSession(id)}
          onDeleteSession={(id) => void removeChat(id)}
          onPatchThread={(id, patch) => void updateThreadMeta(id, patch)}
          onRenameThread={(id, title) => renameThread(id, title)}
          onMoveFolder={(id, folder) => moveThreadFolder(id, folder)}
          onClearHistory={() => void clearHistory()}
          onDeleteBot={() => void removeBot()}
          watching={mergeWatchingList({ en, local: focusPoints, monitors, memories: memories?.memories ?? [] })}
          unread={watchingAlerts.filter((item) => item.status === "unread")}
          onOpenFocus={(point) => openFocusPoint(point)}
          onOpenAlert={(alert) => void openWatchingAlert(alert)}
          onCreateFocus={() => startTrackFocus()}
          en={en}
        />
        <section className={styles.stageColumn} aria-label={en ? "Conversation" : "对话"}>
          <header className={styles.botStageHeader}>
            <button
              type="button"
              className={styles.botStageIdentityButton}
              onClick={() => setBotSwitcherOpen(true)}
              aria-label={en ? "Switch assistant" : "切换助手"}
              title={en ? "Switch assistant (⌘K)" : "切换助手（⌘K）"}
            >
              <span className={styles.presenceAvatarWrap} data-state={botState} title={botCopy(botState, en ? "en" : "zh").detail}>
                <BotAvatar name={activeBot?.name || (en ? "Research Bot" : "研究助手")} state={avatarState} size="large" title={statusText(botState, en)} />
                <i className={styles.presenceAvatarDot} aria-hidden="true" />
                <em className={styles.presenceHoverHint}>{botCopy(botState, en ? "en" : "zh").detail}</em>
              </span>
              <span className={styles.botStageIdentity}>
                <h2>{activeBot?.name || (en ? "Research Bot" : "研究助手")}</h2>
                <p className={styles.presenceLine} data-state={botState} title={botCopy(botState, en ? "en" : "zh").detail}>
                  <i className={styles.presenceDot} aria-hidden="true" />
                  <span>{botCopy(botState, en ? "en" : "zh").title}</span>
                </p>
              </span>
              <span className={styles.botStageSwitcher} aria-hidden="true">⌄</span>
            </button>
            <div className={styles.botStageActions}>
              <a
                className={`${styles.homeBarLink} ${styles.desktopStageAction}`}
                href="/landing"
                aria-label={en ? "Back to home" : "回主页"}
                title={en ? "Back to home" : "回主页"}
              >{en ? "Home" : "主页"}</a>
              {productMode === "use" ? (
                WATCH_ENABLED ? (
                  <button
                    type="button"
                    className={`${styles.useWatchChip} ${styles.desktopStageAction}`}
                    onClick={() => {
                      const unreadFirst = watchingAlerts.find((item) => item.status === "unread");
                      if (unreadFirst) {
                        void openWatchingAlert(unreadFirst);
                        return;
                      }
                      startTrackFocus();
                    }}
                    title={en ? "Watching & unread alerts" : "我在盯的事与未读提醒"}
                  >
                    {unreadCount > 0 ? (
                      <em data-hot="true">{en ? "Unread" : "未读"} {unreadCount}</em>
                    ) : (
                      <span>{en ? "Watch" : "盯梢"}</span>
                    )}
                  </button>
                ) : null
              ) : (
                <button
                  type="button"
                  className={`${styles.presenceBackChat} ${styles.desktopStageAction}`}
                  onClick={() => setProductModeSafe("use")}
                >{en ? "Back to Use" : "回到使用"}</button>
              )}
              <button type="button" className={`${styles.stageIconButton} ${styles.mobileRosterButton}`} onClick={() => { setStageMoreOpen(false); setMobileRosterOpen(true); }} aria-label={en ? "Open assistant roster" : "打开助手列表"}>☰</button>
              <button type="button" className={`${styles.stageIconButton} ${styles.desktopStageAction}`} onClick={() => void newSideThread()} disabled={busy} aria-label={en ? "Branch chat" : "分支对话"} title={en ? "Branch chat — a side thread that does not touch the main conversation" : "分支对话 — 不影响当前主线的独立岔开"}>⑂</button>
              {productMode === "build" ? (
              <span className={`${styles.railCueWrap} ${styles.desktopStageAction}`}>
                <button type="button" className={styles.workspaceActivityChip} data-active={panelOpen && workspaceLevel !== "status" ? "true" : "false"} onClick={() => { if (!panelOpen || workspaceLevel === "status") openBotScreen(); else if (workspaceLevel === "preview") expandWorkspaceTakeover(); else collapseWorkspaceToPreview(); }} aria-label={en ? "Assistant workspace" : "助手工作区"} title={en ? "Assistant workspace is active" : "助手工作区在活动"}>
                  <i aria-hidden="true" />
                  <span>{en ? "Workspace" : "工作区"}</span>
                </button>
                <button type="button" className={styles.stageIconButton} onClick={openBotScreen} aria-label={en ? "Open monitor & delivery" : "打开监控与投递"} title={en ? "Open monitor & delivery" : "打开监控与投递"}>⤢</button>
                {railCueVisible && !panelOpen ? (
                  <span className={styles.railCue} role="status">
                    {en ? "Tap ⤢ for monitors & delivery" : "点 ⤢ 查看监控与投递"}
                    <button
                      type="button"
                      className={styles.railCueDismiss}
                      aria-label={en ? "Dismiss" : "知道了"}
                      onClick={() => {
                        setRailCueVisible(false);
                        try { sessionStorage.setItem("financial-bot-rail-cue-seen", "1"); } catch { /* private mode */ }
                      }}
                    >×</button>
                  </span>
                ) : null}
              </span>
              ) : (
                <button
                  type="button"
                  className={`${styles.adjustCapabilities} ${styles.desktopStageAction}`}
                  onClick={() => setProductModeSafe("build")}
                  aria-label={en ? "Adjust capabilities" : "调整能力"}
                  title={en ? "Monitors, delivery, assistant settings" : "监控、投递与助手设置"}
                >{en ? "Adjust" : "调整能力"}</button>
              )}
              <div className={styles.stageMoreWrap}>
                <button
                  type="button"
                  className={`${styles.stageIconButton} ${styles.stageMoreButton}`}
                  aria-expanded={stageMoreOpen}
                  aria-haspopup="menu"
                  aria-label={en ? "More actions" : "更多操作"}
                  onClick={() => setStageMoreOpen((open) => !open)}
                >⋯</button>
                {stageMoreOpen ? (
                  <>
                    <button type="button" className={styles.stageMoreBackdrop} aria-label={en ? "Close menu" : "关闭菜单"} onClick={() => setStageMoreOpen(false)} />
                    <div className={styles.stageMoreMenu} role="menu">
                      <a role="menuitem" href="/landing" onClick={() => setStageMoreOpen(false)}>{en ? "Home" : "主页"}</a>
                      {productMode === "use" ? (
                        <button type="button" role="menuitem" onClick={() => { setStageMoreOpen(false); setProductModeSafe("build"); }}>{en ? "Adjust capabilities" : "调整能力"}</button>
                      ) : (
                        <button type="button" role="menuitem" onClick={() => { setStageMoreOpen(false); setProductModeSafe("use"); }}>{en ? "Back to Use" : "回到使用"}</button>
                      )}
                      <button type="button" role="menuitem" disabled={busy} onClick={() => { setStageMoreOpen(false); void newSideThread(); }}>{en ? "Branch chat" : "分支对话"}</button>
                      {productMode === "build" ? (
                        <>
                          <button type="button" role="menuitem" onClick={() => { setStageMoreOpen(false); if (!panelOpen || workspaceLevel === "status") openBotScreen(); else if (workspaceLevel === "preview") expandWorkspaceTakeover(); else collapseWorkspaceToPreview(); }}>{en ? "Workspace" : "工作区"}</button>
                          <button type="button" role="menuitem" onClick={() => { setStageMoreOpen(false); openBotScreen(); }}>{en ? "Monitor & delivery" : "监控与投递"}</button>
                        </>
                      ) : null}
                      {WATCH_ENABLED ? (
                        <button type="button" role="menuitem" onClick={() => {
                          setStageMoreOpen(false);
                          const unreadFirst = watchingAlerts.find((item) => item.status === "unread");
                          if (unreadFirst) { void openWatchingAlert(unreadFirst); return; }
                          startTrackFocus();
                        }}>{en ? "Watching" : "盯梢"}{unreadCount > 0 ? ` (${unreadCount})` : ""}</button>
                      ) : null}
                    </div>
                  </>
                ) : null}
              </div>
            </div>
          </header>
          <div
            ref={chatRef}
            className={styles.chat}
            onWheel={(event) => {
              if (event.deltaY < 0) stickToBottom.current = false;
            }}
            onScroll={(event) => {
              if (autoScrollRef.current) return;
              const element = event.currentTarget;
              stickToBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 120;
            }}
          >
            {productMode === "use" ? (
              <InlineAssistTimeline
                en={en}
                botName={activeBot?.name || (en ? "Research Bot" : "研究助手")}
                watching={mergeWatchingList({ en, local: focusPoints, monitors, memories: memories?.memories ?? [] })}
                unread={watchingAlerts.filter((item) => item.status === "unread")}
                monitors={monitors}
                onOpenFocus={(point) => openFocusPoint(point)}
                onOpenAlert={(alert) => void openWatchingAlert(alert)}
                onCreateFocus={() => startTrackFocus()}
                onOpenWorkspace={() => openBotScreen()}
              />
            ) : null}
            {showLocalWelcome ? (
              <WelcomeThread
                key={`${activeBotId ?? "default"}:${sessionId || "new"}`}
                en={en}
                watchingEnabled={WATCH_ENABLED}
                bot={activeBot}
                plan={onboardPlan}
                persona={persona}
                onPersona={choosePersona}
                onRecord={(record) => {
                  setOnboardingRecord(record);
                  try {
                    window.localStorage.setItem(
                      `financial-bot-onboarding-v1:${sessionId}`,
                      JSON.stringify(record),
                    );
                  } catch {
                    // Storage is a convenience; the live view still works.
                  }
                }}
                onStart={(prompt, meta) => {
                  if (meta?.kind === "track") {
                    startTrackFocus(prompt);
                    return;
                  }
                  void submit(prompt);
                }}
                onCustom={focusComposer}
                onSkip={() => setWelcomeDismissed(true)}
              />
            ) : null}
            {orphanMessages.map((message) => (
              <TranscriptMessage key={message.id} message={message} en={en} botName={activeBot?.name} />
            ))}
            {tasks.length > 0 ? <OnboardingReplay record={onboardingRecord} en={en} /> : null}
            {tasks.length === 0 && !activeTask && !pendingApproval ? (
              welcomeDismissed ? (
                <BotDesk
                  en={en}
                  botName={activeBot?.name || (en ? "Research Bot" : "研究助手")}
                  examples={examplePrompts}
                  successPaths={successPaths}
                  monitors={monitors}
                  notifications={notifications}
                  artifacts={artifacts}
                  onStart={(prompt, meta) => {
                    if (meta?.kind === "track") {
                      startTrackFocus(prompt);
                      return;
                    }
                    void submit(prompt);
                  }}
                  onCreateMonitor={() => { setProductModeSafe("build"); setMonitorDialogOpen(true); }}
                  onAnalyze={(notification) => void analyzeAlert(notification)}
                  onOpenArtifact={(artifact) => void openArtifact(artifact)}
                  showIntro
                  showGrid={productMode === "build"}
                  productMode={productMode}
                />
              ) : null
            ) : (
              <>
            {tasks.map((task) => {
              const linked = messages.filter((message) => message.task_id === task.id);
              const userMessages = linked.filter((message) => message.role === "user");
              const assistantMessages = linked.filter((message) => message.role === "assistant" && message.kind === "answer");
              if (!linked.length) {
                return (
                  <TaskView
                    key={task.id}
                    task={task}
                    run={run.taskId === task.id ? run : null}
                    stopped={stoppedTaskId === task.id}
                    en={en}
                    disposition={dispositionsByTask[task.id] || null}
                    candidates={pendingCandidates.filter((candidate) => candidate.task_id === task.id)}
                    drafts={candidateDrafts}
                    onDraft={(id, value) => setCandidateDrafts((previous) => ({ ...previous, [id]: value }))}
                    onConfirm={onConfirmCandidate}
                    onDismiss={onDismissCandidate}
                    onFollowUp={(prompt) => void submit(prompt)}
                    onAnswerShared={answerSharedResearch}
                    onRetryAsQuick={(prompt) => { setResearchMode("quick"); void submit(prompt); }}
                    onOpenTrace={() => setTraceTaskId(task.id)}
                    onResume={() => void resumeExecution(task.id)}
                    botName={activeBot?.name}
                  />
                );
              }
              return (
                <div key={task.id} className={styles.threadTurn}>
                  {userMessages.map((message) => <TranscriptMessage key={message.id} message={message} en={en} botName={activeBot?.name} />)}
                  {assistantMessages.map((message) => <TranscriptMessage key={message.id} message={message} en={en} botName={activeBot?.name} />)}
                  <TaskView
                    task={task}
                    run={run.taskId === task.id ? run : null}
                    stopped={stoppedTaskId === task.id}
                    en={en}
                    disposition={dispositionsByTask[task.id] || null}
                    candidates={pendingCandidates.filter((candidate) => candidate.task_id === task.id)}
                    drafts={candidateDrafts}
                    onDraft={(id, value) => setCandidateDrafts((previous) => ({ ...previous, [id]: value }))}
                    onConfirm={onConfirmCandidate}
                    onDismiss={onDismissCandidate}
                    onFollowUp={(prompt) => void submit(prompt)}
                    onAnswerShared={answerSharedResearch}
                    onRetryAsQuick={(prompt) => { setResearchMode("quick"); void submit(prompt); }}
                    onOpenTrace={() => setTraceTaskId(task.id)}
                    onResume={() => void resumeExecution(task.id)}
                    showUser={false}
                    showAnswer={assistantMessages.length === 0}
                    showHeader={false}
                    showTime={false}
                    botName={activeBot?.name}
                  />
                </div>
              );
            })}
                <div className={styles.attentionDock}>
                  {threadState?.workflow && !tasks.some((task) => task.result?.workflow) ? (
                    <WorkflowPanel workflow={threadState.workflow} en={en} onFollowUp={(prompt) => void submit(prompt)} />
                  ) : null}
                  {productMode === "build" && notifications.length ? (
                    <RoutineRuns
                      notifications={notifications.slice(0, 3)}
                      en={en}
                      onAnalyze={(notification) => void analyzeAlert(notification)}
                    />
                  ) : null}
                  {emptyPortfolioPrompt ? (
                    <EmptyPortfolioCard
                      en={en}
                      onOpenPortfolio={() => window.open("/work?context=portfolio", "_blank", "noopener,noreferrer")}
                      onRetry={() => {
                        void listPortfolioPositions().then((positions) => {
                          setPortfolioPositions(positions);
                          if (positions.length) {
                            setEmptyPortfolioPrompt(null);
                            setNotice(en ? "Holdings found. Retrying the portfolio review." : "已找到持仓，正在重新发起持仓体检。");
                          } else {
                            setNotice(en ? "Still no holdings. Add a position first." : "仍然是空持仓，请先添加持仓。");
                          }
                        }).catch((caught) => setError(describeBotError(caught, en)));
                      }}
                      onAnalyzeStock={() => {
                        setEmptyPortfolioPrompt(null);
                        setInput(en ? "Analyze Kweichow Moutai sh600519" : "分析贵州茅台 sh600519");
                        window.requestAnimationFrame(() => composerRef.current?.focus());
                      }}
                    />
                  ) : null}
                  {pendingApproval ? (
                    <InlineApprovalCard
                      en={en}
                      query={pendingApproval.query}
                      botName={bots.find((bot) => bot.id === pendingApproval.botId)?.name || (en ? "Research Bot" : "研究助手")}
                      preflight={pendingApproval.preflight}
                      onCancel={() => setPendingApproval(null)}
                      onConfirm={() => { const pending = pendingApproval; setPendingApproval(null); void startTask(pending.query, undefined, pending.botId, true, "confirm_model_budget", pending.modelProvider); }}
                    />
                  ) : null}
                </div>
                <div ref={endRef} />
              </>
            )}
          </div>

          <div className={styles.composerWrap}>
            {queuedPrompts.length ? (
              <div className={styles.composerQueue} aria-live="polite">
                <span>{en ? "Queued" : "排队中"} {queuedPrompts.length}</span>
                <small>{queuedPrompts[0]}</small>
                <button type="button" onClick={() => setQueuedPrompts([])}>{en ? "Clear" : "清空"}</button>
              </div>
            ) : null}
            <div className={styles.composer}>
              <button
                type="button"
                className={styles.composerPlus}
                onClick={newSession}
                disabled={busy}
                aria-label={en ? "New chat" : "新对话"}
                title={en ? "New chat" : "新对话"}
              >＋</button>
              <textarea
                ref={composerRef}
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.nativeEvent.isComposing) return;
                  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                    event.preventDefault();
                    void submit(input);
                    return;
                  }
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void submit(input);
                  }
                }}
                rows={1}
                maxLength={4000}
                placeholder={
                  showLocalWelcome
                    ? (en ? "Answer, or just type something else" : "输入你的回答，或聊点别的")
                    : en
                      ? `Message ${activeBot?.name || "Research Bot"}…`
                      : `给${activeBot?.name || "研究助手"}发消息…`
                }
                aria-label={en ? "Message assistant" : "给助手发消息"}
              />
              {busy && run.taskId ? (
                <button type="button" className={styles.composerStop} onClick={() => void onCancel()} aria-label={en ? "Stop task" : "停止任务"}>
                  <span aria-hidden="true" />
                </button>
              ) : (
                <button
                  type="button"
                  className={styles.composerSend}
                  disabled={!input.trim() || !sessionId}
                  onClick={() => void submit(input)}
                  aria-label={en ? "Send" : "发送"}
                >
                  {sending ? "…" : "↑"}
                </button>
              )}
            </div>
            {runStuck && run.taskId && !isTerminal(run.status) ? (
              <div className={styles.attentionCard} role="status">
                <strong>{en ? "Still working longer than usual" : "研究时间比平时更长"}</strong>
                <p>{en ? "You can keep waiting, stop this run, or switch to Quick research." : "可以继续等待、停止本次，或改用快速研究。"}</p>
                <div className={styles.attentionActions}>
                  <button type="button" onClick={() => void onCancel()}>{en ? "Stop" : "停止"}</button>
                  <button type="button" onClick={() => { setResearchMode("quick"); setNotice(en ? "Switched to Quick research for the next send." : "下一次发送将使用快速研究。"); }}>{en ? "Use Quick next" : "改用快速研究"}</button>
                </div>
              </div>
            ) : null}
            <div className={styles.composerMeta}>
              <div className={styles.modeToggle} role="group" aria-label={en ? "Research mode" : "研究模式"}>
                <button type="button" className={researchMode === "quick" ? styles.modeActive : ""} onClick={() => setResearchMode("quick")}>{en ? "Quick" : "快速"}</button>
                <button
                  type="button"
                  className={researchMode === "deep" ? styles.modeActive : ""}
                  onClick={() => {
                    if (!deepAvailable) {
                      setResearchMode("quick");
                      setNotice(en ? "Deep research is unavailable. Switched to Quick." : "深度研究暂不可用，已改用快速研究。");
                      return;
                    }
                    setResearchMode("deep");
                  }}
                  disabled={!deepAvailable}
                  title={!deepAvailable ? (deepUnavailableMessage || (en ? "Deep unavailable" : "深度暂不可用")) : undefined}
                >{en ? "Deep" : "深度"}</button>
                <button
                  type="button"
                  className={researchMode === "openmuse_shared" ? styles.modeActive : ""}
                  onClick={() => setResearchMode("openmuse_shared")}
                  disabled={!status?.openmuse?.shared_research_enabled}
                  title={!status?.openmuse?.shared_research_enabled
                    ? (en ? "Enable OpenMuse shared research in server configuration and connect OpenMuse." : "需先在服务端启用共享研究并连接 OpenMuse。")
                    : (en
                      ? "Use OpenMuse for general research. Recognized financial questions use the Financial Bot source bridge (iFinD first, then configured fallbacks); recognition and data coverage may be incomplete."
                      : "使用 OpenMuse 共享工作区开展自由研究。已识别的金融问题会调用 Financial Bot 数据桥（同花顺优先，失败时尝试已配置的备用源）；问题识别与数据覆盖可能不完整。")}
                >{en ? "Free research" : "自由研究"}</button>
              </div>
              {researchMode === "openmuse_shared" ? (
                <p className={styles.gateReason}>{en
                  ? "Recognized financial questions go through the Financial Bot source bridge. Recognition and available source data may be incomplete."
                  : "已识别的金融问题会经 Financial Bot 数据桥获取来源数据。问题识别与可用数据可能不完整。"}</p>
              ) : null}
              {researchMode === "openmuse_shared" ? (
                <p className={styles.composerDataHint} role="note">
                  {en
                    ? "This run uses the model configured on the OpenMuse server. The selector applies only to Quick and Deep research."
                    : "本次使用 OpenMuse 服务端配置的模型；此处模型选项仅对快速和深度研究生效。"}
                </p>
              ) : (
                <select aria-label={en ? "Model" : "模型"} value={modelProvider} onChange={(event) => { setModelProviderTouched(true); setModelProvider(event.target.value as "deepseek" | "chatgpt"); }}>
                  <option value="deepseek">DeepSeek Flash</option>
                  {chatgptStatus.available ? <option value="chatgpt">ChatGPT {chatgptStatus.connected ? (en ? "subscription" : "订阅") : (en ? "(not connected)" : "（未连接）")}</option> : null}
                </select>
              )}
              <span className={styles.charCount} data-near-limit={input.length >= 3800 ? "true" : "false"}>{input.length}/4000</span>
              {input.length >= 3800 ? (
                <span className={styles.charWarning} role="status">
                  {en ? "Getting long — consider splitting this into two questions." : "有点长了，建议拆成两个问题。"}
                </span>
              ) : null}
            </div>
            {!deepAvailable ? (
              <p className={styles.composerDataHint} role="note">
                {en
                  ? (deepUnavailableMessage || "Deep research is unavailable right now.")
                  : (deepUnavailableMessage || "深度研究暂不可用。")}
                {" "}
                <button type="button" className={styles.textLinkButton} onClick={() => setResearchMode("quick")}>{en ? "Use Quick research" : "改用快速研究"}</button>
              </p>
            ) : null}
            <p className={styles.composerDataHint} role="note">
              {en
                ? "Market quotes here may be delayed or historical — not realtime."
                : "此处行情与报价可能为延迟或历史数据，非实时。"}
            </p>
            {researchMode !== "openmuse_shared" && modelProvider === "chatgpt" && !chatgptStatus.connected ? (
              <p className={styles.composerDataHint} role="status">
                {en
                  ? "ChatGPT is not connected. You can still select it, but this run will fall back to DeepSeek before start."
                  : "ChatGPT 未连接。仍可选择，但开始前会改用 DeepSeek。"}
              </p>
            ) : null}
            {researchMode !== "openmuse_shared" && modelProvider === "chatgpt" && chatgptStatus.connected ? <p className={styles.composerDataHint} role="note">{en ? "If ChatGPT is unavailable before work starts, DeepSeek will be used and API charges may apply. Started work is not automatically replayed." : "ChatGPT 在任务开始前不可用时，将切换到 DeepSeek，可能产生 API 费用；已开始的工作不会自动重放。"}</p> : null}
            <p className={styles.disclaimer}>
              {en
                ? "Research output is not investment advice. Sources, timestamps and gaps are shown; failed synthesis is never presented as a conclusion."
                : "研究输出不构成投资建议。界面展示来源、时间与缺口；未完成的综合不会包装成结论。"}
            </p>
          </div>
        </section>

        {mobileScreenOpen ? <div className={styles.mobileScreenBackdrop} role="presentation" onClick={closeBotScreen} /> : null}
        <aside className={`${styles.contextRail} ${workspaceExpanded ? styles.contextRailExpanded : ""} ${panelOpen ? "" : styles.contextRailClosed} ${mobileScreenOpen ? styles.contextRailMobileOpen : ""} ${workspaceLevel === "status" ? styles.contextRailStatus : ""}`} aria-label={en ? "Assistant space" : "助手空间"} data-workspace-level={workspaceLevel}>
          <div className={styles.botPanelHeader}>
            <button type="button" className={styles.botPanelIcon} onClick={() => {
              if (workspaceLevel === "takeover") collapseWorkspaceToPreview();
              else if (workspaceLevel === "preview") setWorkspaceLevel("status");
              else setWorkspaceLevel("preview");
            }} aria-label={en ? "Workspace level" : "工作区层级"}>
              <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M12.5 4.5 7 10l5.5 5.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
            </button>
            {productMode === "build" ? (
              <div className={styles.workspaceLevelSwitch} role="group" aria-label={en ? "Workspace view" : "工作区视图"}>
                <button type="button" data-active={workspaceLevel === "status" ? "true" : "false"} onClick={() => setWorkspaceLevel("status")}>{en ? "Status" : "状态"}</button>
                <button type="button" data-active={workspaceLevel === "preview" ? "true" : "false"} onClick={() => { setWorkspaceLevel("preview"); setWorkspaceExpanded(false); setPanelOpen(true); }}>{en ? "Preview" : "预览"}</button>
                <button type="button" data-active={workspaceLevel === "takeover" ? "true" : "false"} onClick={() => expandWorkspaceTakeover()}>{en ? "Full" : "全宽"}</button>
              </div>
            ) : (
              <strong className={styles.workspaceUseTitle}>{en ? "Screen & routines" : "屏幕与例行"}</strong>
            )}
            <div className={styles.botPanelActions}>
              <button type="button" className={styles.botPanelIcon} onClick={openEditBot} aria-label={en ? "Assistant settings" : "助手设置"}>
                <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="2.4" fill="none" stroke="currentColor" strokeWidth="1.5" /><path d="M10 3v1.8M10 15.2V17M3 10h1.8M15.2 10H17M5.1 5.1l1.3 1.3M13.6 13.6l1.3 1.3M14.9 5.1l-1.3 1.3M6.4 13.6l-1.3 1.3" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
              </button>
              <button type="button" className={styles.botPanelIcon} onClick={() => { setPanelOpen(false); setWorkspaceLevel("status"); setMobileScreenOpen(false); }} aria-label={en ? "Close" : "关闭"}>
                <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 5l10 10M15 5L5 15" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
              </button>
            </div>
          </div>
          {(() => {
            const screenTask = tasks.find((task) => task.id === run.taskId) || tasks[tasks.length - 1] || null;
            const screenStep = activeSteps(run).at(-1)?.summary;
            const latestArtifact = artifacts[0];
            const title = screenTask?.query || latestArtifact?.title || (en ? "No screen activity" : "暂无屏幕活动");
            const detail = screenStep
              || (screenTask?.result?.summary ? screenTask.result.summary.slice(0, 180) : "")
              || latestArtifact?.summary?.slice(0, 180)
              || (en ? "When this assistant starts working, its screen appears here." : "助手开始工作后，它的屏幕会出现在这里。");
            const hasScreenActivity = Boolean(screenTask || latestArtifact || (run.taskId && !isTerminal(run.status)));
            return (
              <>
                {hasScreenActivity ? (
                  <>
                    <div className={styles.botScreenPreview} data-live={run.taskId && !isTerminal(run.status) ? "true" : "false"}>
                      <div className={styles.botScreenDesktop}>
                        <div className={styles.botScreenMenu}>
                          <span>{activeBot?.name || "Research Bot"}</span>
                          <span>{run.taskId && !isTerminal(run.status) ? (en ? "live" : "运行中") : (en ? "preview" : "预览")}</span>
                        </div>
                        <div className={styles.botScreenWindow}>
                          <div className={styles.botScreenWindowBar} aria-hidden="true"><i /><i /><i /></div>
                          <div className={styles.botScreenBody}>
                            <span className={styles.botScreenKicker}>{run.taskId && !isTerminal(run.status) ? (en ? "LIVE" : "运行中") : (en ? "LAST OUTPUT" : "最近产出")}</span>
                            <strong>{title}</strong>
                            <p>{detail}</p>
                            <div className={styles.botScreenLines} aria-hidden="true"><i /><i /><i /></div>
                          </div>
                        </div>
                        <div className={styles.botScreenDock} aria-hidden="true"><i /><i /><i /><i /></div>
                      </div>
                    </div>
                    <p className={styles.botScreenCaption}>{activeBot?.name || "Research Bot"}{en ? "'s screen" : " 的屏幕"} · {botCopy(botState, en ? "en" : "zh").title}</p>
                  </>
                ) : (
                  <>
                    <div className={styles.botScreenPreview} data-live="false" data-empty="true">
                      <div className={styles.botScreenDesktop}>
                        <div className={styles.botScreenMenu}>
                          <span>{activeBot?.name || "Research Bot"}</span>
                          <span>{en ? "idle" : "待命"}</span>
                        </div>
                        <div className={styles.botScreenWindow}>
                          <div className={styles.botScreenWindowBar} aria-hidden="true"><i /><i /><i /></div>
                          <div className={styles.botScreenBody}>
                            <span className={styles.botScreenKicker}>{en ? "READY" : "待命"}</span>
                            <strong>{en ? "Assistant screen" : "助手屏幕"}</strong>
                            <p>{en ? "When research starts, live progress shows up here." : "开始研究后，进度会出现在这里。"}</p>
                            <div className={styles.botScreenLines} aria-hidden="true"><i /><i /><i /></div>
                          </div>
                        </div>
                        <div className={styles.botScreenDock} aria-hidden="true"><i /><i /><i /><i /></div>
                      </div>
                    </div>
                    <p className={styles.botScreenCaption}>{activeBot?.name || "Research Bot"}{en ? "'s screen" : " 的屏幕"} · {botCopy(botState, en ? "en" : "zh").title}</p>
                  </>
                )}
                {(run.taskId && !isTerminal(run.status)) || workspaceLevel === "takeover" ? (
                  <ReasoningRail
                    task={screenTask}
                    run={screenTask && run.taskId === screenTask.id ? run : null}
                    en={en}
                    onOpenTrace={() => { if (screenTask) setTraceTaskId(screenTask.id); }}
                    onResume={() => { if (screenTask) void resumeExecution(screenTask.id); }}
                  />
                ) : screenTask ? (
                  <details className={styles.workspaceActivityDetails}>
                    <summary>{en ? "Recent steps (one line)" : "最近一步（展开看）"} · {botCopy(botState, en ? "en" : "zh").title}</summary>
                    <p className={styles.workspaceActivityOneLiner}>
                      {activeSteps(run).at(-1)?.summary
                        || screenTask.result?.summary?.slice(0, 120)
                        || (en ? "Idle — ask anything to put the screen to work." : "闲着——丢个问题就会在屏幕上动起来。")}
                    </p>
                  </details>
                ) : null}
                <section className={styles.routinesPanel} data-routines="true" aria-label={en ? "Routines" : "例行"}>
                  <header className={styles.routinesHead}>
                    <strong>{en ? "Routines" : "例行"}</strong>
                    <button type="button" onClick={() => { setProductModeSafe("build"); setMonitorDialogOpen(true); }}>{en ? "Add" : "添加"}</button>
                  </header>
                  <ul className={styles.routinesList}>
                    {monitors.slice(0, 6).map((monitor) => (
                      <li key={monitor.id} data-active={monitor.status === "active" ? "true" : "false"}>
                        <span className={styles.routineMark} aria-hidden="true">{monitor.status === "active" ? "✓" : "◷"}</span>
                        <div>
                          <strong>{monitor.name || monitor.symbol || (en ? "Routine" : "例行任务")}</strong>
                          <small>
                            {monitor.status === "active" ? (en ? "Active" : "运行中") : (en ? "Paused" : "已暂停")}
                            {monitor.symbol ? ` · ${monitor.symbol}` : ""}
                          </small>
                        </div>
                      </li>
                    ))}
                    {!monitors.length ? (
                      <li className={styles.routinesEmpty}>
                        <span className={styles.routineMark} aria-hidden="true">◷</span>
                        <div>
                          <strong>{en ? "No routines yet" : "还没有例行"}</strong>
                          <small>{en ? "Polling checks live here: price moves, announcements, earnings dates." : "定时巡检放这里：价格异动、巨潮公告、财报日。"}</small>
                        </div>
                      </li>
                    ) : null}
                  </ul>
                </section>
              </>
            );
          })()}
          {run.connection === "offline" && run.taskId ? (
            <button type="button" className={styles.reconnect} onClick={() => void followTask(run.taskId!)}>
              {en ? "Reconnect and reconcile" : "重新连接并按服务端状态恢复"}
            </button>
          ) : null}
          {productMode === "use" ? (
            <button
              type="button"
              className={styles.opsMoreToggle}
              data-ops-more="true"
              data-open={opsMoreOpen ? "true" : "false"}
              aria-expanded={opsMoreOpen}
              onClick={() => setOpsMoreOpen((value) => !value)}
            >
              {opsMoreOpen
                ? (en ? "Hide monitors & memory" : "收起监控与记忆")
                : (en ? "More · monitors, memory, status, outputs" : "更多 · 监控、记忆、状态、产出")}
            </button>
          ) : null}
          <div
            className={`${styles.sideTabs} ${styles.sideTabsQuiet}`}
            role="tablist"
            aria-label={en ? "Workspace panels" : "工作台面板"}
            hidden={productMode === "use" && !opsMoreOpen}
            data-ops-panels={productMode === "use" ? (opsMoreOpen ? "open" : "closed") : "build"}
          >
            <button type="button" role="tab" aria-selected={sideTab === "monitor"} className={sideTab === "monitor" ? styles.sideTabActive : undefined} onClick={() => setSideTab("monitor")}>
              {en ? "Monitor" : "监控"}
            </button>
            <button type="button" role="tab" aria-selected={sideTab === "memory"} className={sideTab === "memory" ? styles.sideTabActive : undefined} onClick={() => setSideTab("memory")}>
              {en ? "Memory" : "记忆"}
            </button>
            <button type="button" role="tab" aria-selected={sideTab === "status"} className={sideTab === "status" ? styles.sideTabActive : undefined} onClick={() => setSideTab("status")}>
              {en ? "Status" : "状态"}
            </button>
            <button type="button" role="tab" aria-selected={sideTab === "artifacts"} className={sideTab === "artifacts" ? styles.sideTabActive : undefined} onClick={() => setSideTab("artifacts")}>
              {en ? "Outputs" : "产出"}
            </button>
          </div>
          <div className={styles.workspaceBody} hidden={productMode === "use" && !opsMoreOpen} data-ops-panels={productMode === "use" ? (opsMoreOpen ? "open" : "closed") : "build"}>
          <div className={styles.workspacePanels}>
          {sideTab === "monitor" ? (
            <ProactivePanel
              monitors={monitors}
              notifications={notifications}
              en={en}
              onCreate={() => setMonitorDialogOpen(true)}
              onToggle={(monitor) => void toggleMonitor(monitor)}
              onDelete={(monitor) => void removeMonitor(monitor)}
              onMute={(monitor) => void toggleMonitorMute(monitor)}
              onRead={(notification) => void markRead(notification)}
              onAnalyze={(notification) => void analyzeAlert(notification)}
              deliveries={deliveries}
              notificationsError={notificationsError}
              deliveriesError={deliveriesError}
              botId={activeBotId}
              routinesEnabled={activeBot?.prompt_config?.routines_enabled !== false}
              onRetry={(delivery) => void retryFailedDelivery(delivery)}
            />
          ) : null}
          {sideTab === "memory" ? (
            <MemoryPanel
              en={en}
              memories={memories?.memories ?? []}
              candidates={pendingCandidates}
              drafts={candidateDrafts}
              editing={editingMemory}
              onDraft={(id, value) => setCandidateDrafts((previous) => ({ ...previous, [id]: value }))}
              onConfirm={onConfirmCandidate}
              onDismiss={onDismissCandidate}
              onStartEdit={(memory) => setEditingMemory({ id: memory.id, text: memory.text })}
              onEditChange={(text) => setEditingMemory((previous) => (previous ? { ...previous, text } : previous))}
              onSave={onSaveMemory}
              onCancelEdit={() => setEditingMemory(null)}
              deleteTarget={deleteTarget}
              onAskDelete={setDeleteTarget}
              onCancelDelete={() => setDeleteTarget(null)}
              onDelete={onDeleteMemory}
            />
          ) : null}
          {sideTab === "artifacts" ? (
            <ArtifactsPanel artifacts={artifacts} en={en} onOpen={openArtifact} />
          ) : null}
          {sideTab === "status" ? (
            <>
              <StatusCard status={status} en={en} />
              <DeletionPanel
                en={en}
                deletion={deletion}
                clearConfirm={clearConfirm}
                onClearConfirm={setClearConfirm}
                onClearAll={onClearAll}
                clearing={clearing}
                memoryCount={memories?.memories.length ?? 0}
              />
            </>
          ) : null}
          </div>
          {workspaceExpanded ? (
            <aside className={styles.workspaceSide} aria-label={en ? "Routine timeline" : "例行任务时间线"}>
              <div className={styles.workspaceSideHead}>
                <strong>{en ? "Routine timeline" : "例行任务时间线"}</strong>
                <span>{monitors.length}</span>
              </div>
              {monitors.slice(0, 5).map((monitor) => (
                <div key={monitor.id} className={styles.workspaceSideRow}>
                  <span className={styles.routineDot} data-active={monitor.status === "active" ? "true" : "false"} />
                  <div><strong>{monitor.name || monitor.symbol}</strong><small>{monitor.status === "active" ? (en ? "Active" : "运行中") : (en ? "Paused" : "暂停")}</small></div>
                </div>
              ))}
              {!monitors.length ? <p className={styles.deskEmpty}>{en ? "No routines yet." : "还没有例行任务。"}</p> : null}
              <div className={styles.workspaceSideHead}><strong>{en ? "Latest runs" : "最近运行"}</strong><span>{notifications.length}</span></div>
              {notifications.slice(0, 3).map((notification) => (
                <button key={notification.id} type="button" className={styles.workspaceSideRun} onClick={() => void analyzeAlert(notification)}>
                  <strong>{notification.title}</strong>
                  <small>{new Date(notification.created_at).toLocaleString()}</small>
                </button>
              ))}
            </aside>
          ) : null}
          </div>
        </aside>
      </main>
      </div>
      {traceTaskId ? (() => {
        const traceTask = tasks.find((task) => task.id === traceTaskId);
        if (!traceTask) return null;
        return (
          <TraceSheet
            key={traceTask.id}
            task={traceTask}
            run={run.taskId === traceTask.id ? run : null}
            en={en}
            onClose={() => setTraceTaskId(null)}
          />
        );
      })() : null}
      {botSwitcherOpen ? (
        <BotSwitcher
          bots={bots}
          loading={botsLoading}
          activeBotId={activeBotId}
          activeState={botState}
          en={en}
          onClose={() => setBotSwitcherOpen(false)}
          onSelect={(id) => { setBotSwitcherOpen(false); void selectBot(id); }}
          onCreate={() => { setBotSwitcherOpen(false); openCreateBot(); }}
          onEdit={() => { setBotSwitcherOpen(false); openEditBot(); }}
        />
      ) : null}
      {mobileRosterOpen ? (
        <div className={styles.mobileRosterBackdrop} onClick={() => setMobileRosterOpen(false)}>
          <div className={styles.mobileRosterDrawer} onClick={(event) => event.stopPropagation()}>
            <div className={styles.mobileRosterHead}>
              <span>{en ? "Switch assistant" : "切换助手"}</span>
              <button type="button" onClick={() => setMobileRosterOpen(false)} aria-label={en ? "Close roster" : "关闭助手列表"}>×</button>
            </div>
            <SessionSidebar
              bots={bots}
              botsLoading={botsLoading}
              sessionsLoading={sessionsLoading}
              activeBotId={activeBotId}
              activeState={botState}
              theme={theme}
              onCycleTheme={cycleTheme}
              onSelectBot={(id) => { setMobileRosterOpen(false); void selectBot(id); }}
              onCreateBot={() => { setMobileRosterOpen(false); if (productMode !== "build") setProductModeSafe("build"); openCreateBot(); }}
              onEditBot={() => { setMobileRosterOpen(false); openEditBot(); }}
              productMode={productMode}
              onProductMode={setProductModeSafe}
              sessions={sessions}
              activeId={sessionId}
              onSelect={(id) => { setMobileRosterOpen(false); void selectSession(id); }}
              onDeleteSession={(id) => { setMobileRosterOpen(false); void removeChat(id); }}
              onPatchThread={(id, patch) => void updateThreadMeta(id, patch)}
              onRenameThread={(id, title) => renameThread(id, title)}
              onMoveFolder={(id, folder) => moveThreadFolder(id, folder)}
              onClearHistory={() => { setMobileRosterOpen(false); void clearHistory(); }}
              onDeleteBot={() => { setMobileRosterOpen(false); void removeBot(); }}
              en={en}
            />
          </div>
        </div>
      ) : null}
      {appDialog ? (
        <div className={styles.modalBackdrop} role="dialog" aria-modal="true" aria-labelledby="app-dialog-title">
          <div className={styles.modalCard}>
            <div className={styles.modalHead}>
              <h2 id="app-dialog-title">{appDialog.title}</h2>
              <button type="button" onClick={() => setAppDialog(null)} aria-label={en ? "Close" : "关闭"}>×</button>
            </div>
            {appDialog.body ? <p className={styles.dialogBody}>{appDialog.body}</p> : null}
            {appDialog.kind === "prompt" ? (
              <label className={styles.fieldLabel}>
                <input
                  autoFocus
                  value={promptDraft}
                  placeholder={appDialog.placeholder || ""}
                  onChange={(event) => setPromptDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      if (dialogLockRef.current) return;
                      const value = promptDraft;
                      if (!appDialog.allowEmpty && !value.trim()) return;
                      const confirm = appDialog.onConfirm;
                      dialogLockRef.current = true;
                      setDialogConfirming(true);
                      setAppDialog(null);
                      confirm(value);
                    }
                  }}
                />
              </label>
            ) : null}
            <div className={styles.modalActions}>
              <button type="button" className={styles.quietButton} onClick={() => setAppDialog(null)}>
                {appDialog.cancelLabel || (en ? "Cancel" : "取消")}
              </button>
              <button
                type="button"
                className={appDialog.danger ? styles.dangerButton : styles.confirmButton}
                disabled={dialogConfirming}
                onClick={() => {
                  if (dialogLockRef.current) return;
                  if (appDialog.kind === "prompt") {
                    if (!appDialog.allowEmpty && !promptDraft.trim()) return;
                    const confirm = appDialog.onConfirm;
                    const value = promptDraft;
                    dialogLockRef.current = true;
                    setDialogConfirming(true);
                    setAppDialog(null);
                    confirm(value);
                  } else {
                    const confirm = appDialog.onConfirm;
                    dialogLockRef.current = true;
                    setDialogConfirming(true);
                    setAppDialog(null);
                    confirm();
                  }
                }}
              >
                {dialogConfirming ? (en ? "Working…" : "处理中…") : (appDialog.confirmLabel || (en ? "Confirm" : "确认"))}
              </button>
            </div>
          </div>
        </div>
      ) : null}
      {studioOpen ? (
        <BotStudio
          key={studioBot?.id || "new"}
          bot={studioBot}
          en={en}
          onClose={() => { setStudioOpen(false); setStudioBot(null); }}
          onSaved={onStudioSaved}
          onDeleted={onStudioDeleted}
          onNotice={setNotice}
          onError={(message) => setError(message)}
          onOpenMemory={() => { setStudioOpen(false); setStudioBot(null); setSideTab("memory"); setOpsMoreOpen(true); setPanelOpen(true); }}
        />
      ) : null}
      {pendingNotification ? (
        <div className={styles.modalBackdrop} role="dialog" aria-modal="true" aria-labelledby="alert-approval-title">
          <div className={styles.modalCard}>
            <div className={styles.modalHead}>
              <h2 id="alert-approval-title">{en ? "Confirm model call" : "确认调用模型"}</h2>
              <button type="button" onClick={() => setPendingNotification(null)} aria-label={en ? "Close" : "关闭"}>×</button>
            </div>
            <p className={styles.modalQuery}>{pendingNotification.title}</p>
            <p className={styles.modalHint}>{en ? "This alert exceeds the assistant budget. Confirm to research the move; it will still be read-only analysis." : "该异动分析超过当前助手预算。确认后开始只读研究，不会执行交易。"}</p>
            <div className={styles.modalActions}>
              <button type="button" className={styles.quietButton} onClick={() => setPendingNotification(null)}>{en ? "Cancel" : "取消"}</button>
              <button
                type="button"
                className={styles.confirmButton}
                disabled={dialogConfirming}
                onClick={() => {
                  if (dialogLockRef.current) return;
                  dialogLockRef.current = true;
                  setDialogConfirming(true);
                  const item = pendingNotification;
                  setPendingNotification(null);
                  setDialogConfirming(false);
                  dialogLockRef.current = false;
                  if (item) void analyzeAlert(item, true);
                }}
              >{dialogConfirming ? (en ? "Working…" : "处理中…") : (en ? "Confirm analysis" : "确认分析")}</button>
            </div>
          </div>
        </div>
      ) : null}
      {monitorDialogOpen ? (
        <div className={styles.modalBackdrop} role="dialog" aria-modal="true" aria-labelledby="new-monitor-title">
          <div className={styles.modalCard}>
            <div className={styles.modalHead}>
              <h2 id="new-monitor-title">{en ? "New price monitor" : "新建价格监控"}</h2>
              <button type="button" onClick={() => setMonitorDialogOpen(false)} aria-label={en ? "Close" : "关闭"}>×</button>
            </div>
            {monitorDraft.condition !== "report" ? (
              <label className={styles.fieldLabel}>{en ? "Symbol" : "标的"}
                <input value={monitorDraft.symbol} onChange={(event) => setMonitorDraft((current) => ({ ...current, symbol: event.target.value }))} placeholder={en ? "US.AAPL or 00700.HK" : "US.AAPL 或 00700.HK"} autoFocus />
              </label>
            ) : null}
            <label className={styles.fieldLabel}>{en ? "Trigger" : "触发类型"}
              <select value={monitorDraft.condition} onChange={(event) => { const condition = event.target.value as "price" | "announcement" | "earnings" | "report"; setMonitorDraft((current) => ({ ...current, condition, interval: condition === "report" ? 1440 : 15, threshold: condition === "earnings" ? 7 : 3 })); }}>
                <option value="price">{en ? "Price change" : "价格异动"}</option>
                <option value="announcement">{en ? "New CNINFO announcement" : "巨潮新公告（A股）"}</option>
                <option value="earnings">{en ? "Earnings date" : "财报日"}</option>
                <option value="report">{en ? "Watchlist report" : "自选股日报/周报"}</option>
              </select>
            </label>
            {monitorDraft.condition === "price" ? (
              <label className={styles.fieldLabel}>{en ? "Change threshold (%)" : "涨跌幅阈值（%）"}
                <input type="number" min={0.1} max={100} step={0.1} value={monitorDraft.threshold} onChange={(event) => setMonitorDraft((current) => ({ ...current, threshold: Number(event.target.value) }))} />
              </label>
            ) : monitorDraft.condition === "earnings" ? (
              <label className={styles.fieldLabel}>{en ? "Alert days before earnings" : "财报日前提醒天数"}
                <input type="number" min={0} max={60} value={monitorDraft.threshold} onChange={(event) => setMonitorDraft((current) => ({ ...current, threshold: Number(event.target.value) }))} />
              </label>
            ) : null}
            {monitorDraft.condition === "price" ? (
              <label className={styles.fieldLabel}>{en ? "Direction" : "方向"}
                <select value={monitorDraft.direction} onChange={(event) => setMonitorDraft((current) => ({ ...current, direction: event.target.value as "up" | "down" | "both" }))}>
                  <option value="both">{en ? "Either direction" : "上涨或下跌"}</option>
                  <option value="down">{en ? "Down only" : "仅下跌"}</option>
                  <option value="up">{en ? "Up only" : "仅上涨"}</option>
                </select>
              </label>
            ) : null}
            <label className={styles.fieldLabel}>{en ? "Check interval" : "检查间隔"}
              <select value={monitorDraft.interval} onChange={(event) => setMonitorDraft((current) => ({ ...current, interval: Number(event.target.value) }))}>
                {monitorDraft.condition === "report" ? (
                  <>
                    <option value={1440}>{en ? "Daily" : "每天"}</option>
                    <option value={10080}>{en ? "Weekly" : "每周"}</option>
                  </>
                ) : (
                  <>
                    <option value={15}>{en ? "Every 15 minutes" : "每 15 分钟"}</option>
                    <option value={30}>{en ? "Every 30 minutes" : "每 30 分钟"}</option>
                    <option value={60}>{en ? "Every hour" : "每小时"}</option>
                  </>
                )}
              </select>
            </label>
            <p className={styles.modalHint}>{en ? "Checks use polling and delayed data. Triggered results go to this assistant's in-app inbox; they do not place trades." : "使用轮询和延迟数据；触发结果进入当前助手的站内收件箱，不会执行交易。"}</p>
            <div className={styles.modalActions}>
              <button type="button" className={styles.quietButton} onClick={() => setMonitorDialogOpen(false)}>{en ? "Cancel" : "取消"}</button>
              <button type="button" className={styles.confirmButton} onClick={() => void saveMonitor()} disabled={monitorSaving || (monitorDraft.condition !== "report" && !monitorDraft.symbol.trim())}>{monitorSaving ? (en ? "Creating…" : "创建中…") : (en ? "Create monitor" : "创建监控")}</button>
            </div>
          </div>
        </div>
      ) : null}
      {focusDraft.open ? (
        <div className={styles.modalBackdrop} role="dialog" aria-modal="true" aria-labelledby="new-focus-title">
          <div className={styles.modalCard}>
            <div className={styles.modalHead}>
              <h2 id="new-focus-title">{en ? "Add a watch" : "加一条盯梢"}</h2>
              <button type="button" onClick={() => setFocusDraft({ open: false, text: "" })} aria-label={en ? "Close" : "关闭"}>×</button>
            </div>
            <label className={styles.fieldLabel}>{en ? "What should I keep watching?" : "要我盯什么？"}
              <textarea
                autoFocus
                rows={2}
                maxLength={200}
                value={focusDraft.text}
                placeholder={en ? "e.g. 00700.HK — did guidance change after the latest results?" : "例如：00700.HK 最新财报后，公司指引有没有改口？"}
                onChange={(event) => setFocusDraft((current) => ({ ...current, text: event.target.value }))}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    submitFocusDraft();
                  }
                  if (event.key === "Escape") setFocusDraft({ open: false, text: "" });
                }}
              />
            </label>
            <p className={styles.modalHint}>
              {en
                ? "Saving a watch does not start a research run. When matching filings or moves show up they land in the unread inbox."
                : "只记录关注点，不会立刻开始研究；有匹配的公告/异动时会进未读提醒。"}
            </p>
            <div className={styles.modalActions}>
              <button type="button" className={styles.quietButton} onClick={() => setFocusDraft({ open: false, text: "" })}>{en ? "Cancel" : "取消"}</button>
              <button type="button" className={styles.confirmButton} onClick={() => submitFocusDraft()} disabled={focusDraft.text.replace(/\s+/g, " ").trim().length < 4}>
                {en ? "Save watch" : "添加关注点"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function TaskPlanCard({ task, run, en, onResume }: { task: BotTask; run: RunState | null; en: boolean; onResume: () => void }) {
  const [plan, setPlan] = useState<TaskPlan | null>(null);
  const [open, setOpen] = useState(true);
  useEffect(() => {
    let alive = true;
    const timer = window.setTimeout(() => {
      void getTaskPlan(task.id)
        .then((data) => {
          if (alive) setPlan(data.plan);
        })
        .catch(() => undefined);
    }, 180);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [task.id, task.status, run?.lastSequence]);
  if (!plan || (!plan.steps.length && plan.status !== "planning")) return null;
  const completed = plan.steps.filter((step) => step.status === "completed" || step.status === "skipped").length;
  const incomplete = plan.steps.filter((step) => step.status !== "completed" && step.status !== "skipped");
  const canResume = task.status !== "running" && (plan.status === "partial" || plan.status === "failed" || task.status === "partial" || task.status === "failed" || task.status === "unknown");
  const statusText = plan.status === "completed"
    ? (en ? "All steps verified" : "全部步骤已验证")
    : plan.status === "failed"
      ? (en ? "Stopped" : "已停止")
      : plan.status === "partial"
        ? (en ? "Incomplete" : "未完成")
        : plan.status === "cancelled"
          ? (en ? "Cancelled" : "已取消")
          : plan.status === "planning"
            ? (en ? "Planning" : "规划中")
            : (en ? "Running" : "执行中");
  return (
    <details className={styles.planCard} open={open} onToggle={(event) => setOpen(event.currentTarget.open)} data-status={plan.status}>
      <summary>
        <span className={styles.planIcon} aria-hidden="true">▤</span>
        <strong>{en ? "Task plan" : "任务清单"}</strong>
        <span className={styles.planSource}>{plan.source === "llm" ? (en ? "LLM plan" : "LLM 规划") : en ? "Template plan" : "模板规划"}</span>
        <span className={styles.planCount}>{completed}/{plan.steps.length}</span>
        <span className={styles.planStatus} data-status={plan.status}>{statusText}</span>
      </summary>
      <div className={styles.planBody}>
        <div className={styles.planProgress} aria-hidden="true">
          <i style={{ width: `${plan.steps.length ? Math.round((completed / plan.steps.length) * 100) : 0}%` }} />
        </div>
        <p className={styles.planQuery}>{plan.original_query}</p>
        {!plan.steps.length ? <p className={styles.planQuery}>{en ? "Splitting the request into a persistent task stack…" : "正在把请求拆解成持久化任务清单…"}</p> : null}
        <ol className={styles.planSteps}>
          {plan.steps.map((step) => (
            <li key={step.step_id} data-status={step.status}>
              <span className={styles.planStepDot} aria-hidden="true">{step.status === "completed" ? "✓" : step.status === "failed" ? "!" : step.status === "retry" ? "↻" : step.status === "running" ? "·" : ""}</span>
              <div className={styles.planStepCopy}>
                <strong>{step.step_id} · {step.title}</strong>
                <small>
                  {step.validation.description || (en ? "Validated by server" : "由服务端校验")}
                  {step.tools.length ? ` · ${step.tools.join(" / ")}` : ""}
                  {step.attempts ? ` · ${en ? "attempt" : "尝试"} ${step.attempts}` : ""}
                </small>
                {step.last_error ? <em>{step.last_error}</em> : null}
              </div>
              <span className={styles.planStepStatus} data-status={step.status}>
                {step.status === "completed" ? (en ? "done" : "完成") : step.status === "failed" ? (en ? "failed" : "失败") : step.status === "retry" ? (en ? "retry" : "待重试") : step.status === "running" ? (en ? "running" : "执行中") : (en ? "pending" : "等待")}
              </span>
            </li>
          ))}
        </ol>
        {incomplete.length ? (
          <div className={styles.planFoot}>
            <span>{en ? `${incomplete.length} step(s) not verified; the answer is partial.` : `${incomplete.length} 个步骤未通过校验，本次回答按部分完成处理。`}</span>
            {canResume ? <button type="button" onClick={onResume}>{en ? "Resume from checkpoint" : "从断点继续"}</button> : null}
          </div>
        ) : null}
      </div>
    </details>
  );
}

function ToolRunCards({ run, task, en, onOpenTrace }: { run: RunState | null; task: BotTask; en: boolean; onOpenTrace: () => void }) {
  const liveRows: RunStep[] = [];
  if (run) {
    const latest = new Map<string, RunStep>();
    for (const step of run.steps) {
      if (!step.stepId.startsWith("tool:")) continue;
      latest.set(step.stepId.slice(5), step);
    }
    liveRows.push(...Array.from(latest.values()).sort((a, b) => a.sequence - b.sequence));
  }
  const storedRows = task.result?.research_steps ?? [];
  if (!liveRows.length && !storedRows.length) return null;
  const rows = liveRows.length
    ? liveRows.map((step) => ({
        key: step.stepId,
        tool: step.stepId.slice(5),
        status: step.status,
        summary: step.summary,
        endpoint: step.endpoint,
        endpoints: step.endpoints ?? [],
        evidence: null as number | null,
        retrieved: null as string | null,
        reason: null as string | null,
      }))
    : storedRows.map((step, index) => ({
        key: `${step.tool}-${index}`,
        tool: step.tool,
        status: step.status,
        summary: "",
        endpoint: step.endpoint ?? null,
        endpoints: [] as string[],
        evidence: step.evidence_count ?? null,
        retrieved: step.retrieved_at ?? null,
        reason: step.reason_code ?? null,
      }));
  const visible = rows.slice(-4);
  return (
    <div className={styles.toolCards} aria-label={en ? "Tool calls" : "工具调用"}>
      <div className={styles.toolCardsHead}>
        <span>{en ? "Tool calls" : "工具调用"}</span>
        <small>{rows.length} {en ? "this run" : "次"}</small>
      </div>
      {visible.map((row) => {
        const label = toolLabel(row.tool, en);
        const active = row.status === "running";
        return (
          <details key={row.key} className={styles.toolCard} data-status={row.status} open={active}>
            <summary>
              <span className={styles.toolCardIcon} aria-hidden="true">{label.icon}</span>
              <span className={styles.toolCardName}>{label.label}</span>
              <span className={styles.toolCardStatus} data-status={row.tool === "model_knowledge_fill" ? "warning" : row.status}>
                {active ? <i className={styles.liveDot} aria-hidden="true" /> : null}
                {row.tool === "model_knowledge_fill"
                  ? (en ? "Unverified" : "未核验")
                  : stepStatusLabel(row.status, en)}
              </span>
              <span className={styles.toolCardChevron} aria-hidden="true">⌄</span>
            </summary>
            <div className={styles.toolCardBody}>
              {row.summary ? <p>{row.summary}</p> : null}
              <dl>
                <div><dt>{en ? "Tool" : "工具"}</dt><dd><code>{row.tool}</code></dd></div>
                {row.endpoint ? <div><dt>{en ? "Source" : "来源"}</dt><dd>{row.endpoint}</dd></div> : null}
                {row.endpoints.length > 1 ? <div><dt>{en ? "Also" : "同时"}</dt><dd>{row.endpoints.slice(1).join(" · ")}</dd></div> : null}
                {row.evidence != null ? <div><dt>{en ? "Evidence" : "证据"}</dt><dd>{row.evidence}</dd></div> : null}
                {row.retrieved ? <div><dt>{en ? "Retrieved" : "获取时间"}</dt><dd>{formatTime(row.retrieved, en ? "en" : "zh")}</dd></div> : null}
                {row.reason ? <div><dt>{en ? "Reason" : "原因"}</dt><dd>{row.reason}</dd></div> : null}
              </dl>
            </div>
          </details>
        );
      })}
      {rows.length > visible.length ? (
        <button type="button" className={styles.toolCardsMore} onClick={onOpenTrace}>
          {en ? `View ${rows.length - visible.length} more in the trace` : `在运行过程中查看其余 ${rows.length - visible.length} 次`} ↗
        </button>
      ) : null}
    </div>
  );
}

function UIBlocks({ blocks, en }: { blocks: UIBlock[] | undefined; en: boolean }) {
  const safe = (Array.isArray(blocks) ? blocks : []).filter((block) => block && ["card", "collapse", "callout"].includes(block.type) && block.title && block.body).slice(0, 3);
  if (!safe.length) return null;
  return (
    <div className={styles.uiBlocks} aria-label={en ? "Presentation blocks" : "展示块"}>
      {safe.map((block, index) => {
        if (block.type === "collapse") return <UICollapseBlock key={`${block.type}-${index}`} block={block} en={en} />;
        if (block.type === "callout") {
          return (
            <aside key={`${block.type}-${index}`} className={styles.uiCallout} data-tone={block.tone || "info"}>
              <strong>{block.title}</strong>
              <div className={styles.uiBlockBody}><ResearchMarkdown content={block.body} /></div>
              {block.footer ? <small>{block.footer}</small> : null}
            </aside>
          );
        }
        return (
          <section key={`${block.type}-${index}`} className={styles.uiCard} data-tone={block.tone || "info"}>
            <header className={styles.uiCardHead}>
              <span aria-hidden="true">{block.tone === "warning" ? "!" : block.tone === "success" ? "✓" : "◆"}</span>
              <h3>{block.title}</h3>
            </header>
            <div className={styles.uiBlockBody}><ResearchMarkdown content={block.body} /></div>
            {block.footer ? <footer className={styles.uiCardFoot}>{block.footer}</footer> : null}
          </section>
        );
      })}
    </div>
  );
}

function UICollapseBlock({ block, en }: { block: UIBlock; en: boolean }) {
  const [open, setOpen] = useState(Boolean(block.default_open));
  return (
    <details
      className={styles.uiCollapse}
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        <span>{block.title}</span>
        <small>{open ? (en ? "Collapse" : "收起") : en ? "Expand" : "展开"}</small>
      </summary>
      <div className={styles.uiBlockBody}><ResearchMarkdown content={block.body} /></div>
      {block.footer ? <footer className={styles.uiCardFoot}>{block.footer}</footer> : null}
    </details>
  );
}

function EmptyPortfolioCard({ en, onOpenPortfolio, onRetry, onAnalyzeStock }: {
  en: boolean;
  onOpenPortfolio: () => void;
  onRetry: () => void;
  onAnalyzeStock: () => void;
}) {
  return (
    <section className={styles.emptyPortfolio} role="status">
      <span className={styles.emptyPortfolioIcon} aria-hidden="true">◫</span>
      <div>
        <strong>{en ? "No holdings yet" : "还没有持仓"}</strong>
        <p>{en
          ? "A portfolio review needs at least one position. Add a holding first, or ask about a single stock instead."
          : "持仓体检至少需要一个持仓。先添加持仓，或者直接问一只股票。"}</p>
        <div className={styles.emptyPortfolioActions}>
          <a href="/work?context=portfolio" target="_blank" rel="noreferrer" className={styles.emptyPortfolioPrimary}>
            {en ? "Add a holding" : "添加持仓"}
          </a>
          <button type="button" onClick={onRetry}>{en ? "Check again" : "重新检查"}</button>
          <button type="button" onClick={onAnalyzeStock}>{en ? "Analyze a stock instead" : "改为分析单股"}</button>
        </div>
      </div>
    </section>
  );
}

function WorkflowPanel({ workflow, en, onFollowUp }: {
  workflow: NonNullable<BotTask["result"]>["workflow"];
  en: boolean;
  onFollowUp: (prompt: string) => void;
}) {
  if (!workflow) return null;
  const handoff = workflow.handoff;
  const verification = workflow.verification;
  return (
    <section className={styles.workflowPanel} aria-label={en ? "Task control" : "任务控制"}>
      <header>
        <span>{en ? "TASK CONTROL" : "任务控制"}</span>
        <strong>{workflow.title || (en ? "Step by step" : "分步执行")}</strong>
        <em data-stage={workflow.stage}>{workflow.stage === "handoff" ? (en ? "Needs you" : "需要你接管") : workflow.stage === "verification" ? (en ? "Verify" : "待核验") : workflow.stage === "complete" ? (en ? "Done" : "已完成") : (en ? "In progress" : "进行中")}</em>
      </header>

      {workflow.tracks?.length ? (
        <div className={styles.workflowTracks}>
          {workflow.tracks.map((track) => (
            <div key={track.key} data-status={track.status}>
              <span aria-hidden="true">{track.status === "done" ? "✓" : track.status === "blocked" ? "!" : "○"}</span>
              <div><strong>{track.label}</strong>{track.evidence ? <small>{track.evidence}</small> : null}</div>
            </div>
          ))}
        </div>
      ) : null}

      {verification ? (
        <div className={styles.workflowCheck} data-conclusion={verification.conclusion}>
          <span>{en ? "Status check" : "状态核对"}</span>
          <strong>{verification.conclusion === "complete" ? (en ? "Complete" : "已完成") : verification.conclusion === "not_complete" ? (en ? "Not complete" : "尚未完成") : (en ? "Unconfirmed" : "尚未确认")}</strong>
          {verification.basis ? <p>{verification.basis}</p> : null}
        </div>
      ) : null}

      {handoff ? (
        <div className={styles.handoffCard}>
          <span className={styles.handoffKicker}>{en ? "HANDOFF · YOUR TURN" : "交接 · 该你操作"}</span>
          <div className={styles.handoffNow}>👉 {en ? "Now you need to act manually" : "现在需要你手动操作"}</div>
          <strong>{handoff.reason}</strong>
          {handoff.page_title || handoff.page_state ? (
            <p>
              {handoff.page_title ? <b>{handoff.page_title}</b> : null}
              {handoff.page_state ? ` ${handoff.page_state}` : null}
            </p>
          ) : null}
          {handoff.boundary ? <small>{handoff.boundary}</small> : null}
          <div className={styles.handoffActions}>
            <button type="button" onClick={() => onFollowUp(`${en ? "I completed the manual verification. Continue the remaining workflow." : "我已手动完成验证，请继续剩余流程。"}

${en ? "Do not repeat completed steps. Re-check my personal status with UI evidence only." : "不要重复已完成步骤；只通过页面 UI 核验我的个人状态。"}`)}>
              {handoff.resume_label || (en ? "I completed verification" : "我已完成验证")}
            </button>
            {workflow.options?.map((option) => <button key={option} type="button" onClick={() => onFollowUp(option)}>{option}</button>)}
          </div>
        </div>
      ) : workflow.options?.length ? (
        <div className={styles.workflowOptions}>
          {workflow.options.map((option) => <button key={option} type="button" onClick={() => onFollowUp(option)}>{option}</button>)}
        </div>
      ) : null}
    </section>
  );
}

function splitAnswerSegments(text: string): string[] {
  const normalized = String(text || "").replace(/\r\n/g, "\n").trim();
  if (!normalized) return [];
  const blocks = normalized.split(/\n{2,}/).map((block) => block.trim()).filter(Boolean);
  const segments: string[] = [];
  let current = "";
  const flush = () => {
    if (current.trim()) segments.push(current.trim());
    current = "";
  };
  for (const block of blocks) {
    // Keep a heading attached to its first paragraph, but do not put an entire
    // report in one bubble.
    const isHeading = /^#{1,4}\s/.test(block);
    if (isHeading && current.trim()) flush();
    if (!current) current = block;
    else if (current.length + block.length + 2 <= 900) current += `\n\n${block}`;
    else {
      flush();
      current = block;
    }
    if (current.length > 1200) {
      const sentences = current.split(/(?<=[。！？.!?])\s+/);
      let piece = "";
      for (const sentence of sentences) {
        if (piece && piece.length + sentence.length > 900) {
          segments.push(piece.trim());
          piece = "";
        }
        piece += sentence;
      }
      current = piece;
    }
  }
  flush();
  return segments.length ? segments : [normalized];
}

function AssistantAnswer({ content, en, className, animate = false }: {
  content: string;
  en: boolean;
  className?: string;
  /** Reveal one section at a time, with uneven human-ish pauses. */
  animate?: boolean;
}) {
  const segments = splitAnswerSegments(content);
  const [visible, setVisible] = useState(segments.length);
  const timers = useRef<number[]>([]);

  useEffect(() => {
    timers.current.forEach((timer) => window.clearTimeout(timer));
    timers.current = [];
    if (!animate || segments.length <= 1) {
      setVisible(segments.length);
      return;
    }
    const reduce = typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      setVisible(segments.length);
      return;
    }
    setVisible(1);
    let elapsed = 0;
    for (let index = 1; index < segments.length; index += 1) {
      const previous = segments[index - 1] || "";
      // Longer sections read slower; the jitter keeps the rhythm from feeling
      // like a metronome. The first follow-up gets an extra thinking beat.
      const reading = Math.min(1500, 260 + previous.length * 6);
      const jitter = 90 + Math.random() * 330;
      const opening = index === 1 ? 260 : 0;
      elapsed += reading + jitter + opening;
      const target = index + 1;
      const timer = window.setTimeout(() => setVisible(target), elapsed);
      timers.current.push(timer);
    }
    return () => {
      timers.current.forEach((timer) => window.clearTimeout(timer));
      timers.current = [];
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [content, animate]);

  const shown = animate ? segments.slice(0, Math.max(1, visible)) : segments;
  const composing = animate && visible < segments.length;
  return (
    <div className={styles.answerSegments} aria-label={en ? "Assistant answer" : "助手回答"}>
      {shown.map((segment, index) => (
        <div
          key={`${index}-${segment.slice(0, 24)}`}
          className={`${styles.agentBubble} ${className || ""} ${animate ? styles.enter : ""}`}
        >
          <ResearchMarkdown content={segment} />
        </div>
      ))}
      {composing ? (
        <div className={`${styles.agentBubble} ${styles.composingBubble}`} role="status" aria-live="polite">
          <span className={styles.typingDots} aria-label={en ? "Still writing" : "还在说"}><i /><i /><i /></span>
        </div>
      ) : null}
    </div>
  );
}

function TranscriptMessage({ message, en, botName }: { message: BotMessage; en: boolean; botName?: string }) {
  if (message.role === "user") {
    return (
      <div className={styles.transcriptMessage}>
        <div className={styles.userBubble}><p>{message.content}</p></div>
      </div>
    );
  }
  if (message.role === "assistant" && message.kind === "notice") {
    return (
      <div className={styles.transcriptMessage}>
        <div className={styles.findingBubble}>
          <span className={styles.findingDot} aria-hidden="true" />
          <p>{message.content}</p>
        </div>
      </div>
    );
  }
  if (message.role === "assistant") {
    return (
      <div className={styles.transcriptMessage}>
        <div className={styles.botTurnHead}>
          <GrokAvatar name={botName || "Financial Bot"} state="completed" size="small" />
          <strong>{botName || "Financial Bot"}</strong>
          <span>{en ? "Answer" : "回答"}</span>
        </div>
        <AssistantAnswer content={message.content} en={en} />
      </div>
    );
  }
  return null;
}

/** Readable disposition: prefer server row; else status-derived with an honest gap note. */
function DispositionNote({ task, disposition, en }: {
  task: BotTask;
  disposition: BotDisposition | null | undefined;
  en: boolean;
}) {
  const labels: Record<string, [string, string]> = {
    accepted: ["研究", "Research"],
    direct_answer: ["直接回答", "Direct answer"],
    clarify: ["等待补充", "Waiting for input"],
    dropped: ["无需研究", "Dropped"],
  };
  const statusFallback: Partial<Record<string, string>> = {
    dropped: "dropped",
    waiting_user: "clarify",
  };

  let kind: string | null = null;
  let detail: string | null = null;
  let gap: string | null = null;

  if (disposition?.disposition) {
    kind = String(disposition.disposition);
    detail = disposition.reason_display || disposition.reason_code || null;
  } else if (statusFallback[task.status]) {
    kind = statusFallback[task.status] || null;
    detail = statusLabel(task.status, en ? "en" : "zh", task.waiting_reason);
    gap = en
      ? "No disposition row for this task yet; label comes from task status."
      : "尚无 disposition 记录；当前标签来自任务状态。";
  } else {
    return null;
  }

  const pair = kind ? labels[kind] || null : null;
  const title = pair ? (en ? pair[1] : pair[0]) : kind;

  return (
    <div className={styles.dispositionNote} data-kind={kind} role="note">
      <strong>{en ? "Disposition" : "处理判定"}</strong>
      <span className={styles.dispositionKind}>{title}</span>
      {detail ? <span className={styles.dispositionDetail}>{detail}</span> : null}
      {gap ? <span className={styles.dispositionGap}>{gap}</span> : null}
    </div>
  );
}

/** Honest snapshot of what this run froze / reported — only real fields. */

function ResultTrustStrip({ task, en }: { task: BotTask; en: boolean }) {
  const result = task.result;
  if (!result) return null;
  const meta = (result.meta && typeof result.meta === "object") ? result.meta as Record<string, unknown> : null;
  const answerBasis = typeof meta?.answer_basis === "string" ? meta.answer_basis : null;
  const evidenceCount = Array.isArray(result.evidence) ? result.evidence.length : 0;
  const gapCount = Array.isArray(result.data_gaps) ? result.data_gaps.length : 0;
  const chartAsOf = result.chart?.data_as_of || null;
  const cardAsOf = (result.data_cards || []).map((card) => card.as_of).find(Boolean) || null;
  const evidenceAsOf = (result.evidence || [])
    .map((item) => (item && typeof item === "object" ? (item as { data_as_of?: string | null }).data_as_of : null))
    .find(Boolean) || null;
  const dataAsOf = chartAsOf || cardAsOf || evidenceAsOf || null;

  let basisLabel: string;
  if (answerBasis === "source_data") {
    basisLabel = en
      ? (dataAsOf ? `Source data · as of ${dataAsOf}` : `Source data · ${evidenceCount} citation(s)`)
      : (dataAsOf ? `数据源 · 时点 ${dataAsOf}` : `数据源 · ${evidenceCount} 条引用`);
  } else if (answerBasis === "model_knowledge") {
    basisLabel = en ? "Model knowledge (not live-verified)" : "模型知识（未经数据源核实）";
  } else if (evidenceCount > 0) {
    basisLabel = en
      ? (dataAsOf ? `Evidence present · as of ${dataAsOf}` : `${evidenceCount} source(s) attached`)
      : (dataAsOf ? `已附证据 · 时点 ${dataAsOf}` : `已附 ${evidenceCount} 条来源`);
  } else {
    basisLabel = task.research_mode === "openmuse_shared"
      ? (en ? "No structured source records returned" : "未返回结构化来源记录")
      : (en ? "Basis not labeled yet" : "依据尚未标注");
  }

  const gapLabel = gapCount > 0
    ? (en ? `${gapCount} gap(s) listed` : `已标 ${gapCount} 项缺口`)
    : task.research_mode === "openmuse_shared"
      ? (en ? "No structured gap list returned" : "未返回结构化缺口清单")
      : (en ? "No gaps listed" : "未标明缺口");

  // 2–3 plain-language action tiers from evidence / gaps / answer_basis — not a fixed disclaimer.
  let actionTier: "clue" | "incomplete" | "reference";
  if (answerBasis === "model_knowledge" || evidenceCount === 0) {
    actionTier = "clue";
  } else if (gapCount >= 2 || (gapCount >= 1 && evidenceCount < 2) || answerBasis !== "source_data") {
    actionTier = "incomplete";
  } else {
    // source_data + some evidence + few gaps
    actionTier = "reference";
  }
  const actionLabel = actionTier === "reference"
    ? (en
      ? "Evidence is enough to discuss — still not a trade ticket"
      : "证据够讨论参考，但仍不能据此下单")
    : actionTier === "incomplete"
      ? (en
        ? "Evidence incomplete — do not decide from this alone"
        : "证据不足，别据此单独做决定")
      : (en
        ? "Mostly a lead — missing key data, treat as a clue"
        : "缺关键数据，只能当线索");

  return (
    <div className={`${styles.resultTrustStrip} ${styles.trustCard}`} role="note" aria-label={en ? "Trust labels" : "可信度标注"} data-action-tier={actionTier}>
      <div className={styles.trustCardHead}>
        <strong>{en ? "How far to trust this" : "这条答案能信到哪"}</strong>
        <span>{en ? "Sources, gaps, and what you can do with it" : "来源、缺口，以及能不能据此行动"}</span>
      </div>
      <div className={styles.trustCardGrid}>
        <div><span>{en ? "Basis" : "依据到哪"}</span><strong>{basisLabel}</strong></div>
        <div><span>{en ? "Gaps" : "缺什么"}</span><strong>{gapLabel}</strong></div>
        <div><span>{en ? "Action" : "能否据此行动"}</span><strong>{actionLabel}</strong></div>
      </div>
    </div>
  );
}

function RunSnapshot({ task, en }: { task: BotTask; en: boolean }) {
  const auth = (task.authorization && typeof task.authorization === "object")
    ? task.authorization as Record<string, unknown>
    : null;
  const scope = (auth?.scope && typeof auth.scope === "object")
    ? auth.scope as Record<string, unknown>
    : null;
  const meta = (task.result?.meta && typeof task.result.meta === "object")
    ? task.result.meta as Record<string, unknown>
    : null;
  const dataPlan = task.result?.data_plan ?? null;
  const chartAsOf = task.result?.chart?.data_as_of || null;
  const cardAsOf = (task.result?.data_cards || []).map((card) => card.as_of).find(Boolean) || null;
  const evidenceAsOf = (task.result?.evidence || [])
    .map((item) => (item && typeof item === "object" ? (item as { data_as_of?: string | null }).data_as_of : null))
    .find(Boolean) || null;
  const dataAsOf = chartAsOf || cardAsOf || evidenceAsOf || null;
  const evidenceCount = Array.isArray(task.result?.evidence) ? task.result.evidence.length : 0;
  const terminal = ["completed", "partial", "dropped", "failed", "cancelled", "unknown"].includes(task.status);

  const researchMode = (typeof scope?.research_mode === "string" && scope.research_mode)
    || task.research_mode
    || null;
  const isOpenMuseResearch = researchMode === "openmuse_shared";
  const model = researchModelDisplayName(
    researchMode,
    typeof auth?.model === "string" ? auth.model : null,
    en ? "en" : "zh",
  );
  const promptVersion = auth?.prompt_version != null ? String(auth.prompt_version) : null;
  const dataAuth = typeof auth?.data_authorization === "string" ? auth.data_authorization : null;
  const answerBasis = typeof meta?.answer_basis === "string" ? meta.answer_basis : null;
  const provider = typeof dataPlan?.used_provider === "string" ? dataPlan.used_provider : null;
  const inherited = typeof auth?.inherited_from === "string" ? auth.inherited_from : null;
  const dataSources = (auth?.data_sources && typeof auth.data_sources === "object" && !Array.isArray(auth.data_sources))
    ? auth.data_sources as Record<string, unknown>
    : null;
  const dataSourceLabels = dataSources
    ? Object.keys(dataSources).map((key) => {
        const value = dataSources[key];
        return typeof value === "string" && value.trim() ? value.trim() : key;
      }).filter(Boolean)
    : [];

  const rows: Array<{ label: string; value: string }> = [];
  if (researchMode) {
    rows.push({
      label: en ? "Scope" : "范围",
      value: researchModeDisplayName(researchMode, en ? "en" : "zh"),
    });
  }
  if (model) rows.push({ label: en ? "Model" : "模型", value: model });
  if (isOpenMuseResearch) {
    rows.push({ label: en ? "Run status" : "运行状态", value: statusLabel(task.status, en ? "en" : "zh", task.waiting_reason) });
    if (task.started_at) rows.push({ label: en ? "Started" : "开始时间", value: formatTime(task.started_at, en ? "en" : "zh") });
    if (task.finished_at) rows.push({ label: en ? "Finished" : "结束时间", value: formatTime(task.finished_at, en ? "en" : "zh") });
  }
  if (promptVersion && !isOpenMuseResearch) rows.push({ label: en ? "Rule / prompt" : "规则版本", value: `v${promptVersion}` });
  if (dataAuth && !isOpenMuseResearch) {
    rows.push({
      label: en ? "Data scope" : "数据授权",
      value: dataAuth === "local_single_user"
        ? (en ? "Local single-user" : "本机单用户")
        : dataAuth,
    });
  }
  if (answerBasis) {
    rows.push({
      label: en ? "Answer basis" : "回答依据",
      value: answerBasis === "source_data"
        ? (en ? "Source data" : "数据源")
        : answerBasis === "model_knowledge"
          ? (en ? "Model knowledge" : "模型知识")
          : answerBasis,
    });
  }
  if (dataAsOf) rows.push({ label: en ? "Data as of" : "数据时点", value: String(dataAsOf) });
  if (dataSourceLabels.length) {
    rows.push({
      label: en ? "Data sources" : "数据源清单",
      value: dataSourceLabels.slice(0, 6).join(en ? "; " : "；"),
    });
  } else if (provider) {
    rows.push({ label: en ? "Provider used" : "实际数据源", value: provider });
  }
  if (inherited) rows.push({ label: en ? "Inherited from" : "继承自", value: inherited.slice(0, 8) });

  const gaps: string[] = [];
  if (isOpenMuseResearch) {
    if (terminal && !evidenceCount) gaps.push(en
      ? "OpenMuse did not return structured source records for this run."
      : "OpenMuse 本次未返回结构化来源记录。");
    if (terminal && evidenceCount > 0 && !dataAsOf) gaps.push(en
      ? "No source data timepoint was included."
      : "来源记录未提供数据时点。");
  } else if (!auth) {
    gaps.push(en
      ? "No frozen authorization on this task."
      : "此任务没有冻结授权快照。");
  } else {
    if (!model) gaps.push(en ? "Model not recorded in authorization." : "授权中未记录模型。");
    if (!promptVersion && !isOpenMuseResearch) gaps.push(en ? "Prompt/rule version missing." : "未记录规则/提示词版本。");
    if (!dataAsOf) {
      if (answerBasis === "model_knowledge") {
        gaps.push(en
          ? "No market data timepoint (model-knowledge answer)."
          : "无行情数据时点（本次为模型知识回答）。");
      } else if (terminal) {
        gaps.push(en
          ? "No data timepoint in chart/cards/evidence."
          : "图表/卡片/证据中均无数据时点。");
      } else {
        gaps.push(en
          ? "Data timepoint not available yet."
          : "数据时点尚未生成。");
      }
    }
    if (auth.data_sources == null) {
      if (answerBasis === "model_knowledge") {
        gaps.push(en
          ? "No provider list (no tools/evidence cited)."
          : "无数据源清单（未引用工具/证据）。");
      } else if (terminal) {
        gaps.push(en
          ? "Provider list was not attached from evidence."
          : "未能从证据回填数据源清单。");
      } else {
        gaps.push(en
          ? "Provider list fills after evidence returns (null at create)."
          : "创建时数据源为空；有证据后会回填。");
      }
    }
  }

  if (!rows.length && !gaps.length) return null;

  return (
    <div className={styles.runSnapshot} role="note">
      <div className={styles.runSnapshotHead}>
        <strong>{en ? "This run" : "本次运行"}</strong>
        <span>{en ? "Frozen authorization + result meta" : "冻结授权与结果元数据"}</span>
      </div>
      {rows.length ? (
        <dl className={styles.runSnapshotList}>
          {rows.map((row) => (
            <div key={row.label} className={styles.runSnapshotRow}>
              <dt>{row.label}</dt>
              <dd>{row.value}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className={styles.runSnapshotGap}>
          {en ? "No authorization fields available to display yet." : "暂无可展示的授权字段。"}
        </p>
      )}
      {gaps.length ? (
        <p className={styles.runSnapshotGap}>
          {gaps.join(en ? " " : "")}
        </p>
      ) : null}
    </div>
  );
}

function TaskView({
  task,
  run,
  stopped,
  en,
  disposition = null,
  candidates,
  drafts,
  onDraft,
  onConfirm,
  onDismiss,
  onFollowUp,
  onAnswerShared,
  onRetryAsQuick,
  onOpenTrace,
  onResume,
  showUser = true,
  showAnswer = true,
  showHeader = true,
  showTime = true,
  botName,
}: {
  task: BotTask;
  run: RunState | null;
  stopped?: boolean;
  en: boolean;
  disposition?: BotDisposition | null;
  candidates: MemoryCandidate[];
  drafts: Record<string, string>;
  onDraft: (id: string, value: string) => void;
  onConfirm: (candidate: MemoryCandidate) => void;
  onDismiss: (candidate: MemoryCandidate) => void;
  onFollowUp: (prompt: string) => void;
  onAnswerShared: (taskId: string, answer: string) => Promise<boolean>;
  onRetryAsQuick?: (prompt: string) => void;
  onOpenTrace: () => void;
  onResume: () => void;
  showUser?: boolean;
  showAnswer?: boolean;
  showHeader?: boolean;
  showTime?: boolean;
  botName?: string;
}) {
  const [sharedAnswer, setSharedAnswer] = useState("");
  const [answerSending, setAnswerSending] = useState(false);
  const result = task.result;
  const liveStatus = stopped ? "cancelled" : run ? run.status : task.status;
  const live = Boolean(run && !isTerminal(run.status));
  const completed = run ? completedSteps(run) : [];
  const active = run ? activeSteps(run) : [];
  const clarification = result?.clarification;
  return (
    <article className={styles.turn} id={`task-${task.id}`}>
      {showTime ? <div className={styles.messageTime}><time>{formatTime(task.created_at, en ? "en" : "zh")}</time></div> : null}
      {showUser ? <div className={styles.userBubble}>
        <p>{task.query}</p>
      </div> : null}

      <div className={styles.botTurn}>
        {showHeader ? <div className={styles.botTurnHead}>
          <GrokAvatar
            name={botName || "Financial Bot"}
            state={botStateFromTask(liveStatus, live ? "live" : "idle")}
            size="small"
          />
          <strong>{botName || (en ? "Financial Bot" : "Financial Bot")}</strong>
          <span>{live ? (active.at(-1)?.summary || (en ? "Working" : "处理中")) : liveStatus === "completed" ? (en ? "Research summary" : "研究摘要") : (en ? "Result" : "结果")}</span>
        </div> : null}

        {live ? (
          <div className={styles.findingList}>
            {findingNotices(run!).map((notice) => (
              <div key={notice.id} className={`${styles.findingBubble} ${styles.enter}`}>
                <span className={styles.findingDot} aria-hidden="true" />
                <p>{notice.text}</p>
              </div>
            ))}
            <div className={`${styles.findingBubble} ${styles.typingBubble}`} role="status" aria-live="polite">
              <GrokAvatar name={botName || "Financial Bot"} state="running" size="small" />
              <span className={styles.typingDots} aria-label={en ? "Working" : "正在处理"}>
                <i /><i /><i />
              </span>
              <span className={styles.typingLabel}>
                {active.at(-1)?.summary || (en ? "Working on the next piece" : "正在整理下一条")}
              </span>
            </div>
          </div>
        ) : null}
        {result?.meta?.answer_basis === "model_knowledge" ? (
          <p className={styles.modelOnlyNote} role="note">
            <strong>{en ? "No source data this run" : "本次未取到数据源"}</strong>
            <span>
              {en
                ? "The answer below is model knowledge, not verified live data. Gaps are listed with the run."
                : "以下结论来自模型知识，未经数据源核实；缺口列在本次运行里。"}
            </span>
          </p>
        ) : result?.meta?.llm_research_recovery || result?.meta?.model_fill_as_tool ? (
          <p className={styles.modelOnlyNote} role="note">
            <strong>{en ? "Includes unverified model fill" : "含未核验的模型补齐"}</strong>
            <span>
              {en
                ? "See the tool row “Model fill (unverified)”. Treat those parts as unverified."
                : "详见工具调用里的「模型补齐（未核验）」；该部分不能当作数据源证据。"}
            </span>
          </p>
        ) : result?.meta?.llm_data_gap_fill ? (
          <p className={styles.modelOnlyNote} role="note">
            <strong>{en ? "Some fields model-filled" : "部分字段由模型补齐"}</strong>
            <span>
              {en
                ? "Missing provider fields were filled by the selected model and marked unverified."
                : "缺失数据已由当前所选模型补齐，均标注为未核验，不能当作数据源证据。"}
            </span>
          </p>
        ) : null}
        {showAnswer && result?.summary ? (
          <AssistantAnswer content={result.summary} en={en} animate={Boolean(run && run.taskId === task.id)} />
        ) : null}
        {showAnswer && result && !result.summary && (isTerminal(liveStatus) || liveStatus === "partial") ? (
          <p className={styles.modelOnlyNote} role="status">
            <strong>{en ? "No written summary was returned" : "本次没有返回文字结论"}</strong>
            <span>{result.evidence?.length
              ? (en ? "Source records are listed below; the research run did not provide a synthesis." : "下方仅列出来源记录；本次运行没有提供综合结论。")
              : (en ? "No summary or structured source records were returned." : "本次没有返回摘要或结构化来源记录。")}</span>
          </p>
        ) : null}
        <PriceChartBubble chart={result?.chart} en={en} />
        {result?.ui_blocks?.length ? <UIBlocks blocks={result.ui_blocks} en={en} /> : null}
        <DispositionNote task={task} disposition={disposition} en={en} />
        <ResultTrustStrip task={task} en={en} />
        <RunSnapshot task={task} en={en} />

        {task.research_mode === "openmuse_shared" && liveStatus === "waiting_user" && task.attention?.question ? (
          <form className={styles.attentionCard} onSubmit={(event) => {
            event.preventDefault();
            const answer = sharedAnswer.trim();
            if (!answer || answerSending) return;
            setAnswerSending(true);
            void onAnswerShared(task.id, answer).then((sent) => { if (sent) setSharedAnswer(""); }).finally(() => setAnswerSending(false));
          }}>
            <strong>{task.attention.question}</strong>
            <textarea
              value={sharedAnswer}
              onChange={(event) => setSharedAnswer(event.target.value)}
              maxLength={12000}
              rows={3}
              aria-label={en ? "Answer the research question" : "回答研究问题"}
              placeholder={en ? "Type your answer" : "输入回答"}
            />
            <div className={styles.attentionActions}>
              <button type="submit" disabled={!sharedAnswer.trim() || answerSending}>
                {answerSending ? (en ? "Continuing…" : "正在继续…") : (en ? "Answer and continue" : "回答并继续")}
              </button>
            </div>
          </form>
        ) : null}

        {(() => {
          const summaryText = `${result?.summary || ""}`;
          const synthesisIncomplete = /研究综合未完成|Research synthesis incomplete/i.test(summaryText)
            || (Array.isArray(result?.data_gaps) && result.data_gaps.some((gap) => /综合未完成|synthesis incomplete/i.test(String(gap))));
          const needsRecovery = liveStatus === "failed" || liveStatus === "unknown" || liveStatus === "partial" || liveStatus === "cancelled" || clarification || synthesisIncomplete;
          if (!needsRecovery) return null;
          const deepMode = (task.research_mode || "") === "deep";
          return (
          <div className={styles.attentionCard} role="status">
            <strong>
              {clarification
                ? clarification.question
                : synthesisIncomplete
                  ? (en ? "Deep research did not finish synthesizing" : "深度研究综合未完成")
                : liveStatus === "failed" || liveStatus === "unknown"
                  ? (en ? "Research needs another try" : "研究需要再试一次")
                  : liveStatus === "partial"
                    ? (en ? "Some data is still missing" : "还有一些数据缺失")
                    : (en ? "Research stopped" : "研究已停止")}
            </strong>
            <p>
              {clarification
                ? (en ? "Choose an option below, or add the missing detail in the composer." : "选择下面的选项，或在输入框补充缺失信息。")
                : synthesisIncomplete
                  ? (en
                    ? "Evidence was thin or synthesis stalled. Retry as Quick, retry Deep, or inspect gaps below."
                    : "证据不足或综合卡住了。可改用快速重试、再试深度，或查看下方缺口。")
                : liveStatus === "failed" || liveStatus === "unknown"
                  ? (task.error_message || (en ? "No source-backed answer was produced. You can retry or ask a narrower question." : "没有产出有来源的回答。可以重试，或缩小问题范围。"))
                  : liveStatus === "partial"
                    ? (en ? "The answer is based only on available evidence. Retry, or ask me to use only what was retrieved." : "当前只基于已有证据回答。可以重试，或让我只使用已获取的数据。")
                    : (en ? "The local run is stopped. External requests already sent may still finish." : "本地运行已停止；已经发出的外部请求可能仍会完成。")}
            </p>
            <div className={styles.attentionActions}>
              {clarification ? clarification.options.map((option) => (
                <button key={option} type="button" onClick={() => onFollowUp(`${task.query}\n\n${en ? "Selected:" : "用户选择："}${option}`)}>{option}</button>
              )) : null}
              {(liveStatus === "failed" || liveStatus === "unknown" || liveStatus === "partial" || synthesisIncomplete) ? (
                <button type="button" onClick={() => onFollowUp(task.query)}>{en ? "Retry" : "重试"}</button>
              ) : null}
              {(deepMode || synthesisIncomplete) ? (
                <button type="button" onClick={() => onRetryAsQuick ? onRetryAsQuick(task.query) : onFollowUp(`${task.query}\n\n${en ? "Please answer with Quick research mode." : "请改用快速研究模式回答。"}`)}>{en ? "Retry as Quick" : "改用快速重试"}</button>
              ) : null}
              {(liveStatus === "failed" || liveStatus === "unknown" || liveStatus === "partial" || synthesisIncomplete) ? (
                <button type="button" onClick={() => {
                  const gaps = Array.isArray(result?.data_gaps) ? result.data_gaps.filter(Boolean).slice(0, 8) : [];
                  const body = gaps.length
                    ? (en ? `Show these gaps clearly and suggest what to ask next:\n- ${gaps.join("\n- ")}` : `请清楚列出这些缺口，并建议下一步怎么问：\n- ${gaps.join("\n- ")}`)
                    : (en ? "List the evidence gaps from the last run and suggest a narrower follow-up." : "请列出上次运行的证据缺口，并建议更窄的追问。");
                  onFollowUp(`${task.query}\n\n${body}`);
                }}>{en ? "Review gaps" : "查看缺口"}</button>
              ) : null}
              {(liveStatus === "failed" || liveStatus === "unknown" || liveStatus === "partial") ? (
                <button type="button" onClick={() => onFollowUp(`${task.query}\n\n${en ? "First check my account/login state and network. If the page is unavailable, explain what you can verify and do not retry blindly." : "先检查我的账户/登录状态和网络；如果页面不可用，请说明你还能核验什么，不要盲目重试。"}`)}>{en ? "Check account/network" : "检查账户/网络"}</button>
              ) : null}
              {liveStatus === "partial" ? (
                <button type="button" onClick={() => onFollowUp(`${task.query}\n\n${en ? "Use only the sources already retrieved; do not invent missing data. State each gap explicitly." : "只使用已经获取的来源，不要编造缺失数据；逐项说明缺口。"}`)}>{en ? "Use available data" : "只基于已有数据"}</button>
              ) : null}
            </div>
          </div>
          );
        })()}

        {candidates.length ? (
          <div className={styles.attentionCard}>
            <p className={styles.candidateHeading}>{en ? "Viewpoints proposed by AI — pending your decision" : "AI 归纳的投资观点 · 需要你逐条决定"}</p>
            {candidates.map((candidate) => (
              <CandidateCard key={candidate.id} candidate={candidate} en={en} value={drafts[candidate.id] ?? candidate.proposed_text} onChange={(value) => onDraft(candidate.id, value)} onConfirm={() => onConfirm(candidate)} onDismiss={() => onDismiss(candidate)} />
            ))}
          </div>
        ) : null}

        {/* A dropped run did nothing, so its suggested next questions are the
            most useful thing left on screen. */}
        {result?.actions?.length && (liveStatus === "completed" || liveStatus === "dropped") ? (
          <div className={styles.attentionCard}>
            <strong>{en ? "What next?" : "接下来可以问"}</strong>
            <div className={styles.attentionActions}>
              {result.actions.slice(0, 3).map((action) => (
                <button key={action} type="button" onClick={() => onFollowUp(action)}>{action}</button>
              ))}
            </div>
          </div>
        ) : null}

        {result?.workflow ? <WorkflowPanel workflow={result.workflow} en={en} onFollowUp={onFollowUp} /> : null}
      </div>
    </article>
  );
}

/** Price trend bubble. The series comes from tool data, so the shape is real. */
function PriceChartBubble({ chart, en }: { chart: NonNullable<BotTask["result"]>["chart"]; en: boolean }) {
  const points = (chart?.series ?? []).filter((point) => typeof point.close === "number" && point.date);
  if (!chart || points.length < 2) return null;
  const width = 520;
  const height = 130;
  const pad = 8;
  const values = points.map((point) => point.close);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const step = (width - pad * 2) / (points.length - 1);
  const coords = points.map((point, index) => [
    pad + index * step,
    height - pad - ((point.close - min) / span) * (height - pad * 2),
  ] as const);
  const line = coords.map(([x, y], index) => `${index ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `${line} L${coords[coords.length - 1][0].toFixed(1)},${height - pad} L${coords[0][0].toFixed(1)},${height - pad} Z`;
  const first = values[0];
  const last = values[values.length - 1];
  const change = first ? ((last / first - 1) * 100) : 0;
  const isMultiple = chart.metric === "pe";
  const digits = isMultiple ? 2 : last >= 100 ? 2 : 3;
  return (
    <div className={`${styles.agentBubble} ${styles.chartBubble}`}>
      <header className={styles.chartHead}>
        <div>
          <strong>{chart.title || (en ? "Price trend" : "价格走势")}</strong>
          <span>{points[0].date} → {points[points.length - 1].date}</span>
        </div>
        <div className={styles.chartValue}>
          <b>{last.toFixed(digits)}{isMultiple ? "×" : ""}</b>
          <em data-direction={change >= 0 ? "up" : "down"}>
            {change >= 0 ? "+" : ""}{change.toFixed(2)}%
          </em>
        </div>
      </header>
      <svg
        className={styles.chartSvg}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={en ? "Price trend from retrieved data" : "来自已取数据的价格走势"}
        preserveAspectRatio="none"
      >
        <path d={area} className={styles.chartArea} />
        <path d={line} className={styles.chartLine} />
      </svg>
      <footer className={styles.chartFoot}>
        <span>
          {chart.note ? `${chart.note} · ` : ""}
          {en ? "Low" : "区间低"} {min.toFixed(digits)} · {en ? "High" : "区间高"} {max.toFixed(digits)}
          {chart.currency && !isMultiple ? ` ${chart.currency}` : ""}
        </span>
        <span>
          {chart.source ? `${chart.source}` : ""}
          {chart.data_as_of ? ` · ${chart.data_as_of}` : ""}
        </span>
      </footer>
    </div>
  );
}

function TraceSheet({ task, run, en, onClose }: {
  task: BotTask;
  run: RunState | null;
  en: boolean;
  onClose: () => void;
}) {
  const [mode, setMode] = useState<"trace" | "output">("trace");
  return (
    <div className={styles.traceBackdrop} role="presentation" onClick={onClose}>
      <aside className={styles.traceSheet} role="dialog" aria-modal="true" aria-labelledby="trace-title" onClick={(event) => event.stopPropagation()}>
        <header className={styles.traceHead}>
          <div><span>{en ? "RUN TRACE" : "运行过程"}</span><h2 id="trace-title">{task.query}</h2></div>
          <div className={styles.traceHeadRight}>
            <div className={styles.traceTabs} role="tablist" aria-label={en ? "Trace views" : "过程视图"}>
              <button type="button" role="tab" aria-selected={mode === "trace"} data-active={mode === "trace" ? "true" : "false"} onClick={() => setMode("trace")}>{en ? "Trace" : "过程"}</button>
              <button type="button" role="tab" aria-selected={mode === "output"} data-active={mode === "output" ? "true" : "false"} onClick={() => setMode("output")}>{en ? "Structured output" : "结构化输出"}</button>
            </div>
            <button type="button" className={styles.traceClose} onClick={onClose} aria-label={en ? "Close trace" : "关闭运行过程"}>×</button>
          </div>
        </header>
        <div className={styles.traceBody}>
          {mode === "trace" ? (
            <>
              {run ? <RunTimeline run={run} en={en} stopped={run.status === "cancelled"} /> : null}
              {!run && task.result?.research_steps?.length ? (
                <ol className={styles.traceSteps}>
                  {task.result.research_steps.map((step, index) => (
                    <li key={`${step.tool}-${index}`}><span>{step.tool}</span><small>{step.status}{step.endpoint ? ` · ${step.endpoint}` : ""}</small></li>
                  ))}
                </ol>
              ) : null}
              {!run && !task.result?.research_steps?.length ? <p className={styles.deskEmpty}>{en ? "No process detail is stored for this run." : "这次运行没有保存过程详情。"}</p> : null}
            </>
          ) : (
            <TraceDebug task={task} en={en} />
          )}
        </div>
      </aside>
    </div>
  );
}

function TraceDebug({ task, en }: { task: BotTask; en: boolean }) {
  const [copied, setCopied] = useState(false);
  const [plan, setPlan] = useState<TaskPlan | null>(null);
  useEffect(() => {
    let alive = true;
    void getTaskPlan(task.id)
      .then((data) => {
        if (alive) setPlan(data.plan);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [task.id]);
  const output = {
    task: {
      id: task.id,
      status: task.status,
      query: task.query,
      research_mode: task.research_mode,
      created_at: task.created_at,
      finished_at: task.finished_at,
      ui_context: task.ui_context ?? null,
    },
    response: {
      intent: task.result?.intent ?? null,
      summary: task.result?.summary ?? null,
      actions: task.result?.actions ?? [],
      ui_components: task.result?.ui_components ?? [],
      ui_blocks: task.result?.ui_blocks ?? [],
      data_gaps: task.result?.data_gaps ?? [],
      data_plan: task.result?.data_plan ?? null,
      meta: task.result?.meta ?? {},
    },
    plan,
    research_steps: task.result?.research_steps ?? [],
    evidence: (task.result?.evidence ?? []).map((item) => ({
      id: item.id,
      source: item.source,
      title: item.title ?? null,
      url: item.url ?? null,
      data_as_of: item.data_as_of ?? null,
      freshness: item.freshness ?? null,
    })),
  };
  const raw = JSON.stringify(output, null, 2);
  async function copy() {
    try {
      await navigator.clipboard.writeText(raw);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard is optional.
    }
  }
  return (
    <div className={styles.traceDebug}>
      <div className={styles.traceDebugBar}>
        <div><strong>{en ? "Structured output" : "结构化输出"}</strong><small>{en ? "Sanitized fields only; no private reasoning." : "仅展示脱敏字段；不含私有思考过程。"}</small></div>
        <button type="button" onClick={() => void copy()}>{copied ? (en ? "Copied" : "已复制") : en ? "Copy JSON" : "复制 JSON"}</button>
      </div>
      <pre>{raw}</pre>
    </div>
  );
}

function RunTimeline({ run, en, stopped = false }: { run: RunState; en: boolean; stopped?: boolean }) {
  const done = completedSteps(run);
  const active = stopped ? [] : activeSteps(run);
  if (!done.length && !active.length) return null;
  return (
    <details className={styles.timeline} open>
      <summary>
        <span>{en ? "Run details" : "运行过程"}</span>
        <small>
          {stopped
            ? en ? "Stopped" : "已停止"
            : en
              ? `${done.length} completed · ${active.length} in progress`
              : `已完成 ${done.length} 项 · 正在进行 ${active.length} 项`}
        </small>
      </summary>
      {done.length ? (
        <div className={styles.timelineGroup}>
          <p>{en ? "Completed" : "已完成"}</p>
          <ol>
            {done.map((step) => (
              <li key={step.stepId} data-status={step.status}>
                <span aria-hidden="true">✓</span>
                <span>{step.summary}</span>
                {step.endpoint ? <code className={styles.runEndpoint}>{step.endpoint}</code> : null}
              </li>
            ))}
          </ol>
        </div>
      ) : null}
      {active.length ? (
        <div className={styles.timelineGroup}>
          <p>{en ? "In progress" : "正在进行"}</p>
          <ol>
            {active.map((step) => (
              <li key={step.stepId} data-status={step.status}>
                <span className={styles.stepPulse} aria-hidden="true" />
                <span>{step.summary}</span>
                {step.endpoint ? <code className={styles.runEndpoint}>{step.endpoint}</code> : null}
              </li>
            ))}
          </ol>
        </div>
      ) : null}
    </details>
  );
}

function ToolPlans({ plans, en }: { plans: NonNullable<BotTask["result"]>["data_plans"]; en: boolean }) {
  if (!plans?.length) return null;
  return (
    <details className={styles.dataPlan}>
      <summary>{en ? "Tool data plans" : "工具数据计划"} <span>{plans.length}</span></summary>
      <ul>
        {plans.map((plan, index) => (
          <li key={`${plan.tool}-${index}`}>
            <strong>{plan.capability || plan.tool}</strong>
            {plan.provider ? ` · ${plan.provider}` : ""}
            {plan.endpoint ? ` · ${plan.endpoint}` : ""}
            {plan.status && plan.status !== "completed" ? ` · ${plan.status}` : ""}
          </li>
        ))}
      </ul>
    </details>
  );
}

function DataCards({ cards, en }: { cards: NonNullable<BotTask["result"]>["data_cards"]; en: boolean }) {
  const visible = (cards || []).filter((card) => card.kind !== "model_fill");
  if (!visible.length) return null;
  return (
    <div className={styles.dataCards}>
      {visible.map((card, index) => (
        <details key={`${card.kind}-${index}`} className={styles.dataCard}>
          <summary>
            {card.title || (en ? "Retrieved data" : "已获取数据")}
            <span>{card.provider || ""}{card.as_of ? ` · ${card.as_of}` : ""}</span>
          </summary>
          {card.kind === "financials" && card.rows?.length ? (
            <div className={styles.tableWrap}><table>
              <thead><tr><th>{en ? "Period" : "报告期"}</th>{Object.keys((card.rows[0]?.values as Record<string, unknown>) || {}).slice(0, 6).map((key) => <th key={key}>{key}</th>)}</tr></thead>
              <tbody>{card.rows.map((row, rowIndex) => {
                const values = (row.values as Record<string, unknown>) || {};
                return <tr key={rowIndex}><td>{String(row.period || "")}</td>{Object.keys(values).slice(0, 6).map((key) => <td key={key}>{values[key] == null ? "—" : String(values[key])}</td>)}</tr>;
              })}</tbody>
            </table></div>
          ) : null}
          {card.kind === "estimates" && card.targets ? (
            <ul className={styles.cardList}>{Object.entries(card.targets).map(([key, value]) => <li key={key}><strong>{key}</strong>: {String(value)}</li>)}</ul>
          ) : null}
          {card.kind === "estimates" && card.years?.length ? (
            <div className={styles.tableWrap}><table><thead><tr><th>{en ? "Period" : "年度"}</th><th>Mean</th><th>Min</th><th>Max</th><th>{en ? "Analysts" : "机构数"}</th></tr></thead><tbody>{card.years.map((row, rowIndex) => <tr key={rowIndex}><td>{String(row.period || "")}</td><td>{String(row.mean ?? "—")}</td><td>{String(row.min ?? "—")}</td><td>{String(row.max ?? "—")}</td><td>{String(row.count ?? "—")}</td></tr>)}</tbody></table></div>
          ) : null}
          {card.kind === "index" && card.rows?.length ? (
            <div className={styles.tableWrap}><table><thead><tr><th>{en ? "Code" : "代码"}</th><th>{en ? "Name" : "名称"}</th><th>{en ? "Weight" : "权重"} %</th></tr></thead><tbody>{card.rows.map((row, rowIndex) => <tr key={rowIndex}><td>{String(row.code || "")}</td><td>{String(row.name || "")}</td><td>{String(row.weight_percent ?? "—")}</td></tr>)}</tbody></table></div>
          ) : null}
          {card.kind === "valuations" && card.rows?.length ? (
            <div className={styles.tableWrap}><table><thead><tr><th>{en ? "Code" : "代码"}</th><th>{en ? "Name" : "名称"}</th><th>PE TTM</th><th>PE MRQ</th><th>PB</th><th>PS</th><th>PCF</th></tr></thead><tbody>{card.rows.map((row, rowIndex) => <tr key={rowIndex}><td>{String(row.code || "")}</td><td>{String(row.name || "")}</td><td>{String(row.pe_ttm ?? "—")}</td><td>{String(row.pe_mrq ?? "—")}</td><td>{String(row.pb_mrq ?? "—")}</td><td>{String(row.ps_ttm ?? "—")}</td><td>{String(row.pcf_ttm ?? "—")}</td></tr>)}</tbody></table></div>
          ) : null}
          {card.kind === "announcements" && card.rows?.length ? (
            <ul className={styles.cardList}>{card.rows.map((row, rowIndex) => <li key={rowIndex}><a href={String(row.url || "#")} target="_blank" rel="noreferrer">{String(row.title || "")}</a> <small>{String(row.date || "")}</small></li>)}</ul>
          ) : null}
          {card.kind === "model_fill" && card.rows?.length ? (
            <ul className={styles.cardList}>
              {card.rows.map((row, rowIndex) => (
                <li key={rowIndex}>
                  <strong>{String(row.field || row.period || "")}</strong>: {String(row.value ?? "—")}
                  {row.unit ? ` ${String(row.unit)}` : ""}
                  <small> · {en ? "unverified model fill" : "模型补齐·未核验"}{row.confidence ? ` · ${String(row.confidence)}` : ""}</small>
                </li>
              ))}
            </ul>
          ) : null}
        </details>
      ))}
    </div>
  );
}

function DataPlan({ plan, en }: { plan: NonNullable<BotTask["result"]>["data_plan"]; en: boolean }) {
  if (!plan) return null;
  return (
    <details className={styles.dataPlan}>
      <summary>
        {en ? "Data plan" : "数据计划"} <span>{plan.market || ""} · {plan.capability || ""}</span>
      </summary>
      <ul>
        <li><strong>{en ? "Primary" : "主源"}:</strong> {plan.primary || "—"}</li>
        <li><strong>{en ? "Used" : "实际使用"}:</strong> {plan.used_provider || "—"}</li>
        {plan.fallbacks?.length ? <li><strong>{en ? "Fallbacks" : "备源"}:</strong> {plan.fallbacks.join(" · ")}</li> : null}
        {plan.adjustment ? <li><strong>{en ? "Adjustment" : "复权"}:</strong> {plan.adjustment}</li> : null}
        {plan.policy ? <li><strong>{en ? "Policy" : "规则"}:</strong> {plan.policy}</li> : null}
      </ul>
    </details>
  );
}

function Sources({ evidence, en }: { evidence: SourceEvidence[]; en: boolean }) {
  return (
    <details className={styles.sources}>
      <summary>
        {en ? "Sources & coverage" : "来源与覆盖"} <span>{evidence.length}</span>
      </summary>
      <div className={styles.sourceList}>
        {evidence.map((source, index) => {
          const sourceName = source.source === "__unattributed__"
            ? (en ? "Source not identified" : "来源未注明")
            : source.source;
          const title = source.title ? `${source.title} · ${sourceName}` : sourceName;
          return (
          <article key={source.id ?? `${source.source}-${index}`}>
            <div>
              {source.url ? (
                <a href={source.url} target="_blank" rel="noreferrer">
                  {title} ↗
                </a>
              ) : (
                <strong>{title}</strong>
              )}
              <small>
                {en ? "Data date" : "数据日期"}: {source.data_as_of || (en ? "Unknown" : "未知")} ·{" "}
                {en ? "Retrieved" : "获取"}: {formatTime(source.retrieved_at, en ? "en" : "zh")} ·{" "}
                {source.freshness || (en ? "freshness unknown" : "时效未知")}
                {source.currency ? ` · ${source.currency}` : ""}
                {source.adjustment ? ` · ${en ? "adjustment" : "复权"}: ${source.adjustment}` : ""}
              </small>
            </div>
            {source.excerpts?.length ? (
              <details>
                <summary>{en ? "Excerpts" : "原文节选"}</summary>
                {source.excerpts.map((excerpt, excerptIndex) => (
                  <blockquote key={excerptIndex}>
                    <small>{excerpt.location || ""}</small>
                    <p>{excerpt.text}</p>
                  </blockquote>
                ))}
              </details>
            ) : null}
          </article>
          );
        })}
      </div>
    </details>
  );
}

function CandidateCard({
  candidate,
  en,
  value,
  onChange,
  onConfirm,
  onDismiss,
}: {
  candidate: MemoryCandidate;
  en: boolean;
  value: string;
  onChange: (value: string) => void;
  onConfirm: () => void;
  onDismiss: () => void;
}) {
  return (
    <div className={styles.candidate}>
      <p className={styles.candidateLabel}>
        {en ? "Candidate · not a user view" : "候选 · 不是你的观点"}
      </p>
      <textarea
        value={value}
        rows={3}
        maxLength={2000}
        onChange={(event) => onChange(event.target.value)}
        aria-label={en ? "Edit proposed viewpoint" : "编辑候选观点"}
      />
      {candidate.rationale ? <p className={styles.rationale}>{candidate.rationale}</p> : null}
      <div className={styles.candidateActions}>
        <button type="button" className={styles.confirmButton} onClick={onConfirm} disabled={!value.trim()}>
          {en ? "Confirm as my view" : "确认，作为我的观点"}
        </button>
        <button type="button" className={styles.quietButton} onClick={onDismiss}>
          {en ? "Ignore" : "忽略"}
        </button>
      </div>
    </div>
  );
}

function ProactivePanel({ monitors, notifications, deliveries, botId, en, routinesEnabled, notificationsError = null, deliveriesError = null, onCreate, onToggle, onDelete, onMute, onRead, onAnalyze, onRetry }: {
  monitors: BotMonitor[];
  notifications: BotNotification[];
  deliveries: BotDelivery[];
  botId: string | null;
  en: boolean;
  routinesEnabled: boolean;
  notificationsError?: string | null;
  deliveriesError?: string | null;
  onCreate: () => void;
  onToggle: (monitor: BotMonitor) => void;
  onDelete: (monitor: BotMonitor) => void;
  onMute: (monitor: BotMonitor) => void;
  onRead: (notification: BotNotification) => void;
  onAnalyze: (notification: BotNotification) => void;
  onRetry: (delivery: BotDelivery) => void;
}) {
  const [deliveryFilter, setDeliveryFilter] = useState<"all" | "sent" | "failed">("all");
  const unread = notifications.filter((item) => item.status !== "read").length;
  const groups = Array.from(notifications.reduce((map, item) => {
    const key = item.monitor_id || item.id;
    const existing = map.get(key);
    if (existing) {
      existing.count += 1;
      existing.unread += item.status !== "read" ? 1 : 0;
    } else {
      map.set(key, { latest: item, count: 1, unread: item.status !== "read" ? 1 : 0 });
    }
    return map;
  }, new Map<string, { latest: BotNotification; count: number; unread: number }>()).values());
  return (
    <section className={styles.proactivePanel} aria-labelledby="proactive-heading">
      <div className={styles.railHead}>
        <span className={styles.railKicker} id="proactive-heading">{en ? "Proactive" : "主动监控"}</span>
        <span className={styles.railSub}>{en ? "polled · not real-time" : "轮询 · 非实时"}</span>
        <button type="button" className={styles.railGhost} onClick={onCreate} disabled={!routinesEnabled} title={routinesEnabled ? undefined : (en ? "Routines are disabled in assistant settings" : "助手设置中已关闭例行任务")}>{en ? "+ Monitor" : "＋ 监控"}</button>
      </div>
      {!routinesEnabled ? <p className={styles.proactiveEmpty}>{en ? "Routines are off for this assistant. Re-enable them in assistant settings." : "这个助手已关闭例行任务，请在设置中开启。"}</p> : null}
      {notificationsError ? <p className={styles.proactiveEmpty} role="status">{notificationsError}</p> : null}
      {deliveriesError ? <p className={styles.proactiveEmpty} role="status">{deliveriesError}</p> : null}
      <div className={styles.monitorList}>
        {monitors.map((monitor) => (
          <div key={monitor.id} className={styles.monitorRow}>
            <div>
              <strong>{monitor.symbol}</strong>
              <span>{monitor.condition === "announcement" ? (en ? "CNINFO announcements" : "巨潮公告") : monitor.condition === "earnings" ? (en ? "Earnings date" : "财报日") : monitor.condition === "report" ? (en ? "Watchlist report" : "自选股报告") : `${monitor.direction === "both" ? "±" : monitor.direction === "down" ? "↓" : "↑"}${monitor.threshold}%`} · {monitor.status === "active" ? (en ? "active" : "运行中") : (en ? "paused" : "已暂停")}</span>
              {monitor.last_error ? <small>{monitor.last_error}</small> : null}
              {monitor.muted_until ? <small>{en ? "Muted until " : "静音至 "}{new Date(monitor.muted_until).toLocaleString()}</small> : null}
            </div>
            <div className={styles.monitorActions}>
              <button type="button" onClick={() => onMute(monitor)} title={monitor.muted_until ? (en ? "Unmute" : "取消静音") : (en ? "Mute 2h" : "静音 2 小时")}>{monitor.muted_until ? "🔔" : "🔕"}</button>
              <button type="button" onClick={() => onToggle(monitor)} title={monitor.status === "active" ? (en ? "Pause" : "暂停") : (en ? "Resume" : "恢复")}>{monitor.status === "active" ? "Ⅱ" : "▶"}</button>
              <button type="button" onClick={() => onDelete(monitor)} title={en ? "Delete" : "删除"}>×</button>
            </div>
          </div>
        ))}
        {!monitors.length ? <p className={styles.proactiveEmpty}>{en ? "No monitors yet. Add one when you want standing coverage." : "还没有监控。需要长期盯盘时再添加。"}</p> : null}
      </div>
      {notifications.length ? (
        <>
          <div className={`${styles.railHead} ${styles.railHeadSub}`}>
            <span className={styles.railKicker}>{en ? "Inbox" : "主动收件箱"}</span>
            {unread ? <span className={styles.railCount}>{unread}</span> : null}
          </div>
          <div className={styles.inboxList}>
            {groups.slice(0, 6).map((group) => (
              <div key={group.latest.id} className={`${styles.inboxItem} ${group.unread ? styles.inboxUnread : styles.inboxRead}`}>
                <button type="button" className={styles.inboxBody} onClick={() => notifications.filter((item) => (item.monitor_id || item.id) === (group.latest.monitor_id || group.latest.id)).forEach(onRead)}>
                  <strong>{group.latest.title} {group.count > 1 ? <em className={styles.digestCount}>{group.count}</em> : null}</strong>
                  <span>{group.latest.body}</span>
                  <small>{new Date(group.latest.created_at).toLocaleString()}{group.count > 1 ? (en ? " · grouped alerts" : " · 已合并提醒") : ""}</small>
                </button>
                <div className={styles.inboxActions}>
                  <button type="button" onClick={() => onAnalyze(group.latest)}>{en ? "Analyze" : "分析"}</button>
                </div>
              </div>
            ))}
          </div>
        </>
      ) : null}
      {deliveries.length ? (
        <>
          <div className={`${styles.railHead} ${styles.railHeadSub}`}>
            <span className={styles.railKicker}>{en ? "Delivery log" : "发送记录"}</span>
            <div className={styles.deliveryTools}>
              <select value={deliveryFilter} onChange={(event) => setDeliveryFilter(event.target.value as "all" | "sent" | "failed")} aria-label={en ? "Filter deliveries" : "筛选发送记录"}>
                <option value="all">{en ? "All" : "全部"}</option>
                <option value="sent">{en ? "Sent" : "成功"}</option>
                <option value="failed">{en ? "Failed" : "失败"}</option>
              </select>
              <a
                href={`/api/deliveries/export?${new URLSearchParams({ ...(botId ? { bot_id: botId } : {}), ...(deliveryFilter !== "all" ? { status: deliveryFilter } : {}) }).toString()}`}
                download="delivery-log.csv"
                className={styles.deliveryExport}
              >{en ? "CSV" : "导出"}</a>
            </div>
          </div>
          <div className={styles.deliveryList}>
            {deliveries.filter((item) => deliveryFilter === "all" || item.status === deliveryFilter).slice(0, 5).map((delivery) => (
              <div key={delivery.id} className={styles.deliveryRow}>
                <span className={delivery.status === "sent" ? styles.deliverySent : styles.deliveryFailed}>
                  {delivery.channel} · {delivery.status} · ×{delivery.attempts}
                </span>
                {delivery.status !== "sent" ? <button type="button" onClick={() => onRetry(delivery)}>{en ? "Retry" : "重试"}</button> : null}
              </div>
            ))}
          </div>
        </>
      ) : null}
    </section>
  );
}

function InlineApprovalCard({ en, query, botName, preflight, onCancel, onConfirm }: {
  en: boolean;
  query: string;
  botName: string;
  preflight: BotPreflight;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <article className={styles.inlineDecision} role="region" aria-label={en ? "Model call approval" : "模型调用确认"}>
      <div className={styles.inlineDecisionHead}>
        <BotAvatar name={botName} state="waiting" size="small" />
        <strong>{en ? "Confirm model call" : "确认调用模型"}</strong>
        <span className={styles.decisionStatus}><i aria-hidden="true" />{en ? "Ready" : "待确认"}</span>
      </div>
      <div className={styles.inlineDecisionFields}>
        <div className={styles.inlineDecisionRequest}>
          <span>{en ? "Request" : "研究请求"}</span>
          <p>{query}</p>
        </div>
        <div><span>{en ? "Model" : "模型"}</span><strong>{preflight.model || "—"}</strong></div>
        <div><span>{en ? "Estimated credits" : "预计额度"}</span><strong>{preflight.estimated_credits} / {preflight.threshold}</strong></div>
      </div>
      <p className={styles.inlineDecisionHint}>{en ? "The model is not called until you confirm. The estimate covers this run, including internal retries." : "确认前不会调用模型；预算覆盖本轮研究，包含内部重试。"}</p>
      <div className={styles.inlineDecisionActions}>
        <button type="button" className={styles.confirmButton} onClick={onConfirm}>{en ? "Allow this call" : "允许本次调用"}</button>
        <button type="button" className={styles.quietButton} onClick={onCancel}>{en ? "Cancel" : "取消"}</button>
      </div>
    </article>
  );
}

function ReasoningRail({ task, run, en, onOpenTrace, onResume }: {
  task: BotTask | null;
  run: RunState | null;
  en: boolean;
  onOpenTrace: () => void;
  onResume: () => void;
}) {
  const live = Boolean(run && !isTerminal(run.status));
  const steps = run?.steps?.length ? run.steps : (task?.result?.research_steps || []).map((step, index) => ({
    stepId: `${step.tool}-${index}`,
    kind: "step",
    status: step.status,
    summary: step.tool,
    sequence: index,
  }));
  const current = run?.streamText || "";
  if (!task && !live) return null;
  return (
    <section className={styles.reasoningRail} aria-label={en ? "Working process" : "工作过程"}>
      <div className={styles.railHead}>
        <span className={styles.railKicker}>{en ? "Working process" : "工作过程"}</span>
        <span className={styles.railCount}>
          {live ? (en ? "Thinking" : "思考中") : task ? (en ? "Last run" : "最近运行") : (en ? "Idle" : "待命")}
        </span>
        {live ? <i className={styles.reasoningPulse} aria-hidden="true" /> : null}
      </div>
      {task ? (
        <>
          <p className={styles.reasoningTask}>{task.query.slice(0, 140)}</p>
          {live && current ? (
            <div className={styles.reasoningStream}>
              <ResearchMarkdown content={current.slice(-1800)} />
              <span className={styles.streamingCursor} aria-hidden="true" />
            </div>
          ) : null}
          {steps.length ? (
            <ol className={styles.reasoningSteps}>
              {steps.slice(-6).map((step) => (
                <li key={`${step.stepId}-${step.sequence}`} data-status={step.status}>
                  <span aria-hidden="true">{step.status === "completed" ? "✓" : step.status === "running" ? "·" : "○"}</span>
                  <span>{step.summary || step.stepId}</span>
                </li>
              ))}
            </ol>
          ) : null}
          {run || task.result?.research_steps?.length ? (
            <details className={styles.reasoningDetails}>
              <summary>{en ? "Tools and plan" : "工具与计划"}</summary>
              <TaskPlanCard task={task} run={run} en={en} onResume={onResume} />
              <ToolRunCards run={run} task={task} en={en} onOpenTrace={onOpenTrace} />
            </details>
          ) : null}
          {task.result?.evidence?.length || task.result?.data_gaps?.length ? (
            <details className={styles.reasoningDetails}>
              <summary>{en ? "Evidence and gaps" : "证据与缺口"}</summary>
              {task.result?.evidence?.length ? <Sources evidence={task.result.evidence} en={en} /> : null}
              {task.result?.data_gaps?.length ? (
                <details className={styles.gaps}>
                  <summary>{en ? "Data gaps" : "数据缺口"} <span>{task.result.data_gaps.length}</span></summary>
                  <ul>{task.result.data_gaps.map((gap, index) => <li key={index}>{gap}</li>)}</ul>
                </details>
              ) : null}
            </details>
          ) : null}
        </>
      ) : null}
    </section>
  );
}

function BotDesk({ en, botName, examples, successPaths = [], monitors, notifications, artifacts, onStart, onCreateMonitor, onAnalyze, onOpenArtifact, showIntro = true, showGrid = true, productMode = "use" }: {
  en: boolean;
  botName: string;
  examples: string[];
  successPaths?: Array<{ id: string; label: string; blurb: string; prompt: string; kind?: "ask" | "track" }>;
  monitors: BotMonitor[];
  notifications: BotNotification[];
  artifacts: BotArtifact[];
  onStart: (prompt: string, meta?: { kind?: "ask" | "track"; pathId?: string }) => void;
  onCreateMonitor: () => void;
  onAnalyze: (notification: BotNotification) => void;
  onOpenArtifact: (artifact: BotArtifact) => void;
  showIntro?: boolean;
  showGrid?: boolean;
  productMode?: "use" | "build";
}) {
  const paths = successPaths.length ? successPaths : null;
  return (
    <section className={styles.botDesk} aria-label={en ? "Assistant desk" : "助手桌面"} data-product-mode={productMode}>
      {showIntro ? <div className={styles.deskIntro}>
        <h2>{en ? "Hand me a research task" : "交给我一件研究"}</h2>
        <p>{en
          ? (productMode === "use"
            ? "One ask, or a lasting watch — I'll bring sources and gaps back."
            : "Start a thread, or review what this assistant has been watching.")
          : (productMode === "use"
            ? "问一次，或让我长期盯一件事；来源和缺口会写清楚。"
            : "开一段对话，或先看看我一直在替你盯什么。")}</p>
        {paths ? (
          <div className={`${styles.successPathGrid} ${styles.delegatePaths}`} role="group" aria-label={en ? "Delegate a task" : "委派一件事"}>
            {paths.map((path) => (
              <button
                key={path.id}
                type="button"
                className={styles.successPathCard}
                data-path={path.id}
                onClick={() => onStart(path.prompt, { kind: path.kind || (path.id === "track" ? "track" : "ask"), pathId: path.id })}
              >
                <strong>{path.label}</strong>
                <span>{path.blurb}</span>
              </button>
            ))}
          </div>
        ) : (
          <div className={styles.examples}>
            {examples.map((prompt) => <button key={prompt} type="button" onClick={() => onStart(prompt)}>{prompt}</button>)}
          </div>
        )}
      </div> : null}
      {showGrid ? <div className={styles.deskGrid}>
        <section className={styles.deskPanel}>
          <div className={styles.deskPanelHead}>
            <span>{en ? "Routines" : "例行任务"}</span>
            <button type="button" onClick={onCreateMonitor}>{en ? "New" : "新建"}</button>
          </div>
          {monitors.length ? monitors.slice(0, 4).map((monitor) => (
            <div key={monitor.id} className={styles.deskRoutine}>
              <span className={styles.routineDot} data-active={monitor.status === "active" ? "true" : "false"} />
              <div><strong>{monitor.name || monitor.symbol}</strong><small>{monitor.condition === "announcement" ? (en ? "CNINFO announcements" : "巨潮公告") : monitor.condition === "earnings" ? (en ? "Earnings date" : "财报日") : monitor.condition === "report" ? (en ? "Watchlist report" : "自选股报告") : `${monitor.symbol} · ±${monitor.threshold}%`}</small></div>
              <span>{monitor.status === "active" ? (en ? "Active" : "运行中") : (en ? "Paused" : "暂停")}</span>
            </div>
          )) : <p className={styles.deskEmpty}>{en ? "No routines yet. Give this assistant a standing responsibility." : "还没有例行任务。给这个助手一个长期职责。"}</p>}
        </section>
        <section className={styles.deskPanel}>
          <div className={styles.deskPanelHead}>
            <span>{en ? "Recent runs" : "最近运行"}</span>
            <span>{notifications.length || 0}</span>
          </div>
          {notifications.length ? notifications.slice(0, 3).map((notification) => (
            <button key={notification.id} type="button" className={styles.deskRun} onClick={() => onAnalyze(notification)}>
              <strong>{notification.title}</strong>
              <span>{plainSummary(notification.body)}</span>
              <small>{new Date(notification.created_at).toLocaleString()}</small>
            </button>
          )) : <p className={styles.deskEmpty}>{en ? "No routine runs yet." : "还没有例行任务运行。"}</p>}
        </section>
        <section className={styles.deskPanel} style={{ gridColumn: "1 / -1" }}>
          <div className={styles.deskPanelHead}>
            <span>{en ? "Recent outputs" : "最近产出"}</span>
            <span>{artifacts.length}</span>
          </div>
          {artifacts.length ? artifacts.slice(0, 3).map((artifact) => (
            <button key={artifact.id} type="button" className={styles.deskArtifact} onClick={() => onOpenArtifact(artifact)}>
              <strong>{artifact.title}</strong>
              <span>{plainSummary(artifact.summary)}</span>
              <small>{artifact.evidence_count} {en ? "sources" : "来源"} · {artifact.data_gap_count} {en ? "gaps" : "缺口"}</small>
            </button>
          )) : <p className={styles.deskEmpty}>{en ? "No saved research outputs yet." : "还没有已保存的研究产出。"}</p>}
        </section>
      </div> : null}
    </section>
  );
}

function InlineAssistTimeline({ en, botName, watching, unread, monitors, onOpenFocus, onOpenAlert, onCreateFocus, onOpenWorkspace }: {
  en: boolean;
  botName: string;
  watching: FocusPoint[];
  unread: WatchingAlert[];
  monitors: BotMonitor[];
  onOpenFocus: (point: FocusPoint) => void;
  onOpenAlert: (alert: WatchingAlert) => void;
  onCreateFocus: () => void;
  onOpenWorkspace: () => void;
}) {
  if (!WATCH_ENABLED) return null;
  const unreadCount = unread.length;
  // Watching list lives in the left roster; chat only surfaces hot unread + empty-state nudge.
  if (!unreadCount && watching.length) return null;
  return (
    <div className={styles.inlineTimeline} data-use-watching="true" aria-label={en ? "Inline assists" : "对话内协助"}>
      {unread.slice(0, 2).map((alert) => (
        <article key={alert.id} className={styles.inlineActionCard} data-watching-alert={alert.id} data-alert-kind={alert.kind}>
          <header>
            <strong>{en ? "Unread ping" : "未读提醒"}</strong>
            <span className={styles.inlineCardBadge} data-tone="hot"><i />{en ? "Needs a look" : "待你看一眼"}</span>
          </header>
          <div className={styles.inlineCardBody}>
            <p className={styles.inlineCardTitle}>{alert.title}</p>
            <p className={styles.inlineCardCopy}>
              {alert.focusQuestion
                ? (en ? `About: ${alert.focusQuestion}` : `关于：${alert.focusQuestion}`)
                : plainSummary(alert.body)}
            </p>
          </div>
          <footer>
            <button type="button" className={styles.inlineCardPrimary} onClick={() => onOpenAlert(alert)}>{en ? "Open" : "打开看看"}</button>
            <button type="button" className={styles.inlineCardSecondary} onClick={onOpenWorkspace}>{en ? "Screen" : "看屏幕"}</button>
          </footer>
        </article>
      ))}

      {!watching.length && !unread.length ? (
        <article className={styles.inlineActionCard} data-kind="suggest">
          <header>
            <strong>{en ? "Start a watch" : "建一条盯梢"}</strong>
            <span className={styles.inlineCardBadge}><i />{botName}</span>
          </header>
          <div className={styles.inlineCardBody}>
            <p className={styles.inlineCardTitle}>{en ? "Hand me a lasting research question" : "丢给我一个长期研究问题"}</p>
            <p className={styles.inlineCardCopy}>
              {en
                ? "It lands in the left roster. When filings or moves match, I bring an unread ping into this chat."
                : "会出现在左侧花名册。公告或异动对上时，我会把未读提醒捎进对话。"}
            </p>
          </div>
          <footer>
            <button type="button" className={styles.inlineCardPrimary} onClick={onCreateFocus}>{en ? "Add a watch" : "加一条盯梢"}</button>
            <button type="button" className={styles.inlineCardSecondary} onClick={onOpenWorkspace}>
              {en ? "Open screen" : "看屏幕与例行"}
            </button>
          </footer>
        </article>
      ) : null}
    </div>
  );
}

/** @deprecated visual shell moved to InlineAssistTimeline; keep export surface for older references */
function UseWatchingPanel(props: {
  en: boolean;
  watching: FocusPoint[];
  unread: WatchingAlert[];
  onOpenFocus: (point: FocusPoint) => void;
  onOpenAlert: (alert: WatchingAlert) => void;
  onCreateFocus: () => void;
}) {
  return (
    <InlineAssistTimeline
      en={props.en}
      botName={enLabel(props.en)}
      watching={props.watching}
      unread={props.unread}
      monitors={[]}
      onOpenFocus={props.onOpenFocus}
      onOpenAlert={props.onOpenAlert}
      onCreateFocus={props.onCreateFocus}
      onOpenWorkspace={() => undefined}
    />
  );
}

function enLabel(en: boolean) {
  return en ? "Research Bot" : "研究助手";
}

function UseAlertsStrip({ alerts, en, onOpen }: {
  alerts: WatchingAlert[];
  en: boolean;
  onOpen: (alert: WatchingAlert) => void;
}) {
  if (!alerts.length) return null;
  return (
    <section className={styles.useAlertsStrip} aria-label={en ? "Unread alerts" : "未读提醒"}>
      <div className={styles.useAlertsStripHead}>
        <strong>{en ? "Unread alerts" : "未读提醒"}</strong>
        <small>{alerts.length}</small>
      </div>
      {alerts.map((alert) => (
        <button key={alert.id} type="button" className={styles.useAlertsStripItem} data-watching-alert={alert.id} data-alert-kind={alert.kind} onClick={() => onOpen(alert)}>
          <strong>{alert.title}</strong>
          <span>
            {alert.focusQuestion
              ? (en ? `Focus: ${alert.focusQuestion}` : `关注点：${alert.focusQuestion}`)
              : plainSummary(alert.body)}
          </span>
        </button>
      ))}
    </section>
  );
}

function plainSummary(value: string): string {
  return value
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[#*_`>]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function ArtifactsPanel({ artifacts, en, onOpen }: {
  artifacts: BotArtifact[];
  en: boolean;
  onOpen: (artifact: BotArtifact) => void;
}) {
  return (
    <section className={styles.artifactsPanel} aria-label={en ? "Outputs" : "产出"}>
      <div className={styles.railHead}>
        <span className={styles.railKicker}>{en ? "Saved outputs" : "已保存产出"}</span>
        <span className={styles.railCount}>{artifacts.length}</span>
      </div>
      <div className={styles.artifactList}>
        {artifacts.map((artifact) => (
          <button key={artifact.id} type="button" className={styles.artifactRow} onClick={() => onOpen(artifact)}>
            <strong>{artifact.title}</strong>
            <span>{plainSummary(artifact.summary)}</span>
            <small>{new Date(artifact.created_at).toLocaleDateString()} · {artifact.evidence_count} {en ? "sources" : "来源"}</small>
          </button>
        ))}
        {!artifacts.length ? <p className={styles.proactiveEmpty}>{en ? "Completed research will appear here." : "完成的研究会沉淀在这里。"}</p> : null}
      </div>
    </section>
  );
}

function RoutineRuns({ notifications, en, onAnalyze }: {
  notifications: BotNotification[];
  en: boolean;
  onAnalyze: (notification: BotNotification) => void;
}) {
  const [expanded, setExpanded] = useState<string[]>([]);
  const [reports, setReports] = useState<Record<string, BotTask | null>>({});
  const [loading, setLoading] = useState<string | null>(null);
  const toggle = async (notification: BotNotification) => {
    const id = notification.id;
    if (expanded.includes(id)) {
      setExpanded((current) => current.filter((item) => item !== id));
      return;
    }
    setExpanded((current) => [...current, id]);
    if (notification.task_id && reports[id] === undefined) {
      setLoading(id);
      try {
        const { task } = await getTask(notification.task_id);
        setReports((current) => ({ ...current, [id]: task }));
      } catch {
        setReports((current) => ({ ...current, [id]: null }));
      } finally {
        setLoading(null);
      }
    }
  };
  return (
    <section className={styles.routineRuns} aria-label={en ? "Routine runs" : "Routine 运行记录"}>
      <div className={styles.routineRunsHead}>
        <span>{en ? "Routine runs" : "ROUTINE 运行"}</span>
        <small>{notifications.length}</small>
      </div>
      {notifications.map((notification) => {
        const isOpen = expanded.includes(notification.id);
        return (
          <article key={notification.id} className={styles.routineRun}>
            <div className={styles.routineRunRail} data-severity={notification.severity} />
            <div className={styles.routineRunBody}>
              <div className={styles.routineRunTop}>
                <strong>{notification.title}</strong>
                <time>{new Date(notification.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time>
              </div>
              <p className={isOpen ? styles.routineRunTextOpen : undefined}>{notification.body}</p>
              {isOpen && notification.task_id ? (
                <div className={styles.routineReport}>
                  {loading === notification.id ? <span>{en ? "Loading research…" : "正在读取研报…"}</span> : null}
                  {reports[notification.id]?.result?.summary ? <ResearchMarkdown content={reports[notification.id]!.result!.summary!} /> : null}
                  {!loading && reports[notification.id] && !reports[notification.id]?.result?.summary ? <span>{en ? "No report body is available for this run." : "这次运行还没有可展示的研报正文。"}</span> : null}
                </div>
              ) : null}
              <div className={styles.routineRunActions}>
                <span>{notification.status === "unread" ? (en ? "New routine run" : "新的 Routine 运行") : (en ? "Routine run" : "Routine 运行")}</span>
                <div>
                  <button type="button" className={styles.routineExpand} onClick={() => void toggle(notification)}>{isOpen ? (en ? "Collapse" : "收起") : (en ? "Details" : "详情")}</button>
                  <button type="button" onClick={() => onAnalyze(notification)}>{notification.task_id ? (en ? "Open research" : "查看研究") : (en ? "Research this run" : "分析这次运行")}</button>
                </div>
              </div>
            </div>
          </article>
        );
      })}
    </section>
  );
}

function statusText(state: BotState, en: boolean): string {
  return botCopy(state, en ? "en" : "zh").detail;
}

function BotAvatar({ name, state, size = "small", muted = false, title }: { name: string; state: BotState; size?: "small" | "large"; muted?: boolean; title?: string }) {
  return <GrokAvatar name={name} state={state} size={size} muted={muted} title={title} />;
}

function BotSwitcher({ bots, loading = false, activeBotId, activeState, en, onClose, onSelect, onCreate, onEdit }: {
  bots: BotProfile[];
  loading?: boolean;
  activeBotId: string | null;
  activeState: BotState;
  en: boolean;
  onClose: () => void;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onEdit: () => void;
}) {
  const [query, setQuery] = useState("");
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return bots;
    return bots.filter((bot) => `${bot.name} ${bot.description || ""}`.toLowerCase().includes(needle));
  }, [bots, query]);
  return (
    <div className={styles.botSwitcherBackdrop} role="presentation" onClick={onClose}>
      <section className={styles.botSwitcher} role="dialog" aria-modal="true" aria-labelledby="bot-switcher-title" onClick={(event) => event.stopPropagation()}>
        <header className={styles.botSwitcherHead}>
          <div><span>{en ? "Assistants" : "我的助手"}</span><h2 id="bot-switcher-title">{en ? "Switch assistant" : "切换助手"}</h2></div>
          <button type="button" onClick={onClose} aria-label={en ? "Close" : "关闭"}>×</button>
        </header>
        <label className={styles.botSwitcherSearch}>
          <svg className={styles.botSwitcherSearchIcon} viewBox="0 0 20 20" aria-hidden="true">
            <circle cx="8.5" cy="8.5" r="5.2" fill="none" stroke="currentColor" strokeWidth="1.6" />
            <path d="M12.4 12.4L17 17" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          </svg>
          <input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder={en ? "Search assistants…" : "搜索助手…"} aria-label={en ? "Search assistants" : "搜索助手"} />
          <kbd>⌘K</kbd>
        </label>
        <div className={styles.botSwitcherGrid}>
          {loading && !bots.length ? (
            <div className={styles.switcherSkeleton} aria-busy="true" aria-label={en ? "Loading bots" : "正在加载助手"}>
              {[0, 1, 2].map((index) => (
                <div key={index} className={styles.switcherSkeletonCard}>
                  <span className={styles.skeletonAvatar} />
                  <span className={styles.skeletonLines}><i /><i /></span>
                </div>
              ))}
            </div>
          ) : null}
          {!loading || bots.length ? filtered.map((bot) => {
            const active = bot.id === activeBotId;
            return (
              <button key={bot.id} type="button" className={styles.botSwitcherCard} data-active={active ? "true" : "false"} onClick={() => onSelect(bot.id)}>
                <BotAvatar name={bot.name} state={active ? activeState : "idle"} size="large" />
                <span className={styles.botSwitcherCopy}>
                  <strong>{bot.name}</strong>
                  <small>{bot.description || (en ? "Persistent financial research desk" : "持续在线的金融研究助手")}</small>
                  <em>{bot.session_count ?? 0} {en ? "chats" : "个对话"} · {bot.is_default ? (en ? "Default" : "默认") : (en ? "Custom" : "自定义")}</em>
                </span>
                <span className={styles.botSwitcherMark} aria-hidden="true">{active ? "✓" : "↗"}</span>
              </button>
            );
          }) : null}
          {!loading && !filtered.length ? <p className={styles.botSwitcherEmpty}>{en ? "No assistant matches this search." : "没有匹配的助手。"}</p> : null}
        </div>
        <footer className={styles.botSwitcherFoot}>
          <button type="button" onClick={onCreate}>＋ {en ? "New assistant" : "新建助手"}</button>
          <button type="button" onClick={onEdit} disabled={!activeBotId}>{en ? "Edit current assistant" : "编辑当前助手"}</button>
          <span>{en ? "⌘K to switch" : "⌘K 快速切换"}</span>
        </footer>
      </section>
    </div>
  );
}

function SessionSidebar({ bots, botsLoading = false, sessionsLoading = false, activeBotId, activeState, theme, onCycleTheme, onSelectBot, onCreateBot, onEditBot, sessions, activeId, onSelect, onDeleteSession, onPatchThread, onRenameThread, onMoveFolder, onClearHistory, onDeleteBot, en, productMode = "use", onProductMode, watching = [], unread = [], onOpenFocus, onOpenAlert, onCreateFocus }: {
  bots: BotProfile[];
  botsLoading?: boolean;
  sessionsLoading?: boolean;
  activeBotId: string | null;
  activeState: BotState;
  theme: "system" | "light" | "dark";
  onCycleTheme: () => void;
  onSelectBot: (id: string) => void;
  onCreateBot: () => void;
  onEditBot: () => void;
  sessions: BotSessionSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onDeleteSession: (id: string) => void;
  onPatchThread: (id: string, patch: { pinned?: boolean; archived?: boolean; folder?: string | null; approval_mode?: "ask" | "auto"; research_mode?: "quick" | "deep" }) => void;
  onRenameThread: (id: string, title: string) => void;
  onMoveFolder: (id: string, currentFolder: string) => void;
  onClearHistory: () => void;
  onDeleteBot: () => void;
  en: boolean;
  productMode?: "use" | "build";
  onProductMode?: (mode: "use" | "build") => void;
  watching?: FocusPoint[];
  unread?: WatchingAlert[];
  onOpenFocus?: (point: FocusPoint) => void;
  onOpenAlert?: (alert: WatchingAlert) => void;
  onCreateFocus?: () => void;
}) {
  const [query, setQuery] = useState("");
  // Conversations start open: hiding yesterday's thread behind a second click
  // was the single most common "where is my chat" question.
  const [chatHistoryOpen, setChatHistoryOpen] = useState(true);
  const [threadMenu, setThreadMenu] = useState<string | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const needle = query.trim().toLowerCase();
  const matches = (value: string | null | undefined) => !needle || (value || "").toLowerCase().includes(needle);
  const filteredBots = bots.filter((bot) => matches(`${bot.name} ${bot.description || ""} ${bot.last_query || ""}`));
  const filteredSessions = sessions
    .filter((item) => matches(item.title))
    .filter((item) => showArchived ? item.archived : !item.archived)
    .sort((a, b) => Number(Boolean(b.pinned)) - Number(Boolean(a.pinned)));
  const activeBot = bots.find((item) => item.id === activeBotId);
  return (
    <aside className={styles.rosterRail} aria-label={en ? "Assistants" : "我的助手"}>
      <div className={styles.rosterBrand}>
        <span className={styles.windowDots} aria-hidden="true"><i /><i /><i /></span>
        <span className={styles.rosterBrandMark}>✦</span>
        <span>{en ? "Roster" : "花名册"}</span>
        {productMode === "build" ? (
          <button type="button" onClick={onCreateBot} aria-label={en ? "New assistant" : "新建助手"}>＋</button>
        ) : null}
      </div>
      <label className={styles.rosterSearch}>
        <svg viewBox="0 0 20 20" aria-hidden="true">
          <circle cx="8.5" cy="8.5" r="5.2" fill="none" stroke="currentColor" strokeWidth="1.6" />
          <path d="M12.4 12.4L17 17" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
        <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={en ? "Search assistants & chats" : "搜索助手或对话"} aria-label={en ? "Search assistants and chats" : "搜索助手和对话"} />
      </label>
      <nav className={styles.botRoster} aria-label={en ? "Assistant roster" : "助手列表"}>
        {filteredBots.map((bot) => {
          const isActive = bot.id === activeBotId;
          const showChats = isActive && (chatHistoryOpen || Boolean(needle && filteredSessions.length));
          return (
            <div key={bot.id} className={styles.botRosterGroup}>
              <button
                type="button"
                className={isActive ? styles.botRosterActive : undefined}
                data-open={showChats ? "true" : "false"}
                onClick={() => {
                  if (isActive) setChatHistoryOpen((current) => !current);
                  else { setChatHistoryOpen(false); onSelectBot(bot.id); }
                }}
                title={isActive ? (en ? "Toggle chats" : "展开或收起对话") : (bot.description || bot.name)}
              >
                <BotAvatar name={bot.name} state={isActive ? activeState : "idle"} size="small" muted={!isActive} />
                <span className={styles.botRosterText}>
                  <strong>{bot.name}</strong>
                  <small>{bot.last_query || bot.description || (isActive ? (en ? "Here with you" : "正在与你一起") : (en ? "Idle" : "待命"))}</small>
                </span>
                {bot.last_active_at ? (
                  <span className={styles.botRosterTime}>{formatTime(bot.last_active_at, en ? "en" : "zh")}</span>
                ) : null}
                <span className={styles.botRosterChevron} data-open={showChats ? "true" : "false"} aria-hidden="true">⌄</span>
              </button>
              {showChats ? (
                <div className={styles.rosterChats} aria-label={en ? "Chats with this assistant" : "这个助手的对话"}>
                  {filteredSessions.map((item) => (
                    <div key={item.id} className={styles.rosterChatRow}>
                      <button type="button" className={item.id === activeId ? styles.rosterChatActive : undefined} onClick={() => { setThreadMenu(null); onSelect(item.id); }} title={item.title}>
                        <span>
                          {item.pinned ? <em className={styles.rosterPin} aria-label={en ? "Pinned" : "已置顶"}>📌</em> : null}
                          {item.unread ? <em className={styles.rosterUnread} aria-label={en ? "Unread" : "未读"} /> : null}
                          {item.title || (en ? "Untitled" : "未命名对话")}
                        </span>
                        {item.kind === "side" ? <small>{en ? "side" : "独立"}</small> : item.task_count ? <small>{item.task_count}</small> : null}
                      </button>
                      <button
                        type="button"
                        className={styles.rosterChatMore}
                        onClick={() => setThreadMenu((current) => current === item.id ? null : item.id)}
                        aria-label={en ? "Chat actions" : "对话操作"}
                        title={en ? "Chat actions" : "对话操作"}
                        aria-expanded={threadMenu === item.id}
                      >⋯</button>
                      {threadMenu === item.id ? (
                        <div className={styles.rosterChatMenu} role="menu">
                          <button type="button" onClick={() => { onPatchThread(item.id, { pinned: !item.pinned }); setThreadMenu(null); }}>
                            {item.pinned ? (en ? "Unpin" : "取消置顶") : (en ? "Pin" : "置顶")}
                          </button>
                          <button type="button" onClick={() => { onRenameThread(item.id, item.title); setThreadMenu(null); }}>
                            {en ? "Rename" : "重命名"}
                          </button>
                          <button type="button" onClick={() => { onPatchThread(item.id, { approval_mode: item.approval_mode === "auto" ? "ask" : "auto" }); setThreadMenu(null); }}>
                            {en ? `Approval: ${item.approval_mode === "auto" ? "Auto" : "Ask"}` : `审批：${item.approval_mode === "auto" ? "自动" : "确认"}`}
                          </button>
                          <button type="button" onClick={() => { onPatchThread(item.id, { research_mode: item.research_mode === "deep" ? "quick" : "deep" }); setThreadMenu(null); }}>
                            {en ? `Research: ${item.research_mode === "deep" ? "Deep" : "Quick"}` : `研究：${item.research_mode === "deep" ? "深度" : "快速"}`}
                          </button>
                          <button type="button" onClick={() => {
                            setThreadMenu(null);
                            onMoveFolder(item.id, item.folder || "");
                          }}>
                            {en ? "Move to folder" : "移动到文件夹"}
                          </button>
                          <button type="button" onClick={() => { onPatchThread(item.id, { archived: true }); setThreadMenu(null); }}>
                            {en ? "Archive" : "归档"}
                          </button>
                          <button type="button" className={styles.rosterMenuDanger} onClick={() => { setThreadMenu(null); onDeleteSession(item.id); }}>
                            {en ? "Delete" : "删除"}
                          </button>
                        </div>
                      ) : null}
                    </div>
                  ))}
                  {sessionsLoading && !filteredSessions.length ? (
                    <div className={styles.rosterChatSkeleton} aria-busy="true" aria-label={en ? "Loading chats" : "正在加载对话"}>
                      <i /><i /><i />
                    </div>
                  ) : null}
                  {!sessionsLoading && !filteredSessions.length ? <p>{en ? "No chats yet." : "还没有对话。"}</p> : null}
                  {sessions.some((item) => item.archived) ? (
                    <button type="button" className={styles.rosterClearHistory} onClick={() => setShowArchived((value) => !value)}>
                      {showArchived ? (en ? "Back to active chats" : "返回活动对话") : (en ? "Show archived" : "查看已归档")}
                    </button>
                  ) : null}
                  {filteredSessions.length ? (
                    <button type="button" className={styles.rosterClearHistory} onClick={onClearHistory}>
                      {en ? "Clear all chats" : "清空全部对话"}
                    </button>
                  ) : null}
                </div>
              ) : null}
            </div>
          );
        })}
        {botsLoading && !bots.length ? (
          <div className={styles.rosterSkeleton} aria-busy="true" aria-label={en ? "Loading bots" : "正在加载助手"}>
            {[0, 1, 2].map((index) => (
              <div key={index} className={styles.rosterSkeletonRow}>
                <span className={styles.skeletonAvatar} />
                <span className={styles.skeletonLines}><i /><i /></span>
              </div>
            ))}
          </div>
        ) : null}
        {!botsLoading && !filteredBots.length ? (
          <p className={styles.rosterEmpty}>
            {needle
              ? (en ? "No assistants match." : "没有匹配的助手。")
              : (en ? "No assistants yet." : "还没有助手。")}
          </p>
        ) : null}
      </nav>
      {WATCH_ENABLED ? (
      <div className={styles.rosterSection} data-roster-watching="true">
        <div className={styles.rosterSectionHead}>
          <span>{en ? "Watching" : "我在盯的事"}</span>
          <button type="button" onClick={() => onCreateFocus?.()} aria-label={en ? "Add focus" : "加一条盯梢"}>{en ? "Add" : "＋"}</button>
        </div>
        <nav className={styles.focusRoster} aria-label={en ? "Focus roster" : "关注花名册"}>
          {watching.filter((point) => !needle || matches(point.question) || matches(point.symbol || "")).map((point, index) => {
            const linkedUnread = unread.filter((item) => item.focusId === point.id && item.status === "unread").length;
            const hue = (index * 47 + point.question.length * 13) % 360;
            return (
              <button
                key={point.id}
                type="button"
                className={styles.focusRosterItem}
                data-unread={linkedUnread ? "true" : "false"}
                onClick={() => {
                  const hit = unread.find((item) => item.focusId === point.id && item.status === "unread");
                  if (hit) onOpenAlert?.(hit);
                  else onOpenFocus?.(point);
                }}
                title={point.question}
              >
                <span className={styles.focusRosterAvatar} style={{ background: `hsl(${hue} 72% 58%)` }} aria-hidden="true">
                  <i /><i />
                </span>
                <span className={styles.focusRosterText}>
                  <strong>{point.symbol || (en ? "Focus" : "关注点")}</strong>
                  <small>{point.question}</small>
                </span>
                <span className={styles.focusRosterMeta}>
                  {linkedUnread ? <em className={styles.rosterUnread} aria-label={en ? "Unread" : "未读"} /> : null}
                  <time>{formatTime(point.updatedAt, en ? "en" : "zh")}</time>
                </span>
              </button>
            );
          })}
          {!watching.length ? (
            <p className={styles.rosterEmpty}>{en ? "Nothing on watch yet." : "还没有盯梢关注点。"}</p>
          ) : null}
          {unread.filter((item) => !item.focusId).slice(0, 3).map((alert) => (
            <button
              key={alert.id}
              type="button"
              className={styles.focusRosterItem}
              data-unread="true"
              data-watching-alert={alert.id}
              onClick={() => onOpenAlert?.(alert)}
            >
              <span className={styles.focusRosterAvatar} data-alert="true" aria-hidden="true"><i /><i /></span>
              <span className={styles.focusRosterText}>
                <strong>{alert.title}</strong>
                <small>{alert.focusQuestion || (en ? "Unread alert" : "未读提醒")}</small>
              </span>
              <span className={styles.focusRosterMeta}>
                <em className={styles.rosterUnread} aria-label={en ? "Unread" : "未读"} />
                <time>{formatTime(alert.created_at, en ? "en" : "zh")}</time>
              </span>
            </button>
          ))}
        </nav>
      </div>
      ) : null}
      <div className={styles.rosterFoot}>
        {/* Plugins stay hidden until a real catalog is wired — avoid a permanent "0" ops chrome. */}
        {PLUGINS_READY ? (
          <button type="button" className={styles.rosterPlugins} disabled>
            {en ? "Plugins" : "插件"} <small>0</small>
          </button>
        ) : null}
        <button
          type="button"
          onClick={() => {
            if (productMode !== "build") onProductMode?.("build");
            onEditBot();
          }}
          disabled={!activeBot}
          className={styles.rosterBotSettings}
          title={en ? "Assistant settings" : "助手设置"}
        >
          <BotAvatar name={activeBot?.name || "Bot"} state="idle" size="small" muted />
          <span>{activeBot?.name || (en ? "Research Bot" : "研究助手")}</span>
          <span>⚙</span>
        </button>
        {productMode === "build" && activeBot && !activeBot.is_default ? (
          <button type="button" onClick={onDeleteBot} className={styles.rosterDanger}>
            {en ? "Delete assistant" : "删除助手"}
          </button>
        ) : null}
        <div className={styles.rosterUser}>
          <span className={styles.rosterUserAvatar}>我</span>
          <span>{en ? "Local user" : "本机用户"}</span>
          <small>{en ? "Local" : "本机"}</small>
        </div>
        <div className={styles.rosterControls}>
          <button type="button" className={styles.rosterThemeButton} onClick={onCycleTheme} aria-label={en ? `Theme: ${theme}` : `主题：${theme}`} title={en ? `Theme: ${theme}` : `主题：${theme}`}>
            <span aria-hidden="true">{theme === "system" ? "◐" : theme === "dark" ? "☾" : "☀"}</span>
            <span>{en ? "Theme" : "主题"}</span>
          </button>
          <LanguageSwitcher />
        </div>
        <p>{en ? "Local workspace · no account isolation" : "本机内部 · 无账号隔离"}</p>
      </div>
    </aside>
  );
}

function TaskHistory({ tasks, activeId, onSelect, en }: {
  tasks: BotTask[];
  activeId: string | null;
  onSelect: (id: string) => void;
  en: boolean;
}) {
  if (!tasks.length) return null;
  const recent = [...tasks].slice(-6).reverse();
  return (
    <section className={styles.historyPanel} aria-labelledby="recent-research-heading">
      <div className={styles.panelHead}>
        <h2 id="recent-research-heading">{en ? "Recent research" : "最近研究"}</h2>
        <span>{tasks.length}</span>
      </div>
      <ol className={styles.historyList}>
        {recent.map((task) => (
          <li key={task.id}>
            <button
              type="button"
              className={task.id === activeId ? styles.historyActive : undefined}
              onClick={() => onSelect(task.id)}
              title={task.query}
            >
              <span>{task.query}</span>
              <small>{statusLabel(task.status, en ? "en" : "zh", task.waiting_reason)}</small>
            </button>
          </li>
        ))}
      </ol>
    </section>
  );
}

function StatusCard({ status, en }: { status: BotStatus | null; en: boolean }) {
  if (!status) return null;
  const openMuseStatus = status.openmuse;
  const openMuseCopy = openMuseStatusCopy(openMuseStatus, en ? "en" : "zh");
  return (
    <details className={styles.statusCard}>
      <summary>{en ? "System status" : "系统状态"}</summary>
      <dl>
        <div>
          <dt>{en ? "Mode" : "身份模式"}</dt>
          <dd>
            {status.owner.mode === "local_single_user"
              ? en
                ? "Local internal (shared instance, no account isolation)"
                : "本机内部（共享实例，无账号隔离）"
              : status.owner.label}
          </dd>
        </div>
        <div>
          <dt>{en ? "Storage" : "持久化"}</dt>
          <dd>
            {status.storage.dialect} · {en ? "server-side" : "服务端"}
          </dd>
        </div>
        <div>
          <dt>{en ? "Model" : "模型"}</dt>
          <dd>
            {status.model.configured
              ? `${status.model.model}`
              : en
                ? "Not configured · deterministic fallback"
                : "未配置 · 使用确定性降级"}
          </dd>
        </div>
        <div>
          <dt>OpenMuse</dt>
          <dd className={openMuseStatus?.status === "connected" ? styles.gateOpen : styles.gateClosed}>
            {openMuseCopy.label}
          </dd>
        </div>
        {openMuseStatus?.status === "connected" ? (
          <>
            <div>
              <dt>{en ? "Reported agent configuration" : "OpenMuse 报告的 Agent 配置"}</dt>
              <dd>{openMuseStatus.agent_configured ? (en ? "Configured" : "已配置") : (en ? "Not configured" : "未配置")}</dd>
            </div>
            <div>
              <dt>{en ? "Reported browser worker configuration" : "OpenMuse 报告的浏览器 Worker 配置"}</dt>
              <dd>{openMuseStatus.browser_configured ? (en ? "Configured" : "已配置") : (en ? "Not configured" : "未配置")}</dd>
            </div>
          </>
        ) : null}
        <div>
          <dt>{en ? "Today / limits" : "今日用量 / 限额"}</dt>
          <dd>
            {status.usage.tasks_today}/{status.limits.daily_tasks} {en ? "tasks" : "任务"} ·{" "}
            {status.usage.tokens_today.toLocaleString()}/{status.limits.daily_tokens > 0 ? status.limits.daily_tokens.toLocaleString() : (en ? "unlimited" : "不限")} tokens
          </dd>
        </div>
        <div>
          <dt>{en ? "Local research data" : "本机研究数据"}</dt>
          <dd className={status.local_data.enabled ? styles.gateOpen : styles.gateClosed}>
            {status.local_data.enabled
              ? en
                ? "On · local/internal only"
                : "已启用 · 仅本机内部"
              : en
                ? "Off"
                : "关闭"}
          </dd>
        </div>
        <div>
          <dt>{en ? "Multi-user trial" : "多人试用"}</dt>
          <dd className={styles.gateClosed}>{en ? "Closed (gate)" : "关闭（门禁）"}</dd>
        </div>
      </dl>
      <p className={styles.gateReason}>{openMuseCopy.detail}</p>
      <p className={styles.gateReason}>{status.trial_gate.reason}</p>
      {status.provider_config.ifind_history_note || status.provider_config.ifind_history_quota_exhausted ? (
        <p className={styles.gateReason}>
          {status.provider_config.ifind_history_note
            || (en
              ? "iFinD historical market-data quota is exhausted; using realtime/fundamentals/fallback quotes."
              : "iFinD 历史数据额度已用完，已改用实时/基本面/备用行情")}
        </p>
      ) : null}
      <p className={styles.gateReason}>
        {en ? "Provider config" : "数据源配置"}: Finnhub{" "}
        {status.provider_config.finnhub ? (en ? "on" : "已配置") : (en ? "off" : "未配置")} · Fuyao{" "}
        {status.provider_config.fuyao ? (en ? "on" : "已配置") : (en ? "off" : "未配置")} · Brave{" "}
        {status.provider_config.brave_search ? (en ? "on" : "已配置") : (en ? "off" : "未配置")} · SEC contact{" "}
        {status.provider_config.sec_contact ? (en ? "on" : "已配置") : (en ? "off" : "未配置")} · yfinance shadow{" "}
        {status.provider_config.yfinance_shadow ? (en ? "on" : "已开启") : (en ? "off" : "未开启")} · yfinance US/HK fundamentals{" "}
        {status.provider_config.yfinance_us_hk_fundamentals ? (en ? "on" : "已开启") : (en ? "off" : "未开启")} · yfinance US/HK primary{" "}
        {status.provider_config.yfinance_primary_us_hk ? (en ? "on" : "已开启") : (en ? "off" : "未开启")} · A-share fallback{" "}
        {status.provider_config.yfinance_a_share_fallback ? (en ? "on" : "已开启") : (en ? "off" : "未开启")}
      </p>
      <details className={styles.dataSources}>
        <summary>{en ? "Data sources" : "数据源"}</summary>
        <ul>
          {Object.entries(status.data_sources).map(([key, value]) => (
            <li key={key}>
              <strong>{key}</strong>: {value}
            </li>
          ))}
        </ul>
      </details>
    </details>
  );
}

function MemoryPanel({
  en,
  memories,
  candidates,
  drafts,
  editing,
  onDraft,
  onConfirm,
  onDismiss,
  onStartEdit,
  onEditChange,
  onSave,
  onCancelEdit,
  deleteTarget,
  onAskDelete,
  onCancelDelete,
  onDelete,
}: {
  en: boolean;
  memories: MemoryItem[];
  candidates: MemoryCandidate[];
  drafts: Record<string, string>;
  editing: { id: string; text: string } | null;
  onDraft: (id: string, value: string) => void;
  onConfirm: (candidate: MemoryCandidate) => void;
  onDismiss: (candidate: MemoryCandidate) => void;
  onStartEdit: (memory: MemoryItem) => void;
  onEditChange: (text: string) => void;
  onSave: (memory: MemoryItem, text: string) => void;
  onCancelEdit: () => void;
  deleteTarget: string | null;
  onAskDelete: (id: string | null) => void;
  onCancelDelete: () => void;
  onDelete: (id: string) => void;
}) {
  return (
    <section className={styles.memoryPanel} aria-labelledby="memory-heading">
      <div className={styles.railHead}>
        <span className={styles.railKicker} id="memory-heading">{en ? "Memory" : "长期记忆"}</span>
        <span className={styles.railCount}>{memories.length}</span>
      </div>
      <p className={styles.panelNote}>
        {en
          ? "Only confirmed rows enter cross-session context. Corrections create a new version."
          : "只有已确认条目会进入跨会话上下文；修改会产生新版本。"}
      </p>

      {candidates.length ? (
        <div className={styles.pendingList}>
          <p className={styles.panelSubhead}>{en ? "Pending viewpoints" : "待确认观点"} · {candidates.length}</p>
          {candidates.map((candidate) => (
            <CandidateCard
              key={candidate.id}
              candidate={candidate}
              en={en}
              value={drafts[candidate.id] ?? candidate.proposed_text}
              onChange={(value) => onDraft(candidate.id, value)}
              onConfirm={() => onConfirm(candidate)}
              onDismiss={() => onDismiss(candidate)}
            />
          ))}
        </div>
      ) : null}

      {memories.length ? (
        <ul className={styles.memoryList}>
          {memories.map((memory) => (
            <li key={memory.id}>
              <div className={styles.memoryMeta}>
                <span>{kindLabels[memory.kind]?.[en ? 1 : 0] ?? memory.kind}</span>
                <small>
                  v{memory.version} · {formatTime(memory.confirmed_at || memory.updated_at, en ? "en" : "zh")}
                </small>
              </div>
              {editing?.id === memory.id ? (
                <div className={styles.editBox}>
                  <textarea value={editing.text} rows={3} onChange={(event) => onEditChange(event.target.value)} />
                  <div>
                    <button type="button" onClick={() => onSave(memory, editing.text)} disabled={!editing.text.trim()}>
                      {en ? "Save new version" : "保存新版本"}
                    </button>
                    <button type="button" className={styles.quietButton} onClick={onCancelEdit}>
                      {en ? "Cancel" : "取消"}
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  <p className={styles.memoryText}>{memory.text}</p>
                  {memory.source_text ? (
                    <details className={styles.sourceQuote}>
                      <summary>{en ? "Original words" : "原始表述"}</summary>
                      <p>{memory.source_text}</p>
                    </details>
                  ) : null}
                  <div className={styles.memoryActions}>
                    <button type="button" onClick={() => onStartEdit(memory)}>
                      {en ? "Correct" : "修改"}
                    </button>
                    {deleteTarget === memory.id ? (
                      <>
                        <button type="button" className={styles.danger} onClick={() => onDelete(memory.id)}>
                          {en ? "Confirm delete" : "确认删除"}
                        </button>
                        <button type="button" className={styles.quietButton} onClick={onCancelDelete}>
                          {en ? "Cancel" : "取消"}
                        </button>
                      </>
                    ) : (
                      <button type="button" className={styles.quietButton} onClick={() => onAskDelete(memory.id)}>
                        {en ? "Delete" : "删除"}
                      </button>
                    )}
                  </div>
                </>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className={styles.emptyMemory}>{en ? "No confirmed memory yet." : "还没有已确认的长期记忆。"}</p>
      )}
    </section>
  );
}

function DeletionPanel({
  en,
  deletion,
  clearConfirm,
  onClearConfirm,
  onClearAll,
  clearing,
  memoryCount,
}: {
  en: boolean;
  deletion: DeletionJob | null;
  clearConfirm: string;
  onClearConfirm: (value: string) => void;
  onClearAll: () => void;
  clearing: boolean;
  memoryCount: number;
}) {
  const expected = en ? "DELETE ALL" : "删除全部";
  const layerNames: Record<string, [string, string]> = {
    memory_rows: ["记忆本体", "Memory rows"],
    memory_versions: ["历史版本", "Version history"],
    memory_candidates: ["候选观点", "Candidates"],
    quote_copies: ["引用与原文副本", "Quote copies"],
    search_index: ["检索索引", "Search index"],
    context_cache: ["上下文缓存", "Context cache"],
    physical_storage: ["物理存储整理", "Physical storage"],
    backup: ["备份", "Backups"],
    third_party_model: ["模型供应商保留", "Model provider retention"],
  };
  const layerStatus: Record<string, [string, string]> = {
    pending: ["处理中", "Pending"],
    cleared: ["已清除", "Cleared"],
    verified: ["已验证", "Verified"],
    unverified: ["未核实", "Unverified"],
    blocked: ["阻塞", "Blocked"],
    failed: ["失败", "Failed"],
    not_configured: ["未配置", "Not configured"],
  };
  const layerDetailEn: Record<string, Record<string, string>> = {
    context_cache: { cleared: "Per-owner context cache invalidated." },
    physical_storage: { cleared: "SQLite secure_delete + VACUUM executed after commit." },
    backup: {
      not_configured: "No separate backup directory is configured; deployment-level snapshots still need verification.",
      unverified: "Backups exist but no verifiable backup purge or key destruction is configured.",
      verified: "Backup directory is empty; no applicable backup was found.",
    },
    third_party_model: {
      unverified: "Provider retention for inputs/outputs is unverified; complete deletion cannot be claimed.",
      verified: "Operator marked provider retention as verified.",
    },
  };
  const blocking = deletion
    ? Object.entries(deletion.layers)
        .filter(([, layer]) => ["unverified", "blocked", "failed"].includes(layer.status))
        .map(([name]) => (layerNames[name]?.[en ? 1 : 0] ?? name))
    : [];
  return (
    <section className={styles.deletionPanel} aria-labelledby="deletion-heading">
      <div className={styles.railHead}>
        <span className={styles.railKicker} id="deletion-heading">{en ? "Deletion & verification" : "删除与验证"}</span>
      </div>
      <p className={styles.panelNote}>
        {en
          ? "Deleting one memory does not delete independent conversations. The job blocks when a layer cannot be proven cleared."
          : "删除单条记忆不会删除独立会话；任一层无法证明清除时，任务保持阻塞而不显示“完全删除”。"}
      </p>
      {memoryCount ? (
        <div className={styles.clearAll}>
          <label htmlFor="clear-all-input">
            {en
              ? `Type “${expected}” to clear all long-term memory`
              : `输入“${expected}”以清空全部长期记忆`}
          </label>
          <div>
            <input
              id="clear-all-input"
              value={clearConfirm}
              onChange={(event) => onClearConfirm(event.target.value)}
              autoComplete="off"
            />
            <button
              type="button"
              className={styles.dangerButton}
              disabled={clearConfirm !== expected || clearing}
              onClick={onClearAll}
            >
              {clearing ? (en ? "Clearing…" : "清除中…") : en ? "Clear all" : "清空全部"}
            </button>
          </div>
        </div>
      ) : null}
      {deletion ? (
        <div className={styles.deletionJob}>
          <div className={styles.deletionStatus} data-status={deletion.status}>
            <strong>
              {deletion.status === "completed"
                ? en
                  ? "Completed"
                  : "已完成"
                : deletion.status === "blocked"
                  ? en
                    ? "Blocked"
                    : "阻塞"
                  : deletion.status}
            </strong>
            <small>
              {deletion.scope === "all" ? (en ? "All memory" : "全部记忆") : en ? "Single memory" : "单条记忆"} ·{" "}
              {formatTime(deletion.completed_at || deletion.requested_at, en ? "en" : "zh")}
            </small>
          </div>
          <p className={styles.deletionDetail}>
            {en
              ? blocking.length
                ? `Controlled local layers were cleared. Blocked or unverified layers: ${blocking.join(", ")}.`
                : "All controlled layers were verified as cleared."
              : deletion.detail}
          </p>
          <ul className={styles.layerList}>
            {Object.entries(deletion.layers).map(([name, layer]) => (
              <li key={name} data-status={layer.status}>
                <span>{layerNames[name]?.[en ? 1 : 0] ?? name}</span>
                <small>
                  <strong>{layerStatus[layer.status]?.[en ? 1 : 0] ?? layer.status}</strong>
                  {en
                    ? layerDetailEn[name]?.[layer.status]
                      ? ` · ${layerDetailEn[name][layer.status]}`
                      : ""
                    : layer.detail
                      ? ` · ${layer.detail}`
                      : ""}
                </small>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
