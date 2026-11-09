import asyncio
import hashlib
import json

import httpx
import pytest

from api.financial_bot.auth import HOSTED_MODE, LOCAL_MODE, LOCAL_OWNER_ID, OwnerContext
from api.financial_bot.db import Database
from api.financial_bot import openmuse_task_coordinator as coordinator_module
from api.financial_bot.openmuse_tasks import OpenMuseTasksClient, OpenMuseTasksConfig
from api.financial_bot.openmuse_task_coordinator import (
    OpenMuseTaskCoordinator,
    RESEARCH_SANDBOX_CONTRACT,
    SandboxVerification,
    TASK_CREATE_CONTRACT,
    TASK_REVISION_CONTRACT,
)
from api.financial_bot.store import ConflictError, FinancialBotStore


def run(coro):
    return asyncio.run(coro)


def owner(mode=LOCAL_MODE, owner_id=LOCAL_OWNER_ID):
    return OwnerContext(owner_id=owner_id, mode=mode)


@pytest.fixture
def store(tmp_path):
    db = Database(sqlite_path=tmp_path / "coordinator.sqlite3")
    db.initialize()
    return FinancialBotStore(db)


def create_task(store, *, suffix="one", query="Summarize public software documentation", research_mode="quick"):
    task, created = store.create_task(
        owner_id=LOCAL_OWNER_ID,
        query=query,
        session_id=None,
        language="en-US",
        research_mode=research_mode,
        idempotency_key=f"coordinator-{suffix}",
    )
    assert created
    return task


def test_shared_research_uses_authenticated_openmuse_readiness_without_fake_verifier(store):
    task = create_task(store, suffix="shared", research_mode="openmuse_shared")
    calls = []
    contracts = {
        "taskCreateContract": TASK_CREATE_CONTRACT,
        "researchSandboxContract": "openmuse-research-sandbox:v1:held-provision-attest",
        "taskRevisionContract": "openmuse-task-revision:v1:atomic-per-task-sequence",
    }

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/health":
            return httpx.Response(200, json=contracts)
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "fake-session", "mode": "sample"})
        if request.url.path == "/api/agent/tasks/shared-research":
            body = json.loads(request.content)
            upstream_id = upstream_task_id(body["requestId"])
            return httpx.Response(201, json={
                "id": upstream_id,
                "title": body["title"],
                "prompt": body["prompt"],
                "kind": "research",
                "requestFingerprint": v2_request_fingerprint(body["prompt"], title=body["title"]),
                "taskRevision": 1,
                "input": {"research_scope": "general"},
                "status": "paused",
                "state": {"sharedResearchWorkspace": True},
            })
        assert request.url.path.endswith("/control")
        upstream_id = request.url.path.split("/")[-2]
        return httpx.Response(200, json={
            "id": upstream_id,
            "title": "Summarize public software documentation",
            "prompt": "Summarize public software documentation",
            "kind": "research",
            "requestFingerprint": v2_request_fingerprint("Summarize public software documentation", title="Summarize public software documentation"),
            "taskRevision": 2,
            "input": {"research_scope": "general"},
            "status": "queued",
            "state": {"sharedResearchWorkspace": True},
        })

    client = OpenMuseTasksClient(
        OpenMuseTasksConfig(enabled=True, base_url="http://127.0.0.1:8787", access_key="test"),
        transport=httpx.MockTransport(handler),
    )
    coordinator = OpenMuseTaskCoordinator(
        store, client, enabled=True,
        is_general_task=lambda item: item.get("research_mode") == "openmuse_shared",
    )
    result = run(coordinator.submit(owner(), task["id"]))
    assert result.state == "submitted", result.reason
    assert any(path.endswith("/tasks/shared-research") for _method, path in calls)
    assert not any(path.endswith("/provision-sandbox") for _method, path in calls)
    assert any(path.endswith("/control") for _method, path in calls)


def test_shared_research_task_never_enters_financial_executor_or_restart_recovery(store, monkeypatch):
    task = create_task(store, suffix="shared-executor", research_mode="openmuse_shared")
    from api.financial_bot.executor import TaskExecutor

    executor = TaskExecutor(store)
    starts = []
    monkeypatch.setattr(executor, "_thread_main", lambda *args, **kwargs: starts.append(args))
    executor.start(owner(), task["id"])
    assert starts == []
    assert store.recover_interrupted_tasks() == 0
    assert store.get_task(LOCAL_OWNER_ID, task["id"])["status"] == "queued"
    assert all(item["id"] != task["id"] for item in store.list_resumable_tasks())


def upstream_task_id(request_id):
    return hashlib.sha256(f"task:{request_id}".encode("utf-8")).hexdigest()


def v2_request_fingerprint(prompt, *, title=None, task_input=None):
    normalized_prompt = prompt.strip()
    payload = {
        "goalId": None,
        "input": task_input if task_input is not None else {"research_scope": "general"},
        "kind": "research",
        "milestoneId": None,
        "prompt": normalized_prompt,
        "title": title if title is not None else normalized_prompt[:160],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def test_financial_bridge_scope_comes_from_server_owned_authorization():
    financial = OpenMuseTaskCoordinator._payload({
        "query": "研究腾讯股价和估值",
        "research_mode": "openmuse_shared",
        "authorization": {"scope": {"financial_bridge_required": True}},
    })
    general = OpenMuseTaskCoordinator._payload({
        "query": "Summarize public documentation",
        "research_mode": "openmuse_shared",
        "authorization": {"scope": {"financial_bridge_required": False}},
    })
    assert financial is not None and financial[0]["input"] == {
        "research_scope": "financial", "financial_bridge_required": True,
    }
    assert general is not None and general[0]["input"] == {"research_scope": "general"}


def health_contracts():
    return {
        "taskCreateContract": TASK_CREATE_CONTRACT,
        "researchSandboxContract": RESEARCH_SANDBOX_CONTRACT,
        "taskRevisionContract": TASK_REVISION_CONTRACT,
    }


class GeneralTaskStore:
    """Expose a server-classified general task without changing DB schema."""

    def __init__(self, store, *, query_override=None):
        self.store = store
        self.query_override = query_override

    def get_task(self, owner_id, task_id):
        item = self.store.get_task(owner_id, task_id)
        item["research_mode"] = "general"
        if self.query_override is not None:
            item["query"] = self.query_override
        return item

    def __getattr__(self, name):
        return getattr(self.store, name)


def test_all_gates_are_closed_by_default_and_do_not_call_openmuse(store):
    task = create_task(store)
    requests = []

    def handler(request):
        requests.append(request.url.path)
        raise AssertionError("default-disabled coordinator must not use transport")

    client = OpenMuseTasksClient(
        OpenMuseTasksConfig(enabled=True, base_url="http://127.0.0.1:8787", access_key="test"),
        transport=httpx.MockTransport(handler),
    )
    coordinator = OpenMuseTaskCoordinator(store, client)

    result = run(coordinator.submit(owner(), task["id"]))

    assert result.state == "blocked"
    assert result.reason == "coordinator_disabled"
    assert requests == []
    assert store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"]) is None


def test_submit_creates_held_research_then_provisions_and_resumes(store):
    task = create_task(store)
    calls = []
    contracts = {
        "taskCreateContract": TASK_CREATE_CONTRACT,
        "researchSandboxContract": "openmuse-research-sandbox:v1:held-provision-attest",
        "taskRevisionContract": TASK_REVISION_CONTRACT,
    }

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/health":
            return httpx.Response(200, json=contracts)
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "fake-session", "mode": "sample"})
        if request.url.path == "/api/agent/tasks":
            body = json.loads(request.content)
            assert body["kind"] == "research"
            assert body["input"] == {"research_scope": "general"}
            upstream_id = upstream_task_id(body["requestId"])
            return httpx.Response(
                201,
                json={
                    "id": upstream_id,
                    "title": body["title"],
                    "prompt": body["prompt"],
                    "kind": "research",
                    "taskRevision": 1,
                    "requestFingerprint": v2_request_fingerprint(
                        body["prompt"], title=body["title"], task_input=body["input"]
                    ),
                    "input": body["input"],
                    "status": "paused",
                },
            )
        if request.url.path.endswith("/provision-sandbox"):
            return httpx.Response(
                200,
                json={
                    "taskId": request.url.path.split("/")[-2],
                    "status": "ready",
                    "attestation": {"version": "research-sandbox-v1", "verified": True},
                },
            )
        assert request.url.path.endswith("/control")
        assert json.loads(request.content) == {"action": "resume"}
        upstream_id = (
            request.url.path.split("/")[-2]
            if request.method == "POST"
            else request.url.path.split("/")[-1]
        )
        return httpx.Response(
            200,
            json={
                "id": upstream_id,
                "title": "Summarize public software documentation",
                "prompt": "Summarize public software documentation",
                "kind": "research",
                "taskRevision": 3,
                "requestFingerprint": v2_request_fingerprint(
                    "Summarize public software documentation",
                    title="Summarize public software documentation",
                ),
                "input": {"research_scope": "general"},
                "status": "queued",
            },
        )

    client = OpenMuseTasksClient(
        OpenMuseTasksConfig(enabled=True, base_url="http://127.0.0.1:8787", access_key="test"),
        transport=httpx.MockTransport(handler),
    )
    coordinator = OpenMuseTaskCoordinator(
        GeneralTaskStore(store),
        client,
        enabled=True,
        is_general_task=lambda item: item.get("id") == task["id"],
        verify_sandbox=lambda item: SandboxVerification(item["id"], True, "trusted-policy-check"),
    )

    result = run(coordinator.submit(owner(), task["id"]))

    assert result.state == "submitted"
    assert result.upstream_status == "queued"
    assert [path for _method, path in calls].count("/api/agent/tasks") == 1
    assert [path for _method, path in calls if path.endswith("/provision-sandbox")] == [
        f"/api/agent/tasks/{result.mapping['upstream_task_id']}/provision-sandbox"
    ]
    assert [path for _method, path in calls if path.endswith("/control")] == [
        f"/api/agent/tasks/{result.mapping['upstream_task_id']}/control"
    ]


