import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import api.financial_bot.routes as routes
from api.financial_bot.auth import OwnerContext
from api.financial_bot.executor import TaskExecutor
from api.financial_bot.runtime import configure, reset
from api.financial_bot.store import FinancialBotStore
from api.financial_bot.db import Database
from api.main import app
from api.schemas import AgentResponse, ResearchEvidence, ResearchStep


client = TestClient(app)


@pytest.fixture()
def bot_runtime(tmp_path):
    db = Database(sqlite_path=tmp_path / "bot.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    executor = TaskExecutor(store)
    configure(store=store, executor=executor)
    yield store, executor
    reset()


def use_owner(monkeypatch, owner_id: str):
    context = OwnerContext(owner_id=owner_id, mode="supabase_authenticated")
    monkeypatch.setattr(routes, "resolve_owner", lambda request, config: context)
    return context


def wait_terminal(store: FinancialBotStore, owner_id: str, task_id: str, timeout: float = 8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = store.get_task(owner_id, task_id)
        if task["status"] in {"completed", "partial", "failed", "cancelled", "waiting_user", "unknown"}:
            return task
        time.sleep(0.05)
    raise AssertionError(f"task did not finish: {store.get_task(owner_id, task_id)['status']}")


def fast_response(summary: str = "回答基于两项已检索来源。"):
    return AgentResponse(
        intent="pydantic_ai_agent",
        summary=summary,
        actions=["继续追问"],
        citations=["/api/stocks/sh600519/analysis"],
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
        data_gaps=["行情为延迟数据"],
        payload={"_meta": {"tools_used": ["get_stock_analysis"], "usage": {"input_tokens": 120, "output_tokens": 60}}},
    )


def test_task_events_are_monotonic_and_resumable(bot_runtime, monkeypatch):
    store, executor = bot_runtime
    owner = use_owner(monkeypatch, "11111111-1111-4111-8111-111111111111")

    async def runner(_owner, task, progress):
        progress("tool_started", 30, "正在检查行情", {"tool": "get_stock_analysis"})
        progress("tool_completed", 60, "行情已返回", {"tool": "get_stock_analysis"})
        progress("compose_response", 85, "正在整理回答")
        return fast_response()

    executor.set_agent_runner(runner)
    response = client.post(
        "/api/tasks",
        json={"query": "看看贵州茅台最近行情", "session_id": "s1"},
        headers={"Idempotency-Key": "key-1"},
    )
    assert response.status_code == 201, response.text
    task_id = response.json()["task"]["id"]
    task = wait_terminal(store, owner.owner_id, task_id)
    assert task["status"] == "completed"
    assert task["result"]["summary"].startswith("回答")

    events = store.list_events(owner.owner_id, task_id)
    sequences = [event["sequence"] for event in events]
    assert sequences == sorted(sequences)
    assert len(set(sequences)) == len(sequences)
    assert [event["kind"] for event in events][0] == "status"
    assert any(event["kind"] == "step" and event["step_id"] == "tool:get_stock_analysis" for event in events)
    assert events[-1]["kind"] == "status" and events[-1]["status"] == "completed"
    assert all("owner_id" not in event for event in events)
    latest_step_status: dict[str, str] = {}
    for event in events:
        if event["step_id"]:
            latest_step_status[event["step_id"]] = event["status"]
    assert latest_step_status
    assert all(status != "running" for status in latest_step_status.values())

    resumed = client.get(f"/api/tasks/{task_id}/events?after_sequence={sequences[2]}").json()
    assert [event["sequence"] for event in resumed["events"]] == sequences[3:]


def test_idempotency_returns_same_task(bot_runtime, monkeypatch):
    store, executor = bot_runtime
    owner = use_owner(monkeypatch, "11111111-1111-4111-8111-111111111111")

    async def runner(_owner, task, progress):
        await asyncio.sleep(0.2)
        return fast_response()

    executor.set_agent_runner(runner)
    first = client.post("/api/tasks", json={"query": "研究腾讯", "idempotency_key": "same-key"})
    second = client.post("/api/tasks", json={"query": "研究腾讯", "idempotency_key": "same-key"})
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert first.json()["task"]["id"] == second.json()["task"]["id"]
    assert len(store.list_tasks(owner.owner_id)) == 1


def test_terminal_state_cannot_regress_or_accept_late_events(bot_runtime, monkeypatch):
    store, executor = bot_runtime
    owner = use_owner(monkeypatch, "11111111-1111-4111-8111-111111111111")

    async def runner(_owner, task, progress):
        return fast_response()

    executor.set_agent_runner(runner)
    task_id = client.post("/api/tasks", json={"query": "研究腾讯"}).json()["task"]["id"]
    wait_terminal(store, owner.owner_id, task_id)
    assert store.transition(owner.owner_id, task_id, "running") is None
    assert store.append_event(owner.owner_id, task_id, kind="step", status="running", summary="迟到事件") is None
    assert store.get_task(owner.owner_id, task_id)["status"] == "completed"


def test_cancel_is_idempotent_and_reports_external_limit(bot_runtime, monkeypatch):
    store, executor = bot_runtime
    owner = use_owner(monkeypatch, "11111111-1111-4111-8111-111111111111")

    async def runner(_owner, task, progress):
        progress("tool_started", 30, "正在检索", {"tool": "web_search"})
        await asyncio.sleep(30)
        return fast_response()

    executor.set_agent_runner(runner)
    task_id = client.post("/api/tasks", json={"query": "研究腾讯"}).json()["task"]["id"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and store.get_task(owner.owner_id, task_id)["status"] != "running":
        time.sleep(0.05)
    response = client.post(f"/api/tasks/{task_id}/cancel")
    assert response.status_code == 200
    assert response.json()["cancel"]["requested"] is True
    assert "撤销" in response.json()["cancel"]["note"]
    task = wait_terminal(store, owner.owner_id, task_id)
    assert task["status"] == "cancelled"
    latest: dict[str, str] = {}
    for event in store.list_events(owner.owner_id, task_id):
        if event["step_id"]:
            latest[event["step_id"]] = event["status"]
    assert all(status != "running" for status in latest.values())
    repeat = client.post(f"/api/tasks/{task_id}/cancel")
    assert repeat.json()["cancel"]["already_terminal"] is True


def test_restart_recovery_marks_interrupted_tasks_unknown(bot_runtime, monkeypatch):
    store, _ = bot_runtime
    owner = use_owner(monkeypatch, "11111111-1111-4111-8111-111111111111")
    task, _ = store.create_task(
        owner_id=owner.owner_id,
        query="研究腾讯",
        session_id=None,
        language="zh-CN",
        research_mode="quick",
        idempotency_key="restart-1",
    )
    assert store.transition(owner.owner_id, task["id"], "running") is not None
    assert store.recover_interrupted_tasks() == 1
    recovered = store.get_task(owner.owner_id, task["id"])
    assert recovered["status"] == "unknown"
    events = store.list_events(owner.owner_id, task["id"])
    assert events[-1]["status"] == "unknown"
    assert "重启" in events[-1]["display_summary"]


def test_events_never_contain_credentials(bot_runtime, monkeypatch):
    store, executor = bot_runtime
    owner = use_owner(monkeypatch, "11111111-1111-4111-8111-111111111111")

    async def runner(_owner, task, progress):
        progress("tool_completed", 60, "来源已返回", {"tool": "web_search"})
        return fast_response("合成回答")

    executor.set_agent_runner(runner)
    task_id = client.post(
        "/api/tasks", json={"query": "请用测试查询研究腾讯"}
    ).json()["task"]["id"]
    wait_terminal(store, owner.owner_id, task_id)
    serialized = str(client.get(f"/api/tasks/{task_id}/events").json())
    assert "sk-" not in serialized
    assert "Bearer " not in serialized


def test_client_cannot_choose_owner_or_forge_ids(bot_runtime, monkeypatch):
    store, executor = bot_runtime
    use_owner(monkeypatch, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")

    async def runner(_owner, task, progress):
        return fast_response()

    executor.set_agent_runner(runner)
    rejected = client.post(
        "/api/tasks", json={"query": "研究腾讯", "owner_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"}
    )
    assert rejected.status_code == 422
    created = client.post("/api/tasks", json={"query": "研究腾讯"}).json()["task"]
    assert "owner_id" not in created and "idempotency_key" not in created


def test_unauthenticated_hosted_request_is_rejected(bot_runtime, monkeypatch):
    def deny(request, config):
        raise HTTPException(status_code=401, detail="需要 Supabase 登录令牌。")

    monkeypatch.setattr(routes, "resolve_owner", deny)
    assert client.get("/api/tasks").status_code == 401
    assert client.get("/api/memories").status_code == 401
    assert client.get("/api/bot/status").status_code == 401


def test_owner_resolution_ignores_client_headers_in_local_mode():
    from starlette.requests import Request
    from api.financial_bot.auth import LOCAL_OWNER_ID, resolve_owner
    from api.supabase_config import SupabaseConfig

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/tasks",
            "headers": [(b"x-owner-id", b"attacker"), (b"x-user-id", b"attacker")],
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1234),
        }
    )
    owner = resolve_owner(request, SupabaseConfig(url=None, anon_key=None, require_auth=False))
    assert owner.mode == "local_single_user"
    assert owner.owner_id == LOCAL_OWNER_ID


def test_hosted_mode_requires_a_verified_token():
    from starlette.requests import Request
    from api.financial_bot.auth import resolve_owner
    from api.supabase_config import SupabaseConfig

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/tasks",
            "headers": [(b"x-owner-id", b"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")],
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1234),
        }
    )
    config = SupabaseConfig(url="https://project.supabase.co", anon_key="publishable-test", require_auth=True)
    with pytest.raises(HTTPException) as excinfo:
        resolve_owner(request, config)
    assert excinfo.value.status_code == 401


