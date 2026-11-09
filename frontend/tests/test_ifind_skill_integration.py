from api.datasources.ifind_provider import IfindProvider


def test_skill_smart_query_routes_before_fallback():
    calls = []
    def transport(url, payload, headers):
        calls.append((url, payload))
        return {'errorcode': 0, 'tables': [{'thscode': '600519.SH', 'table': {'latest': [1500]}}]}
    result = IfindProvider(token='test', transport=transport).smart_query('600519.SH 最新价')
    assert calls[0][0].endswith('/real_time_quotation')
    assert result['provider'] == 'ifind'
    assert result['data']['tables'][0]['table']['latest'] == [1500]


def test_ifind_skill_result_has_honest_source_evidence():
    from api.evidence import sources_for_tool
    evidence = sources_for_tool({'tool':'ifind_smart_query', 'retrieved_at':'2026-11-04T00:00:00Z', 'data':{'provider':'ifind', 'endpoint':'real_time_quotation', 'data':{'tables':[{'thscode':'600519.SH','table':{'latest':[1500]}}]}}})
    assert evidence and evidence[0].source == '同花顺 iFinD'
    assert evidence[0].freshness == 'provider_data_time_unverified'


def test_research_prefers_ifind_but_continues_after_failure(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from api.research_capability import ResearchCapability
    monkeypatch.setenv('IFIND_ACCESS_TOKEN', 'offline-test')
    monkeypatch.setenv('IFIND_ENABLED', '1')
    monkeypatch.setenv('IFIND_PRIMARY', '1')
    class Deps:
        user_query = '腾讯股价怎么样'
        research_mode = 'quick'
        model_request_limit = None
        tool_results = []
        verified_us_codes = set()
        def synthesis_reason(self): return None
    async def run():
        deps = Deps()
        tools = [SimpleNamespace(name='ifind_smart_query'), SimpleNamespace(name='get_stock_analysis'), SimpleNamespace(name='web_search')]
        first = await ResearchCapability().prepare_tools(SimpleNamespace(deps=deps), tools)
        assert [item.name for item in first] == ['ifind_smart_query']
        deps.tool_results = [{'tool': 'ifind_smart_query', 'status': 'ok', 'data': {'provider': 'ifind'}}]
        after_ok = await ResearchCapability().prepare_tools(SimpleNamespace(deps=deps), tools)
        assert any(item.name == 'web_search' for item in after_ok)
        assert not any(item.name == 'get_stock_analysis' for item in after_ok)
        deps.tool_results = [{'tool': 'ifind_smart_query', 'status': 'unavailable'}]
        again = await ResearchCapability().prepare_tools(SimpleNamespace(deps=deps), tools)
        assert any(item.name == 'get_stock_analysis' for item in again)
    asyncio.run(run())


def test_expired_access_can_refresh_once_and_retry(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv('IFIND_REFRESH_TOKEN', 'test-refresh')
    calls = []
    def transport(url, payload, headers):
        calls.append(headers['access_token'])
        if len(calls) == 1:
            return {'errorcode': -1301}
        return {'errorcode': 0, 'tables': [{'table': {'latest': [1]}}]}
    monkeypatch.setattr('tonghuashun_ifind_skill.auth.exchange_refresh_token', lambda *a, **k: SimpleNamespace(access_token='new-access'))
    result = IfindProvider(token='expired-access', transport=transport)._post('real_time_quotation', {})
    assert result['errorcode'] == 0
    assert calls == ['expired-access', 'new-access']


def test_failed_refresh_returns_control_to_caller_without_retry_loop(monkeypatch):
    import pytest
    from api.datasources.ifind_provider import IfindUnavailable
    monkeypatch.setenv('IFIND_REFRESH_TOKEN', 'test-refresh')
    calls = []
    def transport(url, payload, headers):
        calls.append(True)
        return {'errorcode': -1301}
    def fail(*args, **kwargs):
        raise RuntimeError('invalid refresh')
    monkeypatch.setattr('tonghuashun_ifind_skill.auth.exchange_refresh_token', fail)
    with pytest.raises(IfindUnavailable):
        IfindProvider(token='expired-access', transport=transport)._post('real_time_quotation', {})
    assert len(calls) == 1


def test_history_request_is_not_misrepresented_as_realtime(monkeypatch):
    import pytest
    from api.datasources.ifind_provider import IfindUnavailable
    monkeypatch.setattr("api.datasources.ifind_provider.history_quota_exhausted", lambda: True)
    calls = []
    def transport(url, payload, headers):
        calls.append(url)
        return {'errorcode': 0, 'tables': [{'table': {'latest': [1]}}]}
    with pytest.raises(IfindUnavailable, match='history_quota_exhausted'):
        IfindProvider(token='test', transport=transport).smart_query('600519.SH近一月行情')
    assert not any(url.endswith('/real_time_quotation') for url in calls)


def test_skill_empty_data_falls_back_instead_of_claiming_success():
    import pytest
    from api.datasources.ifind_provider import IfindUnavailable
    def transport(url, payload, headers):
        return {'errorcode': 0, 'tables': [{'thscode': '600519.SH', 'table': {'latest': [None]}}]}
    with pytest.raises(IfindUnavailable, match='skill_empty_result'):
        IfindProvider(token='test', transport=transport).smart_query('600519.SH 最新价')


def test_ifind_prefetch_failure_returns_to_normal_agent_path(monkeypatch):
    import asyncio
    from api.agent_pydantic import AgentDeps, prefetch_ifind_skill
    monkeypatch.setenv('IFIND_ACCESS_TOKEN', 'offline')
    monkeypatch.setenv('IFIND_ENABLED', '1')
    monkeypatch.setattr(IfindProvider, 'smart_query', lambda *args: (_ for _ in ()).throw(RuntimeError('unavailable')))
    deps = AgentDeps(stock_service=None, news_service=None, hotspot_service=None, portfolio_service=None, web_search_service=None, user_query='腾讯股价')
    result = asyncio.run(prefetch_ifind_skill(deps))
    assert '不可用' in result
    assert deps.tool_results[-1]['status'] == 'unavailable'


def test_verbose_hong_kong_quote_uses_skill_realtime_route():
    calls = []
    def transport(url, payload, headers):
        calls.append((url, payload))
        return {'errorcode': 0, 'tables': [{'thscode': '0700.HK', 'table': {'latest': [400]}}]}
    result = IfindProvider(token='test', transport=transport).smart_query('请查询腾讯控股 00700.HK 的最新可用报价，注明数据来源、报价时间和延迟状态。')
    assert result['endpoint'] == 'real_time_quotation'
    assert calls[-1][1]['codes'] == '0700.HK'


def test_refreshed_access_is_reused_for_new_provider_instances(monkeypatch):
    from types import SimpleNamespace
    from api.datasources import ifind_provider
    monkeypatch.setenv('IFIND_REFRESH_TOKEN', 'isolated-cache-refresh')
    monkeypatch.delenv('IFIND_ACCESS_TOKEN', raising=False)
    monkeypatch.delenv('ifind_api_access_token', raising=False)
    calls = []
    bundle = SimpleNamespace(access_token='cached-access', is_stale=lambda: False)
    monkeypatch.setattr('tonghuashun_ifind_skill.auth.exchange_refresh_token', lambda *a, **kw: calls.append(1) or bundle)
    one = IfindProvider(transport=lambda *_: {'errorcode': 0, 'tables': [{'table': {'latest': [1]}}]})
    one._post('real_time_quotation', {})
    two = IfindProvider(transport=lambda *_: {'errorcode': 0, 'tables': [{'table': {'latest': [1]}}]})
    two._post('real_time_quotation', {})
    assert len(calls) == 1


def test_financial_question_detection_supports_skill_aliases_and_market_terms():
    from api.agent_pydantic import should_prefetch_ifind
    for query in ['宁王怎么样', 'AAPL 怎么样', '今天北向资金流入多少', '中芯最近回购了吗', '比亚迪近一月行情', '今天哪些股票涨停', '上证指数点位']:
        assert should_prefetch_ifind(query), query
    for query in ['你好', '怎样创建助手', '今天北京天气如何', '帮我记住我看好腾讯', '我的持仓多少钱']:
        assert not should_prefetch_ifind(query), query


def test_event_query_does_not_silently_turn_into_stock_quote():
    calls = []
    def transport(url, payload, headers):
        calls.append((url, payload))
        return {'errorcode': 0, 'tables': [{'table': {'公告标题': ['测试公告']}}]}
    result = IfindProvider(token='test', transport=transport).smart_query('中芯最近回购了吗')
    assert result['endpoint'] == 'smart_stock_picking'
    assert '回购' in calls[-1][1]['searchstring']


def test_us_ticker_query_uses_us_search_and_validates_identity():
    calls = []
    def transport(url, payload, headers):
        calls.append((url, payload))
        return {'errorcode': 0, 'tables': [{'table': {'股票代码': ['AAPL.O'], '股票简称': ['苹果'], '收盘价': [200]}}]}
    result = IfindProvider(token='test', transport=transport).smart_query('AAPL 怎么样')
    assert result['endpoint'] == 'smart_stock_picking'
    assert calls[-1][1]['searchtype'] == 'US_stock'


def test_us_ticker_does_not_accept_wrong_company():
    import pytest
    from api.datasources.ifind_provider import IfindUnavailable
    def transport(url, payload, headers):
        return {'errorcode': 0, 'tables': [{'table': {'股票代码': ['MSFT.O'], '股票简称': ['微软']}}]}
    with pytest.raises(IfindUnavailable, match='skill_identity_mismatch'):
        IfindProvider(token='test', transport=transport).smart_query('AAPL 怎么样')


def test_us_ticker_identity_only_result_is_not_claimed_as_research():
    import pytest
    from api.datasources.ifind_provider import IfindUnavailable
    def transport(url, payload, headers):
        return {'errorcode': 0, 'tables': [{'table': {'股票代码': ['AAPL.O'], '股票简称': ['苹果']}}]}
    with pytest.raises(IfindUnavailable, match='skill_no_substantive_data'):
        IfindProvider(token='test', transport=transport).smart_query('AAPL 怎么样')


def test_one_month_history_uses_ifind_history_route(monkeypatch):
    from api.datasources import ifind_provider
    monkeypatch.setattr(ifind_provider, 'history_quota_exhausted', lambda: False)
    calls = []
    def transport(url, payload, headers):
        calls.append((url, payload))
        return {'errorcode': 0, 'tables': [{'thscode':'600519.SH', 'time':['2026-11-01'], 'table': {'close':[1500]}}]}
    out = IfindProvider(token='test', transport=transport).smart_query('600519.SH近一月行情')
    assert out['endpoint'] == 'cmd_history_quotation'
    assert 'startdate' in calls[-1][1] and 'enddate' in calls[-1][1]


def test_personal_portfolio_is_not_sent_to_ifind_smart_query():
    from api.agent_pydantic import should_prefetch_ifind
    for query in ['我持有腾讯1000股，成本300，应该怎么看？', '我买入的茅台现在亏了多少', '帮我分析我的仓位和股票', '我的账户余额是多少']:
        assert not should_prefetch_ifind(query), query
    assert should_prefetch_ifind('腾讯最近资金流怎么样')


def test_multiple_us_tickers_are_not_misrouted_as_a_single_security():
    import pytest
    from api.datasources.ifind_provider import IfindUnavailable
    calls = []
    def transport(url, payload, headers):
        calls.append(url)
        return {'errorcode': 0, 'tables': [{'table': {'股票代码':['AAPL.O'], '最新价':[100]}}]}
    with pytest.raises(IfindUnavailable, match='skill_multi_symbol_unsupported'):
        IfindProvider(token='test', transport=transport).smart_query('比较 AAPL 和 MSFT 的营收')
    assert not calls


def test_prefetch_quota_failure_explains_gap_and_keeps_running(monkeypatch):
    import asyncio
    from api.agent_pydantic import AgentDeps, prefetch_ifind_skill
    from api.datasources.ifind_provider import IfindUnavailable
    monkeypatch.setenv('IFIND_ACCESS_TOKEN', 'offline')
    monkeypatch.setenv('IFIND_ENABLED', '1')
    def fail(*_args):
        raise IfindUnavailable('history_quota_exhausted:-4318', quota_exhausted=True)
    monkeypatch.setattr(IfindProvider, 'smart_query', fail)
    deps = AgentDeps(stock_service=None, news_service=None, hotspot_service=None, portfolio_service=None, web_search_service=None, user_query='比亚迪近一月行情')
    asyncio.run(prefetch_ifind_skill(deps))
    row = deps.tool_results[-1]
    assert row['status'] == 'unavailable'
    assert '额度' in row['data']['data_gaps'][0]


def test_lowercase_us_ticker_with_financial_context_routes_to_us():
    calls = []
    def transport(url, payload, headers):
        calls.append(payload)
        return {'errorcode': 0, 'tables': [{'table': {'股票代码': ['AAPL.O'], '最新价': [200]}}]}
    result = IfindProvider(token='test', transport=transport).smart_query('aapl 股价怎么样')
    assert result['endpoint'] == 'smart_stock_picking'
    assert calls[-1]['searchtype'] == 'US_stock'
