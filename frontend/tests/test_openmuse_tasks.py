import asyncio
import json

import httpx
import pytest

from api.financial_bot.auth import HOSTED_MODE, LOCAL_MODE, LOCAL_OWNER_ID, OwnerContext
from api.financial_bot.db import Database
from api.financial_bot.openmuse_client import OpenMuseClient
from api.financial_bot.openmuse_tasks import (
    OpenMuseTaskError,
    OpenMuseTasksClient,
    OpenMuseTasksConfig,
)
from api.financial_bot.openmuse_task_coordinator import TASK_CREATE_CONTRACT, TASK_REVISION_CONTRACT

RESEARCH_SANDBOX_CONTRACT = "openmuse-research-sandbox:v1:held-provision-attest"
from api.financial_bot.store import FinancialBotStore


def run(coro):
    return asyncio.run(coro)


def owner(mode=LOCAL_MODE, owner_id=LOCAL_OWNER_ID):
    return OwnerContext(owner_id=owner_id, mode=mode)


def config(**kwargs):
    values = {
        "enabled": True,
        "base_url": "http://127.0.0.1:8787",
        "access_key": "local-access-key",
    }
    values.update(kwargs)
    return OpenMuseTasksConfig(**values)


@pytest.fixture
def deletion_store(tmp_path):
    db = Database(sqlite_path=tmp_path / "openmuse-task-client.sqlite3")
    db.initialize()
    return FinancialBotStore(db)


def create_pending_mapping(store, *, suffix="delete", upstream_id="task-1"):
    import hashlib

    task, created = store.create_task(
        owner_id=LOCAL_OWNER_ID,
        query=f"private prompt {suffix}",
        session_id=None,
        language="en-US",
        research_mode="quick",
        idempotency_key=f"openmuse-delete-{suffix}",
    )
    assert created
    store.create_openmuse_task_mapping(
        LOCAL_OWNER_ID,
        task["id"],
        payload_sha256=hashlib.sha256(suffix.encode()).hexdigest(),
    )
    store.update_openmuse_task_mapping(
        LOCAL_OWNER_ID,
        task["id"],
        upstream_task_id=upstream_id,
        deletion_status="pending",
    )
    return task


def test_task_bridge_is_disabled_by_default_without_network_call(monkeypatch):
    monkeypatch.delenv("OPENMUSE_ENABLED", raising=False)
    monkeypatch.setenv("OPENMUSE_TASKS_ENABLED", "1")
    client = OpenMuseTasksClient(OpenMuseTasksConfig.from_env())

    with pytest.raises(OpenMuseTaskError, match="disabled"):
        run(client.create_research_task(owner(), "查找公开的通用技术资料", request_id="disabled"))


def test_task_bridge_uses_the_same_environment_switch_url_and_timeout_as_health(monkeypatch):
    monkeypatch.setenv("OPENMUSE_ENABLED", "yes")
    monkeypatch.setenv("OPENMUSE_BASE_URL", "http://127.0.0.1:9021")
    monkeypatch.setenv("OPENMUSE_TIMEOUT_SECONDS", "7.5")
    monkeypatch.setenv("OPENMUSE_TASKS_ENABLED", "0")
    monkeypatch.setenv("OPENMUSE_TASKS_TIMEOUT_SECONDS", "99")

    task_config = OpenMuseTasksConfig.from_env()
    health_client = OpenMuseClient.from_env()
    task_client = OpenMuseTasksClient(task_config)

    assert task_config.enabled is health_client.enabled is True
    assert task_config.base_url == health_client.base_url == "http://127.0.0.1:9021"
    assert task_config.timeout_seconds == health_client.timeout[0] == 7.5
    assert task_client._http.timeout.read == 7.5


