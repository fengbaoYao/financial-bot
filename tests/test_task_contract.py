import sqlite3
from types import SimpleNamespace

import pytest

from api.financial_bot.db import Database
from api.financial_bot.executor import clarification_waiting
from api.financial_bot.store import FinancialBotStore, NotFoundError

OWNER = "owner-a"
OTHER = "owner-b"


@pytest.fixture()
def store(tmp_path):
    db = Database(sqlite_path=tmp_path / "bot.sqlite3")
    db.initialize()
    return FinancialBotStore(db)


def make_task(store: FinancialBotStore, **kwargs):
    task, _ = store.create_task(
        owner_id=kwargs.pop("owner_id", OWNER),
        query=kwargs.pop("query", "腾讯最近为什么下跌"),
        session_id=kwargs.pop("session_id", "s1"),
        language="zh-CN",
        research_mode="quick",
        idempotency_key=kwargs.pop("idempotency_key", "k1"),
        **kwargs,
    )
    return task


def test_waiting_user_requires_structured_reason_and_question(store):
    task = make_task(store)
    store.transition(OWNER, task["id"], "running")
    with pytest.raises(ValueError):
        store.transition(OWNER, task["id"], "waiting_user", reason="clarification")
    with pytest.raises(ValueError):
        store.transition(OWNER, task["id"], "waiting_user", waiting_reason="question")
    with pytest.raises(ValueError):
        store.transition(
            OWNER, task["id"], "waiting_user", waiting_reason="unknown", attention={"question": "哪只？"}
        )

    waiting = store.transition(
        OWNER,
        task["id"],
        "waiting_user",
        waiting_reason="question",
        attention={"question": "你说的是腾讯控股还是腾讯音乐？", "options": ["腾讯控股", "腾讯音乐"], "allow_free_text": True},
    )
    assert waiting["status"] == "waiting_user"
    assert waiting["waiting_reason"] == "question"
    assert waiting["attention"]["options"] == ["腾讯控股", "腾讯音乐"]

    running = store.transition(OWNER, task["id"], "running")
    assert running["waiting_reason"] is None
    assert running["attention"] is None


def test_dropped_is_terminal_and_carries_a_reason(store):
    task = make_task(store)
    with pytest.raises(ValueError):
        store.transition(OWNER, task["id"], "dropped")
    dropped = store.transition(OWNER, task["id"], "dropped", reason="no_actionable_request")
    assert dropped["status"] == "dropped"
    assert dropped["status_reason"] == "no_actionable_request"
    assert dropped["finished_at"]
    assert store.transition(OWNER, task["id"], "running") is None
    events = store.list_events(OWNER, task["id"])
    assert [event["status"] for event in events][-1] == "dropped"


def test_disposition_is_idempotent_per_input(store):
    first = store.record_disposition(
        OWNER,
        input_ref="msg-1",
        disposition="accepted",
        reason_code="research_started",
        reason_display="已开始研究",
        task_id="task_x",
        session_id="s1",
    )
    second = store.record_disposition(
        OWNER,
        input_ref="msg-1",
        disposition="dropped",
        reason_code="replay",
        reason_display="重复提交",
        session_id="s1",
    )
    assert first["id"] == second["id"]
    assert second["disposition"] == "accepted"
    assert len(store.list_dispositions(OWNER)) == 1

    with pytest.raises(ValueError):
        store.record_disposition(OWNER, input_ref="msg-2", disposition="invented")

    with pytest.raises(NotFoundError):
        store.get_disposition(OTHER, "msg-1")


def test_authorization_snapshot_and_continuation_round_trip(store):
    parent = make_task(store, idempotency_key="parent")
    child = make_task(
        store,
        idempotency_key="child",
        continued_from_task_id=parent["id"],
        authorization={
            "owner_id": OWNER,
            "scope": {"instrument": "00700.HK", "window": "2026-10-01..2026-10-31"},
            "data_sources": ["eastmoney", "yfinance"],
            "budget": {"credits": 8, "spent": 2},
            "model": "deepseek-chat",
            "prompt_version": 3,
            "data_authorization": "granted",
        },
    )
    assert child["continued_from_task_id"] == parent["id"]
    assert child["authorization"]["scope"]["instrument"] == "00700.HK"
    assert child["authorization"]["budget"]["credits"] == 8


def test_platform_pause_round_trip(store):
    assert store.get_platform_state()["paused"] is False

    paused = store.set_platform_pause(paused=True, by="budget", reason="monthly budget reached")
    assert paused["paused"] is True
    assert paused["paused_by"] == "budget"
    assert paused["pause_reason"] == "monthly budget reached"
    assert paused["paused_at"]

    resumed = store.set_platform_pause(paused=False, by="ops", reason=None)
    assert resumed["paused"] is False
    assert resumed["paused_at"] is None


def test_clarification_maps_to_structured_attention():
    response = SimpleNamespace(
        clarification=SimpleNamespace(question="哪只腾讯？", options=["腾讯控股", "腾讯音乐"], allow_free_text=False)
    )
    reason, attention = clarification_waiting(response)
    assert reason == "question"
    assert attention == {"question": "哪只腾讯？", "options": ["腾讯控股", "腾讯音乐"], "allow_free_text": False}

    assert clarification_waiting(SimpleNamespace(clarification=None)) == (None, None)


def test_existing_sqlite_gets_new_task_columns(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE fb_tasks (
            id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, session_id TEXT,
            idempotency_key TEXT NOT NULL, query TEXT NOT NULL,
            language TEXT NOT NULL DEFAULT 'auto', research_mode TEXT NOT NULL DEFAULT 'quick',
            status TEXT NOT NULL, status_reason TEXT, result_json TEXT, error_code TEXT,
            error_message TEXT, input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0, cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
            cancel_requested INTEGER NOT NULL DEFAULT 0, sequence INTEGER NOT NULL DEFAULT 0,
            ui_context_json TEXT, UNIQUE (owner_id, idempotency_key)
        )
        """
    )
    conn.commit()
    conn.close()

    db = Database(sqlite_path=path)
    db.initialize()
    store = FinancialBotStore(db)
    task = make_task(store, idempotency_key="legacy")
    assert task["authorization"] is None
    assert task["attention"] is None
