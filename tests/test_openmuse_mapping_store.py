import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from api.financial_bot.db import Database
from api.financial_bot.store import ConflictError, FinancialBotStore, NotFoundError


OWNER_A = "11111111-1111-4111-8111-111111111111"
OWNER_B = "22222222-2222-4222-8222-222222222222"


@pytest.fixture()
def store(tmp_path):
    db = Database(sqlite_path=tmp_path / "bot.sqlite3")
    db.initialize()
    return FinancialBotStore(db)


def create_local_task(store: FinancialBotStore, owner_id: str, key: str):
    task, created = store.create_task(
        owner_id=owner_id,
        query="private user prompt that must not be copied to the OpenMuse mapping",
        session_id=None,
        language="en-US",
        research_mode="quick",
        idempotency_key=key,
    )
    assert created
    return task


def payload_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def expected_request_id(owner_id: str, task_id: str) -> str:
    binding = hashlib.sha256(f"{owner_id}\0{task_id}".encode("utf-8")).hexdigest()
    return f"fb-{binding}"


def test_openmuse_mapping_binds_to_an_existing_owner_task_without_prompt_or_token(store):
    task = create_local_task(store, OWNER_A, "local-key-a")

    mapping, created = store.create_openmuse_task_mapping(
        OWNER_A,
        task["id"],
        payload_sha256=payload_hash("safe generic task payload"),
    )

    assert created is True
    assert mapping["owner_id"] == OWNER_A
    assert mapping["task_id"] == task["id"]
    assert mapping["request_id"] == expected_request_id(OWNER_A, task["id"])
    assert mapping["payload_sha256"] == payload_hash("safe generic task payload")
    assert mapping["upstream_task_id"] is None
    assert mapping["status"] == "prepared"
    assert mapping["deletion_status"] == "not_requested"
    assert "query" not in mapping
    assert "prompt" not in mapping
    assert "token" not in mapping
    assert "access_key" not in mapping


def test_openmuse_mapping_requires_a_task_owned_by_the_resolved_owner(store):
    task = create_local_task(store, OWNER_A, "local-key-owner")

    with pytest.raises(NotFoundError):
        store.create_openmuse_task_mapping(
            OWNER_B,
            task["id"],
            payload_sha256=payload_hash("payload"),
        )
    with pytest.raises(NotFoundError):
        store.create_openmuse_task_mapping(
            OWNER_A,
            "forged-task-id",
            payload_sha256=payload_hash("payload"),
        )

    assert store.get_openmuse_task_mapping(OWNER_B, task["id"]) is None
    assert store.get_openmuse_task_mapping(OWNER_A, task["id"]) is None


def test_upstream_projection_revision_is_monotonic_and_equal_revision_conflicts_fail_closed(store):
    task = create_local_task(store, OWNER_A, "projection-revision")
    store.create_openmuse_task_mapping(
        OWNER_A, task["id"], payload_sha256=payload_hash("projection payload")
    )
    store.update_openmuse_task_mapping(
        OWNER_A, task["id"], upstream_task_id="upstream-revision-task"
    )
    first_digest = payload_hash("revision 10 running")
    second_digest = payload_hash("revision 10 different payload")

    mapping, outcome = store.apply_openmuse_task_projection(
        OWNER_A, task["id"], revision=10, projection_sha256=first_digest, status="running"
    )
    assert outcome == "applied"
    assert mapping["status"] == "running"
    assert mapping["upstream_task_revision"] == 10
    assert mapping["upstream_projection_sha256"] == first_digest

    mapping, outcome = store.apply_openmuse_task_projection(
        OWNER_A, task["id"], revision=9, projection_sha256=payload_hash("old queued"), status="queued"
    )
    assert outcome == "stale"
    assert mapping["status"] == "running"
    assert mapping["upstream_task_revision"] == 10

    mapping, outcome = store.apply_openmuse_task_projection(
        OWNER_A, task["id"], revision=10, projection_sha256=first_digest, status="running"
    )
    assert outcome == "duplicate"

    mapping, outcome = store.apply_openmuse_task_projection(
        OWNER_A, task["id"], revision=10, projection_sha256=second_digest, status="queued"
    )
    assert outcome == "conflict"
    assert mapping["status"] == "running"

    store.update_openmuse_task_mapping(OWNER_A, task["id"], deletion_status="pending")
    mapping, outcome = store.apply_openmuse_task_projection(
        OWNER_A, task["id"], revision=11, projection_sha256=payload_hash("late delete race"), status="failed"
    )
    assert outcome == "deletion_blocked"
    assert mapping["status"] == "running"
    assert mapping["upstream_task_revision"] == 10


