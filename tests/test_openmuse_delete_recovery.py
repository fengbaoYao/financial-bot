import asyncio
import hashlib

import httpx
import pytest

from api.financial_bot.auth import HOSTED_MODE, LOCAL_MODE, LOCAL_OWNER_ID, OwnerContext
from api.financial_bot.config import BotConfig
from api.financial_bot.db import Database
from api.financial_bot.openmuse_tasks import OpenMuseTasksConfig
from api.financial_bot import runtime
from api.financial_bot.store import FinancialBotStore


OWNER = OwnerContext(owner_id=LOCAL_OWNER_ID, mode=LOCAL_MODE)
HOSTED = OwnerContext(owner_id="11111111-1111-4111-8111-111111111111", mode=HOSTED_MODE)


def create_pending_task(store, session_id="delete-recovery"):
    task, created = store.create_task(
        owner_id=LOCAL_OWNER_ID,
        query="private task content stays local",
        session_id=session_id,
        language="en-US",
        research_mode="quick",
        idempotency_key=session_id,
    )
    assert created
    store.create_openmuse_task_mapping(
        LOCAL_OWNER_ID,
        task["id"],
        payload_sha256=hashlib.sha256(b"safe digest").hexdigest(),
    )
    store.update_openmuse_task_mapping(
        LOCAL_OWNER_ID,
        task["id"],
        upstream_task_id="upstream-task-1",
        deletion_status="pending",
    )
    store.delete_session(LOCAL_OWNER_ID, session_id)
    return task


def local_config(tmp_path):
    return BotConfig(data_dir=tmp_path, backup_dir=None, third_party_retention_verified=False)


def enabled_tasks_config():
    return OpenMuseTasksConfig(
        enabled=True,
        base_url="http://127.0.0.1:8787",
        access_key="local-test-key",
        timeout_seconds=0.5,
    )


def wait_until(predicate, timeout=3):
    async def wait():
        for _ in range(int(timeout * 100)):
            if predicate():
                return
            await asyncio.sleep(0.01)
        raise AssertionError("recovery state did not change before timeout")

    asyncio.run(wait())


def test_recovery_stays_disabled_for_default_and_hosted_modes(tmp_path):
    db = Database(sqlite_path=tmp_path / "disabled.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    create_pending_task(store)
    runtime.configure(config=local_config(tmp_path), store=store)
    calls = []

    def forbidden(request):
        calls.append(request)
        raise AssertionError("disabled and hosted recovery must not contact OpenMuse")

    transport = httpx.MockTransport(forbidden)

    async def run():
        assert not await runtime.start_openmuse_deletion_recovery(
            OWNER,
            config=OpenMuseTasksConfig(enabled=False),
            transport=transport,
        )
        assert not await runtime.start_openmuse_deletion_recovery(
            HOSTED,
            config=enabled_tasks_config(),
            transport=transport,
        )
        assert calls == []
        await runtime.stop_openmuse_deletion_recovery()

    try:
        asyncio.run(run())
    finally:
        runtime.reset()


def test_restart_retries_unreachable_delete_then_completes_once_from_persisted_mapping(tmp_path, caplog):
    db_path = tmp_path / "restart.sqlite3"
    db = Database(sqlite_path=db_path)
    db.initialize()
    store = FinancialBotStore(db)
    task = create_pending_task(store, "restart-delete")
    runtime.configure(config=local_config(tmp_path), store=store)
    first_delete = asyncio.Event()

    def unavailable(request):
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "first-session", "mode": "live"})
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "task": {"id": "upstream-task-1", "kind": "agent", "status": "succeeded"},
                    "events": [],
                },
            )
        first_delete.set()
        raise httpx.ReadTimeout("Authorization: Bearer sensitive-fixture-secret at https://example.test/path")

    async def first_run():
        assert await runtime.start_openmuse_deletion_recovery(
            OWNER,
            config=enabled_tasks_config(),
            transport=httpx.MockTransport(unavailable),
            interval_seconds=30,
        )
        await asyncio.wait_for(first_delete.wait(), timeout=3)
        await asyncio.to_thread(
            wait_until,
            lambda: store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])["deletion_error"]
            == "upstream_unavailable",
        )
        await runtime.stop_openmuse_deletion_recovery()

    asyncio.run(first_run())
    assert "sensitive-fixture-secret" not in caplog.text
    assert "example.test" not in caplog.text
    assert store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])["deletion_status"] == "pending"

    reopened = FinancialBotStore(Database(sqlite_path=db_path))
    runtime.configure(config=local_config(tmp_path), store=reopened)
    second_calls = []
    completed = asyncio.Event()

    def recovered(request):
        second_calls.append(request.method)
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "second-session", "mode": "live"})
        if request.method == "GET":
            return httpx.Response(404, json={"detail": "already deleted"})
        completed.set()
        return httpx.Response(200, json={"taskId": "upstream-task-1", "status": "deleted"})

    async def second_run():
        assert await runtime.start_openmuse_deletion_recovery(
            OWNER,
            config=enabled_tasks_config(),
            transport=httpx.MockTransport(recovered),
            interval_seconds=0.1,
        )
        await asyncio.wait_for(completed.wait(), timeout=3)
        for _ in range(200):
            if reopened.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])["deletion_status"] == "complete":
                break
            await asyncio.sleep(0.01)
        assert reopened.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])["deletion_status"] == "complete"
        await asyncio.sleep(0.15)
        await runtime.stop_openmuse_deletion_recovery()

    try:
        asyncio.run(second_run())
        assert second_calls.count("DELETE") == 1
        assert reopened.list_tasks(LOCAL_OWNER_ID, session_id="restart-delete") == []
        assert reopened.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])["deletion_error"] == ""
    finally:
        asyncio.run(runtime.stop_openmuse_deletion_recovery())
        runtime.reset()


def test_partial_remote_result_remains_pending_and_local_task_is_retained(tmp_path):
    db = Database(sqlite_path=tmp_path / "partial.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    task = create_pending_task(store, "partial-delete")
    runtime.configure(config=local_config(tmp_path), store=store)
    deleted = asyncio.Event()

    def partial(request):
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "test-session", "mode": "live"})
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "task": {"id": "upstream-task-1", "kind": "agent", "status": "cancelled"},
                    "events": [],
                },
            )
        deleted.set()
        return httpx.Response(
            200,
            json={
                "taskId": "upstream-task-1",
                "status": "partial",
                "retainedFileIds": ["file-1"],
                "retainedActionIds": [],
                "retainedActivityIds": [],
            },
        )

    async def run():
        assert await runtime.start_openmuse_deletion_recovery(
            OWNER,
            config=enabled_tasks_config(),
            transport=httpx.MockTransport(partial),
            interval_seconds=30,
        )
        await asyncio.wait_for(deleted.wait(), timeout=3)
        for _ in range(200):
            mapping = store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])
            if mapping["deletion_error"] == "upstream_partial":
                break
            await asyncio.sleep(0.01)
        assert mapping["deletion_status"] == "pending"
        assert mapping["deletion_error"] == "upstream_partial"
        assert store.get_task(LOCAL_OWNER_ID, task["id"])["id"] == task["id"]
        await runtime.stop_openmuse_deletion_recovery()

    try:
        asyncio.run(run())
    finally:
        asyncio.run(runtime.stop_openmuse_deletion_recovery())
        runtime.reset()
