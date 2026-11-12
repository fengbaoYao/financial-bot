import asyncio
import hashlib
import json

import pytest

from api.financial_bot import data_gap_fill as fill
from api.schemas import AgentResponse, ResearchEvidence


def _response(**kwargs):
    base = dict(
        intent="pydantic_ai_agent",
        summary="部分数据缺失",
        actions=[],
        citations=[],
        evidence=[],
        data_gaps=["get_company_fundamentals：来源数据不可用。"],
        payload={"_meta": {"answer_basis": "source_data"}, "data_cards": []},
    )
    base.update(kwargs)
    return AgentResponse(**base)


def test_disabled_skips_model(monkeypatch):
    monkeypatch.setenv("LLM_DATA_GAP_FILL", "0")
    called = {"n": 0}

    async def boom(messages):
        called["n"] += 1
        raise AssertionError("should not call model")

    out = asyncio.run(
        fill.apply_data_gap_fill(
            _response(),
            query="茅台营收",
            language="zh",
            model_provider="deepseek",
            tool_results=[{
                "tool": "get_company_fundamentals",
                "status": "unavailable",
                "data": {"data_gaps": ["provider down"]},
            }],
            complete_fn=boom,
        )
    )
    assert called["n"] == 0
    assert not (out.payload.get("_meta") or {}).get("llm_data_gap_fill")


def test_selected_deepseek_text_completion_disables_thinking(monkeypatch):
    import httpx
    import openai
    from types import SimpleNamespace

    from api import main

    request_bodies = []

    def provider(request):
        request_bodies.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "text-fallback",
            "object": "chat.completion",
            "created": 0,
            "model": "deepseek-flash",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": "根据已检索证据给出结论。"
            }}],
        })

    real_client = openai.AsyncOpenAI(
        api_key="offline-test",
        base_url="https://offline.invalid/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(provider)),
    )
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **_kwargs: real_client)
    monkeypatch.setattr(
        main.settings_store,
        "build_runtime_config",
        lambda _config: SimpleNamespace(
            llm_api_key="offline-test",
            llm_base_url="https://offline.invalid/v1",
            llm_model="deepseek-flash",
        ),
    )

    async def scenario():
        try:
            result = await fill.complete_with_selected_provider(
                [{"role": "user", "content": "test"}], model_provider="deepseek"
            )
            assert result == "根据已检索证据给出结论。"
        finally:
            await real_client.close()

    asyncio.run(scenario())
    assert request_bodies[0]["thinking"] == {"type": "disabled"}


def test_fill_marked_unverified_and_uses_selected_provider(monkeypatch):
    monkeypatch.setenv("LLM_DATA_GAP_FILL", "1")
    field_key = 'response.gap:' + hashlib.sha1('缺少公司主营业务背景'.encode()).hexdigest()[:8]

    async def fake_complete(messages):
        assert '缺少公司主营业务背景' in '\n'.join(item['content'] for item in messages)
        return json.dumps({"fills": [{
            "field_key": field_key,
            "value": "茅台主要经营白酒产品。",
            "confidence": "low",
        }], "still_unconfirmed": []}, ensure_ascii=False)

    # Ensure complete_with_selected_provider is not used; inject complete_fn.
    out = asyncio.run(
        fill.apply_data_gap_fill(
            _response(
                    data_gaps=["缺少公司主营业务背景"],
                evidence=[
                    ResearchEvidence(
                        id="ev_provider",
                        source="fuyao",
                        url="/api/stocks/sh600519/analysis",
                        retrieved_at="2026-11-04T00:00:00+00:00",
                        freshness="historical_or_delayed",
                        title="quote",
                    )
                ],
                payload={
                    "_meta": {"answer_basis": "source_data"},
                    "data_cards": [{
                        "kind": "valuations",
                        "title": "估值",
                        "provider": "fuyao",
                        "rows": [{"code": "600519.SH", "pe_ttm": 21}],
                    }],
                },
            ),
                query="茅台的主营业务是什么",
            language="zh",
            model_provider="chatgpt",
            tool_results=[{
                "tool": "get_company_fundamentals",
                "status": "unavailable",
                "data": {"data_gaps": ["yfinance down"]},
            }],
            complete_fn=fake_complete,
        )
    )
    meta = out.payload["_meta"]
    assert meta["llm_data_gap_fill"] is True
    assert meta["llm_data_gap_fill_provider"] == "chatgpt"
    assert meta["answer_basis"] == "source_data"
    assert meta["answer_basis_detail"] == "source_data_with_model_fill"
    # UX primary: research tool row, not a standalone model_fill blue card.
    assert any(step.tool == "model_knowledge_fill" for step in out.research_steps)
    fill_step = next(step for step in out.research_steps if step.tool == "model_knowledge_fill")
    assert fill_step.status == "completed"
    assert fill_step.reason_code == "unverified_model"
    assert "模型知识补齐" in (fill_step.endpoint or "")
    assert not any(card.get("kind") == "model_fill" for card in out.payload["data_cards"])
    assert out.payload["_meta"].get("model_fill_as_tool") is True
    assert any("未经数据源核验" in gap for gap in out.data_gaps)
    assert any(ev.id.startswith("ev_model_") for ev in out.evidence)
    assert any(ev.source == "fuyao" for ev in out.evidence)
    # Provider card untouched.
    assert out.payload["data_cards"][0]["provider"] == "fuyao"