def test_upstream_revision_allows_control_state_transitions_without_status_ranking(store):
    task = create_local_task(store, OWNER_A, "projection-control-states")
    store.create_openmuse_task_mapping(
        OWNER_A, task["id"], payload_sha256=payload_hash("projection payload")
    )
    store.update_openmuse_task_mapping(
        OWNER_A, task["id"], upstream_task_id="upstream-control-task"
    )

    for revision, status in ((1, "running"), (2, "paused"), (3, "queued")):
        mapping, outcome = store.apply_openmuse_task_projection(
            OWNER_A,
            task["id"],
            revision=revision,
            projection_sha256=payload_hash(f"{revision}:{status}"),
            status=status,
        )
        assert outcome == "applied"
        assert mapping["status"] == status
        assert mapping["upstream_task_revision"] == revision


def test_late_openmuse_event_is_rejected_after_newer_projection(store):
    task = create_local_task(store, OWNER_A, "projection-event-order")
    store.create_openmuse_task_mapping(
        OWNER_A, task["id"], payload_sha256=payload_hash("projection payload")
    )
    store.update_openmuse_task_mapping(
        OWNER_A, task["id"], upstream_task_id="upstream-event-order-task"
    )
    for revision, status in ((10, "queued"), (11, "running")):
        store.apply_openmuse_task_projection(
            OWNER_A,
            task["id"],
            revision=revision,
            projection_sha256=payload_hash(f"{revision}:{status}"),
            status=status,
        )
    assert store.get_openmuse_task_mapping(OWNER_A, task["id"])["upstream_task_revision"] == 11

    stale = store.append_event(
        OWNER_A,
        task["id"],
        kind="openmuse_status",
        status="queued",
        summary="late status event",
        allow_after_terminal=True,
        expected_openmuse_revision=10,
    )
    fresh = store.append_event(
        OWNER_A,
        task["id"],
        kind="openmuse_status",
        status="running",
        summary="current status event",
        allow_after_terminal=True,
        expected_openmuse_revision=11,
    )

    assert stale is None
    assert fresh is not None
    events = store.list_events(OWNER_A, task["id"])
    assert [event["status"] for event in events if event["kind"] == "openmuse_status"] == ["running"]


def test_projection_and_revision_event_commit_together_and_replay_repairs_once(store, monkeypatch):
    task = create_local_task(store, OWNER_A, "projection-event-atomic")
    store.create_openmuse_task_mapping(
        OWNER_A, task["id"], payload_sha256=payload_hash("projection payload")
    )
    store.update_openmuse_task_mapping(
        OWNER_A, task["id"], upstream_task_id="upstream-atomic-event-task"
    )
    original = store.apply_openmuse_task_projection
    crashed = False

    def crash_after_commit(*args, **kwargs):
        nonlocal crashed
        result = original(*args, **kwargs)
        if not crashed and result[1] == "applied":
            crashed = True
            raise RuntimeError("simulated process loss after transaction commit")
        return result

    monkeypatch.setattr(store, "apply_openmuse_task_projection", crash_after_commit)
    kwargs = dict(
        revision=7,
        projection_sha256=payload_hash("revision 7 running"),
        status="running",
        event_summary="OpenMuse status: running; upstream events: 2",
        event_kind="openmuse_status",
    )
    with pytest.raises(RuntimeError, match="simulated process loss"):
        store.apply_openmuse_task_projection(OWNER_A, task["id"], **kwargs)
    monkeypatch.setattr(store, "apply_openmuse_task_projection", original)

    mapping, outcome = store.apply_openmuse_task_projection(OWNER_A, task["id"], **kwargs)

    assert outcome == "duplicate"
    assert mapping["upstream_task_revision"] == 7
    events = [
        event for event in store.list_events(OWNER_A, task["id"])
        if event["kind"] == "openmuse_status"
    ]
    assert len(events) == 1
    assert events[0]["status"] == "running"
    assert events[0]["display_summary"] == kwargs["event_summary"]


