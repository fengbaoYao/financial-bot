import pytest
from fastapi.testclient import TestClient

import api.financial_bot.routes as routes
from api.financial_bot.auth import OwnerContext
from api.financial_bot.config import BotConfig
from api.financial_bot.db import Database
from api.financial_bot.executor import TaskExecutor
from api.financial_bot.runtime import configure, reset
from api.financial_bot.store import FinancialBotStore, LimitError
from api.main import app
from api.schemas import AgentResponse

client = TestClient(app)
OWNER = "11111111-1111-4111-8111-111111111111"


@pytest.fixture()
def limited_runtime(tmp_path):
    db = Database(sqlite_path=tmp_path / "bot.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    executor = TaskExecutor(store)
    config = BotConfig(
        data_dir=tmp_path,
        daily_task_limit=1,
        daily_token_limit=1000,
        backup_dir=None,
        third_party_retention_verified=False,
    )
    configure(config=config, store=store, executor=executor)
    yield store, executor
    reset()


def use_owner(monkeypatch):
    context = OwnerContext(owner_id=OWNER, mode="supabase_authenticated")
    monkeypatch.setattr(routes, "resolve_owner", lambda request, config: context)


def test_daily_task_limit_returns_429_without_starting_another_run(limited_runtime, monkeypatch):
    store, executor = limited_runtime
    use_owner(monkeypatch)

    async def runner(_owner, task, progress):
        return AgentResponse(intent="pydantic_ai_agent", summary="ok", payload={})

    executor.set_agent_runner(runner)
    first = client.post("/api/tasks", json={"query": "第一个任务"})
    assert first.status_code == 201
    second = client.post("/api/tasks", json={"query": "第二个任务"})
    assert second.status_code == 429, second.text
    assert second.json()["code"] == "daily_task_limit"
    assert len(store.list_tasks(OWNER)) == 1


def test_store_enforces_token_limit():
    import tempfile
    from pathlib import Path

    path = Path(tempfile.mkdtemp()) / "bot.sqlite3"
    db = Database(sqlite_path=path)
    db.initialize()
    store = FinancialBotStore(db)
    task, _ = store.create_task(
        owner_id=OWNER,
        query="token budget fixture",
        session_id=None,
        language="zh-CN",
        research_mode="quick",
        idempotency_key="tokens-1",
    )
    store.record_usage(OWNER, task["id"], input_tokens=800, output_tokens=400, cost_usd=0.01)
    with pytest.raises(LimitError) as excinfo:
        store.check_daily_limits(OWNER, max_tasks=5, max_tokens=1000)
    assert excinfo.value.code == "daily_token_limit"
    assert store.check_daily_limits(OWNER, max_tasks=5, max_tokens=5000)["task_count"] == 1


def test_bot_status_never_exposes_a_secret(limited_runtime, monkeypatch):
    import api.main as main

    use_owner(monkeypatch)
    monkeypatch.setattr(
        main.settings_store,
        "build_runtime_config",
        lambda *args, **kwargs: type(
            "RuntimeConfig", (), {"llm_api_key": None, "llm_model": "fixture-model"}
        )(),
    )
    response = client.get("/api/bot/status")
    assert response.status_code == 200
    text = response.text
    assert "sk-" not in text
    assert "api_key" not in text.lower()
    body = response.json()
    assert body["model"]["configured"] is False
    assert body["trial_gate"]["multi_user_open"] is False
    assert body["local_data"]["scope"] == "local_internal_only"
    assert set(body["provider_config"]) == {
        "finnhub", "brave_search", "sec_contact", "web_search_enabled", "web_search_timeout_seconds",
        "yfinance_shadow", "yfinance_shadow_dir", "yfinance_us_hk_fundamentals",
        "yfinance_primary_us_hk", "yfinance_a_share_fallback",
        "ifind", "ifind_primary", "ifind_history_enabled", "ifind_history_quota_exhausted", "ifind_history_note",
        "fuyao", "fuyao_primary_a_share", "fuyao_financials",
    }


def test_welcome_message_is_persisted_once_and_listed(limited_runtime, monkeypatch):
    store, _executor = limited_runtime
    use_owner(monkeypatch)
    bot = store.ensure_default_bot(OWNER)
    session_id = "session-welcome-api"
    first = client.post(
        f"/api/sessions/{session_id}/welcome",
        json={"session_id": session_id, "bot_id": bot["id"], "persona": "beginner", "language": "zh-CN"},
    )
    assert first.status_code == 200
    assert first.json()["created"] is True
    second = client.post(
        f"/api/sessions/{session_id}/welcome",
        json={"session_id": session_id, "bot_id": bot["id"], "persona": "beginner", "language": "zh-CN"},
    )
    assert second.status_code == 200
    assert second.json()["created"] is False
    messages = client.get(f"/api/sessions/{session_id}/messages").json()["messages"]
    assert [(item["role"], item["kind"]) for item in messages] == [("assistant", "welcome")]


def test_side_thread_is_listed_before_first_task(limited_runtime, monkeypatch):
    store, _executor = limited_runtime
    use_owner(monkeypatch)
    bot = store.ensure_default_bot(OWNER)
    created = client.post("/api/threads", json={"bot_id": bot["id"], "parent_session_id": "main-thread", "title": "独立话题"})
    assert created.status_code == 201, created.text
    session = created.json()["session"]
    assert session["kind"] == "side"
    assert session["parent_session_id"] == "main-thread"
    sessions = client.get(f"/api/sessions?bot_id={bot['id']}").json()["sessions"]
    assert any(item["id"] == session["session_id"] and item["kind"] == "side" and item["task_count"] == 0 for item in sessions)


def test_daily_token_limit_can_be_disabled_without_disabling_task_limit(tmp_path, monkeypatch):
    monkeypatch.setenv('FINANCIAL_BOT_DAILY_TOKEN_LIMIT', '0')
    config = BotConfig.from_env(tmp_path)
    assert config.daily_token_limit == 0
    db = Database(sqlite_path=tmp_path / 'unlimited.sqlite3')
    db.initialize()
    store = FinancialBotStore(db)
    task, _ = store.create_task(owner_id=OWNER, query='fixture', session_id=None,
                                language='zh-CN', research_mode='quick', idempotency_key='unlimited')
    store.record_usage(OWNER, task['id'], input_tokens=5000, output_tokens=0, cost_usd=0)
    assert store.check_daily_limits(OWNER, max_tasks=5, max_tokens=0)['token_total'] == 5000
    with pytest.raises(LimitError, match='任务数'):
        store.check_daily_limits(OWNER, max_tasks=1, max_tokens=0)
