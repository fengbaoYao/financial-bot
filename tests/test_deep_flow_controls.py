"""Deep routing, truthful completion and request limits."""
import asyncio
import time
from types import SimpleNamespace

from api.agent_pydantic import AgentDeps
from api.financial_bot.executor import classify_response
from api.research_capability import ResearchCapability


def test_deep_brief_exposes_research_flow_after_evidence_but_quick_does_not():
    async def run():
        tools = [SimpleNamespace(name=name) for name in ("search_stocks", "get_stock_news", "web_search", "research_company")]
        for mode, expected in (("quick", []), ("deep", ["research_company"])):
            deps = AgentDeps(stock_service=None, news_service=None, hotspot_service=None,
                portfolio_service=None, web_search_service=None, research_mode=mode,
                user_query="AAPL first-pass investor brief")
            deps.tool_results = [
                {"tool":"search_stocks", "data":[{"code":"US.AAPL"}], "evidence":[]},
                {"tool":"get_stock_news", "data":[], "evidence":[{"id":"ev_a"}]},
                {"tool":"web_search", "data":[], "evidence":[]},
                {"tool":"get_sec_company_data", "data":{}, "evidence":[]},
            ]
            deps.verified_us_codes.add("US.AAPL")
            result = await ResearchCapability().prepare_tools(SimpleNamespace(deps=deps), tools)
            assert [tool.name for tool in result] == expected
    asyncio.run(run())


def test_incomplete_deep_review_takes_precedence_over_prose_fallback():
    response = SimpleNamespace(intent="answer", summary="Some evidence is available", clarification=None,
        payload={"_meta":{"output_fallback":"text", "structured_output_validated":False,
                          "deep_research_incomplete":True}})
    assert classify_response(response) == ("partial", "deep_research_incomplete")



def test_deep_request_limit_default_is_24(monkeypatch):
    monkeypatch.delenv("FINANCIAL_BOT_DEEP_MODEL_REQUEST_LIMIT", raising=False)
    kwargs = dict(stock_service=None, news_service=None, hotspot_service=None,
                  portfolio_service=None, web_search_service=None)
    assert AgentDeps(**kwargs, research_mode="deep").model_request_limit == 24

def test_deep_request_limit_configured_and_quick_unchanged(monkeypatch):
    monkeypatch.setenv("FINANCIAL_BOT_DEEP_MODEL_REQUEST_LIMIT", "9")
    kwargs = dict(stock_service=None, news_service=None, hotspot_service=None,
                  portfolio_service=None, web_search_service=None)
    assert AgentDeps(**kwargs, research_mode="deep").model_request_limit == 9
    assert AgentDeps(**kwargs, research_mode="quick").model_request_limit is None


def test_role_skipped_for_reserved_synthesis_is_recorded(monkeypatch):
    from api.agent_pydantic import admit_deep_review
    monkeypatch.setenv("FINANCIAL_BOT_DEEP_MODEL_REQUEST_LIMIT", "3")
    deps = AgentDeps(stock_service=None, news_service=None, hotspot_service=None,
        portfolio_service=None, web_search_service=None, research_mode="deep")
    assert not admit_deep_review(deps, SimpleNamespace(usage=SimpleNamespace(requests=2)), "tradingagents_manager")
    assert deps.tool_results[-1]["tool"] == "tradingagents_manager"
    assert deps.tool_results[-1]["status"] == "unavailable"
    assert deps.tool_results[-1]["reason_code"] == "request_limit"


def test_deep_task_disables_automatic_retries_even_if_enabled(monkeypatch):
    from api.financial_bot.executor import TaskExecutor
    monkeypatch.setenv("FINANCIAL_BOT_AUTO_RETRY", "1")
    assert TaskExecutor._task_allows_automatic_retry({"research_mode":"deep"}) is False
    assert TaskExecutor._task_allows_automatic_retry({"research_mode":"quick"}) is True