@pytest.mark.parametrize("uncertain_step", ["create", "provision", "resume"])
def test_uncertain_research_step_uses_get_only_reconciliation(store, uncertain_step):
    task = create_task(store, suffix=f"uncertain-{uncertain_step}")
    calls = []
    upstream = {}
    contracts = {
        "taskCreateContract": TASK_CREATE_CONTRACT,
        "researchSandboxContract": "openmuse-research-sandbox:v1:held-provision-attest",
        "taskRevisionContract": TASK_REVISION_CONTRACT,
    }

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/health":
            return httpx.Response(200, json=contracts)
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "fake-session", "mode": "sample"})
        if request.method == "POST" and request.url.path == "/api/agent/tasks":
            body = json.loads(request.content)
            upstream_id = upstream_task_id(body["requestId"])
            upstream[upstream_id] = {
                "id": upstream_id,
                "title": body["title"],
                "prompt": body["prompt"],
                "kind": "research",
                "taskRevision": 1,
                "requestFingerprint": v2_request_fingerprint(
                    body["prompt"], title=body["title"], task_input=body["input"]
                ),
                "input": body["input"],
                "status": "paused",
                "state": {},
            }
            if uncertain_step == "create":
                raise httpx.ReadTimeout("create response lost after commit")
            return httpx.Response(201, json=upstream[upstream_id])
        upstream_id = request.url.path.split("/")[-2] if request.method == "POST" else request.url.path.split("/")[-1]
        current = upstream[upstream_id]
        if request.method == "GET":
            return httpx.Response(200, json={"task": current, "events": []})
        if request.url.path.endswith("/provision-sandbox"):
            current["state"] = {
                "sandboxProvision": {"status": "ready"},
                "sandboxProvisionCleanup": {"status": "complete"},
            }
            current["taskRevision"] += 1
            if uncertain_step == "provision":
                raise httpx.ReadTimeout("provision response lost after commit")
            return httpx.Response(
                200,
                json={
                    "taskId": upstream_id,
                    "status": "ready",
                    "attestation": {"version": "research-sandbox-v1", "verified": True},
                },
            )
        current["status"] = "queued"
        current["taskRevision"] += 1
        if uncertain_step == "resume":
            raise httpx.ReadTimeout("resume response lost after commit")
        return httpx.Response(200, json=current)

    client = OpenMuseTasksClient(
        OpenMuseTasksConfig(enabled=True, base_url="http://127.0.0.1:8787", access_key="test"),
        transport=httpx.MockTransport(handler),
    )
    coordinator = OpenMuseTaskCoordinator(
        GeneralTaskStore(store),
        client,
        enabled=True,
        is_general_task=lambda item: item.get("id") == task["id"],
        verify_sandbox=lambda item: SandboxVerification(item["id"], True, "trusted-policy-check"),
    )

    result = run(coordinator.submit(owner(), task["id"]))

    assert result.state in {"submitted", "reconciled"}
    assert result.upstream_status == "queued"
    assert sum(method == "POST" and path == "/api/agent/tasks" for method, path in calls) == 1
    assert sum(method == "POST" and path.endswith("/provision-sandbox") for method, path in calls) == 1
    assert sum(method == "POST" and path.endswith("/control") for method, path in calls) == 1


def test_hosted_owner_and_financial_task_are_rejected_before_provider_call(store):
    task = create_task(store)
    requests = []
    client = OpenMuseTasksClient(
        OpenMuseTasksConfig(enabled=True, base_url="http://127.0.0.1:8787", access_key="test"),
        transport=httpx.MockTransport(lambda request: requests.append(request.url.path)),
    )
    coordinator = OpenMuseTaskCoordinator(
        store,
        client,
        enabled=True,
        is_general_task=lambda _task: True,
        verify_sandbox=lambda item: SandboxVerification(item["id"], True, "sandbox-check-1"),
    )

    hosted = run(coordinator.submit(owner(HOSTED_MODE, "supabase-user"), task["id"]))
    financial = run(coordinator.submit(owner(), task["id"]))

    assert hosted.state == "blocked"
    assert hosted.reason == "local_single_user_only"
    assert financial.state == "blocked"
    assert financial.reason == "not_general_task"
    assert requests == []
    assert store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"]) is None


