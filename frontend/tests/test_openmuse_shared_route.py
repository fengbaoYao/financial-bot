import hashlib

from fastapi.testclient import TestClient

import api.financial_bot.routes as routes
from api.financial_bot import openmuse_tasks
from api.financial_bot.auth import LOCAL_MODE, LOCAL_OWNER_ID, OwnerContext
from api.financial_bot.db import Database
from api.financial_bot.executor import TaskExecutor
from api.financial_bot import runtime
from api.financial_bot.openmuse_task_coordinator import (
    RESEARCH_SANDBOX_CONTRACT,
    TASK_CREATE_CONTRACT,
    TASK_REVISION_CONTRACT,
    OpenMuseTaskCoordinator,
)
from api.financial_bot.store import FinancialBotStore
from api.main import app


def test_financial_classifier_recognizes_short_chinese_company_names():
    assert routes._is_financial_research_question("微软未来三年竞争格局如何") is True
    assert routes._is_financial_research_question("小米未来三年的竞争力如何") is True


def test_authenticated_explicit_shared_route_submits_and_reads_upstream_result(tmp_path, monkeypatch):
    db = Database(sqlite_path=tmp_path / "shared-route.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    executor = TaskExecutor(store)
    runtime.configure(store=store, executor=executor)
    bot = store.create_bot(LOCAL_OWNER_ID, name="Research")
    monkeypatch.setattr(routes, "resolve_owner", lambda request, config: OwnerContext(LOCAL_OWNER_ID, LOCAL_MODE))
    monkeypatch.setenv("OPENMUSE_ENABLED", "true")
    monkeypatch.setenv("OPENMUSE_BASE_URL", "http://127.0.0.1:8787")
    monkeypatch.setenv("OPENMUSE_ACCESS_KEY", "synthetic-test-key")
    monkeypatch.setenv("OPENMUSE_SHARED_RESEARCH_ENABLED", "true")

    class FakeOpenMuseClient:
        task = None

        def __init__(self, config, store=None):
            pass

        async def get_task_contracts(self, owner):
            return (TASK_CREATE_CONTRACT, RESEARCH_SANDBOX_CONTRACT, TASK_REVISION_CONTRACT)

        async def shared_research_enabled(self, owner):
            return True

        async def create_shared_research_task(self, owner, prompt, *, request_id, title=None, task_input=None):
            upstream_id = hashlib.sha256(f"task:{request_id}".encode()).hexdigest()
            task_input = task_input or {"research_scope": "general"}
            type(self).task = {
                "id": upstream_id,
                "title": title,
                "prompt": prompt,
                "kind": "research",
                "requestFingerprint": OpenMuseTaskCoordinator._request_fingerprint({
                    "kind": "research", "prompt": prompt, "title": title,
                    "input": task_input,
                }),
                "taskRevision": 1,
                "input": task_input,
                "status": "paused",
                "shared_research_workspace": True,
            }
            return dict(type(self).task)

        async def resume_research_task(self, owner, task_id):
            type(self).task.update(status="queued", taskRevision=2)
            return dict(type(self).task)

        async def answer_task(self, owner, task_id, answer):
            type(self).task.update(
                status="paused",
                question=None,
                taskRevision=type(self).task.get("taskRevision", 1) + 1,
                last_answer=answer,
            )
            return dict(type(self).task)

        async def get_task(self, owner, task_id):
            if type(self).task["status"] not in {"waiting_input", "failed", "cancelled"}:
                type(self).task.update(status="succeeded", taskRevision=3, result="Reviewed public documentation.")
            return {"task": dict(type(self).task), "events": []}

        async def close(self):
            pass

    monkeypatch.setattr(openmuse_tasks, "OpenMuseTasksClient", FakeOpenMuseClient)
    client = TestClient(app, client=("127.0.0.1", 43121))
    try:
        response = client.post("/api/tasks/openmuse-shared", json={
            "query": "Summarize public software documentation",
            "bot_id": bot["id"],
            "language": "en-US",
            "idempotency_key": "shared-route-request",
        })
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["task"]["research_mode"] == "openmuse_shared"
        assert body["task"]["authorization"]["data_sources"] is None
        assert body["coordination"]["state"] == "submitted"
        assert store.list_tasks(LOCAL_OWNER_ID)[0]["id"] == body["task"]["id"]

        financial = client.post("/api/tasks/openmuse-shared", json={
            "query": "研究腾讯股价和估值",
            "bot_id": bot["id"],
            "language": "zh-CN",
            "idempotency_key": "financial-query-shared-route",
        })
        assert financial.status_code == 503
        assert financial.json()["detail"] == "financial_research_bridge_unavailable"
        assert len(store.list_tasks(LOCAL_OWNER_ID)) == 1

        monkeypatch.setenv("FINANCIAL_RESEARCH_SHARED_TOKEN", "f" * 48)
        financial = client.post("/api/tasks/openmuse-shared", json={
            "query": "研究腾讯股价和估值",
            "bot_id": bot["id"],
            "language": "zh-CN",
            "idempotency_key": "financial-query-shared-route-enabled",
        })
        assert financial.status_code == 201, financial.text
        assert financial.json()["task"]["research_mode"] == "openmuse_shared"
        assert financial.json()["task"]["authorization"]["data_sources"] is None
        assert FakeOpenMuseClient.task["input"] == {
            "research_scope": "financial", "financial_bridge_required": True,
        }
        assert len(store.list_tasks(LOCAL_OWNER_ID)) == 2

        for index, query in enumerate((
            "600519",
            "Check 00700",
            "What is NVDA doing this week?",
            "Should I invest for the long term?",
            "长期持有指数基金合适吗",
            "帮我分析一下贵州茅台未来三年盈利能力",
            "帮我研究一下贵州茅台护城河和竞争格局",
            "宁德时代未来三年怎么看",
        )):
            accepted = client.post("/api/tasks/openmuse-shared", json={
                "query": query,
                "bot_id": bot["id"],
                "language": "en-US",
                "idempotency_key": f"financial-query-{index}",
            })
            assert accepted.status_code == 201, (query, accepted.text)
            assert accepted.json()["task"]["research_mode"] == "openmuse_shared"
            assert FakeOpenMuseClient.task["input"] == {
                "research_scope": "financial", "financial_bridge_required": True,
            }
        assert len(store.list_tasks(LOCAL_OWNER_ID)) == 10

        remote_client = TestClient(app, client=("192.0.2.10", 43122))
        remote = remote_client.post("/api/tasks/openmuse-shared", json={
            "query": "Summarize another public document",
            "bot_id": bot["id"],
            "language": "en-US",
            "idempotency_key": "remote-shared-route-request",
        })
        assert remote.status_code == 403
        assert len(store.list_tasks(LOCAL_OWNER_ID)) == 10
        remote_client.close()

        result = client.get(f"/api/tasks/{body['task']['id']}/openmuse-shared")
        assert result.status_code == 200, result.text
        assert result.json()["upstream_task"]["result"] == "Reviewed public documentation."
        assert result.json()["upstream_task"]["status"] == "succeeded"
        local_snapshot = client.get(f"/api/tasks/{body['task']['id']}")
        assert local_snapshot.status_code == 200
        assert local_snapshot.json()["task"]["status"] == "completed"
        assert local_snapshot.json()["task"]["authorization"]["data_sources"] is None
        assert local_snapshot.json()["task"]["result"] == {
            "summary": "Reviewed public documentation.",
            "intent": "openmuse_shared_research",
        }
        reopened_store = FinancialBotStore(Database(sqlite_path=tmp_path / "shared-route.sqlite3"))
        assert reopened_store.get_task(LOCAL_OWNER_ID, body["task"]["id"])["result"] == local_snapshot.json()["task"]["result"]

        # The upstream may ask a clarification during work. The detail route
        # exposes it, and the answer route submits it then resumes the task.
        FakeOpenMuseClient.task.update(status="waiting_input", question="Which time range?", taskRevision=4)
        waiting = client.get(f"/api/tasks/{body['task']['id']}/openmuse-shared")
        assert waiting.status_code == 200
        assert waiting.json()["upstream_task"]["status"] == "waiting_input"
        assert waiting.json()["upstream_task"]["question"] == "Which time range?"
        answered = client.post(
            f"/api/tasks/{body['task']['id']}/openmuse-shared/answer",
            json={"answer": "Cover the last five years."},
        )
        assert answered.status_code == 200, answered.text
        assert answered.json()["upstream_task"]["status"] == "queued"
        assert FakeOpenMuseClient.task["last_answer"] == "Cover the last five years."
    finally:
        client.close()
        runtime.reset()