def test_openmuse_mapping_replay_is_idempotent_but_payload_mismatch_conflicts(store):
    task = create_local_task(store, OWNER_A, "local-key-idempotent")
    digest = payload_hash("same payload")
    first, created = store.create_openmuse_task_mapping(
        OWNER_A,
        task["id"],
        payload_sha256=digest,
    )

    replay, replay_created = store.create_openmuse_task_mapping(
        OWNER_A,
        task["id"],
        payload_sha256=digest,
    )

    assert created is True
    assert replay_created is False
    assert replay == first
    with pytest.raises(ConflictError, match="payload|idempotency|request"):
        store.create_openmuse_task_mapping(
            OWNER_A,
            task["id"],
            payload_sha256=payload_hash("changed payload"),
        )


def test_server_derives_a_distinct_upstream_request_id_for_each_local_task(store):
    first_task = create_local_task(store, OWNER_A, "local-key-first")
    second_task = create_local_task(store, OWNER_A, "local-key-second")
    store.create_openmuse_task_mapping(
        OWNER_A,
        first_task["id"],
        payload_sha256=payload_hash("payload"),
    )

    second, created = store.create_openmuse_task_mapping(
        OWNER_A,
        second_task["id"],
        payload_sha256=payload_hash("payload"),
    )
    assert created is True
    assert second["request_id"] != expected_request_id(OWNER_A, first_task["id"])


def test_mapping_rejects_non_sha256_hashes_and_upstream_id_rebinding(store):
    task = create_local_task(store, OWNER_A, "local-key-validation")
    with pytest.raises(ValueError, match="SHA-256"):
        store.create_openmuse_task_mapping(OWNER_A, task["id"], payload_sha256="not-a-digest")

    store.create_openmuse_task_mapping(
        OWNER_A,
        task["id"],
        payload_sha256=payload_hash("payload"),
    )
    store.update_openmuse_task_mapping(
        OWNER_A,
        task["id"],
        upstream_task_id="upstream-original",
        status="queued",
    )
    with pytest.raises(ConflictError, match="binding"):
        store.update_openmuse_task_mapping(
            OWNER_A,
            task["id"],
            upstream_task_id="upstream-replacement",
        )


def test_deletion_complete_mapping_cannot_receive_its_first_upstream_id(store):
    task = create_local_task(store, OWNER_A, "local-key-complete-no-upstream")
    store.create_openmuse_task_mapping(
        OWNER_A,
        task["id"],
        payload_sha256=payload_hash("payload"),
    )
    completed = store.update_openmuse_task_mapping(
        OWNER_A,
        task["id"],
        deletion_status="complete",
    )

    with pytest.raises(ConflictError, match="complete|immutable"):
        store.update_openmuse_task_mapping(
            OWNER_A,
            task["id"],
            upstream_task_id="late-upstream-task",
        )

    assert store.get_openmuse_task_mapping(OWNER_A, task["id"]) == completed


def test_deletion_complete_mapping_rejects_status_changes_but_allows_exact_replay(store):
    task = create_local_task(store, OWNER_A, "local-key-complete-status")
    store.create_openmuse_task_mapping(
        OWNER_A,
        task["id"],
        payload_sha256=payload_hash("payload"),
    )
    completed = store.update_openmuse_task_mapping(
        OWNER_A,
        task["id"],
        upstream_task_id="upstream-already-cleaned",
        status="cancelled",
        deletion_status="complete",
    )

    with pytest.raises(ConflictError, match="complete|immutable"):
        store.update_openmuse_task_mapping(
            OWNER_A,
            task["id"],
            upstream_task_id="replacement-upstream-task",
        )
    with pytest.raises(ConflictError, match="complete|immutable"):
        store.update_openmuse_task_mapping(OWNER_A, task["id"], status="running")

    replay = store.update_openmuse_task_mapping(
        OWNER_A,
        task["id"],
        upstream_task_id="upstream-already-cleaned",
        status="cancelled",
        deletion_status="complete",
    )
    assert replay == completed


