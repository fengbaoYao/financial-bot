import json
import time

import pytest
from fastapi.testclient import TestClient

import api.financial_bot.routes as routes
from api.financial_bot.auth import OwnerContext
from api.financial_bot.db import Database
from api.financial_bot.executor import TaskExecutor
from api.financial_bot.memory_extract import extract_explicit_memories
from api.financial_bot.runtime import configure, reset
from api.financial_bot.store import ConflictError, FinancialBotStore, NotFoundError
from api.main import app


client = TestClient(app)
OWNER_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OWNER_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


@pytest.fixture()
def bot_runtime(tmp_path):
    db = Database(sqlite_path=tmp_path / "bot.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    configure(store=store, executor=TaskExecutor(store))
    yield store
    reset()


def use_owner(monkeypatch, owner_id: str):
    context = OwnerContext(owner_id=owner_id, mode="supabase_authenticated")
    monkeypatch.setattr(routes, "resolve_owner", lambda request, config: context)


def test_explicit_statements_are_recorded_but_questions_are_not():
    facts = extract_explicit_memories("我持有腾讯 100 股，成本约 300 港元。")
    assert facts[0]["kind"] == "holding"
    assert "100 股" in facts[0]["text"]
    assert facts[0]["source_text"].startswith("我持有")

    watch = extract_explicit_memories("我关注港股互联网和贵州茅台")
    assert watch and watch[0]["kind"] == "watch_object"

    tracking = extract_explicit_memories("帮我持续跟踪腾讯的财报变化")
    assert tracking and tracking[0]["kind"] == "research_question"

    assert extract_explicit_memories("腾讯最近怎么样？能买吗？") == []
    assert extract_explicit_memories("分析一下我的持仓") == []


def test_candidates_are_pending_and_never_enter_confirmed_context(bot_runtime):
    store = bot_runtime
    candidate = store.add_candidate(
        OWNER_A,
        task_id=None,
        proposed_text="腾讯的云业务利润率可能在未来两个季度继续改善。",
        rationale="基于已披露的分部收入趋势，仍存在会计口径不确定性。",
        source_text="原始回答节选",
        source_ref="task:t1",
    )
    assert candidate["status"] == "pending"
    assert store.build_memory_context(OWNER_A) == []
    assert store.list_memories(OWNER_A) == []
    assert [row["id"] for row in store.list_candidates(OWNER_A)] == [candidate["id"]]


def test_confirm_dismiss_and_version_conflicts(bot_runtime, monkeypatch):
    store = bot_runtime
    use_owner(monkeypatch, OWNER_A)
    candidate = store.add_candidate(
        OWNER_A,
        task_id=None,
        proposed_text="腾讯的游戏业务收入可能在下一财年恢复增长。",
        rationale="证据有限",
        source_text="回答节选",
        source_ref="task:t1",
    )
    conflict = client.post(
        f"/api/memory-candidates/{candidate['id']}/confirm",
        json={"expected_version": 99, "final_text": "腾讯游戏收入将增长", "idempotency_key": "c1"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["current_version"] == 1

    confirmed = client.post(
        f"/api/memory-candidates/{candidate['id']}/confirm",
        json={"expected_version": 1, "final_text": "我倾向于认为腾讯游戏收入将在下一财年恢复增长。", "idempotency_key": "c2"},
    )
    assert confirmed.status_code == 200, confirmed.text
    memory = confirmed.json()["memory"]
    assert memory["kind"] == "confirmed_viewpoint"
    assert memory["version"] == 1
    assert "我倾向于" in memory["text"]

    repeated = client.post(
        f"/api/memory-candidates/{candidate['id']}/confirm",
        json={"expected_version": 1, "final_text": "另一份文本", "idempotency_key": "c2"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["memory"]["id"] == memory["id"]

    second = client.post(
        f"/api/memory-candidates/{candidate['id']}/confirm",
        json={"expected_version": 1, "final_text": "并发覆盖文本", "idempotency_key": "c3"},
    )
    assert second.status_code == 409

    context = store.build_memory_context(OWNER_A)
    assert any("腾讯游戏收入" in line for line in context)
    assert all("腾讯游戏业务收入可能" not in line for line in context)


def test_dismiss_does_not_create_memory(bot_runtime, monkeypatch):
    store = bot_runtime
    use_owner(monkeypatch, OWNER_A)
    candidate = store.add_candidate(
        OWNER_A,
        task_id=None,
        proposed_text="茅台的估值可能在利率下行时获得支撑。",
        rationale="宏观不确定",
        source_text="回答节选",
        source_ref="task:t2",
    )
    response = client.post(
        f"/api/memory-candidates/{candidate['id']}/dismiss",
        json={"expected_version": 1, "idempotency_key": "d1"},
    )
    assert response.status_code == 200
    assert response.json()["candidate"]["status"] == "dismissed"
    assert store.list_memories(OWNER_A) == []
    assert store.list_candidates(OWNER_A) == []


def test_memory_correction_uses_optimistic_lock_and_keeps_versions(bot_runtime, monkeypatch):
    store = bot_runtime
    use_owner(monkeypatch, OWNER_A)
    memory = store.add_direct_memory(
        OWNER_A,
        kind="holding",
        text="持有腾讯 100 股，成本 300 港元",
        source_text="我持有腾讯 100 股，成本 300 港元",
        source_ref="task:t3",
    )
    first = client.patch(
        f"/api/memories/{memory['id']}",
        json={"expected_version": 1, "text": "持有腾讯 120 股，成本 300 港元"},
    )
    assert first.status_code == 200
    assert first.json()["memory"]["version"] == 2
    stale = client.patch(
        f"/api/memories/{memory['id']}",
        json={"expected_version": 1, "text": "持有腾讯 999 股"},
    )
    assert stale.status_code == 409
    versions = client.get(f"/api/memories/{memory['id']}/versions").json()["versions"]
    assert [row["version"] for row in versions] == [1, 2]
    assert "100 股" in versions[0]["text"] and "120 股" in versions[1]["text"]


def test_owner_isolation_for_memory_and_candidates(bot_runtime, monkeypatch):
    store = bot_runtime
    candidate = store.add_candidate(
        OWNER_A,
        task_id=None,
        proposed_text="A 的私有观点文本，不能对 B 可见。",
        rationale="私有",
        source_text="私有节选",
        source_ref="task:t4",
    )
    memory = store.add_direct_memory(
        OWNER_A,
        kind="preference",
        text="偏好低波动策略",
        source_text="我偏好低波动策略",
        source_ref="task:t4",
    )
    use_owner(monkeypatch, OWNER_B)
    assert client.get("/api/memories").json()["memories"] == []
    assert client.get("/api/memory-candidates").json()["candidates"] == []
    assert client.patch(
        f"/api/memories/{memory['id']}", json={"expected_version": 1, "text": "篡改"}
    ).status_code == 404
    assert client.post(
        f"/api/memory-candidates/{candidate['id']}/confirm",
        json={"expected_version": 1, "final_text": "篡改观点"},
    ).status_code == 404
    assert store.build_memory_context(OWNER_B) == []


def test_confirmed_memory_survives_store_restart(bot_runtime, monkeypatch, tmp_path):
    store = bot_runtime
    use_owner(monkeypatch, OWNER_A)
    candidate = store.add_candidate(
        OWNER_A,
        task_id=None,
        proposed_text="我倾向于认为高股息板块在利率下行期更具防御性。",
        rationale="示例",
        source_text="回答节选",
        source_ref="task:t5",
    )
    client.post(
        f"/api/memory-candidates/{candidate['id']}/confirm",
        json={"expected_version": 1, "final_text": "我倾向于认为高股息板块在利率下行期更具防御性。"},
    )
    reopened = FinancialBotStore(Database(sqlite_path=store.db.sqlite_path))
    assert any("高股息" in line for line in reopened.build_memory_context(OWNER_A))
    assert all("高股息" not in line for line in reopened.build_memory_context(OWNER_B))


def test_deleted_memory_cannot_be_reconfirmed_from_stale_candidate(bot_runtime, monkeypatch):
    store = bot_runtime
    use_owner(monkeypatch, OWNER_A)
    candidate = store.add_candidate(
        OWNER_A,
        task_id=None,
        proposed_text="我认为该公司的自由现金流将在明年改善。",
        rationale="示例",
        source_text="回答节选",
        source_ref="task:t6",
    )
    confirmed = client.post(
        f"/api/memory-candidates/{candidate['id']}/confirm",
        json={"expected_version": 1, "final_text": "我认为该公司的自由现金流将在明年改善。", "idempotency_key": "k1"},
    ).json()
    memory_id = confirmed["memory"]["id"]
    job = client.delete(f"/api/memories/{memory_id}?request_id=req-1").json()["deletion"]
    assert job["status"] == "blocked"
    with pytest.raises(NotFoundError):
        store.get_memory(OWNER_A, memory_id)
    again = client.post(
        f"/api/memory-candidates/{candidate['id']}/confirm",
        json={"expected_version": 1, "final_text": "重新确认", "idempotency_key": "k1"},
    )
    # Same idempotency key now refers to a deleted memory: the server must not
    # silently recreate a confirmed view from a stale candidate.
    assert again.status_code in {404, 409}


def test_model_viewpoint_proposals_are_pending_not_confirmed(bot_runtime):
    from api.agent_pydantic import AgentOutput, ViewpointProposal, build_agent_response
    from api.financial_bot.memory_extract import candidate_payloads

    output = AgentOutput(
        summary="基于已披露数据的示例回答 [ev_1]。",
        viewpoint_proposals=[
            ViewpointProposal(
                text="腾讯的广告收入可能在视频号商业化推动下继续增长。",
                rationale="分部收入趋势提供支持，但监管与宏观需求仍有不确定性。",
            )
        ],
    )
    response = build_agent_response(output, [], language="zh-CN", user_query="腾讯广告业务怎么看")
    proposals = candidate_payloads(response)
    assert len(proposals) == 1
    assert "视频号" in proposals[0]["text"]

    store = bot_runtime
    candidate = store.add_candidate(
        OWNER_A,
        task_id=None,
        proposed_text=proposals[0]["text"],
        rationale=proposals[0]["rationale"],
        source_text=response.summary[:300],
        source_ref="task:viewpoint",
    )
    assert candidate["status"] == "pending"
    assert store.list_memories(OWNER_A) == []
    assert store.build_memory_context(OWNER_A) == []


def test_concurrent_confirmation_creates_one_viewpoint(bot_runtime):
    from concurrent.futures import ThreadPoolExecutor

    store = bot_runtime
    candidate = store.add_candidate(
        OWNER_A,
        task_id=None,
        proposed_text="并发测试：高股息板块可能在利率下行期更具防御性。",
        rationale="fixture",
        source_text="fixture",
        source_ref="task:concurrent",
    )

    def confirm(index: int):
        try:
            _, memory = store.confirm_candidate(
                OWNER_A,
                candidate["id"],
                expected_version=1,
                final_text="高股息板块可能在利率下行期更具防御性。",
                idempotency_key=f"sqlite-concurrent-{index}",
            )
            return "ok"
        except ConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = sorted(executor.map(confirm, (1, 2)))
    assert results == ["conflict", "ok"]
    viewpoints = [row for row in store.list_memories(OWNER_A) if row["kind"] == "confirmed_viewpoint"]
    assert len(viewpoints) == 1
