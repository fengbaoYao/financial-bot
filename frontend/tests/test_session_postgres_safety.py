"""Optional integration checks for an isolated, disposable PostgreSQL test DB.

Set FINANCIAL_BOT_TEST_POSTGRES_URL only for a loopback test cluster after
applying the Financial Bot migrations. Production URLs are deliberately
rejected so this file cannot mutate a user database by accident.
"""

import hashlib
import os
import threading
import uuid
from urllib.parse import urlsplit

import pytest

from api.financial_bot.db import Database
from api.financial_bot.store import ConflictError, FinancialBotStore


DSN = os.environ.get("FINANCIAL_BOT_TEST_POSTGRES_URL", "")
parsed_dsn = urlsplit(DSN) if DSN else None
if DSN and (parsed_dsn.hostname not in {"127.0.0.1", "::1", "localhost"} or int(parsed_dsn.port or 0) < 1024):
    raise RuntimeError("FINANCIAL_BOT_TEST_POSTGRES_URL must target a loopback test database on a high port")

pytestmark = pytest.mark.skipif(not DSN, reason="requires an isolated loopback PostgreSQL test cluster")


@pytest.fixture
def postgres_store():
    db = Database(database_url=DSN)
    db.initialize()
    store = FinancialBotStore(db)
    owner = str(uuid.uuid4())
    yield db, store, owner
    with db.connect(owner_id=None, bypass_rls=True, write=True) as conn:
        for table in (
            "fb_openmuse_task_mappings",
            "fb_session_lifecycle",
            "fb_task_events",
            "fb_task_plans",
            "fb_tasks",
            "fb_messages",
            "fb_threads",
            "fb_bot_sessions",
            "fb_bots",
        ):
            conn.execute(f"DELETE FROM public.{table} WHERE owner_id = ?", (owner,))


def create_session_task(store, owner, session_id):
    bot = store.ensure_default_bot(owner)
    store.ensure_bot_session(owner, bot["id"], session_id)
    task, created = store.create_task(
        owner_id=owner,
        query="postgres session fence fixture",
        session_id=session_id,
        language="en-US",
        research_mode="quick",
        idempotency_key=f"postgres-{session_id}",
    )
    assert created
    return task


def test_postgres_restricted_function_marks_only_pending_ids_and_rejects_forged_owner(postgres_store):
    db, store, owner = postgres_store
    session_id = f"session-{uuid.uuid4().hex}"
    task = create_session_task(store, owner, session_id)
    request_id = "fb-" + hashlib.sha256(f"{owner}\0{task['id']}".encode()).hexdigest()
    with db.connect(owner_id=None, bypass_rls=True, write=True) as conn:
        conn.execute(
            """INSERT INTO public.fb_openmuse_task_mappings
               (owner_id, task_id, request_id, payload_sha256, upstream_task_id, status,
                deletion_status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'running', 'not_requested', now()::text, now()::text)""",
            (owner, task["id"], request_id, hashlib.sha256(b"safe").hexdigest(), "upstream-1"),
        )
        assert not conn.fetchone(
            "SELECT has_table_privilege('authenticated', 'public.fb_openmuse_task_mappings', 'SELECT') AS allowed"
        )["allowed"]
        assert not conn.fetchone(
            "SELECT has_table_privilege('authenticated', 'public.fb_session_lifecycle', 'SELECT') AS allowed"
        )["allowed"]
        assert not conn.fetchone(
            "SELECT has_function_privilege('anon', 'public.fb_mark_session_openmuse_delete_pending(text,text)', 'EXECUTE') AS allowed"
        )["allowed"]
        assert conn.fetchone(
            "SELECT has_function_privilege('authenticated', 'public.fb_mark_session_openmuse_delete_pending(text,text)', 'EXECUTE') AS allowed"
        )["allowed"]

    with pytest.raises(Exception):
        with db.connect(owner_id=owner, write=True) as conn:
            conn.fetchone(
                "SELECT public.fb_mark_session_openmuse_delete_pending(?, ?) AS task_id",
                (str(uuid.uuid4()), session_id),
            )
    with db.connect(owner_id=owner) as conn:
        assert conn.fetchone(
            "SELECT public.fb_ensure_session_lifecycle(?, ?) AS status",
            (owner, session_id),
        )["status"] == "active"

    result = store.delete_session(owner, session_id)
    assert result["status"] == "pending"
    assert result["pending_openmuse_task_ids"] == [task["id"]]
    assert store.get_task(owner, task["id"])["id"] == task["id"]
    with db.connect(owner_id=None, bypass_rls=True) as conn:
        mapping = conn.fetchone(
            "SELECT deletion_status FROM public.fb_openmuse_task_mappings WHERE owner_id = ? AND task_id = ?",
            (owner, task["id"]),
        )
    assert mapping["deletion_status"] == "pending"


def test_postgres_concurrent_session_delete_and_task_create_leave_no_orphan(postgres_store):
    _db, store, owner = postgres_store
    session_id = f"session-{uuid.uuid4().hex}"
    bot = store.ensure_default_bot(owner)
    store.ensure_bot_session(owner, bot["id"], session_id)
    start = threading.Barrier(2)
    outcomes = []

    def create_task():
        start.wait(timeout=5)
        try:
            store.create_task(
                owner_id=owner,
                query="concurrent task",
                session_id=session_id,
                language="en-US",
                research_mode="quick",
                idempotency_key=f"race-{session_id}",
            )
            outcomes.append("created")
        except ConflictError:
            outcomes.append("blocked")

    def delete_session():
        start.wait(timeout=5)
        result = store.delete_session(owner, session_id)
        outcomes.append(result.get("status", "deleted"))

    threads = [threading.Thread(target=create_task), threading.Thread(target=delete_session)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert "deleted" in outcomes
    assert store.list_tasks(owner, session_id=session_id) == []
    with store.db.connect(owner_id=owner) as conn:
        lifecycle = conn.fetchone(
            "SELECT public.fb_ensure_session_lifecycle(?, ?) AS status",
            (owner, session_id),
        )["status"]
    assert lifecycle == "deleted"