def test_submit_persists_mapping_before_create_and_replay_recovers_without_second_post(store):
    task = create_task(store)
    calls = []
    sent_bodies = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/health":
            return httpx.Response(200, json=health_contracts())
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "fake-session", "mode": "sample"})
        if request.method == "POST":
            if request.url.path == "/api/agent/tasks":
                body = json.loads(request.content)
                sent_bodies.append(body)
                assert body["requestId"].startswith("fb-")
                assert body["kind"] == "research"
                assert body["input"] == {"research_scope": "general"}
                assert store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])["status"] == "creating"
                candidate = upstream_task_id(body["requestId"])
                return httpx.Response(
                    201,
                    json={
                        "id": candidate,
                        "title": body["title"],
                        "prompt": body["prompt"],
                        "kind": body["kind"],
                        "taskRevision": 1,
                            "requestFingerprint": v2_request_fingerprint(
                                body["prompt"], title=body["title"], task_input=body["input"]
                            ),
                        "input": body["input"],
                        "status": "paused",
                    },
                )
            if request.url.path.endswith("/provision-sandbox"):
                return httpx.Response(
                    200,
                    json={
                        "taskId": request.url.path.split("/")[-2],
                        "status": "ready",
                        "attestation": {"version": "research-sandbox-v1", "verified": True},
                    },
                )
            assert json.loads(request.content) == {"action": "resume"}
            candidate = request.url.path.split("/")[-2]
            return httpx.Response(
                200,
                json={
                    "id": candidate,
                    "title": "Summarize public software documentation",
                    "prompt": "Summarize public software documentation",
                    "kind": "research",
                    "taskRevision": 3,
                    "requestFingerprint": v2_request_fingerprint(
                        "Summarize public software documentation",
                        title="Summarize public software documentation",
                    ),
                    "input": {"research_scope": "general"},
                    "status": "queued",
                },
            )
        candidate = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(
            200,
            json={
                "task": {
                    "id": candidate,
                    "title": "Summarize public software documentation",
                    "prompt": "Summarize public software documentation",
                    "kind": "research",
                    "taskRevision": 4,
                    "input": {"research_scope": "general"},
                    "status": "running",
                },
                "events": [
                    {
                        "id": "event-1",
                        "taskId": candidate,
                        "kind": "progress",
                        "title": "Collected sources",
                    }
                ],
            },
        )

    client = OpenMuseTasksClient(
        OpenMuseTasksConfig(enabled=True, base_url="http://127.0.0.1:8787", access_key="test"),
        transport=httpx.MockTransport(handler),
    )
    coordinator = OpenMuseTaskCoordinator(
        GeneralTaskStore(store),
        client,
        enabled=True,
        is_general_task=lambda item: item["id"] == task["id"],
        verify_sandbox=lambda item: SandboxVerification(item["id"], True, "sandbox-check-1"),
    )

    submitted = run(coordinator.submit(owner(), task["id"]))
    replayed = run(coordinator.submit(owner(), task["id"]))

    assert submitted.state == "submitted"
    assert replayed.state == "synced"
    mapping = store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])
    assert mapping["upstream_task_id"] == upstream_task_id(mapping["request_id"])
    assert sent_bodies[0] == {
        "requestId": mapping["request_id"],
        "kind": "research",
        "prompt": "Summarize public software documentation",
        "title": "Summarize public software documentation",
        "input": {"research_scope": "general"},
    }
    assert mapping["payload_sha256"] == hashlib.sha256(
        json.dumps(
            {
                "goalId": None,
                "input": {"research_scope": "general"},
                "kind": "research",
                "milestoneId": None,
                "prompt": "Summarize public software documentation",
                "title": "Summarize public software documentation",
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert [path for _method, path in calls].count("/api/agent/tasks") == 1
    assert sum(method == "POST" and path.endswith("/provision-sandbox") for method, path in calls) == 1
    assert sum(method == "POST" and path.endswith("/control") for method, path in calls) == 1
    status_events = [event for event in store.list_events(LOCAL_OWNER_ID, task["id"]) if event["kind"] == "openmuse_status"]
    assert [event["status"] for event in status_events] == ["paused", "queued", "running"]


# Fixed vectors independently computed with OpenMuse's JS key canonicalizer and
# JSON.stringify, then compared to the coordinator's Python SHA-256 result.
def test_v2_fingerprint_covers_actual_unicode_task_payload_fields(store):
    prompt = "研究公开文档 🧪 café"
    task = create_task(store, suffix="unicode-payload", query=prompt)

    payload, digest = OpenMuseTaskCoordinator._payload(task)

    assert payload == {
        "kind": "research",
        "prompt": prompt,
        "title": prompt,
        "input": {"research_scope": "general"},
    }
    assert digest == "fd30d8c8615f4d725118d75792557afa66ffaf7955dc146f188a119bdbef6675"


def test_v2_fingerprint_uses_openmuse_json_key_order_for_nested_unicode_keys(store, monkeypatch):
    task = create_task(store, suffix="unicode-keys", query="Unicode key order")
    first_input = {"\ue000": "bmp", "𐀀": "astral", "nested": {"z": 1, "a": 2}}
    second_input = {"nested": {"a": 2, "z": 1}, "𐀀": "astral", "\ue000": "bmp"}

    monkeypatch.setattr(coordinator_module, "_GENERIC_INPUT", first_input)
    first_payload, first_digest = OpenMuseTaskCoordinator._payload(task)
    monkeypatch.setattr(coordinator_module, "_GENERIC_INPUT", second_input)
    second_payload, second_digest = OpenMuseTaskCoordinator._payload(task)

    assert first_payload["input"] == first_input
    assert second_payload["input"] == second_input
    assert first_digest == second_digest
    assert first_digest == "015f10b4b4313588ad8ca77761b4e4ed76fd65bb9b5b98743ecd4f9049d11b8e"


def test_reconciliation_rejects_task_with_same_fields_but_wrong_server_fingerprint(store):
    task = create_task(store, suffix="wrong-upstream-fingerprint")
    payload, _digest = OpenMuseTaskCoordinator._payload(task)
    projection = {
        "id": "expected-upstream-id",
        "kind": "research",
        "taskRevision": 1,
        "prompt": payload["prompt"],
        "title": payload["title"],
        "input": payload["input"],
        "requestFingerprint": "f" * 64,
        "status": "paused",
    }

    assert not OpenMuseTaskCoordinator._projection_matches_request(
        projection,
        upstream_id="expected-upstream-id",
        payload=payload,
    )


def test_legacy_payload_hash_is_not_silently_rebound_to_v2(store):
    task = create_task(store, suffix="legacy-hash")
    prompt = task["query"].strip()
    old_v1_payload = {"kind": "agent", "prompt": prompt, "title": prompt[:160]}
    old_hash = hashlib.sha256(
        json.dumps(old_v1_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    mapping, created = store.create_openmuse_task_mapping(
        LOCAL_OWNER_ID,
        task["id"],
        payload_sha256=old_hash,
    )
    assert created
    requests = []
    client = OpenMuseTasksClient(
        OpenMuseTasksConfig(enabled=True, base_url="http://127.0.0.1:8787", access_key="test"),
        transport=httpx.MockTransport(lambda request: requests.append(request.url.path)),
    )
    coordinator = OpenMuseTaskCoordinator(
        GeneralTaskStore(store),
        client,
        enabled=True,
        is_general_task=lambda _item: True,
        verify_sandbox=lambda item: SandboxVerification(item["id"], True, "sandbox-check-1"),
    )

    with pytest.raises(ConflictError, match="payload|idempotency|request"):
        run(coordinator.submit(owner(), task["id"]))

    assert requests == []
    unchanged = store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])
    assert unchanged["payload_sha256"] == old_hash
    assert unchanged["upstream_task_id"] is None


def test_timeout_reconciles_by_pinned_id_and_exact_payload_without_second_post(store):
    task = create_task(store)
    calls = []
    upstream = {}

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/health":
            return httpx.Response(200, json=health_contracts())
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "fake-session", "mode": "sample"})
        if request.method == "POST" and request.url.path == "/api/agent/tasks":
            body = json.loads(request.content)
            candidate = upstream_task_id(body["requestId"])
            upstream[candidate] = {
                "id": candidate,
                "title": body["title"],
                "prompt": body["prompt"],
                "kind": body["kind"],
                "taskRevision": 1,
                "requestFingerprint": v2_request_fingerprint(
                    body["prompt"], title=body["title"], task_input=body["input"]
                ),
                "input": body["input"],
                "status": "paused",
                "state": {},
            }
            # Model a server that committed the task but lost the response.
            raise httpx.ReadTimeout("uncertain create outcome")
        if request.method == "POST" and request.url.path.endswith("/provision-sandbox"):
            candidate = request.url.path.split("/")[-2]
            upstream[candidate]["state"] = {
                "sandboxProvision": {"status": "ready"},
                "sandboxProvisionCleanup": {"status": "complete"},
            }
            upstream[candidate]["taskRevision"] += 1
            return httpx.Response(
                200,
                json={
                    "taskId": candidate,
                    "status": "ready",
                    "attestation": {"version": "research-sandbox-v1", "verified": True},
                },
            )
        if request.method == "POST" and request.url.path.endswith("/control"):
            candidate = request.url.path.split("/")[-2]
            upstream[candidate]["status"] = "queued"
            upstream[candidate]["taskRevision"] += 1
            return httpx.Response(200, json=upstream[candidate])
        candidate = request.url.path.rsplit("/", 1)[-1]
        item = upstream[candidate]
        return httpx.Response(200, json={"task": item, "events": []})

    client = OpenMuseTasksClient(
        OpenMuseTasksConfig(enabled=True, base_url="http://127.0.0.1:8787", access_key="test"),
        transport=httpx.MockTransport(handler),
    )
    coordinator = OpenMuseTaskCoordinator(
        GeneralTaskStore(store),
        client,
        enabled=True,
        is_general_task=lambda _item: True,
        verify_sandbox=lambda item: SandboxVerification(item["id"], True, "sandbox-check-1"),
    )

    first = run(coordinator.submit(owner(), task["id"]))
    second = run(coordinator.submit(owner(), task["id"]))

    assert first.state == "reconciled"
    assert second.state == "synced"
    mapping = store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])
    assert mapping["status"] == "queued"
    assert mapping["upstream_task_id"] == upstream_task_id(mapping["request_id"])
    assert sum(method == "POST" and path == "/api/agent/tasks" for method, path in calls) == 1
    assert sum(method == "POST" and path.endswith("/provision-sandbox") for method, path in calls) == 1
    assert sum(method == "POST" and path.endswith("/control") for method, path in calls) == 1
    assert any(path == f"/api/agent/tasks/{mapping['upstream_task_id']}" for _, path in calls)


