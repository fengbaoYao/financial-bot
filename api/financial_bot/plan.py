"""Persistent financial-task plans and deterministic validation.

The plan is the authoritative task stack.  It is created before the agent runs,
stored independently of the conversation, and evaluated from real tool results
after the run.  A model cannot mark a step complete by saying so in prose.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

PLAN_STATUSES = {"planning", "running", "completed", "partial", "failed", "cancelled"}
STEP_STATUSES = {"pending", "running", "completed", "retry", "failed", "skipped"}
STEP_KINDS = {"resolve", "market", "filings", "news", "web", "portfolio", "compare", "synthesis"}

ALLOWED_TOOLS = {
    "search_stocks",
    "get_stock_analysis",
    "get_benchmark_comparison",
    "get_stock_news",
    "get_global_news",
    "get_sec_company_data",
    "get_cninfo_announcements",
    "get_financial_statements",
    "get_company_fundamentals",
    "get_valuations",
    "get_analyst_estimates",
    "get_consensus_estimates",
    "compare_companies",
    "get_index_constituents",
    "list_hotspots",
    "get_hotspot_detail",
    "web_search",
    "read_public_source",
    "research_company",
    "research_comparison",
    "get_portfolio_analysis",
    "manage_watchlist",
}


class ValidationRule(BaseModel):
    kind: Literal["tool_success", "evidence", "min_evidence", "summary"] = "tool_success"
    tools: list[str] = Field(default_factory=list)
    min_evidence: int = Field(default=0, ge=0, le=20)
    description: str = ""


class PlannedStep(BaseModel):
    step_id: str = Field(min_length=1, max_length=20)
    title: str = Field(min_length=1, max_length=160)
    kind: Literal["resolve", "market", "filings", "news", "web", "portfolio", "compare", "synthesis"]
    tools: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    validation: ValidationRule = Field(default_factory=ValidationRule)
    retry_max: int = Field(default=1, ge=0, le=3)


class PlanOutput(BaseModel):
    answer_intent: str = Field(default="", max_length=300)
    answer_objective: str = Field(default="", max_length=800)
    steps: list[PlannedStep] = Field(min_length=1, max_length=6)
    termination_condition: str = Field(
        default="全部步骤 completed 才算任务完成；关键步骤连续重试失败则终止。",
        max_length=500,
    )


class PlanStep(PlannedStep):
    status: Literal["pending", "running", "completed", "retry", "failed", "skipped"] = "pending"
    attempts: int = 0
    last_error: str | None = None
    evidence_count: int = 0


class TaskPlan(BaseModel):
    task_id: str
    owner_id: str | None = None
    bot_id: str | None = None
    session_id: str | None = None
    original_query: str
    answer_intent: str = ""
    answer_objective: str = ""
    status: Literal["planning", "running", "completed", "partial", "failed", "cancelled"] = "planning"
    source: Literal["template", "llm", "fallback"] = "template"
    steps: list[PlanStep] = Field(default_factory=list)
    termination_condition: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))


def _has(text: str, pattern: str) -> bool:
    return bool(re.search(pattern, text, re.I))


def deterministic_plan(query: str, *, plan_id: str = "") -> TaskPlan:
    """A conservative fallback plan for common financial intents."""
    text = (query or "").strip()
    steps: list[PlannedStep] = []

    def add(
        title: str,
        kind: str,
        tools: list[str],
        validation: ValidationRule,
        depends_on: list[str] | None = None,
        retry_max: int = 1,
    ) -> None:
        step_id = f"s{len(steps) + 1}"
        steps.append(
            PlannedStep(
                step_id=step_id,
                title=title,
                kind=kind,  # type: ignore[arg-type]
                tools=tools,
                depends_on=depends_on or ([steps[-1].step_id] if steps else []),
                validation=validation,
                retry_max=retry_max,
            )
        )

    portfolio = _has(text, r"持仓|仓位|组合|我的股票|portfolio|持仓分析")
    comparison = _has(text, r"对比|比较|哪个好|区别|vs\.?|versus")
    filings = _has(text, r"财报|财务|年报|季报|10[- ]?[kq]|filing|公告|营收|净利润|利润|资产负债|现金流|估值|市盈率|市净率|\bpe\b|\bpb\b|目标价|一致预期")
    theme = _has(text, r"热点|大盘|市场|板块|指数|行业|题材|行情总览|market\s+overview")
    named = _has(text, r"腾讯|阿里|茅台|苹果|英伟达|特斯拉|宁德|招商|平安|比亚迪|小米|美团|京东|拼多多|台积电|微软|谷歌|亚马逊|meta|nvidia|apple|tesla|\b[A-Z]{2,5}\b|\b\d{5,6}\b")
    price = _has(text, r"股价|行情|技术|走势|k线|涨|跌|回调|支撑|压力|rsi|macd|成交量|现价|quote|price")
    news = _has(text, r"新闻|消息|公告|动态|资讯|事件|利好|利空|news")

    if portfolio:
        add("读取并校验当前持仓", "portfolio", ["get_portfolio_analysis"], ValidationRule(kind="tool_success", tools=["get_portfolio_analysis"], description="工具成功返回持仓结构，且不是空结果。"))
    elif theme and not _has(text, r"腾讯|阿里|茅台|苹果|英伟达|特斯拉|[A-Z]{1,5}\b|\d{5,6}"):
        add("获取市场、板块或热点数据", "market", ["list_hotspots", "get_hotspot_detail", "get_index_constituents", "get_company_fundamentals", "get_financial_statements", "web_search"], ValidationRule(kind="evidence", tools=["list_hotspots", "get_hotspot_detail", "get_index_constituents", "get_company_fundamentals", "get_financial_statements", "web_search"], min_evidence=1, description="至少一个市场/热点/指数/财务数据工具返回有效数据。"), depends_on=[])
    else:
        if named:
            add("核验研究对象与市场身份", "resolve", ["search_stocks"], ValidationRule(kind="tool_success", tools=["search_stocks"], description="至少解析出一个明确标的；不能靠模型记忆猜代码。"), depends_on=[])
        if comparison:
            add("获取可比的财务数据", "filings", ["get_sec_company_data", "get_company_fundamentals", "get_financial_statements", "research_comparison"], ValidationRule(kind="evidence", tools=["get_sec_company_data", "get_company_fundamentals", "get_financial_statements", "research_comparison"], min_evidence=1, description="至少一家公司的财务数据可用；报告期和币种保留。"))
            if news:
                add("获取两家公司的最新消息", "news", ["get_stock_news", "get_global_news"], ValidationRule(kind="evidence", tools=["get_stock_news", "get_global_news"], min_evidence=1, description="至少一条公司新闻；没有新闻时明确记录缺口。"))
            add("执行同口径比较", "compare", ["compare_companies", "research_comparison"], ValidationRule(kind="evidence", tools=["compare_companies", "research_comparison"], min_evidence=1, description="比较工具成功返回；不能只看一侧数据。"))
        elif filings and not price:
            add("获取财报、公告与关键字段", "filings", ["get_sec_company_data", "get_company_fundamentals", "get_financial_statements", "get_cninfo_announcements"], ValidationRule(kind="evidence", tools=["get_sec_company_data", "get_company_fundamentals", "get_financial_statements", "get_cninfo_announcements"], min_evidence=1, description="至少一个财务/公告工具成功，且返回可用的报告期数据。"))
        elif price:
            add("获取行情与技术面数据", "market", ["get_stock_analysis", "get_benchmark_comparison"], ValidationRule(kind="evidence", tools=["get_stock_analysis", "get_benchmark_comparison"], min_evidence=1, description="行情或基准对比工具成功返回，价格序列或相对收益不是空数组。"))
            if news:
                add("检索公司新闻与公告时间线", "news", ["get_stock_news", "get_global_news"], ValidationRule(kind="evidence", tools=["get_stock_news", "get_global_news"], min_evidence=1, description="新闻/公告工具成功返回；没有匹配新闻时记录明确缺口，不编造。"))
            if news or _has(text, r"季报|三季报|回购|监管|公告|业绩|财报|earnings|buyback|filing"):
                add("获取近期财报或公告字段", "filings", ["get_company_fundamentals", "get_cninfo_announcements", "get_sec_company_data"], ValidationRule(kind="evidence", tools=["get_company_fundamentals", "get_cninfo_announcements", "get_sec_company_data", "get_stock_news"], min_evidence=1, description="至少一个财报/公告/新闻工具返回可用记录；失败时写出具体来源缺口。"))
        elif news:
            add("检索公司新闻与时间线", "news", ["get_stock_news", "get_global_news"], ValidationRule(kind="evidence", tools=["get_stock_news", "get_global_news"], min_evidence=1, description="新闻工具成功返回；没有匹配新闻时记录明确缺口，不编造。"))
        else:
            add("检索公开来源、行情与公司证据", "web", ["web_search", "research_company", "read_public_source", "get_stock_analysis", "get_stock_news"], ValidationRule(kind="evidence", tools=["web_search", "research_company", "read_public_source", "get_stock_analysis", "get_stock_news"], min_evidence=1, description="至少一个研究/行情/新闻工具成功返回实质证据。"))

    add(
        "综合证据并标注数据缺口",
        "synthesis",
        [],
        ValidationRule(kind="summary", description="最终回答非空，且明确区分事实、推断与缺口。"),
        depends_on=[steps[-1].step_id] if steps else [],
    )
    return TaskPlan(
        task_id=plan_id,
        original_query=text,
        answer_intent="按用户原始问题完成研究",
        answer_objective=text[:800],
        status="planning",
        source="template",
        steps=[
            PlanStep(
                **step.model_dump(),
                status="pending",
                attempts=0,
            )
            for step in steps
        ],
        termination_condition="全部步骤 completed 才算任务完成；关键步骤重试失败则任务标记 partial/failed。",
    )


def normalize_llm_plan(
    output: PlanOutput,
    *,
    query: str,
    plan_id: str,
    allowed_tools: set[str] | None = None,
) -> TaskPlan:
    """Validate an LLM plan against the available tool vocabulary."""
    tool_vocabulary = ALLOWED_TOOLS if allowed_tools is None else ALLOWED_TOOLS & allowed_tools
    steps: list[PlanStep] = []
    seen: set[str] = set()
    for index, raw in enumerate(output.steps[:6]):
        step_id = str(raw.step_id or f"s{index + 1}")[:20]
        if step_id in seen:
            step_id = f"s{index + 1}"
        seen.add(step_id)
        tools = [tool for tool in raw.tools if tool in tool_vocabulary]
        kind = raw.kind if raw.kind in STEP_KINDS else "web"
        if raw.kind != "synthesis" and not tools:
            return deterministic_plan(query, plan_id=plan_id)
        validation = raw.validation
        if kind in {"market", "filings", "news", "web", "compare"} and validation.kind == "tool_success":
            validation = validation.model_copy(update={"kind": "evidence", "min_evidence": max(1, validation.min_evidence)})
        steps.append(
            PlanStep(
                step_id=step_id,
                title=raw.title[:160],
                kind=kind,
                tools=tools,
                depends_on=[dep for dep in raw.depends_on if dep in seen and dep != step_id][:6],
                validation=validation.model_copy(update={
                    "tools": [tool for tool in validation.tools if tool in tool_vocabulary]
                }),
                retry_max=raw.retry_max,
                status="pending",
            )
        )
    if not steps:
        return deterministic_plan(query, plan_id=plan_id)
    if steps[-1].kind != "synthesis":
        if len(steps) >= 6:
            steps = steps[:5]
        prior_id = steps[-1].step_id
        steps.append(PlanStep(
            step_id=f"s{len(steps) + 1}",
            title="根据任务和已取得证据回答用户问题",
            kind="synthesis",
            tools=[],
            depends_on=[prior_id],
            validation=ValidationRule(kind="summary", description="最终回答须回应用户目标，并区分证据与缺口。"),
            retry_max=0,
            status="pending",
        ))
    return TaskPlan(
        task_id=plan_id,
        original_query=query,
        answer_intent=(output.answer_intent or "按用户原始问题完成研究")[:300],
        answer_objective=(output.answer_objective or query)[:800],
        status="planning",
        source="llm",
        steps=steps,
        termination_condition=output.termination_condition[:500],
    )


def format_plan_context(plan: TaskPlan) -> str:
    if not plan.steps:
        return ""
    lines = [
        "## 持久化任务清单（权威，服务端维护）",
        f"用户原始请求：{plan.original_query[:1200]}",
        "你只能执行工具并回答；不能修改、跳过或宣称完成下面的步骤。只有服务端校验全部步骤通过，任务才算完成。",
        "如果某个步骤缺少数据，请明确说明，不要用空结果当作成功。",
        f"用户意图：{plan.answer_intent or '按原始问题完成研究'}",
        f"最终回答目标：{plan.answer_objective or plan.original_query}",
        "取证时围绕各步骤的目标调用对应工具；综合时逐项回应最终回答目标，优先给结论，再用取得的数据支持。不得以工具列表或免责声明代替回答。",
    ]
    for step in plan.steps:
        deps = f"；依赖 {', '.join(step.depends_on)}" if step.depends_on else ""
        tools = f"；工具 {', '.join(step.tools)}" if step.tools else ""
        lines.append(f"- [{step.status}] {step.step_id} {step.title}（{step.kind}{tools}{deps}）")
    return "\n".join(lines)


def _response_steps(response: Any) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for item in getattr(response, "research_steps", None) or []:
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        elif not isinstance(item, dict) and hasattr(item, "tool"):
            item = {
                "tool": getattr(item, "tool", ""),
                "status": getattr(item, "status", ""),
                "evidence_count": getattr(item, "evidence_count", 0),
            }
        if isinstance(item, dict):
            values.append(item)
    return values


def _step_completed(step: PlanStep, response: Any) -> tuple[bool, str, int]:
    summary = str(getattr(response, "summary", "") or "").strip()
    results = _response_steps(response)
    by_tool: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        by_tool.setdefault(str(item.get("tool") or ""), []).append(item)

    if step.kind == "synthesis" or step.validation.kind == "summary":
        return (bool(summary), "" if summary else "最终回答为空", 0)

    tools = step.tools or step.validation.tools
    if not tools:
        return (False, "任务步骤没有可校验的数据工具", 0)
    matching = [item for tool in tools for item in by_tool.get(tool, [])]
    completed = [item for item in matching if str(item.get("status") or "") == "completed"]
    evidence = sum(int(item.get("evidence_count") or 0) for item in completed)
    minimum = max(step.validation.min_evidence, 1 if step.validation.kind in {"evidence", "min_evidence"} else 0)
    if not completed:
        return (False, "未获得该步骤所需的成功工具结果", evidence)
    if evidence < minimum:
        return (False, f"工具成功但证据不足（{evidence}/{minimum}）", evidence)
    return (True, "", evidence)


def evaluate_plan(plan: TaskPlan, response: Any) -> TaskPlan:
    """Update step states from real tool results; never from model prose."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for step in plan.steps:
        if step.status in {"completed", "skipped"}:
            continue
        ok, error, evidence = _step_completed(step, response)
        step.evidence_count = evidence
        step.attempts += 1
        if ok:
            step.status = "completed"
            step.last_error = None
        else:
            step.last_error = error[:300]
            step.status = "retry" if step.attempts <= step.retry_max else "failed"
    terminal = [step for step in plan.steps if step.status in {"completed", "skipped"}]
    failed = [step for step in plan.steps if step.status == "failed"]
    if len(terminal) == len(plan.steps):
        plan.status = "completed"
    elif failed:
        plan.status = "failed"
    else:
        plan.status = "partial"
    plan.updated_at = now
    return plan


def plan_gaps(plan: TaskPlan) -> list[str]:
    gaps = []
    for step in plan.steps:
        if step.status in {"completed", "skipped"}:
            continue
        detail = f"（{step.last_error}）" if step.last_error else ""
        gaps.append(f"任务清单未完成：{step.step_id} {step.title}{detail}")
    return gaps


def should_plan_query(query: str) -> bool:
    text = (query or "").strip()
    if len(text) < 2:
        return False
    return not bool(re.fullmatch(r"(你好|您好|hi|hello|hey|早上好|下午好|晚上好)[!！。.\s]*", text, re.I))


def should_use_llm_planner(query: str) -> bool:
    """Only pay for a planner call when the request is genuinely multi-step."""
    text = query or ""
    if len(text) < 30:
        return False
    signals = len(re.findall(r"并且|同时|然后|接着|对比|比较|导出|生成|计算|分位|报表|汇总|分别|以及", text))
    return signals >= 2 or text.count("？") + text.count("?") >= 2
