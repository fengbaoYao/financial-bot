"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

import { useLanguage } from "@/components/language-provider";
import type { AgentResponse } from "@/lib/types";
import { researchExport } from "@/lib/research-export";
import { researchWorkLabel, toolActivityOutcome, type ToolActivity } from "@/lib/research-activity";
export type { ToolActivity } from "@/lib/research-activity";

const tools: Record<string, [string, string]> = {
  search_stocks: ["核验股票身份", "Resolve company identity"],
  get_sec_company_data: ["获取 SEC 财报", "Retrieve SEC filings and facts"],
  compare_companies: ["核对财务比较口径", "Compare financial periods and units"],
  get_stock_analysis: ["检查行情与技术指标", "Check prices and indicators"],
  get_stock_news: ["查找公司新闻", "Retrieve company news"],
  get_global_news: ["查找宏观新闻", "Retrieve macro news"],
  get_portfolio_analysis: ["检查持仓", "Review portfolio"],
  list_hotspots: ["检查市场热点", "Review market themes"],
  get_hotspot_detail: ["检查主题资料", "Review theme evidence"],
  web_search: ["检索公开资料", "Search public sources"],
  read_public_source: ["读取来源原文", "Read original source"],
  review_policy_exposure: ["核对政策与公司影响", "Review policy and company exposure"],
  manage_watchlist: ["更新自选股", "Update watchlist"],
  manage_research_goal: ["更新研究目标", "Update research goals"],
  research_company: ["分工研究公司", "Coordinate company research"],
  review_company_position: ["核对产业优势与财务韧性", "Review industry position and resilience"],
  review_catalysts_risks: ["核对催化与反向风险", "Review catalysts and contrary evidence"],
  get_cninfo_announcements: ["核对巨潮公告", "Check CNINFO announcements"],
  get_consensus_estimates: ["核对一致预期", "Check consensus estimates"],
  get_index_constituents: ["核对指数成分", "Check index constituents"],
  get_financial_statements: ["核对财报三表", "Check financial statements"],
  get_company_fundamentals: ["核对外部财报", "Check company financials"],
  get_analyst_estimates: ["核对分析师预期", "Check analyst estimates"],
  model_knowledge_fill: ["模型补齐（未核验）", "Model fill (unverified)"],
  ifind_smart_query: ["同花顺智能查询", "iFinD smart query"],
};

const metricLabels: Record<string, [string, string]> = {
  Revenue: ["营业收入", "Revenue"], NetIncomeLoss: ["净利润", "Net income"], ProfitLoss: ["净利润", "Net income"],
  Revenues: ["营业收入", "Revenue"], RevenueFromContractWithCustomerExcludingAssessedTax: ["营业收入", "Revenue"], RevenueFromContractWithCustomerIncludingAssessedTax: ["营业收入", "Revenue"],
  EarningsPerShareDiluted: ["稀释每股收益", "Diluted EPS"], DilutedEarningsLossPerShare: ["稀释每股收益", "Diluted EPS"],
  Assets: ["总资产", "Assets"], CashAndCashEquivalents: ["现金及等价物", "Cash and equivalents"], CashAndCashEquivalentsAtCarryingValue: ["现金及等价物", "Cash and equivalents"],
  Equity: ["股东权益", "Equity"], StockholdersEquity: ["股东权益", "Equity"], NetCashProvidedByUsedInOperatingActivities: ["经营现金流", "Operating cash flow"], CashFlowsFromUsedInOperatingActivities: ["经营现金流", "Operating cash flow"],
  "Net income": ["净利润", "Net income"], "Diluted EPS": ["稀释每股收益", "Diluted EPS"], "Operating cash flow": ["经营现金流", "Operating cash flow"],
};

export function researchToolLabel(tool: string, locale: "zh" | "en") {
  return tools[tool]?.[locale === "en" ? 1 : 0] ?? (locale === "en" ? "Research tool" : "研究工具");
}

type GlyphKind =
  | "identity"
  | "news"
  | "chart"
  | "research"
  | "web"
  | "read"
  | "hotspot"
  | "risk"
  | "watch"
  | "compare"
  | "sec"
  | "goal"
  | "fill"
  | "generic";