def test_timeout_with_reconciliation_404_stays_unknown_and_never_reposts(store):
    task = create_task(store)
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/health":
            return httpx.Response(200, json=health_contracts())
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "fake-session", "mode": "sample"})
        if request.method == "POST":
            raise httpx.ReadTimeout("uncertain create outcome")
        return httpx.Response(404, json={"error": "not found"})

    client = OpenMuseTasksClient(
        OpenMuseTasksConfig(enabled=True, base_url="http://127.0.0.1:8787", access_key="test"),
        transport=httpx.MockTransport(handler),
    )
    coordinator = OpenMuseTaskCoordinator(
        GeneralTaskStore(store),
        client,
        enabled=True,
        is_general_task=lambda _item: True,
        verify_sandbox=lambda item: SandboxVerification(item["id"], True, "sandbox-check-1"),
    )

    first = run(coordinator.submit(owner(), task["id"]))
    second = run(coordinator.submit(owner(), task["id"]))

    assert first.state == second.state == "unknown"
    assert first.reason == second.reason == "upstream_reconciliation_unavailable"
    mapping = store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])
    assert mapping["status"] == "creating"
    assert mapping["upstream_task_id"] is None
    assert sum(method == "POST" and path == "/api/agent/tasks" for method, path in calls) == 1


def test_create_timeout_then_sync_reconciles_delayed_task_and_activates_without_duplicate(store):
    task = create_task(store, suffix="cold-start-recovery")
    calls = []
    upstream = {}
    delayed_create_has_started = False
    contracts = health_contracts()

    def handler(request):
        nonlocal delayed_create_has_started
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/health":
            return httpx.Response(200, json=contracts)
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "fake-session", "mode": "sample"})
        if request.method == "POST" and request.url.path == "/api/agent/tasks":
            body = json.loads(request.content)
            candidate = upstream_task_id(body["requestId"])
            upstream[candidate] = {
                "id": candidate,
                "title": body["title"],
                "prompt": body["prompt"],
                "kind": body["kind"],
                "taskRevision": 1,
                "requestFingerprint": v2_request_fingerprint(
                    body["prompt"], title=body["title"], task_input=body["input"]
                ),
                "input": body["input"],
                "status": "paused",
                "state": {},
            }
            delayed_create_has_started = True
            # Simulate the request timing out before the cold-started worker's
            # task becomes visible to its read API.
            raise httpx.ReadTimeout("OpenMuse is still starting the workspace")
        if request.method == "GET":
            candidate = request.url.path.rsplit("/", 1)[-1]
            if delayed_create_has_started:
                delayed_create_has_started = False
                return httpx.Response(404, json={"error": "not visible yet"})
            return httpx.Response(200, json={"task": upstream[candidate], "events": []})
        candidate = request.url.path.split("/")[-2]
        current = upstream[candidate]
        if request.url.path.endswith("/provision-sandbox"):
            current["state"] = {
                "sandboxProvision": {"status": "ready"},
                "sandboxProvisionCleanup": {"status": "complete"},
            }
            current["taskRevision"] += 1
            return httpx.Response(200, json={
                "taskId": candidate,
                "status": "ready",
                "attestation": {"version": "research-sandbox-v1", "verified": True},
            })
        if request.url.path.endswith("/control"):
            current["status"] = "queued"
            current["taskRevision"] += 1
            return httpx.Response(200, json=current)
        raise AssertionError(f"unexpected request {request.method} {request.url.path}")

    client = OpenMuseTasksClient(
        OpenMuseTasksConfig(enabled=True, base_url="http://127.0.0.1:8787", access_key="test", timeout_seconds=60),
        transport=httpx.MockTransport(handler),
    )
    coordinator = OpenMuseTaskCoordinator(
        GeneralTaskStore(store),
        client,
        enabled=True,
        is_general_task=lambda item: item.get("id") == task["id"],
        verify_sandbox=lambda item: SandboxVerification(item["id"], True, "sandbox-check-cold-start"),
    )

    first = run(coordinator.submit(owner(), task["id"]))
    assert first.state == "unknown"
    mapping_after_timeout = store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])
    assert mapping_after_timeout["upstream_task_id"] is None
    assert mapping_after_timeout["integration_phase"] == "create_unknown"

    recovered = run(coordinator.sync(owner(), task["id"]))

    assert recovered.state == "reconciled"
    assert recovered.upstream_status == "queued"
    mapping = store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])
    assert mapping["integration_phase"] == "active"
    assert mapping["upstream_task_id"] == upstream_task_id(mapping["request_id"])
    assert sum(method == "POST" and path == "/api/agent/tasks" for method, path in calls) == 1
    assert sum(method == "POST" and path.endswith("/provision-sandbox") for method, path in calls) == 1
    assert sum(method == "POST" and path.endswith("/control") for method, path in calls) == 1


@pytest.mark.parametrize("tamper", ["prompt", "title", "input"])
def test_timeout_with_candidate_id_but_mismatched_payload_stays_unknown(store, tamper):
    task = create_task(store)
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/health":
            return httpx.Response(200, json=health_contracts())
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "fake-session", "mode": "sample"})
        if request.method == "POST":
            raise httpx.ReadTimeout("uncertain create outcome")
        candidate = request.url.path.rsplit("/", 1)[-1]
        returned_task = {
            "id": candidate,
            "title": "Summarize public software documentation",
            "prompt": "Summarize public software documentation",
            "kind": "research",
            "taskRevision": 1,
            "requestFingerprint": v2_request_fingerprint(
                "Summarize public software documentation",
                title="Summarize public software documentation",
            ),
            "input": {"research_scope": "general"},
            "status": "paused",
        }
        if tamper == "input":
            returned_task["input"] = {"research_scope": "finance"}
        else:
            returned_task[tamper] = "Different upstream value"
        return httpx.Response(
            200,
            json={"task": returned_task, "events": []},
        )

    client = OpenMuseTasksClient(
        OpenMuseTasksConfig(enabled=True, base_url="http://127.0.0.1:8787", access_key="test"),
        transport=httpx.MockTransport(handler),
    )
    coordinator = OpenMuseTaskCoordinator(
        GeneralTaskStore(store),
        client,
        enabled=True,
        is_general_task=lambda _item: True,
        verify_sandbox=lambda item: SandboxVerification(item["id"], True, "sandbox-check-1"),
    )

    result = run(coordinator.submit(owner(), task["id"]))

    assert result.state == "unknown"
    assert result.reason == "upstream_task_does_not_match_original_request"
    mapping = store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])
    assert mapping["upstream_task_id"] is None
    assert mapping["status"] == "creating"
    assert sum(method == "POST" and path == "/api/agent/tasks" for method, path in calls) == 1


def test_concurrent_unknown_reconciliation_binds_same_candidate_only(store):
    task = create_task(store)
    calls = []
    payload = {
            "kind": "research",
            "taskRevision": 1,
        "prompt": task["query"].strip(),
        "title": task["query"].strip()[:160],
        "input": {"research_scope": "general"},
    }
    digest = v2_request_fingerprint(task["query"])
    mapping, created = store.create_openmuse_task_mapping(
        LOCAL_OWNER_ID,
        task["id"],
        payload_sha256=digest,
    )
    assert created
    store.update_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"], status="unknown")
    candidate = upstream_task_id(mapping["request_id"])

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/health":
            return httpx.Response(200, json=health_contracts())
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "fake-session", "mode": "sample"})
        assert request.method == "GET"
        assert request.url.path == f"/api/agent/tasks/{candidate}"
        return httpx.Response(
            200,
            json={
                "task": {
                    "id": candidate,
                    "title": payload["title"],
                    "prompt": payload["prompt"],
                    "kind": "research",
                    "taskRevision": 1,
                    "requestFingerprint": digest,
                    "input": {"research_scope": "general"},
                    "status": "paused",
                },
                "events": [],
            },
        )

    client = OpenMuseTasksClient(
        OpenMuseTasksConfig(enabled=True, base_url="http://127.0.0.1:8787", access_key="test"),
        transport=httpx.MockTransport(handler),
    )
    coordinator = OpenMuseTaskCoordinator(
        GeneralTaskStore(store),
        client,
        enabled=True,
        is_general_task=lambda _item: True,
        verify_sandbox=lambda item: SandboxVerification(item["id"], True, "sandbox-check-1"),
    )

    async def concurrent_submit():
        return await asyncio.gather(
            coordinator.submit(owner(), task["id"]),
            coordinator.submit(owner(), task["id"]),
        )

    first, second = run(concurrent_submit())

    assert {first.state, second.state} <= {"reconciled", "synced", "unknown"}
    assert store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])["upstream_task_id"] == candidate
    assert sum(method == "POST" and path == "/api/agent/tasks" for method, path in calls) == 0
    assert sum(path == f"/api/agent/tasks/{candidate}" for _, path in calls) >= 2


