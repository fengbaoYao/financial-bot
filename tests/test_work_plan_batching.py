"""Work plan gating must not serialise independent evidence steps."""

from types import SimpleNamespace

from api.agent_pydantic import work_plan_tools_for_request


def _step(kind, tools):
    return SimpleNamespace(kind=kind, tools=tools, validation=SimpleNamespace(tools=tools), status="pending")


def _deps(steps, attempted=()):
    return SimpleNamespace(
        plan_before_research=True,
        bot_prompt=None,
        research_plan=SimpleNamespace(steps=steps),
        tool_results=[{"tool": name} for name in attempted],
        user_query="分析一下贵州茅台的财报",
    )


AVAILABLE = {"search_stocks", "get_financial_statements", "get_valuations",
             "get_cninfo_announcements", "get_stock_analysis", "get_stock_news"}


def test_resolution_is_a_barrier():
    deps = _deps([
        _step("resolve", ["search_stocks"]),
        _step("filings", ["get_financial_statements"]),
    ])
    assert work_plan_tools_for_request(deps, AVAILABLE) == {"search_stocks"}


def test_independent_steps_are_exposed_together():
    deps = _deps([
        _step("resolve", ["search_stocks"]),
        _step("filings", ["get_financial_statements", "get_cninfo_announcements"]),
        _step("market", ["get_stock_analysis"]),
        _step("news", ["get_stock_news"]),
    ], attempted={"search_stocks"})
    exposed = work_plan_tools_for_request(deps, AVAILABLE)
    # One batch, not one step per model round trip.
    assert exposed == {"get_financial_statements", "get_cninfo_announcements",
                       "get_stock_analysis", "get_stock_news"}


def test_attempted_tools_are_not_offered_again():
    deps = _deps([
        _step("resolve", ["search_stocks"]),
        _step("filings", ["get_financial_statements"]),
        _step("market", ["get_stock_analysis"]),
    ], attempted={"search_stocks", "get_financial_statements"})
    assert work_plan_tools_for_request(deps, AVAILABLE) == {"get_stock_analysis"}


def test_unknown_step_kind_keeps_sequential_gating():
    deps = _deps([
        _step("resolve", ["search_stocks"]),
        _step("mystery", ["get_valuations"]),
        _step("market", ["get_stock_analysis"]),
    ], attempted={"search_stocks"})
    # An unrecognised step must not silently gain parallel access.
    assert work_plan_tools_for_request(deps, AVAILABLE) == {"get_valuations"}


def test_bot_conversations_are_not_gated():
    deps = _deps([_step("resolve", ["search_stocks"])])
    deps.bot_prompt = "system"
    assert work_plan_tools_for_request(deps, AVAILABLE) == AVAILABLE


def test_batching_does_not_smuggle_in_web_search():
    """Exposing whole steps must not make the slow web path available by default."""
    tools = {"search_stocks", "get_financial_statements", "web_search",
             "read_public_source", "list_hotspots"}
    deps = _deps([
        _step("resolve", ["search_stocks"]),
        _step("market", ["get_financial_statements", "web_search", "read_public_source", "list_hotspots"]),
    ], attempted={"search_stocks"})
    deps.user_query = "分析一下贵州茅台的财报"
    exposed = work_plan_tools_for_request(deps, tools)
    assert "web_search" not in exposed
    assert "read_public_source" not in exposed
    assert "get_financial_statements" in exposed


def test_batching_keeps_web_search_when_user_asked_for_it():
    tools = {"search_stocks", "get_financial_statements", "web_search"}
    deps = _deps([
        _step("resolve", ["search_stocks"]),
        _step("market", ["get_financial_statements", "web_search"]),
    ], attempted={"search_stocks"})
    deps.user_query = "联网搜一下这家公司的最新报道"
    assert "web_search" in work_plan_tools_for_request(deps, tools)


def test_news_only_query_does_not_run_the_whole_plan():
    """A "latest news on X" lookup must not spend filings, valuation and quotes.

    The planner emits a full research plan regardless of intent, and batching
    turned that into one big parallel spend on a question about headlines.
    """
    tools = {"search_stocks", "get_stock_news", "get_valuations",
             "get_financial_statements", "get_stock_analysis"}
    deps = _deps([
        _step("resolve", ["search_stocks"]),
        _step("filings", ["get_financial_statements"]),
        _step("valuation", ["get_valuations"]),
        _step("market", ["get_stock_analysis"]),
        _step("news", ["get_stock_news"]),
    ], attempted={"search_stocks"})
    deps.user_query = "腾讯最近有什么消息"
    exposed = work_plan_tools_for_request(deps, tools)
    assert exposed == {"get_stock_news"}


def test_non_news_query_still_gets_the_full_batch():
    tools = {"search_stocks", "get_stock_news", "get_valuations", "get_financial_statements"}
    deps = _deps([
        _step("resolve", ["search_stocks"]),
        _step("filings", ["get_financial_statements"]),
        _step("valuation", ["get_valuations"]),
        _step("news", ["get_stock_news"]),
    ], attempted={"search_stocks"})
    deps.user_query = "分析一下贵州茅台的财报"
    exposed = work_plan_tools_for_request(deps, tools)
    assert exposed == {"get_financial_statements", "get_valuations", "get_stock_news"}