def test_request_limit_reserves_final_answer_and_marks_skipped_roles(monkeypatch):
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel
    from api.agent_pydantic import create_agent, run_agent_async
    from tests.test_company_research import dependencies, final
    monkeypatch.setenv("FINANCIAL_BOT_DEEP_MODEL_REQUEST_LIMIT", "3")
    async def run():
        deps = dependencies(research_mode="deep")
        requests = []
        def model(messages, info):
            requests.append(info)
            if len(requests) == 1:
                return ModelResponse(parts=[ToolCallPart("research_company", {"company":"MRNA", "focus":"advantage"})])
            return ModelResponse(parts=[ToolCallPart("final_result", final(deps))])
        agent = create_agent(api_key="offline", model="offline-model")
        agent.model = FunctionModel(model)
        response = await run_agent_async(agent, deps, "Research Moderna's industry advantage")
        assert len(requests) <= 3
        assert response.summary
        assert response.payload["_meta"]["deep_research_incomplete"]
        assert classify_response(response) == ("partial", "deep_research_incomplete")
        assert any(step.status == "unavailable" and step.reason_code == "request_limit" for step in response.research_steps)
    asyncio.run(run())


def test_deep_token_limit_defaults_unlimited_and_optional_budget(monkeypatch):
    monkeypatch.delenv("FINANCIAL_BOT_DEEP_TOTAL_TOKEN_LIMIT", raising=False)
    kwargs = dict(stock_service=None, news_service=None, hotspot_service=None,
                  portfolio_service=None, web_search_service=None)
    assert AgentDeps(**kwargs, research_mode="deep").total_token_limit is None
    assert AgentDeps(**kwargs, research_mode="quick").total_token_limit is None
    monkeypatch.setenv("FINANCIAL_BOT_DEEP_TOTAL_TOKEN_LIMIT", "9000")
    assert AgentDeps(**kwargs, research_mode="deep").total_token_limit == 9000
    monkeypatch.setenv("FINANCIAL_BOT_DEEP_TOTAL_TOKEN_LIMIT", "0")
    assert AgentDeps(**kwargs, research_mode="deep").total_token_limit is None


def test_optional_token_budget_still_stops_when_explicitly_configured(monkeypatch):
    import pytest
    from pydantic_ai.exceptions import UsageLimitExceeded
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.usage import RequestUsage, UsageLimits
    from api.agent_pydantic import create_agent, run_agent_async, AgentDeps
    from tests.test_company_research import dependencies
    monkeypatch.delenv("FINANCIAL_BOT_DEEP_TOTAL_TOKEN_LIMIT", raising=False)
    kwargs = dict(stock_service=None, news_service=None, hotspot_service=None,
                  portfolio_service=None, web_search_service=None)
    assert AgentDeps(**kwargs, research_mode="deep").total_token_limit is None
    observed = {}
    async def capture_unlimited():
        deps = dependencies(research_mode="deep")
        async def fake_run(*args, **kwargs):
            observed["limits"] = kwargs["usage_limits"]
            return SimpleNamespace(output=SimpleNamespace(summary="ok", evidence_ids=[], data_gaps=[], actions=[], financial_fact_ids=[], clarification=None, intent="answer"), usage=SimpleNamespace(requests=1, input_tokens=1, output_tokens=1))
        # Direct property/UsageLimits path: unlimited means None is passed through.
        limits = UsageLimits(request_limit=deps.model_request_limit, tool_calls_limit=None, total_tokens_limit=deps.total_token_limit)
        assert limits.total_tokens_limit is None
    asyncio.run(capture_unlimited())
    monkeypatch.setenv("FINANCIAL_BOT_DEEP_TOTAL_TOKEN_LIMIT", "1000")
    async def limited():
        deps = dependencies(research_mode="deep")
        calls = []
        def model(messages, info):
            calls.append(info)
            return ModelResponse(parts=[ToolCallPart("search_stocks", {"query":"MRNA"})],
                                 usage=RequestUsage(input_tokens=1100, output_tokens=1))
        agent = create_agent(api_key="offline", model="offline-model")
        agent.model = FunctionModel(model)
        with pytest.raises(UsageLimitExceeded):
            await run_agent_async(agent, deps, "Research Moderna")
        assert len(calls) == 1
    asyncio.run(limited())


