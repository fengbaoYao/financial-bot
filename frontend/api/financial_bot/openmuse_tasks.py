"""Opt-in client for OpenMuse's held, task-scoped general-research contract.

The client validates OpenMuse's task/sandbox contract, but does not establish
OpenAshare's financial source policy or prove that public search is safe for
financial questions. Production routes remain disconnected and disabled.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, quote, urlsplit

import httpx

from api.financial_bot.auth import LOCAL_MODE, LOCAL_OWNER_ID, OwnerContext
from api.financial_bot.openmuse_client import OpenMuseClient


class OpenMuseTaskError(RuntimeError):
    """A stable, credential-free error suitable for an API boundary."""

    def __init__(self, code: str, *, status_code: int | None = None):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


@dataclass(frozen=True)
class OpenMuseTasksConfig:
    enabled: bool = False
    base_url: str = ""
    access_key: str = field(default="", repr=False)
    timeout_seconds: float = 2.0

    @classmethod
    def from_env(cls) -> "OpenMuseTasksConfig":
        shared = OpenMuseClient.from_env()
        # Task creation/provisioning can cold-start a Docker workspace. Keep the
        # health probe short, but allow task requests a separate bounded read
        # timeout so a slow first start does not become an ambiguous failure.
        raw_task_timeout = os.environ.get("OPENMUSE_TASK_TIMEOUT_SECONDS")
        if raw_task_timeout is None:
            raw_task_timeout = os.environ.get("OPENMUSE_TIMEOUT_SECONDS", "60")
        try:
            task_timeout = float(raw_task_timeout)
        except (TypeError, ValueError):
            task_timeout = 60.0
        return cls(
            enabled=shared.enabled,
            base_url=shared.base_url or "",
            access_key=os.environ.get("OPENMUSE_ACCESS_KEY", ""),
            timeout_seconds=task_timeout,
        )


_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
_TASK_STATUSES = {
    "queued",
    "running",
    "waiting_approval",
    "waiting_input",
    "scheduled",
    "paused",
    "succeeded",
    "failed",
    "cancelled",
}
_SECRET_KEYS = {
    "accesskey",
    "accesstoken",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "session",
    "sessiontoken",
    "refreshtoken",
    "privatekey",
    "secretkey",
    "token",
}
_URL = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_SAFE_TASK_FIELDS = (
    "id",
    "title",
    "prompt",
    "kind",
    "requestFingerprint",
    "taskRevision",
    "status",
    "plan",
    "evidence",
    "createdAt",
    "updatedAt",
    "result",
    "error",
    "question",
)
_GENERIC_TASK_INPUT = {"research_scope": "general"}
_FINANCIAL_TASK_INPUT = {"research_scope": "financial", "financial_bridge_required": True}


def _valid_task_input(value: Any) -> bool:
    return value in (_GENERIC_TASK_INPUT, _FINANCIAL_TASK_INPUT)


def _js_utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le", "surrogatepass")) // 2
_SAFE_EVENT_FIELDS = ("id", "taskId", "date", "kind", "title", "detail")


def _normalized_secret_key(value: Any) -> str:
    return str(value).replace("_", "").replace("-", "").lower()


def _valid_base_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        _ = parsed.port  # Validate the configured port before constructing requests.
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            return False
        if parsed.path not in {"", "/"}:
            return False
        hostname = parsed.hostname.lower()
        if hostname == "localhost":
            return True
        return ipaddress.ip_address(hostname).is_loopback
    except (ValueError, TypeError):
        return False


def _without_secrets(value: Any, secret_values: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_secrets(item, secret_values)
            for key, item in value.items()
            if _normalized_secret_key(key) not in _SECRET_KEYS
        }
    if isinstance(value, list):
        return [_without_secrets(item, secret_values) for item in value]
    if isinstance(value, str):
        for secret in secret_values:
            if secret:
                value = value.replace(secret, "[redacted]")
        value = _URL.sub(_redact_signed_url, value)
    return value


def _redact_signed_url(match: re.Match[str]) -> str:
    url = match.group(0)
    try:
        query_keys = {
            _normalized_secret_key(key)
            for key, _value in parse_qsl(urlsplit(url).query, keep_blank_values=True)
        }
    except ValueError:
        return "[link removed]"
    if _SECRET_KEYS.intersection(query_keys) or {"signature", "owner", "expires"} & query_keys:
        return "[signed link removed]"
    return url


class OpenMuseTasksClient:
    """Server-side client for held research create/provision/resume/read/cancel.

    A caller must pass the immutable OwnerContext produced by OpenAshare's
    server-side resolver. A normal boolean or client-provided owner id is not
    accepted as proof of local single-user mode.
    """

    def __init__(
        self,
        config: OpenMuseTasksConfig | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        store: Any | None = None,
    ):
        self.config = config or OpenMuseTasksConfig.from_env()
        self._http = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(self.config.timeout_seconds),
            follow_redirects=False,
            trust_env=False,
        )
        self._session_token: str | None = None
        self._session_lock = asyncio.Lock()
        self._store = store

    async def close(self) -> None:
        await self._http.aclose()

    def _check_access(self, owner: OwnerContext) -> str:
        if not self.config.enabled:
            raise OpenMuseTaskError("disabled")
        if (
            not isinstance(owner, OwnerContext)
            or owner.mode != LOCAL_MODE
            or owner.owner_id != LOCAL_OWNER_ID
            or owner.user is not None
        ):
            raise OpenMuseTaskError("local_single_user_only")
        if not self.config.base_url or not self.config.access_key or not _valid_base_url(self.config.base_url):
            raise OpenMuseTaskError("invalid_configuration")
        if not 0.1 <= self.config.timeout_seconds <= 120:
            raise OpenMuseTaskError("invalid_configuration")
        return self.config.base_url.rstrip("/")

    async def _fetch_session_token(self, base_url: str) -> str:
        try:
            response = await self._http.post(
                f"{base_url}/api/session",
                json={"accessKey": self.config.access_key},
            )
        except httpx.HTTPError:
            raise OpenMuseTaskError("upstream_unavailable") from None
        if response.status_code == 404:
            raise OpenMuseTaskError("upstream_not_found")
        if response.is_redirect or not response.is_success:
            raise OpenMuseTaskError("upstream_http_error", status_code=response.status_code)
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            raise OpenMuseTaskError("invalid_response") from None
        token = payload.get("token") if isinstance(payload, dict) else None
        mode = payload.get("mode") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token or mode not in {"sample", "live"}:
            raise OpenMuseTaskError("invalid_response")
        return token

    async def _login(self, base_url: str) -> str:
        if self._session_token:
            return self._session_token
        async with self._session_lock:
            if self._session_token:
                return self._session_token
            self._session_token = await self._fetch_session_token(base_url)
            return self._session_token

    async def _refresh_after_unauthorized(self, base_url: str, rejected_token: str) -> str:
        # Concurrent 401s for the same expired session share one login request.
        async with self._session_lock:
            if self._session_token and self._session_token != rejected_token:
                return self._session_token
            self._session_token = None
            self._session_token = await self._fetch_session_token(base_url)
            return self._session_token

    async def _clear_rejected_session(self, rejected_token: str) -> None:
        async with self._session_lock:
            if self._session_token == rejected_token:
                self._session_token = None

    async def _request(
        self,
        owner: OwnerContext,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        retry_unauthorized: bool = False,
    ) -> dict[str, Any]:
        base_url = self._check_access(owner)
        token = await self._login(base_url)
        try:
            response = await self._http.request(
                method,
                f"{base_url}{path}",
                headers={"Authorization": f"Bearer {token}"},
                json=json_body,
            )
        except httpx.HTTPError:
            # In particular, do not retry a POST whose upstream outcome is
            # uncertain. The caller can inspect the idempotent requestId.
            raise OpenMuseTaskError("upstream_unavailable") from None
        if response.status_code == 401 and retry_unauthorized:
            token = await self._refresh_after_unauthorized(base_url, token)
            try:
                response = await self._http.request(
                    method,
                    f"{base_url}{path}",
                    headers={"Authorization": f"Bearer {token}"},
                    json=json_body,
                )
            except httpx.HTTPError:
                raise OpenMuseTaskError("upstream_unavailable") from None
        if response.status_code == 401:
            # Task creation is never automatically repeated. Drop only the
            # rejected token so a later explicit call can sign in again.
            await self._clear_rejected_session(token)
        if response.status_code == 404:
            raise OpenMuseTaskError("upstream_not_found")
        if response.is_redirect or not response.is_success:
            raise OpenMuseTaskError("upstream_http_error", status_code=response.status_code)
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            raise OpenMuseTaskError("invalid_response") from None
        if not isinstance(payload, dict):
            raise OpenMuseTaskError("invalid_response")
        return _without_secrets(payload, (self.config.access_key, token))

    async def create_research_task(
        self,
        owner: OwnerContext,
        prompt: str,
        *,
        request_id: str,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Create one held OpenMuse research task, never a legacy agent task."""
        if not isinstance(prompt, str) or not prompt.strip() or _js_utf16_length(prompt.strip()) > 12_000:
            raise OpenMuseTaskError("invalid_task_request")
        if title is not None and (not isinstance(title, str) or not title.strip() or _js_utf16_length(title.strip()) > 160):
            raise OpenMuseTaskError("invalid_task_request")
        if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
            raise OpenMuseTaskError("invalid_task_request")
        result = await self._request(
            owner,
            "POST",
            "/api/agent/tasks",
            json_body={
                "requestId": request_id,
                **({"title": title.strip()} if title else {}),
                "prompt": prompt.strip(),
                "kind": "research",
                "input": {"research_scope": "general"},
            },
        )
        task = self._project_task(result, expected_kind="research")
        if task.get("status") != "paused" or task.get("input") != _GENERIC_TASK_INPUT:
            raise OpenMuseTaskError("invalid_response")
        return task

    async def create_shared_research_task(
        self,
        owner: OwnerContext,
        prompt: str,
        *,
        request_id: str,
        title: str | None = None,
        task_input: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a held task on OpenMuse's server-verified shared workspace."""
        if not isinstance(prompt, str) or not prompt.strip() or _js_utf16_length(prompt.strip()) > 12_000:
            raise OpenMuseTaskError("invalid_task_request")
        if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
            raise OpenMuseTaskError("invalid_task_request")
        resolved_input = task_input if task_input is not None else _GENERIC_TASK_INPUT
        if not _valid_task_input(resolved_input):
            raise OpenMuseTaskError("invalid_task_request")
        result = await self._request(
            owner,
            "POST",
            "/api/agent/tasks/shared-research",
            json_body={
                "requestId": request_id,
                **({"title": title.strip()} if isinstance(title, str) and title.strip() else {}),
                "prompt": prompt.strip(),
                "input": resolved_input,
            },
        )
        task = self._project_task(result, expected_kind="research")
        if task.get("status") != "paused" or task.get("input") != resolved_input or task.get("shared_research_workspace") is not True:
            raise OpenMuseTaskError("shared_workspace_not_confirmed")
        return task

    async def create_task(self, owner: OwnerContext, payload: dict[str, Any]) -> dict[str, Any]:
        """Reject broad task payloads; callers must use create_research_task."""
        if not isinstance(payload, dict) or payload.get("kind", "research") != "research":
            raise OpenMuseTaskError("unsupported_task_kind")
        allowed = {"prompt", "title", "requestId"}
        if set(payload) - allowed or "requestId" not in payload:
            raise OpenMuseTaskError("invalid_task_request")
        return await self.create_research_task(
            owner,
            payload.get("prompt"),
            title=payload.get("title"),
            request_id=payload["requestId"],
        )

    async def get_task(self, owner: OwnerContext, task_id: str) -> dict[str, Any]:
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            raise OpenMuseTaskError("invalid_task_id")
        result = await self._request(
            owner,
            "GET",
            f"/api/agent/tasks/{quote(task_id, safe='')}",
            retry_unauthorized=True,
        )
        task = result.get("task")
        if not isinstance(task, dict) or not isinstance(result.get("events"), list):
            raise OpenMuseTaskError("invalid_response")
        projected_task = self._project_task(task, expected_id=task_id)
        events = []
        for event in result["events"]:
            if not isinstance(event, dict) or event.get("taskId") != task_id:
                raise OpenMuseTaskError("invalid_response")
            events.append(
                _without_secrets(
                    {key: event[key] for key in _SAFE_EVENT_FIELDS if key in event},
                    (self.config.access_key, self._session_token or ""),
                )
            )
        # Files and browser sessions contain expiring signed URLs. They are
        # intentionally not forwarded by this task-only bridge.
        return {"task": projected_task, "events": events}

    async def provision_research_sandbox(
        self, owner: OwnerContext, task_id: str
    ) -> dict[str, Any]:
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            raise OpenMuseTaskError("invalid_task_id")
        result = await self._request(
            owner,
            "POST",
            f"/api/agent/tasks/{quote(task_id, safe='')}/provision-sandbox",
            json_body={},
            # Provision can have side effects. On any uncertain result, the
            # coordinator reconciles with GET and never repeats this POST.
        )
        attestation = result.get("attestation")
        if (
            result.get("taskId") != task_id
            or result.get("status") != "ready"
            or not isinstance(attestation, dict)
            or attestation.get("version") != "research-sandbox-v1"
            or attestation.get("verified") is not True
        ):
            raise OpenMuseTaskError("invalid_response")
        return {
            "task_id": task_id,
            "status": "ready",
            "attestation_version": "research-sandbox-v1",
            "verified": True,
        }

    async def resume_research_task(
        self, owner: OwnerContext, task_id: str
    ) -> dict[str, Any]:
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            raise OpenMuseTaskError("invalid_task_id")
        result = await self._request(
            owner,
            "POST",
            f"/api/agent/tasks/{quote(task_id, safe='')}/control",
            json_body={"action": "resume"},
            # A timeout is reconciled by GET; never replay a resume POST.
        )
        return self._project_task(result, expected_id=task_id, expected_kind="research")

    async def answer_task(
        self, owner: OwnerContext, task_id: str, answer: str
    ) -> dict[str, Any]:
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            raise OpenMuseTaskError("invalid_task_id")
        if not isinstance(answer, str) or not answer.strip() or _js_utf16_length(answer) > 12000:
            raise OpenMuseTaskError("invalid_task_answer")
        result = await self._request(
            owner,
            "POST",
            f"/api/agent/tasks/{quote(task_id, safe='')}/input",
            json_body={"answer": answer.strip()},
            # Input changes task state; reconcile with GET on uncertain responses,
            # never replay the answer POST automatically.
        )
        return self._project_task(result, expected_id=task_id, expected_kind="research")

    async def get_task_contracts(self, owner: OwnerContext) -> tuple[str, str, str]:
        """Read all pinned task contracts from one health response."""
        payload = await self._read_health(owner)
        create = payload.get("taskCreateContract")
        research = payload.get("researchSandboxContract")
        revision = payload.get("taskRevisionContract")
        if not isinstance(create, str) or not isinstance(research, str) or not isinstance(revision, str):
            raise OpenMuseTaskError("unsupported_contract")
        return create, research, revision

    async def get_task_create_contract(self, owner: OwnerContext) -> str:
        """Read the public, fixed contract marker used by safe create recovery."""
        contract = (await self._read_health(owner)).get("taskCreateContract")
        if not isinstance(contract, str) or not contract:
            raise OpenMuseTaskError("unsupported_contract")
        return contract

    async def shared_research_enabled(self, owner: OwnerContext) -> bool:
        """Read the upstream server's explicit shared-mode/model configuration gate."""
        return (await self._read_health(owner)).get("sharedResearchEnabled") is True

    async def _read_health(self, owner: OwnerContext) -> dict[str, Any]:
        base_url = self._check_access(owner)
        try:
            response = await self._http.get(f"{base_url}/api/health")
        except httpx.HTTPError:
            raise OpenMuseTaskError("upstream_unavailable") from None
        if response.is_redirect or not response.is_success:
            raise OpenMuseTaskError("unsupported_contract")
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            raise OpenMuseTaskError("invalid_response") from None
        if not isinstance(payload, dict):
            raise OpenMuseTaskError("unsupported_contract")
        return payload

    async def cancel_task(self, owner: OwnerContext, task_id: str) -> dict[str, Any]:
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            raise OpenMuseTaskError("invalid_task_id")
        # Validate the exact task and supported kind before issuing a state
        # change. Durable mapping from OpenAshare owners to OpenMuse task ids
        # is not implemented, so a route must remain disabled until it exists.
        await self.get_task(owner, task_id)
        result = await self._request(
            owner,
            "POST",
            f"/api/agent/tasks/{quote(task_id, safe='')}/control",
            json_body={"action": "cancel"},
            retry_unauthorized=True,
        )
        return self._project_task(result, expected_id=task_id)

    async def delete_task(
        self,
        owner: OwnerContext,
        local_task_id: str,
    ) -> dict[str, Any]:
        """Delete only the upstream task bound to a durable local owner mapping."""
        if not isinstance(local_task_id, str) or not _TASK_ID.fullmatch(local_task_id):
            raise OpenMuseTaskError("invalid_task_id")
        self._check_access(owner)
        mapping = self._load_delete_mapping(owner, local_task_id)
        task_id = mapping["upstream_task_id"]
        try:
            await self.get_task(owner, task_id)
        except OpenMuseTaskError as exc:
            if exc.code != "upstream_not_found":
                raise
        latest = self._read_delete_mapping(owner, local_task_id)
        if (
            not self._valid_delete_mapping(owner, local_task_id, latest)
            or not self._same_pending_mapping(mapping, latest)
        ):
            raise OpenMuseTaskError("task_mapping_changed")
        result = await self._request(
            owner,
            "DELETE",
            f"/api/agent/tasks/{quote(task_id, safe='')}",
            retry_unauthorized=False,
        )
        return self._project_deletion(result, expected_id=task_id)

    def _load_delete_mapping(self, owner: OwnerContext, local_task_id: str) -> dict[str, Any]:
        mapping = self._read_delete_mapping(owner, local_task_id)
        if not self._valid_delete_mapping(owner, local_task_id, mapping):
            raise OpenMuseTaskError("task_mapping_required")
        return mapping

    def _read_delete_mapping(self, owner: OwnerContext, local_task_id: str) -> dict[str, Any] | None:
        if self._store is None:
            raise OpenMuseTaskError("task_mapping_required")
        try:
            return self._store.get_openmuse_task_mapping(owner.owner_id, local_task_id)
        except Exception:
            raise OpenMuseTaskError("task_mapping_unavailable") from None

    @staticmethod
    def _valid_delete_mapping(
        owner: OwnerContext, local_task_id: str, mapping: dict[str, Any] | None
    ) -> bool:
        if not isinstance(mapping, dict):
            return False
        if (
            mapping.get("owner_id") != owner.owner_id
            or mapping.get("task_id") != local_task_id
            or mapping.get("deletion_status") != "pending"
            or not isinstance(mapping.get("upstream_task_id"), str)
            or not _TASK_ID.fullmatch(mapping["upstream_task_id"])
        ):
            return False
        expected_request_id = "fb-" + hashlib.sha256(
            f"{owner.owner_id}\0{local_task_id}".encode("utf-8")
        ).hexdigest()
        return (
            mapping.get("request_id") == expected_request_id
            and isinstance(mapping.get("payload_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", mapping["payload_sha256"]) is not None
        )

    @staticmethod
    def _same_pending_mapping(first: dict[str, Any], latest: dict[str, Any]) -> bool:
        return all(
            first.get(key) == latest.get(key)
            for key in (
                "owner_id",
                "task_id",
                "request_id",
                "payload_sha256",
                "upstream_task_id",
                "deletion_status",
            )
        ) and latest.get("deletion_status") == "pending"

    @staticmethod
    def _project_deletion(result: dict[str, Any], *, expected_id: str) -> dict[str, Any]:
        if result.get("taskId") != expected_id:
            raise OpenMuseTaskError("invalid_response")
        status = result.get("status")
        if status not in {"deleted", "partial", "pending"}:
            raise OpenMuseTaskError("invalid_response")
        retained: dict[str, list[str]] = {}
        for source, target in (
            ("retainedFileIds", "retained_file_ids"),
            ("retainedActionIds", "retained_action_ids"),
            ("retainedActivityIds", "retained_activity_ids"),
        ):
            value = result.get(source, [])
            if not isinstance(value, list) or any(
                not isinstance(item, str) or not _TASK_ID.fullmatch(item) for item in value
            ):
                raise OpenMuseTaskError("invalid_response")
            retained[target] = value
        has_retained = any(retained.values())
        if (status == "partial") != has_retained:
            raise OpenMuseTaskError("invalid_response")
        projected = {"task_id": expected_id, "status": status}
        if status == "partial":
            projected.update(retained)
        return projected

    def _project_task(
        self,
        task: dict[str, Any],
        *,
        expected_id: str | None = None,
        expected_kind: str | None = None,
    ) -> dict[str, Any]:
        if (
            not isinstance(task.get("id"), str)
            or not task["id"]
            or task.get("kind") not in {"agent", "research"}
            or task.get("status") not in _TASK_STATUSES
            or (expected_id is not None and task["id"] != expected_id)
            or (expected_kind is not None and task.get("kind") != expected_kind)
        ):
            raise OpenMuseTaskError("invalid_response")
        if expected_kind == "research" and (
            isinstance(task.get("taskRevision"), bool)
            or not isinstance(task.get("taskRevision"), int)
            or task["taskRevision"] < 1
            or task["taskRevision"] > 2**53 - 1
        ):
            raise OpenMuseTaskError("invalid_response")
        projected = {key: task[key] for key in _SAFE_TASK_FIELDS if key in task}
        # Preserve only the two exact research-scope shapes this bridge can
        # send. Do not expose arbitrary task inputs through task details.
        if _valid_task_input(task.get("input")):
            projected["input"] = dict(task["input"])
        state = task.get("state")
        if isinstance(state, dict):
            if state.get("sharedResearchWorkspace") is True:
                projected["shared_research_workspace"] = True
            provision = state.get("sandboxProvision")
            cleanup = state.get("sandboxProvisionCleanup")
            provision_status = provision.get("status") if isinstance(provision, dict) else None
            cleanup_status = cleanup.get("status") if isinstance(cleanup, dict) else None
            if provision_status in {"provisioning", "ready", "failed"}:
                projected["sandbox_provision_status"] = provision_status
            if cleanup_status in {"pending", "complete"}:
                projected["sandbox_cleanup_status"] = cleanup_status
        return _without_secrets(projected, (self.config.access_key, self._session_token or ""))