const glyphByTool: Record<string, GlyphKind> = {
  search_stocks: "identity",
  get_sec_company_data: "sec",
  compare_companies: "compare",
  get_stock_analysis: "chart",
  get_stock_news: "news",
  get_global_news: "news",
  get_portfolio_analysis: "chart",
  list_hotspots: "hotspot",
  get_hotspot_detail: "hotspot",
  web_search: "web",
  search_public_sources: "web",
  read_public_source: "read",
  review_policy_exposure: "risk",
  review_catalysts_risks: "risk",
  review_company_position: "research",
  research_company: "research",
  manage_watchlist: "watch",
  manage_research_goal: "goal",
  get_cninfo_announcements: "read",
  get_consensus_estimates: "goal",
  get_index_constituents: "compare",
  get_financial_statements: "read",
  get_company_fundamentals: "read",
  get_analyst_estimates: "goal",
  model_knowledge_fill: "fill",
};

function ToolGlyph({ tool }: { tool: string }) {
  const kind = glyphByTool[tool] ?? "generic";
  const paths: Record<GlyphKind, ReactNode> = {
    identity: (
      <>
        <circle cx="10.5" cy="10.5" r="6" />
        <path d="M10.5 3v3M10.5 15v3M3 10.5h3M15 10.5h3M16.5 16.5 21 21" />
      </>
    ),
    news: (
      <>
        <path d="M4 5h13v14H6a2 2 0 0 1-2-2Z" />
        <path d="M17 8h3v9a2 2 0 0 1-2 2h-1M7 9h7M7 12h7M7 15h4" />
      </>
    ),
    chart: (
      <>
        <path d="M4 19V5M4 19h16" />
        <path d="m7 15 4-4 3 2 5-6" />
      </>
    ),
    research: (
      <>
        <circle cx="6" cy="6" r="2" />
        <circle cx="18" cy="7" r="2" />
        <circle cx="12" cy="18" r="2" />
        <path d="m8 6 8 1M7 8l4 8M17 9l-4 7" />
      </>
    ),
    web: (
      <>
        <circle cx="12" cy="12" r="8" />
        <path d="M4 12h16M12 4c2.4 2.2 3.6 4.9 3.6 8S14.4 17.8 12 20c-2.4-2.2-3.6-4.9-3.6-8S9.6 6.2 12 4Z" />
      </>
    ),
    read: (
      <>
        <path d="M6 3h8l4 4v14H6Z" />
        <path d="M14 3v5h4M9 12h6M9 15h6M9 18h3" />
      </>
    ),
    hotspot: (
      <>
        <path d="M12 21c4 0 7-2.8 7-6.5 0-3-1.8-5.2-4-7.5-.6 2-1.7 3-3 4 .3-3-1-6-3-8 .2 3.5-4 6-4 11.5C5 18.2 8 21 12 21Z" />
        <path d="M12 21c1.7 0 3-1.3 3-3s-1.3-3-3-4c-1.7 1-3 2.3-3 4s1.3 3 3 3Z" />
      </>
    ),
    risk: (
      <>
        <path d="M12 3 2.8 20h18.4Z" />
        <path d="M12 9v5M12 17.2v.1" />
      </>
    ),
    watch: <path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1 6.2-5.5-2.9-5.5 2.9 1-6.2L3 9.6l6.2-.9Z" />,
    compare: (
      <>
        <rect x="3" y="4" width="7" height="16" rx="1" />
        <rect x="14" y="4" width="7" height="16" rx="1" />
        <path d="M5 9h3M16 9h3M5 13h3M16 13h3" />
      </>
    ),
    sec: (
      <>
        <path d="M4 21V6l8-3 8 3v15" />
        <path d="M2 21h20M8 10h2M14 10h2M8 14h2M14 14h2M10 21v-3h4v3" />
      </>
    ),
    goal: (
      <>
        <circle cx="12" cy="12" r="8" />
        <circle cx="12" cy="12" r="3.5" />
        <path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
      </>
    ),
        fill: (
      <>
        <path d="M10.5 3.5 12 7.5l4.2.4-3.2 2.8.9 4.1-3.9-2.2-3.9 2.2.9-4.1-3.2-2.8L9 7.5Z" />
      </>
    ),
    generic: (
      <>
        <path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8Z" />
        <path d="m18 16 .8 2.2L21 19l-2.2.8L18 22l-.8-2.2L15 19l2.2-.8Z" />
      </>
    ),
  };
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {paths[kind]}
    </svg>
  );
}

