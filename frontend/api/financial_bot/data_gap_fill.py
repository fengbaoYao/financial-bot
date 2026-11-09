"""Fill missing provider fields via the currently selected LLM.

Provider evidence is never overwritten. Model fills are labeled unverified and
must not invent citation URLs or claim to come from iFinD/Fuyao/yfinance/SEC.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from api.schemas import AgentResponse, ResearchEvidence

logger = logging.getLogger(__name__)

CompleteFn = Callable[[list[dict[str, str]]], Awaitable[str]]

MATERIAL_TOOLS = frozenset({
    "get_company_fundamentals",
    "get_financial_statements",
    "get_benchmark_comparison",
    "get_sec_company_data",
    "get_valuations",
    "get_stock_analysis",
    "get_stock_news",
    "get_analyst_estimates",
    "get_consensus_estimates",
    "get_cninfo_announcements",
    "get_index_constituents",
    "web_search",
    "read_public_source",
    "get_global_news",
})

_PROVIDER_SOURCES = frozenset({
    "ifind", "fuyao", "yfinance", "sec", "finnhub", "eastmoney", "cninfo",
    "sina", "tencent", "akshare", "ths", "brave",
})


def data_gap_fill_enabled() -> bool:
    """Prefer LLM_DATA_GAP_FILL; fall back to LLM_GAP_FILL; default on."""
    raw = os.getenv("LLM_DATA_GAP_FILL", "").strip()
    if not raw:
        raw = os.getenv("LLM_GAP_FILL", "1").strip()
    return raw.lower() in {"1", "true", "yes", "on"}


def _mark_fill_failed(response: AgentResponse, language: str) -> AgentResponse:
    note = ("Model fill failed; existing evidence and gaps are preserved." if language == "en-US"
            else "模型补齐未成功，已保留现有证据和数据缺口。")
    gaps = list(response.data_gaps or [])
    if note not in gaps:
        gaps.append(note)
    response.data_gaps = gaps
    payload = dict(response.payload or {})
    meta = dict(payload.get("_meta") or {})
    meta["llm_data_gap_fill_failed"] = True
    payload["_meta"] = meta
    response.payload = payload
    return response


def collect_material_gaps(
    response: AgentResponse,
    tool_results: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, str]]:
    """Return structured gap descriptors for material missing/failed fields."""
    gaps: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(field_key: str, tool: str, description: str) -> None:
        key = field_key.strip()
        if not key or key in seen:
            return
        seen.add(key)
        gaps.append({
            "field_key": key,
            "tool": tool,
            "description": description[:400],
        })

    for item in tool_results or []:
        tool = str(item.get("tool") or "")
        if tool not in MATERIAL_TOOLS:
            continue
        status = item.get("status") or "completed"
        data = item.get("data")
        if status == "unavailable":
            reason = "; ".join(
                str(x) for x in (item.get("data_gaps") or [])[:3]
            ) or str(item.get("reason_code") or "provider unavailable")
            add(f"{tool}.payload", tool, f"{tool} failed: {reason}")
            continue
        if not data:
            add(f"{tool}.payload", tool, f"{tool} returned no data")
            continue
        if isinstance(data, dict):
            for note in (data.get("data_gaps") or item.get("data_gaps") or [])[:6]:
                text = str(note).strip()
                if text:
                    add(f"{tool}.gap:{hashlib.sha1(text.encode()).hexdigest()[:8]}", tool, text)
            if tool in {"get_company_fundamentals", "get_financial_statements"}:
                periods = data.get("periods") or []
                if not periods:
                    add(f"{tool}.periods", tool, "Financial statement periods missing")
            if tool == "get_benchmark_comparison" and not (data.get("windows") or []):
                add(f"{tool}.windows", tool, "Benchmark windows missing")
            if tool == "get_valuations" and not (data.get("items") or []):
                add(f"{tool}.items", tool, "Valuation rows missing")
            if tool == "get_sec_company_data" and not (data.get("financials") or []):
                add(f"{tool}.financials", tool, "SEC standardized financials missing")

    for note in (response.data_gaps or [])[:12]:
        text = str(note).strip()
        if not text:
            continue
        # Skip notes that already say model fill / explanation-only.
        if re.search(r"模型知识|model knowledge|llm_gap|未核验的兜底", text, re.I):
            continue
        add(f"response.gap:{hashlib.sha1(text.encode()).hexdigest()[:8]}", "response", text)

    return gaps[:12]


def _provider_field_present(response: AgentResponse, field_key: str) -> bool:
    """True when a non-model provider already supplied this field's card/evidence."""
    payload = response.payload if isinstance(response.payload, dict) else {}
    cards = payload.get("data_cards") or []
    tool_hint = field_key.split(".", 1)[0]
    kind_map = {
        "get_company_fundamentals": "financials",
        "get_financial_statements": "financials",
        "get_valuations": "valuations",
        "get_benchmark_comparison": "benchmark",
        "get_analyst_estimates": "estimates",
        "get_consensus_estimates": "estimates",
        "get_cninfo_announcements": "announcements",
        "get_index_constituents": "index",
    }
    want_kind = kind_map.get(tool_hint)
    for card in cards:
        if not isinstance(card, dict):
            continue
        provider = str(card.get("provider") or "").strip().lower()
        if provider in {"model_knowledge", "llm_gap_fill", "模型知识补齐"}:
            continue
        if want_kind and card.get("kind") == want_kind:
            rows = card.get("rows") or card.get("years") or card.get("targets")
            if rows:
                return True
    return False


