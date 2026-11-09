import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.financial_bot.routes as routes
from api.financial_bot.auth import OwnerContext
from api.financial_bot.db import Database
from api.financial_bot.executor import TaskExecutor
from api.financial_bot.runtime import configure, reset
from api.financial_bot.store import FinancialBotStore
from api.main import app


client = TestClient(app)
OWNER_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OWNER_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def build_store(tmp_path, *, backup_dir=None, verified=False) -> FinancialBotStore:
    db = Database(sqlite_path=tmp_path / "bot.sqlite3")
    db.initialize()
    store = FinancialBotStore(
        db, backup_dir=backup_dir, third_party_retention_verified=verified
    )
    configure(store=store, executor=TaskExecutor(store))
    return store


def use_owner(monkeypatch, owner_id: str):
    context = OwnerContext(owner_id=owner_id, mode="supabase_authenticated")
    monkeypatch.setattr(routes, "resolve_owner", lambda request, config: context)


@pytest.fixture()
def bot_runtime(tmp_path):
    store = build_store(tmp_path)
    yield store
    reset()


def seed(store: FinancialBotStore):
    first = store.add_direct_memory(
        OWNER_A,
        kind="holding",
        text="持有腾讯 100 股，成本 300 港元",
        source_text="我持有腾讯 100 股，成本 300 港元",
        source_ref="task:t1",
    )
    second = store.add_direct_memory(
        OWNER_A,
        kind="preference",
        text="偏好低波动策略",
        source_text="我偏好低波动策略",
        source_ref="task:t1",
    )
    store.patch_memory(
        OWNER_A, first["id"], expected_version=1, text="持有腾讯 120 股，成本 300 港元"
    )
    candidate = store.add_candidate(
        OWNER_A,
        task_id=None,
        proposed_text="腾讯的广告业务可能受益于视频号商业化。",
        rationale="示例",
        source_text="回答原始节选",
        source_ref="task:t1",
    )
    store.confirm_candidate(
        OWNER_A,
        candidate["id"],
        expected_version=1,
        final_text="我倾向于认为腾讯广告业务将受益于视频号商业化。",
        idempotency_key="seed-confirm",
    )
    other = store.add_direct_memory(
        OWNER_B,
        kind="watch_object",
        text="腾讯",
        source_text="我关注腾讯",
        source_ref="task:b1",
    )
    return first, second, candidate, other


def table_counts(store: FinancialBotStore, owner_id: str):
    with store.db.connect(owner_id=owner_id) as conn:
        return {
            "memories": conn.fetchone(
                "SELECT COUNT(*) AS c FROM fb_memories WHERE owner_id = ?", (owner_id,)
            )["c"],
            "versions": conn.fetchone(
                "SELECT COUNT(*) AS c FROM fb_memory_versions WHERE owner_id = ?", (owner_id,)
            )["c"],
            "candidates": conn.fetchone(
                "SELECT COUNT(*) AS c FROM fb_memory_candidates WHERE owner_id = ?", (owner_id,)
            )["c"],
            "index": conn.fetchone(
                "SELECT COUNT(*) AS c FROM fb_memory_index WHERE owner_id = ?", (owner_id,)
            )["c"],
        }


def test_single_deletion_clears_selected_scope_and_keeps_other_data(bot_runtime, monkeypatch):
    store = bot_runtime
    use_owner(monkeypatch, OWNER_A)
    first, second, candidate, other = seed(store)
    store.build_memory_context(OWNER_A)
    assert store.cache.get(OWNER_A) is not None

    job = client.delete(f"/api/memories/{first['id']}?request_id=del-single").json()["deletion"]
    assert job["scope"] == "single"
    assert job["status"] == "blocked"  # third-party retention is unverified
    assert job["layers"]["memory_rows"]["deleted"] == 1
    assert job["layers"]["memory_versions"]["deleted"] == 2
    assert job["layers"]["memory_candidates"]["deleted"] == 0
    assert job["layers"]["third_party_model"]["status"] == "unverified"
    assert job["layers"]["backup"]["status"] == "not_configured"
    assert store.cache.get(OWNER_A) is None

    remaining = store.list_memories(OWNER_A)
    assert second["id"] in [row["id"] for row in remaining]
    assert first["id"] not in [row["id"] for row in remaining]
    viewpoint = next(row for row in remaining if row["kind"] == "confirmed_viewpoint")
    assert any("视频号" in row["text"] for row in remaining)
    assert store.list_candidates(OWNER_A) == []
    assert any("低波动" in line for line in store.build_memory_context(OWNER_A))
    assert all("持有腾讯 120 股" not in line for line in store.build_memory_context(OWNER_A))

    # Deleting the confirmed viewpoint also removes the candidate that derived
    # it, while the unrelated preference memory stays.
    second_job = client.delete(f"/api/memories/{viewpoint['id']}?request_id=del-viewpoint").json()["deletion"]
    assert second_job["layers"]["memory_candidates"]["deleted"] == 1
    assert [row["id"] for row in store.list_memories(OWNER_A)] == [second["id"]]

    # Other users and their independent records are untouched.
    assert [row["id"] for row in store.list_memories(OWNER_B)] == [other["id"]]
    assert table_counts(store, OWNER_B)["memories"] == 1

    audit = store.audit_entries(OWNER_A)
    serialized = json.dumps(audit, ensure_ascii=False)
    assert "腾讯" not in serialized and "低波动" not in serialized
    assert any(entry["action"] == "memory.purged" for entry in audit)