def test_concurrent_first_submit_posts_once_and_loser_cannot_overwrite_winner(store):
    task = create_task(store)
    post_started = asyncio.Event()
    release_post = asyncio.Event()
    loser_read_candidate = asyncio.Event()
    calls = []

    async def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/health":
            return httpx.Response(200, json=health_contracts())
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "fake-session", "mode": "sample"})
        if request.method == "POST":
            if request.url.path == "/api/agent/tasks":
                body = json.loads(request.content)
                candidate = upstream_task_id(body["requestId"])
                post_started.set()
                await release_post.wait()
                return httpx.Response(
                    201,
                    json={
                        "id": candidate,
                        "title": body["title"],
                        "prompt": body["prompt"],
                        "kind": body["kind"],
                        "taskRevision": 1,
                        "requestFingerprint": v2_request_fingerprint(
                            body["prompt"], title=body["title"], task_input=body["input"]
                        ),
                        "input": body["input"],
                        "status": "paused",
                    },
                )
            if request.url.path.endswith("/provision-sandbox"):
                candidate = request.url.path.split("/")[-2]
                return httpx.Response(
                    200,
                    json={"taskId": candidate, "status": "ready", "attestation": {"version": "research-sandbox-v1", "verified": True}},
                )
            candidate = request.url.path.split("/")[-2]
            return httpx.Response(
                200,
                json={
                    "id": candidate,
                    "title": task["query"],
                    "prompt": task["query"],
                    "kind": "research",
                    "taskRevision": 2,
                    "requestFingerprint": v2_request_fingerprint(task["query"]),
                    "input": {"research_scope": "general"},
                    "status": "queued",
                },
            )
        assert request.method == "GET"
        candidate = request.url.path.rsplit("/", 1)[-1]
        loser_read_candidate.set()
        return httpx.Response(404, json={"error": "not found yet"})

    client = OpenMuseTasksClient(
        OpenMuseTasksConfig(enabled=True, base_url="http://127.0.0.1:8787", access_key="test"),
        transport=httpx.MockTransport(handler),
    )
    coordinator = OpenMuseTaskCoordinator(
        GeneralTaskStore(store),
        client,
        enabled=True,
        is_general_task=lambda _item: True,
        verify_sandbox=lambda item: SandboxVerification(item["id"], True, "sandbox-check-1"),
    )

    async def concurrent_first_submit():
        winner_call = asyncio.create_task(coordinator.submit(owner(), task["id"]))
        await post_started.wait()
        loser_call = asyncio.create_task(coordinator.submit(owner(), task["id"]))
        await loser_read_candidate.wait()
        loser = await loser_call
        mapping_before_winner = store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])
        release_post.set()
        winner = await winner_call
        return winner, loser, mapping_before_winner

    winner, loser, mapping_before_winner = run(concurrent_first_submit())

    assert winner.state == "submitted"
    assert loser.state == "unknown"
    assert mapping_before_winner["status"] == "creating"
    assert mapping_before_winner["upstream_task_id"] is None
    mapping = store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])
    assert mapping["status"] == "queued"
    assert mapping["upstream_task_id"] == upstream_task_id(mapping["request_id"])
    assert sum(method == "POST" and path == "/api/agent/tasks" for method, path in calls) == 1


def test_legacy_upstream_contract_never_attempts_candidate_read_or_post(store):
    task = create_task(store)
    digest = v2_request_fingerprint(task["query"])
    store.create_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"], payload_sha256=digest)
    store.update_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"], status="unknown")
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(
            200,
            json={"taskCreateContract": "openmuse-task-create:v1:a4944f4c471d3dc93b8bfc060b5da309b5216ab2"},
        )

    client = OpenMuseTasksClient(
        OpenMuseTasksConfig(enabled=True, base_url="http://127.0.0.1:8787", access_key="test"),
        transport=httpx.MockTransport(handler),
    )
    coordinator = OpenMuseTaskCoordinator(
        GeneralTaskStore(store),
        client,
        enabled=True,
        is_general_task=lambda _item: True,
        verify_sandbox=lambda item: SandboxVerification(item["id"], True, "sandbox-check-1"),
    )

    result = run(coordinator.submit(owner(), task["id"]))

    assert result.state == "unknown"
    assert result.reason == "unsupported_upstream_contract"
    assert calls == [("GET", "/api/health")]
    assert store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])["upstream_task_id"] is None


def test_changed_server_payload_conflicts_with_existing_mapping(store):
    task = create_task(store)
    calls = []

    def handler(request):
        calls.append(request.url.path)
        raise AssertionError("conflicting mapping must fail before provider access")

    client = OpenMuseTasksClient(
        OpenMuseTasksConfig(enabled=True, base_url="http://127.0.0.1:8787", access_key="test"),
        transport=httpx.MockTransport(handler),
    )

    coordinator = OpenMuseTaskCoordinator(
        GeneralTaskStore(store, query_override="Changed server-owned payload"),
        client,
        enabled=True,
        is_general_task=lambda _item: True,
        verify_sandbox=lambda item: SandboxVerification(item["id"], True, "sandbox-check-1"),
    )
    store.create_openmuse_task_mapping(
        LOCAL_OWNER_ID,
        task["id"],
        payload_sha256=hashlib.sha256(
            json.dumps(
                {
                    "goalId": None,
                    "input": {"research_scope": "general"},
                    "kind": "agent",
                    "milestoneId": None,
                    "prompt": task["query"].strip(),
                    "title": task["query"].strip()[:160],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    )

    with pytest.raises(ConflictError, match="payload|idempotency|request"):
        run(coordinator.submit(owner(), task["id"]))
    assert calls == []


def test_cancel_is_not_reported_cancelled_without_upstream_confirmation(store):
    task = create_task(store)
    store.create_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"], payload_sha256="1" * 64)
    store.update_openmuse_task_mapping(
        LOCAL_OWNER_ID,
        task["id"],
        upstream_task_id="upstream-1",
        status="running",
    )
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "fake-session", "mode": "sample"})
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"task": {"id": "upstream-1", "kind": "agent", "taskRevision": 1, "status": "running"}, "events": []},
            )
        return httpx.Response(200, json={"id": "upstream-1", "kind": "agent", "taskRevision": 1, "status": "running"})

    client = OpenMuseTasksClient(
        OpenMuseTasksConfig(enabled=True, base_url="http://127.0.0.1:8787", access_key="test"),
        transport=httpx.MockTransport(handler),
    )
    coordinator = OpenMuseTaskCoordinator(
        store,
        client,
        enabled=True,
        is_general_task=lambda _item: True,
        verify_sandbox=lambda item: SandboxVerification(item["id"], True, "sandbox-check-1"),
    )

    result = run(coordinator.cancel(owner(), task["id"]))

    assert result.state == "cancel_unconfirmed"
    assert result.upstream_status == "running"
    assert store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])["status"] == "running"
    assert any(path.endswith("/control") for _method, path in calls)
    status_events = [event for event in store.list_events(LOCAL_OWNER_ID, task["id"]) if event["kind"] == "openmuse_status"]
    assert status_events[-1]["status"] == "running"


@pytest.mark.parametrize(
    "health",
    [
        {"taskCreateContract": TASK_CREATE_CONTRACT},
        {
            "taskCreateContract": TASK_CREATE_CONTRACT,
            "researchSandboxContract": "openmuse-research-sandbox:v0:unheld",
        },
        {
            "taskCreateContract": TASK_CREATE_CONTRACT,
            "researchSandboxContract": RESEARCH_SANDBOX_CONTRACT,
        },
    ],
)
def test_research_contract_mismatch_blocks_before_mapping_or_create(store, health):
    task = create_task(store, suffix="contract-mismatch")
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json=health)

    client = OpenMuseTasksClient(
        OpenMuseTasksConfig(enabled=True, base_url="http://127.0.0.1:8787", access_key="test"),
        transport=httpx.MockTransport(handler),
    )
    coordinator = OpenMuseTaskCoordinator(
        GeneralTaskStore(store),
        client,
        enabled=True,
        is_general_task=lambda item: item.get("id") == task["id"],
        verify_sandbox=lambda item: SandboxVerification(item["id"], True, "trusted-policy-check"),
    )

    result = run(coordinator.submit(owner(), task["id"]))

    assert result.state == "blocked"
    assert result.reason == "unsupported_upstream_contract"
    assert calls == [("GET", "/api/health")]
    assert store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"]) is None


