"""Deterministic, source-only research when no model credential is configured.

This is not a model synthesis and never claims a cause. It resolves the company,
measures the actual price windows from retrieved bars, corrects a false premise,
and lists retrieved company news with an explicit relevance label. If the model
is unavailable, the caller receives this honest partial snapshot instead of a
fabricated "completed" narrative.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Optional

from api.price_research import measured_price_moves
from api.schemas import AgentResponse, ResearchEvidence, ResearchStep
from ashare.search import stock_searcher

PRICE_INTENT = re.compile(
    r"暴跌|大跌|下跌|跌幅|跌了|crash|plunge|sell-?off|why\s+(?:did|is|has).*(?:drop|fall|decline)\b",
    re.IGNORECASE,
)

_COMPANY_ALIASES: dict[str, str] = {
    "腾讯": "00700.HK",
    "腾讯控股": "00700.HK",
    "茅台": "sh600519",
    "贵州茅台": "sh600519",
    "阿里巴巴": "US.BABA",
    "阿里": "US.BABA",
    "苹果": "US.AAPL",
    "英伟达": "US.NVDA",
    "微软": "US.MSFT",
    "特斯拉": "US.TSLA",
    "比亚迪": "sz002594",
    "宁德时代": "sz300750",
    "美团": "03690.HK",
    "小米": "01810.HK",
    "招商银行": "sh600036",
}


def find_company(query: str) -> tuple[str, str] | None:
    text = query or ""
    for alias, code in sorted(_COMPANY_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if alias in text:
            normalized = code
            info = stock_searcher.get_stock_info(normalized)
            return normalized, (info or {}).get("name") or alias
    best: tuple[int, str, str] | None = None
    for name, info in stock_searcher.base_stock_db.items():
        if len(name) >= 3 and name in text:
            code = info.get("code")
            if code and (best is None or len(name) > best[0]):
                best = (len(name), code, name)
    if best:
        return best[1], best[2]
    # Explicit codes are safe to accept without fuzzy name matching.
    for pattern in (r"(?:SH|SZ)\d{6}", r"\d{5}\.HK", r"\bUS\.[A-Z.]{1,8}\b"):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            code = match.group(0)
            info = stock_searcher.get_stock_info(code)
            return code, (info or {}).get("name") or code
    return None


def _evidence_from_news(news: Any, fresh: str) -> ResearchEvidence:
    return ResearchEvidence(
        id=f"news:{news.id}",
        source=news.source or "news",
        url=news.url,
        title=news.title,
        retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        data_as_of=(news.published_at or "")[:10] or None,
        freshness=fresh,
        excerpts=[{"location": "title/summary", "text": f"{news.title} — {news.summary}"[:500]}],
    )


async def deterministic_price_research(
    query: str, language: str
) -> Optional[AgentResponse]:
    """Return a source-only price-move snapshot, or None for other intents."""
    if not PRICE_INTENT.search(query or ""):
        return None
    company = find_company(query)
    en = language == "en-US"
    if company is None:
        return AgentResponse(
            intent="clarification",
            summary=(
                "I could not identify a specific listed company in the question. Which ticker or company do you mean?"
                if en
                else "没有识别出具体上市公司。请补充公司名称或股票代码（例如 腾讯 / 00700.HK）。"
            ),
            payload={"_meta": {"engine": "deterministic_source_only", "model_unavailable": True}},
        )
    code, name = company

    import api.main as main

    def _analysis():
        return main.stock_service.get_stock_analysis(code, include_ai=False)

    try:
        analysis = await asyncio.wait_for(asyncio.to_thread(_analysis), timeout=45)
    except Exception as exc:
        return AgentResponse(
            intent="error",
            summary=(
                f"Market data for {name} ({code}) is unavailable, so the price premise could not be measured ({type(exc).__name__})."
                if en
                else f"未能获取 {name}（{code}）的行情，无法先核实价格前提（{type(exc).__name__}）。"
            ),
            payload={"data_gaps": ["Price data unavailable"]},
        )

    metadata = analysis.metadata or {}
    bars = [
        {"date": str(point.get("date"))[:10], "close": point.get("close"), "volume": point.get("volume")}
        for point in (analysis.chart_series or [])
    ]
    moves = measured_price_moves(bars)
    windows = moves.get("windows") or []
    window_lines = [
        (f"{row['sessions']}-session: {row['change_pct']:+.2f}% ({row['start']} → {row['end']})")
        for row in windows
    ]
    max_20 = next((row["change_pct"] for row in windows if row["sessions"] == 20), None)
    max_5 = next((row["change_pct"] for row in windows if row["sessions"] == 5), None)
    max_1 = next((row["change_pct"] for row in windows if row["sessions"] == 1), None)
    short_sharp = (max_1 is not None and max_1 <= -4) or (max_5 is not None and max_5 <= -8)
    medium_decline = max_20 is not None and max_20 <= -10

    news_items = []
    try:
        news_items = await asyncio.wait_for(
            asyncio.to_thread(main.news_service.get_stock_news, code, name, 8), timeout=25
        )
    except Exception:
        news_items = []
    direct = [item for item in news_items if getattr(item, "relation_type", "") == "direct"]
    topic = [item for item in news_items if getattr(item, "relation_type", "") != "direct"]

    provider = metadata.get("provider") or metadata.get("source") or "unknown"
    adjustment = metadata.get("adjustment") or "unknown"
    currency = metadata.get("currency") or "unknown"
    as_of = metadata.get("data_as_of") or (bars[-1]["date"] if bars else None)

    if en:
        heading = [
            f"## Premise check: {name} ({code})",
            f"Provider **{provider}**; currency **{currency}**; adjustment **{adjustment}**; data as of **{as_of or 'unknown'}**; price status: historical or delayed.",
            "",
            "Measured from the retrieved daily bars:",
            *([f"- {line}" for line in window_lines] or ["- Price windows could not be computed (insufficient or invalid bars)."]),
            "",
            (
                "The short-term decline is consistent with “drop”, but this snapshot does not establish a cause."
                if short_sharp
                else (
                    f"Neither the 1-session nor 5-session window supports “crash”. The 20-session cumulative move is {max_20:+.2f}%, a material medium-term weakness, so “crash” is imprecise while the weakening is real. No cause is established."
                    if medium_decline
                    else "The measured change does **not** support describing this period as a “crash”. The premise needs correction."
                )
            ),
            "",
            "### Retrieved company news (not a causal finding)",
        ]
        if direct:
            heading.extend([f"- {item.published_at[:10]} · {item.title} ({item.source})" for item in direct[:6]])
        else:
            heading.append("- No company-matched news was retrieved. This does not mean nothing happened.")
        if topic:
            heading.append("")
            heading.append("Topic-level fallback items (association unverified, not used as a cause):")
            heading.extend([f"- {item.title} ({item.source})" for item in topic[:3]])
        heading.extend(
            [
                "",
                "### Limits",
                "- No model credential is configured, so causal synthesis did not run. The evidence above is a source-only snapshot.",
                "- A headline is not evidence of causation merely because its date overlaps a price move.",
            ]
        )
    else:
        heading = [
            f"## 先核实前提：{name}（{code}）",
            f"行情来源 **{provider}**；币种 **{currency}**；复权口径 **{adjustment}**；数据截至 **{as_of or '未知'}**；状态：历史或延迟数据。",
            "",
            "按已获取的日线计算：",
            *([f"- {line}" for line in window_lines] or ["- 日线不足或无效，无法计算价格窗口。"]),
            "",
            (
                "短期跌幅明显，与“暴跌”的描述相符；但本快照不能证明下跌原因。"
                if short_sharp
                else (
                    f"单日与 5 日窗口均不支持“暴跌”的描述；20 日累计变化为 {max_20:+.2f}%，属于明显的中期走弱，因此“暴跌”并不准确，但走弱是事实。本快照不能证明原因。"
                    if medium_decline
                    else "按当前数据，该区间并未出现可称为“暴跌”的跌幅，问题前提需要修正。"
                )
            ),
            "",
            "### 已获取的公司相关新闻（不是因果结论）",
        ]
        if direct:
            heading.extend([f"- {item.published_at[:10]} · {item.title}（{item.source}）" for item in direct[:6]])
        else:
            heading.append("- 未获取到与该公司直接匹配的新闻；这不等于没有发生事件。")
        if topic:
            heading.append("")
            heading.append("主题级兜底条目（公司关联未核实，不作为下跌原因）：")
            heading.extend([f"- {item.title}（{item.source}）" for item in topic[:3]])
        heading.extend(
            [
                "",
                "### 限制",
                "- 当前未配置模型凭证，因果综合分析没有运行；以上只是价格事实与来源快照。",
                "- 日期相近不能证明新闻导致价格变化。",
            ]
        )

    evidence = [
        ResearchEvidence(
            id="price:analysis",
            source=provider,
            url=None,
            title=f"{name} daily bars",
            retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            data_as_of=as_of,
            freshness="historical_or_delayed",
            excerpts=[],
        )
    ]
    evidence.extend(_evidence_from_news(item, "company_matched" if item.relation_type == "direct" else "topic_unverified") for item in (direct[:6] + topic[:3]))
    gaps = list(moves.get("data_gaps") or [])
    gaps.append(
        "No model credential is configured; causal synthesis did not run."
        if en
        else "未配置模型凭证，因果综合分析没有运行；以上只是价格与来源快照。"
    )
    if adjustment == "unknown":
        gaps.append(
            "Adjustment convention is unknown; large moves require checking corporate actions."
            if en
            else "复权口径未知；大幅波动需要核对公司行动（拆股、分红等）。"
        )
    return AgentResponse(
        intent="pydantic_ai_agent",
        summary="\n".join(heading),
        actions=(
            ["Show the price table", "Read the latest filing", "Compare with a peer"]
            if en
            else ["查看价格表", "读取最新公告", "与同业比较"]
        ),
        citations=[],
        research_steps=[
            ResearchStep(tool="get_stock_analysis", status="completed", retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), evidence_count=1),
            ResearchStep(tool="get_stock_news", status="completed" if news_items else "unavailable", retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), evidence_count=len(news_items), reason_code=None if news_items else "no_results"),
        ],
        evidence=evidence,
        data_gaps=gaps,
        payload={
            "_meta": {
                "engine": "deterministic_source_only",
                "model_unavailable": True,
                "provider": provider,
                "adjustment": adjustment,
                "data_as_of": as_of,
            },
            "stock_code": code,
            "stock_name": name,
            "market": (stock_searcher.get_stock_info(code) or {}).get("market"),
        },
    )