def test_text_fallback_preserves_incomplete_role_status():
    from api.agent_pydantic import _text_output_fallback
    deps = AgentDeps(stock_service=None, news_service=None, hotspot_service=None,
        portfolio_service=None, web_search_service=None, research_mode="deep")
    deps.record_tool({"tool":"tradingagents_risk", "status":"unavailable", "reason_code":"request_limit", "data":{}})
    response = _text_output_fallback(deps, "A partial explanation.")
    assert response.payload["_meta"]["deep_research_incomplete"]
    assert classify_response(response) == ("partial", "deep_research_incomplete")


def test_role_does_not_stop_on_tokens_when_budget_disabled(monkeypatch):
    from api.agent_pydantic import admit_deep_review
    monkeypatch.delenv("FINANCIAL_BOT_DEEP_TOTAL_TOKEN_LIMIT", raising=False)
    deps = AgentDeps(stock_service=None, news_service=None, hotspot_service=None,
        portfolio_service=None, web_search_service=None, research_mode="deep")
    ctx = SimpleNamespace(usage=SimpleNamespace(requests=2, total_tokens=800_000))
    assert admit_deep_review(deps, ctx, "tradingagents_manager")
    monkeypatch.setenv("FINANCIAL_BOT_DEEP_TOTAL_TOKEN_LIMIT", "1000")
    deps = AgentDeps(stock_service=None, news_service=None, hotspot_service=None,
        portfolio_service=None, web_search_service=None, research_mode="deep")
    assert not admit_deep_review(deps, ctx, "tradingagents_manager")
    assert deps.tool_results[-1]["reason_code"] == "token_limit"


def test_finished_provider_call_missing_usage_is_marked_unresolved():
    from api.research_runs import run_metadata
    deps = AgentDeps(stock_service=None, news_service=None, hotspot_service=None,
        portfolio_service=None, web_search_service=None, research_mode="deep")
    deps.provider_requests_started = 2
    deps.provider_requests_finished = 2
    deps.run_usage.requests = 1
    assert run_metadata(deps, "partial")["usage_unresolved"]


def test_review_retry_does_not_spend_the_reserved_final_request(monkeypatch):
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel
    from api.agent_pydantic import create_agent, run_agent_async
    from tests.test_company_research import dependencies, final
    monkeypatch.setenv("FINANCIAL_BOT_DEEP_MODEL_REQUEST_LIMIT", "3")
    async def run():
        deps = dependencies(research_mode="deep")
        calls = []
        def model(messages, info):
            calls.append(info)
            if len(calls) == 1:
                return ModelResponse(parts=[ToolCallPart("research_company", {"company":"MRNA", "focus":"advantage"})])
            if len(calls) == 2:
                return ModelResponse(parts=[ToolCallPart("final_result", {"summary":"Unsupported [ev_bad].", "evidence_ids":["ev_bad"]})])
            return ModelResponse(parts=[ToolCallPart("final_result", final(deps))])
        agent = create_agent(api_key="offline", model="offline-model")
        agent.model = FunctionModel(model)
        response = await run_agent_async(agent, deps, "Research Moderna's industry advantage")
        assert len(calls) == 3 and response.summary
        assert response.payload["_meta"]["deep_research_incomplete"]
    asyncio.run(run())


def test_detailed_single_company_routes_to_deep_flow_but_memory_does_not():
    from api.agent_pydantic import requires_deep_company_flow
    deps = AgentDeps(stock_service=None, news_service=None, hotspot_service=None,
        portfolio_service=None, web_search_service=None, research_mode="deep")
    deps.tool_results = [{"tool":"search_stocks", "data":[{"code":"US.NVDA"}], "evidence":[]},
                         {"tool":"get_stock_news", "data":[], "evidence":[{"id":"ev_a"}]}]
    deps.verified_us_codes.add("US.NVDA")
    deps.user_query = "详细研究英伟达股票"
    assert requires_deep_company_flow(deps)
    deps.user_query = "详细分析我之前对英伟达的观点和记忆"
    assert not requires_deep_company_flow(deps)
    deps.user_query = "对比英伟达和苹果的公司股票详细研究"
    assert not requires_deep_company_flow(deps)