def test_stale_ready_state_is_not_attestation_when_upstream_rejects_resume(store):
    task = create_task(store, suffix="stale-ready")
    calls = []
    upstream = {}

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/health":
            return httpx.Response(200, json=health_contracts())
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "fake-session", "mode": "sample"})
        if request.method == "POST" and request.url.path == "/api/agent/tasks":
            body = json.loads(request.content)
            upstream_id = upstream_task_id(body["requestId"])
            upstream[upstream_id] = {
                "id": upstream_id,
                "title": body["title"],
                "prompt": body["prompt"],
                "kind": "research",
                "taskRevision": 1,
                "requestFingerprint": v2_request_fingerprint(
                    body["prompt"], title=body["title"], task_input=body["input"]
                ),
                "input": body["input"],
                "status": "paused",
                "state": {},
            }
            return httpx.Response(201, json=upstream[upstream_id])
        upstream_id = (
            request.url.path.split("/")[-2]
            if request.method == "POST"
            else request.url.path.split("/")[-1]
        )
        current = upstream[upstream_id]
        if request.method == "POST" and request.url.path.endswith("/provision-sandbox"):
            current["state"] = {
                "sandboxProvision": {"status": "ready"},
                "sandboxProvisionCleanup": {"status": "complete"},
            }
            current["taskRevision"] += 1
            raise httpx.ReadTimeout("provision completed but response was lost")
        if request.method == "POST" and request.url.path.endswith("/control"):
            return httpx.Response(409, json={"error": "verified research sandbox expired"})
        return httpx.Response(200, json={"task": current, "events": []})

    client = OpenMuseTasksClient(
        OpenMuseTasksConfig(enabled=True, base_url="http://127.0.0.1:8787", access_key="test"),
        transport=httpx.MockTransport(handler),
    )
    coordinator = OpenMuseTaskCoordinator(
        GeneralTaskStore(store),
        client,
        enabled=True,
        is_general_task=lambda item: item.get("id") == task["id"],
        verify_sandbox=lambda item: SandboxVerification(item["id"], True, "trusted-policy-check"),
    )

    result = run(coordinator.submit(owner(), task["id"]))

    assert result.state == "unknown"
    assert result.reason == "resume_outcome_unconfirmed"
    assert sum(method == "POST" and path == "/api/agent/tasks" for method, path in calls) == 1
    assert sum(method == "POST" and path.endswith("/provision-sandbox") for method, path in calls) == 1
    assert sum(method == "POST" and path.endswith("/control") for method, path in calls) == 1


def test_cancel_supports_bound_research_task_without_provision_or_resume(store):
    task = create_task(store, suffix="cancel-research")
    store.create_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"], payload_sha256="2" * 64)
    upstream_id = "upstream-research"
    store.update_openmuse_task_mapping(
        LOCAL_OWNER_ID, task["id"], upstream_task_id=upstream_id, status="paused"
    )
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "fake-session", "mode": "sample"})
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "task": {
                        "id": upstream_id,
                        "kind": "research",
                        "taskRevision": 1,
                        "input": {"research_scope": "general"},
                        "status": "paused",
                    },
                    "events": [],
                },
            )
        assert request.url.path == f"/api/agent/tasks/{upstream_id}/control"
        assert json.loads(request.content) == {"action": "cancel"}
        return httpx.Response(
            200,
            json={
                "id": upstream_id,
                "kind": "research",
                "taskRevision": 1,
                "input": {"research_scope": "general"},
                "status": "cancelled",
            },
        )

    client = OpenMuseTasksClient(
        OpenMuseTasksConfig(enabled=True, base_url="http://127.0.0.1:8787", access_key="test"),
        transport=httpx.MockTransport(handler),
    )
    coordinator = OpenMuseTaskCoordinator(store, client, enabled=True)

    result = run(coordinator.cancel(owner(), task["id"]))

    assert result.state == "cancelled"
    assert [
        path for method, path in calls if method == "POST" and path != "/api/session"
    ] == [
        f"/api/agent/tasks/{upstream_id}/control"
    ]


@pytest.mark.parametrize("crash_point", ["after_held", "after_provision"])
def test_restart_recovers_durable_research_stage_without_repeating_completed_posts(
    store, crash_point, monkeypatch
):
    class SimulatedProcessCrash(BaseException):
        pass

    task = create_task(store, suffix=f"restart-{crash_point}")
    calls = []
    upstream = {}
    contracts = health_contracts()

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/health":
            return httpx.Response(200, json=contracts)
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "restart-session", "mode": "sample"})
        if request.method == "POST" and request.url.path == "/api/agent/tasks":
            body = json.loads(request.content)
            upstream_id = upstream_task_id(body["requestId"])
            upstream[upstream_id] = {
                "id": upstream_id,
                "title": body["title"],
                "prompt": body["prompt"],
                "kind": "research",
                "taskRevision": 1,
                "requestFingerprint": v2_request_fingerprint(
                    body["prompt"], title=body["title"], task_input=body["input"]
                ),
                "input": body["input"],
                "status": "paused",
                "state": {},
            }
            return httpx.Response(201, json=upstream[upstream_id])
        upstream_id = (
            request.url.path.split("/")[-2]
            if request.method == "POST"
            else request.url.path.split("/")[-1]
        )
        current = upstream[upstream_id]
        if request.method == "GET":
            return httpx.Response(200, json={"task": current, "events": []})
        if request.url.path.endswith("/provision-sandbox"):
            current["state"] = {
                "sandboxProvision": {"status": "ready"},
                "sandboxProvisionCleanup": {"status": "complete"},
            }
            current["taskRevision"] += 1
            if crash_point == "after_provision" and sum(
                path.endswith("/provision-sandbox") for _method, path in calls
            ) == 1:
                raise SimulatedProcessCrash("OpenMuse completed provision before process exit")
            return httpx.Response(
                200,
                json={
                    "taskId": upstream_id,
                    "status": "ready",
                    "attestation": {"version": "research-sandbox-v1", "verified": True},
                },
            )
        current["status"] = "queued"
        current["taskRevision"] += 1
        return httpx.Response(200, json=current)

    def new_coordinator(active_store):
        client = OpenMuseTasksClient(
            OpenMuseTasksConfig(
                enabled=True, base_url="http://127.0.0.1:8787", access_key="test"
            ),
            transport=httpx.MockTransport(handler),
        )
        coordinator = OpenMuseTaskCoordinator(
            GeneralTaskStore(active_store),
            client,
            enabled=True,
            is_general_task=lambda item: item.get("id") == task["id"],
            verify_sandbox=lambda item: SandboxVerification(
                item["id"], True, "trusted-policy-check"
            ),
        )
        return client, coordinator

    if crash_point == "after_held":
        original_apply = store.apply_openmuse_task_projection
        crashed = False

        def crash_after_commit(*args, **kwargs):
            nonlocal crashed
            result = original_apply(*args, **kwargs)
            if not crashed and kwargs.get("status") == "paused":
                crashed = True
                raise SimulatedProcessCrash("process stopped after atomic projection and event commit")
            return result

        monkeypatch.setattr(store, "apply_openmuse_task_projection", crash_after_commit)

    first_client, first = new_coordinator(store)
    with pytest.raises(SimulatedProcessCrash):
        run(first.submit(owner(), task["id"]))
    mapping = store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])
    assert mapping["upstream_task_id"] == upstream_task_id(mapping["request_id"])
    assert mapping["integration_phase"] == (
        "held" if crash_point == "after_held" else "provisioning"
    )
    run(first_client.close())

    # Re-open the actual SQLite file to model a process restart, preserving only
    # durable local mapping state and the fake upstream's committed state.
    restarted_db = Database(sqlite_path=store.db.sqlite_path)
    restarted_db.initialize()
    restarted_store = FinancialBotStore(restarted_db)
    second_client, second = new_coordinator(restarted_store)

    result = run(second.submit(owner(), task["id"]))

    assert result.state == "submitted"
    assert result.upstream_status == "queued"
    assert sum(method == "POST" and path == "/api/agent/tasks" for method, path in calls) == 1
    assert sum(method == "POST" and path.endswith("/provision-sandbox") for method, path in calls) == 1
    assert sum(method == "POST" and path.endswith("/control") for method, path in calls) == 1
    assert restarted_store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])[
        "integration_phase"
    ] == "active"
    run(second_client.close())