def test_task_timeout_has_a_cold_start_default_and_can_be_overridden_independently(monkeypatch):
    monkeypatch.setenv("OPENMUSE_ENABLED", "true")
    monkeypatch.setenv("OPENMUSE_BASE_URL", "http://127.0.0.1:8787")
    monkeypatch.setenv("OPENMUSE_ACCESS_KEY", "local-test-key")
    monkeypatch.delenv("OPENMUSE_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("OPENMUSE_TASK_TIMEOUT_SECONDS", raising=False)

    task_config = OpenMuseTasksConfig.from_env()
    assert task_config.timeout_seconds == 60

    monkeypatch.setenv("OPENMUSE_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("OPENMUSE_TASK_TIMEOUT_SECONDS", "75")
    task_config = OpenMuseTasksConfig.from_env()
    assert task_config.timeout_seconds == 75
    assert OpenMuseClient.from_env().timeout[1] == 2


def test_http_client_does_not_trust_environment_proxy_settings():
    client = OpenMuseTasksClient(config())

    assert client._http._trust_env is False


def test_hosted_owner_is_rejected_before_any_request():
    def forbidden(request):
        raise AssertionError("hosted owner must not reach OpenMuse")

    client = OpenMuseTasksClient(
        config(),
        transport=httpx.MockTransport(forbidden),
    )

    with pytest.raises(OpenMuseTaskError, match="local_single_user_only"):
        run(client.create_research_task(owner(HOSTED_MODE, "supabase-user"), "research", request_id="hosted"))


def test_noncanonical_local_owner_is_rejected():
    def forbidden(request):
        raise AssertionError("unexpected owner must not reach OpenMuse")

    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(forbidden))

    with pytest.raises(OpenMuseTaskError, match="local_single_user_only"):
        run(client.get_task(owner(owner_id="client-chosen"), "task-1"))


def test_create_logs_in_then_posts_only_explicit_generic_research_and_never_retries():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/api/session":
            assert request.method == "POST"
            assert json.loads(request.content) == {"accessKey": "local-access-key"}
            return httpx.Response(200, json={"token": "private-session", "mode": "sample"})
        assert request.url.path == "/api/agent/tasks"
        assert request.method == "POST"
        assert request.headers["authorization"] == "Bearer private-session"
        assert json.loads(request.content) == {
            "requestId": "request-1",
            "title": "通用研究",
            "prompt": "总结公开的技术文档",
            "kind": "research",
            "input": {"research_scope": "general"},
        }
        return httpx.Response(
            201,
            json={
                "id": "task-1", "title": "通用研究", "prompt": "总结公开的技术文档",
                "kind": "research", "taskRevision": 1, "input": {"research_scope": "general"}, "status": "paused"
            },
        )

    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(handler))
    result = run(
        client.create_research_task(
            owner(),
            "总结公开的技术文档",
            title="通用研究",
            request_id="request-1",
        )
    )

    assert len(requests) == 2
    assert result["id"] == "task-1"
    assert result["kind"] == "research"
    assert "token" not in result


def test_shared_research_financial_input_is_allowlisted_and_echoed():
    calls = []
    financial_input = {"research_scope": "financial", "financial_bridge_required": True}

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "private-session", "mode": "sample"})
        assert request.url.path == "/api/agent/tasks/shared-research"
        body = json.loads(request.content)
        assert body == {
            "requestId": "financial-request",
            "title": "财务分析",
            "prompt": "贵州茅台未来三年盈利能力",
            "input": financial_input,
        }
        return httpx.Response(201, json={
            "id": "financial-task",
            "title": body["title"],
            "prompt": body["prompt"],
            "kind": "research",
            "taskRevision": 1,
            "input": financial_input,
            "status": "paused",
            "state": {"sharedResearchWorkspace": True},
        })

    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(handler))
    result = run(client.create_shared_research_task(
        owner(),
        "贵州茅台未来三年盈利能力",
        title="财务分析",
        request_id="financial-request",
        task_input=financial_input,
    ))
    assert result["input"] == financial_input
    assert result["shared_research_workspace"] is True
    assert calls == ["/api/session", "/api/agent/tasks/shared-research"]

    with pytest.raises(OpenMuseTaskError, match="invalid_task_request"):
        run(client.create_shared_research_task(
            owner(), "prompt", request_id="unsafe-input", task_input={"research_scope": "financial"}
        ))


