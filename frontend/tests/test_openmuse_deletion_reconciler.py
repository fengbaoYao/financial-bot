import asyncio

import pytest

from api.financial_bot.auth import LOCAL_MODE, LOCAL_OWNER_ID, OwnerContext
from api.financial_bot.db import Database
from api.financial_bot.openmuse_delete_reconciler import OpenMuseDeletionReconciler
from api.financial_bot.store import FinancialBotStore


def run(coro):
    return asyncio.run(coro)


def payload_hash(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def make_mapping(store, owner_id=LOCAL_OWNER_ID, suffix="a", upstream_id="upstream-1"):
    task, created = store.create_task(
        owner_id=owner_id,
        query=f"private prompt {suffix}",
        session_id=None,
        language="en-US",
        research_mode="quick",
        idempotency_key=f"local-delete-{suffix}",
    )
    assert created
    store.create_openmuse_task_mapping(
        owner_id,
        task["id"],
        payload_sha256=payload_hash(suffix),
    )
    if upstream_id is not None:
        store.update_openmuse_task_mapping(
            owner_id,
            task["id"],
            upstream_task_id=upstream_id,
            deletion_status="pending",
        )
    else:
        store.update_openmuse_task_mapping(owner_id, task["id"], deletion_status="pending")
    return task


class FakeDeleteTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def delete_task(self, owner, local_task_id):
        self.calls.append((owner, local_task_id))
        outcome = self.responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


OWNER = OwnerContext(owner_id=LOCAL_OWNER_ID, mode=LOCAL_MODE)


@pytest.fixture
def store(tmp_path):
    db = Database(sqlite_path=tmp_path / "openmuse-delete.sqlite3")
    db.initialize()
    return FinancialBotStore(db)


def test_only_explicit_identity_matched_full_delete_completes_mapping_and_keeps_local_data(store):
    task = make_mapping(store)
    transport = FakeDeleteTransport(
        [{"task_id": "upstream-1", "status": "deleted", "retained_file_ids": []}]
    )

    result = run(OpenMuseDeletionReconciler(store, transport).reconcile(OWNER))

    mapping = store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])
    assert result.completed == 1
    assert result.pending == []
    assert len(transport.calls) == 1
    assert transport.calls == [(OWNER, task["id"])]
    assert mapping["deletion_status"] == "complete"
    assert mapping["upstream_task_id"] == "upstream-1"
    assert mapping["request_id"]
    assert store.get_task(LOCAL_OWNER_ID, task["id"])["query"] == "private prompt a"


@pytest.mark.parametrize(
    ("response", "expected_reason"),
    [
        ({"task_id": "upstream-1", "status": "partial", "retained_file_ids": ["f1"]}, "upstream_partial"),
        ({"task_id": "upstream-1", "status": "pending"}, "upstream_pending"),
        ({"task_id": "another-task", "status": "deleted"}, "upstream_identity_mismatch"),
        ({"task_id": "upstream-1", "status": "deleted", "retainedFileIds": ["f1"]}, "upstream_files_retained"),
        ({"task_id": "upstream-1", "status": "deleted", "retained_action_ids": ["a1"]}, "upstream_audit_records_retained"),
        ({"task_id": "upstream-1", "status": "deleted", "retainedActivityIds": ["h1"]}, "upstream_audit_records_retained"),
    ],
)
def test_partial_pending_or_wrong_identity_never_completes(response, expected_reason, store):
    task = make_mapping(store, suffix=expected_reason)
    transport = FakeDeleteTransport([response])

    result = run(OpenMuseDeletionReconciler(store, transport).reconcile(OWNER))

    mapping = store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])
    assert result.completed == 0
    assert result.pending[0]["reason"] == expected_reason
    assert mapping["deletion_status"] == "pending"
    assert mapping["deletion_error"] == expected_reason
    assert mapping["upstream_task_id"] == "upstream-1"


@pytest.mark.parametrize(
    ("error", "expected_reason"),
    [
        (TimeoutError(), "upstream_timeout"),
        (ConnectionError("private URL and token must not be persisted"), "upstream_unavailable"),
    ],
)
def test_timeout_and_unreachable_keep_mapping_pending_with_sanitized_reason(
    error, expected_reason, store
):
    task = make_mapping(store, suffix=expected_reason)
    transport = FakeDeleteTransport([error])

    result = run(OpenMuseDeletionReconciler(store, transport).reconcile(OWNER))

    mapping = store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])
    assert result.pending[0]["reason"] == expected_reason
    assert mapping["deletion_status"] == "pending"
    assert mapping["deletion_error"] == expected_reason
    assert "private URL" not in mapping["deletion_error"]
    assert mapping["upstream_task_id"] == "upstream-1"