type ToolGroup = {
  key: string;
  tool: string;
  label: string;
  steps: ToolActivity[];
  status: "running" | "completed" | "unavailable" | "warning" | "skipped";
  count: number;
  completedCount: number;
  failedCount: number;
  skippedCount: number;
  activeEndpoint?: string;
  endpoints: string[];
  outcome: ReturnType<typeof toolActivityOutcome>;
};

function buildToolGroups(steps: ToolActivity[], locale: "zh" | "en"): ToolGroup[] {
  const en = locale === "en";
  const groups: ToolGroup[] = [];
  const byTool = new Map<string, ToolGroup>();
  for (const step of steps) {
    let group = byTool.get(step.tool);
    if (!group) {
      group = {
        key: step.tool,
        tool: step.tool,
        label: researchToolLabel(step.tool, locale),
        steps: [],
        status: "completed",
        count: 0,
        completedCount: 0,
        failedCount: 0,
        skippedCount: 0,
        endpoints: [],
        outcome: { label: "", detail: "", recovered: false },
      };
      byTool.set(step.tool, group);
      groups.push(group);
    }
    group.steps.push(step);
  }
  for (const group of groups) {
    const last = group.steps[group.steps.length - 1];
    const running = group.steps.some((step) => step.status === "running");
    const skipped = group.steps.filter((step) => step.status === "unavailable" && step.reasonCode === "not_configured");
    const failed = group.steps.filter((step) => step.status === "unavailable" && step.reasonCode !== "not_configured");
    const completed = group.steps.filter((step) => step.status === "completed");
    const recovered = last.status === "completed" && !last.reasonCode
      && group.steps.slice(0, -1).some((step) => step.status === "unavailable" || Boolean(step.reasonCode));
    group.count = group.steps.length;
    group.completedCount = completed.length;
    group.failedCount = failed.length;
    group.skippedCount = skipped.length;
    group.endpoints = Array.from(new Set(group.steps.flatMap((step) => [
      ...(step.endpoints ?? []),
      ...(step.endpoint ? [step.endpoint] : []),
    ])));
    group.activeEndpoint = group.endpoints[group.endpoints.length - 1];
    group.outcome = toolActivityOutcome(last, locale, recovered);
    if (running) {
      group.status = "running";
    } else if (completed.length > 0 && failed.length > 0) {
      group.status = "warning";
      group.outcome = {
        label: en ? `Partial ${completed.length}/${group.count}` : `部分完成 ${completed.length}/${group.count}`,
        detail: en ? `${failed.length} call(s) failed; completed results are retained.` : `${failed.length} 次调用失败，已完成的结果仍然保留。`,
        recovered: false,
      };
    } else if (completed.length > 0) {
      if (last.reasonCode && last.reasonCode !== "not_configured" && !recovered) {
        group.status = "warning";
      } else {
        group.status = "completed";
        if (skipped.length) {
          group.outcome = { label: en ? "Done (skipped unconfigured)" : "完成（未配置项已跳过）", detail: "", recovered: false };
        }
      }
    } else if (failed.length > 0) {
      group.status = "unavailable";
    } else if (skipped.length > 0) {
      group.status = "skipped";
      group.outcome = { label: en ? "Not configured (skipped)" : "未配置（已跳过）", detail: "", recovered: false };
    } else if (!recovered && last.reasonCode) {
      group.status = "warning";
    } else {
      group.status = "completed";
    }
  }
  return groups;
}

