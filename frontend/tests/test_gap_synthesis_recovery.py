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
            "## 今日相对昨日\n"
            "行情工具显示港股腾讯有可比窗口（见证据）。\n"
            "新闻与公开网页检索失败；以下为模型知识补齐 / 未核验："
            "需关注游戏版号与广告投放节奏，具体以一手公告为准。"
        )

    out = asyncio.run(
        gap.recover_incomplete_synthesis(
            _incomplete_response(),
            query="今天腾讯和昨天腾讯相比有什么需要关注的吗",
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
    assert any(step.tool == "model_knowledge_fill" for step in out.research_steps)
    fill_step = next(step for step in out.research_steps if step.tool == "model_knowledge_fill")
    assert fill_step.status == "completed"
    assert fill_step.reason_code == "unverified_model"
    assert "模型知识补齐" in (fill_step.endpoint or "")
    # Primary UX is the tool row; model_fill data cards should not be the vehicle.
    assert not any(
        isinstance(card, dict) and card.get("kind") == "model_fill"
        for card in (out.payload.get("data_cards") or [])
    )
    # Prompt must include failed tools.
    blob = "\n".join(m["content"] for m in seen["messages"])
    assert "get_stock_news" in blob
    assert "web_search" in blob


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
            _incomplete_response(citations=[]),
            query="腾讯",
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
        return "缺少一手报价，下面是通用分析框架。"

    out = asyncio.run(
        gap.synthesize_gap(
            "茅台股价",
            "zh-CN",
            evidence=[],
            gaps=["行情不可用"],
            model_provider="chatgpt",
            complete_fn=fake_complete,
        )
    )
    assert out is not None
    assert out.payload["_meta"]["model_provider"] == "chatgpt"
    assert out.payload["_meta"]["llm_research_recovery"] is True
    assert "模型知识补齐" in out.summary


def test_append_model_fill_step_registers_tool_row():
    from api.financial_bot.gap_synthesis import append_model_fill_step, MODEL_FILL_TOOL

    base = AgentResponse(
        intent="pydantic_ai_agent",
        summary="回答",
        research_steps=[],
        payload={"data_cards": [{"kind": "model_fill", "title": "模型补齐（未核验）", "provider": "模型知识补齐", "rows": [{"field": "x", "value": "1"}]}]},
    )
    out = append_model_fill_step(base, language="zh-CN", model_provider="deepseek")
    assert any(step.tool == MODEL_FILL_TOOL for step in out.research_steps)
    assert out.payload["_meta"].get("model_fill_as_tool") is True
    assert not any(card.get("kind") == "model_fill" for card in (out.payload.get("data_cards") or []))


def test_run_recovery_async_allows_tools_and_keeps_prior_results():
    """Recovery must re-run the agent with tools enabled and prior tool_results intact."""
    import asyncio
    from types import SimpleNamespace
    from api.agent_pydantic import AgentDeps, AgentOutput, run_recovery_async

    captured = {}

    class FakeAgent:
        model = SimpleNamespace(model_name="deepseek-chat")

        async def run(self, prompt, deps=None, usage=None, usage_limits=None, model_settings=None, **kwargs):
            captured["prompt"] = prompt
            captured["usage_limits"] = usage_limits
            captured["tool_results_len"] = len(deps.tool_results)
            deps.tool_results.append({
                "tool": "web_search",
                "status": "completed",
                "data": {"items": [{"title": "extra"}]},
            })
            return SimpleNamespace(
                output=AgentOutput(summary="补齐后的可用回答"),
                usage=SimpleNamespace(requests=2, input_tokens=10, output_tokens=20),
            )

    deps = AgentDeps(
        stock_service=None,
        news_service=None,
        hotspot_service=None,
        portfolio_service=None,
        web_search_service=None,
        language="zh-CN",
        research_mode="quick",
    )
    deps.tool_results.append({
        "tool": "get_stock_quote",
        "status": "completed",
        "data": {"quote": {"price": 1}},
    })
    deps.run_usage = SimpleNamespace(requests=1)

    out = asyncio.run(
        run_recovery_async(
            FakeAgent(),
            deps,
            "腾讯今天怎么了",
            progress_callback=None,
            model_provider="deepseek",
        )
    )
    assert "允许继续调用工具" in captured["prompt"]
    assert captured["usage_limits"].tool_calls_limit is None
    assert captured["tool_results_len"] == 1
    assert any(item.get("tool") == "web_search" for item in deps.tool_results)
    assert any(step.tool == "model_knowledge_fill" for step in out.research_steps)
    assert out.payload["_meta"].get("llm_research_recovery") is True
    assert out.payload["_meta"].get("model_fill_as_tool") is True
