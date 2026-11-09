"""Canonical Financial Bot tables.

The SQL is deliberately expressed in the common subset understood by SQLite and
PostgreSQL so the local single-user store and the hosted store cannot drift.
PostgreSQL additionally receives row-level security in
``supabase/migrations/0006_financial_bot.sql``; this module only creates tables.
"""

from __future__ import annotations

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS fb_tasks (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        session_id TEXT,
        idempotency_key TEXT NOT NULL,
        query TEXT NOT NULL,
        language TEXT NOT NULL DEFAULT 'auto',
        research_mode TEXT NOT NULL DEFAULT 'quick',
        status TEXT NOT NULL,
        status_reason TEXT,
        result_json TEXT,
        error_code TEXT,
        error_message TEXT,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        sequence INTEGER NOT NULL DEFAULT 0,
        ui_context_json TEXT,
        waiting_reason TEXT,
        attention_json TEXT,
        authorization_json TEXT,
        continued_from_task_id TEXT,
        UNIQUE (owner_id, idempotency_key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS fb_tasks_owner_session ON fb_tasks(owner_id, session_id, created_at)",
    "CREATE INDEX IF NOT EXISTS fb_tasks_owner_status ON fb_tasks(owner_id, status)",
    """
    CREATE TABLE IF NOT EXISTS fb_openmuse_task_mappings (
        owner_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        request_id TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL,
        upstream_task_id TEXT,
        status TEXT NOT NULL DEFAULT 'prepared',
        upstream_task_revision INTEGER,
        upstream_projection_sha256 TEXT,
        integration_phase TEXT NOT NULL DEFAULT 'prepared' CHECK (integration_phase IN (
            'prepared', 'creating', 'create_unknown', 'held', 'provisioning',
            'provision_unknown', 'provisioned', 'resuming', 'resume_unknown',
            'active', 'rejected'
        )),
        deletion_status TEXT NOT NULL DEFAULT 'not_requested',
        deletion_error TEXT NOT NULL DEFAULT '',
        reconcile_lease_id TEXT,
        reconcile_lease_expires_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (owner_id, task_id),
        UNIQUE (owner_id, request_id),
        UNIQUE (owner_id, upstream_task_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS fb_openmuse_task_mappings_deletion ON fb_openmuse_task_mappings(owner_id, deletion_status, updated_at)",
    """
    CREATE TABLE IF NOT EXISTS fb_task_plans (
        task_id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        bot_id TEXT,
        session_id TEXT,
        original_query TEXT NOT NULL,
        status TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'template',
        steps_json TEXT NOT NULL,
        termination_condition TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS fb_task_plans_owner ON fb_task_plans(owner_id, updated_at)",
    """
    CREATE TABLE IF NOT EXISTS fb_bots (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        name TEXT NOT NULL,
        description TEXT,
        instructions TEXT,
        prompt_template TEXT,
        prompt_config TEXT,
        prompt_version INTEGER NOT NULL DEFAULT 1,
        is_default INTEGER NOT NULL DEFAULT 0,
        approval_threshold INTEGER NOT NULL DEFAULT 4,
        market_hours_only INTEGER NOT NULL DEFAULT 1,
        max_alerts_per_day INTEGER NOT NULL DEFAULT 20,
        quiet_hours_start TEXT,
        quiet_hours_end TEXT,
        webhook_url TEXT,
        notification_email TEXT,
        report_style TEXT NOT NULL DEFAULT 'brief',
        digest_mode TEXT NOT NULL DEFAULT 'immediate',
        digest_hour TEXT NOT NULL DEFAULT '18:00',
        last_digest_date TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS fb_bots_owner ON fb_bots(owner_id, is_default, updated_at)",
    """
    CREATE TABLE IF NOT EXISTS fb_bot_sessions (
        bot_id TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (bot_id, session_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS fb_bot_sessions_owner ON fb_bot_sessions(owner_id, bot_id, updated_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS fb_bot_sessions_one_bot ON fb_bot_sessions(owner_id, session_id)",
    """
    CREATE TABLE IF NOT EXISTS fb_session_lifecycle (
        owner_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        updated_at TEXT NOT NULL,
        PRIMARY KEY (owner_id, session_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fb_threads (
        session_id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        bot_id TEXT NOT NULL,
        parent_session_id TEXT,
        title TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'side',
        folder TEXT,
        approval_mode TEXT NOT NULL DEFAULT 'ask',
        research_mode TEXT NOT NULL DEFAULT 'quick',
        pinned INTEGER NOT NULL DEFAULT 0,
        archived INTEGER NOT NULL DEFAULT 0,
        unread INTEGER NOT NULL DEFAULT 0,
        last_read_at TEXT,
        workflow_json TEXT,
        current_task_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS fb_threads_owner_bot ON fb_threads(owner_id, bot_id, updated_at)",
    """
    CREATE TABLE IF NOT EXISTS fb_notification_deliveries (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        bot_id TEXT NOT NULL,
        notification_id TEXT,
        channel TEXT NOT NULL,
        status TEXT NOT NULL,
        error TEXT,
        payload_json TEXT,
        attempts INTEGER NOT NULL DEFAULT 1,
        next_retry_at TEXT,
        created_at TEXT NOT NULL,
        sent_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS fb_notification_deliveries_owner ON fb_notification_deliveries(owner_id, bot_id, created_at)",
    """
    CREATE TABLE IF NOT EXISTS fb_monitors (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        bot_id TEXT NOT NULL,
        session_id TEXT,
        name TEXT NOT NULL,
        symbol TEXT NOT NULL,
        condition TEXT NOT NULL DEFAULT 'change_pct',
        threshold REAL NOT NULL,
        direction TEXT NOT NULL DEFAULT 'both',
        interval_minutes INTEGER NOT NULL DEFAULT 15,
        cooldown_minutes INTEGER NOT NULL DEFAULT 120,
        status TEXT NOT NULL DEFAULT 'active',
        last_checked_at TEXT,
        last_triggered_at TEXT,
        last_event_ref TEXT,
        muted_until TEXT,
        last_error TEXT,
        failure_count INTEGER NOT NULL DEFAULT 0,
        retry_after TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fb_market_closures (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        market TEXT NOT NULL,
        closure_date TEXT NOT NULL,
        reason TEXT,
        created_at TEXT NOT NULL,
        UNIQUE (owner_id, market, closure_date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS fb_market_closures_owner ON fb_market_closures(owner_id, closure_date)",
    "CREATE INDEX IF NOT EXISTS fb_monitors_due ON fb_monitors(status, last_checked_at)",
    "CREATE INDEX IF NOT EXISTS fb_monitors_owner_bot ON fb_monitors(owner_id, bot_id, status)",
    """
    CREATE TABLE IF NOT EXISTS fb_notifications (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        bot_id TEXT NOT NULL,
        monitor_id TEXT,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        severity TEXT NOT NULL DEFAULT 'info',
        status TEXT NOT NULL DEFAULT 'unread',
        task_id TEXT,
        created_at TEXT NOT NULL,
        read_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS fb_notifications_owner_bot ON fb_notifications(owner_id, bot_id, status, created_at)",
    """
    CREATE TABLE IF NOT EXISTS fb_task_events (
        event_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        occurred_at TEXT NOT NULL,
        kind TEXT NOT NULL,
        step_id TEXT,
        status TEXT NOT NULL,
        display_summary TEXT NOT NULL,
        source_refs_json TEXT NOT NULL DEFAULT '[]',
        openmuse_revision INTEGER,
        UNIQUE (task_id, sequence)
    )
    """,
    "CREATE INDEX IF NOT EXISTS fb_task_events_owner ON fb_task_events(owner_id, task_id, sequence)",
    """
    CREATE TABLE IF NOT EXISTS fb_messages (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        bot_id TEXT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'message',
        content TEXT NOT NULL,
        task_id TEXT,
        metadata_json TEXT,
        sequence INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (owner_id, session_id, sequence)
    )
    """,
    "CREATE INDEX IF NOT EXISTS fb_messages_owner_session ON fb_messages(owner_id, session_id, sequence)",
    """
    CREATE TABLE IF NOT EXISTS fb_memories (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        bot_id TEXT,
        kind TEXT NOT NULL,
        text TEXT NOT NULL,
        source_text TEXT,
        source_ref TEXT,
        status TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        text_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        confirmed_at TEXT,
        deletion_state TEXT NOT NULL DEFAULT 'active',
        UNIQUE (owner_id, kind, text_hash)
    )
    """,
    "CREATE INDEX IF NOT EXISTS fb_memories_owner_kind ON fb_memories(owner_id, kind, status)",
    """
    CREATE TABLE IF NOT EXISTS fb_memory_versions (
        id TEXT PRIMARY KEY,
        memory_id TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        text TEXT NOT NULL,
        source_text TEXT,
        reason TEXT,
        created_at TEXT NOT NULL,
        UNIQUE (memory_id, version)
    )
    """,
    "CREATE INDEX IF NOT EXISTS fb_memory_versions_owner ON fb_memory_versions(owner_id, memory_id, version)",
    """
    CREATE TABLE IF NOT EXISTS fb_memory_candidates (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        bot_id TEXT,
        task_id TEXT,
        source_message_ref TEXT,
        proposed_text TEXT NOT NULL,
        rationale TEXT,
        source_text TEXT,
        status TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        decided_at TEXT,
        memory_id TEXT,
        idempotency_key TEXT,
        UNIQUE (owner_id, idempotency_key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS fb_memory_candidates_owner ON fb_memory_candidates(owner_id, status, created_at)",
    """
    CREATE TABLE IF NOT EXISTS fb_memory_index (
        owner_id TEXT NOT NULL,
        memory_id TEXT NOT NULL,
        term TEXT NOT NULL,
        PRIMARY KEY (owner_id, memory_id, term)
    )
    """,
    "CREATE INDEX IF NOT EXISTS fb_memory_index_term ON fb_memory_index(owner_id, term)",
    """
    CREATE TABLE IF NOT EXISTS fb_deletion_jobs (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        scope TEXT NOT NULL,
        target_ids_json TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL,
        layers_json TEXT NOT NULL DEFAULT '{}',
        detail TEXT,
        request_id TEXT NOT NULL,
        requested_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT,
        UNIQUE (owner_id, request_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS fb_deletion_jobs_owner ON fb_deletion_jobs(owner_id, requested_at)",
    """
    CREATE TABLE IF NOT EXISTS fb_audit_log (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        action TEXT NOT NULL,
        object_id TEXT NOT NULL,
        detail TEXT,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS fb_audit_log_owner ON fb_audit_log(owner_id, created_at)",
    """
    CREATE TABLE IF NOT EXISTS fb_dispositions (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        task_id TEXT,
        session_id TEXT,
        input_ref TEXT NOT NULL,
        disposition TEXT NOT NULL,
        reason_code TEXT,
        reason_display TEXT,
        created_at TEXT NOT NULL,
        UNIQUE (owner_id, input_ref)
    )
    """,
    "CREATE INDEX IF NOT EXISTS fb_dispositions_owner_session ON fb_dispositions(owner_id, session_id, created_at)",
    """
    CREATE TABLE IF NOT EXISTS fb_platform_state (
        id TEXT PRIMARY KEY,
        paused INTEGER NOT NULL DEFAULT 0,
        paused_by TEXT,
        pause_reason TEXT,
        paused_at TEXT,
        updated_at TEXT NOT NULL
    )
    """,
)