def test_postgres_mapping_methods_fail_closed_before_reading_control_table():
    class DatabaseSpy:
        dialect = "postgres"

        def __init__(self):
            self.connect_calls = 0

        def connect(self, **_kwargs):
            self.connect_calls += 1
            raise AssertionError("Postgres mapping control table must not be accessed")

    db = DatabaseSpy()
    store = FinancialBotStore(db)
    operations = [
        lambda: store.create_openmuse_task_mapping(
            OWNER_A,
            "task-test",
            payload_sha256=payload_hash("payload"),
        ),
        lambda: store.get_openmuse_task_mapping(OWNER_A, "task-test"),
        lambda: store.update_openmuse_task_mapping(OWNER_A, "task-test", status="running"),
        lambda: store.list_openmuse_task_mappings(OWNER_A),
    ]

    for operation in operations:
        with pytest.raises(RuntimeError, match="PostgreSQL.*server-only") as raised:
            operation()
        assert type(raised.value).__name__ == "OpenMuseMappingUnavailableError"

    assert db.connect_calls == 0


def test_postgres_mapping_migration_does_not_grant_authenticated_table_access():
    migration = (
        Path(__file__).resolve().parents[1]
        / "supabase"
        / "migrations"
        / "0020_openmuse_task_mappings.sql"
    ).read_text()
    mapping_grants = re.findall(
        r"grant\s+[^;]+\s+on\s+public\.fb_openmuse_task_mappings\s+to\s+([^;]+);",
        migration,
        flags=re.IGNORECASE,
    )

    assert mapping_grants == []
    assert re.search(
        r"revoke\s+all\s+on\s+table\s+public\.fb_openmuse_task_mappings\s+from\s+public,\s*anon,\s*authenticated",
        migration,
        flags=re.IGNORECASE,
    )
    assert "server-only" in migration


