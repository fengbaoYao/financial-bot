"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  addMarketClosure,
  createBot,
  deleteBot,
  deleteMarketClosure,
  getMemories,
  getPromptDefaults,
  listMarketClosures,
  previewBotPrompt,
  previewDigest,
  sendDigestNow,
  testBotNotification,
  updateBot,
  type BotProfile,
  type BotPromptConfig,
  type BotPromptPreview,
  type DigestPreview,
  type MarketClosure,
  type PromptDefaults,
} from "@/lib/financial-bot/client";
import type { MemoryResponse } from "@/lib/financial-bot/types";
import styles from "./financial-bot-styles";

type Tab = "identity" | "prompt" | "abilities" | "memory" | "delivery" | "advanced";

type StudioDraft = {
  name: string;
  description: string;
  instructions: string;
  threshold: number;
  promptTemplate: string;
  promptConfig: BotPromptConfig;
  marketHoursOnly: boolean;
  maxAlertsPerDay: number;
  quietHoursStart: string;
  quietHoursEnd: string;
  webhookUrl: string;
  notificationEmail: string;
  reportStyle: "brief" | "standard";
  digestMode: "immediate" | "daily";
  digestHour: string;
};

type Props = {
  bot: BotProfile | null;
  en: boolean;
  onClose: () => void;
  onSaved: (bot: BotProfile, created: boolean) => void | Promise<void>;
  onDeleted: (movedTo: string, movedSessions: number) => void | Promise<void>;
  onNotice: (message: string) => void;
  onError: (message: string) => void;
  onOpenMemory: () => void;
};

const FALLBACK_CONFIG: BotPromptConfig = {
  role: "",
  markets: ["us", "hk", "cn"],
  output_style: "standard",
  capabilities: { web: true, filings: true, market: true, news: true, portfolio: true, watchlist: true },
  memory_enabled: true,
  routines_enabled: true,
  proactive_enabled: true,
};

const CAPABILITY_META: Array<{
  key: keyof BotPromptConfig["capabilities"];
  icon: string;
  zh: string;
  en: string;
  descZh: string;
  descEn: string;
  tools: string[];
}> = [
  {
    key: "web",
    icon: "⌁",
    zh: "联网检索",
    en: "Web research",
    descZh: "搜索公开网页、读取原始来源，并做公司级多步研究。",
    descEn: "Search public pages, read primary sources, and run multi-step company research.",
    tools: ["web_search", "read_public_source", "research_company", "research_comparison"],
  },
  {
    key: "filings",
    icon: "▤",
    zh: "财报与公告",
    en: "Filings & financials",
    descZh: "SEC 申报、A股公告、财务报表、目标价与一致预期。",
    descEn: "SEC filings, China announcements, statements, targets and consensus.",
    tools: ["get_sec_company_data", "get_cninfo_announcements", "get_financial_statements", "get_company_fundamentals", "get_analyst_estimates", "get_consensus_estimates", "compare_companies"],
  },
  {
    key: "market",
    icon: "◴",
    zh: "行情与热点",
    en: "Market & themes",
    descZh: "行情快照、技术指标、指数成分与市场热点。",
    descEn: "Quotes, technicals, index constituents and market themes.",
    tools: ["get_stock_analysis", "get_index_constituents", "list_hotspots", "get_hotspot_detail"],
  },
  {
    key: "news",
    icon: "≋",
    zh: "公司新闻",
    en: "Company news",
    descZh: "按标的检索公司新闻，以及全球市场新闻。",
    descEn: "Company-matched news and global market headlines.",
    tools: ["get_stock_news", "get_global_news"],
  },
  {
    key: "portfolio",
    icon: "◫",
    zh: "只读持仓分析",
    en: "Read-only portfolio",
    descZh: "读取工作区持仓并做风险与结构分析，不执行交易。",
    descEn: "Read the workspace portfolio for risk analysis. No trading.",
    tools: ["get_portfolio_analysis"],
  },
  {
    key: "watchlist",
    icon: "☆",
    zh: "自选股读写",
    en: "Watchlist writes",
    descZh: "允许增删自选股；写入前仍会向用户确认。",
    descEn: "Allow watchlist changes; user confirmation is still required.",
    tools: ["manage_watchlist"],
  },
];

const PRESETS: Array<{
  id: string;
  icon: string;
  nameZh: string;
  nameEn: string;
  descZh: string;
  descEn: string;
  role: string;
  style: BotPromptConfig["output_style"];
  capabilities: Partial<BotPromptConfig["capabilities"]>;
}> = [
  {
    id: "research",
    icon: "◈",
    nameZh: "公司研究",
    nameEn: "Company research",
    descZh: "覆盖财报、公告、新闻与估值，输出有来源的投资研究简报。",
    descEn: "Filings, announcements, news and valuation, with a sourced research brief.",
    role: "你是一名严谨的公司研究分析师。先核验公司身份，再围绕用户的问题收集财报、公告、新闻与行情证据；区分事实与推断，明确数据缺口，不给出买卖建议。",
    style: "standard",
    capabilities: { web: true, filings: true, market: true, news: true, portfolio: false, watchlist: false },
  },
  {
    id: "monitor",
    icon: "◎",
    nameZh: "公告与异动",
    nameEn: "Filings & moves",
    descZh: "盯住公告、财报和价格异动，只在有实质变化时提醒。",
    descEn: "Watch filings, earnings and price moves; alert only on material changes.",
    role: "你是一名事件驱动研究员。优先检查交易所公告、财报和价格异动，判断事件是否具有实质性；没有实质变化时不要制造提醒。所有判断必须有来源和明确时间。",
    style: "concise",
    capabilities: { web: true, filings: true, market: true, news: true, portfolio: false, watchlist: true },
  },
  {
    id: "briefing",
    icon: "▦",
    nameZh: "自选股晨报",
    nameEn: "Watchlist briefing",
    descZh: "每个交易日整理自选股的变化、新闻和需要继续跟踪的问题。",
    descEn: "A daily briefing on watched names, news and open questions.",
    role: "你是一名自选股简报编辑。按重要性整理自选股的价格变化、公司新闻和待跟踪问题；先给结论，再给证据；没有变化的标的不要凑篇幅。",
    style: "concise",
    capabilities: { web: true, filings: true, market: true, news: true, portfolio: true, watchlist: true },
  },
];

function mergeConfig(base: BotPromptConfig, incoming?: BotPromptConfig | null): BotPromptConfig {
  if (!incoming) return { ...base, capabilities: { ...base.capabilities } };
  return {
    role: incoming.role ?? base.role,
    markets: incoming.markets?.length ? incoming.markets : base.markets,
    output_style: incoming.output_style ?? base.output_style,
    capabilities: { ...base.capabilities, ...(incoming.capabilities ?? {}) },
    memory_enabled: incoming.memory_enabled ?? base.memory_enabled,
    routines_enabled: incoming.routines_enabled ?? base.routines_enabled,
    proactive_enabled: incoming.proactive_enabled ?? base.proactive_enabled,
  };
}