def test_missing_or_invalid_upstream_identity_is_not_sent_or_completed(store):
    missing = make_mapping(store, suffix="missing-upstream", upstream_id=None)
    transport = FakeDeleteTransport([])

    result = run(OpenMuseDeletionReconciler(store, transport).reconcile(OWNER))

    assert transport.calls == []
    assert len(result.pending) == 1
    mapping = store.get_openmuse_task_mapping(LOCAL_OWNER_ID, missing["id"])
    assert mapping["deletion_status"] == "pending"
    assert mapping["deletion_error"] == "missing_upstream_task_id"


def test_owner_scope_only_consumes_local_owner_mappings(store):
    local = make_mapping(store, suffix="local")
    other = make_mapping(store, owner_id="11111111-1111-4111-8111-111111111111", suffix="other")
    transport = FakeDeleteTransport([{"task_id": "upstream-1", "status": "deleted"}])

    result = run(OpenMuseDeletionReconciler(store, transport).reconcile(OWNER))

    assert result.completed == 1
    assert [call[1] for call in transport.calls] == [local["id"]]
    assert store.get_openmuse_task_mapping(LOCAL_OWNER_ID, local["id"])["deletion_status"] == "complete"
    assert store.get_openmuse_task_mapping(other["owner_id"], other["id"])["deletion_status"] == "pending"


def test_non_local_or_mismatched_owner_context_is_rejected_without_upstream_call(store):
    transport = FakeDeleteTransport([])
    reconciler = OpenMuseDeletionReconciler(store, transport)

    with pytest.raises(ValueError, match="local owner"):
        run(reconciler.reconcile(OwnerContext(owner_id="11111111-1111-4111-8111-111111111111", mode=LOCAL_MODE)))
    with pytest.raises(ValueError, match="local owner"):
        run(reconciler.reconcile(OwnerContext(owner_id=LOCAL_OWNER_ID, mode="supabase_authenticated")))
    assert transport.calls == []


def test_pending_reason_and_mapping_survive_sqlite_reopen_for_retry(store, tmp_path):
    task = make_mapping(store, suffix="reopen")
    transport = FakeDeleteTransport([TimeoutError()])
    reconciler = OpenMuseDeletionReconciler(store, transport)

    first = run(reconciler.reconcile(OWNER))
    assert first.pending[0]["reason"] == "upstream_timeout"

    reopened_db = Database(sqlite_path=store.db.sqlite_path)
    reopened_db.initialize()
    reopened_store = FinancialBotStore(reopened_db)
    retry = FakeDeleteTransport([{"task_id": "upstream-1", "status": "deleted"}])
    result = run(OpenMuseDeletionReconciler(reopened_store, retry).reconcile(OWNER))

    mapping = reopened_store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])
    assert result.completed == 1
    assert mapping["deletion_status"] == "complete"
    assert mapping["upstream_task_id"] == "upstream-1"
    assert reopened_store.get_task(LOCAL_OWNER_ID, task["id"]) is not None


def test_expired_claim_from_crashed_process_can_be_recovered_after_reopen(store):
    task = make_mapping(store, suffix="crashed-claim")
    claimed = store.claim_openmuse_task_mapping(
        LOCAL_OWNER_ID,
        task["id"],
        lease_id="crashed-process-lease",
        lease_seconds=30,
    )
    assert claimed is not None
    with store.db.connect(owner_id=LOCAL_OWNER_ID, write=True) as conn:
        conn.execute(
            "UPDATE fb_openmuse_task_mappings SET reconcile_lease_expires_at = ? WHERE owner_id = ? AND task_id = ?",
            ("2000-01-01T00:00:00+00:00", LOCAL_OWNER_ID, task["id"]),
        )

    reopened = FinancialBotStore(Database(sqlite_path=store.db.sqlite_path))
    transport = FakeDeleteTransport([{"task_id": "upstream-1", "status": "deleted"}])
    result = run(OpenMuseDeletionReconciler(reopened, transport).reconcile(OWNER))

    mapping = reopened.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])
    assert result.completed == 1
    assert len(transport.calls) == 1
    assert mapping["deletion_status"] == "complete"
    assert mapping["reconcile_lease_id"] is None
    assert mapping["reconcile_lease_expires_at"] is None