def test_create_does_not_retry_uncertain_transport_failure():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "private-session", "mode": "live"})
        raise httpx.ReadTimeout("request timed out after reaching upstream")

    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(handler))

    with pytest.raises(OpenMuseTaskError, match="upstream_unavailable"):
        run(client.create_research_task(owner(), "research", request_id="same-id"))

    assert calls == ["/api/session", "/api/agent/tasks"]


def test_research_contract_reads_both_markers_from_one_health_response():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(
            200,
            json={
                "taskCreateContract": TASK_CREATE_CONTRACT,
                "researchSandboxContract": RESEARCH_SANDBOX_CONTRACT,
                "taskRevisionContract": TASK_REVISION_CONTRACT,
            },
        )

    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(handler))
    contracts = run(client.get_task_contracts(owner()))

    assert contracts == (TASK_CREATE_CONTRACT, RESEARCH_SANDBOX_CONTRACT, TASK_REVISION_CONTRACT)
    assert calls == [("GET", "/api/health")]


def test_research_create_requires_paused_task_and_never_accepts_legacy_agent():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "private-session", "mode": "sample"})
        assert request.method == "POST"
        assert json.loads(request.content) == {
            "requestId": "request-research",
            "title": "通用研究",
            "prompt": "总结公开的技术文档",
            "kind": "research",
            "input": {"research_scope": "general"},
        }
        return httpx.Response(
            201,
            json={
                "id": "task-research",
                "title": "通用研究",
                "prompt": "总结公开的技术文档",
                "kind": "research",
                "taskRevision": 1,
                "input": {"research_scope": "general"},
                "status": "paused",
            },
        )

    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(handler))
    result = run(
        client.create_research_task(
            owner(), "总结公开的技术文档", title="通用研究", request_id="request-research"
        )
    )

    assert result["kind"] == "research"
    assert result["status"] == "paused"
    assert calls == ["/api/session", "/api/agent/tasks"]


@pytest.mark.parametrize(
    ("task_id", "attestation"),
    [
        ("other-task", {"version": "research-sandbox-v1", "verified": True}),
        ("expected-task", {"version": "research-sandbox-v0", "verified": True}),
        ("expected-task", {"version": "research-sandbox-v1", "verified": False}),
    ],
)
def test_provision_rejects_unbound_or_unverified_attestation(task_id, attestation):
    def handler(request):
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "private-session", "mode": "sample"})
        return httpx.Response(
            200,
            json={"taskId": task_id, "status": "ready", "attestation": attestation},
        )

    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(handler))
    with pytest.raises(OpenMuseTaskError, match="invalid_response"):
        run(client.provision_research_sandbox(owner(), "expected-task"))


def test_provision_checks_task_identity_and_resume_requires_explicit_queued_confirmation():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path, request.content))
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "private-session", "mode": "sample"})
        if request.url.path.endswith("/provision-sandbox"):
            return httpx.Response(
                200,
                json={
                    "taskId": "task-research",
                    "status": "ready",
                    "attestation": {"version": "research-sandbox-v1", "verified": True},
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "task-research",
                "kind": "research",
                "taskRevision": 1,
                "status": "queued",
                "input": {"research_scope": "general"},
            },
        )

    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(handler))
    provision = run(client.provision_research_sandbox(owner(), "task-research"))
    resumed = run(client.resume_research_task(owner(), "task-research"))

    assert provision == {
        "task_id": "task-research",
        "status": "ready",
        "attestation_version": "research-sandbox-v1",
        "verified": True,
    }
    assert resumed["status"] == "queued"
    assert [call[1] for call in calls] == [
        "/api/session",
        "/api/agent/tasks/task-research/provision-sandbox",
        "/api/agent/tasks/task-research/control",
    ]
    assert json.loads(calls[-1][2]) == {"action": "resume"}


