export type TaskStatus =
  | "queued"
  | "running"
  | "waiting_user"
  | "completed"
  | "partial"
  | "dropped"
  | "failed"
  | "cancelled"
  | "unknown";

/** Server disposition for one user input (research / answer / wait / drop). */
export type BotDispositionKind = "accepted" | "direct_answer" | "clarify" | "dropped";

export type BotDisposition = {
  id: string;
  task_id?: string | null;
  session_id?: string | null;
  input_ref: string;
  disposition: BotDispositionKind | string;
  reason_code?: string | null;
  reason_display?: string | null;
  created_at?: string | null;
};


export type BotState =
  | "idle"
  | "queued"
  | "running"
  | "waiting"
  | "completed"
  | "partial"
  | "ended"
  | "failed"
  | "cancelled"
  | "unknown";

export type TaskEvent = {
  event_id: string;
  task_id: string;
  sequence: number;
  occurred_at: string;
  kind: "status" | "step" | "answer" | "error" | "notice" | "token";
  step_id: string | null;
  status: string;
  display_summary: string;
  source_refs: Array<Record<string, unknown>>;
};

export type SourceEvidence = {
  id: string | null;
  source: string;
  url?: string | null;
  retrieved_at?: string | null;
  data_as_of?: string | null;
  freshness?: string | null;
  title?: string | null;
  provider_endpoint?: string | null;
  currency?: string | null;
  adjustment?: string | null;
  raw_ref?: string | null;
  raw_sha256?: string | null;
  record_key?: string | null;
  excerpts?: Array<{ location?: string | null; text: string }>;
};

export type TaskPlanStep = {
  step_id: string;
  title: string;
  kind: "resolve" | "market" | "filings" | "news" | "web" | "portfolio" | "compare" | "synthesis";
  tools: string[];
  depends_on: string[];
  validation: {
    kind: "tool_success" | "evidence" | "min_evidence" | "summary";
    tools: string[];
    min_evidence: number;
    description: string;
  };
  retry_max: number;
  status: "pending" | "running" | "completed" | "retry" | "failed" | "skipped";
  attempts: number;
  last_error?: string | null;
  evidence_count: number;
};

export type TaskPlan = {
  task_id: string;
  bot_id?: string | null;
  session_id?: string | null;
  original_query: string;
  status: "planning" | "running" | "completed" | "partial" | "failed" | "cancelled";
  source: "template" | "llm" | "fallback";
  steps: TaskPlanStep[];
  termination_condition?: string;
  created_at: string;
  updated_at: string;
};

export type WorkflowTrack = {
  key: string;
  label: string;
  status: "done" | "pending" | "blocked";
  evidence?: string;
};

export type WorkflowVerification = {
  conclusion: "complete" | "not_complete" | "unknown";
  basis?: string;
  items?: WorkflowTrack[];
};

export type WorkflowHandoff = {
  reason: string;
  page_title?: string;
  page_state?: string;
  boundary?: string;
  resume_label?: string;
};

export type TaskWorkflow = {
  stage: "diagnosis" | "executing" | "handoff" | "verification" | "complete";
  title?: string;
  tracks?: WorkflowTrack[];
  verification?: WorkflowVerification | null;
  handoff?: WorkflowHandoff | null;
  options?: string[];
};

export type UIBlock = {
  type: "card" | "collapse" | "callout";
  title: string;
  body: string;
  tone?: "info" | "success" | "warning";
  footer?: string;
  default_open?: boolean;
};

export type TaskResult = {
  intent?: string;
  summary: string;
  ui_blocks?: UIBlock[];
  workflow?: TaskWorkflow | null;
  actions?: string[];
  citations?: string[];
  ui_components?: string[];
  clarification?: { question: string; options: string[]; allow_free_text: boolean } | null;
  research_steps?: Array<{
    tool: string;
    status: string;
    reason_code?: string | null;
    evidence_count?: number;
    retrieved_at?: string;
    endpoint?: string | null;
  }>;
  evidence?: SourceEvidence[];
  data_gaps?: string[];
  /** Price series assembled server-side from tool data (never model-authored). */
  chart?: {
    title?: string;
    /** "price" plots the close, "pe" plots the valuation multiple. */
    metric?: "price" | "pe";
    currency?: string | null;
    source?: string | null;
    data_as_of?: string | null;
    note?: string | null;
    series: Array<{ date: string; close: number }>;
  } | null;
  meta?: Record<string, unknown>;
  data_plan?: {
    capability?: string;
    market?: string;
    used_provider?: string;
    primary?: string;
    fallbacks?: string[];
    adjustment?: string;
    policy?: string;
  } | null;
  data_plans?: Array<{
    tool?: string;
    capability?: string;
    status?: string;
    provider?: string;
    endpoint?: string | null;
    currency?: string | null;
    adjustment?: string | null;
    reason_code?: string | null;
  }>;
  data_cards?: Array<{
    kind?: "financials" | "estimates" | "index" | "announcements" | "valuations" | string;
    title?: string;
    provider?: string;
    currency?: string | null;
    as_of?: string | null;
    endpoint?: string | null;
    rows?: Array<Record<string, unknown>>;
    years?: Array<Record<string, unknown>>;
    targets?: Record<string, unknown>;
    recommendations?: Array<Record<string, unknown>>;
    member_count?: number;
    weight_total_percent?: number;
  }>;
  identity?: { stock_code?: string | null; stock_name?: string | null; market?: string | null };
};

