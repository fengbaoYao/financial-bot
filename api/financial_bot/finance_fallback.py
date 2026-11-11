"""Deterministic financial/valuation snapshot when the model is unavailable.

This closes the gap where a no-model deployment could answer technical price
questions but could not answer "revenue?" or "valuation?" at all.  It only
formats source-backed values; it never computes a fair value or invents a
number.  Missing data is returned as an explicit gap.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

from api.financial_bot.fallback_research import PRICE_INTENT, _COMPANY_ALIASES
from ashare.search import stock_searcher
from api.schemas import AgentResponse, ResearchEvidence, ResearchStep

FINANCIAL_INTENT = re.compile(
    r"财报|营业收入|营收|净利润|归母|毛利|现金流|资产负债|净资产|eps|revenue|earnings|financial|income|balance\s*sheet|cash\s*flow",
    re.IGNORECASE,
)
VALUATION_INTENT = re.compile(
    r"估值|市盈率|市净率|市销率|市现率|\bpe\b|\bpb\b|\bps\b|\bpcf\b|valuation|multiple",
    re.IGNORECASE,
)
VALUATION_ONLY = re.compile(r"估值|市盈率|市净率|市销率|市现率|\bpe\b|\bpb\b|\bps\b|\bpcf\b|valuation", re.IGNORECASE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    abs_value = abs(number)
    if abs_value >= 1e8:
        return f"{number / 1e8:,.2f} 亿"
    if abs_value >= 1e4:
        return f"{number / 1e4:,.2f} 万"
    return f"{number:,.2f}"


def _periods(query: str) -> int:
    match = re.search(r"(?:最近|近)?\s*(\d+)\s*(?:年|期|个?季度|年報|年报)", query or "")
    if not match:
        return 3
    try:
        return max(1, min(8, int(match.group(1))))
    except ValueError:
        return 3


def _statement(query: str) -> str:
    text = query or ""
    if re.search(r"资产负债|balance\s*sheet|总资产|负债", text, re.I):
        return "balance"
    if re.search(r"现金流|cash\s*flow|经营现金", text, re.I):
        return "cashflow"
    return "income"


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return str(value)


def _find_companies(text: str) -> list[tuple[str, str]]:
    """Resolve all clearly named companies, preserving query order."""
    found: dict[str, str] = {}
    for alias, code in sorted(_COMPANY_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if alias in text:
            info = stock_searcher.get_stock_info(code) or {}
            found.setdefault(code, info.get("name") or alias)
    for name, info in stock_searcher.base_stock_db.items():
        code = str((info or {}).get("code") or "")
        if len(name) >= 3 and name in text and code:
            found.setdefault(code, name)
    for pattern in (r"(?:SH|SZ|BJ)\d{6}", r"\d{5}\.HK", r"\bUS\.[A-Z.]{1,8}\b"):
        for match in re.finditer(pattern, text, re.I):
            code = match.group(0)
            info = stock_searcher.get_stock_info(code) or {}
            found.setdefault(code, info.get("name") or code)
    return list(found.items())


def deterministic_financial_research(query: str, language: str) -> Optional[AgentResponse]:
    """Return a source-only financial or valuation answer, or None if not applicable."""
    text = query or ""
    if not (FINANCIAL_INTENT.search(text) or VALUATION_INTENT.search(text)):
        return None
    # Let the dedicated price-move fallback handle "why did it drop" questions.
    if PRICE_INTENT.search(text) and not FINANCIAL_INTENT.search(text):
        return None
    companies = _find_companies(text)
    if not companies:
        return None
    code, name = companies[0]
    en = language == "en-US"
    evidence: list[ResearchEvidence] = []
    gaps: list[str] = []

    # Valuation has a structured snapshot when Fuyao is configured.  There is
    # no safe local substitute for PE/PB, so absence is an explicit gap.
    if VALUATION_INTENT.search(text):
        try:
            from api.datasources.fuyao_provider import get_fuyao_provider

            provider = get_fuyao_provider()
            if provider.configured and len(companies) > 1:
                data = provider.valuations([item[0] for item in companies])
                items = data.get("items") or []
                if items:
                    header = "| 代码 | 名称 | PE TTM | PE MRQ | PB MRQ | PS TTM | PCF TTM |"
                    lines = [
                        f"## 估值对比" if not en else "## Valuation comparison",
                        header if not en else "| Code | Name | PE TTM | PE MRQ | PB MRQ | PS TTM | PCF TTM |",
                        "|---|---|---:|---:|---:|---:|---:|" if not en else "|---|---|---:|---:|---:|---:|---:|",
                    ]
                    for row in items:
                        lines.append(
                            f"| {row.get('thscode') or '—'} | {row.get('name') or '—'} | "
                            f"{row.get('pe_ttm') if row.get('pe_ttm') is not None else '—'} | "
                            f"{row.get('pe_mrq') if row.get('pe_mrq') is not None else '—'} | "
                            f"{row.get('pb_mrq') if row.get('pb_mrq') is not None else '—'} | "
                            f"{row.get('ps_ttm') if row.get('ps_ttm') is not None else '—'} | "
                            f"{row.get('pcf_ttm') if row.get('pcf_ttm') is not None else '—'} |"
                        )
                    lines.extend(["", "数据来源：Fuyao / 同花顺；仅为快照，不构成合理价值判断。" if not en else "Source: Fuyao / THS; snapshot only, not a fair-value judgement."])
                    evidence.append(ResearchEvidence(
                        id="valuation:fuyao",
                        source="fuyao",
                        title="valuation comparison",
                        retrieved_at=data.get("retrieved_at") or _now(),
                        data_as_of=data.get("as_of"),
                        freshness="latest_snapshot",
                        excerpts=[{"location": "valuation", "text": "\n".join(lines[2:])[:900]}],
                        provider_endpoint=data.get("endpoint"),
                        currency="CNY",
                        adjustment="not_applicable",
                        raw_sha256=data.get("raw_sha256"),
                    ))
                    return AgentResponse(
                        intent="valuation_snapshot",
                        summary="\n".join(lines),
                        actions=[],
                        ui_components=["table"],
                        research_steps=[ResearchStep(tool="get_valuations", status="completed", retrieved_at=_now(), endpoint=data.get("endpoint"), evidence_count=1)],
                        evidence=evidence,
                        data_gaps=data.get("data_gaps") or [],
                        payload={
                            "stock_code": code,
                            "stock_name": ", ".join(item[1] for item in companies),
                            "market": "CN",
                            "data_cards": [{
                                "kind": "valuations",
                                "title": "A 股估值对比" if not en else "A-share valuation comparison",
                                "provider": "fuyao",
                                "as_of": data.get("as_of"),
                                "endpoint": data.get("endpoint"),
                                "rows": [_jsonable(row) for row in items],
                            }],
                            "_meta": {"engine": "deterministic_finance", "model_unavailable": True, "provider": "fuyao", "data_as_of": data.get("as_of")},
                        },
                    )
            if provider.configured:
                data = provider.valuations([code])
                items = data.get("items") or []
                if items:
                    row = items[0]
                    lines = [
                        f"## {name} 估值快照" if not en else f"## {name} valuation snapshot",
                        ("⚠️ 实时估值不可用，以下为 last-good 缓存，不是实时快照。" if not en else "⚠️ Live valuation unavailable; this is a stale last-good cache, not a live snapshot.") if data.get("stale") else "",
                        (
                            f"- PE TTM: {row.get('pe_ttm') if row.get('pe_ttm') is not None else '—'}"
                            f" · PE MRQ: {row.get('pe_mrq') if row.get('pe_mrq') is not None else '—'}"
                            f" · PB MRQ: {row.get('pb_mrq') if row.get('pb_mrq') is not None else '—'}"
                        ),
                        (
                            f"- PS TTM: {row.get('ps_ttm') if row.get('ps_ttm') is not None else '—'}"
                            f" · PCF TTM: {row.get('pcf_ttm') if row.get('pcf_ttm') is not None else '—'}"
                        ),
                        f"- {'Data as of' if en else '数据截至'}: {data.get('as_of') or '—'}",
                        f"- {'Provider' if en else '来源'}: Fuyao / 同花顺",
                        "",
                        (
                            "These are reported multiples from one snapshot, not a fair-value judgement."
                            if en else "以上是单次快照的估值倍数，不构成合理价值判断。"
                        ),
                    ]
                    gaps.extend(data.get("data_gaps") or [])
                    evidence.append(ResearchEvidence(
                        id="valuation:fuyao",
                        source="fuyao",
                        title=f"{name} valuation snapshot",
                        retrieved_at=data.get("retrieved_at") or _now(),
                        data_as_of=data.get("as_of"),
                        freshness="latest_snapshot",
                        excerpts=[{"location": "valuation", "text": "\n".join(lines[1:5])}],
                        provider_endpoint=data.get("endpoint"),
                        currency="CNY",
                        adjustment="not_applicable",
                        raw_sha256=data.get("raw_sha256"),
                    ))
                    return AgentResponse(
                        intent="valuation_snapshot",
                        summary="\n".join(lines),
                        actions=["查看财报", "对比同业估值"] if not en else ["View financials", "Compare peer valuations"],
                        ui_components=["table"],
                        research_steps=[ResearchStep(tool="get_valuations", status="completed", retrieved_at=_now(), endpoint=data.get("endpoint"), evidence_count=1)],
                        evidence=evidence,
                        data_gaps=gaps,
                        payload={
                            "stock_code": code,
                            "stock_name": name,
                            "market": "CN",
                            "data_cards": [{
                                "kind": "valuations",
                                "title": f"{name} 估值快照" if not en else f"{name} valuation",
                                "provider": "fuyao",
                                "as_of": data.get("as_of"),
                                "endpoint": data.get("endpoint"),
                                "rows": [_jsonable(row)],
                            }],
                            "_meta": {"engine": "deterministic_finance", "model_unavailable": True, "provider": "fuyao", "data_as_of": data.get("as_of")},
                        },
                    )
            gaps.append("估值快照不可用：Fuyao 未配置或无权限。" if not en else "Valuation snapshot unavailable: Fuyao is not configured or not authorized.")
        except Exception as exc:
            gaps.append(f"估值数据不可用（{type(exc).__name__}）。" if not en else f"Valuation data unavailable ({type(exc).__name__}).")
        if VALUATION_ONLY.search(text) and not FINANCIAL_INTENT.search(text):
            return None

    if len(companies) > 1:
        statement = _statement(text)
        periods = min(_periods(text), 1)
        label = {"income": "利润表", "balance": "资产负债表", "cashflow": "现金流量表"}[statement]
        entries = []
        for company_code, company_name in companies:
            try:
                from api.local_research import financial_statements

                company_data = financial_statements(company_code, statement, periods)
                period_rows = company_data.get("periods") or []
                if period_rows:
                    entries.append((company_code, company_name, company_data, period_rows[0]))
                    gaps.extend(company_data.get("data_gaps") or [])
            except Exception as exc:
                gaps.append(f"{company_name} 财报不可用（{type(exc).__name__}）。" if not en else f"{company_name} financials unavailable ({type(exc).__name__}).")
        if not entries:
            return None
        metric_order = {
            "income": ["revenue", "operating_profit", "net_profit", "net_profit_parent", "eps_basic"],
            "balance": ["total_assets", "total_liabilities", "equity", "cash"],
            "cashflow": ["net_operating_cash_flow", "net_investing_cash_flow", "net_financing_cash_flow"],
        }[statement]
        metrics = [key for key in metric_order if any(key in row.get("items", {}) for _, _, _, row in entries)]
        if not metrics:
            metrics = list(entries[0][3].get("items", {}))[:4]
        header = "| 公司 | 报告期 | " + " | ".join(metrics) + " |"
        lines = [f"## {'财报对比' if not en else 'Financial comparison'} · {label}", header if not en else header.replace("公司", "Company").replace("报告期", "Period"), "|---|---|" + "---|" * len(metrics)]
        for company_code, company_name, company_data, row in entries:
            values = [str(row.get("period") or "—")] + [_fmt((row.get("items") or {}).get(key)) for key in metrics]
            lines.append("| " + " | ".join([f"{company_name} ({company_code})", *values]) + " |")
            evidence.append(ResearchEvidence(
                id=f"financials:{company_code}:{company_data.get('provider')}",
                source=str(company_data.get("provider") or "financials"),
                title=f"{company_name} {label}",
                retrieved_at=company_data.get("retrieved_at") or _now(),
                data_as_of=company_data.get("data_as_of"),
                freshness="reported_period",
                excerpts=[{"location": "periods", "text": f"{row.get('period')}: " + "; ".join(f"{k}={_fmt(v)}" for k, v in (row.get("items") or {}).items())}],
                provider_endpoint=company_data.get("endpoint"),
                currency=company_data.get("currency") or "CNY",
                adjustment=company_data.get("adjustment") or "not_applicable",
                raw_sha256=company_data.get("raw_sha256"),
            ))
        lines.extend(["", "以上为累计报告期原值；未做单季拆分，也不构成估值判断。" if not en else "Cumulative reported values; no single-quarter split or fair-value conclusion."])
        return AgentResponse(
            intent="financial_snapshot",
            summary="\n".join(lines),
            actions=[],
            ui_components=["table"],
            research_steps=[ResearchStep(tool="get_financial_statements", status="completed", retrieved_at=_now(), evidence_count=len(entries))],
            evidence=evidence,
            data_gaps=gaps,
            payload={
                "stock_code": entries[0][0],
                "stock_name": ", ".join(item[1] for item in entries),
                "market": "CN",
                "data_cards": [{
                    "kind": "financials",
                    "title": f"{item[1]} {label}",
                    "provider": item[2].get("provider"),
                    "stale": bool(item[2].get("stale")),
                    "currency": item[2].get("currency") or "CNY",
                    "as_of": item[2].get("data_as_of"),
                    "endpoint": item[2].get("endpoint"),
                    "rows": [{"period": item[3].get("period"), "values": _jsonable(item[3].get("items") or {})}],
                } for item in entries],
                "_meta": {"engine": "deterministic_finance", "model_unavailable": True, "provider": ", ".join(str(item[2].get("provider")) for item in entries)},
            },
        )
    statement = _statement(text)
    periods = _periods(text)
    data: dict[str, Any] | None = None
    try:
        from api.local_research import financial_statements

        data = financial_statements(code, statement, periods)
    except Exception as exc:
        gaps.append(f"财报数据不可用（{type(exc).__name__}）。" if not en else f"Financial statements unavailable ({type(exc).__name__}).")
        return AgentResponse(
            intent="error",
            summary=(
                f"未能获取 {name}（{code}）的结构化财报；{gaps[-1]} 请不要据此推断财务数字。"
                if not en else f"Structured financial statements for {name} ({code}) were unavailable. Do not infer financial numbers from this result."
            ),
            actions=["稍后重试", "先看公司公告"] if not en else ["Retry later", "Check company filings"],
            research_steps=[ResearchStep(tool="get_financial_statements", status="unavailable", retrieved_at=_now(), reason_code="provider_error")],
            data_gaps=gaps,
            payload={"_meta": {"engine": "deterministic_finance", "model_unavailable": True, "provider": data.get("provider") if isinstance(data, dict) else None}},
        )

    rows = data.get("periods") or []
    if not rows:
        return None
    label = {"income": "利润表", "balance": "资产负债表", "cashflow": "现金流量表"}[statement]
    en_label = {"income": "income statement", "balance": "balance sheet", "cashflow": "cash flow"}[statement]
    lines = [f"## {name} · {label}" if not en else f"## {name} · {en_label}"]
    if data.get("stale"):
        lines.append("⚠️ 实时财报源不可用，以下为 last-good 缓存。" if not en else "⚠️ Live financial source unavailable; this is a stale last-good cache.")
    lines.append(
        f"- {'Provider' if en else '来源'}: {data.get('provider')} · {'as of' if en else '截至'} {data.get('data_as_of') or '—'}"
    )
    for row in rows:
        items = row.get("items") or {}
        cells = []
        for key, value in items.items():
            cells.append(f"{key}: {_fmt(value)}")
        lines.append(f"- {row.get('period')}: " + "; ".join(cells))
    lines.append("")
    lines.append(
        "Cumulative reported values; no single-quarter split and no fair-value conclusion."
        if en else "以上为累计报告期原值；未做单季拆分，也不构成估值判断。"
    )
    gaps.extend(data.get("data_gaps") or [])
    evidence.append(ResearchEvidence(
        id=f"financials:{data.get('provider')}",
        source=str(data.get("provider") or "financials"),
        title=f"{name} {label}",
        retrieved_at=data.get("retrieved_at") or _now(),
        data_as_of=data.get("data_as_of"),
        freshness="reported_period",
        excerpts=[{"location": "periods", "text": "\n".join(lines[2:8])}],
        provider_endpoint=data.get("endpoint"),
        currency=data.get("currency") or "CNY",
        adjustment=data.get("adjustment") or "not_applicable",
        raw_sha256=data.get("raw_sha256"),
    ))
    return AgentResponse(
        intent="financial_snapshot",
        summary="\n".join(lines),
        actions=["查看年报表", "对比同业"] if not en else ["View annual report", "Compare peers"],
        ui_components=["table"],
        research_steps=[ResearchStep(tool="get_financial_statements", status="completed", retrieved_at=_now(), endpoint=data.get("endpoint"), evidence_count=1)],
        evidence=evidence,
        data_gaps=gaps,
        payload={
            "stock_code": code,
            "stock_name": name,
            "market": "CN",
            "data_cards": [{
                "kind": "financials",
                "title": f"{name} {label}",
                "provider": data.get("provider"),
                "currency": data.get("currency") or "CNY",
                "as_of": data.get("data_as_of"),
                "endpoint": data.get("endpoint"),
                "rows": [{"period": row.get("period"), "values": _jsonable(row.get("items") or {})} for row in rows],
            }],
            "_meta": {
                "engine": "deterministic_finance",
                "model_unavailable": True,
                "provider": data.get("provider"),
                "data_as_of": data.get("data_as_of"),
            },
        },
    )