def test_create_401_is_not_retried_even_after_session_expiry():
    calls = []
    sessions = 0
    task_attempts = 0

    def handler(request):
        nonlocal sessions, task_attempts
        calls.append(request.url.path)
        if request.url.path == "/api/session":
            sessions += 1
            token = "expired-session" if sessions == 1 else "fresh-session"
            return httpx.Response(200, json={"token": token, "mode": "live"})
        task_attempts += 1
        if task_attempts == 1:
            return httpx.Response(401, json={"error": "expired"})
        return httpx.Response(
            201,
            json={"id": "task-1", "kind": "research", "taskRevision": 1, "input": {"research_scope": "general"}, "status": "paused"},
        )

    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(handler))

    with pytest.raises(OpenMuseTaskError, match="upstream_http_error"):
        run(client.create_research_task(owner(), "research", request_id="no-repeat"))

    assert calls == ["/api/session", "/api/agent/tasks"]
    # The next explicit operation logs in again instead of reusing the rejected token.
    result = run(client.create_research_task(owner(), "research", request_id="no-repeat"))
    assert result["id"] == "task-1"
    assert calls == ["/api/session", "/api/agent/tasks", "/api/session", "/api/agent/tasks"]


def test_create_sends_idempotency_in_upstream_request_body_not_header():
    captured = []

    def handler(request):
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "private-session", "mode": "live"})
        captured.append((dict(request.headers), json.loads(request.content)))
        return httpx.Response(
            201,
            json={"id": "task-2", "kind": "research", "taskRevision": 1, "input": {"research_scope": "general"}, "status": "paused"},
        )

    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(handler))
    run(client.create_research_task(owner(), "research", request_id="stable-request"))

    headers, body = captured[0]
    assert "x-request-id" not in headers
    assert body["requestId"] == "stable-request"


def test_repeated_explicit_request_id_uses_upstream_idempotency_without_hidden_retry():
    task_posts = []

    def handler(request):
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "private-session", "mode": "live"})
        task_posts.append(json.loads(request.content))
        return httpx.Response(
            201,
            json={"id": "same-task", "kind": "research", "taskRevision": 1, "input": {"research_scope": "general"}, "status": "paused"},
        )

    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(handler))
    first = run(client.create_research_task(owner(), "research", request_id="retry-key"))
    second = run(client.create_research_task(owner(), "research", request_id="retry-key"))

    assert first["id"] == second["id"] == "same-task"
    assert len(task_posts) == 2  # Only the two explicit caller operations.
    assert [body["requestId"] for body in task_posts] == ["retry-key", "retry-key"]


def test_get_and_cancel_use_owner_authenticated_upstream_routes():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "private-session", "mode": "live"})
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"task": {"id": "task-1", "kind": "agent", "status": "running"}, "events": []},
            )
        return httpx.Response(200, json={"id": "task-1", "kind": "agent", "status": "cancelled"})

    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(handler))
    task = run(client.get_task(owner(), "task-1"))
    cancelled = run(client.cancel_task(owner(), "task-1"))

    assert task["task"]["id"] == "task-1"
    assert cancelled["status"] == "cancelled"
    assert requests[1].url.path == "/api/agent/tasks/task-1"
    assert requests[2].url.path == "/api/agent/tasks/task-1"  # cancel preflight
    assert requests[3].url.path == "/api/agent/tasks/task-1/control"
    assert json.loads(requests[3].content) == {"action": "cancel"}
    assert all(request.headers.get("authorization") == "Bearer private-session" for request in requests[1:])


def test_task_detail_drops_file_and_browser_signed_urls_and_projects_safe_fields():
    signed_url = "http://openmuse.local/api/files/f1/content?owner=local-user&expires=123&signature=abc"

    def handler(request):
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "private-session", "mode": "live"})
        return httpx.Response(
            200,
            json={
                "task": {
                    "id": "task-1",
                    "kind": "agent",
                    "status": "succeeded",
                    "result": f"See {signed_url}",
                    "state": {"url": signed_url},
                },
                "events": [
                    {
                        "id": "event-1",
                        "taskId": "task-1",
                        "date": "2026-11-05T00:00:00Z",
                        "kind": "result",
                        "title": "Done",
                        "detail": f"Artifact: {signed_url}",
                    }
                ],
                "files": [{"url": signed_url}],
                "browsers": [{"consoleUrl": signed_url}],
                "artifacts": [{"data": {"url": signed_url}}],
            },
        )

    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(handler))
    detail = run(client.get_task(owner(), "task-1"))

    assert set(detail) == {"task", "events"}
    assert "state" not in detail["task"]
    assert "files" not in detail and "browsers" not in detail
    assert "signature=abc" not in repr(detail)
    assert "[signed link removed]" in detail["task"]["result"]


