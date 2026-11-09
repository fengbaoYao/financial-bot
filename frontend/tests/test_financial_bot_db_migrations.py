import sqlite3

from api.financial_bot.db import Database


def test_initialize_adds_openmuse_revision_to_legacy_events_without_losing_rows(tmp_path):
    db_path = tmp_path / "legacy-financial-bot.sqlite3"
    raw = sqlite3.connect(db_path)
    raw.execute("""
        CREATE TABLE fb_task_events (
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
            UNIQUE (task_id, sequence)
        )
    """)
    raw.execute(
        "INSERT INTO fb_task_events(event_id, task_id, owner_id, sequence, occurred_at, kind, status, display_summary) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("event-old", "task-old", "owner-old", 1, "2026-11-04T00:00:00Z", "status", "running", "Existing event"),
    )
    raw.commit()
    raw.close()

    Database(sqlite_path=db_path).initialize()

    verify = sqlite3.connect(db_path)
    columns = {row[1] for row in verify.execute("PRAGMA table_info(fb_task_events)")}
    row = verify.execute(
        "SELECT event_id, task_id, owner_id, sequence, display_summary, openmuse_revision "
        "FROM fb_task_events WHERE event_id = 'event-old'"
    ).fetchone()
    index = verify.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'fb_task_events_openmuse_revision'"
    ).fetchone()
    verify.close()

    assert "openmuse_revision" in columns
    assert row == ("event-old", "task-old", "owner-old", 1, "Existing event", None)
    assert index == ("fb_task_events_openmuse_revision",)
