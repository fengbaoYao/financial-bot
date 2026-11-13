"""A structured-output failure must not re-run the whole research agent."""

import asyncio
from types import SimpleNamespace

from api.financial_bot.gap_synthesis import needs_research_recovery

from api.agent_pydantic import _text_output_fallback


def _deps(text_query, tool_results):
    deps = SimpleNamespace(
        user_query=text_query,
        language="zh-CN",
        tool_results=tool_results,
        research_mode="quick",
        verified_us_codes=set(),
        report=lambda *a, **k: None,
    )
    return deps


EVIDENCE = [{
    "tool": "get_financial_statements",
    "status": "completed",
    "evidence": [{"id": "ev_1", "source": "iFinD", "retrieved_at": "2026-11-13T08:00:00+00:00"}],
    "data": {"rows": []},
}]


def test_substantive_text_fallback_is_not_marked_incomplete():
    response = _text_output_fallback(
        _deps("分析一下贵州茅台的财报", EVIDENCE),
        "## 结论\n\n贵州茅台 2025 年营收 1688.38 亿元，归母净利 823.20 亿元，同比分别 -1.2% 和 -4.5%。",
    )
    assert response is not None
    meta = response.payload["_meta"]
    assert meta["structured_output_validated"] is False
    assert meta["synthesis_incomplete"] is False
    # The whole point: no second agent run for prose we already have.
    assert needs_research_recovery(response) is False


def test_plan_only_text_still_asks_for_recovery():
    response = _text_output_fallback(
        _deps("分析一下贵州茅台的财报", EVIDENCE),
        "我先去查一下这家公司的财报数据，请稍等。",
    )
    assert response is not None
    assert response.payload["_meta"]["synthesis_incomplete"] is True
    assert needs_research_recovery(response) is True


def test_empty_text_returns_no_fallback():
    assert _text_output_fallback(_deps("分析一下贵州茅台的财报", EVIDENCE), "") is None
    assert _text_output_fallback(_deps("分析一下贵州茅台的财报", EVIDENCE), "   ") is None