def test_task_detail_redacts_token_bearing_urls_in_text_fields():
    bearer_url = "https://files.example/report?access_token=private-file-token&download=1"

    def handler(request):
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "private-session", "mode": "live"})
        return httpx.Response(
            200,
            json={
                "task": {
                    "id": "task-1",
                    "kind": "agent",
                    "status": "succeeded",
                    "result": f"Download: {bearer_url}",
                },
                "events": [
                    {
                        "id": "event-1",
                        "taskId": "task-1",
                        "date": "2026-11-05T00:00:00Z",
                        "kind": "result",
                        "title": "Ready",
                        "detail": f"Artifact: {bearer_url}",
                    }
                ],
            },
        )

    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(handler))
    detail = run(client.get_task(owner(), "task-1"))

    assert "private-file-token" not in repr(detail)
    assert "[signed link removed]" in detail["task"]["result"]
    assert "[signed link removed]" in detail["events"][0]["detail"]


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_task_id_mismatch_from_upstream_is_rejected(method):
    def handler(request):
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "private-session", "mode": "live"})
        if method == "GET":
            return httpx.Response(
                200,
                json={"task": {"id": "other-task", "kind": "agent", "status": "running"}, "events": []},
            )
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"task": {"id": "task-1", "kind": "agent", "status": "running"}, "events": []},
            )
        return httpx.Response(200, json={"id": "other-task", "kind": "agent", "status": "cancelled"})

    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(handler))

    with pytest.raises(OpenMuseTaskError, match="invalid_response"):
        if method == "GET":
            run(client.get_task(owner(), "task-1"))
        else:
            run(client.cancel_task(owner(), "task-1"))


def test_cancel_never_posts_when_preflight_kind_is_not_agent():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "private-session", "mode": "live"})
        return httpx.Response(
            200,
            json={"task": {"id": "task-1", "kind": "finance", "status": "running"}, "events": []},
        )

    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(handler))

    with pytest.raises(OpenMuseTaskError, match="invalid_response"):
        run(client.cancel_task(owner(), "task-1"))

    assert calls == ["/api/session", "/api/agent/tasks/task-1"]


def test_concurrent_get_401s_share_one_session_refresh():
    calls = {"sessions": 0, "reads": 0}
    expired = False

    def handler(request):
        if request.url.path == "/api/session":
            calls["sessions"] += 1
            token = "old-session" if calls["sessions"] == 1 else "fresh-session"
            return httpx.Response(200, json={"token": token, "mode": "live"})
        calls["reads"] += 1
        if expired and request.headers["authorization"] == "Bearer old-session":
            return httpx.Response(401, json={"error": "expired"})
        return httpx.Response(
            200,
            json={"task": {"id": "task-1", "kind": "agent", "status": "running"}, "events": []},
        )

    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(handler))

    async def exercise():
        nonlocal expired
        await client.get_task(owner(), "task-1")
        expired = True
        return await asyncio.gather(
            client.get_task(owner(), "task-1"),
            client.get_task(owner(), "task-1"),
        )

    results = run(exercise())

    assert len(results) == 2
    assert calls == {"sessions": 2, "reads": 4}


@pytest.mark.parametrize(
    "base_url",
    [
        "http://example.com:8787",
        "http://192.168.1.12:8787",
        "http://localhost.evil:8787",
        "http://user:pass@127.0.0.1:8787",
        "http://127.0.0.1:8787/path",
        "http://127.0.0.1:not-a-port",
    ],
)
def test_http_targets_must_be_fixed_loopback_urls(base_url):
    client = OpenMuseTasksClient(config(base_url=base_url))

    with pytest.raises(OpenMuseTaskError, match="invalid_configuration"):
        run(client.create_research_task(owner(), "research", request_id="bad-url"))


