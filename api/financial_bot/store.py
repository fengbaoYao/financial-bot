"""Owner-scoped repository for Financial Bot tasks, events, memory and deletion.

Every public method takes an ``owner_id`` and every SQL statement filters by it.
PostgreSQL additionally enforces RLS using the transaction-local JWT subject.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

from api.financial_bot.db import Database
from api.financial_bot.prompt import DEFAULT_PROMPT_TEMPLATE, normalize_prompt_config

TERMINAL_STATUSES = {"completed", "partial", "dropped", "failed", "cancelled", "unknown"}
TASK_STATUSES = {"queued", "running", "waiting_user", *TERMINAL_STATUSES}
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "cancelled", "dropped", "unknown"},
    "running": {"waiting_user", "completed", "partial", "dropped", "failed", "cancelled", "unknown"},
    "waiting_user": {"running", "cancelled", "dropped", "unknown"},
}
WAITING_REASONS = {"question", "confirmation", "permission"}
DISPOSITIONS = {"accepted", "direct_answer", "clarify", "dropped"}
MEMORY_KINDS = {
    "watch_object",
    "preference",
    "research_question",
    "holding",
    "confirmed_viewpoint",
}
CANDIDATE_STATUSES = {"pending", "confirmed", "dismissed"}
DELETION_LAYERS = (
    "memory_rows",
    "memory_versions",
    "memory_candidates",
    "quote_copies",
    "search_index",
    "context_cache",
    "physical_storage",
    "backup",
    "third_party_model",
)
OPENMUSE_TASK_STATUSES = {
    "prepared",
    "creating",
    "queued",
    "running",
    "waiting_approval",
    "waiting_input",
    "scheduled",
    "paused",
    "succeeded",
    "failed",
    "cancelled",
    "unknown",
}
OPENMUSE_DELETION_STATUSES = {"not_requested", "pending", "complete"}
OPENMUSE_INTEGRATION_PHASES = {
    "prepared", "creating", "create_unknown", "held", "provisioning",
    "provision_unknown", "provisioned", "resuming", "resume_unknown",
    "active", "rejected",
}
OPENMUSE_INTEGRATION_TRANSITIONS = {
    "prepared": {"creating", "held"},
    "creating": {"create_unknown", "held", "rejected"},
    "create_unknown": {"held", "rejected"},
    "held": {"provisioning", "provisioned"},
    "provisioning": {"provision_unknown", "provisioned", "rejected"},
    "provision_unknown": {"provisioned", "rejected"},
    "provisioned": {"resuming"},
    "resuming": {"active", "rejected"},
    "resume_unknown": {"active"},
    "active": set(),
    "rejected": set(),
}
OPENMUSE_DELETION_ERRORS = {
    "",
    "missing_upstream_task_id",
    "invalid_upstream_task_id",
    "upstream_invalid_response",
    "upstream_identity_mismatch",
    "upstream_partial",
    "upstream_pending",
    "upstream_not_deleted",
    "upstream_files_retained",
    "upstream_audit_records_retained",
    "upstream_timeout",
    "upstream_unavailable",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _text_hash(kind: str, text: str) -> str:
    return hashlib.sha256(f"{kind}\0{_normalize_text(text)}".encode("utf-8")).hexdigest()


class NotFoundError(LookupError):
    pass


class ConflictError(RuntimeError):
    def __init__(self, message: str, *, current_version: int | None = None):
        super().__init__(message)
        self.current_version = current_version


class OpenMuseMappingUnavailableError(RuntimeError):
    """OpenMuse control mappings require a trusted server-only database role."""


class LimitError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class MemoryContextCache:
    """Short-lived per-owner cache. Deletion must clear it before completing."""

    def __init__(self, ttl_seconds: float = 30.0, max_entries: int = 512):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._entries: "OrderedDict[str, tuple[float, list[str]]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, owner_id: str) -> Optional[list[str]]:
        with self._lock:
            item = self._entries.get(owner_id)
            if not item:
                return None
            created, value = item
            if time.monotonic() - created > self.ttl_seconds:
                self._entries.pop(owner_id, None)
                return None
            return list(value)

    def set(self, owner_id: str, value: list[str]) -> None:
        with self._lock:
            self._entries[owner_id] = (time.monotonic(), list(value))
            self._entries.move_to_end(owner_id)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def invalidate(self, owner_id: str) -> None:
        with self._lock:
            self._entries.pop(owner_id, None)
            for key in [key for key in self._entries if key.startswith(f"{owner_id}:")]:
                self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


class FinancialBotStore:
    def __init__(
        self,
        db: Database,
        *,
        backup_dir=None,
        third_party_retention_verified: bool = False,
    ):
        self.db = db
        self.cache = MemoryContextCache()
        self.backup_dir = backup_dir
        self.third_party_retention_verified = third_party_retention_verified

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _tasks_owner(task: dict[str, Any] | None, owner_id: str) -> dict[str, Any]:
        if not task or str(task.get("owner_id")) != str(owner_id):
            raise NotFoundError("task not found")
        return task

    @staticmethod
    def _lock_session_write(conn, owner_id: str, session_id: str) -> None:
        if conn.dialect == "postgres":
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(?), hashtext(?))",
                (f"financial-bot-session:{owner_id}", session_id),
            )

    @staticmethod
    def _ensure_session_lifecycle_locked(conn, owner_id: str, session_id: str) -> str:
        if conn.dialect == "postgres":
            row = conn.fetchone(
                "SELECT public.fb_ensure_session_lifecycle(?, ?) AS status",
                (owner_id, session_id),
            )
            return str((row or {}).get("status") or "deleted")
        conn.execute(
            "INSERT OR IGNORE INTO fb_session_lifecycle(owner_id, session_id, status, updated_at) VALUES (?, ?, 'active', ?)",
            (owner_id, session_id, utc_now()),
        )
        row = conn.fetchone(
            "SELECT status FROM fb_session_lifecycle WHERE owner_id = ? AND session_id = ?",
            (owner_id, session_id),
        )
        return str((row or {}).get("status") or "deleted")

    @classmethod
    def _require_active_session_locked(cls, conn, owner_id: str, session_id: str) -> None:
        if cls._ensure_session_lifecycle_locked(conn, owner_id, session_id) != "active":
            raise ConflictError("session_delete_in_progress")

    @staticmethod
    def _set_session_lifecycle_locked(conn, owner_id: str, session_id: str, status: str) -> None:
        if status not in {"deleting", "deleted"}:
            raise ValueError("invalid session lifecycle status")
        if conn.dialect == "postgres":
            conn.execute(
                "SELECT public.fb_set_session_lifecycle(?, ?, ?)",
                (owner_id, session_id, status),
            )
            return
        conn.execute(
            "UPDATE fb_session_lifecycle SET status = ?, updated_at = ? WHERE owner_id = ? AND session_id = ?",
            (status, utc_now(), owner_id, session_id),
        )

    @staticmethod
    def _memory_owner(memory: dict[str, Any] | None, owner_id: str) -> dict[str, Any]:
        if not memory or str(memory.get("owner_id")) != str(owner_id):
            raise NotFoundError("memory not found")
        return memory

    @staticmethod
    def _candidate_owner(candidate: dict[str, Any] | None, owner_id: str) -> dict[str, Any]:
        if not candidate or str(candidate.get("owner_id")) != str(owner_id):
            raise NotFoundError("candidate not found")
        return candidate

    def _append_event_locked(
        self,
        conn,
        *,
        owner_id: str,
        task_id: str,
        kind: str,
        status: str,
        summary: str,
        step_id: str | None = None,
        source_refs: Optional[list[dict[str, Any]]] = None,
        allow_after_terminal: bool = False,
        openmuse_revision: int | None = None,
    ) -> dict[str, Any] | None:
        row = conn.fetchone(
            "SELECT status, sequence FROM fb_tasks WHERE id = ? AND owner_id = ?",
            (task_id, owner_id),
        )
        if not row:
            raise NotFoundError("task not found")
        if row["status"] in TERMINAL_STATUSES and not allow_after_terminal:
            return None
        updated = conn.fetchone(
            """
            UPDATE fb_tasks
               SET sequence = sequence + 1, updated_at = ?
             WHERE id = ? AND owner_id = ?
         RETURNING sequence
            """,
            (utc_now(), task_id, owner_id),
        )
        sequence = int(updated["sequence"]) if updated else int(row["sequence"]) + 1
        event = {
            "event_id": new_id("evt"),
            "task_id": task_id,
            "owner_id": owner_id,
            "sequence": sequence,
            "occurred_at": utc_now(),
            "kind": kind,
            "step_id": step_id,
            "status": status,
            "display_summary": summary[:2000],
            "source_refs": list(source_refs or []),
        }
        conn.execute(
            """
            INSERT INTO fb_task_events
                (event_id, task_id, owner_id, sequence, occurred_at, kind, step_id,
                 status, display_summary, source_refs_json, openmuse_revision)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["event_id"],
                task_id,
                owner_id,
                sequence,
                event["occurred_at"],
                kind,
                step_id,
                status,
                event["display_summary"],
                json.dumps(event["source_refs"], ensure_ascii=False),
                openmuse_revision,
            ),
        )
        return event

    def _lock_clause(self) -> str:
        """SQLite's BEGIN IMMEDIATE already serializes writers; PostgreSQL needs
        an explicit row lock for optimistic version checks."""
        return " FOR UPDATE" if self.db.dialect == "postgres" else ""

    @staticmethod
    def _serialize_task(row: dict[str, Any]) -> dict[str, Any]:
        task = dict(row)
        result_raw = task.pop("result_json", None)
        task["result"] = json.loads(result_raw) if result_raw else None
        ui_raw = task.pop("ui_context_json", None)
        try:
            task["ui_context"] = json.loads(ui_raw) if ui_raw else None
        except (TypeError, ValueError):
            task["ui_context"] = None
        authorization_raw = task.pop("authorization_json", None)
        try:
            task["authorization"] = json.loads(authorization_raw) if authorization_raw else None
        except (TypeError, ValueError):
            task["authorization"] = None
        attention_raw = task.pop("attention_json", None)
        try:
            task["attention"] = json.loads(attention_raw) if attention_raw else None
        except (TypeError, ValueError):
            task["attention"] = None
        task["cancel_requested"] = bool(task.get("cancel_requested"))
        return task

    @staticmethod
    def _serialize_plan(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        plan = dict(row)
        raw = plan.pop("steps_json", "[]")
        try:
            plan["steps"] = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            plan["steps"] = []
        return plan

    @staticmethod
    def _serialize_event(row: dict[str, Any]) -> dict[str, Any]:
        event = dict(row)
        event.pop("owner_id", None)
        raw = event.pop("source_refs_json", "[]")
        try:
            event["source_refs"] = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            event["source_refs"] = []
        return event

    # ------------------------------------------------------------------ bots
    @staticmethod
    def _serialize_bot(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        result = dict(row)
        result["is_default"] = bool(result.get("is_default"))
        result["approval_threshold"] = int(result.get("approval_threshold") or 0)
        result["market_hours_only"] = bool(result.get("market_hours_only", 1))
        result["max_alerts_per_day"] = int(result.get("max_alerts_per_day") or 20)
        result["webhook_configured"] = bool(result.pop("webhook_url", None))
        result["notification_email"] = result.get("notification_email") or ""
        result["report_style"] = result.get("report_style") or "brief"
        result["digest_mode"] = result.get("digest_mode") or "immediate"
        result["digest_hour"] = result.get("digest_hour") or "18:00"
        stored_template = str(result.get("prompt_template") or "")
        result["prompt_template_customized"] = bool(stored_template.strip())
        result["prompt_template"] = stored_template or DEFAULT_PROMPT_TEMPLATE
        raw_config = result.get("prompt_config")
        if isinstance(raw_config, str):
            try:
                raw_config = json.loads(raw_config) if raw_config else {}
            except (TypeError, ValueError):
                raw_config = {}
        result["prompt_config"] = normalize_prompt_config(raw_config)
        instructions = str(result.get("instructions") or "").strip()
        if instructions:
            result["prompt_config"]["role"] = instructions
        result["prompt_version"] = int(result.get("prompt_version") or 1)
        return result

    def ensure_default_bot(self, owner_id: str) -> dict[str, Any]:
        now = utc_now()
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            row = conn.fetchone(
                "SELECT * FROM fb_bots WHERE owner_id = ? AND is_default = 1 ORDER BY created_at ASC LIMIT 1",
                (owner_id,),
            )
            if row is None:
                bot_id = new_id("bot")
                conn.execute(
                    """
                    INSERT INTO fb_bots
                        (id, owner_id, name, description, instructions, is_default, approval_threshold, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 1, 4, ?, ?)
                    """,
                    (bot_id, owner_id, "研究助手", "默认研究 Bot", "", now, now),
                )
                row = conn.fetchone("SELECT * FROM fb_bots WHERE id = ? AND owner_id = ?", (bot_id, owner_id))
            # Claim old sessions once so existing conversations remain visible.
            legacy = conn.fetchall(
                """
                SELECT DISTINCT session_id FROM fb_tasks
                 WHERE owner_id = ? AND session_id IS NOT NULL AND session_id <> ''
                   AND session_id NOT IN (SELECT session_id FROM fb_bot_sessions WHERE owner_id = ?)
                """,
                (owner_id, owner_id),
            )
            for item in legacy:
                conn.execute(
                    """
                    INSERT INTO fb_bot_sessions (bot_id, owner_id, session_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (row["id"], owner_id, item["session_id"], now, now),
                )
        return self._serialize_bot(row)

    def list_bots(self, owner_id: str) -> list[dict[str, Any]]:
        self.ensure_default_bot(owner_id)
        with self.db.connect(owner_id=owner_id) as conn:
            rows = conn.fetchall(
                "SELECT * FROM fb_bots WHERE owner_id = ? ORDER BY is_default DESC, updated_at DESC",
                (owner_id,),
            )
            result = []
            for row in rows:
                count = conn.fetchone(
                    "SELECT COUNT(*) AS count FROM fb_bot_sessions WHERE owner_id = ? AND bot_id = ?",
                    (owner_id, row["id"]),
                )
                item = self._serialize_bot(row)
                item["session_count"] = int((count or {}).get("count") or 0)
                latest = conn.fetchone(
                    """
                    SELECT t.query, t.updated_at
                      FROM fb_tasks t
                      JOIN fb_bot_sessions bs
                        ON bs.session_id = t.session_id
                       AND bs.owner_id = t.owner_id
                     WHERE bs.owner_id = ? AND bs.bot_id = ?
                     ORDER BY t.updated_at DESC
                     LIMIT 1
                    """,
                    (owner_id, row["id"]),
                )
                item["last_query"] = str((latest or {}).get("query") or "")[:300]
                item["last_active_at"] = (latest or {}).get("updated_at")
                result.append(item)
            return result

    def get_bot(self, owner_id: str, bot_id: str) -> dict[str, Any] | None:
        with self.db.connect(owner_id=owner_id) as conn:
            row = conn.fetchone("SELECT * FROM fb_bots WHERE owner_id = ? AND id = ?", (owner_id, bot_id))
        return self._serialize_bot(row)

    def get_bot_channels(self, owner_id: str, bot_id: str) -> dict[str, Any]:
        """Internal channel settings; never return this dictionary to the browser."""
        with self.db.connect(owner_id=owner_id) as conn:
            row = conn.fetchone(
                "SELECT webhook_url, notification_email, quiet_hours_start, quiet_hours_end, max_alerts_per_day FROM fb_bots WHERE owner_id = ? AND id = ?",
                (owner_id, bot_id),
            )
        return dict(row) if row else {}

    def count_notifications_today(self, owner_id: str, bot_id: str) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        with self.db.connect(owner_id=owner_id) as conn:
            row = conn.fetchone(
                "SELECT COUNT(*) AS count FROM fb_notifications WHERE owner_id = ? AND bot_id = ? AND created_at >= ?",
                (owner_id, bot_id, today),
            )
        return int((row or {}).get("count") or 0)

    def create_bot(self, owner_id: str, *, name: str, description: str = "", instructions: str = "", approval_threshold: int = 4, prompt_template: str | None = None, prompt_config: dict[str, Any] | None = None) -> dict[str, Any]:
        now = utc_now()
        bot_id = new_id("bot")
        config = normalize_prompt_config(prompt_config)
        if not config["role"]:
            config["role"] = instructions[:4000]
        template = (prompt_template or "").strip() or None
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            conn.execute(
                """
                INSERT INTO fb_bots
                    (id, owner_id, name, description, instructions, prompt_template, prompt_config, prompt_version, is_default, approval_threshold, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?, ?)
                """,
                (
                    bot_id,
                    owner_id,
                    name[:80],
                    description[:500],
                    instructions[:4000],
                    template[:8000] if template else None,
                    json.dumps(config, ensure_ascii=False, separators=(",", ":")),
                    max(0, min(100, int(approval_threshold))),
                    now,
                    now,
                ),
            )
        return self.get_bot(owner_id, bot_id) or {}

    def update_bot(self, owner_id: str, bot_id: str, *, name: str | None = None, description: str | None = None, instructions: str | None = None, approval_threshold: int | None = None, prompt_template: str | None = None, prompt_config: dict[str, Any] | None = None, market_hours_only: bool | None = None, max_alerts_per_day: int | None = None, quiet_hours_start: str | None = None, quiet_hours_end: str | None = None, webhook_url: str | None = None, notification_email: str | None = None, report_style: str | None = None, digest_mode: str | None = None, digest_hour: str | None = None) -> dict[str, Any] | None:
        sets, params = [], []
        if name is not None:
            sets.append("name = ?"); params.append(name[:80])
        if description is not None:
            sets.append("description = ?"); params.append(description[:500])
        if instructions is not None:
            sets.append("instructions = ?"); params.append(instructions[:4000])
        if approval_threshold is not None:
            sets.append("approval_threshold = ?"); params.append(max(0, min(100, int(approval_threshold))))
        if prompt_template is not None:
            sets.append("prompt_template = ?"); params.append(prompt_template.strip()[:8000] or None)
        if prompt_config is not None:
            config = normalize_prompt_config(prompt_config)
            sets.append("prompt_config = ?")
            params.append(json.dumps(config, ensure_ascii=False, separators=(",", ":")))
        if market_hours_only is not None:
            sets.append("market_hours_only = ?"); params.append(1 if market_hours_only else 0)
        if max_alerts_per_day is not None:
            sets.append("max_alerts_per_day = ?"); params.append(max(1, min(200, int(max_alerts_per_day))))
        if quiet_hours_start is not None:
            sets.append("quiet_hours_start = ?"); params.append(quiet_hours_start[:5] or None)
        if quiet_hours_end is not None:
            sets.append("quiet_hours_end = ?"); params.append(quiet_hours_end[:5] or None)
        if webhook_url is not None:
            sets.append("webhook_url = ?"); params.append(webhook_url[:1000] or None)
        if notification_email is not None:
            sets.append("notification_email = ?"); params.append(notification_email[:300] or None)
        if report_style is not None:
            if report_style not in {"brief", "standard"}:
                raise ValueError("invalid report style")
            sets.append("report_style = ?"); params.append(report_style)
        if digest_mode is not None:
            if digest_mode not in {"immediate", "daily"}:
                raise ValueError("invalid digest mode")
            sets.append("digest_mode = ?"); params.append(digest_mode)
        if digest_hour is not None:
            sets.append("digest_hour = ?"); params.append((digest_hour or "18:00")[:5])
        if not sets:
            return self.get_bot(owner_id, bot_id)
        sets.append("updated_at = ?"); params.append(utc_now())
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            conn.execute(f"UPDATE fb_bots SET {', '.join(sets)} WHERE owner_id = ? AND id = ?", (*params, owner_id, bot_id))
        return self.get_bot(owner_id, bot_id)

    def delete_bot(self, owner_id: str, bot_id: str) -> dict[str, Any]:
        bot = self.get_bot(owner_id, bot_id)
        if bot is None:
            raise NotFoundError("bot not found")
        if bot.get("is_default"):
            raise ValueError("default_bot_cannot_be_deleted")
        default = self.ensure_default_bot(owner_id)
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            sessions = conn.fetchone(
                "SELECT COUNT(*) AS count FROM fb_bot_sessions WHERE owner_id = ? AND bot_id = ?",
                (owner_id, bot_id),
            )
            conn.execute(
                "UPDATE fb_bot_sessions SET bot_id = ?, updated_at = ? WHERE owner_id = ? AND bot_id = ?",
                (default["id"], utc_now(), owner_id, bot_id),
            )
            conn.execute(
                "UPDATE fb_memories SET bot_id = ? WHERE owner_id = ? AND bot_id = ?",
                (default["id"], owner_id, bot_id),
            )
            conn.execute(
                "UPDATE fb_memory_candidates SET bot_id = ? WHERE owner_id = ? AND bot_id = ?",
                (default["id"], owner_id, bot_id),
            )
            conn.execute("DELETE FROM fb_bots WHERE owner_id = ? AND id = ?", (owner_id, bot_id))
        self.cache.invalidate(owner_id)
        return {"deleted": bot_id, "moved_sessions": int((sessions or {}).get("count") or 0), "moved_to": default["id"]}

    def delete_session(self, owner_id: str, session_id: str) -> dict[str, Any]:
        """Delete one conversation unless an upstream task deletion is pending."""
        pending_openmuse_task_ids: list[str] = []
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            self._lock_session_write(conn, owner_id, session_id)
            self._ensure_session_lifecycle_locked(conn, owner_id, session_id)
            task_rows = conn.fetchall(
                "SELECT id FROM fb_tasks WHERE owner_id = ? AND session_id = ?",
                (owner_id, session_id),
            )
            task_ids = [str(row["id"]) for row in task_rows]
            if conn.dialect == "postgres":
                mapped_tasks = conn.fetchall(
                    "SELECT task_id FROM public.fb_mark_session_openmuse_delete_pending(?, ?)",
                    (owner_id, session_id),
                )
            elif task_ids:
                mapped_tasks = conn.fetchall(
                    """
                    SELECT mapping.task_id, mapping.deletion_status
                      FROM fb_openmuse_task_mappings AS mapping
                      JOIN fb_tasks AS task
                        ON task.owner_id = mapping.owner_id AND task.id = mapping.task_id
                     WHERE mapping.owner_id = ? AND task.session_id = ?
                       AND mapping.deletion_status != 'complete'
                    """,
                    (owner_id, session_id),
                )
            else:
                mapped_tasks = []
            pending_openmuse_task_ids = sorted({str(row["task_id"]) for row in mapped_tasks})
            if conn.dialect == "sqlite" and pending_openmuse_task_ids:
                placeholders = ",".join("?" for _ in pending_openmuse_task_ids)
                conn.execute(
                    f"""
                    UPDATE fb_openmuse_task_mappings
                       SET deletion_status = 'pending', updated_at = ?
                     WHERE owner_id = ? AND task_id IN ({placeholders})
                       AND deletion_status != 'complete'
                    """,
                    [utc_now(), owner_id, *pending_openmuse_task_ids],
                )
            if pending_openmuse_task_ids:
                self._set_session_lifecycle_locked(conn, owner_id, session_id, "deleting")
                result = {
                    "status": "pending",
                    "session_id": session_id,
                    "pending_openmuse_task_ids": pending_openmuse_task_ids,
                    "session": 0,
                    "tasks": 0,
                    "messages": 0,
                }
            else:
                if task_ids:
                    placeholders = ",".join("?" for _ in task_ids)
                    conn.execute(
                        f"DELETE FROM fb_task_events WHERE owner_id = ? AND task_id IN ({placeholders})",
                        [owner_id, *task_ids],
                    )
                    conn.execute(
                        f"DELETE FROM fb_task_plans WHERE owner_id = ? AND task_id IN ({placeholders})",
                        [owner_id, *task_ids],
                    )
                    conn.execute(
                        f"DELETE FROM fb_tasks WHERE owner_id = ? AND id IN ({placeholders})",
                        [owner_id, *task_ids],
                    )
                messages = conn.execute(
                    "DELETE FROM fb_messages WHERE owner_id = ? AND session_id = ?",
                    (owner_id, session_id),
                ).rowcount
                conn.execute(
                    "DELETE FROM fb_bot_sessions WHERE owner_id = ? AND session_id = ?",
                    (owner_id, session_id),
                )
                conn.execute(
                    "DELETE FROM fb_threads WHERE owner_id = ? AND session_id = ?",
                    (owner_id, session_id),
                )
                self._set_session_lifecycle_locked(conn, owner_id, session_id, "deleted")
                result = {"session": 1, "tasks": len(task_ids), "messages": int(messages or 0)}
        self.cache.invalidate(owner_id)
        return result

    def finalize_pending_session_deletions(self, owner_id: str, *, limit: int = 20) -> dict[str, int]:
        """Finish local purges only after every task mapping for a fenced session is complete."""
        if self.db.dialect != "sqlite":
            return {"sessions": 0, "tasks": 0, "messages": 0}
        limit = max(1, min(100, int(limit)))
        with self.db.connect(owner_id=owner_id) as conn:
            candidates = conn.fetchall(
                "SELECT session_id FROM fb_session_lifecycle WHERE owner_id = ? AND status = 'deleting' "
                "ORDER BY updated_at, session_id LIMIT ?",
                (owner_id, limit),
            )
        totals = {"sessions": 0, "tasks": 0, "messages": 0}
        for candidate in candidates:
            session_id = str(candidate["session_id"])
            with self.db.connect(owner_id=owner_id, write=True) as conn:
                self._lock_session_write(conn, owner_id, session_id)
                lifecycle = conn.fetchone(
                    "SELECT status FROM fb_session_lifecycle WHERE owner_id = ? AND session_id = ?",
                    (owner_id, session_id),
                )
                if not lifecycle or lifecycle.get("status") != "deleting":
                    continue
                task_rows = conn.fetchall(
                    "SELECT id FROM fb_tasks WHERE owner_id = ? AND session_id = ?",
                    (owner_id, session_id),
                )
                task_ids = [str(row["id"]) for row in task_rows]
                if task_ids:
                    placeholders = ",".join("?" for _ in task_ids)
                    incomplete = conn.fetchone(
                        f"SELECT 1 AS found FROM fb_openmuse_task_mappings "
                        f"WHERE owner_id = ? AND task_id IN ({placeholders}) "
                        "AND deletion_status != 'complete' LIMIT 1",
                        [owner_id, *task_ids],
                    )
                    if incomplete:
                        continue
                    conn.execute(
                        f"DELETE FROM fb_task_events WHERE owner_id = ? AND task_id IN ({placeholders})",
                        [owner_id, *task_ids],
                    )
                    conn.execute(
                        f"DELETE FROM fb_task_plans WHERE owner_id = ? AND task_id IN ({placeholders})",
                        [owner_id, *task_ids],
                    )
                    conn.execute(
                        f"DELETE FROM fb_tasks WHERE owner_id = ? AND id IN ({placeholders})",
                        [owner_id, *task_ids],
                    )
                messages = conn.execute(
                    "DELETE FROM fb_messages WHERE owner_id = ? AND session_id = ?",
                    (owner_id, session_id),
                ).rowcount
                conn.execute(
                    "DELETE FROM fb_bot_sessions WHERE owner_id = ? AND session_id = ?",
                    (owner_id, session_id),
                )
                conn.execute(
                    "DELETE FROM fb_threads WHERE owner_id = ? AND session_id = ?",
                    (owner_id, session_id),
                )
                self._set_session_lifecycle_locked(conn, owner_id, session_id, "deleted")
                totals["sessions"] += 1
                totals["tasks"] += len(task_ids)
                totals["messages"] += int(messages or 0)
        if totals["sessions"]:
            self.cache.invalidate(owner_id)
        return totals

    def clear_bot_sessions(self, owner_id: str, bot_id: str) -> dict[str, Any]:
        with self.db.connect(owner_id=owner_id) as conn:
            rows = conn.fetchall(
                "SELECT session_id FROM fb_bot_sessions WHERE owner_id = ? AND bot_id = ?",
                (owner_id, bot_id),
            )
            task_rows = conn.fetchall(
                "SELECT DISTINCT session_id FROM fb_tasks WHERE owner_id = ? AND session_id IN (SELECT session_id FROM fb_bot_sessions WHERE owner_id = ? AND bot_id = ?)",
                (owner_id, owner_id, bot_id),
            )
        session_ids = {str(row["session_id"]) for row in rows if row.get("session_id")}
        session_ids.update(str(row["session_id"]) for row in task_rows if row.get("session_id"))
        totals: dict[str, Any] = {"sessions": 0, "tasks": 0, "messages": 0}
        pending_sessions: list[str] = []
        pending_task_ids: set[str] = set()
        for session_id in session_ids:
            result = self.delete_session(owner_id, session_id)
            if result.get("status") == "pending":
                pending_sessions.append(session_id)
                pending_task_ids.update(result.get("pending_openmuse_task_ids", []))
                continue
            totals["sessions"] += result["session"]
            totals["tasks"] += result["tasks"]
            totals["messages"] += result["messages"]
        if pending_sessions:
            totals.update(
                {
                    "status": "pending",
                    "pending_sessions": sorted(pending_sessions),
                    "pending_openmuse_task_ids": sorted(pending_task_ids),
                }
            )
        return totals

    def set_thread_workflow(self, owner_id: str, session_id: str, workflow: dict[str, Any], task_id: str) -> None:
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            self._lock_session_write(conn, owner_id, session_id)
            self._require_active_session_locked(conn, owner_id, session_id)
            conn.execute(
                "UPDATE fb_threads SET workflow_json = ?, current_task_id = ?, updated_at = ? WHERE owner_id = ? AND session_id = ?",
                (json.dumps(workflow, ensure_ascii=False, separators=(",", ":")), task_id, utc_now(), owner_id, session_id),
            )

    def get_thread_workflow(self, owner_id: str, session_id: str) -> dict[str, Any] | None:
        thread = self.get_thread(owner_id, session_id)
        if not thread:
            return None
        raw = thread.get("workflow_json")
        if not raw:
            return None
        try:
            workflow = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return {
            "workflow": workflow if isinstance(workflow, dict) else None,
            "task_id": thread.get("current_task_id"),
            "updated_at": thread.get("updated_at"),
        }

    def ensure_main_thread(self, owner_id: str, bot_id: str, session_id: str, title: str) -> dict[str, Any]:
        now = utc_now()
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            self._lock_session_write(conn, owner_id, session_id)
            self._require_active_session_locked(conn, owner_id, session_id)
            existing = conn.fetchone(
                "SELECT * FROM fb_threads WHERE owner_id = ? AND session_id = ?",
                (owner_id, session_id),
            )
            if existing:
                return dict(existing)
            conn.execute(
                """
                INSERT INTO fb_threads
                    (session_id, owner_id, bot_id, parent_session_id, title, kind, approval_mode, research_mode, created_at, updated_at)
                VALUES (?, ?, ?, NULL, ?, 'main', 'ask', 'quick', ?, ?)
                """,
                (session_id, owner_id, bot_id, (title or "未命名对话")[:120], now, now),
            )
        return self.get_thread(owner_id, session_id) or {}

    def ensure_thread_for_session(self, owner_id: str, session_id: str) -> dict[str, Any] | None:
        existing = self.get_thread(owner_id, session_id)
        if existing:
            return existing
        bot = self.bot_for_session(owner_id, session_id)
        tasks = self.list_tasks(owner_id, session_id=session_id, limit=1)
        title = str(tasks[0].get("query") or "未命名对话") if tasks else "未命名对话"
        if bot is None:
            return None
        return self.ensure_main_thread(owner_id, bot["id"], session_id, title)

    def update_thread(self, owner_id: str, session_id: str, *, title: str | None = None, pinned: bool | None = None, archived: bool | None = None, folder: str | None = None, unread: int | None = None, approval_mode: str | None = None, research_mode: str | None = None) -> dict[str, Any] | None:
        sets, params = [], []
        if title is not None:
            if not title.strip():
                raise ValueError("title is required")
            sets.append("title = ?"); params.append(title.strip()[:120])
        if pinned is not None:
            sets.append("pinned = ?"); params.append(1 if pinned else 0)
        if archived is not None:
            sets.append("archived = ?"); params.append(1 if archived else 0)
        if folder is not None:
            sets.append("folder = ?"); params.append(folder.strip()[:80] or None)
        if unread is not None:
            sets.append("unread = ?"); params.append(max(0, int(unread)))
        if approval_mode is not None:
            if approval_mode not in {"ask", "auto"}:
                raise ValueError("invalid approval mode")
            sets.append("approval_mode = ?"); params.append(approval_mode)
        if research_mode is not None:
            if research_mode not in {"quick", "deep"}:
                raise ValueError("invalid research mode")
            sets.append("research_mode = ?"); params.append(research_mode)
        if not sets:
            return self.get_thread(owner_id, session_id)
        sets.append("updated_at = ?"); params.append(utc_now())
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            self._lock_session_write(conn, owner_id, session_id)
            self._require_active_session_locked(conn, owner_id, session_id)
            conn.execute(
                f"UPDATE fb_threads SET {', '.join(sets)} WHERE owner_id = ? AND session_id = ?",
                (*params, owner_id, session_id),
            )
        return self.get_thread(owner_id, session_id)

    def mark_thread_read(self, owner_id: str, session_id: str) -> dict[str, Any] | None:
        now = utc_now()
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            self._lock_session_write(conn, owner_id, session_id)
            self._require_active_session_locked(conn, owner_id, session_id)
            conn.execute(
                "UPDATE fb_threads SET unread = 0, last_read_at = ? WHERE owner_id = ? AND session_id = ?",
                (now, owner_id, session_id),
            )
        return self.get_thread(owner_id, session_id)

    def get_thread(self, owner_id: str, session_id: str | None) -> dict[str, Any] | None:
        if not session_id:
            return None
        with self.db.connect(owner_id=owner_id) as conn:
            row = conn.fetchone(
                "SELECT * FROM fb_threads WHERE owner_id = ? AND session_id = ?",
                (owner_id, session_id),
            )
        return dict(row) if row else None

    def create_thread(
        self,
        owner_id: str,
        *,
        bot_id: str,
        parent_session_id: str | None,
        title: str,
        kind: str = "side",
    ) -> dict[str, Any]:
        if kind not in {"side", "main"}:
            raise ValueError("invalid thread kind")
        session_id = new_id("thread")
        now = utc_now()
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            lock_ids = sorted({session_id, parent_session_id} if parent_session_id else {session_id})
            for lock_id in lock_ids:
                self._lock_session_write(conn, owner_id, lock_id)
            self._require_active_session_locked(conn, owner_id, session_id)
            if parent_session_id:
                self._require_active_session_locked(conn, owner_id, parent_session_id)
            existing = conn.fetchone(
                "SELECT bot_id FROM fb_bot_sessions WHERE owner_id = ? AND session_id = ? LIMIT 1",
                (owner_id, session_id),
            )
            if existing is None:
                conn.execute(
                    "INSERT INTO fb_bot_sessions (bot_id, owner_id, session_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (bot_id, owner_id, session_id, now, now),
                )
            conn.execute(
                """
                INSERT INTO fb_threads
                    (session_id, owner_id, bot_id, parent_session_id, title, kind, approval_mode, research_mode, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'ask', 'quick', ?, ?)
                """,
                (session_id, owner_id, bot_id, parent_session_id, title[:120] or "Side thread", kind, now, now),
            )
        return {
            "id": session_id,
            "session_id": session_id,
            "bot_id": bot_id,
            "parent_session_id": parent_session_id,
            "title": title[:120] or "Side thread",
            "kind": kind,
            "created_at": now,
            "updated_at": now,
        }

    def list_threads(self, owner_id: str, *, bot_id: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
        clauses, params = ["owner_id = ?"], [owner_id]
        if bot_id:
            clauses.append("bot_id = ?"); params.append(bot_id)
        params.append(max(1, min(100, limit)))
        with self.db.connect(owner_id=owner_id) as conn:
            rows = conn.fetchall(
                f"SELECT * FROM fb_threads WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT ?",
                params,
            )
        return [dict(row) for row in rows]

    def ensure_bot_session(self, owner_id: str, bot_id: str, session_id: str) -> dict[str, Any] | None:
        """Bind a conversation to a Bot once, and never move it afterwards.

        A conversation belongs to the Bot that started it.  Re-binding used to
        happen implicitly (welcome message, task creation), which moved the
        whole research history of an old session under whichever Bot happened to
        be active next - the new Bot then "showed history it never created".
        The first binding wins; callers get the real owner back so they can
        refuse instead of silently hijacking the session.
        """
        now = utc_now()
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            self._lock_session_write(conn, owner_id, session_id)
            lifecycle = self._ensure_session_lifecycle_locked(conn, owner_id, session_id)
            if lifecycle != "active":
                raise ConflictError("session_delete_in_progress")
            existing = conn.fetchone(
                "SELECT bot_id FROM fb_bot_sessions WHERE owner_id = ? AND session_id = ? LIMIT 1",
                (owner_id, session_id),
            )
            if existing is None:
                conn.execute(
                    "INSERT INTO fb_bot_sessions (bot_id, owner_id, session_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (bot_id, owner_id, session_id, now, now),
                )
                return {"bot_id": bot_id}
            return {"bot_id": existing["bot_id"]}

    def bot_for_session(self, owner_id: str, session_id: str | None) -> dict[str, Any] | None:
        if not session_id:
            return self.ensure_default_bot(owner_id)
        with self.db.connect(owner_id=owner_id) as conn:
            row = conn.fetchone(
                "SELECT bot_id FROM fb_bot_sessions WHERE owner_id = ? AND session_id = ? LIMIT 1",
                (owner_id, session_id),
            )
        if row:
            return self.get_bot(owner_id, row["bot_id"])
        default = self.ensure_default_bot(owner_id)
        self.ensure_bot_session(owner_id, default["id"], session_id)
        return default

    def list_digest_bots(self) -> list[dict[str, Any]]:
        """Bots configured for a daily external digest; local worker only."""
        with self.db.connect(owner_id=None) as conn:
            rows = conn.fetchall("SELECT * FROM fb_bots WHERE digest_mode = 'daily' ORDER BY updated_at ASC")
        return [dict(row) for row in rows]

    def mark_digest_sent(self, owner_id: str, bot_id: str, day: str) -> None:
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            conn.execute(
                "UPDATE fb_bots SET last_digest_date = ?, updated_at = ? WHERE owner_id = ? AND id = ?",
                (day, utc_now(), owner_id, bot_id),
            )

    def record_delivery(
        self,
        owner_id: str,
        *,
        bot_id: str,
        notification_id: str | None,
        channel: str,
        status: str,
        error: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            conn.execute(
                """
                INSERT INTO fb_notification_deliveries
                    (id, owner_id, bot_id, notification_id, channel, status, error, payload_json, attempts, next_retry_at, created_at, sent_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (new_id("deliv"), owner_id, bot_id, notification_id, channel, status,
                 error[:500] if error else None, json.dumps(payload, ensure_ascii=False) if payload else None,
                 None if status == "sent" else (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(timespec="seconds"),
                 utc_now(), utc_now() if status == "sent" else None),
            )

    def list_deliveries(self, owner_id: str, *, bot_id: str | None = None, status: str | None = None, channel: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        clauses, params = ["owner_id = ?"], [owner_id]
        if bot_id:
            clauses.append("bot_id = ?"); params.append(bot_id)
        if status:
            clauses.append("status = ?"); params.append(status)
        if channel:
            clauses.append("channel LIKE ?"); params.append(f"%{channel}%")
        params.append(max(1, min(1000, int(limit))))
        with self.db.connect(owner_id=owner_id) as conn:
            rows = conn.fetchall(
                f"SELECT * FROM fb_notification_deliveries WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT ?",
                params,
            )
        result = []
        for row in rows:
            item = dict(row)
            item.pop("payload_json", None)
            result.append(item)
        return result

    def get_delivery(self, owner_id: str, delivery_id: str) -> dict[str, Any] | None:
        with self.db.connect(owner_id=owner_id) as conn:
            row = conn.fetchone(
                "SELECT * FROM fb_notification_deliveries WHERE owner_id = ? AND id = ?",
                (owner_id, delivery_id),
            )
        return dict(row) if row else None

    def update_delivery(self, owner_id: str, delivery_id: str, *, status: str, error: str | None = None) -> None:
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            current = conn.fetchone(
                "SELECT attempts FROM fb_notification_deliveries WHERE owner_id = ? AND id = ?",
                (owner_id, delivery_id),
            )
            attempts = int((current or {}).get("attempts") or 1) + 1
            backoff = {2: 1, 3: 5, 4: 15}.get(attempts, 60)
            final_status = "dead" if status != "sent" and attempts >= 4 else status
            next_retry = None if final_status != "failed" else (datetime.now(timezone.utc) + timedelta(minutes=backoff)).isoformat(timespec="seconds")
            conn.execute(
                """
                UPDATE fb_notification_deliveries
                   SET status = ?, error = ?, attempts = ?, sent_at = ?, next_retry_at = ?
                 WHERE owner_id = ? AND id = ?
                """,
                (final_status, error[:500] if error else None, attempts, utc_now() if final_status == "sent" else None, next_retry, owner_id, delivery_id),
            )

    def list_retry_deliveries(self, *, limit: int = 20) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.db.connect(owner_id=None) as conn:
            rows = conn.fetchall(
                """
                SELECT * FROM fb_notification_deliveries
                 WHERE status = 'failed' AND attempts < 4
                   AND (next_retry_at IS NULL OR next_retry_at <= ?)
                 ORDER BY next_retry_at ASC, created_at ASC
                 LIMIT ?
                """,
                (now, max(1, min(100, int(limit)))),
            )
        return [dict(row) for row in rows]

    def list_market_closures(self, owner_id: str) -> list[dict[str, Any]]:
        with self.db.connect(owner_id=owner_id) as conn:
            rows = conn.fetchall(
                "SELECT * FROM fb_market_closures WHERE owner_id = ? ORDER BY closure_date ASC",
                (owner_id,),
            )
        return [dict(row) for row in rows]

    def add_market_closure(self, owner_id: str, *, market: str, closure_date: str, reason: str = "") -> dict[str, Any]:
        if market not in {"us", "hk", "cn"}:
            raise ValueError("invalid market")
        now = utc_now()
        closure_id = new_id("closure")
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            existing = conn.fetchone(
                "SELECT 1 FROM fb_market_closures WHERE owner_id = ? AND market = ? AND closure_date = ?",
                (owner_id, market, closure_date[:10]),
            )
            if existing is None:
                conn.execute(
                    "INSERT INTO fb_market_closures (id, owner_id, market, closure_date, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (closure_id, owner_id, market, closure_date[:10], reason[:200], now),
                )
            row = conn.fetchone(
                "SELECT * FROM fb_market_closures WHERE owner_id = ? AND market = ? AND closure_date = ?",
                (owner_id, market, closure_date[:10]),
            )
        return dict(row) if row else {}

    def delete_market_closure(self, owner_id: str, closure_id: str) -> bool:
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            row = conn.fetchone("SELECT 1 FROM fb_market_closures WHERE owner_id = ? AND id = ?", (owner_id, closure_id))
            if not row:
                return False
            conn.execute("DELETE FROM fb_market_closures WHERE owner_id = ? AND id = ?", (owner_id, closure_id))
        return True

    def owner_market_closed(self, owner_id: str, market: str, day: str) -> bool:
        with self.db.connect(owner_id=owner_id) as conn:
            row = conn.fetchone(
                "SELECT 1 FROM fb_market_closures WHERE owner_id = ? AND market = ? AND closure_date = ?",
                (owner_id, market, day[:10]),
            )
        return bool(row)

    # ------------------------------------------------------------- monitors
    def list_monitors(self, owner_id: str, *, bot_id: str | None = None) -> list[dict[str, Any]]:
        clause, params = "", [owner_id]
        if bot_id:
            clause = " AND bot_id = ?"
            params.append(bot_id)
        with self.db.connect(owner_id=owner_id) as conn:
            rows = conn.fetchall(
                f"SELECT * FROM fb_monitors WHERE owner_id = ?{clause} ORDER BY created_at DESC",
                params,
            )
        return [dict(row) for row in rows]

    def get_monitor(self, owner_id: str, monitor_id: str) -> dict[str, Any] | None:
        with self.db.connect(owner_id=owner_id) as conn:
            row = conn.fetchone(
                "SELECT * FROM fb_monitors WHERE owner_id = ? AND id = ?",
                (owner_id, monitor_id),
            )
        return dict(row) if row else None

    def create_monitor(
        self,
        owner_id: str,
        *,
        bot_id: str,
        name: str,
        symbol: str,
        threshold: float,
        condition: str = "price",
        direction: str = "both",
        interval_minutes: int = 15,
        cooldown_minutes: int = 120,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if direction not in {"up", "down", "both"}:
            raise ValueError("invalid direction")
        if condition not in {"price", "announcement", "earnings", "report"}:
            raise ValueError("invalid monitor condition")
        now = utc_now()
        monitor_id = new_id("mon")
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            conn.execute(
                """
                INSERT INTO fb_monitors
                    (id, owner_id, bot_id, session_id, name, symbol, condition, threshold, direction,
                     interval_minutes, cooldown_minutes, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (monitor_id, owner_id, bot_id, session_id, name[:120], symbol[:40], condition, float(threshold),
                 direction, max(1, min(1440, int(interval_minutes))), max(0, min(1440, int(cooldown_minutes))), now, now),
            )
        return self.get_monitor(owner_id, monitor_id) or {}

    def update_monitor(self, owner_id: str, monitor_id: str, *, status: str | None = None, name: str | None = None, threshold: float | None = None, muted_until: str | None = None, clear_mute: bool = False) -> dict[str, Any] | None:
        sets, params = [], []
        if status is not None:
            if status not in {"active", "paused"}:
                raise ValueError("invalid monitor status")
            sets.append("status = ?"); params.append(status)
        if name is not None:
            sets.append("name = ?"); params.append(name[:120])
        if threshold is not None:
            sets.append("threshold = ?"); params.append(float(threshold))
        if clear_mute:
            sets.append("muted_until = ?"); params.append(None)
        elif muted_until is not None:
            sets.append("muted_until = ?"); params.append(muted_until)
        if not sets:
            return self.get_monitor(owner_id, monitor_id)
        sets.append("updated_at = ?"); params.append(utc_now())
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            conn.execute(
                f"UPDATE fb_monitors SET {', '.join(sets)} WHERE owner_id = ? AND id = ?",
                (*params, owner_id, monitor_id),
            )
        return self.get_monitor(owner_id, monitor_id)

    def delete_monitor(self, owner_id: str, monitor_id: str) -> bool:
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            row = conn.fetchone(
                "SELECT 1 FROM fb_monitors WHERE owner_id = ? AND id = ?",
                (owner_id, monitor_id),
            )
            if not row:
                return False
            conn.execute("DELETE FROM fb_monitors WHERE owner_id = ? AND id = ?", (owner_id, monitor_id))
        return True

    def list_due_monitors(self) -> list[dict[str, Any]]:
        """Return active monitors whose polling interval elapsed. Local worker only."""
        now = datetime.now(timezone.utc)
        with self.db.connect(owner_id=None) as conn:
            rows = conn.fetchall("SELECT * FROM fb_monitors WHERE status = 'active' ORDER BY last_checked_at ASC")
        due = []
        for row in rows:
            retry_after = row.get("retry_after")
            if retry_after:
                try:
                    retry_at = datetime.fromisoformat(str(retry_after).replace("Z", "+00:00"))
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    if now < retry_at:
                        continue
                except ValueError:
                    pass
            last = row.get("last_checked_at")
            interval = max(1, int(row.get("interval_minutes") or 15))
            if not last:
                due.append(dict(row)); continue
            try:
                previous = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
                if previous.tzinfo is None:
                    previous = previous.replace(tzinfo=timezone.utc)
                if (now - previous).total_seconds() >= interval * 60:
                    due.append(dict(row))
            except ValueError:
                due.append(dict(row))
        return due

    def record_monitor_check(
        self,
        owner_id: str,
        monitor_id: str,
        *,
        checked_at: str,
        price: float | None = None,
        change_pct: float | None = None,
        data_as_of: str | None = None,
        currency: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        monitor = self.get_monitor(owner_id, monitor_id)
        if monitor is None:
            return None
        triggered = False
        if error:
            failures = int(monitor.get("failure_count") or 0) + 1
            retry_minutes = min(240, 15 * (2 ** min(failures - 1, 4)))
            retry_at = (datetime.fromisoformat(checked_at.replace("Z", "+00:00")) + timedelta(minutes=retry_minutes)).isoformat()
            with self.db.connect(owner_id=owner_id, write=True) as conn:
                conn.execute(
                    "UPDATE fb_monitors SET last_checked_at = ?, last_error = ?, failure_count = ?, retry_after = ?, updated_at = ? WHERE owner_id = ? AND id = ?",
                    (checked_at, error[:500], failures, retry_at, checked_at, owner_id, monitor_id),
                )
            return None
        threshold = float(monitor.get("threshold") or 0)
        direction = str(monitor.get("direction") or "both")
        change = float(change_pct or 0)
        triggered = (abs(change) >= threshold) if direction == "both" else (change <= -threshold if direction == "down" else change >= threshold)
        previous = monitor.get("last_triggered_at")
        if triggered and previous:
            try:
                previous_dt = datetime.fromisoformat(str(previous).replace("Z", "+00:00"))
                if previous_dt.tzinfo is None:
                    previous_dt = previous_dt.replace(tzinfo=timezone.utc)
                if (datetime.fromisoformat(checked_at.replace("Z", "+00:00")) - previous_dt).total_seconds() < int(monitor.get("cooldown_minutes") or 0) * 60:
                    triggered = False
            except ValueError:
                pass
        now = checked_at
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            conn.execute(
                "UPDATE fb_monitors SET last_checked_at = ?, last_error = NULL, failure_count = 0, retry_after = NULL, updated_at = ? WHERE owner_id = ? AND id = ?",
                (now, now, owner_id, monitor_id),
            )
            if triggered:
                notification_id = new_id("notif")
                sign = "+" if change >= 0 else ""
                price_text = (f"{price:.{6 if abs(price) < 1 else 4}f}".rstrip("0").rstrip(".")
                              if price is not None else "—")
                direction_text = {"up": "上涨", "down": "下跌", "both": "双向"}.get(direction, "未知方向")
                title = f"{monitor.get('name') or monitor.get('symbol')} · {sign}{change:.2f}%"
                body = (
                    f"{monitor.get('symbol')} 最新价 {price_text}"
                    f"{(' ' + currency) if currency else ''}，较前收 {sign}{change:.2f}%，"
                    f"达到监控阈值 {threshold:g}%（{direction_text}）。"
                    f"数据时间 {data_as_of or '未知'}；该结果为轮询/延迟数据，不是实时行情。"
                )
                conn.execute(
                    """
                    INSERT INTO fb_notifications
                        (id, owner_id, bot_id, monitor_id, title, body, severity, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'unread', ?)
                    """,
                    (notification_id, owner_id, monitor["bot_id"], monitor_id, title[:300], body[:2000],
                     "warning" if abs(change) >= threshold * 1.5 else "info", now),
                )
                conn.execute(
                    "UPDATE fb_monitors SET last_triggered_at = ?, updated_at = ? WHERE owner_id = ? AND id = ?",
                    (now, now, owner_id, monitor_id),
                )
        if triggered:
            self._append_proactive_message(
                owner_id,
                monitor.get("bot_id"),
                title,
                body,
                notification_id=notification_id,
                monitor_id=monitor_id,
                session_id=monitor.get("session_id"),
            )
        return {"triggered": triggered, "monitor": self.get_monitor(owner_id, monitor_id)}

    def _append_proactive_message(
        self,
        owner_id: str,
        bot_id: str | None,
        title: str,
        body: str,
        *,
        notification_id: str,
        monitor_id: str,
        session_id: str | None = None,
    ) -> None:
        if not bot_id:
            return
        try:
            target_session = session_id or self.latest_session_for_bot(owner_id, bot_id)
            if not target_session:
                return
            self.append_message(
                owner_id,
                session_id=target_session,
                role="assistant",
                kind="proactive",
                content=f"**{title}**\n\n{body}",
                bot_id=bot_id,
                metadata={"notification_id": notification_id, "monitor_id": monitor_id, "source": "routine"},
            )
        except Exception:
            # Proactive conversation delivery is additive; the notification remains.
            pass

    def mark_monitor_checked(self, owner_id: str, monitor_id: str, checked_at: str, error: str | None = None) -> None:
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            conn.execute(
                "UPDATE fb_monitors SET last_checked_at = ?, last_error = ?, failure_count = 0, retry_after = NULL, updated_at = ? WHERE owner_id = ? AND id = ?",
                (checked_at, error[:500] if error else None, checked_at, owner_id, monitor_id),
            )

    def record_monitor_event(
        self,
        owner_id: str,
        monitor_id: str,
        *,
        checked_at: str,
        event_ref: str,
        title: str,
        body: str,
    ) -> bool:
        monitor = self.get_monitor(owner_id, monitor_id)
        if monitor is None:
            return False
        if monitor.get("last_event_ref") == event_ref:
            with self.db.connect(owner_id=owner_id, write=True) as conn:
                conn.execute(
                    "UPDATE fb_monitors SET last_checked_at = ?, last_error = NULL, updated_at = ? WHERE owner_id = ? AND id = ?",
                    (checked_at, checked_at, owner_id, monitor_id),
                )
            return False
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            conn.execute(
                "UPDATE fb_monitors SET last_checked_at = ?, last_triggered_at = ?, last_event_ref = ?, last_error = NULL, updated_at = ? WHERE owner_id = ? AND id = ?",
                (checked_at, checked_at, event_ref[:300], checked_at, owner_id, monitor_id),
            )
            notification_id = new_id("notif")
            conn.execute(
                """
                INSERT INTO fb_notifications
                    (id, owner_id, bot_id, monitor_id, title, body, severity, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'info', 'unread', ?)
                """,
                (notification_id, owner_id, monitor["bot_id"], monitor_id, title[:300], body[:2000], checked_at),
            )
        self._append_proactive_message(
            owner_id,
            monitor.get("bot_id"),
            title,
            body,
            notification_id=notification_id,
            monitor_id=monitor_id,
            session_id=monitor.get("session_id"),
        )
        return True

    def list_notifications(self, owner_id: str, *, bot_id: str | None = None, unread_only: bool = False, limit: int = 50) -> list[dict[str, Any]]:
        clauses, params = ["owner_id = ?"], [owner_id]
        if bot_id:
            clauses.append("bot_id = ?"); params.append(bot_id)
        if unread_only:
            clauses.append("status = 'unread'")
        params.append(max(1, min(1000, int(limit))))
        with self.db.connect(owner_id=owner_id) as conn:
            rows = conn.fetchall(
                f"SELECT * FROM fb_notifications WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT ?",
                params,
            )
        return [dict(row) for row in rows]

    def get_notification(self, owner_id: str, notification_id: str) -> dict[str, Any] | None:
        with self.db.connect(owner_id=owner_id) as conn:
            row = conn.fetchone(
                "SELECT * FROM fb_notifications WHERE owner_id = ? AND id = ?",
                (owner_id, notification_id),
            )
        return dict(row) if row else None

    def link_notification_task(self, owner_id: str, notification_id: str, task_id: str) -> None:
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            conn.execute(
                "UPDATE fb_notifications SET task_id = ?, status = 'read', read_at = ? WHERE owner_id = ? AND id = ?",
                (task_id, utc_now(), owner_id, notification_id),
            )

    def mark_notification_read(self, owner_id: str, notification_id: str) -> dict[str, Any] | None:
        now = utc_now()
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            conn.execute(
                "UPDATE fb_notifications SET status = 'read', read_at = ? WHERE owner_id = ? AND id = ? AND status = 'unread'",
                (now, owner_id, notification_id),
            )
            row = conn.fetchone("SELECT * FROM fb_notifications WHERE owner_id = ? AND id = ?", (owner_id, notification_id))
        return dict(row) if row else None

    # ----------------------------------------------------------------- tasks
    def save_plan(
        self,
        owner_id: str,
        task_id: str,
        *,
        bot_id: str | None,
        session_id: str | None,
        original_query: str,
        status: str,
        source: str,
        steps: list[Any],
        termination_condition: str = "",
    ) -> dict[str, Any]:
        now = utc_now()
        serialized_steps = [
            step.model_dump() if hasattr(step, "model_dump") else step
            for step in steps
        ]
        payload = json.dumps(serialized_steps, ensure_ascii=False, separators=(",", ":"))
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            conn.execute(
                """
                INSERT INTO fb_task_plans
                    (task_id, owner_id, bot_id, session_id, original_query, status, source,
                     steps_json, termination_condition, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status = excluded.status,
                    source = excluded.source,
                    steps_json = excluded.steps_json,
                    termination_condition = excluded.termination_condition,
                    updated_at = excluded.updated_at
                WHERE fb_task_plans.owner_id = excluded.owner_id
                """,
                (
                    task_id,
                    owner_id,
                    bot_id,
                    session_id,
                    original_query[:4000],
                    status,
                    source,
                    payload,
                    termination_condition[:500],
                    now,
                    now,
                ),
            )
        return self.get_plan(owner_id, task_id) or {}

    def get_plan(self, owner_id: str, task_id: str) -> dict[str, Any] | None:
        with self.db.connect(owner_id=owner_id) as conn:
            row = conn.fetchone(
                "SELECT * FROM fb_task_plans WHERE owner_id = ? AND task_id = ?",
                (owner_id, task_id),
            )
        return self._serialize_plan(row)

    def create_task(
        self,
        *,
        owner_id: str,
        query: str,
        session_id: str | None,
        language: str,
        research_mode: str,
        idempotency_key: str,
        zh: bool = True,
        ui_context: dict[str, Any] | None = None,
        authorization: dict[str, Any] | None = None,
        continued_from_task_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        if session_id:
            with self.db.connect(owner_id=owner_id, write=True) as conn:
                self._lock_session_write(conn, owner_id, session_id)
                lifecycle = self._ensure_session_lifecycle_locked(conn, owner_id, session_id)
                if lifecycle != "active":
                    raise ConflictError("session_delete_in_progress")
        existing = self.get_task_by_idempotency(owner_id, idempotency_key)
        if existing:
            return existing, False
        now = utc_now()
        task_id = new_id("task")
        try:
            with self.db.connect(owner_id=owner_id, write=True) as conn:
                if session_id:
                    self._lock_session_write(conn, owner_id, session_id)
                    lifecycle = self._ensure_session_lifecycle_locked(conn, owner_id, session_id)
                    if lifecycle != "active":
                        raise ConflictError("session_delete_in_progress")
                conn.execute(
                    """
                    INSERT INTO fb_tasks
                        (id, owner_id, session_id, idempotency_key, query, language,
                         research_mode, status, created_at, updated_at, ui_context_json,
                         authorization_json, continued_from_task_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        owner_id,
                        session_id,
                        idempotency_key,
                        query,
                        language,
                        research_mode,
                        now,
                        now,
                        json.dumps(ui_context, ensure_ascii=False, separators=(",", ":")) if ui_context else None,
                        json.dumps(authorization, ensure_ascii=False, separators=(",", ":")) if authorization else None,
                        continued_from_task_id,
                    ),
                )
                self._append_event_locked(
                    conn,
                    owner_id=owner_id,
                    task_id=task_id,
                    kind="status",
                    status="queued",
                    summary="任务已创建，等待执行" if zh else "Task created and queued",
                )
        except ConflictError:
            raise
        except Exception:
            # A concurrent identical request may have won the unique race.
            existing = self.get_task_by_idempotency(owner_id, idempotency_key)
            if existing:
                return existing, False
            raise
        return self.get_task(owner_id, task_id), True

    def get_task_by_idempotency(self, owner_id: str, idempotency_key: str) -> dict[str, Any] | None:
        with self.db.connect(owner_id=owner_id) as conn:
            row = conn.fetchone(
                "SELECT * FROM fb_tasks WHERE owner_id = ? AND idempotency_key = ?",
                (owner_id, idempotency_key),
            )
        return self._serialize_task(row) if row else None

    def get_task(self, owner_id: str, task_id: str) -> dict[str, Any]:
        with self.db.connect(owner_id=owner_id) as conn:
            row = conn.fetchone(
                "SELECT * FROM fb_tasks WHERE id = ? AND owner_id = ?",
                (task_id, owner_id),
            )
        return self._serialize_task(self._tasks_owner(row, owner_id))

    # ------------------------------------------------------ OpenMuse mappings
    @staticmethod
    def _serialize_openmuse_task_mapping(row: dict[str, Any] | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    def _require_openmuse_mapping_backend(self) -> None:
        if self.db.dialect == "postgres":
            raise OpenMuseMappingUnavailableError(
                "OpenMuse task mappings require a PostgreSQL server-only database role"
            )

    @staticmethod
    def _openmuse_request_id(owner_id: str, task_id: str) -> str:
        """Derive a stable upstream idempotency key from server-owned identity."""
        binding = hashlib.sha256(f"{owner_id}\0{task_id}".encode("utf-8")).hexdigest()
        return f"fb-{binding}"

    def create_openmuse_task_mapping(
        self,
        owner_id: str,
        task_id: str,
        *,
        payload_sha256: str,
    ) -> tuple[dict[str, Any], bool]:
        """Persist a secret-free binding before an OpenMuse create request.

        The request ID is derived from the authenticated owner and local task ID,
        so callers cannot supply or rebind an upstream task identity. Only the
        payload digest is persisted; prompt text and credentials never enter this
        table. A replay for the same local task must have the same payload hash.
        """
        self._require_openmuse_mapping_backend()
        task_id = str(task_id or "").strip()
        if not task_id or len(task_id) > 200:
            raise ValueError("invalid task id")
        if not isinstance(payload_sha256, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", payload_sha256):
            raise ValueError("payload_sha256 must be a SHA-256 hex digest")
        digest = payload_sha256.lower()
        request_id = self._openmuse_request_id(owner_id, task_id)
        now = utc_now()

        try:
            with self.db.connect(owner_id=owner_id, write=True) as conn:
                task = conn.fetchone(
                    "SELECT owner_id, session_id FROM fb_tasks WHERE id = ? AND owner_id = ?",
                    (task_id, owner_id),
                )
                self._tasks_owner(task, owner_id)
                if task.get("session_id"):
                    self._lock_session_write(conn, owner_id, str(task["session_id"]))
                    self._require_active_session_locked(conn, owner_id, str(task["session_id"]))
                existing = conn.fetchone(
                    "SELECT * FROM fb_openmuse_task_mappings WHERE owner_id = ? AND task_id = ?",
                    (owner_id, task_id),
                )
                if existing:
                    if existing["request_id"] != request_id or existing["payload_sha256"] != digest:
                        raise ConflictError(
                            "OpenMuse task mapping payload or idempotency binding changed"
                        )
                    return dict(existing), False
                conn.execute(
                    """
                    INSERT INTO fb_openmuse_task_mappings
                        (owner_id, task_id, request_id, payload_sha256, upstream_task_id,
                         status, integration_phase, deletion_status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, NULL, 'prepared', 'prepared', 'not_requested', ?, ?)
                    """,
                    (owner_id, task_id, request_id, digest, now, now),
                )
                row = conn.fetchone(
                    "SELECT * FROM fb_openmuse_task_mappings WHERE owner_id = ? AND task_id = ?",
                    (owner_id, task_id),
                )
        except ConflictError:
            raise
        except Exception:
            # An identical concurrent insert is idempotent; all other unique
            # collisions are reported as a binding conflict.
            existing = self.get_openmuse_task_mapping(owner_id, task_id)
            if existing:
                if existing["request_id"] == request_id and existing["payload_sha256"] == digest:
                    return existing, False
                raise ConflictError(
                    "OpenMuse task mapping payload or idempotency binding changed"
                ) from None
            raise
        return dict(row), True

    def get_openmuse_task_mapping(self, owner_id: str, task_id: str) -> dict[str, Any] | None:
        self._require_openmuse_mapping_backend()
        with self.db.connect(owner_id=owner_id) as conn:
            row = conn.fetchone(
                "SELECT * FROM fb_openmuse_task_mappings WHERE owner_id = ? AND task_id = ?",
                (owner_id, task_id),
            )
        return self._serialize_openmuse_task_mapping(row)

    def claim_openmuse_task_mapping(
        self,
        owner_id: str,
        task_id: str,
        *,
        lease_id: str,
        lease_seconds: float = 60.0,
    ) -> dict[str, Any] | None:
        """Claim one pending mapping atomically across local worker processes."""
        self._require_openmuse_mapping_backend()
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat(timespec="microseconds")
        expiry = (now_dt + timedelta(seconds=max(1.0, min(float(lease_seconds), 300.0)))).isoformat(
            timespec="microseconds"
        )
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            conn.execute(
                """
                UPDATE fb_openmuse_task_mappings
                   SET reconcile_lease_id = ?, reconcile_lease_expires_at = ?, updated_at = ?
                 WHERE owner_id = ? AND task_id = ? AND deletion_status = 'pending'
                   AND (reconcile_lease_id IS NULL OR reconcile_lease_expires_at IS NULL
                        OR reconcile_lease_expires_at <= ?)
                """,
                (lease_id, expiry, utc_now(), owner_id, task_id, now),
            )
            row = conn.fetchone(
                "SELECT * FROM fb_openmuse_task_mappings WHERE owner_id = ? AND task_id = ? "
                "AND deletion_status = 'pending' AND reconcile_lease_id = ?",
                (owner_id, task_id, lease_id),
            )
        return dict(row) if row else None

    def release_openmuse_task_mapping_claim(
        self,
        owner_id: str,
        task_id: str,
        *,
        lease_id: str,
    ) -> None:
        self._require_openmuse_mapping_backend()
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            conn.execute(
                """
                UPDATE fb_openmuse_task_mappings
                   SET reconcile_lease_id = NULL, reconcile_lease_expires_at = NULL
                 WHERE owner_id = ? AND task_id = ? AND deletion_status = 'pending'
                   AND reconcile_lease_id = ?
                """,
                (owner_id, task_id, lease_id),
            )

    def update_openmuse_task_mapping(
        self,
        owner_id: str,
        task_id: str,
        *,
        upstream_task_id: str | None = None,
        status: str | None = None,
        deletion_status: str | None = None,
        deletion_error: str | None = None,
        lease_id: str | None = None,
    ) -> dict[str, Any]:
        """Update non-secret upstream state without changing the task binding."""
        self._require_openmuse_mapping_backend()
        if upstream_task_id is not None and (
            not isinstance(upstream_task_id, str)
            or not upstream_task_id.strip()
            or len(upstream_task_id) > 200
        ):
            raise ValueError("invalid upstream task id")
        if status is not None and status not in OPENMUSE_TASK_STATUSES:
            raise ValueError("invalid OpenMuse task status")
        if deletion_status is not None and deletion_status not in OPENMUSE_DELETION_STATUSES:
            raise ValueError("invalid OpenMuse deletion status")
        if deletion_error is not None and deletion_error not in OPENMUSE_DELETION_ERRORS:
            raise ValueError("invalid OpenMuse deletion error code")

        with self.db.connect(owner_id=owner_id, write=True) as conn:
            row = conn.fetchone(
                "SELECT * FROM fb_openmuse_task_mappings WHERE owner_id = ? AND task_id = ?"
                + self._lock_clause(),
                (owner_id, task_id),
            )
            if not row:
                raise NotFoundError("OpenMuse task mapping not found")
            saved_upstream_id = row.get("upstream_task_id") or upstream_task_id
            saved_status = status or row["status"]
            saved_deletion_status = deletion_status or row["deletion_status"]
            if row["deletion_status"] == "complete":
                unchanged = (
                    (upstream_task_id is None or upstream_task_id == row.get("upstream_task_id"))
                    and (status is None or status == row["status"])
                    and (deletion_status is None or deletion_status == row["deletion_status"])
                    and (deletion_error is None or deletion_error == row.get("deletion_error", ""))
                )
                if not unchanged:
                    raise ConflictError("OpenMuse task mapping is immutable after deletion completes")
                return dict(row)
            if lease_id is not None and (
                row.get("reconcile_lease_id") != lease_id
                or not row.get("reconcile_lease_expires_at")
                or row["reconcile_lease_expires_at"]
                <= datetime.now(timezone.utc).isoformat(timespec="microseconds")
            ):
                raise ConflictError("OpenMuse deletion reconciliation lease is no longer owned")
            if upstream_task_id and row.get("upstream_task_id") not in (None, upstream_task_id):
                raise ConflictError("OpenMuse upstream task binding cannot be changed")
            next_lease_id = row.get("reconcile_lease_id")
            next_lease_expiry = row.get("reconcile_lease_expires_at")
            if deletion_status == "complete" or lease_id is not None:
                next_lease_id = None
                next_lease_expiry = None
            conn.execute(
                """
                UPDATE fb_openmuse_task_mappings
                   SET upstream_task_id = ?, status = ?, deletion_status = ?, deletion_error = ?,
                       reconcile_lease_id = ?, reconcile_lease_expires_at = ?, updated_at = ?
                 WHERE owner_id = ? AND task_id = ?
                """,
                (
                    saved_upstream_id,
                    saved_status,
                    saved_deletion_status,
                    row.get("deletion_error", "") if deletion_error is None else deletion_error,
                    next_lease_id,
                    next_lease_expiry,
                    utc_now(),
                    owner_id,
                    task_id,
                ),
            )
            saved = conn.fetchone(
                "SELECT * FROM fb_openmuse_task_mappings WHERE owner_id = ? AND task_id = ?",
                (owner_id, task_id),
            )
        return dict(saved)

    def apply_openmuse_task_projection(
        self,
        owner_id: str,
        task_id: str,
        *,
        revision: int,
        projection_sha256: str,
        status: str,
        local_projection: dict[str, Any] | None = None,
        event_kind: str | None = None,
        event_summary: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Apply one authenticated task snapshot by upstream's atomic sequence."""
        self._require_openmuse_mapping_backend()
        if isinstance(revision, bool) or not isinstance(revision, int) or not 1 <= revision <= 2**53 - 1:
            raise ValueError("invalid upstream task revision")
        if not isinstance(projection_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", projection_sha256):
            raise ValueError("invalid upstream projection digest")
        if status not in OPENMUSE_TASK_STATUSES:
            raise ValueError("invalid OpenMuse task status")
        if (event_kind is None) != (event_summary is None):
            raise ValueError("OpenMuse event kind and summary must be provided together")
        if event_kind is not None and event_kind != "openmuse_status":
            raise ValueError("invalid OpenMuse projection event kind")
        serialized_result = None
        if local_projection is not None:
            local_status = local_projection.get("status")
            if local_status not in TASK_STATUSES:
                raise ValueError("invalid projected local task status")
            result = local_projection.get("result")
            if result is not None:
                if not isinstance(result, dict):
                    raise ValueError("invalid projected local task result")
                serialized_result = json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            error_message = local_projection.get("error_message")
            if error_message is not None and not isinstance(error_message, str):
                raise ValueError("invalid projected local task error")
            attention = local_projection.get("attention")
            if attention is not None and not isinstance(attention, dict):
                raise ValueError("invalid projected local task attention")
            if local_status == "waiting_user" and (
                not isinstance(attention, dict) or not str(attention.get("question") or "").strip()
            ):
                # An upstream wait without its question cannot be answered safely.
                local_projection = {**local_projection, "status": "unknown", "attention": None}

        def persist_local_projection(conn, at: str) -> None:
            if local_projection is None:
                return
            task = conn.fetchone(
                "SELECT owner_id, research_mode FROM fb_tasks WHERE id = ? AND owner_id = ?",
                (task_id, owner_id),
            )
            self._tasks_owner(task, owner_id)
            if task.get("research_mode") != "openmuse_shared":
                raise ConflictError("OpenMuse projection may only update a shared research task")
            projected_status = local_projection["status"]
            projected_attention = local_projection.get("attention")
            attention_json = (
                json.dumps(projected_attention, ensure_ascii=False, separators=(",", ":"))
                if projected_attention is not None else None
            )
            waiting_reason = "question" if projected_attention is not None else None
            started_at = at if projected_status in {"running", *TERMINAL_STATUSES} else None
            finished_at = at if projected_status in TERMINAL_STATUSES else None
            conn.execute(
                "UPDATE fb_tasks SET status = ?, result_json = COALESCE(?, result_json), error_message = ?, "
                "attention_json = ?, waiting_reason = ?, started_at = COALESCE(started_at, ?), "
                "finished_at = CASE WHEN ? IS NULL THEN NULL ELSE COALESCE(finished_at, ?) END, updated_at = ? "
                "WHERE id = ? AND owner_id = ?",
                (
                    projected_status,
                    serialized_result,
                    local_projection.get("error_message"),
                    attention_json,
                    waiting_reason,
                    started_at,
                    finished_at,
                    finished_at,
                    at,
                    task_id,
                    owner_id,
                ),
            )
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            row = conn.fetchone(
                "SELECT * FROM fb_openmuse_task_mappings WHERE owner_id = ? AND task_id = ?"
                + self._lock_clause(),
                (owner_id, task_id),
            )
            if not row:
                raise NotFoundError("OpenMuse task mapping not found")
            if not row.get("upstream_task_id"):
                raise ConflictError("OpenMuse projection requires an immutable upstream task binding")
            if row["deletion_status"] != "not_requested":
                return dict(row), "deletion_blocked"
            current_revision = row.get("upstream_task_revision")
            current_digest = row.get("upstream_projection_sha256")
            if current_revision is not None and revision < int(current_revision):
                return dict(row), "stale"
            if current_revision is not None and revision == int(current_revision):
                if projection_sha256 != current_digest:
                    return dict(row), "conflict"
                if event_kind is not None:
                    event = conn.fetchone(
                        "SELECT event_id FROM fb_task_events WHERE task_id = ? AND openmuse_revision = ?",
                        (task_id, revision),
                    )
                    if not event:
                        self._append_event_locked(
                            conn,
                            owner_id=owner_id,
                            task_id=task_id,
                            kind=event_kind,
                            status=status,
                            summary=event_summary or "",
                            allow_after_terminal=True,
                            openmuse_revision=revision,
                        )
                persist_local_projection(conn, utc_now())
                return dict(row), "duplicate"
            conn.execute(
                "UPDATE fb_openmuse_task_mappings SET status = ?, upstream_task_revision = ?, "
                "upstream_projection_sha256 = ?, updated_at = ? WHERE owner_id = ? AND task_id = ? "
                "AND deletion_status = 'not_requested'",
                (status, revision, projection_sha256, utc_now(), owner_id, task_id),
            )
            saved = conn.fetchone(
                "SELECT * FROM fb_openmuse_task_mappings WHERE owner_id = ? AND task_id = ?",
                (owner_id, task_id),
            )
            persist_local_projection(conn, utc_now())
            if event_kind is not None:
                self._append_event_locked(
                    conn,
                    owner_id=owner_id,
                    task_id=task_id,
                    kind=event_kind,
                    status=status,
                    summary=event_summary or "",
                    allow_after_terminal=True,
                    openmuse_revision=revision,
                )
        return dict(saved), "applied"

    def compare_and_set_openmuse_integration_phase(
        self, owner_id: str, task_id: str, *, expected: str | set[str], value: str
    ) -> bool:
        """Atomically claim a side-effect stage; expired workers never replay it."""
        self._require_openmuse_mapping_backend()
        expected_phases = {expected} if isinstance(expected, str) else set(expected)
        if (
            not expected_phases
            or not expected_phases <= OPENMUSE_INTEGRATION_PHASES
            or value not in OPENMUSE_INTEGRATION_PHASES
            or any(value not in OPENMUSE_INTEGRATION_TRANSITIONS[phase] for phase in expected_phases)
        ):
            raise ValueError("invalid OpenMuse integration phase")
        placeholders = ",".join("?" for _ in expected_phases)
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            changed = conn.execute(
                "UPDATE fb_openmuse_task_mappings SET integration_phase = ?, updated_at = ? "
                f"WHERE owner_id = ? AND task_id = ? AND integration_phase IN ({placeholders}) AND deletion_status != 'complete'",
                (value, utc_now(), owner_id, task_id, *sorted(expected_phases)),
            )
            return changed.rowcount == 1

    def list_openmuse_task_mappings(
        self,
        owner_id: str,
        *,
        deletion_status: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        self._require_openmuse_mapping_backend()
        if deletion_status is not None and deletion_status not in OPENMUSE_DELETION_STATUSES:
            raise ValueError("invalid OpenMuse deletion status")
        if limit is not None:
            limit = max(1, min(100, int(limit)))
        with self.db.connect(owner_id=owner_id) as conn:
            if deletion_status is None:
                sql = "SELECT * FROM fb_openmuse_task_mappings WHERE owner_id = ? ORDER BY updated_at, created_at, task_id"
                params: list[Any] = [owner_id]
            else:
                sql = "SELECT * FROM fb_openmuse_task_mappings WHERE owner_id = ? AND deletion_status = ? ORDER BY updated_at, created_at, task_id"
                params = [owner_id, deletion_status]
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
            rows = conn.fetchall(sql, params)
        return [dict(row) for row in rows]

    # ---------------------------------------------------------- dispositions
    def record_disposition(
        self,
        owner_id: str,
        *,
        input_ref: str,
        disposition: str,
        reason_code: str | None = None,
        reason_display: str | None = None,
        task_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Record why one user input was researched, answered, clarified or dropped.

        Idempotent per ``(owner_id, input_ref)``: a replay or duplicate submit
        never creates a second disposition.
        """
        if disposition not in DISPOSITIONS:
            raise ValueError(f"unknown disposition {disposition}")
        input_ref = (input_ref or "").strip()
        if not input_ref:
            raise ValueError("input_ref is required")
        now = utc_now()
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            if session_id:
                self._lock_session_write(conn, owner_id, session_id)
                self._require_active_session_locked(conn, owner_id, session_id)
            conn.execute(
                """
                INSERT INTO fb_dispositions
                    (id, owner_id, task_id, session_id, input_ref, disposition,
                     reason_code, reason_display, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (owner_id, input_ref) DO NOTHING
                """,
                (
                    new_id("disp"),
                    owner_id,
                    task_id,
                    session_id,
                    input_ref,
                    disposition,
                    reason_code,
                    (reason_display or "")[:500] or None,
                    now,
                ),
            )
        return self.get_disposition(owner_id, input_ref)

    def finalize_disposition(
        self,
        owner_id: str,
        *,
        input_ref: str | None = None,
        task_id: str | None = None,
        disposition: str,
        reason_code: str | None = None,
        reason_display: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Upgrade a provisional ``accepted`` row to the real terminal disposition.

        Create-time records ``accepted`` so the UI has a row while research runs.
        When the run ends as dropped / direct_answer / clarify, replace that
        provisional value. Never invent a disposition that was not observed;
        never overwrite a non-accepted final value.
        """
        if disposition not in DISPOSITIONS:
            raise ValueError(f"unknown disposition {disposition}")
        if disposition == "accepted":
            # Provisional create-time value; use record_disposition instead.
            return None
        ref = (input_ref or "").strip() or None
        now = utc_now()
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            if session_id:
                self._lock_session_write(conn, owner_id, session_id)
                self._require_active_session_locked(conn, owner_id, session_id)
            row = None
            if ref:
                row = conn.fetchone(
                    "SELECT * FROM fb_dispositions WHERE owner_id = ? AND input_ref = ?",
                    (owner_id, ref),
                )
            if row is None and task_id:
                row = conn.fetchone(
                    "SELECT * FROM fb_dispositions WHERE owner_id = ? AND task_id = ? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (owner_id, task_id),
                )
            if row is None:
                # No provisional row — insert the terminal outcome directly.
                if not ref:
                    ref = f"task:{task_id}" if task_id else ""
                if not ref:
                    return None
                conn.execute(
                    """
                    INSERT INTO fb_dispositions
                        (id, owner_id, task_id, session_id, input_ref, disposition,
                         reason_code, reason_display, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (owner_id, input_ref) DO NOTHING
                    """,
                    (
                        new_id("disp"),
                        owner_id,
                        task_id,
                        session_id,
                        ref,
                        disposition,
                        reason_code,
                        (reason_display or "")[:500] or None,
                        now,
                    ),
                )
            else:
                current = str(row.get("disposition") or "")
                if current != "accepted":
                    # Already finalized (or clarify) — leave honest first write.
                    row.pop("owner_id", None)
                    return row
                conn.execute(
                    """
                    UPDATE fb_dispositions
                       SET disposition = ?,
                           reason_code = ?,
                           reason_display = ?,
                           task_id = COALESCE(task_id, ?),
                           session_id = COALESCE(session_id, ?)
                     WHERE owner_id = ? AND id = ?
                    """,
                    (
                        disposition,
                        reason_code,
                        (reason_display or "")[:500] or None,
                        task_id,
                        session_id,
                        owner_id,
                        row["id"],
                    ),
                )
                ref = row.get("input_ref") or ref
        if not ref:
            return None
        try:
            return self.get_disposition(owner_id, ref)
        except NotFoundError:
            return None

    def attach_authorization_data_sources(
        self,
        owner_id: str,
        task_id: str,
        sources: dict[str, str],
    ) -> dict[str, Any] | None:
        """Fill ``authorization.data_sources`` only when still null.

        Sources must come from evidence / providers actually used. Empty maps
        are ignored so we never freeze a fake empty catalog.
        """
        cleaned: dict[str, str] = {}
        for key, value in (sources or {}).items():
            k = str(key or "").strip()[:80]
            v = str(value or "").strip()[:200]
            if k and v:
                cleaned[k] = v
        if not cleaned:
            return None
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            row = conn.fetchone(
                "SELECT owner_id, authorization_json FROM fb_tasks WHERE id = ? AND owner_id = ?",
                (task_id, owner_id),
            )
            try:
                row = self._tasks_owner(row, owner_id)
            except NotFoundError:
                return None
            raw = row.get("authorization_json")
            try:
                auth = json.loads(raw) if raw else None
            except (TypeError, ValueError):
                auth = None
            if not isinstance(auth, dict):
                return None
            existing = auth.get("data_sources")
            if existing is not None:
                # Already frozen (including inherited snapshots) — do not rewrite.
                return self.get_task(owner_id, task_id)
            auth["data_sources"] = cleaned
            conn.execute(
                "UPDATE fb_tasks SET authorization_json = ?, updated_at = ? WHERE id = ? AND owner_id = ?",
                (
                    json.dumps(auth, ensure_ascii=False, separators=(",", ":")),
                    utc_now(),
                    task_id,
                    owner_id,
                ),
            )
        return self.get_task(owner_id, task_id)

    def get_disposition(self, owner_id: str, input_ref: str) -> dict[str, Any]:
        with self.db.connect(owner_id=owner_id) as conn:
            row = conn.fetchone(
                "SELECT * FROM fb_dispositions WHERE owner_id = ? AND input_ref = ?",
                (owner_id, input_ref),
            )
        if not row:
            raise NotFoundError("disposition not found")
        row.pop("owner_id", None)
        return row

    def list_dispositions(
        self, owner_id: str, *, session_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self.db.connect(owner_id=owner_id) as conn:
            if session_id:
                rows = conn.fetchall(
                    "SELECT * FROM fb_dispositions WHERE owner_id = ? AND session_id = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (owner_id, session_id, limit),
                )
            else:
                rows = conn.fetchall(
                    "SELECT * FROM fb_dispositions WHERE owner_id = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (owner_id, limit),
                )
        for row in rows:
            row.pop("owner_id", None)
        return rows

    # ------------------------------------------------------- platform state
    def get_platform_state(self) -> dict[str, Any]:
        with self.db.connect(owner_id=None, bypass_rls=True) as conn:
            row = conn.fetchone("SELECT * FROM fb_platform_state WHERE id = 'platform'")
        return {
            "paused": bool(row.get("paused")) if row else False,
            "paused_by": row.get("paused_by") if row else None,
            "pause_reason": row.get("pause_reason") if row else None,
            "paused_at": row.get("paused_at") if row else None,
            "updated_at": row.get("updated_at") if row else None,
        }

    def set_platform_pause(
        self, *, paused: bool, by: str, reason: str | None = None
    ) -> dict[str, Any]:
        now = utc_now()
        with self.db.connect(owner_id=None, bypass_rls=True, write=True) as conn:
            conn.execute(
                """
                INSERT INTO fb_platform_state
                    (id, paused, paused_by, pause_reason, paused_at, updated_at)
                VALUES ('platform', ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO UPDATE SET
                    paused = excluded.paused,
                    paused_by = excluded.paused_by,
                    pause_reason = excluded.pause_reason,
                    paused_at = excluded.paused_at,
                    updated_at = excluded.updated_at
                """,
                (1 if paused else 0, by, reason, now if paused else None, now),
            )
        return self.get_platform_state()

    def append_message(
        self,
        owner_id: str,
        *,
        session_id: str,
        role: str,
        content: str,
        kind: str = "message",
        task_id: str | None = None,
        bot_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        if role not in {"user", "assistant", "system"}:
            raise ValueError("invalid message role")
        if not session_id:
            raise ValueError("session_id is required")
        text = str(content or "").strip()
        if not text:
            raise ValueError("message content is required")
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            self._lock_session_write(conn, owner_id, session_id)
            self._require_active_session_locked(conn, owner_id, session_id)
            row = conn.fetchone(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM fb_messages WHERE owner_id = ? AND session_id = ?",
                (owner_id, session_id),
            )
            sequence = int((row or {}).get("next_sequence") or 1)
            message_id = new_id("msg")
            created = created_at or utc_now()
            conn.execute(
                """
                INSERT INTO fb_messages
                    (id, owner_id, bot_id, session_id, role, kind, content, task_id,
                     metadata_json, sequence, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    owner_id,
                    bot_id,
                    session_id,
                    role,
                    kind,
                    text[:20000],
                    task_id,
                    json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) if metadata else None,
                    sequence,
                    created,
                ),
            )
            if role == "assistant" and kind in {"answer", "proactive"}:
                conn.execute(
                    "UPDATE fb_threads SET unread = unread + 1 WHERE owner_id = ? AND session_id = ? AND last_read_at IS NOT NULL",
                    (owner_id, session_id),
                )
        return {
            "id": message_id,
            "owner_id": owner_id,
            "bot_id": bot_id,
            "session_id": session_id,
            "role": role,
            "kind": kind,
            "content": text[:20000],
            "task_id": task_id,
            "metadata": metadata or {},
            "sequence": sequence,
            "created_at": created,
        }

    def latest_session_for_bot(self, owner_id: str, bot_id: str) -> str | None:
        with self.db.connect(owner_id=owner_id) as conn:
            row = conn.fetchone(
                "SELECT session_id FROM fb_bot_sessions WHERE owner_id = ? AND bot_id = ? ORDER BY updated_at DESC LIMIT 1",
                (owner_id, bot_id),
            )
        return str(row["session_id"]) if row else None

    def list_messages(self, owner_id: str, session_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        with self.db.connect(owner_id=owner_id) as conn:
            rows = conn.fetchall(
                """
                SELECT * FROM fb_messages
                 WHERE owner_id = ? AND session_id = ?
                 ORDER BY sequence ASC
                 LIMIT ?
                """,
                (owner_id, session_id, limit),
            )
        result = []
        for row in rows:
            result.append({
                "id": row["id"],
                "bot_id": row.get("bot_id"),
                "session_id": row["session_id"],
                "role": row["role"],
                "kind": row["kind"],
                "content": row["content"],
                "task_id": row.get("task_id"),
                "metadata": json.loads(row.get("metadata_json") or "{}"),
                "sequence": row["sequence"],
                "created_at": row["created_at"],
            })
        return result

    def backfill_session_messages(self, owner_id: str, session_id: str) -> int:
        """Project legacy task cards into the message thread.

        This is idempotent per task: existing user/answer messages for a task
        are left untouched, missing turns are appended in task order.  It is
        intentionally conservative about activities: only completed research
        steps are projected, and no model call or data fetch is made.
        """
        tasks = list(reversed(self.list_tasks(owner_id, session_id=session_id, limit=100)))
        if not tasks:
            return 0
        existing = self.list_messages(owner_id, session_id, limit=500)
        user_tasks = {str(row.get("task_id")) for row in existing if row.get("role") == "user" and row.get("task_id")}
        answer_tasks = {
            str(row.get("task_id")) for row in existing
            if row.get("role") == "assistant" and row.get("kind") == "answer" and row.get("task_id")
        }
        activity_keys = {
            (str(row.get("task_id")), str((row.get("metadata") or {}).get("tool") or ""))
            for row in existing
            if row.get("kind") == "activity" and row.get("task_id")
        }
        created = 0
        for task in tasks:
            task_id = str(task.get("id") or "")
            if not task_id:
                continue
            query = str(task.get("query") or "").strip()
            if query and task_id not in user_tasks:
                self.append_message(
                    owner_id,
                    session_id=session_id,
                    role="user",
                    kind="message",
                    content=query,
                    task_id=task_id,
                    bot_id=None,
                    metadata={"source": "backfill"},
                    created_at=task.get("created_at"),
                )
                created += 1
            result = task.get("result") or {}
            steps = result.get("research_steps") or []
            for step in steps:
                if not isinstance(step, dict):
                    continue
                tool = str(step.get("tool") or "")
                if not tool or str(step.get("status") or "") != "completed":
                    continue
                key = (task_id, tool)
                if key in activity_keys:
                    continue
                self.append_message(
                    owner_id,
                    session_id=session_id,
                    role="assistant",
                    kind="activity",
                    content=f"{tool} 已完成",
                    task_id=task_id,
                    metadata={"tool": tool, "status": "completed", "source": "backfill"},
                    created_at=task.get("created_at"),
                )
                activity_keys.add(key)
                created += 1
            summary = str(result.get("summary") or "").strip()
            if summary and task_id not in answer_tasks:
                self.append_message(
                    owner_id,
                    session_id=session_id,
                    role="assistant",
                    kind="answer",
                    content=summary,
                    task_id=task_id,
                    metadata={"source": "backfill", "intent": result.get("intent")},
                    created_at=task.get("finished_at") or task.get("updated_at") or task.get("created_at"),
                )
                created += 1
        return created

    def backfill_all_messages(self, owner_id: str | None = None) -> dict[str, int]:
        """Backfill every session that still only exists as legacy tasks."""
        clause, params = "", []
        if owner_id:
            clause = " AND owner_id = ?"
            params.append(owner_id)
        with self.db.connect(owner_id=owner_id) as conn:
            rows = conn.fetchall(
                f"""
                SELECT DISTINCT owner_id, session_id
                  FROM fb_tasks
                 WHERE session_id IS NOT NULL AND session_id <> ''{clause}
                """,
                params,
            )
        sessions = 0
        messages = 0
        for row in rows:
            created = self.backfill_session_messages(str(row["owner_id"]), str(row["session_id"]))
            if created:
                sessions += 1
                messages += created
        return {"sessions": sessions, "messages": messages}

    def list_tasks(
        self, owner_id: str, *, session_id: str | None = None, limit: int = 30
    ) -> list[dict[str, Any]]:
        limit = max(1, min(100, int(limit)))
        with self.db.connect(owner_id=owner_id) as conn:
            if session_id:
                rows = conn.fetchall(
                    """
                    SELECT * FROM fb_tasks
                     WHERE owner_id = ? AND session_id = ?
                     ORDER BY created_at DESC
                     LIMIT ?
                    """,
                    (owner_id, session_id, limit),
                )
            else:
                rows = conn.fetchall(
                    """
                    SELECT * FROM fb_tasks
                     WHERE owner_id = ?
                     ORDER BY created_at DESC
                     LIMIT ?
                    """,
                    (owner_id, limit),
                )
        return [self._serialize_task(row) for row in reversed(rows)]

    def list_artifacts(self, owner_id: str, *, bot_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Completed research outputs, scoped to a Bot's conversations."""
        limit = max(1, min(100, int(limit)))
        with self.db.connect(owner_id=owner_id) as conn:
            if bot_id:
                rows = conn.fetchall(
                    """
                    SELECT t.* FROM fb_tasks t
                      JOIN fb_bot_sessions s
                        ON s.session_id = t.session_id AND s.owner_id = t.owner_id AND s.bot_id = ?
                     WHERE t.owner_id = ? AND t.status IN ('completed', 'partial')
                       AND t.result_json IS NOT NULL
                     ORDER BY t.created_at DESC LIMIT ?
                    """,
                    (bot_id, owner_id, limit),
                )
            else:
                rows = conn.fetchall(
                    """
                    SELECT * FROM fb_tasks
                     WHERE owner_id = ? AND status IN ('completed', 'partial')
                       AND result_json IS NOT NULL
                     ORDER BY created_at DESC LIMIT ?
                    """,
                    (owner_id, limit),
                )
        artifacts = []
        for row in rows:
            try:
                result = json.loads(row.get("result_json") or "{}")
            except json.JSONDecodeError:
                result = {}
            if not isinstance(result, dict):
                continue
            artifacts.append({
                "id": row["id"],
                "session_id": row.get("session_id"),
                "title": row.get("query") or "",
                "status": row.get("status"),
                "created_at": row.get("created_at"),
                "summary": str(result.get("summary") or "")[:1200],
                "evidence_count": len(result.get("evidence") or []),
                "data_gap_count": len(result.get("data_gaps") or []),
            })
        return artifacts

    def list_sessions(self, owner_id: str, *, bot_id: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
        limit = max(1, min(100, int(limit)))
        with self.db.connect(owner_id=owner_id) as conn:
            if bot_id:
                rows = conn.fetchall(
                    """
                    SELECT t.session_id, COUNT(*) AS task_count, MAX(t.updated_at) AS updated_at
                      FROM fb_tasks t
                      JOIN fb_bot_sessions s
                        ON s.session_id = t.session_id AND s.owner_id = t.owner_id AND s.bot_id = ?
                     WHERE t.owner_id = ? AND t.session_id IS NOT NULL AND t.session_id <> ''
                     GROUP BY t.session_id
                     ORDER BY MAX(t.updated_at) DESC
                     LIMIT ?
                    """,
                    (bot_id, owner_id, limit),
                )
            else:
                rows = conn.fetchall(
                    """
                    SELECT session_id, COUNT(*) AS task_count, MAX(updated_at) AS updated_at
                      FROM fb_tasks
                     WHERE owner_id = ? AND session_id IS NOT NULL AND session_id <> ''
                     GROUP BY session_id
                     ORDER BY MAX(updated_at) DESC
                     LIMIT ?
                    """,
                    (owner_id, limit),
                )
            sessions = []
            for row in rows:
                session_id = str(row["session_id"])
                first = conn.fetchone(
                    """
                    SELECT query, status, created_at
                      FROM fb_tasks
                     WHERE owner_id = ? AND session_id = ?
                     ORDER BY created_at ASC
                     LIMIT 1
                    """,
                    (owner_id, session_id),
                )
                latest = conn.fetchone(
                    """
                    SELECT status, updated_at
                      FROM fb_tasks
                     WHERE owner_id = ? AND session_id = ?
                     ORDER BY created_at DESC
                     LIMIT 1
                    """,
                    (owner_id, session_id),
                )
                sessions.append({
                    "id": session_id,
                    "title": str((first or {}).get("query") or "").strip() or "未命名对话",
                    "updated_at": (latest or row).get("updated_at"),
                    "task_count": int(row["task_count"] or 0),
                    "last_status": (latest or {}).get("status"),
                    "kind": "main",
                    "parent_session_id": None,
                    "folder": None,
                    "approval_mode": "ask",
                    "research_mode": "quick",
                    "pinned": False,
                    "archived": False,
                    "unread": 0,
                })
            thread_rows = conn.fetchall(
                f"SELECT * FROM fb_threads WHERE {'owner_id = ?' if not bot_id else 'owner_id = ? AND bot_id = ?'} ORDER BY updated_at DESC LIMIT ?",
                ([owner_id, bot_id, limit] if bot_id else [owner_id, limit]),
            )
            known = {item["id"] for item in sessions}
            for thread in thread_rows:
                if thread["session_id"] in known:
                    for item in sessions:
                        if item["id"] == thread["session_id"]:
                            item["kind"] = thread["kind"]
                            item["parent_session_id"] = thread.get("parent_session_id")
                            item["title"] = thread["title"] or item["title"]
                            item["folder"] = thread.get("folder")
                            item["approval_mode"] = thread.get("approval_mode") or "ask"
                            item["research_mode"] = thread.get("research_mode") or "quick"
                            item["pinned"] = bool(thread.get("pinned"))
                            item["archived"] = bool(thread.get("archived"))
                            item["unread"] = int(thread.get("unread") or 0)
                    continue
                sessions.append({
                    "id": thread["session_id"],
                    "bot_id": thread.get("bot_id"),
                    "title": thread["title"],
                    "updated_at": thread["updated_at"],
                    "task_count": 0,
                    "last_status": None,
                    "kind": thread["kind"],
                    "parent_session_id": thread.get("parent_session_id"),
                    "folder": thread.get("folder"),
                    "approval_mode": thread.get("approval_mode") or "ask",
                    "research_mode": thread.get("research_mode") or "quick",
                    "pinned": bool(thread.get("pinned")),
                    "archived": bool(thread.get("archived")),
                    "unread": int(thread.get("unread") or 0),
                })
            # A conversation belongs to exactly one Bot; the client uses this to
            # line the header up with the stored session instead of probing.
            missing = [item["id"] for item in sessions if not item.get("bot_id")]
            if missing:
                placeholders = ",".join("?" for _ in missing)
                bound = conn.fetchall(
                    f"SELECT session_id, bot_id FROM fb_bot_sessions WHERE owner_id = ? AND session_id IN ({placeholders})",
                    [owner_id, *missing],
                )
                owner_of = {str(row["session_id"]): row["bot_id"] for row in bound}
                for item in sessions:
                    item.setdefault("bot_id", None)
                    if not item["bot_id"]:
                        item["bot_id"] = owner_of.get(item["id"])
            for item in sessions:
                item.setdefault("bot_id", None)
        return sessions

    def transition(
        self,
        owner_id: str,
        task_id: str,
        to_status: str,
        *,
        reason: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        summary: str | None = None,
        zh: bool = True,
        waiting_reason: str | None = None,
        attention: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if to_status not in TASK_STATUSES:
            raise ValueError(f"unknown task status {to_status}")
        if to_status == "dropped" and not (reason or "").strip():
            raise ValueError("dropped requires a user-visible reason")
        if to_status == "waiting_user":
            if waiting_reason not in WAITING_REASONS:
                raise ValueError(f"unknown waiting_reason {waiting_reason}")
            if not isinstance(attention, dict) or not str(attention.get("question") or "").strip():
                raise ValueError("waiting_user requires a structured attention question")
        now = utc_now()
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            current = conn.fetchone(
                "SELECT status, owner_id FROM fb_tasks WHERE id = ? AND owner_id = ?",
                (task_id, owner_id),
            )
            current = self._tasks_owner(current, owner_id)
            if to_status not in ALLOWED_TRANSITIONS.get(current["status"], set()):
                return None
            sets = ["status = ?", "updated_at = ?"]
            params: list[Any] = [to_status, now]
            if reason is not None:
                sets.append("status_reason = ?")
                params.append(reason)
            if error_code is not None:
                sets.append("error_code = ?")
                params.append(error_code)
            if error_message is not None:
                sets.append("error_message = ?")
                params.append(error_message[:1000])
            if to_status == "running":
                sets.append("started_at = ?")
                params.append(now)
            if to_status in TERMINAL_STATUSES:
                sets.append("finished_at = ?")
                params.append(now)
            if to_status == "waiting_user":
                sets.append("waiting_reason = ?")
                params.append(waiting_reason)
                sets.append("attention_json = ?")
                params.append(json.dumps(attention, ensure_ascii=False, separators=(",", ":")))
            else:
                sets.append("waiting_reason = NULL")
                sets.append("attention_json = NULL")
            params.extend([task_id, owner_id])
            conn.execute(
                f"UPDATE fb_tasks SET {', '.join(sets)} WHERE id = ? AND owner_id = ?",
                params,
            )
            labels = {
                "running": ("正在执行研究任务", "Research task is running"),
                "waiting_user": ("等待你补充信息", "Waiting for your input"),
                "completed": ("任务已完成", "Task completed"),
                "partial": ("任务部分完成", "Task partially completed"),
                "dropped": ("无需处理", "Nothing to do"),
                "failed": ("任务失败", "Task failed"),
                "cancelled": ("任务已取消", "Task cancelled"),
                "unknown": ("任务状态未知", "Task status unknown"),
            }
            label = summary or labels.get(to_status, (to_status, to_status))[0 if zh else 1]
            self._append_event_locked(
                conn,
                owner_id=owner_id,
                task_id=task_id,
                kind="status",
                status=to_status,
                summary=label,
                allow_after_terminal=True,
            )
        return self.get_task(owner_id, task_id)

    def append_event(
        self,
        owner_id: str,
        task_id: str,
        *,
        kind: str,
        status: str,
        summary: str,
        step_id: str | None = None,
        source_refs: Optional[list[dict[str, Any]]] = None,
        allow_after_terminal: bool = False,
        expected_openmuse_revision: int | None = None,
    ) -> dict[str, Any] | None:
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            if expected_openmuse_revision is not None:
                mapping = conn.fetchone(
                    "SELECT upstream_task_revision, deletion_status FROM fb_openmuse_task_mappings "
                    "WHERE owner_id = ? AND task_id = ?" + self._lock_clause(),
                    (owner_id, task_id),
                )
                if (
                    not mapping
                    or mapping["deletion_status"] != "not_requested"
                    or mapping["upstream_task_revision"] != expected_openmuse_revision
                ):
                    return None
            self._tasks_owner(
                conn.fetchone(
                    "SELECT owner_id FROM fb_tasks WHERE id = ? AND owner_id = ?",
                    (task_id, owner_id),
                ),
                owner_id,
            )
            return self._append_event_locked(
                conn,
                owner_id=owner_id,
                task_id=task_id,
                kind=kind,
                status=status,
                summary=summary,
                step_id=step_id,
                source_refs=source_refs,
                allow_after_terminal=allow_after_terminal,
            )

    def list_events(
        self, owner_id: str, task_id: str, *, after_sequence: int = 0
    ) -> list[dict[str, Any]]:
        self.get_task(owner_id, task_id)
        with self.db.connect(owner_id=owner_id) as conn:
            rows = conn.fetchall(
                """
                SELECT * FROM fb_task_events
                 WHERE owner_id = ? AND task_id = ? AND sequence > ?
                 ORDER BY sequence ASC
                """,
                (owner_id, task_id, max(0, int(after_sequence))),
            )
        return [self._serialize_event(row) for row in rows]

    def request_cancel(self, owner_id: str, task_id: str, *, zh: bool = True) -> dict[str, Any]:
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            row = conn.fetchone(
                "SELECT * FROM fb_tasks WHERE id = ? AND owner_id = ?",
                (task_id, owner_id),
            )
            row = self._tasks_owner(row, owner_id)
            if row["status"] in TERMINAL_STATUSES:
                return self._serialize_task(row)
            if not row["cancel_requested"]:
                conn.execute(
                    "UPDATE fb_tasks SET cancel_requested = 1, updated_at = ? WHERE id = ? AND owner_id = ?",
                    (utc_now(), task_id, owner_id),
                )
                self._append_event_locked(
                    conn,
                    owner_id=owner_id,
                    task_id=task_id,
                    kind="notice",
                    status="cancelling",
                    summary=(
                        "已收到取消请求；正在停止后续动作，已发出的外部请求无法保证撤销"
                        if zh
                        else "Cancellation requested. Remaining actions stop; external calls already sent cannot be guaranteed to be revoked."
                    ),
                )
        return self.get_task(owner_id, task_id)

    def task_is_cancelled(self, owner_id: str, task_id: str) -> bool:
        with self.db.connect(owner_id=owner_id) as conn:
            row = conn.fetchone(
                "SELECT cancel_requested FROM fb_tasks WHERE id = ? AND owner_id = ?",
                (task_id, owner_id),
            )
        return bool(row and row.get("cancel_requested"))

    def close_running_steps(
        self,
        owner_id: str,
        task_id: str,
        *,
        status: str = "completed",
        summary: str | None = None,
    ) -> None:
        """Close stages that remained open when the task ended.

        A cancelled or failed task must not leave stale "in progress" rows.
        """
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            rows = conn.fetchall(
                """
                SELECT e.step_id, e.status, e.display_summary, e.sequence
                  FROM fb_task_events e
                  JOIN (
                        SELECT step_id, MAX(sequence) AS last_sequence
                          FROM fb_task_events
                         WHERE owner_id = ? AND task_id = ? AND step_id IS NOT NULL
                         GROUP BY step_id
                       ) latest
                    ON latest.step_id = e.step_id
                   AND latest.last_sequence = e.sequence
                 WHERE e.owner_id = ? AND e.task_id = ? AND e.status = 'running'
                """,
                (owner_id, task_id, owner_id, task_id),
            )
            for row in rows:
                self._append_event_locked(
                    conn,
                    owner_id=owner_id,
                    task_id=task_id,
                    kind="step",
                    status=status,
                    summary=summary or row["display_summary"],
                    step_id=row["step_id"],
                    allow_after_terminal=True,
                )

    def record_usage(
        self, owner_id: str, task_id: str, *, input_tokens: int, output_tokens: int, cost_usd: float
    ) -> None:
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            conn.execute(
                """
                UPDATE fb_tasks
                   SET input_tokens = ?, output_tokens = ?, cost_usd = ?, updated_at = ?
                 WHERE id = ? AND owner_id = ?
                """,
                (
                    max(0, int(input_tokens)),
                    max(0, int(output_tokens)),
                    max(0.0, float(cost_usd)),
                    utc_now(),
                    task_id,
                    owner_id,
                ),
            )

    def usage_since(self, owner_id: str, since_iso: str) -> dict[str, Any]:
        with self.db.connect(owner_id=owner_id) as conn:
            row = conn.fetchone(
                """
                SELECT COUNT(*) AS task_count,
                       COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(cost_usd), 0) AS cost_usd
                  FROM fb_tasks
                 WHERE owner_id = ? AND created_at >= ?
                """,
                (owner_id, since_iso),
            )
        return row or {"task_count": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0}

    def check_daily_limits(
        self, owner_id: str, *, max_tasks: int, max_tokens: int
    ) -> dict[str, Any]:
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        usage = self.usage_since(owner_id, today.isoformat(timespec="seconds"))
        tasks = int(usage.get("task_count") or 0)
        tokens = int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
        if tasks >= max_tasks:
            raise LimitError(
                "daily_task_limit",
                f"今日任务数已达到上限（{max_tasks}）。请明天再试或调整服务端限额。",
            )
        if max_tokens > 0 and tokens >= max_tokens:
            raise LimitError(
                "daily_token_limit",
                f"今日模型用量已达到上限（{max_tokens} tokens）。请明天再试或调整服务端限额。",
            )
        return {**usage, "token_total": tokens, "task_limit": max_tasks, "token_limit": max_tokens}

    def recover_interrupted_tasks(self) -> int:
        """Reconcile tasks left running by a previous process.

        Tasks with a persisted, incomplete plan become resumable instead of
        being discarded as ``unknown``. Their completed steps stay completed;
        non-terminal steps are reset to pending. Tasks without a plan keep the
        old safe behavior.
        """
        now = utc_now()
        recovered = 0
        # This is a server maintenance path rather than a user request, so the
        # PostgreSQL connection uses its own role instead of request claims.
        with self.db.connect(owner_id=None, write=True, bypass_rls=True) as conn:
            rows = conn.fetchall(
                "SELECT id, owner_id, status, research_mode FROM fb_tasks WHERE status IN ('queued', 'running', 'waiting_user')"
            )
            for row in rows:
                if row.get("research_mode") == "openmuse_shared":
                    # The upstream coordinator owns this durable task. Preserve
                    # its status/mapping for OpenMuse reconciliation on restart.
                    continue
                plan_row = conn.fetchone(
                    "SELECT * FROM fb_task_plans WHERE task_id = ? AND owner_id = ?",
                    (row["id"], row["owner_id"]),
                )
                plan_steps = self._parse_plan_steps(plan_row)
                resumable = (
                    row["status"] in {"queued", "running"}
                    and plan_row is not None
                    and plan_row.get("status") != "completed"
                    and bool(plan_steps)
                )
                if not resumable:
                    conn.execute(
                        """
                        UPDATE fb_tasks
                           SET status = 'unknown', status_reason = 'server_restarted', updated_at = ?
                         WHERE id = ? AND owner_id = ?
                        """,
                        (now, row["id"], row["owner_id"]),
                    )
                    self._append_event_locked(
                        conn,
                        owner_id=row["owner_id"],
                        task_id=row["id"],
                        kind="status",
                        status="unknown",
                        summary="服务进程重启，无法确认任务是否仍在执行；请重新发起任务",
                        allow_after_terminal=True,
                    )
                    recovered += 1
                    continue

                for step in plan_steps:
                    if step.get("status") in {"running", "retry", "failed"}:
                        step["status"] = "pending"
                        step["attempts"] = 0
                        step["last_error"] = None
                conn.execute(
                    """
                    UPDATE fb_task_plans
                       SET status = 'partial', steps_json = ?, updated_at = ?
                     WHERE task_id = ? AND owner_id = ?
                    """,
                    (
                        json.dumps(plan_steps, ensure_ascii=False, separators=(",", ":")),
                        now,
                        row["id"],
                        row["owner_id"],
                    ),
                )
                conn.execute(
                    """
                    UPDATE fb_tasks
                       SET status = 'queued', status_reason = 'resume_pending',
                           started_at = NULL, finished_at = NULL,
                           cancel_requested = 0, updated_at = ?
                     WHERE id = ? AND owner_id = ?
                    """,
                    (now, row["id"], row["owner_id"]),
                )
                self._append_event_locked(
                    conn,
                    owner_id=row["owner_id"],
                    task_id=row["id"],
                    kind="status",
                    status="queued",
                    summary="服务进程重启，已保留任务清单；将从断点继续执行",
                    allow_after_terminal=True,
                )
                recovered += 1
        return recovered

    @staticmethod
    def _parse_plan_steps(plan_row: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not plan_row:
            return []
        raw = plan_row.get("steps_json") or "[]"
        try:
            steps = json.loads(raw)
        except (TypeError, ValueError):
            return []
        return steps if isinstance(steps, list) else []

    def list_resumable_tasks(self) -> list[dict[str, Any]]:
        with self.db.connect(owner_id=None, bypass_rls=True) as conn:
            rows = conn.fetchall(
                "SELECT id, owner_id, session_id FROM fb_tasks WHERE status = 'queued' AND status_reason = 'resume_pending' AND research_mode != 'openmuse_shared'"
            )
        return [dict(row) for row in rows]

    def resume_task(self, owner_id: str, task_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """Prepare a terminal/partial task to continue from its saved plan."""
        now = utc_now()
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            task = conn.fetchone(
                "SELECT * FROM fb_tasks WHERE id = ? AND owner_id = ?",
                (task_id, owner_id),
            )
            if task is None:
                raise NotFoundError("task not found")
            if task["status"] in {"completed", "cancelled", "running"}:
                raise ValueError(f"task_not_resumable:{task['status']}")
            plan_row = conn.fetchone(
                "SELECT * FROM fb_task_plans WHERE task_id = ? AND owner_id = ?",
                (task_id, owner_id),
            )
            steps = self._parse_plan_steps(plan_row)
            if plan_row is None or not steps:
                raise ValueError("plan_unavailable")
            pending = False
            for step in steps:
                if step.get("status") not in {"completed", "skipped"}:
                    step["status"] = "pending"
                    step["attempts"] = 0
                    step["last_error"] = None
                    pending = True
            if not pending:
                raise ValueError("no_pending_steps")
            conn.execute(
                """
                UPDATE fb_task_plans
                   SET status = 'partial', steps_json = ?, updated_at = ?
                 WHERE task_id = ? AND owner_id = ?
                """,
                (
                    json.dumps(steps, ensure_ascii=False, separators=(",", ":")),
                    now,
                    task_id,
                    owner_id,
                ),
            )
            conn.execute(
                """
                UPDATE fb_tasks
                   SET status = 'queued', status_reason = 'resume_requested',
                       error_code = NULL, error_message = NULL,
                       finished_at = NULL, cancel_requested = 0, updated_at = ?
                 WHERE id = ? AND owner_id = ?
                """,
                (now, task_id, owner_id),
            )
            self._append_event_locked(
                conn,
                owner_id=owner_id,
                task_id=task_id,
                kind="status",
                status="queued",
                summary="已从任务清单恢复，等待继续执行",
                allow_after_terminal=True,
            )
        return self.get_task(owner_id, task_id), self.get_plan(owner_id, task_id) or {}

    # ---------------------------------------------------------------- memory
    def add_direct_memory(
        self,
        owner_id: str,
        *,
        kind: str,
        text: str,
        source_text: str,
        source_ref: str | None,
        bot_id: str | None = None,
        zh: bool = True,
    ) -> dict[str, Any]:
        if kind not in MEMORY_KINDS:
            raise ValueError(f"unsupported memory kind {kind}")
        text = _normalize_display(text)
        if not text:
            raise ValueError("memory text is empty")
        now = utc_now()
        digest = _text_hash(kind, text)
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            existing = conn.fetchone(
                "SELECT * FROM fb_memories WHERE owner_id = ? AND kind = ? AND text_hash = ?",
                (owner_id, kind, digest),
            )
            if existing and existing["deletion_state"] == "active":
                return self._serialize_memory(existing)
            memory_id = new_id("mem")
            conn.execute(
                """
                INSERT INTO fb_memories
                    (id, owner_id, bot_id, kind, text, source_text, source_ref, status,
                     version, text_hash, created_at, updated_at, confirmed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'confirmed', 1, ?, ?, ?, ?)
                """,
                (memory_id, owner_id, bot_id, kind, text, source_text, source_ref, digest, now, now, now),
            )
            conn.execute(
                """
                INSERT INTO fb_memory_versions
                    (id, memory_id, owner_id, version, text, source_text, reason, created_at)
                VALUES (?, ?, ?, 1, ?, ?, 'explicit_user_statement', ?)
                """,
                (new_id("memv"), memory_id, owner_id, text, source_text, now),
            )
            self._index_memory_locked(conn, owner_id, memory_id, text)
            self._audit_locked(conn, owner_id, "memory.created", memory_id, f"kind={kind}")
        self.cache.invalidate(owner_id)
        return self.get_memory(owner_id, memory_id)

    def add_candidate(
        self,
        owner_id: str,
        *,
        task_id: str | None,
        proposed_text: str,
        rationale: str | None,
        source_text: str | None,
        source_ref: str | None,
        bot_id: str | None = None,
    ) -> dict[str, Any] | None:
        proposed_text = _normalize_display(proposed_text)
        if not proposed_text:
            return None
        # A model-proposed view is never a user view. Only pending rows are
        # created here; confirmation is a separate user command.
        digest = hashlib.sha256(_normalize_text(proposed_text).encode("utf-8")).hexdigest()
        now = utc_now()
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            existing = conn.fetchone(
                """
                SELECT * FROM fb_memory_candidates
                 WHERE owner_id = ? AND status = 'pending'
                   AND lower(proposed_text) = lower(?)
                 LIMIT 1
                """,
                (owner_id, proposed_text),
            )
            if existing:
                return self._serialize_candidate(existing)
            candidate_id = new_id("cand")
            conn.execute(
                """
                INSERT INTO fb_memory_candidates
                    (id, owner_id, bot_id, task_id, source_message_ref, proposed_text,
                     rationale, source_text, status, version, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 1, ?)
                """,
                (
                    candidate_id,
                    owner_id,
                    bot_id,
                    task_id,
                    source_ref,
                    proposed_text,
                    rationale,
                    source_text,
                    now,
                ),
            )
            self._audit_locked(conn, owner_id, "candidate.created", candidate_id, f"sha={digest[:12]}")
        return self.get_candidate(owner_id, candidate_id)

    def get_candidate(self, owner_id: str, candidate_id: str) -> dict[str, Any]:
        with self.db.connect(owner_id=owner_id) as conn:
            row = conn.fetchone(
                "SELECT * FROM fb_memory_candidates WHERE id = ? AND owner_id = ?",
                (candidate_id, owner_id),
            )
        return self._serialize_candidate(self._candidate_owner(row, owner_id))

    def list_candidates(self, owner_id: str, *, status: str = "pending", bot_id: str | None = None, include_legacy: bool = False) -> list[dict[str, Any]]:
        if status not in CANDIDATE_STATUSES:
            raise ValueError("invalid candidate status")
        clause, params = "", [owner_id, status]
        if bot_id:
            clause = " AND (bot_id = ?" + (" OR bot_id IS NULL" if include_legacy else "") + ")"
            params.append(bot_id)
        with self.db.connect(owner_id=owner_id) as conn:
            rows = conn.fetchall(
                f"""
                SELECT * FROM fb_memory_candidates
                 WHERE owner_id = ? AND status = ?{clause}
                 ORDER BY created_at ASC
                """,
                params,
            )
        return [self._serialize_candidate(row) for row in rows]

    def confirm_candidate(
        self,
        owner_id: str,
        candidate_id: str,
        *,
        expected_version: int,
        final_text: str,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        final_text = _normalize_display(final_text)
        if not final_text:
            raise ValueError("final_text is required")
        now = utc_now()
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            candidate = conn.fetchone(
                "SELECT * FROM fb_memory_candidates WHERE id = ? AND owner_id = ?" + self._lock_clause(),
                (candidate_id, owner_id),
            )
            candidate = self._candidate_owner(candidate, owner_id)
            duplicate = conn.fetchone(
                "SELECT * FROM fb_memory_candidates WHERE owner_id = ? AND idempotency_key = ?",
                (owner_id, idempotency_key),
            )
            if duplicate:
                if duplicate["id"] == candidate_id and duplicate["memory_id"]:
                    memory = conn.fetchone(
                        "SELECT * FROM fb_memories WHERE id = ? AND owner_id = ?",
                        (duplicate["memory_id"], owner_id),
                    )
                    if memory:
                        return self._serialize_candidate(duplicate), self._serialize_memory(memory)
                raise ConflictError("该幂等键已用于其他记忆操作")
            if candidate["status"] != "pending":
                raise ConflictError(
                    "候选观点已被处理，不能重复确认",
                    current_version=int(candidate["version"]),
                )
            if int(candidate["version"]) != int(expected_version):
                raise ConflictError(
                    "候选观点版本已变化，请刷新后重试",
                    current_version=int(candidate["version"]),
                )
            memory = self._upsert_confirmed_memory_locked(
                conn,
                owner_id=owner_id,
                bot_id=candidate.get("bot_id"),
                text=final_text,
                source_text=candidate.get("source_text") or candidate["proposed_text"],
                source_ref=candidate.get("source_message_ref") or candidate_id,
                reason="candidate_confirmed",
                now=now,
            )
            conn.execute(
                """
                UPDATE fb_memory_candidates
                   SET status = 'confirmed', decided_at = ?, memory_id = ?,
                       idempotency_key = ?, proposed_text = ?
                 WHERE id = ? AND owner_id = ?
                """,
                (now, memory["id"], idempotency_key, final_text, candidate_id, owner_id),
            )
            self._audit_locked(conn, owner_id, "candidate.confirmed", candidate_id, f"memory={memory['id']}")
            candidate_after = conn.fetchone(
                "SELECT * FROM fb_memory_candidates WHERE id = ? AND owner_id = ?",
                (candidate_id, owner_id),
            )
        self.cache.invalidate(owner_id)
        return (
            self._serialize_candidate(candidate_after),
            self.get_memory(owner_id, memory["id"]),
        )

    def dismiss_candidate(
        self,
        owner_id: str,
        candidate_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            candidate = conn.fetchone(
                "SELECT * FROM fb_memory_candidates WHERE id = ? AND owner_id = ?" + self._lock_clause(),
                (candidate_id, owner_id),
            )
            candidate = self._candidate_owner(candidate, owner_id)
            duplicate = conn.fetchone(
                "SELECT * FROM fb_memory_candidates WHERE owner_id = ? AND idempotency_key = ?",
                (owner_id, idempotency_key),
            )
            if duplicate:
                if duplicate["id"] == candidate_id and duplicate["status"] == "dismissed":
                    return self._serialize_candidate(duplicate)
                raise ConflictError("该幂等键已用于其他记忆操作")
            if candidate["status"] == "dismissed":
                return self._serialize_candidate(candidate)
            if candidate["status"] != "pending":
                raise ConflictError("候选观点已被确认，不能忽略", current_version=int(candidate["version"]))
            if int(candidate["version"]) != int(expected_version):
                raise ConflictError("候选观点版本已变化，请刷新后重试", current_version=int(candidate["version"]))
            conn.execute(
                """
                UPDATE fb_memory_candidates
                   SET status = 'dismissed', decided_at = ?, idempotency_key = ?
                 WHERE id = ? AND owner_id = ?
                """,
                (now, idempotency_key, candidate_id, owner_id),
            )
            self._audit_locked(conn, owner_id, "candidate.dismissed", candidate_id, None)
            row = conn.fetchone(
                "SELECT * FROM fb_memory_candidates WHERE id = ? AND owner_id = ?",
                (candidate_id, owner_id),
            )
        return self._serialize_candidate(row)

    def _upsert_confirmed_memory_locked(
        self,
        conn,
        *,
        owner_id: str,
        bot_id: str | None = None,
        text: str,
        source_text: str | None,
        source_ref: str | None,
        reason: str,
        now: str,
    ) -> dict[str, Any]:
        digest = _text_hash("confirmed_viewpoint", text)
        existing = conn.fetchone(
            """
            SELECT * FROM fb_memories
             WHERE owner_id = ? AND kind = 'confirmed_viewpoint' AND text_hash = ?
            """,
            (owner_id, digest),
        )
        if existing and existing["deletion_state"] == "active":
            return existing
        if existing:
            # A previously deleted text may be re-added; reset the row.
            conn.execute(
                """
                UPDATE fb_memories
                   SET deletion_state = 'active', status = 'confirmed', text = ?,
                       source_text = ?, source_ref = ?, version = version + 1,
                       updated_at = ?, confirmed_at = ?
                 WHERE id = ? AND owner_id = ?
                """,
                (text, source_text, source_ref, now, now, existing["id"], owner_id),
            )
            memory_id = existing["id"]
            version = int(existing["version"]) + 1
        else:
            memory_id = new_id("mem")
            version = 1
            conn.execute(
                """
                INSERT INTO fb_memories
                    (id, owner_id, bot_id, kind, text, source_text, source_ref, status,
                     version, text_hash, created_at, updated_at, confirmed_at)
                VALUES (?, ?, ?, 'confirmed_viewpoint', ?, ?, ?, 'confirmed', 1, ?, ?, ?, ?)
                """,
                (memory_id, owner_id, bot_id, text, source_text, source_ref, digest, now, now, now),
            )
        conn.execute(
            """
            INSERT INTO fb_memory_versions
                (id, memory_id, owner_id, version, text, source_text, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (new_id("memv"), memory_id, owner_id, version, text, source_text, reason, now),
        )
        conn.execute(
            "DELETE FROM fb_memory_index WHERE owner_id = ? AND memory_id = ?",
            (owner_id, memory_id),
        )
        self._index_memory_locked(conn, owner_id, memory_id, text)
        return {
            "id": memory_id,
            "owner_id": owner_id,
            "bot_id": bot_id,
            "kind": "confirmed_viewpoint",
            "text": text,
            "source_text": source_text,
            "source_ref": source_ref,
            "status": "confirmed",
            "version": version,
            "deletion_state": "active",
        }

    def get_memory(self, owner_id: str, memory_id: str) -> dict[str, Any]:
        with self.db.connect(owner_id=owner_id) as conn:
            row = conn.fetchone(
                "SELECT * FROM fb_memories WHERE id = ? AND owner_id = ?",
                (memory_id, owner_id),
            )
        return self._serialize_memory(self._memory_owner(row, owner_id))

    def list_memories(
        self,
        owner_id: str,
        *,
        kind: str | None = None,
        include_pending: bool = False,
        bot_id: str | None = None,
        include_legacy: bool = False,
    ) -> list[dict[str, Any]]:
        clauses = ["owner_id = ?", "deletion_state = 'active'"]
        params: list[Any] = [owner_id]
        if bot_id:
            clauses.append("(bot_id = ?" + (" OR bot_id IS NULL" if include_legacy else "") + ")")
            params.append(bot_id)
        if kind:
            if kind not in MEMORY_KINDS:
                raise ValueError("invalid memory kind")
            clauses.append("kind = ?")
            params.append(kind)
        if not include_pending:
            clauses.append("status = 'confirmed'")
        with self.db.connect(owner_id=owner_id) as conn:
            rows = conn.fetchall(
                f"SELECT * FROM fb_memories WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC",
                params,
            )
        return [self._serialize_memory(row) for row in rows]

    def patch_memory(
        self,
        owner_id: str,
        memory_id: str,
        *,
        expected_version: int,
        text: str,
        source_text: str | None = None,
    ) -> dict[str, Any]:
        text = _normalize_display(text)
        if not text:
            raise ValueError("memory text is required")
        now = utc_now()
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            memory = conn.fetchone(
                "SELECT * FROM fb_memories WHERE id = ? AND owner_id = ?" + self._lock_clause(),
                (memory_id, owner_id),
            )
            memory = self._memory_owner(memory, owner_id)
            if memory["deletion_state"] != "active":
                raise NotFoundError("memory not found")
            if int(memory["version"]) != int(expected_version):
                raise ConflictError(
                    "记忆版本已变化，请刷新后重试",
                    current_version=int(memory["version"]),
                )
            version = int(memory["version"]) + 1
            conn.execute(
                """
                UPDATE fb_memories
                   SET text = ?, source_text = COALESCE(?, source_text),
                       version = ?, text_hash = ?, updated_at = ?
                 WHERE id = ? AND owner_id = ?
                """,
                (text, source_text, version, _text_hash(memory["kind"], text), now, memory_id, owner_id),
            )
            conn.execute(
                """
                INSERT INTO fb_memory_versions
                    (id, memory_id, owner_id, version, text, source_text, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'user_correction', ?)
                """,
                (new_id("memv"), memory_id, owner_id, version, text, source_text, now),
            )
            conn.execute(
                "DELETE FROM fb_memory_index WHERE owner_id = ? AND memory_id = ?",
                (owner_id, memory_id),
            )
            self._index_memory_locked(conn, owner_id, memory_id, text)
            self._audit_locked(conn, owner_id, "memory.corrected", memory_id, f"version={version}")
        self.cache.invalidate(owner_id)
        return self.get_memory(owner_id, memory_id)

    def memory_versions(self, owner_id: str, memory_id: str) -> list[dict[str, Any]]:
        self.get_memory(owner_id, memory_id)
        with self.db.connect(owner_id=owner_id) as conn:
            rows = conn.fetchall(
                """
                SELECT version, text, source_text, reason, created_at
                  FROM fb_memory_versions
                 WHERE owner_id = ? AND memory_id = ?
                 ORDER BY version ASC
                """,
                (owner_id, memory_id),
            )
        return rows

    def build_memory_context(self, owner_id: str, *, bot_id: str | None = None, include_legacy: bool = False, limit: int = 12) -> list[str]:
        """Confirmed, active memories only; cached per Bot for one short window."""
        cache_key = owner_id if not bot_id else f"{owner_id}:{bot_id}:{1 if include_legacy else 0}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached[:limit]
        clause, params = "", [owner_id, max(1, min(50, int(limit)))]
        if bot_id:
            clause = " AND (bot_id = ?" + (" OR bot_id IS NULL" if include_legacy else "") + ")"
            params = [owner_id, bot_id, max(1, min(50, int(limit)))]
        with self.db.connect(owner_id=owner_id) as conn:
            rows = conn.fetchall(
                f"""
                SELECT kind, text, confirmed_at, updated_at
                  FROM fb_memories
                 WHERE owner_id = ? AND status = 'confirmed'
                   AND deletion_state = 'active'{clause}
                 ORDER BY updated_at DESC
                 LIMIT ?
                """,
                params,
            )
        labels = {
            "watch_object": "关注对象",
            "preference": "明确偏好",
            "research_question": "研究问题",
            "holding": "用户陈述的持仓",
            "confirmed_viewpoint": "用户已确认观点",
        }
        values = [
            f"{labels.get(row['kind'], row['kind'])}（{str(row.get('confirmed_at') or row.get('updated_at'))[:10]}）：{row['text']}"
            for row in rows
        ]
        self.cache.set(cache_key, values)
        return values

    def search_memories(self, owner_id: str, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        terms = _terms(query)
        if not terms:
            return []
        placeholders = ",".join("?" for _ in terms)
        with self.db.connect(owner_id=owner_id) as conn:
            rows = conn.fetchall(
                f"""
                SELECT DISTINCT m.*
                  FROM fb_memories m
                  JOIN fb_memory_index i
                    ON i.owner_id = m.owner_id AND i.memory_id = m.id
                 WHERE m.owner_id = ? AND m.status = 'confirmed'
                   AND m.deletion_state = 'active' AND i.term IN ({placeholders})
                 ORDER BY m.updated_at DESC
                 LIMIT ?
                """,
                (owner_id, *terms, max(1, min(50, limit))),
            )
        return [self._serialize_memory(row) for row in rows]

    @staticmethod
    def _index_memory_locked(conn, owner_id: str, memory_id: str, text: str) -> None:
        for term in _terms(text):
            conn.execute(
                "INSERT INTO fb_memory_index (owner_id, memory_id, term) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                (owner_id, memory_id, term),
            )

    @staticmethod
    def _audit_locked(conn, owner_id: str, action: str, object_id: str, detail: str | None) -> None:
        conn.execute(
            """
            INSERT INTO fb_audit_log (id, owner_id, action, object_id, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (new_id("audit"), owner_id, action, object_id, (detail or "")[:240], utc_now()),
        )

    def audit_entries(self, owner_id: str) -> list[dict[str, Any]]:
        with self.db.connect(owner_id=owner_id) as conn:
            rows = conn.fetchall(
                "SELECT action, object_id, detail, created_at FROM fb_audit_log WHERE owner_id = ? ORDER BY created_at ASC",
                (owner_id,),
            )
        return rows

    def _serialize_memory(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "kind": row["kind"],
            "text": row["text"],
            "source_text": row.get("source_text"),
            "source_ref": row.get("source_ref"),
            "status": row["status"],
            "version": int(row["version"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "confirmed_at": row.get("confirmed_at"),
        }

    def _serialize_candidate(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "task_id": row.get("task_id"),
            "proposed_text": row["proposed_text"],
            "rationale": row.get("rationale"),
            "source_text": row.get("source_text"),
            "source_ref": row.get("source_message_ref"),
            "status": row["status"],
            "version": int(row["version"]),
            "created_at": row["created_at"],
            "decided_at": row.get("decided_at"),
            "memory_id": row.get("memory_id"),
        }

    # -------------------------------------------------------------- deletion
    def request_deletion(
        self,
        owner_id: str,
        *,
        scope: str,
        target_ids: Optional[list[str]] = None,
        request_id: str,
        zh: bool = True,
    ) -> dict[str, Any]:
        if scope not in {"single", "all"}:
            raise ValueError("invalid deletion scope")
        if scope == "single" and not target_ids:
            raise ValueError("target_ids is required for single deletion")
        request_id = (request_id or "").strip() or new_id("delreq")
        existing = self.get_deletion_job_by_request(owner_id, request_id)
        if existing:
            return existing

        job_id = new_id("del")
        now = utc_now()
        layers = {name: {"status": "pending"} for name in DELETION_LAYERS}
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            conn.execute(
                """
                INSERT INTO fb_deletion_jobs
                    (id, owner_id, scope, target_ids_json, status, layers_json,
                     request_id, requested_at, updated_at)
                VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?)
                """,
                (
                    job_id,
                    owner_id,
                    scope,
                    json.dumps(target_ids or [], ensure_ascii=False),
                    json.dumps(layers, ensure_ascii=False),
                    request_id,
                    now,
                    now,
                ),
            )
            # Phase one: stop retrieval immediately by moving selected rows out
            # of the active state, before any destructive statement runs.
            if scope == "all":
                conn.execute(
                    "UPDATE fb_memories SET deletion_state = 'deleting' WHERE owner_id = ?",
                    (owner_id,),
                )
            else:
                placeholders = ",".join("?" for _ in target_ids)
                conn.execute(
                    f"UPDATE fb_memories SET deletion_state = 'deleting' WHERE owner_id = ? AND id IN ({placeholders})",
                    (owner_id, *target_ids),
                )
        self.cache.invalidate(owner_id)

        counts, targets, candidate_ids = self._purge_memories(owner_id, scope=scope, target_ids=target_ids)
        self.db.vacuum()
        self.cache.invalidate(owner_id)

        layers["memory_rows"] = {"status": "cleared", "deleted": counts["memories"]}
        layers["memory_versions"] = {"status": "cleared", "deleted": counts["versions"]}
        layers["memory_candidates"] = {"status": "cleared", "deleted": counts["candidates"]}
        layers["quote_copies"] = {"status": "cleared", "deleted": counts["quote_copies"]}
        layers["search_index"] = {"status": "cleared", "deleted": counts["index"]}
        layers["context_cache"] = {"status": "cleared", "detail": "per-owner cache invalidated"}
        if self.db.dialect == "sqlite":
            layers["physical_storage"] = {
                "status": "cleared",
                "detail": "SQLite secure_delete + VACUUM executed after commit",
            }
        else:
            layers["physical_storage"] = {
                "status": "unverified",
                "detail": "PostgreSQL storage compaction is provider controlled",
            }
        backup_layer = self._backup_layer()
        layers["backup"] = backup_layer
        if self._third_party_retention_verified():
            layers["third_party_model"] = {"status": "verified", "detail": "operator marked retention verified"}
        else:
            layers["third_party_model"] = {
                "status": "unverified",
                "detail": "模型供应商对输入/输出的保留范围尚未核实；不能宣称完全删除",
            }

        blocking = [
            name
            for name, value in layers.items()
            if value["status"] in {"unverified", "blocked", "failed"}
        ]
        labels = {
            "memory_rows": "记忆本体",
            "memory_versions": "历史版本",
            "memory_candidates": "候选观点",
            "quote_copies": "引用副本",
            "search_index": "检索索引",
            "context_cache": "上下文缓存",
            "physical_storage": "物理存储",
            "backup": "备份",
            "third_party_model": "模型供应商保留",
        }
        status = "blocked" if blocking else "completed"
        detail = (
            "本机受控数据已按范围清除；以下层未核实或阻塞：" + "、".join(labels.get(name, name) for name in blocking)
            if zh
            else "Controlled local layers were cleared; unverified or blocked layers: " + ", ".join(blocking)
        )
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            conn.execute(
                """
                UPDATE fb_deletion_jobs
                   SET status = ?, layers_json = ?, detail = ?, updated_at = ?,
                       completed_at = ?
                 WHERE id = ? AND owner_id = ?
                """,
                (
                    status,
                    json.dumps(layers, ensure_ascii=False),
                    detail,
                    utc_now(),
                    utc_now(),
                    job_id,
                    owner_id,
                ),
            )
            self._audit_locked(
                conn,
                owner_id,
                "memory.deleted",
                job_id,
                f"scope={scope};targets={len(targets)};candidates={len(candidate_ids)};status={status}",
            )
        return self.get_deletion_job(owner_id, job_id)

    def complete_running_steps(self, owner_id: str, task_id: str) -> None:
        self.close_running_steps(owner_id, task_id, status="completed")

    def _purge_memories(
        self, owner_id: str, *, scope: str, target_ids: Optional[list[str]]
    ) -> tuple[dict[str, int], list[str], list[str]]:
        now = utc_now()
        with self.db.connect(owner_id=owner_id, write=True) as conn:
            if scope == "all":
                targets = [
                    row["id"]
                    for row in conn.fetchall(
                        "SELECT id FROM fb_memories WHERE owner_id = ?", (owner_id,)
                    )
                ]
            else:
                requested = list(dict.fromkeys(target_ids or []))
                targets = []
                if requested:
                    placeholders = ",".join("?" for _ in requested)
                    targets = [
                        row["id"]
                        for row in conn.fetchall(
                            f"SELECT id FROM fb_memories WHERE owner_id = ? AND id IN ({placeholders})",
                            (owner_id, *requested),
                        )
                    ]

            counts = {"memories": 0, "versions": 0, "candidates": 0, "index": 0, "quote_copies": 0}
            candidate_ids: list[str] = []

            if scope == "all":
                candidate_ids = [
                    row["id"]
                    for row in conn.fetchall(
                        "SELECT id FROM fb_memory_candidates WHERE owner_id = ?", (owner_id,)
                    )
                ]
                counts["index"] = max(
                    0, conn.execute("DELETE FROM fb_memory_index WHERE owner_id = ?", (owner_id,)).rowcount
                )
                counts["versions"] = max(
                    0,
                    conn.execute(
                        "DELETE FROM fb_memory_versions WHERE owner_id = ?", (owner_id,)
                    ).rowcount,
                )
                counts["candidates"] = max(
                    0,
                    conn.execute(
                        "DELETE FROM fb_memory_candidates WHERE owner_id = ?", (owner_id,)
                    ).rowcount,
                )
                counts["memories"] = max(
                    0, conn.execute("DELETE FROM fb_memories WHERE owner_id = ?", (owner_id,)).rowcount
                )
            elif targets:
                placeholders = ",".join("?" for _ in targets)
                # Only candidates linked to the selected memories are derived
                # copies. A pending candidate with no confirmed memory is an
                # independent record and stays unless the user clears all.
                candidate_ids = [
                    row["id"]
                    for row in conn.fetchall(
                        f"SELECT id FROM fb_memory_candidates WHERE owner_id = ? AND memory_id IN ({placeholders})",
                        (owner_id, *targets),
                    )
                ]
                counts["index"] = max(
                    0,
                    conn.execute(
                        f"DELETE FROM fb_memory_index WHERE owner_id = ? AND memory_id IN ({placeholders})",
                        (owner_id, *targets),
                    ).rowcount,
                )
                counts["versions"] = max(
                    0,
                    conn.execute(
                        f"DELETE FROM fb_memory_versions WHERE owner_id = ? AND memory_id IN ({placeholders})",
                        (owner_id, *targets),
                    ).rowcount,
                )
                if candidate_ids:
                    cand_placeholders = ",".join("?" for _ in candidate_ids)
                    counts["candidates"] = max(
                        0,
                        conn.execute(
                            f"DELETE FROM fb_memory_candidates WHERE owner_id = ? AND id IN ({cand_placeholders})",
                            (owner_id, *candidate_ids),
                        ).rowcount,
                    )
                counts["memories"] = max(
                    0,
                    conn.execute(
                        f"DELETE FROM fb_memories WHERE owner_id = ? AND id IN ({placeholders})",
                        (owner_id, *targets),
                    ).rowcount,
                )
            counts["quote_copies"] = counts["memories"] + counts["versions"] + counts["candidates"]
            # Audit rows deliberately contain no deleted text.
            conn.execute(
                "INSERT INTO fb_audit_log (id, owner_id, action, object_id, detail, created_at) VALUES (?, ?, 'memory.purged', ?, ?, ?)",
                (new_id("audit"), owner_id, scope, f"memories={counts['memories']}", now),
            )
        return counts, targets, candidate_ids

    def get_deletion_job(self, owner_id: str, job_id: str) -> dict[str, Any]:
        with self.db.connect(owner_id=owner_id) as conn:
            row = conn.fetchone(
                "SELECT * FROM fb_deletion_jobs WHERE id = ? AND owner_id = ?",
                (job_id, owner_id),
            )
        if not row or str(row.get("owner_id")) != str(owner_id):
            raise NotFoundError("deletion job not found")
        return self._serialize_deletion_job(row)

    def get_deletion_job_by_request(self, owner_id: str, request_id: str) -> dict[str, Any] | None:
        with self.db.connect(owner_id=owner_id) as conn:
            row = conn.fetchone(
                "SELECT * FROM fb_deletion_jobs WHERE owner_id = ? AND request_id = ?",
                (owner_id, request_id),
            )
        return self._serialize_deletion_job(row) if row else None

    def latest_deletion_job(self, owner_id: str) -> dict[str, Any] | None:
        with self.db.connect(owner_id=owner_id) as conn:
            row = conn.fetchone(
                "SELECT * FROM fb_deletion_jobs WHERE owner_id = ? ORDER BY requested_at DESC LIMIT 1",
                (owner_id,),
            )
        return self._serialize_deletion_job(row) if row else None

    def _serialize_deletion_job(self, row: dict[str, Any]) -> dict[str, Any]:
        job = dict(row)
        job["target_ids"] = json.loads(job.pop("target_ids_json") or "[]")
        layers = json.loads(job.pop("layers_json") or "{}")
        job["layers"] = layers
        job.pop("owner_id", None)
        return job

    def _backup_layer(self) -> dict[str, Any]:
        backup_dir = getattr(self, "backup_dir", None)
        if not backup_dir:
            return {
                "status": "not_configured",
                "detail": "未配置独立备份目录；数据库快照/副本仍需在部署层核实",
            }
        try:
            files = [path for path in backup_dir.glob("*") if path.is_file()]
        except OSError:
            return {"status": "unverified", "detail": "备份目录不可读"}
        if files:
            return {
                "status": "unverified",
                "detail": f"备份目录存在 {len(files)} 个文件，未配置可验证的备份清除或密钥销毁",
            }
        return {"status": "verified", "detail": "备份目录为空，未发现适用备份"}

    def _third_party_retention_verified(self) -> bool:
        return bool(getattr(self, "third_party_retention_verified", False))


def _normalize_display(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())[:2000]


def _terms(text: str) -> list[str]:
    """Small deterministic retrieval index: ASCII words plus CJK bigrams."""
    normalized = (text or "").lower()
    terms = set(re.findall(r"[a-z0-9][a-z0-9._-]{1,30}", normalized))
    for run in re.findall(r"[\u3400-\u9fff]+", normalized):
        if len(run) <= 2:
            terms.add(run)
        for index in range(len(run) - 1):
            terms.add(run[index : index + 2])
    return sorted(terms)[:200]