def test_late_provision_response_cannot_reopen_resume_stage(store):
    task = create_task(store, suffix="late-provision")
    with store.db.connect(owner_id=LOCAL_OWNER_ID, write=True) as conn:
        conn.execute("UPDATE fb_tasks SET research_mode = 'general' WHERE owner_id = ? AND id = ?", (LOCAL_OWNER_ID, task["id"]))
    payload, fingerprint = OpenMuseTaskCoordinator._payload(task)
    upstream_id = "late-provision-upstream"
    mapping, created = store.create_openmuse_task_mapping(
        LOCAL_OWNER_ID, task["id"], payload_sha256=fingerprint
    )
    assert created
    store.update_openmuse_task_mapping(
        LOCAL_OWNER_ID, task["id"], upstream_task_id=upstream_id,
        status="paused",
    )
    assert store.compare_and_set_openmuse_integration_phase(
        LOCAL_OWNER_ID, task["id"], expected="prepared", value="held"
    )
    provision_entered = asyncio.Event()
    release_provision = asyncio.Event()
    calls = []
    upstream = {
        "id": upstream_id,
        "kind": "research",
        "taskRevision": 1,
        "prompt": payload["prompt"],
        "title": payload["title"],
        "input": payload["input"],
        "requestFingerprint": fingerprint,
        "status": "paused",
        "state": {},
    }

    async def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/health":
            return httpx.Response(200, json=health_contracts())
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "race-session", "mode": "sample"})
        if request.method == "GET":
            return httpx.Response(200, json={"task": dict(upstream), "events": []})
        if request.url.path.endswith("/provision-sandbox"):
            upstream["state"] = {
                "sandboxProvision": {"status": "ready"},
                "sandboxProvisionCleanup": {"status": "complete"},
            }
            upstream["taskRevision"] += 1
            provision_entered.set()
            await release_provision.wait()
            return httpx.Response(200, json={
                "taskId": upstream_id, "status": "ready",
                "attestation": {"version": "research-sandbox-v1", "verified": True},
            })
        upstream["status"] = "queued"
        upstream["taskRevision"] += 1
        return httpx.Response(200, json=dict(upstream))

    def coordinator():
        client = OpenMuseTasksClient(
            OpenMuseTasksConfig(enabled=True, base_url="http://127.0.0.1:8787", access_key="test"),
            transport=httpx.MockTransport(handler),
        )
        return client, OpenMuseTaskCoordinator(
            store, client, enabled=True,
            is_general_task=lambda item: item.get("id") == task["id"],
            verify_sandbox=lambda item: SandboxVerification(item["id"], True, "test-verifier"),
        )

    async def race():
        first_client, first = coordinator()
        second_client, second = coordinator()
        first_submit = asyncio.create_task(first.submit(owner(), task["id"]))
        await asyncio.wait_for(provision_entered.wait(), timeout=2)
        second_result = await second.submit(owner(), task["id"])
        release_provision.set()
        first_result = await first_submit
        await first_client.close()
        await second_client.close()
        return first_result, second_result

    run(race())
    assert sum(method == "POST" and path.endswith("/provision-sandbox") for method, path in calls) == 1
    assert sum(method == "POST" and path.endswith("/control") for method, path in calls) == 1
    assert store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])["integration_phase"] == "active"
    with pytest.raises(ValueError, match="invalid OpenMuse integration phase"):
        store.compare_and_set_openmuse_integration_phase(
            LOCAL_OWNER_ID, task["id"], expected="active", value="held"
        )


@pytest.mark.parametrize(
    ("http_status", "expected_phase", "expected_result"),
    [(401, "rejected", "blocked"), (429, "create_unknown", "unknown")],
)
def test_create_http_rejection_certainty_is_conservative(
    store, http_status, expected_phase, expected_result
):
    task = create_task(store, suffix=f"http-{http_status}")
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/health":
            return httpx.Response(200, json=health_contracts())
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "http-status-session", "mode": "sample"})
        if request.method == "GET":
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(http_status, json={"error": "request rejected or rate limited"})

    client = OpenMuseTasksClient(
        OpenMuseTasksConfig(enabled=True, base_url="http://127.0.0.1:8787", access_key="test"),
        transport=httpx.MockTransport(handler),
    )
    coordinator = OpenMuseTaskCoordinator(
        GeneralTaskStore(store), client, enabled=True,
        is_general_task=lambda item: item.get("id") == task["id"],
        verify_sandbox=lambda item: SandboxVerification(item["id"], True, "test-verifier"),
    )
    result = run(coordinator.submit(owner(), task["id"]))
    mapping = store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])
    assert result.state == expected_result
    assert mapping["integration_phase"] == expected_phase
    assert sum(method == "POST" and path == "/api/agent/tasks" for method, path in calls) == 1


def test_external_state_writer_is_not_an_available_projection_surface(store):
    task = create_task(store, suffix="untrusted-state-writer")
    store.create_openmuse_task_mapping(
        LOCAL_OWNER_ID, task["id"], payload_sha256="a" * 64
    )
    store.update_openmuse_task_mapping(
        LOCAL_OWNER_ID, task["id"], upstream_task_id="upstream-untrusted-writer"
    )
    calls = []
    with pytest.raises(TypeError, match="state_writer"):
        OpenMuseTaskCoordinator(
            store,
            client=None,
            state_writer=lambda *_args: calls.append("called"),
            state_writer_enabled=True,
        )
    assert calls == []
    assert store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])["upstream_task_revision"] is None


def test_stale_sync_projection_cannot_regress_status_or_emit_old_event(store):
    task = create_task(store, suffix="stale-sync")
    payload, fingerprint = OpenMuseTaskCoordinator._payload(task)
    upstream_id = "stale-sync-upstream"
    store.create_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"], payload_sha256=fingerprint)
    store.update_openmuse_task_mapping(
        LOCAL_OWNER_ID, task["id"], upstream_task_id=upstream_id, status="queued"
    )
    store.compare_and_set_openmuse_integration_phase(
        LOCAL_OWNER_ID, task["id"], expected="prepared", value="creating"
    )
    store.compare_and_set_openmuse_integration_phase(
        LOCAL_OWNER_ID, task["id"], expected="creating", value="held"
    )
    store.compare_and_set_openmuse_integration_phase(
        LOCAL_OWNER_ID, task["id"], expected="held", value="provisioning"
    )
    store.compare_and_set_openmuse_integration_phase(
        LOCAL_OWNER_ID, task["id"], expected="provisioning", value="provision_unknown"
    )
    store.compare_and_set_openmuse_integration_phase(
        LOCAL_OWNER_ID, task["id"], expected="provision_unknown", value="provisioned"
    )
    store.compare_and_set_openmuse_integration_phase(
        LOCAL_OWNER_ID, task["id"], expected="provisioned", value="resuming"
    )
    store.compare_and_set_openmuse_integration_phase(
        LOCAL_OWNER_ID, task["id"], expected="resuming", value="active"
    )
    entered = asyncio.Event()
    release_stale = asyncio.Event()
    request_index = 0
    state_writes = []

    async def handler(request):
        nonlocal request_index
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "stale-sync-session", "mode": "sample"})
        request_index += 1
        index = request_index
        if index == 1:
            entered.set()
            await release_stale.wait()
            status = "queued"
        else:
            status = "running"
        return httpx.Response(200, json={"task": {
            "id": upstream_id, "kind": "research", "prompt": payload["prompt"],
            "taskRevision": 11 if index == 1 else 12,
            "title": payload["title"], "input": payload["input"],
            "requestFingerprint": fingerprint, "status": status,
        }, "events": []})

    def coordinator():
        client = OpenMuseTasksClient(
            OpenMuseTasksConfig(enabled=True, base_url="http://127.0.0.1:8787", access_key="test"),
            transport=httpx.MockTransport(handler),
        )
        return client, OpenMuseTaskCoordinator(store, client, enabled=True)

    async def race():
        first_client, first = coordinator()
        second_client, second = coordinator()
        stale = asyncio.create_task(first.sync(owner(), task["id"]))
        await asyncio.wait_for(entered.wait(), timeout=2)
        fresh = await second.sync(owner(), task["id"])
        release_stale.set()
        await stale
        await first_client.close()
        await second_client.close()
        return fresh

    fresh = run(race())
    assert fresh.upstream_status == "running"
    assert store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])["status"] == "running"
    status_events = [event for event in store.list_events(LOCAL_OWNER_ID, task["id"]) if event["kind"] == "openmuse_status"]
    assert [event["status"] for event in status_events] == ["running"]