def test_remote_tls_endpoint_is_rejected_before_any_request():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": "https://evil.example/steal"})

    client = OpenMuseTasksClient(
        config(base_url="https://openmuse.internal"),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(OpenMuseTaskError, match="invalid_configuration"):
        run(client.create_research_task(owner(), "research", request_id="remote-tls"))

    assert seen == []


def test_loopback_https_redirects_are_not_followed():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": "http://127.0.0.1:8787/another-route"})

    client = OpenMuseTasksClient(
        config(base_url="https://localhost:8787"),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(OpenMuseTaskError, match="upstream_http_error"):
        run(client.create_research_task(owner(), "research", request_id="redirect"))

    assert len(seen) == 1
    assert seen[0].url == "https://localhost:8787/api/session"


def test_upstream_credentials_and_error_body_are_not_returned():
    def handler(request):
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "private-session", "mode": "live"})
        return httpx.Response(500, text="token=private-session internal stack trace")

    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(handler))

    with pytest.raises(OpenMuseTaskError) as error:
        run(client.create_research_task(owner(), "research", request_id="server-error"))

    assert "private-session" not in repr(error.value)
    assert "internal stack trace" not in repr(error.value)


def test_session_and_access_key_are_redacted_from_payload_and_config_repr():
    def handler(request):
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "private-session", "mode": "live"})
        return httpx.Response(
            201,
            json={
                "id": "task-3",
                "kind": "research",
                "taskRevision": 1,
                "input": {"research_scope": "general"},
                "status": "paused",
                "note": "echo private-session local-access-key",
            },
        )

    settings = config()
    client = OpenMuseTasksClient(settings, transport=httpx.MockTransport(handler))
    result = run(client.create_research_task(owner(), "research", request_id="redaction"))

    assert "private-session" not in repr(result)
    assert "local-access-key" not in repr(result)
    assert "local-access-key" not in repr(settings)


def test_rejects_task_kinds_other_than_explicit_general_research():
    client = OpenMuseTasksClient(OpenMuseTasksConfig())

    with pytest.raises(OpenMuseTaskError, match="unsupported_task_kind"):
        run(client.create_task(owner(), {"prompt": "research", "kind": "finance"}))


def test_research_task_requires_caller_supplied_request_id():
    client = OpenMuseTasksClient(OpenMuseTasksConfig())

    with pytest.raises(OpenMuseTaskError, match="unsupported_task_kind"):
        run(client.create_task(owner(), {"prompt": "research", "kind": "agent"}))


def test_requires_nonempty_bounded_prompt_and_does_not_claim_finance_provenance():
    client = OpenMuseTasksClient(OpenMuseTasksConfig())

    with pytest.raises(OpenMuseTaskError, match="invalid_task_request"):
        run(client.create_research_task(owner(), "  ", request_id="empty"))
    with pytest.raises(OpenMuseTaskError, match="invalid_task_request"):
        run(client.create_research_task(owner(), "x" * 12001, request_id="long"))


def test_prompt_and_title_limits_match_javascript_utf16_code_units():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "unit-session", "mode": "sample"})
        body = json.loads(request.content)
        return httpx.Response(201, json={
            "id": "astral-task", "kind": "research", "taskRevision": 1, "status": "paused",
            "input": {"research_scope": "general"}, "prompt": body["prompt"], "title": body["title"],
        })

    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(handler))
    prompt = "😀" * 6000
    title = "😀" * 80
    run(client.create_research_task(owner(), prompt, title=title, request_id="utf16-boundary"))
    sent = json.loads(requests[-1].content)
    assert len(sent["prompt"].encode("utf-16-le")) // 2 == 12000
    assert len(sent["title"].encode("utf-16-le")) // 2 == 160
    request_count = len(requests)
    with pytest.raises(OpenMuseTaskError, match="invalid_task_request"):
        run(client.create_research_task(owner(), prompt + "😀", request_id="utf16-over"))
    with pytest.raises(OpenMuseTaskError, match="invalid_task_request"):
        run(client.create_research_task(owner(), "ok", title=title + "😀", request_id="title-over"))
    assert len(requests) == request_count


