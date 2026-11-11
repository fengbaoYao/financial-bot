"""Fail-closed, standalone coordinator for held OpenMuse research tasks.

This module is intentionally not connected to API routes. Financial quick/deep
tasks are not eligible. Source-policy and independent sandbox-verification
gates must come from server-side code; both remain disabled by default.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit

from api.financial_bot.auth import LOCAL_MODE, LOCAL_OWNER_ID, OwnerContext
from api.financial_bot.openmuse_tasks import OpenMuseTaskError


@dataclass(frozen=True)
class SandboxVerification:
    """Evidence returned by an independent, server-side sandbox verifier."""

    task_id: str
    verified: bool
    evidence_ref: str


@dataclass(frozen=True)
class CoordinationResult:
    state: str
    task_id: str
    reason: str | None = None
    upstream_status: str | None = None
    mapping: dict[str, Any] | None = None


GeneralTaskCheck = Callable[[dict[str, Any]], bool]
SandboxVerifier = Callable[[dict[str, Any]], SandboxVerification | None]
TASK_CREATE_CONTRACT = "openmuse-task-create:v2:request-payload-fingerprint"
RESEARCH_SANDBOX_CONTRACT = "openmuse-research-sandbox:v1:held-provision-attest"
TASK_REVISION_CONTRACT = "openmuse-task-revision:v1:atomic-per-task-sequence"
_GENERIC_INPUT = {"research_scope": "general"}
_FINANCIAL_INPUT = {"research_scope": "financial", "financial_bridge_required": True}
_MAX_SAFE_INTEGER = 2**53 - 1
_OPENMUSE_EVIDENCE_KINDS = {"mail", "file", "web", "user"}


def _project_evidence(value: Any) -> list[dict[str, Any]]:
    """Persist only concrete, display-safe fields from OpenMuse evidence rows."""
    if not isinstance(value, list):
        return []
    projected: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict) or item.get("kind") not in _OPENMUSE_EVIDENCE_KINDS:
            continue
        evidence: dict[str, Any] = {"kind": item["kind"]}
        for key in ("id", "title", "excerpt", "source", "provider", "retrievedAt", "retrieved_at", "dataAsOf", "data_as_of", "freshness"):
            field = item.get(key)
            if isinstance(field, str) and field.strip():
                evidence[key] = field
        url = item.get("url")
        if isinstance(url, str):
            try:
                parsed = urlsplit(url)
            except ValueError:
                parsed = None
            if parsed and parsed.scheme in {"http", "https"} and parsed.netloc:
                evidence["url"] = url
        excerpts = item.get("excerpts")
        if isinstance(excerpts, list):
            safe_excerpts = [excerpt for excerpt in excerpts if isinstance(excerpt, str) and excerpt.strip()]
            if safe_excerpts:
                evidence["excerpts"] = safe_excerpts
        if any(key in evidence for key in ("title", "excerpt", "excerpts", "url")):
            projected.append(evidence)
    return projected


def _definite_rejection(error: OpenMuseTaskError) -> bool:
    # Exclude 408/409/425/429 and all server failures: those can race with
    # processing or indicate that a side effect may already have happened.
    return error.status_code in {400, 401, 403, 404, 405, 413, 415, 422}


def _js_utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le", "surrogatepass")) // 2


def _js_truncate(value: str, units: int) -> str:
    encoded = value.encode("utf-16-le", "surrogatepass")[: units * 2]
    # Drop an unpaired trailing surrogate rather than produce invalid JSON text.
    return encoded.decode("utf-16-le", "ignore")


def _is_openmuse_json_value(value: Any) -> bool:
    """Accept only the JSON values whose Python encoding matches the JS contract."""
    if value is None or isinstance(value, (str, bool)):
        return True
    if isinstance(value, int):
        return abs(value) <= _MAX_SAFE_INTEGER
    if isinstance(value, list):
        return all(_is_openmuse_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_openmuse_json_value(item) for key, item in value.items())
    # Floats, custom objects and other Python-only values are rejected rather
    # than guessed across the Python/JavaScript JSON number boundary.
    return False


def _canonicalize_openmuse_json(value: Any) -> Any:
    if isinstance(value, list):
        return [_canonicalize_openmuse_json(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _canonicalize_openmuse_json(value[key])
            for key in sorted(value, key=lambda item: item.encode("utf-16-be", "surrogatepass"))
        }
    return value


class OpenMuseTaskCoordinator:
    """Coordinate one server-owned task with OpenMuse without unsafe replay."""

    def __init__(
        self,
        store: Any,
        client: Any,
        *,
        enabled: bool = False,
        is_general_task: GeneralTaskCheck | None = None,
        verify_sandbox: SandboxVerifier | None = None,
    ) -> None:
        self.store = store
        self.client = client
        self.enabled = enabled
        self.is_general_task = is_general_task
        self.verify_sandbox = verify_sandbox

    @staticmethod
    def _check_owner(owner: OwnerContext) -> bool:
        return (
            isinstance(owner, OwnerContext)
            and owner.mode == LOCAL_MODE
            and owner.owner_id == LOCAL_OWNER_ID
            and owner.user is None
        )

    def _gate(self, task: dict[str, Any]) -> str | None:
        if not self.enabled:
            return "coordinator_disabled"
        # A quick/deep Financial Bot task stays in its own executor even if its
        # text happens to look like a general research request.
        if task.get("research_mode") in {"quick", "deep"}:
            return "not_general_task"
        shared_workspace = task.get("research_mode") == "openmuse_shared"
        if self.is_general_task is None or (not shared_workspace and self.verify_sandbox is None):
            return "gates_not_configured"
        try:
            if self.is_general_task(task) is not True:
                return "not_general_task"
            verification = None if shared_workspace else self.verify_sandbox(task)
        except Exception:
            return "gate_verification_failed"
        if not shared_workspace and (
            not isinstance(verification, SandboxVerification)
            or verification.verified is not True
            or verification.task_id != task.get("id")
            or not isinstance(verification.evidence_ref, str)
            or not verification.evidence_ref.strip()
        ):
            return "sandbox_not_verified"
        return None

    @staticmethod
    def _payload(task: dict[str, Any]) -> tuple[dict[str, Any], str] | None:
        prompt = task.get("query")
        if not isinstance(prompt, str) or not prompt.strip() or _js_utf16_length(prompt.strip()) > 12_000:
            return None
        authorization = task.get("authorization") or {}
        scope = authorization.get("scope") if isinstance(authorization, dict) else None
        financial_bridge_required = bool(
            task.get("research_mode") == "openmuse_shared"
            and isinstance(scope, dict)
            and scope.get("financial_bridge_required") is True
        )
        payload = {
            "kind": "research",
            "prompt": prompt.strip(),
            "title": _js_truncate(prompt.strip(), 160),
            "input": _FINANCIAL_INPUT if financial_bridge_required else _GENERIC_INPUT,
        }
        if task.get("research_mode") == "openmuse_shared":
            payload["shared_workspace"] = True
        digest = OpenMuseTaskCoordinator._request_fingerprint(payload)
        return (payload, digest) if digest is not None else None

    @staticmethod
    def _request_fingerprint(payload: dict[str, Any]) -> str | None:
        fingerprint_payload = {
            "kind": payload["kind"],
            "prompt": payload["prompt"],
            "title": payload["title"],
            "goalId": None,
            "milestoneId": None,
            "input": payload["input"],
        }
        if not _is_openmuse_json_value(fingerprint_payload):
            return None
        try:
            canonical = json.dumps(
                _canonicalize_openmuse_json(fingerprint_payload),
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        except (TypeError, UnicodeEncodeError, ValueError):
            return None
        return digest

    def _persist_projection(
        self,
        owner_id: str,
        task_id: str,
        projection: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        task = projection["task"]
        status = task["status"]
        revision = task.get("taskRevision")
        if isinstance(revision, bool) or not isinstance(revision, int) or not 1 <= revision <= _MAX_SAFE_INTEGER:
            return self.store.get_openmuse_task_mapping(owner_id, task_id), "invalid_revision"
        try:
            payload = json.dumps(task, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        except (TypeError, UnicodeEncodeError, ValueError):
            return self.store.get_openmuse_task_mapping(owner_id, task_id), "invalid_projection"
        local_status = {
            "queued": "queued",
            "scheduled": "queued",
            "paused": "queued",
            "running": "running",
            "waiting_approval": "waiting_user",
            "waiting_input": "waiting_user",
            "succeeded": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
        }.get(status, "unknown")
        result = task.get("result")
        local_result = None
        if isinstance(result, str) and result.strip():
            local_result = {"summary": result, "intent": "openmuse_shared_research"}
        elif isinstance(result, dict):
            local_result = {**result, "intent": result.get("intent") or "openmuse_shared_research"}
        result_evidence = _project_evidence(result.get("evidence")) if isinstance(result, dict) else []
        task_evidence = _project_evidence(task.get("evidence"))
        safe_evidence = []
        seen_evidence = set()
        for evidence in [*result_evidence, *task_evidence]:
            key = evidence.get("id") or json.dumps(evidence, ensure_ascii=False, sort_keys=True)
            if key in seen_evidence:
                continue
            seen_evidence.add(key)
            safe_evidence.append(evidence)
        if safe_evidence:
            if local_result is None:
                local_result = {"intent": "openmuse_shared_research"}
            local_result["evidence"] = safe_evidence
        elif isinstance(local_result, dict):
            # A result object can also contain model supplied evidence. Do not
            # persist that raw field when it did not pass the evidence contract.
            local_result.pop("evidence", None)
        error = task.get("error")
        error_message = error if isinstance(error, str) else None
        question = task.get("question")
        attention = (
            {"question": question, "allow_free_text": True}
            if local_status == "waiting_user" and isinstance(question, str) and question.strip()
            else None
        )
        bound_task = self.store.get_task(owner_id, task_id)
        local_projection = None
        if bound_task.get("research_mode") == "openmuse_shared":
            local_projection = {
                "status": local_status,
                "result": local_result,
                "error_message": error_message,
                "attention": attention,
            }
        mapping, outcome = self.store.apply_openmuse_task_projection(
            owner_id,
            task_id,
            revision=revision,
            projection_sha256=digest,
            status=status,
            local_projection=local_projection,
            event_kind="openmuse_status",
            event_summary=f"OpenMuse status: {status}; upstream events: {len(projection['events'])}",
        )
        return mapping, outcome

    def _persist_unknown(self, owner_id: str, task_id: str) -> dict[str, Any]:
        return self.store.get_openmuse_task_mapping(owner_id, task_id)

    @staticmethod
    def _candidate_upstream_id(request_id: str, task_id: str) -> str | None:
        expected_request_id = "fb-" + hashlib.sha256(
            f"{LOCAL_OWNER_ID}\0{task_id}".encode("utf-8")
        ).hexdigest()
        if request_id != expected_request_id:
            return None
        if not isinstance(request_id, str) or not request_id.startswith("fb-"):
            return None
        digest = request_id[3:]
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            return None
        return hashlib.sha256(f"task:{request_id}".encode("utf-8")).hexdigest()

    async def _contract_matches(self, owner: OwnerContext) -> bool:
        get_contracts = getattr(self.client, "get_task_contracts", None)
        if get_contracts is None:
            return False
        try:
            return await get_contracts(owner) == (
                TASK_CREATE_CONTRACT,
                RESEARCH_SANDBOX_CONTRACT,
                TASK_REVISION_CONTRACT,
            )
        except Exception:
            return False

    @staticmethod
    def _projection_matches_request(
        projection: dict[str, Any],
        *,
        upstream_id: str,
        payload: dict[str, Any],
    ) -> bool:
        return (
            OpenMuseTaskCoordinator._projection_matches_payload(
                projection,
                upstream_id=upstream_id,
                payload=payload,
            )
            and projection.get("status") == "paused"
        )

    @staticmethod
    def _projection_matches_payload(
        projection: dict[str, Any],
        *,
        upstream_id: str,
        payload: dict[str, Any],
    ) -> bool:
        expected_fingerprint = OpenMuseTaskCoordinator._request_fingerprint(payload)
        if expected_fingerprint is None:
            return False
        return (
            isinstance(projection, dict)
            and projection.get("id") == upstream_id
            and projection.get("kind") == "research"
            and projection.get("prompt") == payload["prompt"]
            and projection.get("title") == payload["title"]
            and projection.get("input") == payload["input"]
            and projection.get("requestFingerprint") == expected_fingerprint
            and isinstance(projection.get("taskRevision"), int)
            and not isinstance(projection.get("taskRevision"), bool)
            and 1 <= projection["taskRevision"] <= _MAX_SAFE_INTEGER
            and isinstance(projection.get("status"), str)
            and (payload.get("shared_workspace") is not True or projection.get("shared_research_workspace") is True)
        )

    async def _reconcile_unknown_create(
        self,
        owner: OwnerContext,
        task_id: str,
        mapping: dict[str, Any],
        payload: dict[str, Any],
    ) -> CoordinationResult:
        candidate_id = self._candidate_upstream_id(mapping.get("request_id"), task_id)
        if candidate_id is None:
            return CoordinationResult("unknown", task_id, "invalid_persisted_request_id", mapping=mapping)
        if not await self._contract_matches(owner):
            return CoordinationResult(
                "unknown",
                task_id,
                "unsupported_upstream_contract",
                mapping=mapping,
            )
        try:
            result = await self.client.get_task(owner, candidate_id)
        except Exception:
            return CoordinationResult("unknown", task_id, "upstream_reconciliation_unavailable", mapping=mapping)
        projection = result.get("task") if isinstance(result, dict) else None
        if not self._projection_matches_request(
            projection,
            upstream_id=candidate_id,
            payload=payload,
        ):
            return CoordinationResult(
                "unknown",
                task_id,
                "upstream_task_does_not_match_original_request",
                mapping=mapping,
            )

        # The Store update is a transactional, immutable binding. Concurrent
        # reconcilers can only bind the same deterministic candidate ID.
        mapping = self.store.update_openmuse_task_mapping(
            LOCAL_OWNER_ID,
            task_id,
            upstream_task_id=candidate_id,
        )
        if self.store.compare_and_set_openmuse_integration_phase(
            LOCAL_OWNER_ID, task_id, expected={"prepared", "creating", "create_unknown"}, value="held"
        ):
            mapping = self.store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task_id)
        mapping, outcome = self._persist_projection(LOCAL_OWNER_ID, task_id, result)
        if outcome not in {"applied", "duplicate"}:
            return CoordinationResult("unknown", task_id, "create_projection_revision_untrusted", mapping=mapping)
        return CoordinationResult(
            "reconciled",
            task_id,
            "create_recovered_by_request_id",
            upstream_status=projection["status"],
            mapping=mapping,
        )

    @staticmethod
    def _sandbox_state_ready(projection: dict[str, Any]) -> bool:
        return (
            projection.get("sandbox_provision_status") == "ready"
            and projection.get("sandbox_cleanup_status") == "complete"
        )

    async def _get_exact_task(
        self,
        owner: OwnerContext,
        upstream_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        try:
            result = await self.client.get_task(owner, upstream_id)
        except Exception:
            return None
        projection = result.get("task") if isinstance(result, dict) else None
        if not self._projection_matches_payload(
            projection,
            upstream_id=upstream_id,
            payload=payload,
        ):
            return None
        return projection

    async def _activate_held_research(
        self,
        owner: OwnerContext,
        task_id: str,
        upstream_id: str,
        payload: dict[str, Any],
        held_projection: dict[str, Any],
        *,
        create_recovered: bool = False,
    ) -> CoordinationResult:
        if not self._projection_matches_request(
            held_projection,
            upstream_id=upstream_id,
            payload=payload,
        ):
            return CoordinationResult(
                "unknown", task_id, "research_task_not_confirmed_held", mapping=self.store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task_id)
            )

        mapping = self.store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task_id)
        phase = mapping.get("integration_phase", "prepared")
        mapping, outcome = self._persist_projection(
            LOCAL_OWNER_ID, task_id, {"task": held_projection, "events": []}
        )
        if outcome not in {"applied", "duplicate"}:
            return CoordinationResult("unknown", task_id, "research_projection_revision_untrusted", mapping=mapping)
        if not await self._contract_matches(owner):
            return CoordinationResult(
                "unknown", task_id, "unsupported_upstream_contract", upstream_status="paused",
                mapping=self.store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task_id),
            )

        if payload.get("shared_workspace") is True and phase == "held":
            if held_projection.get("shared_research_workspace") is not True:
                return CoordinationResult(
                    "unknown", task_id, "shared_workspace_not_confirmed",
                    upstream_status="paused", mapping=self.store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task_id),
                )
            if not self.store.compare_and_set_openmuse_integration_phase(
                LOCAL_OWNER_ID, task_id, expected="held", value="provisioned"
            ):
                return CoordinationResult(
                    "unknown", task_id, "shared_workspace_stage_advanced",
                    mapping=self.store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task_id),
                )
            phase = "provisioned"

        if phase in {"provisioning", "provision_unknown"}:
            current = await self._get_exact_task(owner, upstream_id, payload)
            if current is None or not self._sandbox_state_ready(current):
                mapping = self._persist_unknown(LOCAL_OWNER_ID, task_id)
                return CoordinationResult("unknown", task_id, "provision_outcome_unconfirmed", mapping=mapping)
            mapping, outcome = self._persist_projection(
                LOCAL_OWNER_ID, task_id, {"task": current, "events": []}
            )
            if outcome not in {"applied", "duplicate"}:
                return CoordinationResult("unknown", task_id, "provision_projection_revision_untrusted", mapping=mapping)
            held_projection = current
            expected_phase = phase
            if not self.store.compare_and_set_openmuse_integration_phase(
                LOCAL_OWNER_ID, task_id, expected=expected_phase, value="provisioned"
            ):
                return CoordinationResult(
                    "unknown", task_id, "provision_stage_advanced_concurrently",
                    mapping=self.store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task_id),
                )
            phase = "provisioned"
        elif phase == "held":
            if not self.store.compare_and_set_openmuse_integration_phase(
                LOCAL_OWNER_ID, task_id, expected="held", value="provisioning"
            ):
                return CoordinationResult("unknown", task_id, "provision_stage_claimed", mapping=mapping)
            try:
                attestation = await self.client.provision_research_sandbox(owner, upstream_id)
            except OpenMuseTaskError as exc:
                if _definite_rejection(exc):
                    self.store.compare_and_set_openmuse_integration_phase(
                        LOCAL_OWNER_ID, task_id, expected="provisioning", value="rejected"
                    )
                    mapping = self.store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task_id)
                    return CoordinationResult("blocked", task_id, "upstream_rejected_request", mapping=mapping)
                current = await self._get_exact_task(owner, upstream_id, payload)
                if current is None or not self._sandbox_state_ready(current):
                    self.store.compare_and_set_openmuse_integration_phase(
                        LOCAL_OWNER_ID, task_id, expected="provisioning", value="provision_unknown"
                    )
                    mapping = self.store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task_id)
                    return CoordinationResult("unknown", task_id, "provision_outcome_unconfirmed", mapping=mapping)
                held_projection = current
            except Exception:
                current = await self._get_exact_task(owner, upstream_id, payload)
                if current is None or not self._sandbox_state_ready(current):
                    self.store.compare_and_set_openmuse_integration_phase(
                        LOCAL_OWNER_ID, task_id, expected="provisioning", value="provision_unknown"
                    )
                    mapping = self.store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task_id)
                    return CoordinationResult("unknown", task_id, "provision_outcome_unconfirmed", mapping=mapping)
                held_projection = current
            else:
                if (
                    not isinstance(attestation, dict)
                    or attestation.get("task_id") != upstream_id
                    or attestation.get("status") != "ready"
                    or attestation.get("attestation_version") != "research-sandbox-v1"
                    or attestation.get("verified") is not True
                ):
                    self.store.compare_and_set_openmuse_integration_phase(
                        LOCAL_OWNER_ID, task_id, expected="provisioning", value="provision_unknown"
                    )
                    mapping = self.store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task_id)
                    return CoordinationResult("unknown", task_id, "sandbox_attestation_not_confirmed", mapping=mapping)
            if not self.store.compare_and_set_openmuse_integration_phase(
                LOCAL_OWNER_ID, task_id, expected="provisioning", value="provisioned"
            ):
                return CoordinationResult(
                    "unknown", task_id, "provision_stage_advanced_concurrently",
                    mapping=self.store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task_id),
                )
            phase = "provisioned"
        elif phase == "provisioned":
            pass
        else:
            return CoordinationResult("unknown", task_id, "integration_stage_not_replayable", mapping=mapping)

        if phase != "provisioned":
            return CoordinationResult("unknown", task_id, "provision_outcome_unconfirmed", mapping=mapping)
        if not self.store.compare_and_set_openmuse_integration_phase(
            LOCAL_OWNER_ID, task_id, expected="provisioned", value="resuming"
        ):
            return CoordinationResult("unknown", task_id, "resume_stage_claimed", mapping=mapping)
        try:
            resumed = await self.client.resume_research_task(owner, upstream_id)
        except OpenMuseTaskError as exc:
            if _definite_rejection(exc):
                self.store.compare_and_set_openmuse_integration_phase(
                    LOCAL_OWNER_ID, task_id, expected="resuming", value="rejected"
                )
                mapping = self.store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task_id)
                return CoordinationResult("blocked", task_id, "upstream_rejected_request", mapping=mapping)
            # A lost provision response is reconciled by GET only. The request
            # is never POSTed twice; non-ready or unavailable state stays unknown.
            resumed = await self._get_exact_task(owner, upstream_id, payload)
            if resumed is None or resumed.get("status") not in {"queued", "running"}:
                mapping = self._persist_unknown(LOCAL_OWNER_ID, task_id)
                return CoordinationResult("unknown", task_id, "resume_outcome_unconfirmed", mapping=mapping)
        except Exception:
            # Resume may have been accepted despite a lost response. Only a
            # read proving the task left held state counts as confirmation.
            resumed = await self._get_exact_task(owner, upstream_id, payload)
            if resumed is None or resumed.get("status") not in {"queued", "running"}:
                mapping = self._persist_unknown(LOCAL_OWNER_ID, task_id)
                return CoordinationResult(
                    "unknown", task_id, "resume_outcome_unconfirmed", mapping=mapping
                )
        if (
            not self._projection_matches_payload(
                resumed,
                upstream_id=upstream_id,
                payload=payload,
            )
            or resumed.get("status") not in {"queued", "running"}
        ):
            mapping = self._persist_unknown(LOCAL_OWNER_ID, task_id)
            return CoordinationResult(
                "unknown", task_id, "resume_not_confirmed", mapping=mapping
            )

        projection = {"task": resumed, "events": []}
        mapping, outcome = self._persist_projection(LOCAL_OWNER_ID, task_id, projection)
        if outcome not in {"applied", "duplicate"}:
            return CoordinationResult("unknown", task_id, "resume_projection_revision_untrusted", mapping=mapping)
        self.store.compare_and_set_openmuse_integration_phase(
            LOCAL_OWNER_ID, task_id, expected="resuming", value="active"
        )
        mapping = self.store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task_id)
        return CoordinationResult(
            "reconciled" if create_recovered else "submitted",
            task_id,
            "create_recovered_by_request_id" if create_recovered else None,
            upstream_status=resumed["status"],
            mapping=mapping,
        )

    async def submit(self, owner: OwnerContext, task_id: str) -> CoordinationResult:
        if not self._check_owner(owner):
            return CoordinationResult("blocked", str(task_id), "local_single_user_only")
        task = self.store.get_task(LOCAL_OWNER_ID, task_id)
        reason = self._gate(task)
        if reason:
            return CoordinationResult("blocked", task_id, reason)
        payload_data = self._payload(task)
        if payload_data is None:
            return CoordinationResult("blocked", task_id, "invalid_task_payload")
        payload, payload_hash = payload_data

        existing = self.store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task_id)
        if existing:
            # Re-run Store's payload-binding check before any upstream read.
            mapping, created = self.store.create_openmuse_task_mapping(
                LOCAL_OWNER_ID,
                task_id,
                payload_sha256=payload_hash,
            )
            if created:
                raise RuntimeError("mapping appeared inconsistent during submit")
            if mapping.get("upstream_task_id"):
                phase = mapping.get("integration_phase", "prepared")
                if phase == "active" or phase in {"resume_unknown", "resuming"}:
                    return await self.sync(owner, task_id)
                if phase == "creating":
                    if not await self._contract_matches(owner):
                        return CoordinationResult("unknown", task_id, "unsupported_upstream_contract", mapping=mapping)
                    held = await self._get_exact_task(owner, mapping["upstream_task_id"], payload)
                    if held is None or not self._projection_matches_request(
                        held, upstream_id=mapping["upstream_task_id"], payload=payload
                    ):
                        return CoordinationResult("unknown", task_id, "bound_create_not_confirmed_held", mapping=mapping)
                    if not self.store.compare_and_set_openmuse_integration_phase(
                        LOCAL_OWNER_ID, task_id, expected="creating", value="held"
                    ):
                        mapping = self.store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task_id)
                        if mapping.get("integration_phase") != "held":
                            return CoordinationResult("unknown", task_id, "create_stage_advanced_concurrently", mapping=mapping)
                    return await self._activate_held_research(
                        owner, task_id, mapping["upstream_task_id"], payload, held
                    )
                if phase in {"held", "provisioning", "provision_unknown", "provisioned"}:
                    held = await self._get_exact_task(owner, mapping["upstream_task_id"], payload)
                    if held is None:
                        return CoordinationResult("unknown", task_id, "upstream_reconciliation_unavailable", mapping=mapping)
                    return await self._activate_held_research(owner, task_id, mapping["upstream_task_id"], payload, held)
                return CoordinationResult("unknown", task_id, "integration_stage_not_replayable", mapping=mapping)
            reconciled = await self._reconcile_unknown_create(owner, task_id, mapping, payload)
            # Replays may recover and bind a held task, but only the original
            # mapping creator may continue into side-effecting provision/resume.
            return reconciled

        if not await self._contract_matches(owner):
            return CoordinationResult("blocked", task_id, "unsupported_upstream_contract")

        # This durable binding is written before any provider request. Only the
        # call that actually created it may issue POST; every replay queries a
        # known upstream id or fails closed as unknown.
        mapping, created = self.store.create_openmuse_task_mapping(
            LOCAL_OWNER_ID,
            task_id,
            payload_sha256=payload_hash,
        )
        if not created:
            if mapping.get("upstream_task_id"):
                return await self.sync(owner, task_id)
            reconciled = await self._reconcile_unknown_create(owner, task_id, mapping, payload)
            if reconciled.state != "reconciled":
                return reconciled
            upstream_id = (reconciled.mapping or {}).get("upstream_task_id")
            held = await self._get_exact_task(owner, upstream_id, payload) if upstream_id else None
            if held is None:
                return CoordinationResult(
                    "unknown", task_id, "upstream_reconciliation_unavailable", mapping=reconciled.mapping
                )
            return await self._activate_held_research(
                owner, task_id, upstream_id, payload, held, create_recovered=True
            )

        if not self.store.compare_and_set_openmuse_integration_phase(
            LOCAL_OWNER_ID, task_id, expected="prepared", value="creating"
        ):
            return CoordinationResult("unknown", task_id, "create_stage_claimed", mapping=mapping)
        self.store.update_openmuse_task_mapping(LOCAL_OWNER_ID, task_id, status="creating")
        try:
            create_shared = getattr(self.client, "create_shared_research_task", None)
            if payload.get("shared_workspace") is True:
                if create_shared is None:
                    raise OpenMuseTaskError("shared_workspace_unsupported", status_code=403)
                projection = await create_shared(
                    owner,
                    payload["prompt"],
                    title=payload["title"],
                    request_id=mapping["request_id"],
                    task_input=payload["input"],
                )
            else:
                projection = await self.client.create_research_task(
                    owner,
                    payload["prompt"],
                    title=payload["title"],
                    request_id=mapping["request_id"],
                )
        except OpenMuseTaskError as exc:
            if _definite_rejection(exc):
                self.store.compare_and_set_openmuse_integration_phase(
                    LOCAL_OWNER_ID, task_id, expected="creating", value="rejected"
                )
                mapping = self.store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task_id)
                return CoordinationResult("blocked", task_id, "upstream_rejected_request", mapping=mapping)
            # Definite 4xx rejection is terminal; transport and server errors
            # remain uncertain and are reconciled by the deterministic ID.
            self.store.compare_and_set_openmuse_integration_phase(
                LOCAL_OWNER_ID, task_id, expected="creating", value="create_unknown"
            )
            mapping = self.store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task_id)
            reconciled = await self._reconcile_unknown_create(owner, task_id, mapping, payload)
            if reconciled.state != "reconciled":
                return reconciled
            upstream_id = (reconciled.mapping or {}).get("upstream_task_id")
            held = await self._get_exact_task(owner, upstream_id, payload) if upstream_id else None
            if held is None:
                return CoordinationResult(
                    "unknown", task_id, "upstream_reconciliation_unavailable", mapping=reconciled.mapping
                )
            return await self._activate_held_research(
                owner, task_id, upstream_id, payload, held, create_recovered=True
            )
        except Exception:
            # The provider may have accepted the POST before the connection
            # failed. Reconcile only through the pinned GET contract; never POST
            # again after an uncertain outcome.
            mapping = self._persist_unknown(LOCAL_OWNER_ID, task_id)
            reconciled = await self._reconcile_unknown_create(owner, task_id, mapping, payload)
            if reconciled.state != "reconciled":
                return reconciled
            upstream_id = (reconciled.mapping or {}).get("upstream_task_id")
            held = await self._get_exact_task(owner, upstream_id, payload) if upstream_id else None
            if held is None:
                return CoordinationResult(
                    "unknown", task_id, "upstream_reconciliation_unavailable", mapping=reconciled.mapping
                )
            return await self._activate_held_research(
                owner, task_id, upstream_id, payload, held, create_recovered=True
            )

        upstream_id = projection.get("id") if isinstance(projection, dict) else None
        candidate_id = self._candidate_upstream_id(mapping["request_id"], task_id)
        if (
            candidate_id is None
            or upstream_id != candidate_id
            or not self._projection_matches_request(projection, upstream_id=candidate_id, payload=payload)
            or not await self._contract_matches(owner)
        ):
            mapping = self._persist_unknown(LOCAL_OWNER_ID, task_id)
            reconciled = await self._reconcile_unknown_create(owner, task_id, mapping, payload)
            if reconciled.state != "reconciled":
                return reconciled
            upstream_id = (reconciled.mapping or {}).get("upstream_task_id")
            held = await self._get_exact_task(owner, upstream_id, payload) if upstream_id else None
            if held is None:
                return CoordinationResult(
                    "unknown", task_id, "upstream_reconciliation_unavailable", mapping=reconciled.mapping
                )
            return await self._activate_held_research(
                owner, task_id, upstream_id, payload, held, create_recovered=True
            )
        state = {"task": projection, "events": []}
        mapping = self.store.update_openmuse_task_mapping(
            LOCAL_OWNER_ID,
            task_id,
            upstream_task_id=upstream_id,
        )
        self.store.compare_and_set_openmuse_integration_phase(
            LOCAL_OWNER_ID, task_id, expected="creating", value="held"
        )
        mapping = self.store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task_id)
        return await self._activate_held_research(
            owner, task_id, upstream_id, payload, projection
        )

    async def sync(self, owner: OwnerContext, task_id: str) -> CoordinationResult:
        if not self._check_owner(owner):
            return CoordinationResult("blocked", str(task_id), "local_single_user_only")
        mapping = self.store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task_id)
        if not mapping:
            return CoordinationResult("blocked", task_id, "mapping_not_found")
        upstream_id = mapping.get("upstream_task_id")
        if not upstream_id:
            # A timed-out create may still be starting the workspace. Reconcile
            # only by the persisted deterministic request ID; never repeat POST.
            task = self.store.get_task(LOCAL_OWNER_ID, task_id)
            reason = self._gate(task)
            if reason:
                return CoordinationResult("blocked", task_id, reason, mapping=mapping)
            payload_data = self._payload(task)
            if payload_data is None:
                return CoordinationResult("blocked", task_id, "invalid_task_payload", mapping=mapping)
            payload, _payload_hash = payload_data
            reconciled = await self._reconcile_unknown_create(owner, task_id, mapping, payload)
            if reconciled.state != "reconciled":
                return reconciled
            mapping = reconciled.mapping or self.store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task_id)
            upstream_id = mapping.get("upstream_task_id") if mapping else None
            if not upstream_id:
                return CoordinationResult("unknown", task_id, "unknown_upstream_without_id", mapping=mapping)
            held = await self._get_exact_task(owner, upstream_id, payload)
            if held is None:
                return CoordinationResult(
                    "unknown", task_id, "upstream_reconciliation_unavailable", mapping=mapping
                )
            # The activation phases are claimed with Store compare-and-set, so
            # concurrent syncs cannot provision or resume the same task twice.
            return await self._activate_held_research(
                owner, task_id, upstream_id, payload, held, create_recovered=True
            )
        try:
            projection = await self.client.get_task(owner, upstream_id)
        except OpenMuseTaskError:
            mapping = self._persist_unknown(LOCAL_OWNER_ID, task_id)
            return CoordinationResult("unknown", task_id, "upstream_state_unavailable", mapping=mapping)
        mapping, outcome = self._persist_projection(LOCAL_OWNER_ID, task_id, projection)
        if outcome not in {"applied", "duplicate"}:
            return CoordinationResult("unknown", task_id, "sync_projection_revision_untrusted", mapping=mapping)
        if mapping.get("integration_phase") in {"resuming", "resume_unknown"} and projection["task"].get("status") in {"queued", "running"}:
            self.store.compare_and_set_openmuse_integration_phase(
                LOCAL_OWNER_ID, task_id, expected={"resuming", "resume_unknown"}, value="active"
            )
            mapping = self.store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task_id)
        return CoordinationResult(
            "synced",
            task_id,
            upstream_status=mapping["status"],
            mapping=mapping,
        )

    async def cancel(self, owner: OwnerContext, task_id: str) -> CoordinationResult:
        if not self._check_owner(owner):
            return CoordinationResult("blocked", str(task_id), "local_single_user_only")
        mapping = self.store.get_openmuse_task_mapping(LOCAL_OWNER_ID, task_id)
        if not mapping:
            return CoordinationResult("blocked", task_id, "mapping_not_found")
        upstream_id = mapping.get("upstream_task_id")
        if not upstream_id:
            return CoordinationResult("unknown", task_id, "unknown_upstream_without_id", mapping=mapping)
        try:
            projection = await self.client.cancel_task(owner, upstream_id)
        except OpenMuseTaskError:
            mapping = self._persist_unknown(LOCAL_OWNER_ID, task_id)
            return CoordinationResult("unknown", task_id, "cancel_outcome_unconfirmed", mapping=mapping)
        status = projection.get("status") if isinstance(projection, dict) else None
        state = {"task": projection, "events": []}
        mapping, outcome = self._persist_projection(LOCAL_OWNER_ID, task_id, state)
        if outcome not in {"applied", "duplicate"}:
            return CoordinationResult("unknown", task_id, "cancel_projection_revision_untrusted", mapping=mapping)
        if status == "cancelled":
            return CoordinationResult("cancelled", task_id, upstream_status=status, mapping=mapping)
        return CoordinationResult(
            "cancel_unconfirmed",
            task_id,
            "upstream_has_not_confirmed_cancellation",
            upstream_status=status,
            mapping=mapping,
        )