function fromBot(bot: BotProfile | null, defaults: PromptDefaults | null): StudioDraft {
  const base = defaults?.config ?? FALLBACK_CONFIG;
  const config = mergeConfig(base, bot?.prompt_config);
  return {
    name: bot?.name || "",
    description: bot?.description || "",
    instructions: bot?.instructions || config.role || "",
    threshold: bot?.approval_threshold ?? 4,
    promptTemplate: bot?.prompt_template || defaults?.template || "",
    promptConfig: { ...config, role: bot?.instructions || config.role || "" },
    marketHoursOnly: bot?.market_hours_only !== false,
    maxAlertsPerDay: bot?.max_alerts_per_day ?? 20,
    quietHoursStart: (bot?.quiet_hours_start || "").slice(0, 5),
    quietHoursEnd: (bot?.quiet_hours_end || "").slice(0, 5),
    webhookUrl: "",
    notificationEmail: bot?.notification_email || "",
    reportStyle: bot?.report_style === "standard" ? "standard" : "brief",
    digestMode: bot?.digest_mode === "daily" ? "daily" : "immediate",
    digestHour: (bot?.digest_hour || "18:00").slice(0, 5),
  };
}

function comparable(draft: StudioDraft) {
  return {
    name: draft.name,
    description: draft.description,
    instructions: draft.instructions,
    threshold: draft.threshold,
    promptTemplate: draft.promptTemplate,
    promptConfig: { ...draft.promptConfig, role: draft.instructions, capabilities: { ...draft.promptConfig.capabilities } },
    marketHoursOnly: draft.marketHoursOnly,
    maxAlertsPerDay: draft.maxAlertsPerDay,
    quietHoursStart: draft.quietHoursStart,
    quietHoursEnd: draft.quietHoursEnd,
    webhookUrl: draft.webhookUrl,
    notificationEmail: draft.notificationEmail,
    reportStyle: draft.reportStyle,
    digestMode: draft.digestMode,
    digestHour: draft.digestHour,
  };
}

function formatDateTime(iso?: string | null): string {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(iso));
  } catch {
    return iso;
  }
}

function StudioAvatar({ name }: { name: string }) {
  return <span className={styles.studioAvatar} aria-hidden="true">{name.trim().slice(0, 1) || "B"}</span>;
}