def test_expired_worker_cannot_overwrite_a_new_process_lease(store):
    from api.financial_bot.store import ConflictError

    task = make_mapping(store, suffix="stale-lease-result")
    store.claim_openmuse_task_mapping(
        LOCAL_OWNER_ID,
        task["id"],
        lease_id="old-worker",
        lease_seconds=30,
    )
    with store.db.connect(owner_id=LOCAL_OWNER_ID, write=True) as conn:
        conn.execute(
            "UPDATE fb_openmuse_task_mappings SET reconcile_lease_expires_at = ? WHERE owner_id = ? AND task_id = ?",
            ("2000-01-01T00:00:00+00:00", LOCAL_OWNER_ID, task["id"]),
        )
    reopened = FinancialBotStore(Database(sqlite_path=store.db.sqlite_path))
    assert reopened.claim_openmuse_task_mapping(
        LOCAL_OWNER_ID,
        task["id"],
        lease_id="new-worker",
        lease_seconds=30,
    ) is not None

    with pytest.raises(ConflictError, match="lease"):
        store.update_openmuse_task_mapping(
            LOCAL_OWNER_ID,
            task["id"],
            deletion_status="complete",
            lease_id="old-worker",
        )
    current = reopened.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])
    assert current["deletion_status"] == "pending"
    assert current["reconcile_lease_id"] == "new-worker"
    completed = reopened.update_openmuse_task_mapping(
        LOCAL_OWNER_ID,
        task["id"],
        deletion_status="complete",
        lease_id="new-worker",
    )
    assert completed["deletion_status"] == "complete"


def test_expired_worker_is_fenced_at_subsecond_lease_boundary(store, monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from api.financial_bot import store as store_module
    from api.financial_bot.store import ConflictError

    task = make_mapping(store, suffix="subsecond-expired-lease")
    store.claim_openmuse_task_mapping(
        LOCAL_OWNER_ID,
        task["id"],
        lease_id="expired-worker",
        lease_seconds=30,
    )
    now = datetime.now(timezone.utc).replace(microsecond=500_000)
    expired_at = now.replace(microsecond=100_000)
    with store.db.connect(owner_id=LOCAL_OWNER_ID, write=True) as conn:
        conn.execute(
            "UPDATE fb_openmuse_task_mappings SET reconcile_lease_expires_at = ? WHERE owner_id = ? AND task_id = ?",
            (expired_at.isoformat(timespec="microseconds"), LOCAL_OWNER_ID, task["id"]),
        )

    # The normal updated_at clock is second-granular. It must not make an
    # already expired subsecond lease appear valid to a stale worker.
    monkeypatch.setattr(
        store_module,
        "utc_now",
        lambda: now.replace(microsecond=0).isoformat(timespec="seconds"),
    )
    monkeypatch.setattr(store_module, "datetime", SimpleNamespace(now=lambda _timezone: now))
    with pytest.raises(ConflictError, match="lease"):
        store.update_openmuse_task_mapping(
            LOCAL_OWNER_ID,
            task["id"],
            deletion_status="complete",
            lease_id="expired-worker",
        )
    current = store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])
    assert current["deletion_status"] == "pending"
    assert current["reconcile_lease_id"] == "expired-worker"


def test_owner_mapping_with_invalid_task_id_is_preserved_without_remote_call(store):
    task = make_mapping(store, suffix="invalid-id")
    with store.db.connect(owner_id=LOCAL_OWNER_ID, write=True) as conn:
        conn.execute(
            "UPDATE fb_openmuse_task_mappings SET upstream_task_id = ? WHERE owner_id = ? AND task_id = ?",
            ("../wrong/task", LOCAL_OWNER_ID, task["id"]),
        )
    transport = FakeDeleteTransport([])

    result = run(OpenMuseDeletionReconciler(store, transport).reconcile(OWNER))

    assert transport.calls == []
    mapping = store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])
    assert result.pending[0]["reason"] == "invalid_upstream_task_id"
    assert mapping["deletion_status"] == "pending"
    assert mapping["upstream_task_id"] == "../wrong/task"


def test_concurrent_failed_attempt_does_not_overwrite_or_break_completed_delete(store):
    task = make_mapping(store, suffix="concurrent-delete")

    class RacingTransport:
        def __init__(self, response):
            self.calls = []
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.response = response

        async def delete_task(self, owner, local_task_id):
            self.calls.append((owner, local_task_id))
            self.started.set()
            await self.release.wait()
            if isinstance(self.response, Exception):
                raise self.response
            return self.response

    async def run_race():
        first_transport = RacingTransport({"task_id": "upstream-1", "status": "deleted"})
        second_transport = RacingTransport(TimeoutError())
        first = asyncio.create_task(OpenMuseDeletionReconciler(store, first_transport).reconcile(OWNER))
        await asyncio.wait_for(first_transport.started.wait(), timeout=1)
        second_store = FinancialBotStore(Database(sqlite_path=store.db.sqlite_path))
        second = asyncio.create_task(OpenMuseDeletionReconciler(second_store, second_transport).reconcile(OWNER))
        second_result = await asyncio.wait_for(second, timeout=1)
        first_transport.release.set()
        first_result = await asyncio.wait_for(first, timeout=1)
        return first_result, second_result, first_transport, second_transport

    first_result, second_result, first_transport, second_transport = run(run_race())

    mapping = store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])
    assert first_result.completed == 1
    assert second_result.completed == 0
    assert second_result.pending == []
    assert len(first_transport.calls) == 1
    assert second_transport.calls == []
    assert mapping["deletion_status"] == "complete"
    assert mapping["deletion_error"] == ""
