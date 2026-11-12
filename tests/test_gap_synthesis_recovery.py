import asyncio

import pytest

from api.financial_bot import gap_synthesis as gap
from api.schemas import AgentResponse, ResearchEvidence


def _incomplete_response(**kwargs):
    base = dict(
        intent="error",
        summary="## 研究综合未完成\n\n研究汇总未能完成，已获取的来源保留在下方。\n\n已检索的记录保留在下方“来源与数据覆盖”中；本次尚未将它们综合为可靠结论。",
        actions=[],
        citations=[],
        evidence=[
            ResearchEvidence(
                id="ev_yf",
                source="yfinance",
                url=None,
                retrieved_at="2026-11-04T00:00:00+00:00",
                freshness="historical_or_delayed",
                title="00700.HK vs 3033.HK",
            )
        ],
        data_gaps=["研究汇总未能完成，已获取的来源保留在下方。"],
        payload={"_meta": {"engine": "pydantic_ai", "synthesis_incomplete": True}},
        research_steps=[],
    )
    base.update(kwargs)
    return AgentResponse(**base)


def test_needs_recovery_detects_incomplete_summary():
    assert gap.needs_research_recovery(_incomplete_response()) is True
    ok = AgentResponse(
        intent="pydantic_ai_agent",
        summary="腾讯今日相对昨日波动不大。",
        actions=[],
        citations=[],
        evidence=[],
        data_gaps=[],
        payload={"_meta": {"synthesis_incomplete": False}},
    )
    assert gap.needs_research_recovery(ok) is False


def test_recover_replaces_stall_with_labeled_model_answer(monkeypatch):
    monkeypatch.setenv("LLM_DATA_GAP_FILL", "1")
    seen = {"n": 0, "messages": None}

    async def fake_complete(messages):
        seen["n"] += 1
        seen["messages"] = messages
        return (
            "## 腾讯的主营业务\n"
            "腾讯提供社交、内容、游戏和数字服务。模型知识补齐 / 未核验。"
        )

    out = asyncio.run(
        gap.recover_incomplete_synthesis(
            _incomplete_response(data_gaps=["缺少腾讯的主营业务背景"]),
            query="腾讯的主营业务和稳定行业背景是什么",
            language="zh-CN",
            model_provider="chatgpt",
            tool_results=[
                {"tool": "search_stocks", "status": "completed", "data": [{"code": "00700.HK", "name": "腾讯"}]},
                {"tool": "get_stock_analysis", "status": "completed", "data": {"quote": {"current_price": 600}, "metadata": {"currency": "HKD"}}},
                {"tool": "get_stock_news", "status": "unavailable", "data_gaps": ["暂无结果"], "data": []},
                {"tool": "web_search", "status": "unavailable", "data_gaps": ["不可用"], "data": None},
            ],
            complete_fn=fake_complete,
        )
    )
    assert seen["n"] == 1
    assert "研究综合未完成" not in out.summary
    assert "模型知识补齐" in out.summary
    assert out.payload["_meta"]["llm_research_recovery"] is True
    assert out.payload["_meta"]["llm_research_recovery_provider"] == "chatgpt"
    assert out.payload["_meta"]["synthesis_incomplete"] is False
    assert out.payload["_meta"]["unverified"] is True
    assert out.payload["_meta"]["answer_basis_detail"] == "source_data_with_model_fill"
    assert any(ev.id == "ev_yf" for ev in out.evidence)
    assert out.citations == []
    assert any("未经数据源核验" in g for g in out.data_gaps)
    # Prompt must include failed tools.
    blob = "\n".join(m["content"] for m in seen["messages"])
    assert "get_stock_news" in blob
    assert "web_search" in blob


def test_recover_synthesizes_investment_answer_from_retrieved_evidence(monkeypatch):
    monkeypatch.setenv("LLM_DATA_GAP_FILL", "1")
    seen = {}

    async def fake_complete(messages):
        seen["messages"] = messages
        return "结论：近20日上涨，但估值偏高；适合长期分批观察，不宜追高。"

    source_results = [{
        "tool": "get_stock_analysis",
        "status": "completed",
        "data": {"quote": {"close": 1405.39}, "windows": [{"days": 20, "change_pct": 7.04}]},
        "evidence": [{
            "id": "ev_price",
            "source": "iFinD",
            "title": "贵州茅台行情",
            "data_as_of": "2026-11-12",
            "excerpts": [{"location": "price_window", "text": "收盘价1405.39元；近20日涨幅7.04%。"}],
        }],
    }]
    response = _incomplete_response(
        evidence=[ResearchEvidence(
            id="ev_price", source="iFinD", retrieved_at="2026-11-12T00:00:00+00:00",
            freshness="current", title="贵州茅台行情", data_as_of="2026-11-12",
            excerpts=[{"location": "price_window", "text": "收盘价1405.39元；近20日涨幅7.04%。"}],
        )],
    )

    out = asyncio.run(gap.recover_incomplete_synthesis(
        response,
        query="帮我看下茅台最近的走势以及适不适合投资",
        language="zh-CN",
        tool_results=source_results,
        plan_context="结合近期走势和估值判断是否适合投资",
        complete_fn=fake_complete,
    ))

    assert out is not response
    assert "结论：近20日上涨" in out.summary
    assert "研究综合未完成" not in out.summary
    assert "模型知识补齐" not in out.summary
    assert out.payload["_meta"]["synthesis_incomplete"] is False
    assert out.payload["_meta"]["answer_basis"] == "source_data"
    assert out.payload["_meta"]["evidence_synthesis_recovery"] is True
    assert any(ev.id == "ev_price" for ev in out.evidence)
    prompt = "\n".join(message["content"] for message in seen["messages"])
    assert "1405.39" in prompt
    assert "7.04%" in prompt
    assert "结合近期走势和估值判断是否适合投资" in prompt
    assert "不可信" in prompt