export function ResearchTools({ steps, phase, workSeconds }: { steps: ToolActivity[]; phase?: string; workSeconds?: number }) {
  const { locale } = useLanguage();
  const en = locale === "en";
  const worked = researchWorkLabel(workSeconds, locale);
  const groups = buildToolGroups(steps, locale);
  const [open, setOpen] = useState(Boolean(phase));
  const [merging, setMerging] = useState<Record<string, boolean>>({});
  const previousStatus = useRef<Record<string, string>>({});
  const signature = groups.map((group) => `${group.key}:${group.status}:${group.count}`).join("|");

  useEffect(() => {
    if (phase) setOpen(true);
  }, [phase]);

  useEffect(() => {
    const next: Record<string, string> = {};
    const justMerged: string[] = [];
    for (const group of groups) {
      next[group.key] = group.status;
      if (previousStatus.current[group.key] && previousStatus.current[group.key] !== group.status && group.status !== "running") {
        justMerged.push(group.key);
      }
    }
    previousStatus.current = next;
    if (!justMerged.length) return;
    setMerging((current) => {
      const updated = { ...current };
      for (const key of justMerged) updated[key] = true;
      return updated;
    });
    const timer = window.setTimeout(() => {
      setMerging((current) => {
        const updated = { ...current };
        for (const key of justMerged) delete updated[key];
        return updated;
      });
    }, 900);
    return () => window.clearTimeout(timer);
    // The signature captures status/count changes without depending on the array identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);

  if (!steps.length && !phase) return worked ? <p className="research-tool-activity">{worked}</p> : null;

  const summaryLabel = phase || worked || (en ? "Tools used" : "调用的工具");
  const countLabel = steps.length
    ? `${steps.length} ${en ? "tools" : "个工具"}${groups.length ? ` · ${groups.length} ${en ? "groups" : "组"}` : ""}`
    : "";

  return (
    <details
      className="research-tool-activity agent-run-timeline"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary aria-live={phase ? "polite" : undefined}>
        <span className="agent-run-summary-icon" aria-hidden="true">
          <ToolGlyph tool={groups.find((group) => group.status === "running")?.tool ?? "generic"} />
        </span>
        <span className="agent-run-summary-label">
          {phase ? <i className="research-stream-dot" aria-hidden="true" /> : null}
          {summaryLabel}
        </span>
        {countLabel ? <span className="agent-run-summary-count">{countLabel}</span> : null}
      </summary>
      <ol className="agent-run-list">
        {!groups.length && phase ? (
          <li className="agent-run-item agent-run-placeholder" data-status="running">
            <span className="agent-run-rail"><span className="agent-run-icon"><ToolGlyph tool="generic" /></span></span>
            <div className="agent-run-body">
              <div className="agent-run-title">
                <span className="agent-run-label">{en ? "Preparing the first tool call" : "正在准备第一次工具调用"}</span>
                <span className="agent-run-wave" aria-hidden="true"><i /><i /><i /></span>
              </div>
            </div>
          </li>
        ) : null}
        {groups.map((group, index) => (
          <li
            key={group.key}
            className={`agent-run-item${merging[group.key] ? " is-merging" : ""}${groups[index + 1]?.status === "running" ? " is-lead-in" : ""}`}
            data-status={group.status}
          >
            <span className="agent-run-rail" aria-hidden="true">
              <span className="agent-run-icon"><ToolGlyph tool={group.tool} /></span>
            </span>
            <div className="agent-run-body">
              <div className="agent-run-title">
                <span className="agent-run-label">{group.label}</span>
                {group.count > 1 ? <span className="agent-run-count">×{group.count}</span> : null}
                <span className="agent-run-status" data-status={group.outcome.recovered ? "retried" : group.status}>
                  {group.status === "running" ? (en ? "Running" : "执行中") : group.outcome.label}
                </span>
              </div>
              {group.status === "running" ? (
                <div className="agent-run-live">
                  <span>{en ? "Fetching" : "正在获取"}</span>
                  {group.activeEndpoint ? <code className="agent-run-endpoint">{group.activeEndpoint}</code> : <span>{en ? "source interface" : "数据接口"}</span>}
                  <span className="agent-run-wave" aria-hidden="true"><i /><i /><i /></span>
                </div>
              ) : null}
              {group.status !== "running" && group.outcome.detail ? (
                <p className="agent-run-detail">{group.outcome.detail}</p>
              ) : null}
              {group.status === "running" && group.endpoints.length > 1 ? (
                <div className="agent-run-endpoint-history" aria-hidden="true">
                  {group.endpoints.slice(-3).map((endpoint) => <span key={endpoint}>{endpoint}</span>)}
                </div>
              ) : null}
              {group.status !== "running" && group.endpoints.length ? (
                <p className="agent-run-endpoint-summary">
                  {en ? "Interfaces" : "接口"}: {group.endpoints.slice(0, 3).join(" · ")}
                  {group.endpoints.length > 3 ? ` +${group.endpoints.length - 3}` : ""}
                  {group.failedCount ? ` · ${en ? `${group.failedCount} failed` : `${group.failedCount} 次失败`}` : ""}
                  {group.skippedCount ? ` · ${en ? `${group.skippedCount} skipped` : `${group.skippedCount} 次跳过`}` : ""}
                </p>
              ) : null}
            </div>
          </li>
        ))}
      </ol>
    </details>
  );
}

function ResearchDataCards({ cards, en }: { cards: Array<Record<string, unknown>>; en: boolean }) {
  const visible = cards.filter((card) => String(card.kind || "") !== "model_fill");
  if (!visible.length) return null;
  return <div className="research-data-cards">
    {visible.map((card, index) => {
      const rows = Array.isArray(card.rows) ? card.rows as Array<Record<string, unknown>> : [];
      const years = Array.isArray(card.years) ? card.years as Array<Record<string, unknown>> : [];
      const targets = card.targets && typeof card.targets === "object" ? card.targets as Record<string, unknown> : null;
      const kind = String(card.kind || "");
      return <details className="research-data-card" key={`${kind}-${index}`}>
        <summary>{String(card.title || (en ? "Retrieved data" : "已获取数据"))}<span>{String(card.provider || "")}{card.as_of ? ` · ${String(card.as_of)}` : ""}</span></summary>
        {kind === "financials" && rows.length ? <div className="research-data-table"><table><thead><tr><th>{en ? "Period" : "报告期"}</th>{Object.keys((rows[0]?.values as Record<string, unknown>) || {}).slice(0, 6).map((key) => <th key={key}>{key}</th>)}</tr></thead><tbody>{rows.map((row, rowIndex) => {
          const values = (row.values as Record<string, unknown>) || {};
          return <tr key={rowIndex}><td>{String(row.period || "")}</td>{Object.keys(values).slice(0, 6).map((key) => <td key={key}>{values[key] == null ? "—" : String(values[key])}</td>)}</tr>;
        })}</tbody></table></div> : null}
        {kind === "estimates" && targets ? <ul className="research-data-list">{Object.entries(targets).map(([key, value]) => <li key={key}><strong>{key}</strong>: {String(value)}</li>)}</ul> : null}
        {kind === "estimates" && years.length ? <div className="research-data-table"><table><thead><tr><th>{en ? "Period" : "年度"}</th><th>Mean</th><th>Min</th><th>Max</th><th>{en ? "Analysts" : "机构数"}</th></tr></thead><tbody>{years.map((row, rowIndex) => <tr key={rowIndex}><td>{String(row.period || "")}</td><td>{String(row.mean ?? "—")}</td><td>{String(row.min ?? "—")}</td><td>{String(row.max ?? "—")}</td><td>{String(row.count ?? "—")}</td></tr>)}</tbody></table></div> : null}
        {kind === "index" && rows.length ? <div className="research-data-table"><table><thead><tr><th>{en ? "Code" : "代码"}</th><th>{en ? "Name" : "名称"}</th><th>{en ? "Weight" : "权重"} %</th></tr></thead><tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}><td>{String(row.code || "")}</td><td>{String(row.name || "")}</td><td>{String(row.weight_percent ?? "—")}</td></tr>)}</tbody></table></div> : null}
        {kind === "announcements" && rows.length ? <ul className="research-data-list">{rows.map((row, rowIndex) => <li key={rowIndex}><a href={String(row.url || "#")} target="_blank" rel="noreferrer">{String(row.title || "")}</a> <small>{String(row.date || "")}</small></li>)}</ul> : null}
      </details>;
    })}
  </div>;
}