def test_task_writes_pending_candidates_and_explicit_memory(bot_runtime, monkeypatch):
    store, executor = bot_runtime
    owner = use_owner(monkeypatch, "11111111-1111-4111-8111-111111111111")

    async def runner(_owner, task, progress):
        return AgentResponse(
            intent="pydantic_ai_agent",
            summary="示例回答，包含一个候选观点。",
            payload={
                "viewpoint_proposals": [
                    {
                        "text": "腾讯的广告收入可能继续受益于视频号商业化。",
                        "rationale": "基于已检索的分部趋势，监管仍是不确定性。",
                    }
                ]
            },
        )

    executor.set_agent_runner(runner)
    task_id = client.post("/api/tasks", json={"query": "我关注腾讯，帮我持续跟踪腾讯财报"}).json()["task"]["id"]
    wait_terminal(store, owner.owner_id, task_id)
    pending = store.list_candidates(owner.owner_id)
    assert len(pending) == 1
    assert pending[0]["status"] == "pending"
    assert "视频号" in pending[0]["proposed_text"]
    confirmed = store.list_memories(owner.owner_id)
    assert all(row["kind"] != "confirmed_viewpoint" for row in confirmed)
    kinds = {row["kind"] for row in confirmed}
    assert "watch_object" in kinds
    assert "research_question" in kinds


def test_sse_events_replay_and_finish_for_a_terminal_task(bot_runtime, monkeypatch):
    store, executor = bot_runtime
    owner = use_owner(monkeypatch, "11111111-1111-4111-8111-111111111111")

    async def runner(_owner, task, progress):
        progress("tool_completed", 60, "来源已返回", {"tool": "web_search"})
        return fast_response()

    executor.set_agent_runner(runner)
    task_id = client.post("/api/tasks", json={"query": "研究腾讯"}).json()["task"]["id"]
    wait_terminal(store, owner.owner_id, task_id)
    with client.stream(
        "GET",
        f"/api/tasks/{task_id}/events?after_sequence=0",
        headers={"Accept": "text/event-stream"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())
    assert "event: task_event" in body
    assert "event: done" in body
    assert '"status": "completed"' in body
    assert "sk-" not in body