def test_does_not_overwrite_good_provider_data(monkeypatch):
    monkeypatch.setenv("LLM_DATA_GAP_FILL", "1")

    async def fake_complete(messages):
        return (
            '{"fills":[{"field_key":"get_company_fundamentals.periods",'
            '"value":"假数据","confidence":"low"}],"still_unconfirmed":[]}'
        )

    out = asyncio.run(
        fill.apply_data_gap_fill(
            _response(
                data_gaps=["次要缺口"],
                payload={
                    "_meta": {"answer_basis": "source_data"},
                    "data_cards": [{
                        "kind": "financials",
                        "title": "利润表",
                        "provider": "fuyao",
                        "rows": [{"period": "2025", "values": {"revenue": 100}}],
                    }],
                },
            ),
            query="茅台营收",
            language="zh",
            model_provider="deepseek",
            tool_results=[{
                "tool": "get_company_fundamentals",
                "status": "completed",
                "data": {
                    "periods": [{"period": "2025", "items": {"revenue": 100}}],
                    "data_gaps": ["次要缺口"],
                },
            }],
            complete_fn=fake_complete,
        )
    )
    # Good financials card must remain first / provider-tagged.
    financial = [c for c in out.payload["data_cards"] if c.get("kind") == "financials"]
    assert financial and financial[0]["provider"] == "fuyao"
    assert financial[0]["rows"][0]["values"]["revenue"] == 100
    # Fill for that field_key is skipped because provider data present.
    model_cards = [c for c in out.payload["data_cards"] if c.get("kind") == "model_fill"]
    if model_cards:
        assert all(row["field"] != "get_company_fundamentals.periods" for row in model_cards[0]["rows"])


def test_rejects_fake_urls_and_provider_claims(monkeypatch):
    monkeypatch.setenv("LLM_DATA_GAP_FILL", "1")
    field_key = 'response.gap:' + hashlib.sha1('缺少公司主营业务背景'.encode()).hexdigest()[:8]

    async def fake_complete(messages):
        return json.dumps({"fills": [
            {"field_key": field_key, "value": "虚假链接值", "url": "https://fake.example/x"},
            {"field_key": field_key, "value": "冒充来源值", "source": "yfinance"},
            {"field_key": field_key, "value": "稳定背景值", "confidence": "low"},
        ], "still_unconfirmed": []}, ensure_ascii=False)

    out = asyncio.run(
        fill.apply_data_gap_fill(
            _response(data_gaps=["缺少公司主营业务背景"]),
            query="腾讯的主营业务是什么",
            language="zh",
            model_provider="deepseek",
            tool_results=[{
                "tool": "get_benchmark_comparison",
                "status": "unavailable",
                "data": {"data_gaps": ["down"]},
            }],
            complete_fn=fake_complete,
        )
    )
    fills = out.payload.get("llm_gap_fills") or []
    keys = {row["field_key"] for row in fills}
    assert keys == {field_key}
    assert [row["value"] for row in fills] == ["稳定背景值"]


def test_collect_material_gaps_from_unavailable_tools():
    gaps = fill.collect_material_gaps(
        _response(data_gaps=[]),
        [{
            "tool": "get_benchmark_comparison",
            "status": "unavailable",
            "data": {"data_gaps": ["benchmark unavailable"]},
        }, {
            "tool": "search_stocks",
            "status": "unavailable",
            "data": {},
        }],
    )
    assert any(g["tool"] == "get_benchmark_comparison" for g in gaps)
    assert not any(g["tool"] == "search_stocks" for g in gaps)


def test_missing_provider_model_fill_is_visible_and_does_not_claim_completion(monkeypatch):
    monkeypatch.setenv('LLM_DATA_GAP_FILL', '1')
    calls = []
    async def completion(_messages):
        calls.append(True)
        return '{"fills":[{"field_key":"get_stock_news.payload","value":"历史背景仅供参考","confidence":"low"}],"still_unconfirmed":["最新公告尚无法核验"]}'
    original = _response(intent='error', summary='新闻数据暂不可用', data_gaps=['新闻源不可用'])
    out = asyncio.run(fill.apply_data_gap_fill(original, query='分析新闻', language='zh-CN', tool_results=[{'tool':'get_stock_news','status':'unavailable','data':None}], complete_fn=completion))
    assert calls == []
    assert out.summary == '新闻数据暂不可用'
    assert '新闻源不可用' in out.data_gaps