def test_concurrent_mapping_update_cannot_mutate_after_deletion_complete(store):
    task = create_local_task(store, OWNER_A, "local-key-complete-concurrent")
    store.create_openmuse_task_mapping(
        OWNER_A,
        task["id"],
        payload_sha256=payload_hash("payload"),
    )
    completed_write = Event()

    def mark_complete():
        try:
            return store.update_openmuse_task_mapping(
                OWNER_A,
                task["id"],
                deletion_status="complete",
            )
        finally:
            completed_write.set()

    def late_upstream_update():
        assert completed_write.wait(timeout=5)
        return store.update_openmuse_task_mapping(
            OWNER_A,
            task["id"],
            upstream_task_id="concurrent-late-task",
            status="running",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        complete_result = pool.submit(mark_complete)
        late_result = pool.submit(late_upstream_update)
        complete_result.result(timeout=5)
        with pytest.raises(ConflictError, match="complete|immutable"):
            late_result.result(timeout=5)

    saved = store.get_openmuse_task_mapping(OWNER_A, task["id"])
    assert saved["deletion_status"] == "complete"
    assert saved["upstream_task_id"] is None
    assert saved["status"] == "prepared"


def test_openmuse_upstream_binding_and_deletion_pending_survive_store_reopen(store, tmp_path):
    task = create_local_task(store, OWNER_A, "local-key-recovery")
    store.create_openmuse_task_mapping(
        OWNER_A,
        task["id"],
        payload_sha256=payload_hash("payload"),
    )

    updated = store.update_openmuse_task_mapping(
        OWNER_A,
        task["id"],
        upstream_task_id="upstream-7",
        status="running",
        deletion_status="pending",
    )

    assert updated["upstream_task_id"] == "upstream-7"
    assert updated["status"] == "running"
    assert updated["deletion_status"] == "pending"
    pending = store.list_openmuse_task_mappings(OWNER_A, deletion_status="pending")
    assert [entry["task_id"] for entry in pending] == [task["id"]]

    sqlite_path = store.db.sqlite_path
    assert sqlite_path == tmp_path / "bot.sqlite3"
    reopened_db = Database(sqlite_path=sqlite_path)
    reopened_db.initialize()
    reopened = FinancialBotStore(reopened_db).get_openmuse_task_mapping(OWNER_A, task["id"])
    assert reopened == updated


def test_session_delete_persists_upstream_intent_and_preserves_local_records(store):
    task, created = store.create_task(
        owner_id=OWNER_A,
        query="delete with the session",
        session_id="openmuse-delete-session",
        language="en-US",
        research_mode="quick",
        idempotency_key="local-key-delete-session",
    )
    assert created
    bot = store.ensure_default_bot(OWNER_A)
    store.ensure_bot_session(OWNER_A, bot["id"], "openmuse-delete-session")
    store.ensure_main_thread(OWNER_A, bot["id"], "openmuse-delete-session", "保留会话")
    store.append_message(
        OWNER_A,
        session_id="openmuse-delete-session",
        role="user",
        content="保留本地消息直到上游确认",
    )
    store.create_openmuse_task_mapping(
        OWNER_A,
        task["id"],
        payload_sha256=payload_hash("payload"),
    )
    store.update_openmuse_task_mapping(
        OWNER_A,
        task["id"],
        upstream_task_id="upstream-delete-pending",
    )

    result = store.delete_session(OWNER_A, "openmuse-delete-session")

    mapping = store.get_openmuse_task_mapping(OWNER_A, task["id"])
    assert result["status"] == "pending"
    assert result["pending_openmuse_task_ids"] == [task["id"]]
    assert store.get_task(OWNER_A, task["id"]) is not None
    assert any(
        session["id"] == "openmuse-delete-session"
        for session in store.list_sessions(OWNER_A, bot_id=bot["id"])
    )
    assert store.get_thread(OWNER_A, "openmuse-delete-session") is not None
    assert store.list_messages(OWNER_A, "openmuse-delete-session")
    with pytest.raises(ConflictError, match="session_delete_in_progress"):
        store.create_task(
            owner_id=OWNER_A,
            query="do not add work while upstream deletion is pending",
            session_id="openmuse-delete-session",
            language="en-US",
            research_mode="quick",
            idempotency_key="create-during-pending-delete",
        )
    with pytest.raises(ConflictError, match="session_delete_in_progress"):
        store.create_openmuse_task_mapping(
            OWNER_A,
            task["id"],
            payload_sha256=payload_hash("payload"),
        )
    assert mapping is not None
    assert mapping["upstream_task_id"] == "upstream-delete-pending"
    assert mapping["deletion_status"] == "pending"
    assert store.list_openmuse_task_mappings(OWNER_A, deletion_status="pending") == [mapping]


def test_session_finalizes_only_after_every_upstream_mapping_is_complete(store):
    bot = store.ensure_default_bot(OWNER_A)
    session_id = "openmuse-delete-multi"
    store.ensure_bot_session(OWNER_A, bot["id"], session_id)
    store.ensure_main_thread(OWNER_A, bot["id"], session_id, "多任务会话")
    store.append_message(OWNER_A, session_id=session_id, role="user", content="会话内容")
    task_ids = []
    for index in range(2):
        task, created = store.create_task(
            owner_id=OWNER_A,
            query=f"mapped task {index}",
            session_id=session_id,
            language="en-US",
            research_mode="quick",
            idempotency_key=f"delete-multi-{index}",
        )
        assert created
        task_ids.append(task["id"])
        store.create_openmuse_task_mapping(
            OWNER_A,
            task["id"],
            payload_sha256=payload_hash(f"payload-{index}"),
        )
        store.update_openmuse_task_mapping(
            OWNER_A,
            task["id"],
            upstream_task_id=f"upstream-multi-{index}",
        )

    assert store.delete_session(OWNER_A, session_id)["status"] == "pending"
    store.update_openmuse_task_mapping(OWNER_A, task_ids[0], deletion_status="complete", deletion_error="")
    assert store.finalize_pending_session_deletions(OWNER_A) == {"sessions": 0, "tasks": 0, "messages": 0}
    assert store.get_task(OWNER_A, task_ids[0]) is not None
    assert store.get_thread(OWNER_A, session_id) is not None

    store.update_openmuse_task_mapping(OWNER_A, task_ids[1], deletion_status="complete", deletion_error="")
    assert store.finalize_pending_session_deletions(OWNER_A) == {"sessions": 1, "tasks": 2, "messages": 1}
    assert store.list_tasks(OWNER_A, session_id=session_id) == []
    assert store.get_thread(OWNER_A, session_id) is None
    assert store.list_messages(OWNER_A, session_id) == []
    assert all(store.get_openmuse_task_mapping(OWNER_A, task_id)["deletion_status"] == "complete" for task_id in task_ids)


def test_clear_bot_sessions_reports_pending_without_counting_mapping_session_as_deleted(store):
    bot = store.ensure_default_bot(OWNER_A)
    store.ensure_bot_session(OWNER_A, bot["id"], "one")
    store.ensure_bot_session(OWNER_A, bot["id"], "two")
    task, created = store.create_task(
        owner_id=OWNER_A,
        query="keep pending session",
        session_id="one",
        language="en-US",
        research_mode="quick",
        idempotency_key="clear-pending-session",
    )
    assert created
    store.create_openmuse_task_mapping(
        OWNER_A,
        task["id"],
        payload_sha256=payload_hash("clear-pending"),
    )
    store.update_openmuse_task_mapping(
        OWNER_A,
        task["id"],
        upstream_task_id="upstream-clear-pending",
    )

    result = store.clear_bot_sessions(OWNER_A, bot["id"])

    assert result == {
        "sessions": 1,
        "tasks": 0,
        "messages": 0,
        "status": "pending",
        "pending_sessions": ["one"],
        "pending_openmuse_task_ids": [task["id"]],
    }
    assert store.get_task(OWNER_A, task["id"]) is not None
    remaining = {session["id"] for session in store.list_sessions(OWNER_A, bot_id=bot["id"])}
    assert remaining == {"one"}


def test_mapping_deletion_reason_only_accepts_stable_codes(store):
    task = create_local_task(store, OWNER_A, "safe-delete-reason")
    store.create_openmuse_task_mapping(
        OWNER_A,
        task["id"],
        payload_sha256=payload_hash("payload"),
    )

    with pytest.raises(ValueError, match="error code"):
        store.update_openmuse_task_mapping(
            OWNER_A,
            task["id"],
            deletion_status="pending",
            deletion_error="https://example.test/private?token=secret",
        )


def test_sqlite_upgrade_adds_reconciliation_lease_columns(tmp_path):
    import sqlite3

    db_path = tmp_path / "old-openmuse-mapping.sqlite3"
    raw = sqlite3.connect(db_path)
    raw.execute(
        """CREATE TABLE fb_openmuse_task_mappings (
               owner_id TEXT NOT NULL,
               task_id TEXT NOT NULL,
               request_id TEXT NOT NULL,
               payload_sha256 TEXT NOT NULL,
               upstream_task_id TEXT,
               status TEXT NOT NULL DEFAULT 'prepared',
               deletion_status TEXT NOT NULL DEFAULT 'not_requested',
               deletion_error TEXT NOT NULL DEFAULT '',
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL,
               PRIMARY KEY (owner_id, task_id),
               UNIQUE (owner_id, request_id),
               UNIQUE (owner_id, upstream_task_id)
           )"""
    )
    raw.commit()
    raw.close()

    db = Database(sqlite_path=db_path)
    db.initialize()

    with db.connect(owner_id=None, bypass_rls=True) as conn:
        columns = {
            row["name"] for row in conn.fetchall("PRAGMA table_info(fb_openmuse_task_mappings)")
        }
    assert "reconcile_lease_id" in columns
    assert "reconcile_lease_expires_at" in columns