def test_deep_parent_withdraws_research_tools_to_reserve_answer_requests(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from api.research_capability import ResearchCapability
    monkeypatch.setenv('FINANCIAL_BOT_DEEP_MODEL_REQUEST_LIMIT', '12')
    deps = AgentDeps(stock_service=None, news_service=None, hotspot_service=None,
                     portfolio_service=None, web_search_service=None, research_mode='deep')
    ctx = SimpleNamespace(deps=deps, usage=SimpleNamespace(requests=9))
    tools = [SimpleNamespace(name='web_search'), SimpleNamespace(name='research_company')]
    available = asyncio.run(ResearchCapability().prepare_tools(ctx, tools))
    assert available == []
    assert deps.finish_reason == 'request_reserved_for_synthesis'


def test_specialist_reviews_do_not_consume_final_answer_reserve(monkeypatch):
    from api.agent_pydantic import admit_deep_review
    monkeypatch.setenv('FINANCIAL_BOT_DEEP_MODEL_REQUEST_LIMIT', '12')
    deps = AgentDeps(stock_service=None, news_service=None, hotspot_service=None,
                     portfolio_service=None, web_search_service=None, research_mode='deep')
    ctx = SimpleNamespace(usage=SimpleNamespace(requests=9))
    assert not admit_deep_review(deps, ctx, 'tradingagents_manager')
    assert deps.tool_results[-1]['reason_code'] == 'request_limit'


def test_private_diagnostics_preserve_only_specialist_exception_type(tmp_path, monkeypatch):
    import json
    from api.research_runs import save_run
    monkeypatch.setenv('RESEARCH_RUN_DIR', str(tmp_path))
    deps = AgentDeps(stock_service=None, news_service=None, hotspot_service=None,
                     portfolio_service=None, web_search_service=None, research_mode='deep')
    deps.record_tool({'tool': 'tradingagents_risk', 'status': 'unavailable',
                      'reason_code': 'specialist_error',
                      'data': {'data_gaps': ['Specialist review unavailable (UnexpectedModelBehavior); use original sources.',
                                             'private provider error body']}})
    save_run(deps, 'partial')
    saved = json.loads((tmp_path / f'{deps.run_id}.json').read_text())
    tool = saved['tools'][0]
    assert tool['error_type'] == 'UnexpectedModelBehavior'
    assert 'private provider error body' not in json.dumps(saved)


def test_specialist_diagnostic_records_fixed_categories_only():
    from api.research_runs import specialist_failure_category
    from pydantic_ai.exceptions import UnexpectedModelBehavior, ModelRetry
    error = UnexpectedModelBehavior('Exceeded maximum output retries (1)')
    error.__cause__ = ModelRetry('Select the retrieved evidence IDs supporting your answer.')
    assert specialist_failure_category(error) == 'output_evidence_selection'
    error.__cause__ = ModelRetry('private provider details and secret')
    assert specialist_failure_category(error) == 'output_retries_exhausted'
    assert specialist_failure_category(ValueError('private detail')) == 'other'


def test_deep_result_using_unverified_financial_currency_is_partial():
    from api.agent_pydantic import AgentOutput, build_agent_response, mark_deep_review_incomplete
    deps = AgentDeps(stock_service=None, news_service=None, hotspot_service=None,
        portfolio_service=None, web_search_service=None, research_mode='deep', language='zh-CN')
    deps.tool_results = [{'tool': 'get_company_fundamentals', 'data': {'currency': None, 'currency_basis': 'unverified'},
                          'evidence': [{'id': 'ev_abc123', 'source': 'yfinance', 'url': None,
                                        'retrieved_at': '2026-11-04T00:00:00Z', 'freshness': 'reported_period'}]}]
    response = build_agent_response(AgentOutput(summary='财报数据 [ev_abc123]。', evidence_ids=['ev_abc123']), deps.tool_results, 'zh-CN')
    mark_deep_review_incomplete(response, deps)
    assert classify_response(response) == ('partial', 'deep_research_incomplete')
    assert any('币种' in gap for gap in response.data_gaps)