def test_model_fill_failure_preserves_original_answer_and_gaps(monkeypatch):
    monkeypatch.setenv('LLM_DATA_GAP_FILL', '1')
    async def completion(_messages):
        raise RuntimeError('provider unavailable')
    original = _response(summary='保留已取得的行情', data_gaps=['公告数据不可用'])
    out = asyncio.run(fill.apply_data_gap_fill(original, query='腾讯公告', language='zh-CN', tool_results=[{'tool':'get_stock_news','status':'unavailable','data':None}], complete_fn=completion))
    assert out.summary == '保留已取得的行情'
    assert '公告数据不可用' in out.data_gaps
    assert not out.payload['_meta'].get('llm_data_gap_fill')


def test_model_fill_cannot_override_usage_limit(monkeypatch):
    monkeypatch.setenv('LLM_DATA_GAP_FILL', '1')
    calls = []
    async def completion(_messages):
        calls.append(True)
        raise AssertionError('must not call the model after a limit')
    original = _response(payload={'_meta': {'limit_reached': True}})
    out = asyncio.run(fill.apply_data_gap_fill(original, query='腾讯', language='zh-CN', complete_fn=completion))
    assert out is original
    assert not calls


def test_model_fill_failure_is_visible_and_partial(monkeypatch):
    monkeypatch.setenv('LLM_DATA_GAP_FILL', '1')
    calls = []
    async def completion(_messages):
        calls.append(True)
        raise RuntimeError('unavailable')
    original = _response(summary='已有的行情', data_gaps=['公告源不可用'])
    out = asyncio.run(fill.apply_data_gap_fill(original, query='公告', language='zh-CN', tool_results=[{'tool':'get_stock_news','status':'unavailable','data':None}], complete_fn=completion))
    assert calls == []
    assert out.data_gaps == ['公告源不可用']


@pytest.mark.parametrize('query', [
    '腾讯近期震荡回弹的原因是什么',
    '腾讯现在股价多少',
    '腾讯今天发布了什么公告',
    '腾讯在11月10日为什么上涨',
])
def test_time_sensitive_and_causal_questions_never_use_model_fill(monkeypatch, query):
    monkeypatch.setenv('LLM_DATA_GAP_FILL', '1')
    calls = []

    async def completion(_messages):
        calls.append(True)
        return '{"fills":[],"still_unconfirmed":[]}'

    original = _response(summary='已核实的行情片段', data_gaps=['缺少近期催化证据'])
    out = asyncio.run(fill.apply_data_gap_fill(
        original,
        query=query,
        language='zh-CN',
        tool_results=[{'tool': 'get_stock_news', 'status': 'unavailable', 'data': None}],
        complete_fn=completion,
    ))

    assert calls == []
    assert out.summary == '已核实的行情片段'
    assert out.data_gaps == ['缺少近期催化证据']
    assert not out.payload['_meta'].get('llm_data_gap_fill')


def test_only_stable_background_gaps_are_sent_to_model(monkeypatch):
    monkeypatch.setenv('LLM_DATA_GAP_FILL', '1')
    seen = {}

    field_key = 'response.gap:' + hashlib.sha1('缺少公司主营业务背景'.encode()).hexdigest()[:8]

    async def completion(messages):
        seen['prompt'] = '\n'.join(item['content'] for item in messages)
        return json.dumps({
            'fills': [{'field_key': field_key, 'value': '腾讯主要从事互联网平台与数字服务。', 'confidence': 'low'}],
            'still_unconfirmed': [],
        }, ensure_ascii=False)

    original = _response(data_gaps=['缺少公司主营业务背景'])
    out = asyncio.run(fill.apply_data_gap_fill(
        original,
        query='腾讯的主营业务是什么',
        language='zh-CN',
        tool_results=None,
        complete_fn=completion,
    ))

    assert '缺少公司主营业务背景' in seen['prompt']
    assert '免责声明' in seen['prompt']
    assert '腾讯主要从事互联网平台与数字服务。' in out.summary
    assert out.payload['_meta'].get('llm_data_gap_fill') is True


def test_model_fill_must_match_a_requested_stable_field(monkeypatch):
    monkeypatch.setenv('LLM_DATA_GAP_FILL', '1')

    async def completion(_messages):
        return '{"fills":[{"field_key":"unrequested.current_price","value":"999","confidence":"low"}],"still_unconfirmed":[]}'

    original = _response(data_gaps=['缺少公司主营业务背景'])
    out = asyncio.run(fill.apply_data_gap_fill(
        original,
        query='腾讯的主营业务是什么',
        language='zh-CN',
        tool_results=None,
        complete_fn=completion,
    ))

    assert out.summary == original.summary
    assert not out.payload['_meta'].get('llm_data_gap_fill')
