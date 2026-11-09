"""Background task execution for Financial Bot.

Each task runs on its own thread with its own asyncio loop. That keeps tasks
alive across the HTTP request that created them (including the FastAPI test
client), while cancellation still goes through ``loop.call_soon_threadsafe``.

Only real agent/tool progress is published. If the process restarts, tasks are
moved to ``unknown`` by the router startup hook instead of pretending to run.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from typing import Any, Optional

from api.financial_bot.auth import OwnerContext
from api.financial_bot.memory_extract import candidate_payloads
from api.financial_bot.plan import (
    TaskPlan,
    deterministic_plan,
    evaluate_plan,
    format_plan_context,
    plan_gaps,
    should_plan_query,
)
from api.financial_bot.planner import LLMPlanner
from api.financial_bot.prompt import enabled_tools, prompt_config_from_bot, render_bot_prompt
from api.financial_bot.store import FinancialBotStore

logger = logging.getLogger(__name__)

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{8,}"),
    re.compile(r"(?i)api[_-]?key\s*[=:]\s*\S+"),
)

STOP_STATUSES = {"completed", "partial", "dropped", "failed", "cancelled", "unknown", "waiting_user"}

# Intents that mean the run did no research, so there is nothing left to act on.
DROPPED_INTENTS = {
    # The agent's own "I could not identify a task in this input" answer.
    "unknown",
    "out_of_scope",
    "no_research_needed",
    "cannot_execute",
}
_DROPPED_DECISIONS = {"dropped", "no_action", "no_research", "cannot_execute"}

_DROPPED_SUMMARIES = {
    "no_actionable_target": (
        "没有识别出可研究的目标；请给出股票名称或代码。",
        "No research target was identified; name a stock or ticker.",
    ),
    "out_of_scope": (
        "这个请求超出现有数据能力；可以换成具体的股票、公告或行情问题。",
        "This request is outside the available data; try a specific stock, filing or quote question.",
    ),
    "cannot_execute": (
        "这一步无法执行，已停止且没有伪造结果；可以换个更具体的问法。",
        "This step could not be executed, so it stopped instead of faking a result; try a narrower question.",
    ),
}


def redact(text: str) -> str:
    cleaned = text or ""
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("[redacted]", cleaned)
    return cleaned[:1200]


def dropped_reason(response: Any) -> Optional[str]:
    """Why this response needs no research, or ``None`` when it does.

    Only explicit signals count: the response either carries one of the
    ``DROPPED_INTENTS``, or the model declares the outcome in ``_meta``
    (``{"disposition": "dropped", "disposition_reason": "..."}``).  Answer
    prose is never pattern-matched, so a real answer cannot be silently
    downgraded to "nothing to do".
    """
    payload = getattr(response, "payload", None)
    meta = payload.get("_meta") if isinstance(payload, dict) else None
    meta = meta if isinstance(meta, dict) else {}
    intent = str(getattr(response, "intent", "") or "").strip().lower()
    declared = str(meta.get("disposition") or meta.get("decision") or "").strip().lower()
    if intent not in DROPPED_INTENTS and declared not in _DROPPED_DECISIONS:
        return None
    reason = meta.get("disposition_reason") or meta.get("decision_reason")
    if isinstance(reason, str):
        code = re.sub(r"[^a-z0-9]+", "_", reason.strip().lower()).strip("_")
        if code:
            return code[:60]
    if intent == "unknown":
        return "no_actionable_target"
    return intent if intent in DROPPED_INTENTS else "no_research_needed"


def dropped_summary(reason: str, zh: bool) -> str:
    """User-visible explanation for a dropped task (the reason code is not public)."""
    pair = _DROPPED_SUMMARIES.get(reason)
    if pair:
        return pair[0] if zh else pair[1]
    return "无需执行研究；答案已给出，原因已记录。" if zh else "No research was needed; the answer is shown and the reason is recorded."


def assess_deep_evidence_quality(response: Any, *, language: str) -> None:
    """Flag identifiable citation gaps and strong causal wording for review."""
    summary = str(getattr(response, "summary", "") or "")
    missing_links = "来源缺少可验证链接" in summary or "source has no verifiable link" in summary.lower()
    causal = bool(re.search(r"(?:主要与.{0,35}同步.{0,35}主导|.{1,20}为主因)", summary))
    if not (missing_links or causal):
        return
    payload = getattr(response, "payload", None)
    if not isinstance(payload, dict):
        payload = {}
        response.payload = payload
    payload.setdefault("_meta", {})["deep_evidence_quality_unverified"] = True
    en = language == "en-US"
    gaps = list(getattr(response, "data_gaps", None) or [])
    note = ("Some cited sources lack verifiable links." if en else "部分引用缺少可核验链接，相关事实需复核。")
    if missing_links and note not in gaps:
        gaps.append(note)
    response.data_gaps = gaps
    warning = ("Evidence review required: causal claims have not been independently established.\n\n" if en else "证据待复核：文中因果判断尚未独立证实，请勿将相关性直接视为原因。\n\n")
    if causal and not summary.startswith(warning):
        response.summary = warning + summary


def classify_response(response: Any) -> tuple[str, str | None]:
    intent = str(getattr(response, "intent", "") or "")
    payload = getattr(response, "payload", None) or {}
    meta = payload.get("_meta") if isinstance(payload, dict) else {}
    meta = meta if isinstance(meta, dict) else {}
    summary = str(getattr(response, "summary", "") or "").strip()
    if getattr(response, "clarification", None) is not None:
        return "waiting_user", "clarification"
    if meta.get("deep_evidence_quality_unverified") and summary:
        return "partial", "deep_evidence_quality_unverified"
    if meta.get("deep_research_incomplete") and summary:
        return "partial", "deep_research_incomplete"
    if meta.get("llm_research_recovery") and meta.get("unverified") and summary:
        return "partial", "unverified_recovery"
    if meta.get("llm_data_gap_fill_count") and summary:
        return "partial", "unverified_model_fill"
    if meta.get("llm_data_gap_fill_failed") and summary and (intent != "error" or getattr(response, "evidence", None)):
        return "partial", "model_fill_failed"
    if intent == "error":
        if summary and (getattr(response, "evidence", None) or []):
            return "partial", "synthesis_error_with_sources"
        return "failed", "agent_error"
    dropped = dropped_reason(response)
    # A dropped task must still say something; an empty answer stays a failure.
    if dropped and summary:
        return "dropped", dropped
    # The provider answered in prose instead of the structured channel. When the
    # visible text already carries the evidence, that is a full answer with a
    # validation downgrade - not a half-finished one.
    prose_fallback = (
        meta.get("output_fallback") == "text"
        and meta.get("structured_output_validated") is False
    )
    if prose_fallback and summary:
        # Provenance is carried separately by answer_basis, so this is a full
        # answer either way: sourced when tools returned evidence, model
        # knowledge (labelled in the UI) when they did not.
        return "completed", "structured_output_fallback"
    if meta.get("synthesis_incomplete") or meta.get("limit_reached"):
        return "partial", str(meta.get("limit_reason") or "synthesis_incomplete")
    if not summary:
        return "failed", "empty_answer"
    return "completed", None



def collect_used_data_sources(response: Any, document: dict[str, Any] | None = None) -> dict[str, str]:
    """Derive authorization.data_sources from evidence / plans actually used.

    Keys are stable provider labels; values are short provenance strings.
    Empty when nothing real was cited — callers must not write empty maps.
    """
    doc = document if isinstance(document, dict) else result_document(response)
    out: dict[str, str] = {}

    def put(key: str, value: str) -> None:
        k = str(key or "").strip()
        v = str(value or "").strip()
        if not k or not v:
            return
        # Prefer the first real label; do not overwrite with emptier text.
        if k not in out:
            out[k] = v[:200]

    for src in doc.get("evidence") or []:
        if not isinstance(src, dict):
            continue
        name = str(src.get("source") or src.get("provider_endpoint") or "").strip()
        if not name:
            continue
        bits = [name]
        if src.get("data_as_of"):
            bits.append(f"as_of={src.get('data_as_of')}")
        if src.get("freshness"):
            bits.append(str(src.get("freshness")))
        put(name, " · ".join(bits))

    plan = doc.get("data_plan") if isinstance(doc.get("data_plan"), dict) else None
    if plan:
        provider = str(plan.get("used_provider") or plan.get("provider") or "").strip()
        if provider:
            put(provider, provider)

    for step in doc.get("research_steps") or []:
        if not isinstance(step, dict):
            continue
        status = str(step.get("status") or "").lower()
        failed = status in {"failed", "error", "skipped", "cancelled", "canceled"}
        has_evidence = int(step.get("evidence_count") or 0) > 0
        if failed and not has_evidence:
            continue
        if status and status not in {"ok", "success", "completed", "done", "partial", "running", ""} and not has_evidence:
            continue
        tool = str(step.get("tool") or "").strip()
        endpoint = str(step.get("endpoint") or "").strip()
        # A tool name describes an operation, not necessarily a data source
        # (for example, search_stocks only resolves identity). Only attach a
        # step-derived source when the step carries its actual endpoint; source
        # evidence and provider cards above remain the preferred provenance.
        if endpoint:
            put(endpoint, endpoint)

    for card in doc.get("data_cards") or []:
        if not isinstance(card, dict):
            continue
        provider = str(card.get("provider") or "").strip()
        if provider:
            note = provider
            if card.get("as_of"):
                note = f"{provider} · as_of={card.get('as_of')}"
            put(provider, note)

    return out


def disposition_for_outcome(
    status: str,
    reason: str | None,
    response: Any,
    *,
    zh: bool,
) -> tuple[str, str | None, str | None] | None:
    """Map a terminal task outcome to a disposition upgrade (or None to keep accepted)."""
    payload = getattr(response, "payload", None) or {}
    meta = payload.get("_meta") if isinstance(payload, dict) else {}
    meta = meta if isinstance(meta, dict) else {}
    answer_basis = str(meta.get("answer_basis") or "").strip()
    evidence = list(getattr(response, "evidence", None) or [])
    tools = meta.get("tools_used") if isinstance(meta.get("tools_used"), list) else []

    if status == "dropped":
        display = dropped_summary(reason or "no_research_needed", zh)
        return "dropped", reason or "no_research_needed", display
    if status == "waiting_user":
        return (
            "clarify",
            reason or "clarification",
            "等待你补充信息" if zh else "Waiting for your input",
        )
    if status in {"completed", "partial"}:
        # Direct answer: model answered without research tools / evidence.
        if answer_basis == "model_knowledge" and not evidence and not tools:
            return (
                "direct_answer",
                reason or "model_knowledge",
                "基于模型知识直接回答" if zh else "Answered from model knowledge",
            )
        # Keep provisional accepted for researched answers.
        return None
    return None


def clarification_waiting(response: Any) -> tuple[str | None, dict[str, Any] | None]:

    """Map a model clarification into the structured waiting contract."""
    clarification = getattr(response, "clarification", None)
    if clarification is None:
        return None, None
    if hasattr(clarification, "model_dump"):
        clarification = clarification.model_dump()
    if isinstance(clarification, dict):
        question = str(clarification.get("question") or "").strip()
        options = list(clarification.get("options") or [])
        allow_free_text = bool(clarification.get("allow_free_text", True))
    else:
        question = str(getattr(clarification, "question", "") or "").strip()
        options = list(getattr(clarification, "options", []) or [])
        allow_free_text = bool(getattr(clarification, "allow_free_text", True))
    if not question:
        return None, None
    options = [str(item)[:120] for item in options][:4]
    return "question", {
        "question": question[:240],
        "options": options,
        "allow_free_text": allow_free_text,
    }


def result_document(response: Any) -> dict[str, Any]:
    payload = getattr(response, "payload", None) or {}
    if not isinstance(payload, dict):
        payload = {}
    evidence: list[dict[str, Any]] = []
    for source in (getattr(response, "evidence", None) or [])[:24]:
        if hasattr(source, "model_dump"):
            source = source.model_dump()
        if not isinstance(source, dict):
            continue
        evidence.append(
            {
                "id": source.get("id"),
                "source": source.get("source"),
                "url": source.get("url"),
                "retrieved_at": source.get("retrieved_at"),
                "data_as_of": source.get("data_as_of"),
                "freshness": source.get("freshness"),
                "title": source.get("title"),
                "provider_endpoint": source.get("provider_endpoint"),
                "currency": source.get("currency"),
                "adjustment": source.get("adjustment"),
                "raw_ref": source.get("raw_ref"),
                "raw_sha256": source.get("raw_sha256"),
                "record_key": source.get("record_key"),
                "excerpts": [
                    {"location": item.get("location"), "text": str(item.get("text") or "")[:500]}
                    for item in (source.get("excerpts") or [])[:2]
                ],
            }
        )
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
    safe_meta = {
        key: meta.get(key)
        for key in (
            "engine",
            "model_provider",
            "requested_model_provider",
            "model_fallback",
            "tools_used",
            "usage",
            "latency_seconds",
            "run_id",
            "finish_reason",
            "limit_reached",
            "deep_research_incomplete",
            "synthesis_incomplete",
            "failure_type",
            "estimated_cost_usd",
            "usage_unresolved",
            "answer_basis",
            "answer_basis_detail",
            "llm_data_gap_fill",
            "llm_data_gap_fill_count",
            "llm_data_gap_fill_failed",
            "llm_data_gap_fill_provider",
            "llm_research_recovery",
            "llm_research_recovery_provider",
            "model_fill_as_tool",
            "unverified_model_fields",
            "unverified",
        )
        if key in meta
    }
    return {
        "intent": getattr(response, "intent", ""),
        "summary": getattr(response, "summary", ""),
        "actions": list(getattr(response, "actions", None) or []),
        "citations": list(getattr(response, "citations", None) or []),
        "ui_components": list(getattr(response, "ui_components", None) or []),
        "ui_blocks": payload.get("ui_blocks") if isinstance(payload.get("ui_blocks"), list) else [],
        "workflow": payload.get("workflow") if isinstance(payload.get("workflow"), dict) else None,
        "chart": payload.get("chart") if isinstance(payload.get("chart"), dict) else None,
        "clarification": (
            getattr(response, "clarification").model_dump()
            if getattr(response, "clarification", None) is not None
            else None
        ),
        "research_steps": [
            {
                "tool": step.tool,
                "status": step.status,
                "reason_code": step.reason_code,
                "endpoint": step.endpoint,
                "evidence_count": step.evidence_count,
                "retrieved_at": step.retrieved_at,
            }
            for step in (getattr(response, "research_steps", None) or [])
        ],
        "evidence": evidence,
        "data_gaps": list(getattr(response, "data_gaps", None) or [])[:20],
        "meta": safe_meta,
        "data_plan": payload.get("metadata", {}).get("data_plan") if isinstance(payload.get("metadata"), dict) else None,
        "data_plans": payload.get("data_plans") if isinstance(payload.get("data_plans"), list) else [],
        "data_cards": payload.get("data_cards") if isinstance(payload.get("data_cards"), list) else [],
        "identity": {
            "stock_code": payload.get("stock_code"),
            "stock_name": payload.get("stock_name"),
            "market": payload.get("market"),
        },
    }


class TaskExecutor:
    def __init__(self, store: FinancialBotStore, agent_runner=None):
        self.store = store
        self._agent_runner = agent_runner
        self._handles: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Task]] = {}
        self._plans: dict[str, TaskPlan] = {}
        self._findings_seen: dict[str, set[str]] = {}
        self._starting: set[str] = set()
        self._answer_streamed: set[str] = set()
        self._lock = threading.Lock()

    def set_agent_runner(self, runner) -> None:
        self._agent_runner = runner

    def start(self, owner: OwnerContext, task_id: str, *, resume: bool = False) -> None:
        try:
            if self.store.get_task(owner.owner_id, task_id).get("research_mode") == "openmuse_shared":
                return
        except Exception:
            # Preserve existing error handling for ordinary tasks; _execute
            # still checks the mode before making any local execution effect.
            pass
        self._findings_seen.pop(task_id, None)
        with self._lock:
            if task_id in self._handles or task_id in self._starting:
                return
            self._starting.add(task_id)
        thread = threading.Thread(
            target=self._thread_main,
            args=(owner, task_id, resume),
            name=f"financial-bot-{task_id[:12]}",
            daemon=True,
        )
        thread.start()

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            handle = self._handles.get(task_id)
        if not handle:
            return False
        loop, task = handle
        try:
            loop.call_soon_threadsafe(task.cancel)
        except RuntimeError:
            return False
        return True

    def _thread_main(self, owner: OwnerContext, task_id: str, resume: bool = False) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        task = loop.create_task(self._execute(owner, task_id, resume=resume))
        with self._lock:
            self._handles[task_id] = (loop, task)
        try:
            loop.run_until_complete(task)
        except asyncio.CancelledError:
            # The task may have been cancelled before it got a chance to run.
            try:
                current = self.store.get_task(owner.owner_id, task_id)
                if current["status"] in {"queued", "running"}:
                    self.store.transition(
                        owner.owner_id,
                        task_id,
                        "cancelled",
                        reason="cancelled_before_start",
                        summary="任务在开始执行前被取消",
                    )
            except Exception:
                logger.exception("Failed to record pre-start cancellation")
        except Exception:  # pragma: no cover - defensive boundary
            logger.exception("Financial Bot task thread failed")
        finally:
            with self._lock:
                self._handles.pop(task_id, None)
                self._starting.discard(task_id)
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()

    # -------------------------------------------------------------- execution
    async def _execute(self, owner: OwnerContext, task_id: str, *, resume: bool = False) -> None:
        task = self.store.get_task(owner.owner_id, task_id)
        if task.get("research_mode") == "openmuse_shared":
            return
        language = task.get("language") or "auto"
        zh = language != "en-US"
        if not self.store.transition(
            owner.owner_id, task_id, "running", zh=zh
        ):
            return  # already terminal (e.g. cancelled before start)
        if self.store.task_is_cancelled(owner.owner_id, task_id):
            self.store.transition(owner.owner_id, task_id, "cancelled", reason="cancelled_before_start", zh=zh)
            return

        import api.main as main

        reservation = None
        settled = False
        response = None
        attempt_responses: list[Any] = []
        reservation = await self._reserve(owner, task)
        if reservation is False:
            return
        try:
            response = await self._run_agent_with_exception_retry(owner, task, resume=resume)
            attempt_responses.append(response)
            # Structured output can fail for one malformed attempt while the
            # same question succeeds on a fresh run. Retry once, from a fresh
            # dependency context, before accepting the prose-only fallback.
            if self._automatic_retry_permitted(response) and self._structured_output_failed(response) and self._task_allows_automatic_retry(task):
                retried = await self._run_agent(owner, task, resume=True)
                if retried is not None and not self._structured_output_failed(retried):
                    response = retried
                    attempt_responses.append(retried)
            if self.store.task_is_cancelled(owner.owner_id, task_id):
                self.store.close_running_steps(
                    owner.owner_id,
                    task_id,
                    status="cancelled",
                    summary="任务已取消，后续动作已停止" if zh else "Cancelled; remaining actions stopped.",
                )
                self._store_result(owner.owner_id, task_id, response)
                if reservation:
                    await self._release(owner, reservation)
                    settled = True
                self.store.transition(
                    owner.owner_id,
                    task_id,
                    "cancelled",
                    reason="cancelled_after_response",
                    summary="任务在回答生成后被取消；已生成的部分仍可查看" if zh else "Cancelled after the answer was generated; the generated result remains visible.",
                    zh=zh,
                )
                return
            if task.get("research_mode") == "deep":
                assess_deep_evidence_quality(response, language=task.get("language") or "zh-CN")
            status, reason = classify_response(response)
            plan = self._plans.pop(task_id, None)
            if plan is not None and status in {"completed", "partial"}:
                plan = evaluate_plan(plan, response)
                gaps = plan_gaps(plan)
                if gaps:
                    response.data_gaps = [*(getattr(response, "data_gaps", None) or []), *gaps]
                    payload = getattr(response, "payload", None)
                    if isinstance(payload, dict):
                        payload.setdefault("_meta", {})["plan_incomplete"] = True
                        payload["_meta"]["plan_id"] = task_id
                # A model-knowledge answer covers the question even when the plan
                # could not be verified; the UI labels it instead of downgrading it
                # to "partially completed".
                payload_meta = getattr(response, "payload", None)
                model_only = str((payload_meta or {}).get("_meta", {}).get("answer_basis") or "") == "model_knowledge" if isinstance(payload_meta, dict) else False
                if status == "completed" and plan.status != "completed" and not model_only:
                    status = "partial"
                    reason = "plan_incomplete"
                self.store.save_plan(
                    owner.owner_id,
                    task_id,
                    bot_id=plan.bot_id,
                    session_id=plan.session_id,
                    original_query=plan.original_query,
                    status=plan.status,
                    source=plan.source,
                    steps=plan.steps,
                    termination_condition=plan.termination_condition,
                )
            elif plan is not None:
                plan.status = "cancelled" if status == "waiting_user" else "failed"
                self.store.save_plan(
                    owner.owner_id,
                    task_id,
                    bot_id=plan.bot_id,
                    session_id=plan.session_id,
                    original_query=plan.original_query,
                    status=plan.status,
                    source=plan.source,
                    steps=plan.steps,
                    termination_condition=plan.termination_condition,
                )
            if self._automatic_retry_permitted(response) and self._task_allows_automatic_retry(task) and self._should_auto_retry(status, plan):
                first_response = response
                response, status, reason, plan = await self._retry_plan_once(
                    owner, {**task, "result": result_document(response)}, response, status, reason, plan
                )
                if response is not first_response:
                    attempt_responses.append(response)
            await self._stream_answer_tokens(owner.owner_id, task_id, response)
            self._store_result(owner.owner_id, task_id, response)
            self._record_usage(owner.owner_id, task_id, attempt_responses)
            self._create_candidates(owner, task_id, response)
            actual = main.final_request_cost(str(getattr(response, "intent", "")))
            if reservation:
                settled = await self._settle(owner, reservation, actual, task_id)
            waiting_reason, attention = clarification_waiting(response)
            if status == "waiting_user" and not waiting_reason:
                waiting_reason = "question"
                attention = {
                    "question": "请补充关键信息后再继续。" if zh else "Please clarify the key detail before I continue.",
                    "options": [],
                    "allow_free_text": True,
                }
            self.store.transition(
                owner.owner_id,
                task_id,
                status,
                reason=reason,
                summary=dropped_summary(reason, zh) if status == "dropped" and reason else None,
                zh=zh,
                **(
                    {"waiting_reason": waiting_reason, "attention": attention}
                    if status == "waiting_user"
                    else {}
                ),
            )
            # Honesty chrome: fill data_sources from real evidence; upgrade disposition.
            try:
                document = result_document(response)
                sources = collect_used_data_sources(response, document)
                if sources:
                    self.store.attach_authorization_data_sources(owner.owner_id, task_id, sources)
            except Exception:
                logger.debug("authorization data_sources attach skipped for %s", task_id, exc_info=True)
            try:
                outcome = disposition_for_outcome(status, reason, response, zh=zh)
                if outcome:
                    disp, code, display = outcome
                    self.store.finalize_disposition(
                        owner.owner_id,
                        input_ref=str(task.get("idempotency_key") or "") or None,
                        task_id=task_id,
                        disposition=disp,
                        reason_code=code,
                        reason_display=display,
                        session_id=task.get("session_id"),
                    )
            except Exception:
                logger.debug("disposition finalize skipped for %s", task_id, exc_info=True)
        except asyncio.CancelledError:
            self._save_plan_snapshot(owner.owner_id, task_id, self._plans.pop(task_id, None), "cancelled")
            if reservation and not settled:
                await self._release(owner, reservation)
                settled = True
            self.store.close_running_steps(
                owner.owner_id,
                task_id,
                status="cancelled",
                summary="任务已取消，后续动作已停止" if zh else "Cancelled; remaining actions stopped.",
            )
            self.store.transition(
                owner.owner_id,
                task_id,
                "cancelled",
                reason="cancelled_by_user",
                summary="任务已取消；已发出的外部请求无法保证撤销" if zh else "Task cancelled. External calls already sent cannot be guaranteed to be revoked.",
                zh=zh,
            )
            raise
        except Exception as exc:  # noqa: BLE001 - task boundary
            self._save_plan_snapshot(owner.owner_id, task_id, self._plans.pop(task_id, None), "failed")
            if reservation and not settled:
                await self._release(owner, reservation)
                settled = True
            code = getattr(exc, "code", type(exc).__name__)
            message = redact(str(exc))
            self.store.close_running_steps(
                owner.owner_id,
                task_id,
                status="failed",
                summary="任务失败，正在进行的步骤已停止" if zh else "Failed; the in-progress step stopped.",
            )
            self.store.append_event(
                owner.owner_id,
                task_id,
                kind="error",
                status="failed",
                summary=("研究执行失败：" if zh else "Research failed: ") + (message or code),
            )
            self.store.transition(
                owner.owner_id,
                task_id,
                "failed",
                reason=str(code),
                error_code=str(code),
                error_message=message,
                zh=zh,
            )
        finally:
            if reservation and not settled:
                try:
                    await self._release(owner, reservation)
                except Exception:  # pragma: no cover
                    logger.exception("Credit release after task failure failed")

    async def _reserve(self, owner: OwnerContext, task: dict[str, Any]):
        import api.main as main

        if not getattr(main.credit_service, "enforced", False):
            return None
        estimate = main.estimate_request_cost(task.get("query") or "")
        try:
            return await asyncio.to_thread(
                main.credit_service.reserve,
                owner.user,
                task["id"],
                estimate,
                {"session_id": task.get("session_id"), "estimated_amount": estimate},
            )
        except Exception as exc:  # CreditError
            code = getattr(exc, "code", type(exc).__name__)
            message = getattr(exc, "message", None) or str(exc)
            self.store.append_event(
                owner.owner_id,
                task["id"],
                kind="error",
                status="failed",
                summary=f"额度不足或计费不可用：{message}",
                allow_after_terminal=True,
            )
            self.store.transition(
                owner.owner_id,
                task["id"],
                "failed",
                reason=str(code),
                error_code=str(code),
                error_message=redact(message),
            )
            return False

    async def _settle(self, owner: OwnerContext, reservation, actual: int, task_id: str) -> bool:
        import api.main as main

        try:
            await asyncio.to_thread(
                main.credit_service.settle, owner.user, reservation, actual, {"task_id": task_id}
            )
            return True
        except Exception:
            logger.exception("Financial Bot credit settlement failed")
            return False

    async def _release(self, owner: OwnerContext, reservation) -> None:
        import api.main as main

        if not getattr(main.credit_service, "enforced", False):
            return
        await asyncio.to_thread(main.credit_service.release, owner.user, reservation, {"reason": "task_failed"})

    async def _prepare_plan(self, owner: OwnerContext, task: dict[str, Any], bot: dict[str, Any] | None, zh: bool, *, resume: bool = False) -> TaskPlan | None:
        """Create the authoritative step stack before the model starts."""
        if self._agent_runner is not None:
            return None
        task_id = task["id"]
        query = task.get("query") or ""
        bot_config = prompt_config_from_bot(bot)
        if resume:
            existing = self.store.get_plan(owner.owner_id, task_id)
            if not existing or not existing.get("steps"):
                return None
            try:
                plan = TaskPlan.model_validate(existing)
            except Exception:
                logger.exception("Stored task plan could not be resumed")
                return None
            plan.owner_id = owner.owner_id
            plan.bot_id = (bot or {}).get("id")
            plan.session_id = task.get("session_id")
            plan.status = "running"
            self._plans[task_id] = plan
            self._save_plan_snapshot(owner.owner_id, task_id, plan)
            self._progress_callback(owner, task)("plan", 12, "从持久化任务清单恢复，继续未完成步骤")
            return plan
        if not should_plan_query(query):
            return None
        import api.main as main
        try:
            self.store.save_plan(
                owner.owner_id,
                task_id,
                bot_id=(bot or {}).get("id"),
                session_id=task.get("session_id"),
                original_query=query,
                status="planning",
                source="template",
                steps=[],
                termination_condition="全部步骤 completed 才算完成；关键步骤重试失败则任务终止。",
            )
            self._progress_callback(owner, task)("plan", 12, "正在拆解任务清单并写入任务栈")
            runtime_config = main.settings_store.build_runtime_config(main.stock_service.config)
            planner = LLMPlanner(
                api_key=runtime_config.llm_api_key,
                base_url=runtime_config.llm_base_url,
                model=runtime_config.llm_model,
            )
            capabilities = bot_config.get("capabilities") or {}
            allowed = set(enabled_tools(capabilities))
            # Deep tasks use a deterministic plan so planning cannot consume
            # unaccounted model requests outside the task's request cap.
            plan = None if task.get("research_mode") == "deep" else await planner.plan(query, plan_id=task_id, allowed_tools=allowed)
            if plan is None:
                plan = deterministic_plan(query, plan_id=task_id)
            for step in plan.steps:
                if step.kind == "synthesis":
                    continue
                step.tools = [tool for tool in step.tools if tool in allowed]
                step.validation.tools = [tool for tool in step.validation.tools if tool in allowed]
                if not step.tools:
                    step.status = "failed"
                    step.last_error = "该步骤所需能力已被 Bot 设置关闭"
            plan.owner_id = owner.owner_id
            plan.bot_id = (bot or {}).get("id")
            plan.session_id = task.get("session_id")
            plan.status = "running"
            self.store.save_plan(
                owner.owner_id,
                task_id,
                bot_id=plan.bot_id,
                session_id=plan.session_id,
                original_query=plan.original_query,
                status=plan.status,
                source=plan.source,
                steps=plan.steps,
                termination_condition=plan.termination_condition,
            )
            self._plans[task_id] = plan
            self._progress_callback(owner, task)("plan", 16, f"任务清单已生成：{len(plan.steps)} 个步骤")
            return plan
        except Exception:
            logger.exception("Task planning failed; continuing without a plan")
            return None

    def _save_plan_snapshot(self, owner_id: str, task_id: str, plan: TaskPlan | None, status: str | None = None) -> None:
        if plan is None:
            return
        if status is not None:
            plan.status = status  # type: ignore[assignment]
        try:
            self.store.save_plan(
                owner_id,
                task_id,
                bot_id=plan.bot_id,
                session_id=plan.session_id,
                original_query=plan.original_query,
                status=plan.status,
                source=plan.source,
                steps=plan.steps,
                termination_condition=plan.termination_condition,
            )
        except Exception:
            logger.exception("Failed to persist task plan snapshot")

    async def _run_agent_with_exception_retry(
        self, owner: OwnerContext, task: dict[str, Any], *, resume: bool = False
    ):
        """Retry a direct agent exception once on the production path.

        Plan-level incompleteness is retried later after plan evaluation. This
        wrapper covers the other failure mode: the agent raises before returning
        a response at all.
        """
        try:
            return await self._run_agent(owner, task, resume=resume)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._agent_runner is not None or not self._task_allows_automatic_retry(task):
                raise
            zh = (task.get("language") or "auto") != "en-US"
            self.store.append_event(
                owner.owner_id,
                task["id"],
                kind="step",
                status="running",
                summary="Agent 执行异常，自动重试一次" if zh else "Agent raised an exception; retrying once automatically",
                step_id="automatic_retry",
                source_refs=[{"type": "reason_code", "name": type(exc).__name__}],
            )
            logger.warning("Agent exception for %s; retrying once: %s", task["id"], type(exc).__name__)
            try:
                retry_response = await self._run_agent(owner, task, resume=True)
            except asyncio.CancelledError:
                raise
            except Exception as retry_exc:
                self.store.append_event(
                    owner.owner_id,
                    task["id"],
                    kind="step",
                    status="failed",
                    summary="Agent 自动重试仍失败" if zh else "Automatic agent retry failed again",
                    step_id="automatic_retry",
                    source_refs=[{"type": "reason_code", "name": type(retry_exc).__name__}],
                )
                raise retry_exc from exc
            self.store.append_event(
                owner.owner_id,
                task["id"],
                kind="step",
                status="completed",
                summary="Agent 自动重试成功" if zh else "Automatic agent retry succeeded",
                step_id="automatic_retry",
                source_refs=[{"type": "reason_code", "name": "exception_recovered"}],
            )
            return retry_response

    async def _run_agent(self, owner: OwnerContext, task: dict[str, Any], *, resume: bool = False):
        if self._agent_runner is not None:
            return await self._agent_runner(owner, task, self._progress_callback(owner, task))

        import api.main as main
        from api.schemas import AgentHistoryTurn, AgentQuery

        runtime_config = main.settings_store.build_runtime_config(main.stock_service.config)
        selected_provider = ((task.get("authorization") or {}).get("scope") or {}).get("model_provider", "deepseek")
        if not runtime_config.llm_api_key and selected_provider != "chatgpt":
            from api.financial_bot.fallback_research import deterministic_price_research
            from api.financial_bot.finance_fallback import deterministic_financial_research

            query = task.get("query") or ""
            language = task.get("language") or "auto"
            fallback = deterministic_financial_research(query, language)
            if fallback is None:
                fallback = await deterministic_price_research(query, language)
            if fallback is not None:
                return fallback

        history: list[AgentHistoryTurn] = []
        for prior in self.store.list_tasks(
            owner.owner_id, session_id=task.get("session_id"), limit=12
        ):
            if prior["id"] == task["id"]:
                continue
            result = prior.get("result") or {}
            summary = str(result.get("summary") or "").strip()
            if not summary:
                continue
            history.append(
                AgentHistoryTurn(
                    role="user",
                    content=prior.get("query") or "",
                    stock_code=(result.get("identity") or {}).get("stock_code"),
                )
            )
            history.append(
                AgentHistoryTurn(
                    role="agent",
                    content=summary[:4000],
                    stock_code=(result.get("identity") or {}).get("stock_code"),
                )
            )
        thread = self.store.get_thread(owner.owner_id, task.get("session_id"))
        if thread and thread.get("parent_session_id"):
            parent_messages = self.store.list_messages(
                owner.owner_id,
                str(thread["parent_session_id"]),
                limit=24,
            )
            for message in parent_messages:
                if message.get("kind") in {"welcome", "proactive"}:
                    continue
                role = "user" if message.get("role") == "user" else "agent"
                content = str(message.get("content") or "").strip()
                if content:
                    history.append(AgentHistoryTurn(role=role, content=content[:4000]))
        profile = main.agent_memory_store.get_profile(task.get("session_id") or "")
        bot = self.store.bot_for_session(owner.owner_id, task.get("session_id"))
        bot_config = prompt_config_from_bot(bot)
        memory_context = (
            self.store.build_memory_context(
                owner.owner_id,
                bot_id=(bot or {}).get("id"),
                include_legacy=bool((bot or {}).get("is_default")),
            )
            if bot_config.get("memory_enabled", True)
            else []
        )
        profile["confirmed_memories"] = memory_context
        routine_count = len(self.store.list_monitors(owner.owner_id, bot_id=(bot or {}).get("id"))) if bot else 0
        rendered_bot = render_bot_prompt(
            bot or {},
            memory_text="\n".join(memory_context),
            history_count=max(0, len(history) // 2),
            routine_count=routine_count,
        )["rendered"]
        prompt_parts = [rendered_bot]
        if memory_context:
            memory_lines = [f"- {str(item).strip()[:500]}" for item in memory_context[:12] if str(item).strip()]
            if memory_lines:
                prompt_parts.append("## 长期记忆（当前 Bot 已确认）\n" + "\n".join(memory_lines))
        plan = await self._prepare_plan(owner, task, bot, bool(task.get("language") != "en-US"), resume=resume)
        if plan is not None:
            prompt_parts.append(format_plan_context(plan))
        if resume and isinstance(task.get("result"), dict):
            previous = str(task["result"].get("summary") or "").strip()
            if previous:
                prompt_parts.append(
                    "## 上次执行的部分结果（可能不完整；仍以任务清单校验为准）\n"
                    + previous[:3000]
                )
        bot_prompt = "\n\n".join(prompt_parts)
        payload = AgentQuery(
            query=task.get("query") or "",
            session_id=task.get("session_id"),
            bot_id=(bot or {}).get("id"),
            bot_name=(bot or {}).get("name"),
            bot_instructions=(bot or {}).get("instructions"),
            bot_prompt=bot_prompt,
            bot_capabilities=bot_config.get("capabilities") or {},
            ui_context=task.get("ui_context") if isinstance(task.get("ui_context"), dict) else None,
            request_id=task["id"],
            language=task.get("language") or "auto",
            research_mode=task.get("research_mode") or "quick",
            model_provider=((task.get("authorization") or {}).get("scope") or {}).get("model_provider", "deepseek"),
        )
        model_options = {"allow_chatgpt": owner.is_local} if payload.model_provider == "chatgpt" else {}
        return await main._run_agent_request(
            payload,
            history,
            profile,
            self._progress_callback(owner, task, plan),
            watchlist_store=None,
            **model_options,
        )

    @staticmethod
    def _structured_output_failed(response: Any) -> bool:
        payload = getattr(response, "payload", None)
        meta = payload.get("_meta") if isinstance(payload, dict) else None
        meta = meta if isinstance(meta, dict) else {}
        return meta.get("output_fallback") == "text" or meta.get("structured_output_validated") is False

    @staticmethod
    def _automatic_retry_permitted(response: Any) -> bool:
        payload = getattr(response, "payload", None) or {}
        meta = payload.get("_meta") if isinstance(payload, dict) else {}
        return not (isinstance(meta, dict) and meta.get("limit_reached"))

    @staticmethod
    def _task_allows_automatic_retry(task: dict) -> bool:
        return task.get("research_mode") != "deep" and TaskExecutor._auto_retry_enabled()

    @staticmethod
    def _auto_retry_enabled() -> bool:
        import os

        return os.getenv("AUTO_RETRY_PLAN", "1").strip().lower() in {"1", "true", "yes", "on"}

    def _should_auto_retry(self, status: str, plan: TaskPlan | None) -> bool:
        if self._agent_runner is not None:
            # Custom runners may be test doubles or embedding-specific; only the
            # production agent path has resumable plan semantics.
            return False
        if not self._auto_retry_enabled() or plan is None:
            return False
        if status not in {"partial", "failed"}:
            return False
        if plan.status == "completed":
            return False
        return any(step.status in {"retry", "failed", "pending", "running"} for step in plan.steps)

    async def _retry_plan_once(
        self,
        owner: OwnerContext,
        task: dict[str, Any],
        response: Any,
        status: str,
        reason: str | None,
        plan: TaskPlan | None,
    ):
        """Re-run an incomplete plan once, preserving the first result on failure."""
        task_id = task["id"]
        zh = (task.get("language") or "auto") != "en-US"
        if plan is not None:
            self.store.save_plan(
                owner.owner_id,
                task_id,
                bot_id=plan.bot_id,
                session_id=plan.session_id,
                original_query=plan.original_query,
                status=plan.status,
                source=plan.source,
                steps=plan.steps,
                termination_condition=plan.termination_condition,
            )
        self.store.append_event(
            owner.owner_id,
            task_id,
            kind="step",
            status="running",
            summary="任务清单未完成，自动重试一次" if zh else "Incomplete task plan; retrying once automatically",
            step_id="automatic_retry",
            source_refs=[{"type": "reason_code", "name": "plan_incomplete"}],
        )
        retry_task = {**task, "result": result_document(response)}
        try:
            retry_response = await self._run_agent(owner, retry_task, resume=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Automatic plan retry failed for %s: %s", task_id, type(exc).__name__)
            response.data_gaps = [
                *(getattr(response, "data_gaps", None) or []),
                "自动重试失败；保留首次部分结果。" if zh else "Automatic retry failed; the first partial result is preserved.",
            ]
            self.store.append_event(
                owner.owner_id,
                task_id,
                kind="step",
                status="failed",
                summary="自动重试失败，已保留首次部分结果" if zh else "Automatic retry failed; first partial result preserved",
                step_id="automatic_retry",
                source_refs=[{"type": "reason_code", "name": type(exc).__name__}],
            )
            return response, status, reason, plan
        retry_status, retry_reason = classify_response(retry_response)
        retry_plan = self._plans.pop(task_id, None)
        if retry_plan is None:
            # A custom runner may not prepare a new plan.  Keep the first plan
            # and re-evaluate it instead of silently upgrading a partial result.
            retry_plan = plan
        if retry_plan is not None and retry_status in {"completed", "partial"}:
            retry_plan = evaluate_plan(retry_plan, retry_response)
            gaps = plan_gaps(retry_plan)
            if gaps:
                retry_response.data_gaps = [*(getattr(retry_response, "data_gaps", None) or []), *gaps]
                payload = getattr(retry_response, "payload", None)
                if isinstance(payload, dict):
                    payload.setdefault("_meta", {})["plan_incomplete"] = True
                    payload["_meta"]["plan_id"] = task_id
            if retry_status == "completed" and retry_plan.status != "completed":
                retry_status = "partial"
                retry_reason = "plan_incomplete"
            self.store.save_plan(
                owner.owner_id,
                task_id,
                bot_id=retry_plan.bot_id,
                session_id=retry_plan.session_id,
                original_query=retry_plan.original_query,
                status=retry_plan.status,
                source=retry_plan.source,
                steps=retry_plan.steps,
                termination_condition=retry_plan.termination_condition,
            )
        elif retry_plan is not None:
            retry_plan.status = "cancelled" if retry_status == "waiting_user" else "failed"
            self.store.save_plan(
                owner.owner_id,
                task_id,
                bot_id=retry_plan.bot_id,
                session_id=retry_plan.session_id,
                original_query=retry_plan.original_query,
                status=retry_plan.status,
                source=retry_plan.source,
                steps=retry_plan.steps,
                termination_condition=retry_plan.termination_condition,
            )
        self.store.append_event(
            owner.owner_id,
            task_id,
            kind="step",
            status="completed" if retry_status in {"completed", "partial"} else "failed",
            summary="自动重试完成" if zh and retry_status in {"completed", "partial"} else ("自动重试结束" if zh else "Automatic retry finished"),
            step_id="automatic_retry",
            source_refs=[{"type": "reason_code", "name": retry_reason or retry_status}],
        )
        return retry_response, retry_status, retry_reason, retry_plan

    def _progress_callback(self, owner: OwnerContext, task: dict[str, Any], plan: TaskPlan | None = None):
        task_id = task["id"]
        zh = (task.get("language") or "auto") != "en-US"
        last: list[tuple[str, str, str]] = []
        # One finding per fact, across every run of this task: a retried run
        # re-executes the same tools and the person must not see "PE 21.9" twice.
        seen_findings = self._findings_seen.setdefault(task_id, set())
        active_stage: dict[str, str] = {"step_id": "", "summary": ""}

        def report(stage: str, progress_pct: int, message: str, meta: Optional[dict[str, Any]] = None) -> None:
            if self.store.task_is_cancelled(owner.owner_id, task_id):
                raise asyncio.CancelledError()
            meta = meta or {}
            token_delta = str(meta.get("token_delta") or "")
            if token_delta:
                self._answer_streamed.add(task_id)
                self.store.append_event(
                    owner.owner_id,
                    task_id,
                    kind="token",
                    status="streaming",
                    summary=token_delta[:200],
                    source_refs=[{"type": "token", "name": "model_delta"}],
                    allow_after_terminal=False,
                )
                return
            tool = str(meta.get("tool") or "").strip()
            if tool and plan is not None:
                for step in plan.steps:
                    if tool in step.tools and step.status == "pending":
                        step.status = "running"
                        self._save_plan_snapshot(owner.owner_id, task_id, plan)
                        break
            if tool:
                step_id = f"tool:{tool}"
                if stage == "tool_completed":
                    status = "completed"
                elif stage == "tool_error":
                    status = "failed"
                else:
                    status = "running"
                refs = [{"type": "tool", "name": tool}]
                if meta.get("endpoint"):
                    refs.append({"type": "endpoint", "name": str(meta["endpoint"])})
                if meta.get("reason_code"):
                    refs.append({"type": "reason_code", "name": str(meta["reason_code"])})
            else:
                step_id = f"stage:{stage or 'progress'}"
                status = "completed" if stage in {"compose_response", "publish_answer", "persist_memory"} else "running"
                refs = []
                if status == "running" and active_stage["step_id"] and active_stage["step_id"] != step_id:
                    # A new stage means the previous one finished. Publish that
                    # as a real update instead of leaving stale "in progress".
                    self.store.append_event(
                        owner.owner_id,
                        task_id,
                        kind="step",
                        status="completed",
                        summary=active_stage["summary"],
                        step_id=active_stage["step_id"],
                    )
                if status == "running":
                    active_stage["step_id"] = step_id
                    active_stage["summary"] = str(message or stage or "progress")
            summary = str(message or "").strip() or (stage or "progress")
            key = (step_id, status, summary)
            if key in last:
                return
            last.append(key)
            self.store.append_event(
                owner.owner_id,
                task_id,
                kind="step",
                status=status,
                summary=summary,
                step_id=step_id,
                source_refs=refs,
            )
            finding = str(meta.get("finding") or "").strip()
            if finding and finding in seen_findings:
                finding = ""
            if tool and status == "completed" and finding:
                seen_findings.add(finding)
                # A finding is a real result, so it goes out as its own bubble
                # while the run is still going - not buried in the final text.
                self.store.append_event(
                    owner.owner_id,
                    task_id,
                    kind="notice",
                    status="completed",
                    summary=finding[:400],
                    step_id=step_id,
                    source_refs=refs,
                )
            if tool and status in {"completed", "failed"}:
                try:
                    task_row = self.store.get_task(owner.owner_id, task_id)
                    session_id = task_row.get("session_id")
                    if session_id:
                        bot = self.store.bot_for_session(owner.owner_id, session_id)
                        self.store.append_message(
                            owner.owner_id,
                            session_id=str(session_id),
                            role="assistant",
                            kind="notice" if (status == "completed" and finding) else "activity",
                            content=(finding or summary)[:600],
                            task_id=task_id,
                            bot_id=(bot or {}).get("id"),
                            metadata={"step_id": step_id, "status": status, "tool": tool},
                        )
                except Exception:
                    logger.debug("Activity message write skipped for task %s", task_id, exc_info=True)

        return report

    # -------------------------------------------------------------- persistence
    async def _stream_answer_tokens(self, owner_id: str, task_id: str, response: Any) -> None:
        """Publish the accepted answer as bounded token-sized SSE chunks.

        The model's structured final_result is assembled before validation; we
        deliberately stream only the validated summary, so a draft that is later
        discarded can never leak into the conversation.  This is visual token
        cadence, not raw provider chain-of-thought.
        """
        if task_id in self._answer_streamed:
            return
        summary = str(getattr(response, "summary", "") or "")
        if not summary:
            return
        chunks = [summary[index:index + 28] for index in range(0, len(summary), 28)]
        for chunk in chunks:
            self.store.append_event(
                owner_id,
                task_id,
                kind="token",
                status="streaming",
                summary=chunk,
                source_refs=[{"type": "token", "name": "answer"}],
                allow_after_terminal=False,
            )
            await asyncio.sleep(0.012)

    def _store_result(self, owner_id: str, task_id: str, response: Any) -> None:
        document = result_document(response)
        serialized = json.dumps(document, ensure_ascii=False, default=str)
        with self.store.db.connect(owner_id=owner_id, write=True) as conn:
            conn.execute(
                "UPDATE fb_tasks SET result_json = ?, updated_at = ? WHERE id = ? AND owner_id = ?",
                (serialized, _now(), task_id, owner_id),
            )
        self._answer_streamed.discard(task_id)
        self.store.complete_running_steps(owner_id, task_id)
        self.store.append_event(
            owner_id,
            task_id,
            kind="answer",
            status="ready",
            summary="最终回答已生成" if document.get("summary") else "回答为空",
            source_refs=[{"type": "citations", "count": len(document.get("citations") or [])}],
            allow_after_terminal=True,
        )
        try:
            task = self.store.get_task(owner_id, task_id)
            summary = str(document.get("summary") or "").strip()
            if task.get("session_id") and summary:
                bot = self.store.bot_for_session(owner_id, task.get("session_id"))
                workflow = document.get("workflow")
                if isinstance(workflow, dict):
                    self.store.set_thread_workflow(owner_id, str(task["session_id"]), workflow, task_id)
                self.store.append_message(
                    owner_id,
                    session_id=str(task["session_id"]),
                    role="assistant",
                    kind="answer",
                    content=summary,
                    task_id=task_id,
                    bot_id=(bot or {}).get("id"),
                    metadata={"source": "task_result", "intent": document.get("intent")},
                )
        except Exception:
            logger.debug("Conversation message write skipped for task %s", task_id, exc_info=True)

    def _record_usage(self, owner_id: str, task_id: str, responses: Any) -> None:
        items = responses if isinstance(responses, (list, tuple)) else [responses]
        input_tokens = output_tokens = 0
        cost_usd = 0.0
        for response in items:
            payload = getattr(response, "payload", None) or {}
            meta = payload.get("_meta") if isinstance(payload, dict) else {}
            meta = meta if isinstance(meta, dict) else {}
            usage = meta.get("usage") if isinstance(meta.get("usage"), dict) else {}
            input_tokens += int(usage.get("input_tokens") or 0)
            output_tokens += int(usage.get("output_tokens") or 0)
            cost_usd += float(meta.get("estimated_cost_usd") or 0)
        self.store.record_usage(
            owner_id,
            task_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        )

    def _create_candidates(self, owner: OwnerContext, task_id: str, response: Any) -> None:
        proposals = candidate_payloads(response)
        if not proposals:
            return
        summary = str(getattr(response, "summary", "") or "")
        task = self.store.get_task(owner.owner_id, task_id)
        bot = self.store.bot_for_session(owner.owner_id, task.get("session_id"))
        for proposal in proposals:
            self.store.add_candidate(
                owner.owner_id,
                task_id=task_id,
                proposed_text=proposal["text"],
                rationale=proposal.get("rationale") or None,
                source_text=summary[:600] or None,
                source_ref=f"task:{task_id}",
                bot_id=(bot or {}).get("id"),
            )


def _now() -> str:
    from api.financial_bot.store import utc_now

    return utc_now()
