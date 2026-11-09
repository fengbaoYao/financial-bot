"""Safe model-assisted fallback when research tools or synthesis fail.

This is *not* an unrestricted knowledge answer. It runs after provider /
tool / synthesis failures, and its prompt forbids fabricating citation URLs
or claiming provider provenance. It may answer the user question using
retrieved evidence plus clearly labeled model knowledge.

For structured field fill of partial provider gaps, see data_gap_fill.py.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Awaitable, Callable, Optional

from api.schemas import AgentResponse, ResearchEvidence, ResearchStep

logger = logging.getLogger(__name__)

CompleteFn = Callable[[list[dict[str, str]]], Awaitable[str]]

_INCOMPLETE_SUMMARY = re.compile(
    r"研究综合未完成|研究汇总未能完成|Research synthesis incomplete|"
    r"Research synthesis could not|综合未完成",
    re.I,
)



MODEL_FILL_TOOL = "model_knowledge_fill"


def append_model_fill_step(
    response: AgentResponse,
    *,
    language: str,
    model_provider: str,
    note: str | None = None,
) -> AgentResponse:
    """Expose model fill as a normal research_steps tool row (not a blue data card)."""
    en = language == "en-US"
    endpoint = "模型知识补齐" if not en else "model_knowledge"
    provider = "chatgpt" if (model_provider or "").lower() == "chatgpt" else "deepseek"
    steps = [step for step in (response.research_steps or []) if step.tool != MODEL_FILL_TOOL]
    steps.append(
        ResearchStep(
            tool=MODEL_FILL_TOOL,
            status="completed",
            retrieved_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            evidence_count=0,
            reason_code="unverified_model",
            endpoint=f"{endpoint} · {provider}",
        )
    )
    response.research_steps = steps
    # Drop model_fill data cards from primary payload so UI uses the tool row.
    payload = dict(response.payload or {})
    cards = [
        card for card in (payload.get("data_cards") or [])
        if not (isinstance(card, dict) and card.get("kind") == "model_fill")
    ]
    if cards:
        payload["data_cards"] = cards
    elif "data_cards" in payload:
        payload.pop("data_cards", None)
    meta = dict(payload.get("_meta") or {})
    meta["model_fill_as_tool"] = True
    if note:
        meta["model_fill_note"] = note[:240]
    payload["_meta"] = meta
    response.payload = payload
    return response


def _enabled() -> bool:
    """Prefer LLM_DATA_GAP_FILL; fall back to LLM_GAP_FILL; default on."""
    raw = os.getenv("LLM_DATA_GAP_FILL", "").strip()
    if not raw:
        raw = os.getenv("LLM_GAP_FILL", "1").strip()
    return raw.lower() in {"1", "true", "yes", "on"}


def _tool_snapshot(tool_results: Optional[list[dict[str, Any]]], limit: int = 12) -> str:
    rows: list[dict[str, Any]] = []
    for item in tool_results or []:
        tool = str(item.get("tool") or "")
        if not tool:
            continue
        status = item.get("status") or "completed"
        entry: dict[str, Any] = {"tool": tool, "status": status}
        gaps = item.get("data_gaps") or []
        if gaps:
            entry["data_gaps"] = [str(g)[:160] for g in gaps[:3]]
        reason = item.get("reason_code")
        if reason:
            entry["reason_code"] = str(reason)[:80]
        data = item.get("data")
        if status == "unavailable" or data in (None, [], {}):
            entry["data"] = None
        elif isinstance(data, list):
            entry["data"] = data[:3]
        elif isinstance(data, dict):
            # Keep compact, JSON-serializable subset.
            slim = {}
            for key in (
                "quote", "metadata", "price_moves", "windows", "periods",
                "benchmark_label", "benchmark_code", "items", "company",
                "code", "name", "title", "published_at", "source",
            ):
                if key in data:
                    slim[key] = data[key]
            if not slim:
                # Fall back to a tiny preview of keys only.
                slim = {"keys": sorted(str(k) for k in list(data.keys())[:12])}
            entry["data"] = slim
        else:
            entry["data"] = str(data)[:240]
        rows.append(entry)
        if len(rows) >= limit:
            break
    try:
        return json.dumps(rows, ensure_ascii=False, default=str)[:6000]
    except Exception:
        return "[]"


def _evidence_lines(evidence: Optional[list[ResearchEvidence]]) -> str:
    lines = []
    for item in (evidence or [])[:10]:
        bits = [item.source or "", item.title or "", item.data_as_of or ""]
        if item.id:
            bits.append(f"[{item.id}]")
        line = " · ".join(part for part in bits if part).strip(" ·")
        if line:
            lines.append(f"- {line}")
    return "\n".join(lines) or "(none)"


def needs_research_recovery(response: AgentResponse) -> bool:
    """True when the research answer stalled on incomplete synthesis / tool failure."""
    if response.intent in {"clarification", "greeting"}:
        return False
    meta = (response.payload or {}).get("_meta") if isinstance(response.payload, dict) else None
    if isinstance(meta, dict):
        if meta.get("llm_research_recovery") or meta.get("engine") == "llm_gap_fill":
            return False
        if meta.get("synthesis_incomplete") or meta.get("limit_reached"):
            return True
        if meta.get("deep_research_incomplete") and not (response.summary or "").strip():
            return True
    summary = response.summary or ""
    if _INCOMPLETE_SUMMARY.search(summary):
        return True
    for gap in response.data_gaps or []:
        if _INCOMPLETE_SUMMARY.search(str(gap)):
            return True
    return False


async def synthesize_gap(
    query: str,
    language: str,
    *,
    evidence: Optional[list[ResearchEvidence]] = None,
    gaps: Optional[list[str]] = None,
    model_provider: str = "deepseek",
    complete_fn: Optional[CompleteFn] = None,
) -> Optional[AgentResponse]:
    """Return an explicitly unverified gap explanation, or None if unavailable."""
    if not _enabled():
        return None
    try:
        en = language == "en-US"
        evidence_text = _evidence_lines(evidence)
        gap_text = "\n".join(f"- {item}" for item in (gaps or [])[:10])
        if en:
            system = (
                "You are a financial research fallback. The live provider data is missing or "
                "incomplete. You MUST NOT invent citation URLs or claim values came from "
                "iFinD, Fuyao, yfinance, SEC, Finnhub or other live providers. "
                "Answer the user's question as helpfully as you can. When you use model "
                "knowledge rather than retrieved evidence, label it clearly as unverified "
                "model fill. Prefer short headings and plain text. "
                "If a specific number cannot be confirmed, say so."
            )
            user = (
                f"Question:\n{query}\n\nAvailable evidence:\n{evidence_text}\n\n"
                f"Known gaps:\n{gap_text or '(provider data unavailable)'}"
            )
        else:
            system = (
                "你是金融研究兜底助手。当前实时工具数据缺失或不完整。"
                "严禁编造引用 URL，也不得声称内容来自 iFinD/Fuyao/yfinance/SEC 等数据源。"
                "请尽量直接回答用户问题；凡使用模型知识而非已检索证据的内容，必须明确标注为"
                "「模型知识补齐 / 未核验」。用简短标题和纯文本。"
                "无法确认的具体数字要直接说明无法确认。"
            )
            user = (
                f"问题：\n{query}\n\n已有证据：\n{evidence_text}\n\n"
                f"已知缺口：\n{gap_text or '（数据源不可用）'}"
            )

        from api.financial_bot.data_gap_fill import complete_with_selected_provider

        runner = complete_fn or (
            lambda messages: complete_with_selected_provider(
                messages, model_provider=model_provider
            )
        )
        text = await runner([
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ])
        if not text:
            return None
        prefix = (
            "Model knowledge fill (unverified)\n\n"
            if en
            else "模型知识补齐（未核验）\n\n"
        )
        if not text.lstrip().startswith(("模型知识补齐", "Model knowledge fill", "未核验")):
            text = prefix + text
        model_label = "chatgpt" if (model_provider or "").lower() == "chatgpt" else "deepseek"
        gap_response = AgentResponse(
            intent="gap_explanation",
            summary=text,
            actions=[],
            citations=[],
            evidence=list(evidence or [])[:8],
            data_gaps=(gaps or []) + (
                ["Model-assisted answer; numeric facts were not provider-verified."]
                if en
                else ["模型辅助回答；数字事实未经数据源核验。"]
            ),
            payload={
                "_meta": {
                    "engine": "llm_gap_fill",
                    "synthesis_incomplete": False,
                    "unverified": True,
                    "llm_research_recovery": True,
                    "model_provider": model_label,
                    "answer_basis": "model_knowledge",
                }
            },
        )
        return append_model_fill_step(
            gap_response,
            language=language,
            model_provider=model_provider,
            note="模型知识补齐（未核验）" if not en else "Model knowledge fill (unverified)",
        )
    except Exception as exc:
        logger.warning("LLM gap fill failed: %s", type(exc).__name__)
        return None


async def recover_incomplete_synthesis(
    response: AgentResponse,
    *,
    query: str,
    language: str,
    model_provider: str = "deepseek",
    tool_results: Optional[list[dict[str, Any]]] = None,
    complete_fn: Optional[CompleteFn] = None,
) -> AgentResponse:
    """Replace an incomplete evidence-only stall with a labeled model answer.

    Successful tool evidence is preserved. No citation URLs are invented.
    """
    if not _enabled() or not needs_research_recovery(response):
        return response

    en = language == "en-US"
    snapshot = _tool_snapshot(tool_results)
    evidence_text = _evidence_lines(response.evidence)
    gap_text = "\n".join(f"- {item}" for item in (response.data_gaps or [])[:12]) or (
        "(tools failed or returned empty)" if en else "（工具失败或结果为空）"
    )
    prior = (response.summary or "")[:1500]

    if en:
        system = (
            "You finish a financial research answer after some tools failed, returned "
            "empty, timed out, or synthesis aborted. Write a usable answer to the user "
            "question. Rules: (1) Prefer facts present in the tool snapshot / evidence; "
            "(2) You may add model knowledge for missing pieces but MUST label those "
            "parts as unverified model fill; (3) Never invent citation URLs or IDs; "
            "(4) Never claim a value came from iFinD, Fuyao, yfinance, SEC or other "
            "providers unless that provider appears in the snapshot; "
            "(5) Do not say research synthesis is incomplete — give the best answer "
            "with clear gaps; (6) Short headings, plain text, Chinese or English to "
            "match the user."
        )
        user = (
            f"Question:\n{query}\n\nTool snapshot (JSON):\n{snapshot}\n\n"
            f"Evidence lines:\n{evidence_text}\n\nGaps:\n{gap_text}\n\n"
            f"Prior stalled summary (do not repeat the stall wording):\n{prior}"
        )
    else:
        system = (
            "研究工具失败、空结果、超时或综合中止后，由你给出可用的金融研究回答。"
            "规则：(1) 优先使用工具快照/证据里已有的事实；"
            "(2) 缺失部分可用模型知识补齐，但必须明确标注「模型知识补齐 / 未核验」；"
            "(3) 严禁编造引用 URL 或引用 ID；"
            "(4) 除非快照里出现对应数据源，否则不得声称来自 iFinD/Fuyao/yfinance/SEC；"
            "(5) 不要再写「研究综合未完成」——直接给出尽量可用的回答并列出缺口；"
            "(6) 短标题、纯文本，语言跟用户一致。"
        )
        user = (
            f"问题：\n{query}\n\n工具快照（JSON）：\n{snapshot}\n\n"
            f"证据行：\n{evidence_text}\n\n缺口：\n{gap_text}\n\n"
            f"此前卡住的摘要（不要重复卡住话术）：\n{prior}"
        )

    try:
        from api.financial_bot.data_gap_fill import complete_with_selected_provider

        runner = complete_fn or (
            lambda messages: complete_with_selected_provider(
                messages, model_provider=model_provider
            )
        )
        text = await runner([
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ])
    except Exception as exc:
        logger.warning("research recovery LLM call failed: %s", type(exc).__name__)
        return response

    cleaned = (text or "").strip()
    if not cleaned:
        return response

    prefix = "模型知识补齐（未核验）\n\n" if not en else "Model knowledge fill (unverified)\n\n"
    if not cleaned.lstrip().startswith(("模型知识补齐", "Model knowledge fill", "未核验")):
        cleaned = prefix + cleaned

    # Drop stall-only gaps; keep real tool gaps and add an explicit fill note.
    kept_gaps = [
        str(g).strip()
        for g in (response.data_gaps or [])
        if str(g).strip() and not _INCOMPLETE_SUMMARY.search(str(g))
    ]
    note = (
        "Answer includes model knowledge fill; not provider-verified."
        if en
        else "回答含模型知识补齐，未经数据源核验。"
    )
    if note not in kept_gaps:
        kept_gaps.append(note)

    payload = dict(response.payload or {})
    meta = dict(payload.get("_meta") or {})
    has_source = bool(response.evidence) or any(
        (item.get("status") != "unavailable")
        and item.get("data") not in (None, [], {})
        and item.get("tool") not in {None, "", "search_stocks"}
        for item in (tool_results or [])
    )
    meta.update({
        "llm_research_recovery": True,
        "llm_research_recovery_provider": (
            "chatgpt" if (model_provider or "").lower() == "chatgpt" else "deepseek"
        ),
        "unverified": True,
        "synthesis_incomplete": False,
        "answer_basis": "source_data" if has_source else "model_knowledge",
        "answer_basis_detail": (
            "source_data_with_model_fill" if has_source else "model_knowledge"
        ),
    })
    # Preserve engine lineage but mark recovery.
    if meta.get("engine") in {None, "pydantic_ai"}:
        meta["recovery_engine"] = "llm_research_recovery"
    payload["_meta"] = meta

    # Keep intent as a normal research answer when we recovered usable prose.
    intent = response.intent
    if intent in {"error", "gap_explanation"}:
        intent = "pydantic_ai_agent"

    recovered = AgentResponse(
        intent=intent,
        summary=cleaned,
        actions=list(response.actions or []),
        ui_components=list(getattr(response, "ui_components", None) or []),
        citations=list(response.citations or []),  # do not invent; keep existing only
        evidence=list(response.evidence or []),
        data_gaps=kept_gaps,
        clarification=response.clarification,
        research_steps=list(response.research_steps or []),
        payload=payload,
    )
    return append_model_fill_step(
        recovered,
        language=language,
        model_provider=model_provider,
        note="模型知识补齐（未核验）" if not en else "Model knowledge fill (unverified)",
    )