def test_rejects_malformed_upstream_task_payload():
    def handler(request):
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "private-session", "mode": "live"})
        return httpx.Response(201, json={"id": "task-4", "kind": "agent", "status": "unknown"})

    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(handler))

    with pytest.raises(OpenMuseTaskError, match="invalid_response"):
        run(client.create_research_task(owner(), "research", request_id="malformed"))


def test_delete_task_preflights_bound_task_and_validates_deleted_receipt(deletion_store):
    task = create_pending_mapping(deletion_store)
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "private-session", "mode": "live"})
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"task": {"id": "task-1", "kind": "agent", "status": "succeeded"}, "events": []},
            )
        assert request.method == "DELETE"
        return httpx.Response(200, json={"taskId": "task-1", "status": "deleted"})

    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(handler), store=deletion_store)

    result = run(client.delete_task(owner(), task["id"]))

    assert result == {"task_id": "task-1", "status": "deleted"}
    assert [request.method for request in requests] == ["POST", "GET", "DELETE"]
    assert requests[1].url.path == requests[2].url.path == "/api/agent/tasks/task-1"
    assert requests[2].headers["authorization"] == "Bearer private-session"


@pytest.mark.parametrize(
    ("receipt", "expected"),
    [
        (
            {
                "taskId": "task-1",
                "status": "partial",
                "retainedFileIds": ["file-1"],
                "retainedActionIds": [],
                "retainedActivityIds": [],
            },
            {
                "task_id": "task-1",
                "status": "partial",
                "retained_file_ids": ["file-1"],
                "retained_action_ids": [],
                "retained_activity_ids": [],
            },
        ),
        ({"taskId": "task-1", "status": "pending"}, {"task_id": "task-1", "status": "pending"}),
    ],
)
def test_delete_task_projects_partial_and_pending_receipts(receipt, expected, deletion_store):
    task = create_pending_mapping(deletion_store, suffix=receipt["status"])
    def handler(request):
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "private-session", "mode": "live"})
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"task": {"id": "task-1", "kind": "agent", "status": "cancelled"}, "events": []},
            )
        return httpx.Response(200, json=receipt)

    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(handler), store=deletion_store)
    assert run(client.delete_task(owner(), task["id"])) == expected


@pytest.mark.parametrize(
    "receipt",
    [
        {"taskId": "other-task", "status": "deleted"},
        {"status": "deleted"},
        {
            "taskId": "task-1",
            "status": "partial",
            "retainedFileIds": [],
            "retainedActionIds": [],
            "retainedActivityIds": [],
        },
        {"taskId": "task-1", "status": "deleted", "retainedActionIds": ["action-1"]},
    ],
)
def test_delete_task_rejects_wrong_identity_or_inconsistent_receipt(receipt, deletion_store):
    task = create_pending_mapping(deletion_store, suffix="bad-receipt")
    def handler(request):
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "private-session", "mode": "live"})
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"task": {"id": "task-1", "kind": "agent", "status": "queued"}, "events": []},
            )
        return httpx.Response(200, json=receipt)

    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(handler), store=deletion_store)
    with pytest.raises(OpenMuseTaskError, match="invalid_response"):
        run(client.delete_task(owner(), task["id"]))


def test_delete_task_401_is_never_retried(deletion_store):
    task = create_pending_mapping(deletion_store, suffix="unauthorized")
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "private-session", "mode": "live"})
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"task": {"id": "task-1", "kind": "agent", "status": "queued"}, "events": []},
            )
        return httpx.Response(401, json={"error": "expired"})

    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(handler), store=deletion_store)
    with pytest.raises(OpenMuseTaskError, match="upstream_http_error"):
        run(client.delete_task(owner(), task["id"]))
    assert calls == [
        ("POST", "/api/session"),
        ("GET", "/api/agent/tasks/task-1"),
        ("DELETE", "/api/agent/tasks/task-1"),
    ]


