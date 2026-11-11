"""Fail-closed retry coordinator for pending OpenMuse task deletions.

This module is intentionally not wired to an API route or a real OpenMuse
client. Callers must supply a transport and a trusted local OwnerContext.
Only an explicit, identity-matched ``deleted`` response completes a mapping.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from api.financial_bot.auth import LOCAL_MODE, LOCAL_OWNER_ID, OwnerContext
from api.financial_bot.store import ConflictError

_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,200}$")


class DeleteTaskTransport(Protocol):
    async def delete_task(self, owner: OwnerContext, local_task_id: str) -> dict[str, Any]: ...


@dataclass
class ReconcileReport:
    completed: int = 0
    pending: list[dict[str, str]] = field(default_factory=list)


class OpenMuseDeletionReconciler:
    """Retry pending, owner-scoped mappings without deleting local user data."""

    def __init__(self, store, transport: DeleteTaskTransport):
        self.store = store
        self.transport = transport

    @staticmethod
    def _validate_owner(owner: OwnerContext) -> None:
        if (
            not isinstance(owner, OwnerContext)
            or owner.mode != LOCAL_MODE
            or owner.owner_id != LOCAL_OWNER_ID
            or owner.user is not None
        ):
            raise ValueError("OpenMuse deletion reconciliation requires the trusted local owner")

    @staticmethod
    def _response_reason(response: Any, upstream_task_id: str) -> str | None:
        if not isinstance(response, dict):
            return "upstream_invalid_response"
        response_id = response.get("task_id", response.get("taskId"))
        if response_id != upstream_task_id:
            return "upstream_identity_mismatch"
        status = response.get("status")
        if status == "partial":
            return "upstream_partial"
        if status == "pending":
            return "upstream_pending"
        if status != "deleted":
            return "upstream_not_deleted"
        retained = response.get("retained_file_ids", response.get("retainedFileIds", []))
        if retained not in (None, []) and (not isinstance(retained, list) or retained):
            return "upstream_files_retained"
        for snake, camel in (
            ("retained_action_ids", "retainedActionIds"),
            ("retained_activity_ids", "retainedActivityIds"),
        ):
            values = response.get(snake, response.get(camel, []))
            if values not in (None, []) and (not isinstance(values, list) or values):
                return "upstream_audit_records_retained"
        return None

    def _record_pending(
        self,
        owner_id: str,
        task_id: str,
        reason: str,
        report: ReconcileReport,
        *,
        lease_id: str,
    ) -> None:
        try:
            self.store.update_openmuse_task_mapping(
                owner_id,
                task_id,
                deletion_status="pending",
                deletion_error=reason,
                lease_id=lease_id,
            )
        except ConflictError:
            # Another reconciler may have completed this mapping while our
            # request was in flight. Never let a stale failure reopen it.
            current = self.store.get_openmuse_task_mapping(owner_id, task_id)
            if not current or current.get("deletion_status") != "complete":
                if current and current.get("reconcile_lease_id") != lease_id:
                    return
                raise
            report.completed += 1
            return
        report.pending.append({"task_id": task_id, "reason": reason})

    async def reconcile(self, owner: OwnerContext, *, limit: int = 20) -> ReconcileReport:
        self._validate_owner(owner)
        report = ReconcileReport()
        mappings = self.store.list_openmuse_task_mappings(
            owner.owner_id,
            deletion_status="pending",
            limit=limit,
        )
        for mapping in mappings:
            task_id = mapping.get("task_id")
            # Treat a malformed or unexpectedly cross-owner row as unsafe. The
            # query is owner-scoped, but this check prevents a bad adapter/store
            # result from being used to target another identity.
            if mapping.get("owner_id") != owner.owner_id or not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
                continue
            current = self.store.get_openmuse_task_mapping(owner.owner_id, task_id)
            if not current or current.get("owner_id") != owner.owner_id:
                continue
            if current.get("deletion_status") == "complete":
                report.completed += 1
                continue
            if current.get("deletion_status") != "pending":
                continue
            lease_id = uuid.uuid4().hex
            mapping = self.store.claim_openmuse_task_mapping(
                owner.owner_id,
                task_id,
                lease_id=lease_id,
            )
            if not mapping:
                continue
            upstream_task_id = mapping.get("upstream_task_id")
            if upstream_task_id is None or upstream_task_id == "":
                self._record_pending(owner.owner_id, task_id, "missing_upstream_task_id", report, lease_id=lease_id)
                continue
            if not isinstance(upstream_task_id, str) or not _TASK_ID.fullmatch(upstream_task_id):
                self._record_pending(owner.owner_id, task_id, "invalid_upstream_task_id", report, lease_id=lease_id)
                continue
            try:
                response = await self.transport.delete_task(owner, task_id)
            except (asyncio.TimeoutError, TimeoutError):
                self._record_pending(owner.owner_id, task_id, "upstream_timeout", report, lease_id=lease_id)
                continue
            except Exception:
                # Do not persist exception text: transports can include URL,
                # credentials, request bodies, or user data in their messages.
                self._record_pending(owner.owner_id, task_id, "upstream_unavailable", report, lease_id=lease_id)
                continue
            reason = self._response_reason(response, upstream_task_id)
            if reason:
                self._record_pending(owner.owner_id, task_id, reason, report, lease_id=lease_id)
                continue
            try:
                self.store.update_openmuse_task_mapping(
                    owner.owner_id,
                    task_id,
                    deletion_status="complete",
                    deletion_error="",
                    lease_id=lease_id,
                )
            except ConflictError:
                current = self.store.get_openmuse_task_mapping(owner.owner_id, task_id)
                if current and current.get("deletion_status") == "complete":
                    report.completed += 1
                    continue
                if current and current.get("reconcile_lease_id") != lease_id:
                    continue
                if not current or current.get("deletion_status") != "complete":
                    raise
            report.completed += 1
        return report