function downloadReport(response: AgentResponse, format: "sources" | "md", locale: "en" | "zh") {
  const { body, mime } = researchExport(response, format, locale);
  const url = URL.createObjectURL(new Blob([body], { type: mime }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `financial-bot-${format === "sources" ? "sources" : "research"}-${Date.now()}.md`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function ResponseTools({ response }: { response: AgentResponse }) {
  return <ResearchTools steps={(response.research_steps ?? []).map(step => ({tool: step.tool, status: step.status, reasonCode: step.reason_code, endpoint: step.endpoint}))} workSeconds={response.payload?._meta?.latency_seconds} />;
}

export function ResearchTrace({ response, showTools = true }: { response: AgentResponse; showTools?: boolean }) {
  const { locale } = useLanguage();
  const en = locale === "en";
  const metric = (name: string) => metricLabels[name]?.[en ? 1 : 0] ?? name;
  const evidence = response.evidence ?? [];
  const companies = response.payload.sec_companies ?? (response.payload.sec_company ? [response.payload.sec_company] : []);
  const comparison = response.payload.financial_comparison;
  const freshnessLabel = (value: string) => ({
    historical_or_delayed: en ? "Historical / delayed" : "历史／延迟数据",
    delayed_or_snapshot: en ? "Delayed / snapshot" : "延迟／快照数据",
    older_reported_period: en ? "Older reported period" : "较早报告期",
    reported_period: en ? "Reported period" : "已披露报告期",
    provider_summary: en ? "Provider publication" : "数据源发布",
    source_excerpts: en ? "Original source excerpts" : "来源原文节选",
    identity_mapping: en ? "Company identity mapping" : "公司身份映射",
    retrieved_snapshot: en ? "Retrieved snapshot" : "获取时的快照",
  }[value] ?? (en ? "Freshness unknown" : "时效未知"));
  const rawPlan = (response.payload.metadata as Record<string, unknown> | undefined)?.data_plan;
  const dataPlan = rawPlan && typeof rawPlan === "object" ? rawPlan as {
    capability?: string;
    market?: string;
    used_provider?: string;
    primary?: string;
    fallbacks?: string[];
    adjustment?: string;
    policy?: string;
  } : null;
  const rawCards = (response.payload as Record<string, unknown> | undefined)?.data_cards;
  const dataCards = Array.isArray(rawCards) ? rawCards as Array<Record<string, unknown>> : [];
  return <div className="research-trace">
    {showTools ? <ResponseTools response={response} /> : null}
    {dataCards.length ? <ResearchDataCards cards={dataCards} en={en} /> : null}
    {dataPlan ? <details className="research-data-plan">
      <summary>{en ? "Data plan" : "数据计划"}<span>{dataPlan.market || ""} · {dataPlan.capability || ""}</span></summary>
      <ul>
        <li><strong>{en ? "Primary" : "主源"}:</strong> {dataPlan.primary || "—"}</li>
        <li><strong>{en ? "Used" : "实际使用"}:</strong> {dataPlan.used_provider || "—"}</li>
        {dataPlan.fallbacks?.length ? <li><strong>{en ? "Fallbacks" : "备源"}:</strong> {dataPlan.fallbacks.join(" · ")}</li> : null}
        {dataPlan.adjustment ? <li><strong>{en ? "Adjustment" : "复权"}:</strong> {dataPlan.adjustment}</li> : null}
        {dataPlan.policy ? <li><strong>{en ? "Policy" : "规则"}:</strong> {dataPlan.policy}</li> : null}
      </ul>
    </details> : null}
    {comparison ? <details className="research-financial-details">
      <summary>{en ? "Financial comparison" : "财务比较"}<span>{comparison.companies.map(company => company.code.replace("US.", "")).join(" / ")}</span></summary>
      <div className="research-financial-table"><table><thead><tr><th>{en ? "Metric" : "指标"}</th>{comparison.companies.map(company => <th key={company.code}>{company.code.replace("US.", "")}</th>)}<th>{en ? "Comparability" : "可比性"}</th></tr></thead><tbody>{comparison.metrics.map(row => <tr key={row.metric}>
        <td>{metric(row.metric)}</td>{row.values.map(value => <td key={value.code}>{value.fact ? <><a href={value.fact.url} target="_blank" rel="noreferrer">{value.fact.val.toLocaleString(en ? "en-US" : "zh-CN")} {value.fact.unit}</a><small>{value.fact.start ? `${value.fact.start} → ` : ""}{value.fact.end}</small></> : (en ? "Unavailable" : "不可用")}</td>)}
        <td><span className={row.comparable ? "research-comparable" : "research-period-gap"}>{row.comparable ? (en ? "Similar periods" : "报告期相近") : (en ? "No direct ranking" : "不可直接排名")}</span><small>{en ? row.comparison_note : (row.comparable ? "核对各自的准确日期" : "单位、准则或报告期不一致，或数据缺失")}</small></td>
      </tr>)}</tbody></table></div>
    </details> : null}
    {companies.map(sec => <details className="research-financial-details" key={sec.company.code}>
      <summary>{en ? "Reported financial facts" : "已披露财务数据"}<span>SEC · {sec.company.code.replace("US.", "")} · {sec.financials.length}</span></summary>
      <div className="research-sec-card">
      <div><strong>{sec.company.name}</strong><small>{en ? "Values shown in original reporting units" : "数值保留原始报告单位"}</small></div>
      <div className="research-financial-table"><table><thead><tr><th>{en ? "Metric" : "指标"}</th><th>{en ? "Value / unit" : "数值／单位"}</th><th>{en ? "Reporting period" : "报告期"}</th></tr></thead><tbody>
        {sec.financials.map((fact, i) => <tr key={`${fact.concept}-${i}`}><td><a href={fact.url} title={`${fact.taxonomy}: ${fact.concept}`} target="_blank" rel="noreferrer">{metric(fact.concept)}</a><small>{fact.form} · {en ? "Filed" : "披露"} {fact.filed}</small></td><td>{fact.val.toLocaleString(en ? "en-US" : "zh-CN")} {fact.unit}</td><td>{fact.start ? `${fact.start} → ` : ""}{fact.end}<small>{fact.freshness === "older_reported_period" ? (en ? "Older reported period" : "较早报告期") : ""}</small></td></tr>)}
      </tbody></table></div>
      {!sec.financials.length ? <p>{en ? "Standardized financial facts unavailable." : "标准化财务数据不可用。"}</p> : null}
      <div className="research-filing-links">{sec.filings.slice(0, 4).map(filing => <a key={filing.accession} href={filing.url} target="_blank" rel="noreferrer">{filing.form} · {filing.filed} ↗</a>)}</div>
      </div>
    </details>)}
    {evidence.length || response.data_gaps?.length ? <details>
      <summary>{en ? "Sources & data coverage" : "来源与数据覆盖"} <span>{evidence.length} {en ? "sources" : "来源"}</span></summary>
      <div className="research-source-list">{evidence.map(source => <article key={source.id}>
        {source.url ? <a href={source.url} target="_blank" rel="noreferrer">{source.source} ↗</a> : <strong>{source.source}</strong>}
        <small>{en ? "Data date" : "数据日期"}: {source.data_as_of || (en ? "Unknown" : "未知")} · {freshnessLabel(source.freshness)}</small>
        <small>{en ? "Retrieved" : "获取时间"}: {source.retrieved_at}</small>
        {source.excerpts?.length ? <details><summary>{en ? "Read source excerpts" : "查看原文节选"}</summary>{source.excerpts.map((excerpt, index) => <blockquote key={index}><small>{excerpt.location}</small><p>{excerpt.text}</p></blockquote>)}</details> : null}
      </article>)}</div>
      {response.data_gaps?.length ? <div className="research-data-gaps"><strong>{en ? "Data gaps" : "数据缺口"}</strong>{response.data_gaps.map((gap, i) => <p key={i}>{gap}</p>)}</div> : null}
    </details> : null}
    <div className="research-export-actions">
      <button type="button" onClick={() => downloadReport(response, "md", locale)}>{en ? "Save report" : "保存报告"} ↓</button>
      {evidence.length ? <button type="button" onClick={() => downloadReport(response, "sources", locale)}>{en ? "Save sources" : "保存来源"} ↓</button> : null}
    </div>
  </div>;
}