def test_delete_task_timeout_is_not_retried_and_mapped_retry_can_replay_after_get_404(deletion_store):
    calls = []
    get_attempts = 0
    delete_attempts = 0
    task = create_pending_mapping(deletion_store, suffix="timeout-retry")

    def handler(request):
        nonlocal get_attempts, delete_attempts
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "private-session", "mode": "live"})
        if request.method == "GET":
            get_attempts += 1
            if get_attempts == 1:
                return httpx.Response(
                    200,
                    json={"task": {"id": "task-1", "kind": "agent", "status": "running"}, "events": []},
                )
            return httpx.Response(404, json={"message": "Task not found"})
        delete_attempts += 1
        if delete_attempts == 1:
            raise httpx.ReadTimeout("private transport detail")
        return httpx.Response(200, json={"taskId": "task-1", "status": "deleted"})

    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(handler), store=deletion_store)
    with pytest.raises(OpenMuseTaskError, match="upstream_unavailable"):
        run(client.delete_task(owner(), task["id"]))
    assert delete_attempts == 1

    result = run(client.delete_task(owner(), task["id"]))
    assert result == {"task_id": "task-1", "status": "deleted"}
    assert delete_attempts == 2


def test_delete_task_requires_persisted_mapping_before_any_upstream_request():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "private-session", "mode": "live"})
        return httpx.Response(404, json={"message": "Task not found"})

    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(handler))
    with pytest.raises(OpenMuseTaskError, match="task_mapping_required"):
        run(client.delete_task(owner(), "local-task-without-mapping"))
    assert calls == []


def test_delete_task_rejects_caller_supplied_mapping_dict():
    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(lambda _request: None))
    with pytest.raises(TypeError):
        run(client.delete_task(owner(), "local-task-1", mapping={"upstream_task_id": "task-1"}))


def test_delete_task_uses_only_the_upstream_id_bound_to_requested_local_task(deletion_store):
    first = create_pending_mapping(deletion_store, suffix="bound-first", upstream_id="upstream-first")
    create_pending_mapping(deletion_store, suffix="bound-second", upstream_id="upstream-second")
    requests = []

    def handler(request):
        requests.append((request.method, request.url.path))
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "private-session", "mode": "live"})
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "task": {"id": "upstream-first", "kind": "agent", "status": "succeeded"},
                    "events": [],
                },
            )
        return httpx.Response(200, json={"taskId": "upstream-first", "status": "deleted"})

    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(handler), store=deletion_store)
    result = run(client.delete_task(owner(), first["id"]))

    assert result == {"task_id": "upstream-first", "status": "deleted"}
    assert requests[-2:] == [
        ("GET", "/api/agent/tasks/upstream-first"),
        ("DELETE", "/api/agent/tasks/upstream-first"),
    ]
    with pytest.raises(TypeError):
        run(client.delete_task(owner(), first["id"], upstream_task_id="upstream-second"))


def test_delete_task_rechecks_mapping_after_preflight_before_delete(deletion_store):
    task = create_pending_mapping(deletion_store, suffix="racing-delete")
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"token": "private-session", "mode": "live"})
        if request.method == "GET":
            deletion_store.update_openmuse_task_mapping(
                LOCAL_OWNER_ID,
                task["id"],
                deletion_status="complete",
            )
            return httpx.Response(
                200,
                json={"task": {"id": "task-1", "kind": "agent", "status": "succeeded"}, "events": []},
            )
        raise AssertionError("mapping changed before DELETE; request must fail closed")

    client = OpenMuseTasksClient(config(), transport=httpx.MockTransport(handler), store=deletion_store)
    with pytest.raises(OpenMuseTaskError, match="task_mapping_changed"):
        run(client.delete_task(owner(), task["id"]))
    assert not any(method == "DELETE" for method, _path in calls)