async def complete_with_selected_provider(
    messages: list[dict[str, str]],
    *,
    model_provider: str = "deepseek",
) -> str:
    """One-shot text completion via DeepSeek env or the ChatGPT plan session."""
    provider = (model_provider or "deepseek").strip().lower()
    if provider == "chatgpt":
        from api.financial_bot import chatgpt_session
        from api.financial_bot.chatgpt_provider import (
            DEFAULT_CHATGPT_REASONING_EFFORT,
            PREFERRED_CHATGPT_MODEL,
            select_chatgpt_model,
        )
        from openai import AsyncOpenAI

        if not chatgpt_session.is_signed_in():
            raise RuntimeError("chatgpt_not_signed_in")
        auth = chatgpt_session.load_auth_module()
        models = await __import__("asyncio").to_thread(auth.list_models)
        choices = [slug for _, slug in (models or [])]
        selected = select_chatgpt_model(choices)
        client = AsyncOpenAI(
            api_key=auth.credential(),
            base_url="https://api.openai.com/v1",
            max_retries=0,
            timeout=45,
        )
        # Prefer Responses for terra / Codex plan models; fall back to chat.
        try:
            response = await client.responses.create(
                model=selected or PREFERRED_CHATGPT_MODEL,
                input=[{"role": m["role"], "content": m["content"]} for m in messages],
                reasoning={"effort": DEFAULT_CHATGPT_REASONING_EFFORT},
                max_output_tokens=1200,
            )
            text = getattr(response, "output_text", None) or ""
            if not text and getattr(response, "output", None):
                chunks = []
                for item in response.output:
                    for part in getattr(item, "content", None) or []:
                        value = getattr(part, "text", None)
                        if value:
                            chunks.append(value)
                text = "".join(chunks)
            if text.strip():
                return text.strip()
        except Exception as exc:
            logger.info("ChatGPT responses fill failed (%s); trying chat.completions", type(exc).__name__)
        response = await client.chat.completions.create(
            model=selected or PREFERRED_CHATGPT_MODEL,
            messages=messages,
            temperature=0.2,
            max_tokens=1200,
        )
        return (response.choices[0].message.content or "").strip()

    import api.main as main

    runtime = main.settings_store.build_runtime_config(main.stock_service.config)
    if not runtime.llm_api_key:
        raise RuntimeError("llm_not_configured")
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=runtime.llm_api_key,
        base_url=runtime.llm_base_url,
        max_retries=0,
        timeout=30,
    )
    response = await client.chat.completions.create(
        model=runtime.llm_model,
        messages=messages,
        temperature=0.2,
        max_tokens=1200,
    )
    return (response.choices[0].message.content or "").strip()


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
    if fence:
        raw = fence.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return None
        raw = raw[start : end + 1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _sanitize_fill(row: dict[str, Any]) -> dict[str, Any] | None:
    field_key = str(row.get("field_key") or "").strip()[:120]
    value = row.get("value")
    if not field_key or value is None or value == "":
        return None
    # Never accept fabricated URLs or provider-looking source labels.
    url = row.get("url")
    if isinstance(url, str) and url.strip():
        return None
    source = str(row.get("source") or "").strip().lower()
    # Reject fills that claim a live provider as their source.
    if any(token in source for token in _PROVIDER_SOURCES):
        return None
    note = str(row.get("note") or "").strip()[:240]
    period = str(row.get("period") or "").strip()[:40] or None
    unit = str(row.get("unit") or "").strip()[:40] or None
    confidence = str(row.get("confidence") or "low").strip().lower()
    if confidence not in {"low", "medium"}:
        confidence = "low"
    if isinstance(value, (dict, list)):
        rendered = json.dumps(value, ensure_ascii=False)[:500]
    else:
        rendered = str(value).strip()[:500]
    if not rendered:
        return None
    return {
        "field_key": field_key,
        "value": rendered,
        "period": period,
        "unit": unit,
        "confidence": confidence,
        "note": note,
        "source": "model_knowledge",
    }


def _model_evidence(fills: list[dict[str, Any]], language: str) -> ResearchEvidence:
    identity = hashlib.sha256(
        json.dumps([row["field_key"] for row in fills], ensure_ascii=False).encode()
    ).hexdigest()[:16]
    en = language == "en-US"
    lines = []
    for row in fills[:8]:
        bits = [row["field_key"], row["value"]]
        if row.get("unit"):
            bits.append(str(row["unit"]))
        if row.get("period"):
            bits.append(str(row["period"]))
        lines.append(" · ".join(bits))
    return ResearchEvidence(
        id="ev_model_" + identity,
        source="模型知识补齐" if not en else "Model knowledge fill",
        url=None,
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        data_as_of=None,
        freshness="unverified_model",
        title="模型知识补齐，未经数据源核验" if not en else "Model-sourced fill; not provider-verified",
        excerpts=[{"location": "llm_data_gap_fill", "text": "\n".join(lines)[:1200]}],
        provider_endpoint=None,
        record_key="llm_data_gap_fill",
    )


def _merge_fills(
    response: AgentResponse,
    fills: list[dict[str, Any]],
    remaining: list[str],
    *,
    language: str,
    model_provider: str,
    requested_gaps: list[dict[str, str]],
) -> AgentResponse:
    en = language == "en-US"
    payload = dict(response.payload or {})
    meta = dict(payload.get("_meta") or {})
    cards = list(payload.get("data_cards") or [])

    kept = [row for row in fills if not _provider_field_present(response, row["field_key"])]
    if not kept and not remaining:
        return response

    if kept:
        label = "Model knowledge fill (unverified)" if en else "模型知识补齐（未核验）"
        lines = []
        for row in kept:
            value = html.escape(str(row["value"]).replace("\n", " "))
            value = value.replace("[", "\\[").replace("]", "\\]")
            lines.append(f"- {value}")
        response.summary = (response.summary or "").rstrip() + f"\n\n**{label}**\n\n" + "\n".join(lines)
        card_rows = [
            {
                "field": row["field_key"],
                "value": row["value"],
                "unit": row.get("unit"),
                "period": row.get("period"),
                "confidence": row.get("confidence"),
                "note": row.get("note") or (
                    "Model knowledge fill; not provider-verified"
                    if en
                    else "模型知识补齐，未经数据源核验"
                ),
            }
            for row in kept
        ]
        cards.append({
            "kind": "model_fill",
            "title": "模型补齐（未核验）" if not en else "Model fill (unverified)",
            "provider": "模型知识补齐" if not en else "model_knowledge",
            "as_of": None,
            "endpoint": None,
            "rows": card_rows,
            "unverified": True,
            "model_sourced": True,
        })
        payload["data_cards"] = cards[:8]
        payload["llm_gap_fills"] = kept
        evidence = list(response.evidence or [])
        evidence.append(_model_evidence(kept, language))
        response.evidence = evidence[:24]

    fill_note = (
        f"Model filled {len(kept)} missing field(s); unverified — not from live providers."
        if en
        else f"已用模型补齐 {len(kept)} 项缺失字段；模型知识补齐，未经数据源核验。"
    )
    gaps = list(response.data_gaps or [])
    if kept and fill_note not in gaps:
        gaps.append(fill_note)
    for note in remaining[:8]:
        text = str(note).strip()
        if text and text not in gaps:
            gaps.append(text)
    # Keep original material gaps that were not filled.
    filled_keys = {row["field_key"] for row in kept}
    for item in requested_gaps:
        if item["field_key"] in filled_keys:
            continue
        desc = item.get("description") or item["field_key"]
        remnant = (
            f"Still unconfirmed after model fill: {desc}"
            if en
            else f"模型补齐后仍无法确认：{desc}"
        )
        if remnant not in gaps:
            gaps.append(remnant)
    response.data_gaps = gaps[:24]

    meta["llm_data_gap_fill"] = True
    meta["llm_data_gap_fill_count"] = len(kept)
    meta["llm_data_gap_fill_provider"] = model_provider
    meta["unverified_model_fields"] = [row["field_key"] for row in kept]
    # Mixed source + model fill: keep source_data when provider evidence exists;
    # otherwise mark model_knowledge so the UI trust strip stays honest.
    has_provider_evidence = any(
        (ev.source or "").strip() not in {"模型知识补齐", "Model knowledge fill"}
        and not str(ev.id or "").startswith("ev_model_")
        for ev in (response.evidence or [])
    ) or any(
        isinstance(card, dict)
        and str(card.get("provider") or "").lower() not in {"model_knowledge", "llm_gap_fill", "模型知识补齐"}
        for card in (payload.get("data_cards") or [])
        if card.get("kind") != "model_fill"
    )
    if has_provider_evidence:
        meta.setdefault("answer_basis", "source_data")
        meta["answer_basis_detail"] = "source_data_with_model_fill"
    else:
        meta["answer_basis"] = "model_knowledge"
    payload["_meta"] = meta
    response.payload = payload
    try:
        from api.financial_bot.gap_synthesis import append_model_fill_step
        response = append_model_fill_step(
            response,
            language=language,
            model_provider=model_provider,
            note="模型知识补齐（未核验）" if not en else "Model knowledge fill (unverified)",
        )
        # Keep structured fills in meta, but primary UX is the tool row.
        meta2 = dict((response.payload or {}).get("_meta") or {})
        meta2["llm_data_gap_fill"] = True
        meta2["llm_data_gap_fill_count"] = len(kept)
        meta2["llm_data_gap_fill_provider"] = (
            "chatgpt" if (model_provider or "").lower() == "chatgpt" else "deepseek"
        )
        response.payload["_meta"] = meta2
    except Exception:
        pass
    return response


async def apply_data_gap_fill(
    response: AgentResponse,
    *,
    query: str,
    language: str,
    model_provider: str = "deepseek",
    tool_results: Optional[list[dict[str, Any]]] = None,
    complete_fn: Optional[CompleteFn] = None,
) -> AgentResponse:
    """Call the selected LLM once to fill material gaps; label fills unverified."""
    if not data_gap_fill_enabled():
        return response
    if response.intent in {"clarification", "greeting"}:
        return response
    meta = (response.payload or {}).get("_meta") if isinstance(response.payload, dict) else None
    if isinstance(meta, dict) and (meta.get("llm_data_gap_fill") or meta.get("limit_reached")):
        return response
    if isinstance(meta, dict) and meta.get("engine") == "llm_gap_fill":
        # Explanation-only path already ran; skip a second paid fill.
        return response

    material = collect_material_gaps(response, tool_results)
    if not material:
        return response

    en = language == "en-US"
    if en:
        system = (
            "You fill missing financial research fields after live providers failed. "
            "Return ONLY JSON: "
            '{"fills":[{"field_key":"...","value":"...","unit":null,"period":null,'
            '"confidence":"low|medium","note":"..."}],"still_unconfirmed":["..."]}. '
            "Rules: never invent URLs or citation IDs; never claim the value came from "
            "iFinD, Fuyao, yfinance, SEC, Finnhub or other providers; prefer short "
            "qualitative background; never guess current quotes, financial figures, "
            "announcements or dates; if unsure, "
            "put the field in still_unconfirmed instead of guessing. Do not overwrite "
            "fields that providers already returned."
        )
        user = (
            f"Question:\n{query}\n\nMissing fields:\n"
            + json.dumps(material, ensure_ascii=False)
            + "\n\nExisting summary (context only):\n"
            + (response.summary or "")[:1200]
        )
    else:
        system = (
            "你在数据源失败后补齐缺失的金融研究字段。"
            "只返回 JSON："
            '{"fills":[{"field_key":"...","value":"...","unit":null,"period":null,'
            '"confidence":"low|medium","note":"..."}],"still_unconfirmed":["..."]}。'
            "规则：严禁编造 URL 或引用 ID；不得声称来自 iFinD/Fuyao/yfinance/SEC 等数据源；"
            "不得猜测最新行情、财务数字、公告内容或日期；仅提供定性背景。"
            "不确定就放入 still_unconfirmed；置信度偏低时用 low；不要覆盖数据源已返回的字段。"
        )
        user = (
            f"问题：\n{query}\n\n缺失字段：\n"
            + json.dumps(material, ensure_ascii=False)
            + "\n\n已有摘要（仅供上下文）：\n"
            + (response.summary or "")[:1200]
        )

    try:
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
        logger.warning("LLM data gap fill call failed: %s", type(exc).__name__)
        return _mark_fill_failed(response, language)

    parsed = _extract_json_object(text)
    if not parsed:
        logger.warning("LLM data gap fill returned non-JSON")
        return _mark_fill_failed(response, language)

    fills = []
    for row in parsed.get("fills") or []:
        if isinstance(row, dict):
            clean = _sanitize_fill(row)
            if clean:
                fills.append(clean)
    remaining = [
        str(item).strip()
        for item in (parsed.get("still_unconfirmed") or [])
        if str(item).strip()
    ]
    if not fills and not remaining:
        return response
    return _merge_fills(
        response,
        fills[:8],
        remaining,
        language=language,
        model_provider=model_provider,
        requested_gaps=material,
    )