export function BotStudio({ bot, en, onClose, onSaved, onDeleted, onNotice, onError, onOpenMemory }: Props) {
  const [tab, setTab] = useState<Tab>("identity");
  /** Simple: identity + memory. Advanced: prompt / abilities / delivery / advanced too. */
  const [studioLayer, setStudioLayer] = useState<"simple" | "advanced">("simple");
  const [mobileView, setMobileView] = useState<"config" | "preview">("config");
  const [defaults, setDefaults] = useState<PromptDefaults | null>(null);
  const [draft, setDraft] = useState<StudioDraft>(() => fromBot(bot, null));
  const [initialized, setInitialized] = useState(false);
  const [saving, setSaving] = useState(false);
  const [preview, setPreview] = useState<BotPromptPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(true);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewQuery, setPreviewQuery] = useState("");
  const [memories, setMemories] = useState<MemoryResponse | null>(null);
  const [memoryError, setMemoryError] = useState<string | null>(null);
  const [inlineNote, setInlineNote] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [digest, setDigest] = useState<DigestPreview | null>(null);
  const [digestSending, setDigestSending] = useState(false);
  const [closures, setClosures] = useState<MarketClosure[]>([]);
  const [closureDraft, setClosureDraft] = useState<{ market: "us" | "hk" | "cn"; date: string; reason: string }>({ market: "us", date: "", reason: "" });
  const [closureSaving, setClosureSaving] = useState(false);
  const [showVariables, setShowVariables] = useState(false);
  const [copied, setCopied] = useState(false);
  const [studioConfirming, setStudioConfirming] = useState(false);
  const studioConfirmLockRef = useRef(false);
  const [studioConfirm, setStudioConfirm] = useState<null | {
    title: string;
    body: string;
    confirmLabel: string;
    danger?: boolean;
    onConfirm: () => void;
  }>(null);
  useEffect(() => {
    if (studioConfirm) studioConfirmLockRef.current = false;
  }, [studioConfirm]);
  const templateRef = useRef<HTMLTextAreaElement | null>(null);
  const previewSeq = useRef(0);
  const defaultsLoaded = useRef(false);

  const isEdit = Boolean(bot);

  useEffect(() => {
    let alive = true;
    getPromptDefaults()
      .then((data) => {
        if (!alive) return;
        setDefaults(data);
        if (!defaultsLoaded.current) {
          defaultsLoaded.current = true;
          setDraft((current) => ({
            ...current,
            promptTemplate: current.promptTemplate || data.template,
            promptConfig: mergeConfig(data.config, current.promptConfig),
          }));
        }
        setInitialized(true);
      })
      .catch((caught) => {
        if (!alive) return;
        setInitialized(true);
        setPreviewError(caught instanceof Error ? caught.message : String(caught));
      });
    return () => {
      alive = false;
    };
  }, []);

  const initial = useMemo(() => fromBot(bot, defaults), [bot, defaults]);
  const dirty = useMemo(() => !bot || JSON.stringify(comparable(draft)) !== JSON.stringify(comparable(initial)), [bot, draft, initial]);
  const requestClose = useCallback(() => {
    if (saving) return;
    if (!dirty) {
      onClose();
      return;
    }
    setStudioConfirm({
      title: en ? "Discard changes?" : "放弃修改？",
      body: en ? "Discard unsaved changes?" : "放弃未保存的修改？",
      confirmLabel: en ? "Discard" : "放弃",
      danger: true,
      onConfirm: () => onClose(),
    });
  }, [saving, dirty, en, onClose]);

  useEffect(() => {
    if (!bot) return;
    let alive = true;
    getMemories(bot.id, bot.is_default)
      .then((data) => {
        if (alive) setMemories(data);
      })
      .catch((caught) => {
        if (alive) setMemoryError(caught instanceof Error ? caught.message : String(caught));
      });
    return () => {
      alive = false;
    };
  }, [bot]);

  useEffect(() => {
    if (tab !== "delivery" || !bot) return;
    let alive = true;
    listMarketClosures()
      .then((data) => {
        if (alive) setClosures(data.closures);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [tab, bot]);

  useEffect(() => {
    if (!initialized || !draft.promptTemplate) return;
    const timer = window.setTimeout(() => {
      const seq = ++previewSeq.current;
      setPreviewLoading(true);
      setPreviewError(null);
      void previewBotPrompt({
        botId: bot?.id,
        name: draft.name.trim() || (en ? "New assistant" : "新助手"),
        description: draft.description,
        instructions: draft.instructions,
        promptTemplate: draft.promptTemplate,
        promptConfig: { ...draft.promptConfig, role: draft.instructions },
        query: previewQuery,
      })
        .then((data) => {
          if (seq !== previewSeq.current) return;
          setPreview(data);
          const serverTemplate = data.template;
          if (!draft.promptTemplate && serverTemplate) {
            setDraft((current) => (current.promptTemplate ? current : { ...current, promptTemplate: serverTemplate }));
          }
        })
        .catch((caught) => {
          if (seq !== previewSeq.current) return;
          setPreviewError(caught instanceof Error ? caught.message : String(caught));
        })
        .finally(() => {
          if (seq === previewSeq.current) setPreviewLoading(false);
        });
    }, 320);
    return () => window.clearTimeout(timer);
  }, [initialized, draft.name, draft.description, draft.instructions, draft.promptTemplate, draft.promptConfig, previewQuery, bot?.id, en]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !saving && !digest) requestClose();
    };
    window.addEventListener("keydown", onKeyDown);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previous;
    };
  }, [saving, digest, requestClose]);

  const enabledCount = preview?.enabled_tools.length ?? 0;
  const allCount = preview?.all_tools.length ?? 0;

  function update<K extends keyof StudioDraft>(key: K, value: StudioDraft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  function updateCapability(key: keyof BotPromptConfig["capabilities"], value: boolean) {
    setDraft((current) => ({
      ...current,
      promptConfig: { ...current.promptConfig, capabilities: { ...current.promptConfig.capabilities, [key]: value } },
    }));
  }

  function applyPreset(preset: (typeof PRESETS)[number]) {
    setDraft((current) => ({
      ...current,
      name: current.name || (en ? preset.nameEn : preset.nameZh),
      instructions: preset.role,
      promptConfig: {
        ...current.promptConfig,
        role: preset.role,
        output_style: preset.style,
        capabilities: { ...current.promptConfig.capabilities, ...preset.capabilities },
      },
    }));
    setTab("prompt");
  }

  function insertVariable(name: string) {
    const token = `{{${name}}}`;
    const element = templateRef.current;
    if (!element) {
      update("promptTemplate", `${draft.promptTemplate}${token}`);
      return;
    }
    const start = element.selectionStart ?? draft.promptTemplate.length;
    const end = element.selectionEnd ?? start;
    const next = `${draft.promptTemplate.slice(0, start)}${token}${draft.promptTemplate.slice(end)}`;
    update("promptTemplate", next);
    window.requestAnimationFrame(() => {
      element.focus();
      element.setSelectionRange(start + token.length, start + token.length);
    });
  }

  async function save() {
    if (!draft.name.trim() || saving) return;
    setSaving(true);
    setInlineNote(null);
    const payload = {
      name: draft.name.trim(),
      description: draft.description.trim(),
      instructions: draft.instructions.trim(),
      approvalThreshold: draft.threshold,
      ...(draft.promptTemplate ? { promptTemplate: draft.promptTemplate } : {}),
      promptConfig: { ...draft.promptConfig, role: draft.instructions.trim() },
      marketHoursOnly: draft.marketHoursOnly,
      maxAlertsPerDay: draft.maxAlertsPerDay,
      quietHoursStart: draft.quietHoursStart,
      quietHoursEnd: draft.quietHoursEnd,
      notificationEmail: draft.notificationEmail,
      reportStyle: draft.reportStyle,
      digestMode: draft.digestMode,
      digestHour: draft.digestHour,
      ...(draft.webhookUrl.trim() ? { webhookUrl: draft.webhookUrl.trim() } : {}),
    };
    try {
      if (bot) {
        const { bot: updated } = await updateBot(bot.id, payload);
        await onSaved(updated, false);
      } else {
        const { bot: created } = await createBot(payload);
        await onSaved(created, true);
      }
    } catch (caught) {
      onError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSaving(false);
    }
  }

  function remove() {
    if (!bot || bot.is_default || saving) return;
    setStudioConfirm({
      title: en ? "Delete assistant" : "删除助手",
      body: en ? `Delete ${bot.name}? Its conversations and memories move to the default Bot.` : `删除 ${bot.name}？它的对话和记忆会转移到默认 Bot。`,
      confirmLabel: en ? "Delete" : "删除",
      danger: true,
      onConfirm: () => {
        void (async () => {
          setSaving(true);
          try {
            const result = await deleteBot(bot.id);
            await onDeleted(result.moved_to, result.moved_sessions);
          } catch (caught) {
            onError(caught instanceof Error ? caught.message : String(caught));
          } finally {
            setSaving(false);
          }
        })();
      },
    });
  }

  const deliveryConfigured = Boolean(
    draft.webhookUrl.trim()
    || draft.notificationEmail.trim()
    || bot?.webhook_configured
    || bot?.notification_email,
  );

  async function testChannels() {
    if (!bot || testing) return;
    setTesting(true);
    try {
      const result = await testBotNotification(bot.id);
      if (!result.configured) setInlineNote(en ? "No webhook or email is configured yet. Fill one in Delivery first." : "还没有配置 webhook/邮箱，请先在投递里配置。");
      else if (result.webhook || result.email) setInlineNote(en ? "Test notification sent." : "测试通知已发送。");
      else setInlineNote(en ? "Test failed; the in-app inbox is unaffected." : "测试发送失败；站内收件箱不受影响。");
    } catch (caught) {
      onError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setTesting(false);
    }
  }

  async function openDigest() {
    if (!bot) {
      setInlineNote(en ? "Save the assistant before previewing a digest." : "请先保存助手，再预览摘要。");
      return;
    }
    try {
      const result = await previewDigest(bot.id);
      setDigest(result.digest);
    } catch (caught) {
      onError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  async function sendDigest() {
    if (!bot || digestSending) return;
    setDigestSending(true);
    try {
      const result = await sendDigestNow(bot.id);
      setDigest(result.digest);
      if (!result.configured) setInlineNote(en ? "No webhook or email is configured yet. Fill one in Delivery first." : "还没有配置 webhook/邮箱，请先在投递里配置。");
      else if (result.sent.webhook || result.sent.email) onNotice(en ? "Digest sent to the configured channels." : "摘要已发送到配置的渠道。");
      else setInlineNote(en ? "Digest send failed; check the delivery log." : "摘要发送失败，请查看发送记录。");
    } catch (caught) {
      onError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setDigestSending(false);
    }
  }

  async function addClosure() {
    if (!closureDraft.date || closureSaving) return;
    setClosureSaving(true);
    try {
      const result = await addMarketClosure({ market: closureDraft.market, date: closureDraft.date, reason: closureDraft.reason });
      setClosures((current) => [result.closure, ...current]);
      setClosureDraft((current) => ({ ...current, date: "", reason: "" }));
    } catch (caught) {
      onError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setClosureSaving(false);
    }
  }

  async function removeClosure(closure: MarketClosure) {
    try {
      await deleteMarketClosure(closure.id);
      setClosures((current) => current.filter((item) => item.id !== closure.id));
    } catch (caught) {
      onError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  async function copyPrompt() {
    if (!preview) return;
    const text = preview.segments
      .filter((segment) => segment.role === "system")
      .map((segment) => `## ${segment.label}\n${segment.content}`)
      .join("\n\n");
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setInlineNote(en ? "Clipboard is unavailable." : "当前浏览器不允许读取剪贴板。");
    }
  }

  const allTabs: Array<{ id: Tab; icon: string; zh: string; en: string; layer: "simple" | "advanced" }> = [
    { id: "identity", icon: "◈", zh: "身份", en: "Identity", layer: "simple" },
    { id: "memory", icon: "◉", zh: "记忆", en: "Memory", layer: "simple" },
    { id: "prompt", icon: "⌘", zh: "提示词", en: "Prompt", layer: "advanced" },
    { id: "abilities", icon: "◇", zh: "能力与权限", en: "Abilities", layer: "advanced" },
    { id: "delivery", icon: "↗", zh: "投递", en: "Delivery", layer: "advanced" },
    { id: "advanced", icon: "⋯", zh: "高级", en: "Advanced", layer: "advanced" },
  ];
  const tabs = allTabs.filter((item) => studioLayer === "advanced" || item.layer === "simple");

  const recentMemories = (memories?.memories ?? []).slice(0, 6);
  const pendingCandidates = memories?.pending_candidates?.length ?? 0;

  return (
    <div className={styles.studioBackdrop} role="dialog" aria-modal="true" aria-labelledby="studio-title" onClick={requestClose}>
      <div className={styles.studio} onClick={(event) => event.stopPropagation()}>
        <header className={styles.studioHead}>
          <div className={styles.studioIdentity}>
            <StudioAvatar name={draft.name} />
            <div>
              <span className={styles.studioKicker}>{en ? "Assistant settings" : "助手设置"}</span>
              <h2 id="studio-title">{draft.name.trim() || (en ? "Untitled assistant" : "未命名助手")}</h2>
            </div>
            <span className={styles.studioDirty} data-dirty={dirty ? "true" : "false"}>
              {dirty ? (en ? (bot ? "Unsaved" : "New") : bot ? "未保存" : "新建") : en ? "Saved" : "已保存"}
            </span>
          </div>
          <div className={styles.studioHeadActions}>
            <div className={styles.studioMobileSwitch} role="group" aria-label={en ? "Settings view" : "设置视图"}>
              <button type="button" data-active={mobileView === "config" ? "true" : "false"} onClick={() => setMobileView("config")}>{en ? "Config" : "配置"}</button>
              <button type="button" data-active={mobileView === "preview" ? "true" : "false"} onClick={() => setMobileView("preview")}>{en ? "Preview" : "预览"}</button>
            </div>
            <button type="button" className={styles.studioGhostButton} onClick={requestClose} disabled={saving}>{en ? "Close" : "关闭"}</button>
            <button type="button" className={styles.studioSaveButton} onClick={() => void save()} disabled={saving || !draft.name.trim()}>
              {saving ? (en ? "Saving…" : "保存中…") : isEdit ? (en ? "Save changes" : "保存修改") : (en ? "Create assistant" : "创建助手")}
            </button>
          </div>
        </header>

        <div className={styles.studioBody}>
          <nav className={styles.studioNav} aria-label={en ? "Assistant settings sections" : "助手设置分区"} data-mobile={mobileView}>
            <p className={styles.studioNavLabel}>{en ? "Configure" : "配置"}</p>
            <div className={styles.studioLayerSwitch} role="group" aria-label={en ? "Settings complexity" : "设置复杂度"}>
              <button
                type="button"
                data-active={studioLayer === "simple" ? "true" : "false"}
                onClick={() => {
                  setStudioLayer("simple");
                  setTab((current) => (current === "identity" || current === "memory" ? current : "identity"));
                }}
              >{en ? "Simple" : "简单"}</button>
              <button
                type="button"
                data-active={studioLayer === "advanced" ? "true" : "false"}
                onClick={() => setStudioLayer("advanced")}
              >{en ? "Advanced" : "高级"}</button>
            </div>
            <p className={styles.studioLayerHint}>
              {studioLayer === "simple"
                ? (en ? "Name, description and memory for everyday use." : "日常只用：名称、说明与记忆。")
                : (en ? "Prompt, abilities, delivery and advanced controls." : "提示词、能力、投递与高级选项。")}
            </p>
            {tabs.map((item) => (
              <button
                key={item.id}
                type="button"
                className={tab === item.id ? styles.studioNavActive : undefined}
                onClick={() => setTab(item.id)}
                data-tab={item.id}
              >
                <span aria-hidden="true">{item.icon}</span>
                <span>{en ? item.en : item.zh}</span>
                {item.id === "memory" && recentMemories.length ? <small>{recentMemories.length}</small> : null}
                {item.id === "abilities" ? <small>{enabledCount}/{allCount || 20}</small> : null}
              </button>
            ))}
            <div className={styles.studioNavFoot}>
              <span className={styles.studioVersion}>UI v2 · prompt v{preview?.template_version ?? 1}</span>
              <p>{en ? "The preview and the run use the same server renderer." : "预览与真实执行使用同一个服务端渲染器。"}</p>
            </div>
          </nav>

          <section className={styles.studioForm} data-mobile={mobileView}>
            {tab === "identity" ? (
              <div className={styles.studioSection}>
                {!bot ? (
                  <>
                    <div className={styles.studioSectionHead}>
                      <div><span>{en ? "START FROM A PRESET" : "从预设开始"}</span><h3>{en ? "What should this assistant own?" : "这个助手负责什么？"}</h3></div>
                    </div>
                    <div className={styles.presetGrid}>
                      {PRESETS.map((preset) => (
                        <button key={preset.id} type="button" className={styles.presetCard} onClick={() => applyPreset(preset)}>
                          <span className={styles.presetIcon} aria-hidden="true">{preset.icon}</span>
                          <strong>{en ? preset.nameEn : preset.nameZh}</strong>
                          <small>{en ? preset.descEn : preset.descZh}</small>
                        </button>
                      ))}
                    </div>
                  </>
                ) : null}
                <div className={styles.studioSectionHead}>
                  <div><span>{en ? "IDENTITY" : "身份"}</span><h3>{en ? "Independent assistant, independent memory" : "独立助手，独立记忆"}</h3></div>
                  <p>{en ? "Each assistant gets its own prompt, sessions, memory and routines." : "每个助手拥有独立的提示词、会话、记忆和例行任务。"}</p>
                </div>
                <label className={styles.studioField}>
                  <span>{en ? "Assistant name" : "助手名称"}</span>
                  <input value={draft.name} onChange={(event) => update("name", event.target.value)} placeholder={en ? "e.g. Tencent watcher" : "例如：腾讯观察员"} autoFocus maxLength={80} />
                </label>
                <label className={styles.studioField}>
                  <span>{en ? "Short description" : "一句话描述"}</span>
                  <input value={draft.description} onChange={(event) => update("description", event.target.value)} placeholder={en ? "What is this assistant responsible for?" : "它长期负责什么？"} maxLength={500} />
                </label>
                <label className={styles.studioField}>
                  <span>{en ? "Role and responsibilities" : "身份与职责"} <small>{draft.instructions.length}/4000</small></span>
                  <textarea rows={6} value={draft.instructions} onChange={(event) => update("instructions", event.target.value)} placeholder={en ? "Describe the analyst this assistant should be, what it should prioritise, and how it should answer." : "描述它是什么分析师、优先看什么、应该怎样回答。这段内容会进入系统提示词，不会拼进用户输入。"} maxLength={4000} />
                </label>
                <div className={styles.studioField}>
                  <span>{en ? "Markets" : "覆盖市场"}</span>
                  <div className={styles.segmentRow}>
                    {(["us", "hk", "cn"] as const).map((market) => (
                      <button
                        key={market}
                        type="button"
                        className={draft.promptConfig.markets.includes(market) ? styles.segmentActive : undefined}
                        onClick={() => {
                          const set = new Set(draft.promptConfig.markets);
                          if (set.has(market)) set.delete(market); else set.add(market);
                          update("promptConfig", { ...draft.promptConfig, markets: set.size ? Array.from(set) : ["us"] });
                        }}
                      >
                        {market === "us" ? (en ? "US" : "美股") : market === "hk" ? (en ? "HK" : "港股") : en ? "A-share" : "A股"}
                      </button>
                    ))}
                  </div>
                </div>
                <div className={styles.studioField}>
                  <span>{en ? "Default answer style" : "默认回答风格"}</span>
                  <div className={styles.segmentRow}>
                    {(["concise", "standard", "detailed"] as const).map((style) => (
                      <button
                        key={style}
                        type="button"
                        className={draft.promptConfig.output_style === style ? styles.segmentActive : undefined}
                        onClick={() => update("promptConfig", { ...draft.promptConfig, output_style: style })}
                      >
                        {style === "concise" ? (en ? "Concise" : "简洁") : style === "standard" ? (en ? "Standard" : "标准") : en ? "Detailed" : "详细"}
                      </button>
                    ))}
                  </div>
                </div>
                <div className={styles.studioNotice}>
                  <strong>{en ? "System and user are separate." : "系统提示词与用户输入严格分开。"}</strong>
                  <p>{en ? "The role above becomes a system instruction. The current question stays in its own user message, which reduces prompt-injection risk." : "上面的职责会作为 system 指令注入；当前问题始终作为独立的 user 消息发送，降低提示词注入风险。"}</p>
                </div>
              </div>
            ) : null}

            {tab === "prompt" ? (
              <div className={styles.studioSection}>
                <div className={styles.studioSectionHead}>
                  <div><span>{en ? "PROMPT TEMPLATE" : "提示词模板"}</span><h3>{en ? "Compose, don’t hard-code" : "编排，而不是写死"}</h3></div>
                  <div className={styles.studioInlineActions}>
                    <button type="button" onClick={() => { setShowVariables((value) => !value); }}>{showVariables ? (en ? "Hide variables" : "收起变量") : en ? "Variables" : "变量"}</button>
                    <button type="button" onClick={() => update("promptTemplate", defaults?.template || draft.promptTemplate)} disabled={!defaults}>{en ? "Reset to default" : "恢复默认模板"}</button>
                  </div>
                </div>
                <p className={styles.studioLead}>{en ? "The platform rules, assistant identity, runtime metadata, memory and the user turn are rendered as separate layers. This template is the editable assistant layer." : "平台规则、助手身份、运行元数据、长期记忆和用户本轮输入是分层渲染的；这里编辑的是可变的助手层。"}</p>
                {showVariables ? (
                  <div className={styles.variableTray}>
                    {(preview?.known_variables ?? []).map((variable) => (
                      <button key={variable} type="button" onClick={() => insertVariable(variable)} title={preview?.variable_labels?.[variable] || variable}>
                        <code>{`{{${variable}}}`}</code>
                        <span>{preview?.variable_labels?.[variable] || variable}</span>
                      </button>
                    ))}
                  </div>
                ) : null}
                <div className={styles.templateEditor}>
                  <div className={styles.templateEditorBar}>
                    <span><i /> bot_system_prompt.md</span>
                    <small>{draft.promptTemplate.length}/8000</small>
                  </div>
                  <textarea
                    ref={templateRef}
                    rows={18}
                    value={draft.promptTemplate}
                    onChange={(event) => update("promptTemplate", event.target.value)}
                    spellCheck={false}
                    aria-label={en ? "Assistant system prompt template" : "助手系统提示词模板"}
                    maxLength={8000}
                  />
                </div>
                {preview?.unknown_variables?.length ? (
                  <div className={styles.studioWarning} role="alert">
                    <strong>{en ? "Unknown variables" : "未知变量"}</strong>
                    <p>{preview.unknown_variables.map((item) => `{{${item}}}`).join("、")} {en ? "will be left as literal text." : "会保留为普通文本，不会被替换。"}</p>
                  </div>
                ) : null}
                <div className={styles.studioNotice}>
                  <strong>{en ? "Platform guardrails are not editable." : "平台保护规则不可编辑。"}</strong>
                  <p>{en ? "Evidence, citation, no-trading and isolation rules are applied outside this template and cannot be disabled by an assistant." : "证据、引用、禁止交易和助手隔离规则在模板之外强制执行，不能被单个助手关闭。"}</p>
                </div>
              </div>
            ) : null}

            {tab === "abilities" ? (
              <div className={styles.studioSection}>
                <div className={styles.studioSectionHead}>
                  <div><span>{en ? "CAPABILITIES" : "能力"}</span><h3>{en ? "Toggles remove tools, not just words" : "开关会移除工具，而不只是改提示词"}</h3></div>
                  <p>{en ? `${enabledCount} of ${allCount || 20} tools active` : `当前实际启用 ${enabledCount} / ${allCount || 20} 个工具`}</p>
                </div>
                <div className={styles.capabilityList}>
                  {CAPABILITY_META.map((capability) => {
                    const active = draft.promptConfig.capabilities[capability.key];
                    const disabled = (preview?.disabled_tools ?? []).filter((tool) => capability.tools.includes(tool));
                    return (
                      <div key={capability.key} className={styles.capabilityCard} data-active={active ? "true" : "false"}>
                        <span className={styles.capabilityIcon} aria-hidden="true">{capability.icon}</span>
                        <div className={styles.capabilityCopy}>
                          <strong>{en ? capability.en : capability.zh}</strong>
                          <p>{en ? capability.descEn : capability.descZh}</p>
                          <small>
                            {active
                              ? `${capability.tools.length} ${en ? "tools" : "个工具"}`
                              : disabled.length
                                ? `${disabled.length} ${en ? "tools removed" : "个工具已移除"}`
                                : en ? "No tools in this group" : "该组没有工具"}
                          </small>
                        </div>
                        <label className={styles.toggle}>
                          <input type="checkbox" checked={active} aria-label={`${en ? "Enable" : "启用"}${en ? capability.en : capability.zh}`} onChange={(event) => updateCapability(capability.key, event.target.checked)} />
                          <span aria-hidden="true" />
                          <em>{active ? (en ? "On" : "开") : en ? "Off" : "关"}</em>
                        </label>
                      </div>
                    );
                  })}
                </div>
                <div className={styles.studioSectionHead}>
                  <div><span>{en ? "PERMISSIONS" : "权限"}</span><h3>{en ? "What may persist or act" : "什么可以持久化或主动行动"}</h3></div>
                </div>
                <div className={styles.permissionGrid}>
                  <label className={styles.permissionCard}>
                    <input type="checkbox" checked={draft.promptConfig.memory_enabled} onChange={(event) => update("promptConfig", { ...draft.promptConfig, memory_enabled: event.target.checked })} />
                    <span><strong>{en ? "Long-term memory" : "长期记忆"}</strong><small>{en ? "Read and write this assistant’s confirmed memories." : "读写这个助手已确认的长期记忆。"}</small></span>
                  </label>
                  <label className={styles.permissionCard}>
                    <input type="checkbox" checked={draft.promptConfig.routines_enabled} onChange={(event) => update("promptConfig", { ...draft.promptConfig, routines_enabled: event.target.checked })} />
                    <span><strong>{en ? "Routines" : "例行任务"}</strong><small>{en ? "Allow scheduled monitoring and recurring runs." : "允许创建定时监控与周期运行。"}</small></span>
                  </label>
                  <label className={styles.permissionCard}>
                    <input type="checkbox" checked={draft.promptConfig.proactive_enabled} onChange={(event) => update("promptConfig", { ...draft.promptConfig, proactive_enabled: event.target.checked })} />
                    <span><strong>{en ? "Proactive alerts" : "主动提醒"}</strong><small>{en ? "Allow this assistant to push alerts into Work and external channels." : "允许它把提醒推送到 Work 与外部渠道。"}</small></span>
                  </label>
                </div>
                <div className={styles.boundaryCard}>
                  <span>{en ? "HARD BOUNDARIES" : "硬边界"}</span>
                  <ul>
                    <li>{en ? "No trading, order placement or broker account changes." : "禁止交易、下单或修改券商账户。"}</li>
                    <li>{en ? "No shell, server filesystem or secret access." : "禁止 Shell、服务器文件系统或密钥访问。"}</li>
                    <li>{en ? "No cross-assistant conversations, memories or artifacts." : "禁止跨助手读取会话、记忆或产物。"}</li>
                    <li>{en ? "Web and document content is untrusted data, never instructions." : "网页与文档内容只作为不可信资料，不能当作指令。"}</li>
                  </ul>
                </div>
              </div>
            ) : null}

            {tab === "memory" ? (
              <div className={styles.studioSection}>
                <div className={styles.studioSectionHead}>
                  <div><span>{en ? "MEMORY" : "记忆"}</span><h3>{en ? "Only this assistant’s confirmed context" : "只使用这个助手已确认的上下文"}</h3></div>
                  <p>{en ? "Retrieved memories are appended as a separate system block, not mixed with the question." : "召回的长期记忆会作为独立的 system 区块追加，不混进用户问题。"}</p>
                </div>
                <label className={styles.memoryToggleCard}>
                  <input type="checkbox" checked={draft.promptConfig.memory_enabled} onChange={(event) => update("promptConfig", { ...draft.promptConfig, memory_enabled: event.target.checked })} />
                  <div>
                    <strong>{en ? "Enable long-term memory for this assistant" : "为这个助手启用长期记忆"}</strong>
                    <p>{en ? "When off, the run neither reads nor writes memories, and the memory block is removed from the prompt." : "关闭后，本次运行既不读取也不写入记忆，提示词中也不会出现记忆区块。"}</p>
                  </div>
                </label>
                {!bot ? (
                  <div className={styles.studioNotice}><strong>{en ? "Save first" : "先保存"}</strong><p>{en ? "A new assistant has no memories until its first conversation." : "新助手还没有记忆；第一次对话后才会产生候选记忆。"}</p></div>
                ) : (
                  <>
                    <div className={styles.memoryStats}>
                      <div><strong>{memories?.memories.length ?? 0}</strong><span>{en ? "confirmed" : "已确认"}</span></div>
                      <div><strong>{pendingCandidates}</strong><span>{en ? "pending" : "待确认"}</span></div>
                      <div><strong>{preview?.memory_count ?? 0}</strong><span>{en ? "in preview" : "预览中"}</span></div>
                    </div>
                    {memoryError ? <p className={styles.studioWarning}>{memoryError}</p> : null}
                    <div className={styles.memoryList}>
                      {recentMemories.map((memory) => (
                        <div key={memory.id} className={styles.memoryRow}>
                          <span>{memory.kind === "watch_object" ? (en ? "Watched" : "关注") : memory.kind === "preference" ? (en ? "Preference" : "偏好") : memory.kind === "holding" ? (en ? "Holding" : "持仓") : memory.kind === "confirmed_viewpoint" ? (en ? "View" : "观点") : en ? "Question" : "问题"}</span>
                          <p>{memory.text}</p>
                          <small>{formatDateTime(memory.updated_at)}</small>
                        </div>
                      ))}
                      {!recentMemories.length ? <p className={styles.studioEmpty}>{en ? "No confirmed memories yet." : "还没有已确认的长期记忆。"}</p> : null}
                    </div>
                    <button type="button" className={styles.studioSecondaryWide} onClick={onOpenMemory}>{en ? "Open full memory manager" : "打开完整记忆管理"} ↗</button>
                  </>
                )}
              </div>
            ) : null}

            {tab === "delivery" ? (
              <div className={styles.studioSection}>
                <div className={styles.studioSectionHead}>
                  <div><span>{en ? "DELIVERY" : "投递"}</span><h3>{en ? "Where routine results go" : "例行任务结果发到哪里"}</h3></div>
                  <p>{en ? "In-app Work is always complete. Webhook and email are best-effort external copies." : "站内 Work 始终完整；Webhook 与邮箱只是尽力而为的外部副本。"}</p>
                </div>
                <div className={styles.studioGrid2}>
                  <label className={styles.studioField}>
                    <span>{en ? "Webhook URL" : "Webhook 地址"}</span>
                    <input type="url" value={draft.webhookUrl} onChange={(event) => update("webhookUrl", event.target.value)} placeholder={bot?.webhook_configured ? (en ? "Configured; enter a new URL to replace" : "已配置；输入新地址可替换") : "https://example.com/hook"} />
                  </label>
                  <label className={styles.studioField}>
                    <span>{en ? "Notification email" : "通知邮箱"}</span>
                    <input type="email" value={draft.notificationEmail} onChange={(event) => update("notificationEmail", event.target.value)} placeholder="alerts@example.com" />
                  </label>
                </div>
                <div className={styles.studioGrid2}>
                  <label className={styles.studioField}>
                    <span>{en ? "External delivery" : "外部发送"}</span>
                    <select value={draft.digestMode} onChange={(event) => update("digestMode", event.target.value as "immediate" | "daily")}>
                      <option value="immediate">{en ? "Immediate" : "逐条即时"}</option>
                      <option value="daily">{en ? "Daily digest" : "每日摘要"}</option>
                    </select>
                  </label>
                  {draft.digestMode === "daily" ? (
                    <label className={styles.studioField}>
                      <span>{en ? "Digest hour" : "摘要发送时间"}</span>
                      <input type="time" value={draft.digestHour} onChange={(event) => update("digestHour", event.target.value)} />
                    </label>
                  ) : null}
                </div>
                <div className={styles.studioGrid2}>
                  <label className={styles.studioField}>
                    <span>{en ? "Watchlist report style" : "自选股报告样式"}</span>
                    <select value={draft.reportStyle} onChange={(event) => update("reportStyle", event.target.value as "brief" | "standard")}>
                      <option value="brief">{en ? "Brief" : "简报"}</option>
                      <option value="standard">{en ? "Standard" : "标准"}</option>
                    </select>
                  </label>
                  <label className={styles.studioField}>
                    <span>{en ? "Max alerts per day" : "每日最多提醒"}</span>
                    <input type="number" min={1} max={200} value={draft.maxAlertsPerDay} onChange={(event) => update("maxAlertsPerDay", Number(event.target.value))} />
                  </label>
                </div>
                <div className={styles.studioGrid2}>
                  <label className={styles.studioField}>
                    <span>{en ? "Quiet hours start" : "免打扰开始"}</span>
                    <input type="time" value={draft.quietHoursStart} onChange={(event) => update("quietHoursStart", event.target.value)} />
                  </label>
                  <label className={styles.studioField}>
                    <span>{en ? "Quiet hours end" : "免打扰结束"}</span>
                    <input type="time" value={draft.quietHoursEnd} onChange={(event) => update("quietHoursEnd", event.target.value)} />
                  </label>
                </div>
                <label className={styles.checkboxLine}>
                  <input type="checkbox" checked={draft.marketHoursOnly} onChange={(event) => update("marketHoursOnly", event.target.checked)} />
                  {en ? "Price checks only during market hours" : "价格监控仅在交易时段检查"}
                </label>
                {inlineNote ? <p className={styles.studioInlineNote}>{inlineNote}</p> : null}
                <div className={styles.studioInlineActions}>
                  <button
                    type="button"
                    onClick={() => void testChannels()}
                    disabled={!bot || testing || !deliveryConfigured}
                    title={!deliveryConfigured ? (en ? "Configure webhook or email first" : "先去投递里配置 webhook/邮箱") : undefined}
                  >{testing ? (en ? "Sending…" : "发送中…") : en ? "Send test" : "发送测试"}</button>
                  <button type="button" onClick={() => void openDigest()} disabled={!bot}>{en ? "Preview digest" : "预览摘要"}</button>
                </div>
                {!deliveryConfigured ? (
                  <p className={styles.studioInlineNote} role="note">
                    {en
                      ? "Webhook/email not set yet. Fill one above in Delivery, save, then send a test."
                      : "还没配置 webhook/邮箱。先在上方投递里填一项并保存，再发测试。"}
                  </p>
                ) : null}
                {bot ? (
                  <div className={styles.closureBlock}>
                    <div className={styles.studioSubHead}><strong>{en ? "Ad-hoc market closures" : "临时休市"}</strong><span>{closures.length}</span></div>
                    <div className={styles.closureForm}>
                      <select value={closureDraft.market} onChange={(event) => setClosureDraft((current) => ({ ...current, market: event.target.value as "us" | "hk" | "cn" }))}>
                        <option value="us">US</option><option value="hk">HK</option><option value="cn">CN</option>
                      </select>
                      <input type="date" value={closureDraft.date} onChange={(event) => setClosureDraft((current) => ({ ...current, date: event.target.value }))} />
                      <input value={closureDraft.reason} onChange={(event) => setClosureDraft((current) => ({ ...current, reason: event.target.value }))} placeholder={en ? "Reason (optional)" : "原因（可选）"} />
                      <button type="button" onClick={() => void addClosure()} disabled={!closureDraft.date || closureSaving}>＋</button>
                    </div>
                    {closures.map((closure) => (
                      <div key={closure.id} className={styles.closureRow}>
                        <span>{closure.market.toUpperCase()} · {closure.closure_date}{closure.reason ? ` · ${closure.reason}` : ""}</span>
                        <button type="button" onClick={() => void removeClosure(closure)} aria-label={en ? "Remove closure" : "删除休市"}>×</button>
                      </div>
                    ))}
                    {!closures.length ? <p className={styles.studioEmpty}>{en ? "No ad-hoc closures." : "没有临时休市。"}</p> : null}
                  </div>
                ) : null}
              </div>
            ) : null}

            {tab === "advanced" ? (
              <div className={styles.studioSection}>
                <div className={styles.studioSectionHead}>
                  <div><span>{en ? "ADVANCED" : "高级"}</span><h3>{en ? "Budget and lifecycle" : "预算与生命周期"}</h3></div>
                  <p>{en ? "Runs above the estimated budget ask for confirmation before the model call." : "估算超过预算的运行会在调用模型前请求你确认。"}</p>
                </div>
                <label className={styles.studioField}>
                  <span>{en ? "Approval threshold (credits)" : "审批阈值（额度）"}</span>
                  <div className={styles.thresholdRow}>
                    <input type="range" min={0} max={20} value={draft.threshold} onChange={(event) => update("threshold", Number(event.target.value))} />
                    <output>{draft.threshold}</output>
                  </div>
                  <small>{en ? "Higher threshold = fewer confirmations, higher possible spend. 0 confirms every model call." : "阈值越高，确认越少但可能花费越高；0 表示每次模型调用都要确认。"}</small>
                </label>
                <div className={styles.botMetaGrid}>
                  <div><span>{en ? "Assistant ID" : "助手 ID"}</span><code>{bot?.id || "—"}</code></div>
                  <div><span>{en ? "Prompt version" : "提示词版本"}</span><code>v{bot?.prompt_version ?? 1} · {draft.promptTemplate.length} chars</code></div>
                  <div><span>{en ? "Created" : "创建时间"}</span><code>{formatDateTime(bot?.created_at)}</code></div>
                  <div><span>{en ? "Updated" : "更新时间"}</span><code>{formatDateTime(bot?.updated_at)}</code></div>
                </div>
                {isEdit && !bot?.is_default ? (
                  <div className={styles.dangerZone}>
                    <div><strong>{en ? "Delete this assistant" : "删除这个助手"}</strong><p>{en ? "Conversations and memories move to the default assistant. This cannot be undone from assistant settings." : "对话和记忆会转移到默认助手；此操作无法从助手设置撤销。"}</p></div>
                    <button type="button" onClick={() => void remove()} disabled={saving}>{en ? "Delete assistant" : "删除助手"}</button>
                  </div>
                ) : null}
              </div>
            ) : null}
          </section>

          <aside className={styles.studioPreview} data-mobile={mobileView} aria-label={en ? "Final prompt preview" : "最终提示词预览"}>
            <div className={styles.previewHead}>
              <div>
                <span>{en ? "FINAL PROMPT" : "最终提示词"}</span>
                <h3>{en ? "Rendered by the server" : "服务端同源渲染"}</h3>
                <p className={styles.previewSub}>{en ? "Platform + assistant + memory + history + user. Language, research-mode and UI-state instructions are appended per run." : "平台 + 助手 + 记忆 + 历史 + 用户层；语言、研究模式与 UI 状态由服务端按本轮追加。"}</p>
              </div>
              <span className={styles.previewLive} data-loading={previewLoading ? "true" : "false"}><i />{previewLoading ? (en ? "Rendering" : "渲染中") : (en ? "Live" : "实时")}</span>
            </div>
            <div className={styles.previewStats}>
              <span><strong>{preview?.estimated_tokens ?? 0}</strong>{en ? " est. tokens" : " 估算 tokens"}</span>
              <span><strong>{preview?.total_chars ?? 0}</strong>{en ? " chars" : " 字符"}</span>
              <span><strong>{enabledCount}</strong>{en ? " tools" : " 工具"}</span>
              <button type="button" onClick={() => void copyPrompt()} disabled={!preview}>{copied ? (en ? "Copied" : "已复制") : en ? "Copy" : "复制"}</button>
            </div>
            {previewError ? <div className={styles.previewError}>{previewError}</div> : null}
            <div className={styles.previewScroll}>
              {(preview?.segments ?? []).map((segment) => {
                const isPlatform = segment.id === "platform";
                const isUser = segment.role === "user";
                if (isPlatform) {
                  return (
                    <details key={segment.id} className={styles.previewSegment} data-role="system">
                      <summary>
                        <span className={styles.segmentRole}>SYSTEM</span>
                        <strong>{en ? "Platform guardrails" : "平台保护规则"}</strong>
                        <small>{segment.chars} chars · {en ? "read-only" : "只读"}</small>
                      </summary>
                      <pre className={styles.segmentContent}>{segment.content}</pre>
                    </details>
                  );
                }
                return (
                  <div key={segment.id} className={styles.previewSegment} data-role={isUser ? "user" : "system"} data-editable={segment.editable ? "true" : "false"}>
                    <div className={styles.segmentHead}>
                      <span className={styles.segmentRole}>{isUser ? "USER" : "SYSTEM"}</span>
                      <strong>{segment.label}</strong>
                      <small>{segment.chars} chars{segment.editable ? (en ? " · editable" : " · 可编辑") : ""}</small>
                    </div>
                    <pre className={styles.segmentContent}>{segment.content}</pre>
                  </div>
                );
              })}
              {!preview && !previewError ? <p className={styles.studioEmpty}>{en ? "Preparing the preview…" : "正在准备预览…"}</p> : null}
            </div>
            <div className={styles.previewQuery}>
              <label htmlFor="studio-preview-query">{en ? "Try a user question" : "试一条用户输入"}</label>
              <input id="studio-preview-query" value={previewQuery} onChange={(event) => setPreviewQuery(event.target.value)} placeholder={en ? "e.g. Why did Tencent move today?" : "例如：腾讯今天为什么上涨？"} maxLength={4000} />
              <p>{en ? "It renders in the user layer only; it is never concatenated into the system prompt." : "它只会出现在 user 层，不会拼进系统提示词。"}</p>
            </div>
            <div className={styles.previewFoot}>
              <span>{en ? "Enabled tools" : "已启用工具"} {enabledCount}/{allCount || 20}</span>
              <span>{en ? "Unknown vars" : "未知变量"} {preview?.unknown_variables.length ?? 0}</span>
              <span>v{preview?.template_version ?? 1}</span>
            </div>
          </aside>
        </div>
      </div>
      {digest ? (
        <div className={styles.studioDialogBackdrop} role="presentation" onClick={(event) => { event.stopPropagation(); setDigest(null); }}>
          <div className={styles.studioDialog} role="dialog" aria-modal="true" aria-labelledby="studio-digest-title" onClick={(event) => event.stopPropagation()}>
            <div className={styles.studioDialogHead}><h3 id="studio-digest-title">{digest.title}</h3><button type="button" onClick={() => setDigest(null)} aria-label={en ? "Close" : "关闭"}>×</button></div>
            <p>{digest.unread_count} {en ? "unread alert(s)" : "条未读提醒"}</p>
            <pre>{digest.body}</pre>
            <div className={styles.studioDialogActions}>
              <button type="button" onClick={() => setDigest(null)}>{en ? "Close" : "关闭"}</button>
              <button type="button" onClick={() => void sendDigest()} disabled={digestSending}>{digestSending ? (en ? "Sending…" : "发送中…") : en ? "Send now" : "立即发送摘要"}</button>
            </div>
          </div>
        </div>
      ) : null}
      {studioConfirm ? (
        <div className={styles.studioDialogBackdrop} role="presentation" onClick={(event) => { event.stopPropagation(); setStudioConfirm(null); }}>
          <div className={styles.studioDialog} role="dialog" aria-modal="true" aria-labelledby="studio-confirm-title" onClick={(event) => event.stopPropagation()}>
            <div className={styles.studioDialogHead}>
              <h3 id="studio-confirm-title">{studioConfirm.title}</h3>
              <button type="button" onClick={() => setStudioConfirm(null)} aria-label={en ? "Close" : "关闭"}>×</button>
            </div>
            <p>{studioConfirm.body}</p>
            <div className={styles.studioDialogActions}>
              <button type="button" onClick={() => setStudioConfirm(null)}>{en ? "Cancel" : "取消"}</button>
              <button
                type="button"
                className={studioConfirm.danger ? styles.dangerButton : undefined}
                disabled={studioConfirming}
                onClick={() => {
                  if (studioConfirmLockRef.current) return;
                  const confirm = studioConfirm.onConfirm;
                  studioConfirmLockRef.current = true;
                  setStudioConfirming(true);
                  setStudioConfirm(null);
                  confirm();
                }}
              >
                {studioConfirming ? (en ? "Working…" : "处理中…") : studioConfirm.confirmLabel}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
