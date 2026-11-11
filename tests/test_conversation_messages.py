from api.financial_bot.db import Database
from api.financial_bot.store import FinancialBotStore

OWNER = "11111111-1111-4111-8111-111111111111"


def test_messages_are_ordered_and_owner_scoped(tmp_path):
    db = Database(sqlite_path=tmp_path / "messages.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    first = store.append_message(
        OWNER, session_id="s1", role="user", kind="message", content="茅台怎么样",
    )
    second = store.append_message(
        OWNER, session_id="s1", role="assistant", kind="answer", content="先看营收和风险。",
    )
    other = store.append_message(
        "22222222-2222-4222-8222-222222222222", session_id="s1", role="user", content="其他用户",
    )
    rows = store.list_messages(OWNER, "s1")
    assert [row["id"] for row in rows] == [first["id"], second["id"]]
    assert [row["sequence"] for row in rows] == [1, 2]
    assert other["id"] not in {row["id"] for row in rows}


def test_backfill_projects_legacy_tasks_once(tmp_path):
    import json

    db = Database(sqlite_path=tmp_path / "backfill.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    task, _ = store.create_task(
        owner_id=OWNER,
        query="旧问题",
        session_id="legacy-session",
        language="zh-CN",
        research_mode="quick",
        idempotency_key="legacy-1",
    )
    with db.connect(owner_id=OWNER, write=True) as conn:
        conn.execute(
            "UPDATE fb_tasks SET result_json = ? WHERE id = ? AND owner_id = ?",
            (json.dumps({"summary": "旧回答", "research_steps": [{"tool": "get_stock_analysis", "status": "completed"}]}, ensure_ascii=False), task["id"], OWNER),
        )
    assert store.backfill_session_messages(OWNER, "legacy-session") == 3
    assert store.backfill_session_messages(OWNER, "legacy-session") == 0
    rows = store.list_messages(OWNER, "legacy-session")
    assert [row["kind"] for row in rows] == ["message", "activity", "answer"]
    assert rows[0]["content"] == "旧问题"
    assert rows[2]["content"] == "旧回答"


def test_proactive_message_targets_monitor_session(tmp_path):
    db = Database(sqlite_path=tmp_path / "proactive.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    bot = store.ensure_default_bot(OWNER)
    store.ensure_bot_session(OWNER, bot["id"], "side-session")
    monitor = store.create_monitor(
        OWNER,
        bot_id=bot["id"],
        name="测试监控",
        symbol="sh600519",
        threshold=1,
        session_id="side-session",
    )
    assert store.record_monitor_event(
        OWNER,
        monitor["id"],
        checked_at="2026-10-31T10:00:00+00:00",
        event_ref="announcement-1",
        title="测试公告",
        body="测试公告正文",
    )
    rows = store.list_messages(OWNER, "side-session")
    assert [(row["kind"], row["content"][:4]) for row in rows] == [("proactive", "**测试")]


def test_backfill_all_messages_covers_sessions(tmp_path):
    import json

    db = Database(sqlite_path=tmp_path / "backfill-all.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    for index in (1, 2):
        task, _ = store.create_task(
            owner_id=OWNER,
            query=f"历史问题 {index}",
            session_id=f"legacy-{index}",
            language="zh-CN",
            research_mode="quick",
            idempotency_key=f"legacy-all-{index}",
        )
        with db.connect(owner_id=OWNER, write=True) as conn:
            conn.execute(
                "UPDATE fb_tasks SET result_json = ? WHERE id = ? AND owner_id = ?",
                (json.dumps({"summary": f"历史回答 {index}"}, ensure_ascii=False), task["id"], OWNER),
            )
    result = store.backfill_all_messages(OWNER)
    assert result == {"sessions": 2, "messages": 4}
    assert store.backfill_all_messages(OWNER) == {"sessions": 0, "messages": 0}


def test_delete_session_removes_chat_records(tmp_path):
    db = Database(sqlite_path=tmp_path / "delete.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    bot = store.ensure_default_bot(OWNER)
    store.ensure_bot_session(OWNER, bot["id"], "delete-me")
    store.append_message(OWNER, session_id="delete-me", role="user", content="删除我")
    task, _ = store.create_task(
        owner_id=OWNER,
        query="删除我",
        session_id="delete-me",
        language="zh-CN",
        research_mode="quick",
        idempotency_key="delete-me-1",
    )
    result = store.delete_session(OWNER, "delete-me")
    assert result["session"] == 1
    assert result["tasks"] == 1
    assert store.list_messages(OWNER, "delete-me") == []
    assert store.list_tasks(OWNER, session_id="delete-me") == []


def test_clear_bot_sessions_removes_all_sessions(tmp_path):
    db = Database(sqlite_path=tmp_path / "clear-bot.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    bot = store.ensure_default_bot(OWNER)
    for name in ("one", "two"):
        store.ensure_bot_session(OWNER, bot["id"], name)
        store.create_task(
            owner_id=OWNER,
            query=name,
            session_id=name,
            language="zh-CN",
            research_mode="quick",
            idempotency_key=f"clear-{name}",
        )
        store.append_message(OWNER, session_id=name, role="user", content=name)
    result = store.clear_bot_sessions(OWNER, bot["id"])
    assert result == {"sessions": 2, "tasks": 2, "messages": 2}
    assert store.list_sessions(OWNER, bot_id=bot["id"]) == []


def test_thread_metadata_pin_rename_archive(tmp_path):
    db = Database(sqlite_path=tmp_path / "thread-meta.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    bot = store.ensure_default_bot(OWNER)
    store.ensure_bot_session(OWNER, bot["id"], "thread-meta")
    store.ensure_main_thread(OWNER, bot["id"], "thread-meta", "旧标题")
    updated = store.update_thread(OWNER, "thread-meta", title="新标题", pinned=True)
    assert updated["title"] == "新标题"
    assert bool(updated["pinned"]) is True
    store.mark_thread_read(OWNER, "thread-meta")
    archived = store.update_thread(OWNER, "thread-meta", archived=True)
    assert bool(archived["archived"]) is True


def test_monitor_failure_sets_retry_backoff(tmp_path):
    db = Database(sqlite_path=tmp_path / "backoff.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    bot = store.ensure_default_bot(OWNER)
    monitor = store.create_monitor(OWNER, bot_id=bot["id"], name="回退", symbol="sh600519", threshold=1)
    # Relative "now": a fixed timestamp makes this test expire as wall-clock
    # moves past the backoff window.
    from datetime import datetime, timezone

    store.record_monitor_check(OWNER, monitor["id"], checked_at=datetime.now(timezone.utc).isoformat(), error="provider down")
    updated = store.get_monitor(OWNER, monitor["id"])
    assert updated["failure_count"] == 1
    assert updated["retry_after"] is not None
    assert monitor["id"] not in {row["id"] for row in store.list_due_monitors()}


def test_thread_workflow_state_round_trips(tmp_path):
    db = Database(sqlite_path=tmp_path / "workflow-state.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    bot = store.ensure_default_bot(OWNER)
    store.ensure_main_thread(OWNER, bot["id"], "workflow", "报名")
    workflow = {"stage": "handoff", "handoff": {"reason": "Cloudflare"}}
    store.set_thread_workflow(OWNER, "workflow", workflow, "task-1")
    state = store.get_thread_workflow(OWNER, "workflow")
    assert state["workflow"] == workflow
    assert state["task_id"] == "task-1"
