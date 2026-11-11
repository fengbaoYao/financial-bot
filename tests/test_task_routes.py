import time

import pytest
from fastapi.testclient import TestClient

import api.financial_bot.routes as routes
from api.financial_bot.auth import OwnerContext
from api.financial_bot.db import Database
from api.financial_bot.executor import TaskExecutor
from api.financial_bot.runtime import configure, reset
from api.financial_bot.store import FinancialBotStore
from api.main import app
from api.schemas import AgentResponse, ResearchEvidence, ResearchStep

client = TestClient(app)
OWNER = "11111111-1111-4111-8111-111111111111"


@pytest.fixture()
def bot_runtime(tmp_path, monkeypatch):
    db = Database(sqlite_path=tmp_path / "bot.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    executor = TaskExecutor(store)
    configure(store=store, executor=executor)
    context = OwnerContext(owner_id=OWNER, mode="supabase_authenticated")
    monkeypatch.setattr(routes, "resolve_owner", lambda request, config: context)
    yield store, executor
    reset()


def fast_response():
    return AgentResponse(
        intent="pydantic_ai_agent",
        summary="回答基于一项示例来源。",
        actions=[],
        citations=[],
        evidence=[
            ResearchEvidence(
                id="ev_1",
                source="example",
                url="https://example.org/a",
                retrieved_at="2026-10-28T00:00:00+00:00",
                data_as_of="2026-10-27",
                freshness="historical_or_delayed",
                title="示例来源",
            )
        ],
        research_steps=[
            ResearchStep(tool="get_stock_analysis", status="completed", retrieved_at="2026-10-28T00:00:00+00:00", evidence_count=1)
        ],
        data_gaps=[],
        payload={"_meta": {"tools_used": ["get_stock_analysis"], "usage": {"input_tokens": 10, "output_tokens": 5}}},
    )


def wait_terminal(store, task_id, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = store.get_task(OWNER, task_id)
        if task["status"] in {"completed", "partial", "failed", "cancelled", "dropped", "unknown"}:
            return task
        time.sleep(0.05)
    raise AssertionError(f"task did not finish: {store.get_task(OWNER, task_id)['status']}")


def test_paused_platform_rejects_new_tasks(bot_runtime):
    store, executor = bot_runtime
    executor.set_agent_runner(lambda *_: fast_response())
    store.set_platform_pause(paused=True, by="budget", reason="monthly budget reached")

    response = client.post("/api/tasks", json={"query": "研究腾讯", "session_id": "s1"})
    assert response.status_code == 409
    body = response.json()
    assert body["detail"] == "platform_paused"
    assert body["reason"] == "monthly budget reached"
    assert store.list_tasks(OWNER) == []

    store.set_platform_pause(paused=False, by="ops", reason=None)
    created = client.post("/api/tasks", json={"query": "研究腾讯", "session_id": "s1"})
    assert created.status_code == 201


def test_task_freezes_authorization_and_records_disposition(bot_runtime):
    store, executor = bot_runtime
    executor.set_agent_runner(lambda *_: fast_response())

    response = client.post(
        "/api/tasks",
        json={"query": "腾讯最近为什么下跌", "session_id": "s1"},
        headers={"Idempotency-Key": "key-authz"},
    )
    assert response.status_code == 201, response.text
    task = response.json()["task"]
    assert task["authorization"]["scope"]["query"] == "腾讯最近为什么下跌"
    assert task["authorization"]["scope"]["research_mode"] == "quick"
    assert task["authorization"]["owner_id"] == OWNER
    assert task["continued_from_task_id"] is None

    disposition = store.get_disposition(OWNER, "key-authz")
    assert disposition["disposition"] == "accepted"
    assert disposition["task_id"] == task["id"]
    wait_terminal(store, task["id"])


def test_follow_up_inherits_parent_authorization(bot_runtime):
    store, executor = bot_runtime
    executor.set_agent_runner(lambda *_: fast_response())

    parent = client.post(
        "/api/tasks", json={"query": "腾讯最近为什么下跌", "session_id": "s1"}, headers={"Idempotency-Key": "p1"}
    ).json()["task"]
    wait_terminal(store, parent["id"])

    follow = client.post(
        "/api/tasks",
        json={
            "query": "那机构在买还是卖",
            "session_id": "s1",
            "continued_from_task_id": parent["id"],
        },
        headers={"Idempotency-Key": "p2"},
    )
    assert follow.status_code == 201, follow.text
    child = follow.json()["task"]
    assert child["continued_from_task_id"] == parent["id"]
    assert child["authorization"]["inherited_from"] == parent["id"]
    assert child["authorization"]["budget"] == parent["authorization"]["budget"]
    assert child["authorization"]["scope"]["query"] == "那机构在买还是卖"

    mismatched = client.post(
        "/api/tasks",
        json={
            "query": "换个话题",
            "session_id": "s2",
            "continued_from_task_id": parent["id"],
        },
        headers={"Idempotency-Key": "p3"},
    )
    assert mismatched.status_code == 409
    wait_terminal(store, child["id"])


def test_local_admin_can_pause_and_resume_platform(bot_runtime, monkeypatch):
    store, executor = bot_runtime
    executor.set_agent_runner(lambda *_: fast_response())
    monkeypatch.setattr(
        routes,
        "resolve_owner",
        lambda request, config: OwnerContext(owner_id=OWNER, mode="local_single_user"),
    )

    paused = client.post("/api/platform/pause", json={"paused": True, "reason": "预算用完"})
    assert paused.status_code == 200
    assert paused.json()["state"]["paused"] is True
    assert paused.json()["state"]["pause_reason"] == "预算用完"

    state = client.get("/api/platform/state")
    assert state.json()["state"]["paused"] is True

    blocked = client.post("/api/tasks", json={"query": "研究腾讯", "session_id": "s1"})
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "platform_paused"

    resumed = client.post("/api/platform/pause", json={"paused": False})
    assert resumed.json()["state"]["paused"] is False


def test_hosted_owner_cannot_pause_platform(bot_runtime):
    response = client.post("/api/platform/pause", json={"paused": True})
    assert response.status_code == 403
    assert response.json()["detail"] == "platform_admin_local_only"


def test_dispositions_are_queryable(bot_runtime):
    store, executor = bot_runtime
    executor.set_agent_runner(lambda *_: fast_response())
    created = client.post(
        "/api/tasks",
        json={"query": "腾讯最近为什么下跌", "session_id": "s1"},
        headers={"Idempotency-Key": "disp-key"},
    )
    assert created.status_code == 201
    wait_terminal(store, created.json()["task"]["id"])

    response = client.get("/api/dispositions", params={"session_id": "s1"})
    assert response.status_code == 200
    rows = response.json()["dispositions"]
    assert len(rows) == 1
    assert rows[0]["input_ref"] == "disp-key"
    assert rows[0]["disposition"] == "accepted"
    assert rows[0]["reason_code"] == "research_started"


def test_chatgpt_subscription_cannot_use_shared_hosted_identity(bot_runtime):
    store, _ = bot_runtime
    response = client.post('/api/tasks', json={'query':'研究腾讯', 'session_id':'s1', 'model_provider':'chatgpt'})
    assert response.status_code == 403
    assert not store.list_tasks(OWNER)


def test_local_task_freezes_selected_model_provider(bot_runtime, monkeypatch):
    store, executor = bot_runtime
    monkeypatch.setattr(routes, 'resolve_owner', lambda request, config: OwnerContext(owner_id=OWNER, mode='local_single_user'))
    executor.set_agent_runner(lambda *_: fast_response())
    response = client.post('/api/tasks', json={'query':'研究腾讯', 'session_id':'s1', 'model_provider':'chatgpt'})
    assert response.status_code == 201
    assert response.json()['task']['authorization']['scope']['model_provider'] == 'chatgpt'


def test_chatgpt_status_does_not_expose_identity(bot_runtime, monkeypatch):
    monkeypatch.setattr(routes, 'resolve_owner', lambda request, config: OwnerContext(owner_id=OWNER, mode='local_single_user'))
    from api.financial_bot import chatgpt_session
    monkeypatch.setattr(chatgpt_session, 'is_signed_in', lambda: True)
    response = client.get('/api/model-providers')
    assert response.status_code == 200
    assert response.json()['chatgpt']['connected'] is True
    assert 'email' not in response.text
