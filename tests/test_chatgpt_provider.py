"""Local ChatGPT plan provider must use the supported Responses contract."""
from api.financial_bot.chatgpt_provider import make_chatgpt_model


def test_chatgpt_model_uses_public_streamed_responses_contract():
    model = make_chatgpt_model(lambda: 'test-token', 'gpt-6-luna')
    assert str(model.client.base_url).rstrip('/') == 'https://api.openai.com/v1'
    assert model.profile['openai_responses_requires_streaming']
    assert model.profile['openai_responses_requires_store_false']
    assert model.profile['openai_system_prompt_role'] == 'developer'
    assert 'max_tokens' in model.profile['openai_unsupported_model_settings']
    assert 'with_tool_search' not in model.supported_tool_deferral_modes


def test_chatgpt_wire_request_omits_unsupported_fields():
    import asyncio, json, httpx, pytest
    from pydantic_ai import Agent
    from pydantic_ai.exceptions import ModelHTTPError

    async def run():
        captured = []
        def transport(request):
            captured.append(json.loads(request.content))
            return httpx.Response(400, json={'error': {'message':'offline test', 'type':'invalid_request_error'}})
        client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        model = make_chatgpt_model(lambda:'test-token', 'gpt-6-luna', http_client=client)
        agent = Agent(model, system_prompt='test system', model_settings={'max_tokens':100, 'temperature':0.1})
        with pytest.raises(ModelHTTPError):
            await agent.run('hello')
        payload = captured[0]
        assert payload['stream'] is True and payload['store'] is False
        assert 'max_output_tokens' not in payload and 'temperature' not in payload
        assert all(item.get('role') != 'system' for item in payload['input'])
        assert not any(tool.get('type') == 'tool_search' for tool in payload.get('tools', []))
        await client.aclose()
    asyncio.run(run())


def test_chatgpt_wire_groups_function_tools_in_input():
    import asyncio, json, httpx, pytest
    from pydantic_ai import Agent
    from pydantic_ai.exceptions import ModelHTTPError

    async def run():
        captured = []
        def transport(request):
            captured.append(json.loads(request.content))
            return httpx.Response(400, json={'error': {'message': 'offline test', 'type': 'invalid_request_error'}})
        client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        agent = Agent(make_chatgpt_model(lambda: 'test-token', 'gpt-6-luna', http_client=client))
        @agent.tool_plain
        def example(value: str) -> str:
            return value
        with pytest.raises(ModelHTTPError):
            await agent.run('hello')
        payload = captured[0]
        assert not any(tool.get('type') == 'function' for tool in payload.get('tools', []))
        group = next(item for item in payload['input'] if item.get('type') == 'additional_tools')
        assert any(tool['name'] == 'example' for tool in group['tools'])
        await client.aclose()
    asyncio.run(run())


def test_auth_registration_is_separate_from_tradingagents(tmp_path, monkeypatch):
    from api.financial_bot import chatgpt_session
    monkeypatch.setattr(chatgpt_session, 'SESSION_DIR', tmp_path / 'financial-bot')
    auth = chatgpt_session.load_auth_module()
    assert auth.APP_NAME == 'Financial Bot'
    assert auth.AUTH_FILE == tmp_path / 'financial-bot' / 'chatgpt-auth.json'
    assert 'tradingagents' not in str(auth.AUTH_FILE)


def test_research_agent_accepts_separately_supplied_model():
    from pydantic_ai.models.function import FunctionModel
    from api.agent_pydantic import create_agent
    model = FunctionModel(lambda messages, info: None)
    agent = create_agent(model_instance=model)
    assert agent.model is model


def test_failover_only_before_any_tool_or_streamed_output():
    from types import SimpleNamespace
    from api.financial_bot.chatgpt_provider import can_fallback_after_failure
    from pydantic_ai.exceptions import ModelHTTPError
    rejected = ModelHTTPError(status_code=429, model_name='gpt', body={'error':'limit'})
    clean = SimpleNamespace(tool_results=[], stream_text='')
    assert can_fallback_after_failure(rejected, clean)
    assert not can_fallback_after_failure(rejected, SimpleNamespace(tool_results=[{'tool':'manage_watchlist'}], stream_text=''))
    assert not can_fallback_after_failure(rejected, SimpleNamespace(tool_results=[], stream_text='partly streamed'))
    assert not can_fallback_after_failure(RuntimeError('unknown'), clean)


def test_unavailable_chatgpt_uses_deepseek_before_task_starts(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from api import main
    from api.schemas import AgentQuery, AgentResponse
    from api.financial_bot import chatgpt_session
    fallback = object()
    deps = SimpleNamespace()
    monkeypatch.setattr(main, '_get_pydantic_agent', lambda: (fallback, deps))
    monkeypatch.setattr(main, '_explicit_watchlist_request', lambda *args: asyncio.sleep(0, result=None))
    monkeypatch.setattr(main.agent_service, 'clarification_for_query', lambda *args: None)
    monkeypatch.setattr(chatgpt_session, 'is_signed_in', lambda: False)
    async def fake_run(agent, deps, query, **kwargs):
        assert agent is fallback
        return AgentResponse(intent='pydantic_ai_agent', summary='fixture', payload={'_meta':{}})
    monkeypatch.setattr(main, '_load_agent_runtime', lambda: (None, None, fake_run))
    async def run():
        events = []
        result = await main._run_agent_request(AgentQuery(query='test', model_provider='chatgpt'), [], {},
            lambda *args: events.append(args), allow_chatgpt=True)
        assert result.payload['_meta']['model_provider'] == 'deepseek'
        assert result.payload['_meta']['model_fallback'] == 'chatgpt_unavailable'
        assert any('DeepSeek' in str(event) for event in events)
    asyncio.run(run())


def test_select_chatgpt_model_prefers_terra():
    from api.financial_bot.chatgpt_provider import select_chatgpt_model
    assert select_chatgpt_model(["gpt-6-luna", "gpt-6.1-terra"]) == "gpt-6.1-terra"
    assert select_chatgpt_model(["gpt-6-luna"]) == "gpt-6-luna"


def test_chatgpt_terra_profile_enables_medium_reasoning():
    import asyncio, json, httpx, pytest
    from pydantic_ai import Agent
    from pydantic_ai.exceptions import ModelHTTPError
    from api.financial_bot.chatgpt_provider import make_chatgpt_model

    model = make_chatgpt_model(lambda: "test-token", "gpt-6.1-terra")
    assert model.profile["openai_supports_reasoning"] is True
    assert model.settings["openai_reasoning_effort"] == "medium"

    async def run():
        captured = []
        def transport(request):
            captured.append(json.loads(request.content))
            return httpx.Response(400, json={"error": {"message": "offline test", "type": "invalid_request_error"}})
        client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        agent = Agent(make_chatgpt_model(lambda: "test-token", "gpt-6.1-terra", http_client=client))
        with pytest.raises(ModelHTTPError):
            await agent.run("hello")
        payload = captured[0]
        assert payload.get("reasoning", {}).get("effort") == "medium"
        await client.aclose()
    asyncio.run(run())