def test_source_evidence_recovery_precedes_another_tool_agent_turn(monkeypatch):
    from types import SimpleNamespace
    from api import main

    recovered = AgentResponse(
        intent="pydantic_ai_agent",
        summary="结论：现有数据支持谨慎分批观察。",
        actions=[], citations=[], evidence=[], data_gaps=[],
        payload={"_meta": {
            "llm_research_recovery": True,
            "evidence_synthesis_recovery": True,
            "synthesis_incomplete": False,
        }},
    )
    seen = {}

    async def fake_synthesis(response, **kwargs):
        seen.update(kwargs)
        return recovered

    async def no_agent_recovery(*args, **kwargs):
        raise AssertionError("must synthesize existing evidence before retrying tools")

    monkeypatch.setattr(gap, "recover_incomplete_synthesis", fake_synthesis)
    monkeypatch.setattr("api.agent_pydantic.run_recovery_async", no_agent_recovery, raising=False)
    result = asyncio.run(main._maybe_recover_incomplete_synthesis(
        _incomplete_response(),
        query="茅台走势适合投资吗",
        language="zh-CN",
        model_provider="deepseek",
        tool_results=[{"tool": "get_stock_analysis", "status": "completed", "data": {"close": 1405}}],
        agent=object(),
        deps=SimpleNamespace(tool_results=[], research_plan=None),
    ))
    assert result is recovered
    assert seen["tool_results"][0]["tool"] == "get_stock_analysis"


def test_recovery_snapshot_keeps_valid_json_and_provider_excerpt():
    snapshot = gap._tool_snapshot([{
        "tool": "ifind_smart_query",
        "status": "completed",
        "data": {"unrecognized_provider_fields": {"close": 1405.39}},
        "evidence": [{
            "id": "ev_quote", "source": "iFinD", "title": "行情",
            "excerpts": [{"text": "近20日涨幅7.04%"}],
        }],
    }])
    import json

    parsed = json.loads(snapshot)
    assert parsed[0]["evidence"][0]["excerpts"][0]["text"] == "近20日涨幅7.04%"
    assert parsed[0]["data"]["keys"] == ["unrecognized_provider_fields"]


def test_recover_disabled_leaves_stall(monkeypatch):
    monkeypatch.setenv("LLM_DATA_GAP_FILL", "0")
    monkeypatch.setenv("LLM_GAP_FILL", "0")

    async def boom(messages):
        raise AssertionError("should not call")

    original = _incomplete_response()
    out = asyncio.run(
        gap.recover_incomplete_synthesis(
            original,
            query="腾讯",
            language="zh-CN",
            model_provider="deepseek",
            complete_fn=boom,
        )
    )
    assert out.summary == original.summary
    assert out.payload["_meta"].get("llm_research_recovery") is not True


def test_recover_does_not_invent_citations(monkeypatch):
    monkeypatch.setenv("LLM_GAP_FILL", "1")

    async def fake_complete(messages):
        return "可用回答。详见 https://evil.example/fake"

    out = asyncio.run(
        gap.recover_incomplete_synthesis(
            _incomplete_response(citations=[], data_gaps=["缺少公司主营业务背景"]),
            query="腾讯的主营业务是什么",
            language="zh-CN",
            model_provider="deepseek",
            complete_fn=fake_complete,
        )
    )
    # We keep the model text but do not promote invented URLs into citations[].
    assert out.citations == []


def test_synthesize_gap_uses_selected_provider_label(monkeypatch):
    monkeypatch.setenv("LLM_GAP_FILL", "1")

    async def fake_complete(messages):
        return "茅台主要经营白酒产品。"

    out = asyncio.run(
        gap.synthesize_gap(
            "茅台的主营业务是什么",
            "zh-CN",
            evidence=[],
            gaps=["缺少公司主营业务背景"],
            model_provider="chatgpt",
            complete_fn=fake_complete,
        )
    )
    assert out is not None
    assert out.payload["_meta"]["model_provider"] == "chatgpt"
    assert out.payload["_meta"]["llm_research_recovery"] is True
    assert "模型知识补齐" in out.summary


def test_time_sensitive_gap_fallbacks_skip_model_knowledge(monkeypatch):
    monkeypatch.setenv("LLM_GAP_FILL", "1")
    calls = []

    async def completion(_messages):
        calls.append(True)
        return "模型记忆中的公告摘要"

    original = _incomplete_response(data_gaps=["缺少当天公告内容"])
    recovered = asyncio.run(gap.recover_incomplete_synthesis(
        original,
        query="腾讯今天发布了什么公告",
        language="zh-CN",
        complete_fn=completion,
    ))
    synthesized = asyncio.run(gap.synthesize_gap(
        "腾讯今天发布了什么公告",
        "zh-CN",
        gaps=["缺少当天公告内容"],
        complete_fn=completion,
    ))

    assert calls == []
    assert recovered is original
    assert synthesized is None