def test_succeeded_openmuse_projection_survives_local_task_reload(store):
    task = create_task(store, suffix="reload-completed", research_mode="openmuse_shared")
    payload, fingerprint = OpenMuseTaskCoordinator._payload(task)
    upstream_id = "reload-completed-upstream"
    store.create_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"], payload_sha256=fingerprint)
    store.update_openmuse_task_mapping(
        LOCAL_OWNER_ID, task["id"], upstream_task_id=upstream_id, status="running"
    )

    async def handler(request):
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "reload-session", "mode": "sample"})
        assert request.url.path == f"/api/agent/tasks/{upstream_id}"
        return httpx.Response(200, json={"task": {
            "id": upstream_id,
            "kind": "research",
            "prompt": payload["prompt"],
            "title": payload["title"],
            "input": payload["input"],
            "requestFingerprint": v2_request_fingerprint(payload["prompt"], title=payload["title"]),
            "taskRevision": 7,
            "status": "succeeded",
            "result": "Full persisted research report body",
            "evidence": [
                {
                    "id": "source-1",
                    "kind": "web",
                    "title": "Public source",
                    "excerpt": "The source says this.",
                    "url": "https://example.com/report",
                    "internal_state": {"financial_state": "must not be projected"},
                },
                {
                    "id": "source-2",
                    "kind": "file",
                    "title": "Imported public report",
                    "excerpt": "Another supported source kind.",
                    "url": "file://private/path",
                },
                {"id": "source-3", "kind": "unknown", "title": "Do not project"},
                "malformed evidence",
            ],
        }, "events": []})

    client = OpenMuseTasksClient(
        OpenMuseTasksConfig(enabled=True, base_url="http://127.0.0.1:8787", access_key="test"),
        transport=httpx.MockTransport(handler),
    )
    coordinator = OpenMuseTaskCoordinator(
        store, client, enabled=True,
        is_general_task=lambda item: item.get("research_mode") == "openmuse_shared",
    )
    synced = run(coordinator.sync(owner(), task["id"]))
    reloaded_store = FinancialBotStore(Database(sqlite_path=store.db.sqlite_path))
    reloaded = reloaded_store.get_task(LOCAL_OWNER_ID, task["id"])
    run(client.close())

    assert synced.state == "synced"
    assert synced.upstream_status == "succeeded"
    assert reloaded["status"] == "completed"
    assert reloaded["result"] == {
        "summary": "Full persisted research report body",
        "intent": "openmuse_shared_research",
        "evidence": [
            {
                "id": "source-1",
                "kind": "web",
                "title": "Public source",
                "excerpt": "The source says this.",
                "url": "https://example.com/report",
            },
            {
                "id": "source-2",
                "kind": "file",
                "title": "Imported public report",
                "excerpt": "Another supported source kind.",
            },
        ],
    }


def test_duplicate_succeeded_projection_hydrates_task_created_before_projection_persistence(store):
    task = create_task(store, suffix="hydrate-duplicate", research_mode="openmuse_shared")
    projection = {
        "task": {"status": "succeeded", "result": "Report body", "taskRevision": 4},
        "events": [],
    }
    task_payload, fingerprint = OpenMuseTaskCoordinator._payload(task)
    upstream_id = "hydrate-duplicate-upstream"
    store.create_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"], payload_sha256=fingerprint)
    store.update_openmuse_task_mapping(
        LOCAL_OWNER_ID, task["id"], upstream_task_id=upstream_id, status="succeeded"
    )
    canonical_projection = json.dumps(projection["task"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical_projection.encode("utf-8")).hexdigest()
    store.apply_openmuse_task_projection(
        LOCAL_OWNER_ID, task["id"], revision=4, projection_sha256=digest, status="succeeded"
    )

    class ExistingProjectionClient:
        async def get_task(self, _owner, _task_id):
            return projection

    coordinator = OpenMuseTaskCoordinator(
        store, ExistingProjectionClient(), enabled=True,
        is_general_task=lambda item: item.get("research_mode") == "openmuse_shared",
    )
    synced = run(coordinator.sync(owner(), task["id"]))
    reloaded = FinancialBotStore(Database(sqlite_path=store.db.sqlite_path)).get_task(LOCAL_OWNER_ID, task["id"])

    assert synced.upstream_status == "succeeded"
    assert reloaded["status"] == "completed"
    assert reloaded["result"] == {"summary": "Report body", "intent": "openmuse_shared_research"}


def test_restart_recovers_bound_creating_mapping_by_exact_get(store):
    task = create_task(store, suffix="bound-creating")
    with store.db.connect(owner_id=LOCAL_OWNER_ID, write=True) as conn:
        conn.execute("UPDATE fb_tasks SET research_mode = 'general' WHERE owner_id = ? AND id = ?", (LOCAL_OWNER_ID, task["id"]))
    payload, fingerprint = OpenMuseTaskCoordinator._payload(task)
    upstream_id = "bound-creating-upstream"
    store.create_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"], payload_sha256=fingerprint)
    store.compare_and_set_openmuse_integration_phase(
        LOCAL_OWNER_ID, task["id"], expected="prepared", value="creating"
    )
    store.update_openmuse_task_mapping(
        LOCAL_OWNER_ID, task["id"], upstream_task_id=upstream_id, status="paused"
    )
    calls = []
    upstream = {"id": upstream_id, "kind": "research", "taskRevision": 1, "prompt": payload["prompt"],
                "title": payload["title"], "input": payload["input"],
                "requestFingerprint": fingerprint, "status": "paused", "state": {}}

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/health":
            return httpx.Response(200, json=health_contracts())
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "creating-restart-session", "mode": "sample"})
        if request.method == "GET":
            return httpx.Response(200, json={"task": upstream, "events": []})
        if request.url.path.endswith("/provision-sandbox"):
            upstream["state"] = {"sandboxProvision": {"status": "ready"},
                                 "sandboxProvisionCleanup": {"status": "complete"}}
            upstream["taskRevision"] += 1
            return httpx.Response(200, json={"taskId": upstream_id, "status": "ready",
                "attestation": {"version": "research-sandbox-v1", "verified": True}})
        upstream["status"] = "queued"
        upstream["taskRevision"] += 1
        return httpx.Response(200, json=upstream)

    client = OpenMuseTasksClient(
        OpenMuseTasksConfig(enabled=True, base_url="http://127.0.0.1:8787", access_key="test"),
        transport=httpx.MockTransport(handler),
    )
    coordinator = OpenMuseTaskCoordinator(
        GeneralTaskStore(store), client, enabled=True,
        is_general_task=lambda item: item.get("id") == task["id"],
        verify_sandbox=lambda item: SandboxVerification(item["id"], True, "test-verifier"),
    )
    result = run(coordinator.submit(owner(), task["id"]))
    assert result.state == "submitted"
    assert result.upstream_status == "queued"
    assert not any(method == "POST" and path == "/api/agent/tasks" for method, path in calls)
    assert store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])["integration_phase"] == "active"


def test_http_422_create_rejection_is_deterministic_not_permanently_unknown(store):
    task = create_task(store, suffix="deterministic-422")
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/health":
            return httpx.Response(200, json=health_contracts())
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "fake-session", "mode": "sample"})
        assert request.url.path == "/api/agent/tasks"
        return httpx.Response(422, json={"error": "Invalid request"})

    client = OpenMuseTasksClient(
        OpenMuseTasksConfig(enabled=True, base_url="http://127.0.0.1:8787", access_key="test"),
        transport=httpx.MockTransport(handler),
    )
    coordinator = OpenMuseTaskCoordinator(
        GeneralTaskStore(store),
        client,
        enabled=True,
        is_general_task=lambda item: item.get("id") == task["id"],
        verify_sandbox=lambda item: SandboxVerification(item["id"], True, "trusted-policy-check"),
    )

    result = run(coordinator.submit(owner(), task["id"]))

    assert result.state == "blocked"
    assert result.reason == "upstream_rejected_request"
    mapping = store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task["id"])
    assert mapping["integration_phase"] == "rejected"
    assert mapping["upstream_task_id"] is None
    assert sum(method == "POST" and path == "/api/agent/tasks" for method, path in calls) == 1


def test_payload_uses_javascript_utf16_prompt_and_title_boundaries():
    valid = OpenMuseTaskCoordinator._payload({"query": "😀" * 6000})
    assert valid is not None
    payload, _fingerprint = valid
    assert len(payload["prompt"].encode("utf-16-le")) // 2 == 12000
    assert len(payload["title"].encode("utf-16-le")) // 2 == 160
    assert OpenMuseTaskCoordinator._payload({"query": "😀" * 6001}) is None
