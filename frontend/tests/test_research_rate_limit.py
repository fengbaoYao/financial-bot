"""Cross-request research call frequency limits replace per-run token stops."""

from __future__ import annotations

from starlette.requests import Request as StarletteRequest


def _request(*, owner_header: str | None = None, client_host: str = "203.0.113.10", language_path="/"):
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": language_path,
        "raw_path": language_path.encode(),
        "query_string": b"",
        "headers": [],
        "client": (client_host, 443),
        "server": ("test", 80),
        "state": {},
    }
    request = StarletteRequest(scope)
    return request


def test_research_rate_defaults_and_disable(monkeypatch):
    from api.research_rate_limit import research_rate_limit_config, research_rate_limiter

    monkeypatch.delenv("FINANCIAL_BOT_RESEARCH_RATE_LIMIT", raising=False)
    monkeypatch.delenv("FINANCIAL_BOT_RESEARCH_RATE_WINDOW_SECONDS", raising=False)
    assert research_rate_limit_config() == (30, 3600)
    monkeypatch.setenv("FINANCIAL_BOT_RESEARCH_RATE_LIMIT", "0")
    assert research_rate_limit_config()[0] == 0
    research_rate_limiter.reset()


def test_research_rate_limit_blocks_after_window_quota(monkeypatch):
    from api.research_rate_limit import enforce_research_rate_limit, research_rate_limiter

    monkeypatch.setenv("FINANCIAL_BOT_RESEARCH_RATE_LIMIT", "2")
    monkeypatch.setenv("FINANCIAL_BOT_RESEARCH_RATE_WINDOW_SECONDS", "3600")
    research_rate_limiter.reset()
    request = _request()
    assert enforce_research_rate_limit(request, owner_id="owner-a", language="zh-CN") is None
    assert enforce_research_rate_limit(request, owner_id="owner-a", language="zh-CN") is None
    blocked = enforce_research_rate_limit(request, owner_id="owner-a", language="zh-CN")
    assert blocked is not None
    assert blocked.status_code == 429
    body = blocked.body
    import json
    detail = json.loads(body)
    assert detail["code"] == "research_rate_limit"
    assert "过于频繁" in detail["detail"]
    assert detail["limit"] == 2
    # Different owner is independent
    assert enforce_research_rate_limit(request, owner_id="owner-b", language="zh-CN") is None
    research_rate_limiter.reset()


def test_create_task_returns_chinese_rate_limit(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import api.financial_bot.routes as routes
    from api.financial_bot.auth import OwnerContext
    from api.financial_bot.db import Database
    from api.financial_bot.executor import TaskExecutor
    from api.financial_bot.runtime import configure, reset
    from api.financial_bot.store import FinancialBotStore
    from api.main import app
    from api.research_rate_limit import research_rate_limiter
    from api.schemas import AgentResponse

    db = Database(sqlite_path=tmp_path / "bot.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    executor = TaskExecutor(store)
    executor.set_agent_runner(lambda *_: AgentResponse(
        intent="answer", summary="ok", actions=[], citations=[], evidence=[],
        research_steps=[], data_gaps=[], payload={"_meta": {}},
    ))
    configure(store=store, executor=executor)
    owner = OwnerContext(owner_id="11111111-1111-4111-8111-111111111111", mode="supabase_authenticated")
    monkeypatch.setattr(routes, "resolve_owner", lambda request, config: owner)
    monkeypatch.setenv("FINANCIAL_BOT_RESEARCH_RATE_LIMIT", "1")
    monkeypatch.setenv("FINANCIAL_BOT_RESEARCH_RATE_WINDOW_SECONDS", "3600")
    research_rate_limiter.reset()
    try:
        client = TestClient(app)
        first = client.post("/api/tasks", json={"query": "研究腾讯", "session_id": "s-rate", "language": "zh-CN"})
        assert first.status_code in {200, 201}, first.text
        second = client.post("/api/tasks", json={"query": "再研究腾讯", "session_id": "s-rate", "language": "zh-CN"})
        assert second.status_code == 429, second.text
        body = second.json()
        assert body["code"] == "research_rate_limit"
        assert "过于频繁" in body["detail"]
    finally:
        research_rate_limiter.reset()
        reset()