def test_clear_all_removes_pending_candidates_but_not_conversations(bot_runtime, monkeypatch):
    store = bot_runtime
    use_owner(monkeypatch, OWNER_A)
    seed(store)
    store.add_candidate(
        OWNER_A,
        task_id=None,
        proposed_text="未确认的候选观点也必须随清空全部一起删除。",
        rationale="示例",
        source_text="候选原文",
        source_ref="task:t9",
    )
    task, _ = store.create_task(
        owner_id=OWNER_A,
        query="这是一段独立会话记录",
        session_id="s1",
        language="zh-CN",
        research_mode="quick",
        idempotency_key="conversation-1",
    )
    job = client.request(
        "DELETE", "/api/memories", json={"confirm": "delete_all", "request_id": "del-all"}
    ).json()["deletion"]
    assert job["scope"] == "all"
    assert job["status"] == "blocked"
    assert table_counts(store, OWNER_A) == {
        "memories": 0,
        "versions": 0,
        "candidates": 0,
        "index": 0,
    }
    assert store.build_memory_context(OWNER_A) == []
    # The independent conversation is not part of the selected deletion scope.
    assert store.get_task(OWNER_A, task["id"])["query"] == "这是一段独立会话记录"
    # B is unaffected.
    assert table_counts(store, OWNER_B)["memories"] == 1


def test_deletion_request_is_idempotent(bot_runtime, monkeypatch):
    store = bot_runtime
    use_owner(monkeypatch, OWNER_A)
    memory = store.add_direct_memory(
        OWNER_A,
        kind="preference",
        text="偏好 A 股大盘股",
        source_text="我偏好 A 股大盘股",
        source_ref="task:t1",
    )
    first = client.delete(f"/api/memories/{memory['id']}?request_id=same-request").json()["deletion"]
    second = client.delete(f"/api/memories/{memory['id']}?request_id=same-request").json()["deletion"]
    assert first["id"] == second["id"]
    assert second["status"] == first["status"]


def test_backup_and_third_party_layers_gate_completion(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    (backup_dir / "snapshot-1.db").write_bytes(b"backup")
    store = build_store(tmp_path, backup_dir=backup_dir, verified=False)
    use_owner(monkeypatch, OWNER_A)
    memory = store.add_direct_memory(
        OWNER_A,
        kind="watch_object",
        text="腾讯",
        source_text="我关注腾讯",
        source_ref="task:t1",
    )
    job = client.delete(f"/api/memories/{memory['id']}?request_id=r1").json()["deletion"]
    assert job["layers"]["backup"]["status"] == "unverified"
    assert job["status"] == "blocked"
    assert "备份" in job["detail"] and "模型供应商保留" in job["detail"]

    # A fresh store with an empty backup directory and operator-verified
    # provider retention is the only configuration that can complete.
    empty_backup = tmp_path / "empty-backups"
    empty_backup.mkdir()
    reset()
    verified_store = build_store(tmp_path, backup_dir=empty_backup, verified=True)
    memory = verified_store.add_direct_memory(
        OWNER_A,
        kind="watch_object",
        text="贵州茅台",
        source_text="我关注贵州茅台",
        source_ref="task:t2",
    )
    job = verified_store.request_deletion(
        OWNER_A, scope="single", target_ids=[memory["id"]], request_id="r2"
    )
    assert job["layers"]["backup"]["status"] == "verified"
    assert job["layers"]["third_party_model"]["status"] == "verified"
    assert job["status"] == "completed"


def test_deleting_state_blocks_retrieval_immediately(bot_runtime):
    store = bot_runtime
    memory = store.add_direct_memory(
        OWNER_A,
        kind="holding",
        text="持有腾讯 100 股",
        source_text="我持有腾讯 100 股",
        source_ref="task:t1",
    )
    store.build_memory_context(OWNER_A)
    with store.db.connect(owner_id=OWNER_A, write=True) as conn:
        conn.execute(
            "UPDATE fb_memories SET deletion_state = 'deleting' WHERE owner_id = ? AND id = ?",
            (OWNER_A, memory["id"]),
        )
    store.cache.invalidate(OWNER_A)
    assert store.build_memory_context(OWNER_A) == []
    assert store.list_memories(OWNER_A) == []


def test_deleted_text_is_not_recoverable_from_sqlite_files(tmp_path):
    store = build_store(tmp_path)
    marker = "UNIQUE-DELETE-MARKER-998877"
    memory = store.add_direct_memory(
        OWNER_A,
        kind="research_question",
        text=f"{marker} 的跟踪问题",
        source_text=f"我想持续研究 {marker}",
        source_ref="task:t1",
    )
    job = store.request_deletion(
        OWNER_A, scope="single", target_ids=[memory["id"]], request_id="physical-1"
    )
    assert job["layers"]["physical_storage"]["status"] == "cleared"
    database = store.db.sqlite_path
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(database) + suffix)
        if path.exists():
            assert marker.encode("utf-8") not in path.read_bytes()
    reopened = FinancialBotStore(Database(sqlite_path=database))
    assert reopened.list_memories(OWNER_A) == []
    with pytest.raises(Exception):
        reopened.get_memory(OWNER_A, memory["id"])
