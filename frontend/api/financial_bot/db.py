"""Database boundary for Financial Bot.

Two dialects are supported:

* SQLite for the explicitly labelled local single-user development mode.
* PostgreSQL for a hosted deployment. Each transaction switches to the
  ``authenticated`` role and installs the owner's JWT subject, so RLS is an
  independent second boundary in addition to the owner filters in every query.

No driver is imported at module import time, keeping the SQLite path usable even
when psycopg is not installed.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence


class DatabaseError(RuntimeError):
    pass


class Connection:
    """Thin wrapper that returns rows as dictionaries for both dialects."""

    def __init__(self, raw, dialect: str):
        self.raw = raw
        self.dialect = dialect

    def _sql(self, sql: str) -> str:
        if self.dialect == "postgres":
            # The shared SQL uses qmark placeholders. No SQL string in this
            # package contains a literal question mark.
            return sql.replace("?", "%s")
        return sql

    def execute(self, sql: str, params: Sequence[Any] = ()):
        cursor = self.raw.execute(self._sql(sql), tuple(params))
        return cursor

    def fetchone(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        cursor = self.execute(sql, params)
        row = cursor.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        cursor = self.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]


class Database:
    def __init__(self, *, sqlite_path: Path | None = None, database_url: str | None = None):
        if not sqlite_path and not database_url:
            raise DatabaseError("Either sqlite_path or database_url is required")
        self.sqlite_path = Path(sqlite_path) if sqlite_path else None
        self.database_url = database_url
        self.dialect = "postgres" if database_url else "sqlite"
        self._init_lock = threading.Lock()
        if self.sqlite_path:
            self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ setup
    def initialize(self) -> None:
        with self._init_lock:
            if self.dialect == "sqlite":
                from api.financial_bot.schema import SCHEMA_STATEMENTS

                conn = sqlite3.connect(self.sqlite_path, timeout=30, isolation_level=None)
                try:
                    # journal_mode cannot be changed inside a transaction.
                    conn.execute("PRAGMA journal_mode = WAL")
                    conn.execute("PRAGMA secure_delete = ON")
                    conn.execute("BEGIN IMMEDIATE")
                    for statement in SCHEMA_STATEMENTS:
                        conn.execute(statement)
                    # Additive migration for existing local databases. Keep this
                    # idempotent because SQLite has no ADD COLUMN IF NOT EXISTS.
                    for table in ("fb_memories", "fb_memory_candidates"):
                        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                        if "bot_id" not in columns:
                            conn.execute(f"ALTER TABLE {table} ADD COLUMN bot_id TEXT")
                    event_columns = {row[1] for row in conn.execute("PRAGMA table_info(fb_task_events)").fetchall()}
                    if event_columns and "openmuse_revision" not in event_columns:
                        conn.execute("ALTER TABLE fb_task_events ADD COLUMN openmuse_revision INTEGER")
                    conn.execute(
                        "CREATE UNIQUE INDEX IF NOT EXISTS fb_task_events_openmuse_revision "
                        "ON fb_task_events(task_id, openmuse_revision)"
                    )
                    openmuse_mapping_columns = {
                        row[1] for row in conn.execute("PRAGMA table_info(fb_openmuse_task_mappings)").fetchall()
                    }
                    if openmuse_mapping_columns and "deletion_error" not in openmuse_mapping_columns:
                        conn.execute(
                            "ALTER TABLE fb_openmuse_task_mappings ADD COLUMN deletion_error TEXT NOT NULL DEFAULT ''"
                        )
                        openmuse_mapping_columns.add("deletion_error")
                    if openmuse_mapping_columns and "reconcile_lease_id" not in openmuse_mapping_columns:
                        conn.execute("ALTER TABLE fb_openmuse_task_mappings ADD COLUMN reconcile_lease_id TEXT")
                    if openmuse_mapping_columns and "reconcile_lease_expires_at" not in openmuse_mapping_columns:
                        conn.execute("ALTER TABLE fb_openmuse_task_mappings ADD COLUMN reconcile_lease_expires_at TEXT")
                        openmuse_mapping_columns.add("reconcile_lease_expires_at")
                    if openmuse_mapping_columns and "integration_phase" not in openmuse_mapping_columns:
                        conn.execute("ALTER TABLE fb_openmuse_task_mappings ADD COLUMN integration_phase TEXT NOT NULL DEFAULT 'prepared'")
                        openmuse_mapping_columns.add("integration_phase")
                    if openmuse_mapping_columns and "upstream_task_revision" not in openmuse_mapping_columns:
                        conn.execute("ALTER TABLE fb_openmuse_task_mappings ADD COLUMN upstream_task_revision INTEGER")
                    if openmuse_mapping_columns and "upstream_projection_sha256" not in openmuse_mapping_columns:
                        conn.execute("ALTER TABLE fb_openmuse_task_mappings ADD COLUMN upstream_projection_sha256 TEXT")
                    monitor_columns = {row[1] for row in conn.execute("PRAGMA table_info(fb_monitors)").fetchall()}
                    if monitor_columns and "last_event_ref" not in monitor_columns:
                        conn.execute("ALTER TABLE fb_monitors ADD COLUMN last_event_ref TEXT")
                    if monitor_columns and "muted_until" not in monitor_columns:
                        conn.execute("ALTER TABLE fb_monitors ADD COLUMN muted_until TEXT")
                    if monitor_columns and "session_id" not in monitor_columns:
                        conn.execute("ALTER TABLE fb_monitors ADD COLUMN session_id TEXT")
                    if monitor_columns and "failure_count" not in monitor_columns:
                        conn.execute("ALTER TABLE fb_monitors ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0")
                    if monitor_columns and "retry_after" not in monitor_columns:
                        conn.execute("ALTER TABLE fb_monitors ADD COLUMN retry_after TEXT")
                    thread_columns = {row[1] for row in conn.execute("PRAGMA table_info(fb_threads)").fetchall()}
                    thread_additions = {
                        "folder": "TEXT",
                        "approval_mode": "TEXT NOT NULL DEFAULT 'ask'",
                        "research_mode": "TEXT NOT NULL DEFAULT 'quick'",
                        "pinned": "INTEGER NOT NULL DEFAULT 0",
                        "archived": "INTEGER NOT NULL DEFAULT 0",
                        "unread": "INTEGER NOT NULL DEFAULT 0",
                        "last_read_at": "TEXT",
                        "workflow_json": "TEXT",
                        "current_task_id": "TEXT",
                    }
                    for column, definition in thread_additions.items():
                        if thread_columns and column not in thread_columns:
                            conn.execute(f"ALTER TABLE fb_threads ADD COLUMN {column} {definition}")
                    task_columns = {row[1] for row in conn.execute("PRAGMA table_info(fb_tasks)").fetchall()}
                    if task_columns and "ui_context_json" not in task_columns:
                        conn.execute("ALTER TABLE fb_tasks ADD COLUMN ui_context_json TEXT")
                    task_additions = {
                        "waiting_reason": "TEXT",
                        "attention_json": "TEXT",
                        "authorization_json": "TEXT",
                        "continued_from_task_id": "TEXT",
                    }
                    for column, definition in task_additions.items():
                        if task_columns and column not in task_columns:
                            conn.execute(f"ALTER TABLE fb_tasks ADD COLUMN {column} {definition}")
                    bot_columns = {row[1] for row in conn.execute("PRAGMA table_info(fb_bots)").fetchall()}
                    bot_additions = {
                        "market_hours_only": "INTEGER NOT NULL DEFAULT 1",
                        "max_alerts_per_day": "INTEGER NOT NULL DEFAULT 20",
                        "quiet_hours_start": "TEXT",
                        "quiet_hours_end": "TEXT",
                        "webhook_url": "TEXT",
                        "notification_email": "TEXT",
                        "report_style": "TEXT NOT NULL DEFAULT 'brief'",
                        "digest_mode": "TEXT NOT NULL DEFAULT 'immediate'",
                        "digest_hour": "TEXT NOT NULL DEFAULT '18:00'",
                        "last_digest_date": "TEXT",
                        "prompt_template": "TEXT",
                        "prompt_config": "TEXT",
                        "prompt_version": "INTEGER NOT NULL DEFAULT 1",
                    }
                    for column, definition in bot_additions.items():
                        if bot_columns and column not in bot_columns:
                            conn.execute(f"ALTER TABLE fb_bots ADD COLUMN {column} {definition}")
                    delivery_columns = {row[1] for row in conn.execute("PRAGMA table_info(fb_notification_deliveries)").fetchall()}
                    if delivery_columns and "payload_json" not in delivery_columns:
                        conn.execute("ALTER TABLE fb_notification_deliveries ADD COLUMN payload_json TEXT")
                    if delivery_columns and "next_retry_at" not in delivery_columns:
                        conn.execute("ALTER TABLE fb_notification_deliveries ADD COLUMN next_retry_at TEXT")
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
                finally:
                    conn.close()
            else:
                # PostgreSQL schema and RLS are owned by
                # supabase/migrations/0006_financial_bot.sql.
                self._verify_postgres_schema()

    def _verify_postgres_schema(self) -> None:
        try:
            with self.connect(owner_id=None, bypass_rls=True) as conn:
                row = conn.fetchone(
                    "SELECT to_regclass('public.fb_tasks') AS table_name"
                )
                if not row or not row.get("table_name"):
                    raise DatabaseError(
                        "PostgreSQL schema is missing; apply supabase/migrations/0006_financial_bot.sql"
                    )
        except DatabaseError:
            raise
        except Exception as exc:  # pragma: no cover - connection error path
            raise DatabaseError(f"PostgreSQL schema check failed: {type(exc).__name__}") from exc

    # -------------------------------------------------------------- lifecycle
    @contextmanager
    def connect(
        self,
        *,
        owner_id: str | None,
        write: bool = False,
        bypass_rls: bool = False,
    ) -> Iterator[Connection]:
        if self.dialect == "postgres":
            yield from self._connect_postgres(owner_id=owner_id, bypass_rls=bypass_rls)
            return
        conn = sqlite3.connect(self.sqlite_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA secure_delete = ON")
            conn.execute("PRAGMA busy_timeout = 30000")
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            wrapper = Connection(conn, "sqlite")
            yield wrapper
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    def _connect_postgres(self, *, owner_id: str | None, bypass_rls: bool) -> Iterator[Connection]:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - configuration error path
            raise DatabaseError("DATABASE_URL is set but psycopg is not installed") from exc
        conn = psycopg.connect(self.database_url, row_factory=dict_row)
        try:
            if bypass_rls:
                # DDL/migration path only. Normal requests always take the
                # authenticated role below.
                pass
            else:
                conn.execute("SET LOCAL ROLE authenticated")
                claims = json.dumps({"sub": owner_id or "", "role": "authenticated"})
                conn.execute("SELECT set_config('request.jwt.claims', %s, true)", (claims,))
            wrapper = Connection(conn, "postgres")
            yield wrapper
            conn.commit()
        except BaseException:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------ misc
    def vacuum(self) -> None:
        """Compact SQLite so deleted page content is overwritten."""
        if self.dialect != "sqlite":
            return
        conn = sqlite3.connect(self.sqlite_path, timeout=30, isolation_level=None)
        try:
            conn.execute("PRAGMA secure_delete = ON")
            conn.execute("VACUUM")
            # Remove deleted pages that may still live in the WAL after VACUUM.
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()

    def path_size(self) -> int:
        if self.dialect != "sqlite" or not self.sqlite_path:
            return 0
        try:
            return self.sqlite_path.stat().st_size
        except OSError:
            return 0