export type BotMessage = {
  id: string;
  bot_id?: string | null;
  session_id: string;
  role: "user" | "assistant" | "system";
  kind: string;
  content: string;
  task_id?: string | null;
  metadata?: Record<string, unknown>;
  sequence: number;
  created_at: string;
};

export type BotTask = {
  id: string;
  session_id: string | null;
  query: string;
  language: string;
  research_mode: "quick" | "deep" | "openmuse_shared";
  status: TaskStatus;
  waiting_reason?: "question" | "confirmation" | "permission" | null;
  attention?: {
    question: string;
    options?: string[];
    allow_free_text?: boolean;
  } | null;
  authorization?: Record<string, unknown> | null;
  continued_from_task_id?: string | null;
  result: TaskResult | null;
  error_code?: string | null;
  error_message?: string | null;
  cancel_requested: boolean;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  sequence: number;
  ui_context?: {
    page?: "bot" | "work";
    last_action?: string;
    task_id?: string | null;
    task_status?: TaskStatus | null;
    view?: "transcript" | "trace" | "studio";
  } | null;
};

export type MemoryKind =
  | "watch_object"
  | "preference"
  | "research_question"
  | "holding"
  | "confirmed_viewpoint";

export type MemoryItem = {
  id: string;
  kind: MemoryKind;
  text: string;
  source_text?: string | null;
  source_ref?: string | null;
  status: "confirmed" | "pending" | "dismissed";
  version: number;
  created_at: string;
  updated_at: string;
  confirmed_at?: string | null;
};

export type MemoryCandidate = {
  id: string;
  task_id?: string | null;
  proposed_text: string;
  rationale?: string | null;
  source_text?: string | null;
  source_ref?: string | null;
  status: "pending" | "confirmed" | "dismissed";
  version: number;
  created_at: string;
  decided_at?: string | null;
  memory_id?: string | null;
};

export type DeletionLayer = {
  status: "pending" | "cleared" | "verified" | "unverified" | "blocked" | "failed" | "not_configured";
  detail?: string;
  deleted?: number;
};

export type DeletionJob = {
  id: string;
  scope: "single" | "all";
  target_ids: string[];
  status: "running" | "completed" | "blocked" | "failed";
  layers: Record<string, DeletionLayer>;
  detail?: string | null;
  request_id: string;
  requested_at: string;
  updated_at: string;
  completed_at?: string | null;
};

export type BotStatus = {
  owner: {
    mode: "local_single_user" | "supabase_authenticated";
    authenticated: boolean;
    label: string;
    email?: string | null;
  };
  openmuse?: {
    status: "disabled" | "configuration_error" | "unreachable" | "connected" | "unavailable";
    reason?: string | null;
    mode?: "sample" | "live" | null;
    agent_configured?: boolean | null;
    browser_configured?: boolean | null;
    shared_research_enabled?: boolean;
  };
  storage: { dialect: string; durable: boolean; database: string };
  model: { configured: boolean; model?: string | null; note: string };
  limits: { daily_tasks: number; daily_tokens: number };
  usage: { tasks_today: number; tokens_today: number; cost_usd_today: number };
  deletion: { third_party_retention_verified: boolean; backup_dir_configured: boolean };
  local_data: {
    enabled: boolean;
    scope: string;
    providers: string[];
    authorization_note: string;
    raw_retention: string;
  };
  trial_gate: { multi_user_open: boolean; reason: string };
  recovery: { interrupted_tasks_recovered: number };
  data_sources: Record<string, string>;
  provider_config: {
    finnhub: boolean;
    fuyao?: boolean;
    fuyao_primary_a_share?: boolean;
    fuyao_financials?: boolean;
    ifind?: boolean;
    ifind_primary?: boolean;
    ifind_history_enabled?: boolean;
    ifind_history_quota_exhausted?: boolean;
    ifind_history_note?: string | null;
    brave_search: boolean;
    sec_contact: boolean;
    web_search_enabled: boolean;
    web_search_timeout_seconds: number;
    yfinance_shadow: boolean;
    yfinance_shadow_dir: string;
    yfinance_us_hk_fundamentals: boolean;
    yfinance_primary_us_hk: boolean;
    yfinance_a_share_fallback: boolean;
  };
};

export type MemoryResponse = {
  memories: MemoryItem[];
  pending_candidates: MemoryCandidate[];
  latest_deletion: DeletionJob | null;
  cache: { context_scope: string };
};
