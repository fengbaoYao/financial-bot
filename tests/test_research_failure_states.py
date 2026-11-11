import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import api.financial_bot.routes as routes
from api.financial_bot.auth import OwnerContext
from api.financial_bot.db import Database
from api.financial_bot.executor import TaskExecutor, classify_response, result_document
from api.financial_bot.runtime import configure, reset
from api.financial_bot.store import FinancialBotStore
from api.main import app
from api.schemas import AgentClarification, AgentResponse, ResearchEvidence

client = TestClient(app)
OWNER = "11111111-1111-4111-8111-111111111111"


@pytest.fixture()
def bot_runtime(tmp_path):
    db = Database(sqlite_path=tmp_path / "bot.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    executor = TaskExecutor(store)
    configure(store=store, executor=executor)
    yield store, executor
    reset()


def use_owner(monkeypatch):
    context = OwnerContext(owner_id=OWNER, mode="supabase_authenticated")
    monkeypatch.setattr(routes, "resolve_owner", lambda request, config: context)


def wait_terminal(store, task_id):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        task = store.get_task(OWNER, task_id)
        if task["status"] in {"completed", "partial", "dropped", "failed", "cancelled", "waiting_user", "unknown"}:
            return task
        time.sleep(0.05)
    raise AssertionError("task did not finish")


def test_classification_does_not_disguise_failure(monkeypatch):
    source = ResearchEvidence(
        id="ev_1",
        source="example",
        retrieved_at="2026-10-28T00:00:00+00:00",
        freshness="unknown",
    )
    error_with_sources = AgentResponse(
        intent="error",
        summary="综合未完成，保留来源。",
        evidence=[source],
        payload={"_meta": {"synthesis_incomplete": True}},
    )
    assert classify_response(error_with_sources)[0] == "partial"

    error_without_sources = AgentResponse(intent="error", summary="失败", payload={})
    assert classify_response(error_without_sources)[0] == "failed"

    incomplete = AgentResponse(
        intent="pydantic_ai_agent",
        summary="部分结果",
        payload={"_meta": {"synthesis_incomplete": True, "limit_reason": "deadline"}},
    )
    assert classify_response(incomplete) == ("partial", "deadline")

    clarification = AgentResponse(
        intent="clarification",
        summary="请确认",
        clarification=AgentClarification(question="哪家公司？", options=["腾讯", "茅台"]),
        payload={},
    )
    assert classify_response(clarification)[0] == "waiting_user"

    empty = AgentResponse(intent="pydantic_ai_agent", summary="", payload={})
    assert classify_response(empty)[0] == "failed"


def test_result_document_is_bounded_and_has_no_private_payload():
    response = AgentResponse(
        intent="pydantic_ai_agent",
        summary="回答",
        citations=["https://example.org"],
        evidence=[
            ResearchEvidence(
                id="ev_1",
                source="example",
                url="https://example.org",
                retrieved_at="2026-10-28T00:00:00+00:00",
                freshness="historical_or_delayed",
                excerpts=[{"location": "p1", "text": "x" * 900} for _ in range(4)],
            )
        ],
        payload={"chart_series": [{"secret": "not stored"}], "metadata": {"data_plan": {"capability": "quote_history", "market": "us"}}, "_meta": {"usage": {"input_tokens": 1}, "tools_used": ["web_search"]}},
    )
    document = result_document(response)
    assert "chart_series" not in document
    assert len(document["evidence"][0]["excerpts"]) == 2
    assert len(document["evidence"][0]["excerpts"][0]["text"]) == 500
    assert document["meta"]["usage"] == {"input_tokens": 1}
    assert document["data_plan"] == {"capability": "quote_history", "market": "us"}


def test_dropped_needs_an_explicit_signal_from_the_model():
    # The agent's own "I could not identify a task" answer means nothing ran.
    unknown = AgentResponse(
        intent="unknown",
        summary="没有识别出明确目标。当前支持单股分析、消息查询、热点查看和持仓分析。",
        payload={},
    )
    assert classify_response(unknown) == ("dropped", "no_actionable_target")

    # A model that says outright that no research is needed also settles it.
    declared = AgentResponse(
        intent="pydantic_ai_agent",
        summary="这个问题不需要研究，直接回答即可。",
        payload={"_meta": {"disposition": "dropped", "disposition_reason": "No Research Needed"}},
    )
    assert classify_response(declared) == ("dropped", "no_research_needed")

    # A direct answer is still an answer, not "nothing to do".
    greeting = AgentResponse(intent="greeting", summary="你好，我是 Financial Bot。", payload={})
    assert classify_response(greeting) == ("completed", None)

    # Prose alone never downgrades an ordinary answer.
    prose = AgentResponse(
        intent="pydantic_ai_agent",
        summary="这项研究不需要额外数据，结论如下：营收同比下滑 2.1%。",
        payload={"_meta": {"tools_used": ["web_search"]}},
    )
    assert classify_response(prose) == ("completed", None)

    # With no visible text there is nothing to explain, so it stays a failure.
    assert classify_response(AgentResponse(intent="unknown", summary="   ", payload={})) == (
        "failed",
        "empty_answer",
    )


def test_model_declared_no_research_becomes_a_dropped_payload():
    from api.agent_pydantic import AgentOutput, build_agent_response

    declared = build_agent_response(
        AgentOutput(summary="这个问题不需要再查数据，直接回答即可。", no_research_reason="out of scope"),
        [],
        "zh-CN",
    )
    assert declared.payload["_meta"]["disposition"] == "dropped"
    assert classify_response(declared) == ("dropped", "out_of_scope")

    # An ordinary answer keeps its completed status.
    ordinary = build_agent_response(AgentOutput(summary="腾讯广告收入增速在放缓。"), [], "zh-CN")
    assert "disposition" not in ordinary.payload["_meta"]
    assert classify_response(ordinary) == ("completed", None)


def test_unknown_input_ends_as_dropped_with_a_visible_reason(bot_runtime, monkeypatch):
    store, executor = bot_runtime
    use_owner(monkeypatch)

    async def runner(_owner, task, progress):
        return AgentResponse(
            intent="unknown",
            summary="没有识别出明确目标。当前支持单股分析、消息查询、热点查看和持仓分析。",
            payload={},
        )

    executor.set_agent_runner(runner)
    task_id = client.post("/api/tasks", json={"query": "今天天气怎么样"}).json()["task"]["id"]
    task = wait_terminal(store, task_id)
    assert task["status"] == "dropped"
    # The reason code is stored for audit, while the event carries the words the
    # person sees (status_reason is stripped from the public task).
    assert task["status_reason"] == "no_actionable_target"
    events = store.list_events(OWNER, task_id)
    assert events[-1]["status"] == "dropped"
    assert "目标" in events[-1]["display_summary"]
    snapshot = client.get(f"/api/tasks/{task_id}").json()["task"]
    assert snapshot["status"] == "dropped"
    assert "status_reason" not in snapshot
    assert snapshot["result"]["summary"].startswith("没有识别出明确目标")

    # The event stream must stop on a dropped task instead of polling forever.
    with client.stream(
        "GET",
        f"/api/tasks/{task_id}/events?after_sequence=0",
        headers={"Accept": "text/event-stream"},
    ) as response:
        body = "".join(response.iter_text())
    assert "event: done" in body
    assert '"status": "dropped"' in body


def test_failed_task_is_failed_and_redacts_credentials(bot_runtime, monkeypatch):
    store, executor = bot_runtime
    use_owner(monkeypatch)

    async def runner(_owner, task, progress):
        progress("tool_started", 30, "调用来源", {"tool": "web_search"})
        raise RuntimeError("provider failed api_key=sk-super-secret-value")

    executor.set_agent_runner(runner)
    task_id = client.post("/api/tasks", json={"query": "研究腾讯"}).json()["task"]["id"]
    task = wait_terminal(store, task_id)
    assert task["status"] == "failed"
    assert task["error_code"] == "RuntimeError"
    assert "sk-super-secret-value" not in (task["error_message"] or "")
    events = store.list_events(OWNER, task_id)
    assert events[-1]["status"] == "failed"
    latest: dict[str, str] = {}
    for event in events:
        if event["step_id"]:
            latest[event["step_id"]] = event["status"]
    assert all(status != "running" for status in latest.values())
    assert "sk-super-secret-value" not in str(events)


def test_partial_and_waiting_statuses_survive(bot_runtime, monkeypatch):
    store, executor = bot_runtime
    use_owner(monkeypatch)

    async def partial_runner(_owner, task, progress):
        return AgentResponse(
            intent="error",
            summary="综合失败，仅保留来源。",
            evidence=[
                ResearchEvidence(
                    id="ev_1", source="example", retrieved_at="2026-10-28T00:00:00+00:00", freshness="unknown"
                )
            ],
            data_gaps=["综合未完成"],
            payload={"_meta": {"synthesis_incomplete": True}},
        )

    executor.set_agent_runner(partial_runner)
    partial_id = client.post("/api/tasks", json={"query": "研究腾讯"}).json()["task"]["id"]
    assert wait_terminal(store, partial_id)["status"] == "partial"

    async def waiting_runner(_owner, task, progress):
        return AgentResponse(
            intent="clarification",
            summary="哪家公司？",
            clarification=AgentClarification(question="哪家公司？", options=["腾讯", "茅台"]),
            payload={},
        )

    executor.set_agent_runner(waiting_runner)
    waiting_id = client.post("/api/tasks", json={"query": "分析一下"}).json()["task"]["id"]
    waiting = wait_terminal(store, waiting_id)
    assert waiting["status"] == "waiting_user"
    snapshot = client.get(f"/api/tasks/{waiting_id}").json()["task"]
    assert snapshot["result"]["clarification"]["question"] == "哪家公司？"


def test_structured_output_failure_keeps_visible_answer_as_partial():
    from types import SimpleNamespace

    from api.agent_pydantic import _text_output_fallback

    deps = SimpleNamespace(language="zh-CN", tool_results=[], user_query="茅台公告")
    response = _text_output_fallback(deps, "## 已检索到的公告\n\n- 贵州茅台2026年半年度报告")
    assert response is not None
    assert response.intent == "pydantic_ai_agent"
    assert "半年度报告" in response.summary
    assert response.payload["_meta"]["synthesis_incomplete"] is True
    assert response.payload["_meta"]["failure_type"] == "UnexpectedModelBehavior"
    assert response.payload["_meta"]["output_fallback"] == "text"
    assert any("结构化输出" in gap for gap in response.data_gaps)
    assert _text_output_fallback(deps, "   ") is None


def test_store_lists_sessions_with_scoped_tasks(bot_runtime):
    store, _executor = bot_runtime
    for index, (session, query) in enumerate((("s1", "苹果行情"), ("s2", "茅台公告"), ("s1", "苹果财报"))):
        store.create_task(
            owner_id=OWNER,
            query=query,
            session_id=session,
            language="zh-CN",
            research_mode="quick",
            idempotency_key=f"session-{index}",
        )
    sessions = store.list_sessions(OWNER)
    assert [row["id"] for row in sessions] == ["s1", "s2"]
    assert sessions[0]["task_count"] == 2
    assert sessions[0]["title"] == "苹果行情"
    assert sessions[1]["title"] == "茅台公告"


def test_bot_scope_owns_conversations_separately(bot_runtime):
    store, _executor = bot_runtime
    default = store.ensure_default_bot(OWNER)
    custom = store.create_bot(
        OWNER,
        name="腾讯观察员",
        description="只看腾讯",
        instructions="回答保持简短",
        approval_threshold=2,
    )
    store.ensure_bot_session(OWNER, custom["id"], "custom-session")
    for index, session in enumerate(("default-session", "custom-session")):
        store.create_task(
            owner_id=OWNER,
            query=f"问题 {index}",
            session_id=session,
            language="zh-CN",
            research_mode="quick",
            idempotency_key=f"bot-scope-{index}",
        )
    store.ensure_default_bot(OWNER)
    assert store.bot_for_session(OWNER, "custom-session")["id"] == custom["id"]
    assert [row["id"] for row in store.list_sessions(OWNER, bot_id=custom["id"])] == ["custom-session"]
    assert [row["id"] for row in store.list_sessions(OWNER, bot_id=default["id"])] == ["default-session"]
    assert store.get_bot(OWNER, custom["id"])["approval_threshold"] == 2


def test_model_gate_requires_explicit_approval(bot_runtime, monkeypatch):
    store, _executor = bot_runtime
    bot = store.ensure_default_bot(OWNER)
    use_owner(monkeypatch)
    import api.main as main

    monkeypatch.setattr(
        main.settings_store,
        "build_runtime_config",
        lambda config: SimpleNamespace(llm_api_key="test-key", llm_model="test-model"),
    )
    response = client.post(
        "/api/tasks",
        json={
            "query": "全面分析腾讯和阿里巴巴的基本面，并做深度研究",
            "session_id": "gate-api-session",
            "bot_id": bot["id"],
        },
        headers={"Idempotency-Key": "gate-api-1"},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "model_approval_required"
    assert response.json()["estimated_credits"] > response.json()["threshold"]


def test_memory_context_is_scoped_to_bot(bot_runtime):
    store, _executor = bot_runtime
    default = store.ensure_default_bot(OWNER)
    custom = store.create_bot(OWNER, name="腾讯观察员", instructions="只看腾讯")
    store.add_direct_memory(
        OWNER,
        kind="watch_object",
        text="默认 Bot 关注苹果",
        source_text="默认 Bot 关注苹果",
        source_ref="test:default",
        bot_id=default["id"],
    )
    store.add_direct_memory(
        OWNER,
        kind="watch_object",
        text="腾讯观察员只关注腾讯",
        source_text="腾讯观察员只关注腾讯",
        source_ref="test:custom",
        bot_id=custom["id"],
    )
    default_context = store.build_memory_context(OWNER, bot_id=default["id"], include_legacy=True)
    custom_context = store.build_memory_context(OWNER, bot_id=custom["id"])
    assert any("苹果" in item for item in default_context)
    assert not any("腾讯观察员" in item for item in default_context)
    assert any("腾讯" in item for item in custom_context)
    assert not any("苹果" in item for item in custom_context)
    assert [row["text"] for row in store.list_memories(OWNER, bot_id=custom["id"])] == ["腾讯观察员只关注腾讯"]


def test_delete_bot_moves_work_to_default_and_protects_default(bot_runtime):
    import pytest as _pytest
    from api.financial_bot.store import NotFoundError

    store, _executor = bot_runtime
    default = store.ensure_default_bot(OWNER)
    custom = store.create_bot(OWNER, name="临时 Bot")
    store.create_task(
        owner_id=OWNER,
        query="临时任务",
        session_id="delete-bot-session",
        language="zh-CN",
        research_mode="quick",
        idempotency_key="delete-bot-task",
    )
    store.ensure_bot_session(OWNER, custom["id"], "delete-bot-session")
    store.add_direct_memory(
        OWNER,
        kind="watch_object",
        text="临时 Bot 的关注对象",
        source_text="临时 Bot 的关注对象",
        source_ref="test:delete",
        bot_id=custom["id"],
    )
    result = store.delete_bot(OWNER, custom["id"])
    assert result["moved_to"] == default["id"]
    assert result["moved_sessions"] == 1
    assert store.get_bot(OWNER, custom["id"]) is None
    assert store.bot_for_session(OWNER, "delete-bot-session")["id"] == default["id"]
    assert any("临时 Bot 的关注对象" in item for item in store.build_memory_context(OWNER, bot_id=default["id"], include_legacy=True))
    with _pytest.raises(ValueError, match="default_bot_cannot_be_deleted"):
        store.delete_bot(OWNER, default["id"])


def test_price_monitor_writes_unread_notification_and_respects_cooldown(bot_runtime):
    from datetime import datetime, timedelta, timezone

    store, _executor = bot_runtime
    bot = store.ensure_default_bot(OWNER)
    monitor = store.create_monitor(
        OWNER,
        bot_id=bot["id"],
        name="AAPL 波动",
        symbol="US.AAPL",
        threshold=3,
        direction="both",
        interval_minutes=15,
        cooldown_minutes=120,
    )
    now = datetime.now(timezone.utc)
    result = store.record_monitor_check(
        OWNER,
        monitor["id"],
        checked_at=now.isoformat(),
        price=200,
        change_pct=-3.5,
        data_as_of="2026-10-29",
        currency="USD",
    )
    assert result["triggered"] is True
    notifications = store.list_notifications(OWNER, bot_id=bot["id"], unread_only=True)
    assert len(notifications) == 1
    assert "AAPL" in notifications[0]["body"]
    assert "不是实时行情" in notifications[0]["body"]
    again = store.record_monitor_check(
        OWNER,
        monitor["id"],
        checked_at=(now + timedelta(minutes=10)).isoformat(),
        price=199,
        change_pct=-4.0,
        data_as_of="2026-10-29",
        currency="USD",
    )
    assert again["triggered"] is False
    assert len(store.list_notifications(OWNER, bot_id=bot["id"], unread_only=True)) == 1
    store.mark_notification_read(OWNER, notifications[0]["id"])
    assert store.list_notifications(OWNER, bot_id=bot["id"], unread_only=True) == []


def test_monitor_runner_uses_polling_fetcher_and_creates_one_alert(bot_runtime):
    import asyncio
    from api.financial_bot.monitors import MonitorRunner

    store, _executor = bot_runtime
    bot = store.ensure_default_bot(OWNER)
    store.create_monitor(
        OWNER,
        bot_id=bot["id"],
        name="NVDA 波动",
        symbol="US.NVDA",
        threshold=2,
        direction="both",
        interval_minutes=15,
    )
    monkeypatch = __import__("pytest").MonkeyPatch()
    import api.financial_bot.monitors as monitor_module
    monkeypatch.setattr(monitor_module, "market_is_open", lambda symbol: True)
    try:
        runner = MonitorRunner(
            store,
            interval_seconds=60,
            fetcher=lambda symbol: {"price": 100, "change_pct": 2.5, "data_as_of": "2026-10-29", "currency": "USD"},
        )
        result = asyncio.run(runner.run_once())
    finally:
        monkeypatch.undo()
    assert result["checked"] == 1
    assert result["triggered"] == 1
    assert len(store.list_notifications(OWNER, bot_id=bot["id"])) == 1


def test_announcement_monitor_is_deduplicated(bot_runtime):
    import asyncio
    import api.financial_bot.monitors as monitor_module
    from api.financial_bot.monitors import MonitorRunner

    store, _executor = bot_runtime
    bot = store.ensure_default_bot(OWNER)
    monitor = store.create_monitor(
        OWNER,
        bot_id=bot["id"],
        name="茅台公告",
        symbol="600519",
        condition="announcement",
        threshold=0,
        interval_minutes=15,
    )
    monkeypatch = __import__("pytest").MonkeyPatch()
    monkeypatch.setattr(monitor_module, "fetch_announcement_snapshot", lambda symbol: {
        "event_ref": "announcement-1",
        "title": "2026-10-29 · 茅台季度报告",
        "body": "600519 出现新的巨潮公告：茅台季度报告。",
    })
    try:
        runner = MonitorRunner(store, fetcher=lambda symbol: {})
        first = asyncio.run(runner.run_once())
        second = asyncio.run(runner.run_once())
        assert first["triggered"] == 1
        assert second["triggered"] == 0
        assert len(store.list_notifications(OWNER, bot_id=bot["id"])) == 1
        assert monitor["condition"] == "announcement"
    finally:
        monkeypatch.undo()


def test_earnings_and_watchlist_report_use_same_notification_path(bot_runtime):
    import asyncio
    import api.financial_bot.monitors as monitor_module
    from api.financial_bot.monitors import MonitorRunner

    store, _executor = bot_runtime
    bot = store.ensure_default_bot(OWNER)
    earnings = store.create_monitor(
        OWNER,
        bot_id=bot["id"],
        name="AAPL 财报",
        symbol="US.AAPL",
        condition="earnings",
        threshold=7,
        interval_minutes=1440,
    )
    report = store.create_monitor(
        OWNER,
        bot_id=bot["id"],
        name="自选股周报",
        symbol="WATCHLIST",
        condition="report",
        threshold=0,
        interval_minutes=10080,
    )
    monkeypatch = __import__("pytest").MonkeyPatch()
    monkeypatch.setattr(monitor_module, "fetch_earnings_snapshot", lambda symbol, days=7: {
        "event_ref": "earnings:2026-11-05",
        "title": "US.AAPL 财报日",
        "body": "财报日历提醒。",
    })
    monkeypatch.setattr(monitor_module, "fetch_watchlist_report", lambda monitor: {
        "event_ref": "report:周报:2026-10-29",
        "title": "自选股周报",
        "body": "AAPL +1.2%。",
    })
    try:
        result = asyncio.run(MonitorRunner(store, fetcher=lambda symbol: {}).run_once())
        assert result["triggered"] == 2
        titles = [row["title"] for row in store.list_notifications(OWNER, bot_id=bot["id"])]
        assert "US.AAPL 财报日" in titles
        assert "自选股周报" in titles
        assert store.get_monitor(OWNER, earnings["id"])["last_event_ref"] == "earnings:2026-11-05"
        assert store.get_monitor(OWNER, report["id"])["last_event_ref"] == "report:周报:2026-10-29"
    finally:
        monkeypatch.undo()


def test_outbound_webhook_is_opt_in_and_best_effort(monkeypatch):
    from api.financial_bot.dispatch import dispatch_notification

    monkeypatch.delenv("FINANCIAL_BOT_WEBHOOK_URL", raising=False)
    assert dispatch_notification({"title": "x", "body": "y"})["webhook"] is False

    import api.financial_bot.dispatch as dispatch_module

    monkeypatch.setenv("FINANCIAL_BOT_WEBHOOK_URL", "https://example.org/hook")
    calls = []

    class Response:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(dispatch_module.urllib.request, "urlopen", lambda request, timeout: calls.append(request) or Response())
    result = dispatch_notification({"title": "x", "body": "y", "bot_id": "b"})
    assert result["webhook"] is True
    assert len(calls) == 1
    assert calls[0].data


def test_market_hours_quiet_hours_and_private_channel_settings():
    from datetime import datetime, time, timezone
    from api.financial_bot.monitors import in_quiet_hours, market_is_open

    # 2026-10-29 is a Thursday.
    assert market_is_open("US.AAPL", datetime(2026, 10, 29, 14, 0, tzinfo=timezone.utc)) is True
    assert market_is_open("US.AAPL", datetime(2026, 10, 31, 14, 0, tzinfo=timezone.utc)) is False
    assert market_is_open("US.AAPL", datetime(2026, 7, 3, 14, 0, tzinfo=timezone.utc)) is False
    assert market_is_open("US.AAPL", datetime(2026, 11, 27, 17, 0, tzinfo=timezone.utc)) is True
    assert market_is_open("US.AAPL", datetime(2026, 11, 27, 18, 30, tzinfo=timezone.utc)) is False
    assert market_is_open("600519", datetime(2026, 10, 29, 2, 0, tzinfo=timezone.utc)) is True
    assert in_quiet_hours("22:00", "07:00", time(23, 0)) is True
    assert in_quiet_hours("22:00", "07:00", time(12, 0)) is False


def test_public_bot_never_exposes_webhook_url(bot_runtime):
    store, _executor = bot_runtime
    default = store.ensure_default_bot(OWNER)
    updated = store.update_bot(
        OWNER,
        default["id"],
        webhook_url="https://example.org/secret-hook",
        notification_email="alerts@example.com",
        market_hours_only=False,
        max_alerts_per_day=5,
    )
    assert updated["webhook_configured"] is True
    assert "webhook_url" not in updated
    assert "secret-hook" not in str(updated)
    channels = store.get_bot_channels(OWNER, default["id"])
    assert channels["webhook_url"] == "https://example.org/secret-hook"
    assert channels["notification_email"] == "alerts@example.com"
    assert channels["max_alerts_per_day"] == 5


def test_bot_test_notification_reports_unconfigured_without_leaking(bot_runtime, monkeypatch):
    store, _executor = bot_runtime
    bot = store.ensure_default_bot(OWNER)
    use_owner(monkeypatch)
    response = client.post(f"/api/bots/{bot['id']}/test-notification")
    assert response.status_code == 200
    assert response.json() == {"webhook": False, "email": False, "configured": False}
    assert "webhook_url" not in response.text


def test_daily_digest_marks_sent_without_channels_and_respects_hour(bot_runtime):
    import asyncio
    from api.financial_bot.monitors import MonitorRunner

    store, _executor = bot_runtime
    bot = store.ensure_default_bot(OWNER)
    store.update_bot(OWNER, bot["id"], digest_mode="daily", digest_hour="00:00")
    monitor = store.create_monitor(
        OWNER,
        bot_id=bot["id"],
        name="digest fixture",
        symbol="US.AAPL",
        threshold=1,
        interval_minutes=15,
    )
    store.record_monitor_check(
        OWNER,
        monitor["id"],
        checked_at="2026-10-29T12:00:00+00:00",
        price=100,
        change_pct=2,
        data_as_of="2026-10-29",
        currency="USD",
    )
    runner = MonitorRunner(store, fetcher=lambda symbol: {})
    assert asyncio.run(runner.run_digests()) == 1
    updated = store.get_bot(OWNER, bot["id"])
    assert updated["last_digest_date"] is not None
    assert asyncio.run(runner.run_digests()) == 0


def test_delivery_audit_keeps_payload_for_retry_but_hides_it_from_list(bot_runtime):
    store, _executor = bot_runtime
    bot = store.ensure_default_bot(OWNER)
    store.record_delivery(
        OWNER,
        bot_id=bot["id"],
        notification_id="notif_1",
        channel="webhook",
        status="failed",
        payload={"title": "fixture", "body": "body"},
    )
    rows = store.list_deliveries(OWNER, bot_id=bot["id"])
    assert rows and rows[0]["status"] == "failed"
    assert "payload_json" not in rows[0]
    delivery = store.get_delivery(OWNER, rows[0]["id"])
    assert delivery and delivery["payload_json"]
    store.update_delivery(OWNER, rows[0]["id"], status="sent")
    assert store.get_delivery(OWNER, rows[0]["id"])["attempts"] == 2


def test_digest_preview_and_send_without_channels(bot_runtime, monkeypatch):
    store, _executor = bot_runtime
    bot = store.ensure_default_bot(OWNER)
    monitor = store.create_monitor(
        OWNER,
        bot_id=bot["id"],
        name="digest api",
        symbol="US.AAPL",
        threshold=1,
        interval_minutes=1440,
    )
    store.record_monitor_check(
        OWNER,
        monitor["id"],
        checked_at="2026-10-29T12:00:00+00:00",
        price=100,
        change_pct=2,
        data_as_of="2026-10-29",
        currency="USD",
    )
    use_owner(monkeypatch)
    preview = client.get(f"/api/bots/{bot['id']}/digest-preview")
    assert preview.status_code == 200
    assert preview.json()["digest"]["unread_count"] >= 1
    sent = client.post(f"/api/bots/{bot['id']}/digest/send")
    assert sent.status_code == 200
    assert sent.json()["configured"] is False
    assert "webhook_url" not in sent.text


def test_failed_delivery_is_retried_with_backoff(bot_runtime):
    import asyncio
    import api.financial_bot.dispatch as dispatch_module
    from api.financial_bot.monitors import MonitorRunner

    store, _executor = bot_runtime
    bot = store.ensure_default_bot(OWNER)
    store.record_delivery(
        OWNER,
        bot_id=bot["id"],
        notification_id="n1",
        channel="webhook",
        status="failed",
        payload={"title": "retry", "body": "body"},
    )
    delivery = store.list_deliveries(OWNER, bot_id=bot["id"])[0]
    with store.db.connect(owner_id=OWNER, write=True) as conn:
        conn.execute("UPDATE fb_notification_deliveries SET next_retry_at = ? WHERE id = ?", ("2000-01-01T00:00:00+00:00", delivery["id"]))
    monkeypatch = __import__("pytest").MonkeyPatch()
    monkeypatch.setattr(dispatch_module, "dispatch_channel", lambda *a, **kw: True)
    try:
        assert asyncio.run(MonitorRunner(store, fetcher=lambda symbol: {}).run_retries()) == 1
        updated = store.get_delivery(OWNER, delivery["id"])
        assert updated["status"] == "sent"
        assert updated["attempts"] == 2
    finally:
        monkeypatch.undo()


def test_delivery_log_filters_by_status_and_channel(bot_runtime):
    store, _executor = bot_runtime
    bot = store.ensure_default_bot(OWNER)
    for status, channel in (("sent", "webhook"), ("failed", "email"), ("failed", "webhook")):
        store.record_delivery(
            OWNER,
            bot_id=bot["id"],
            notification_id=None,
            channel=channel,
            status=status,
            payload={"title": "fixture", "body": "body"},
        )
    assert len(store.list_deliveries(OWNER, bot_id=bot["id"], status="failed")) == 2
    assert len(store.list_deliveries(OWNER, bot_id=bot["id"], channel="email")) == 1


def test_delivery_moves_to_dead_letter_after_four_attempts(bot_runtime):
    store, _executor = bot_runtime
    bot = store.ensure_default_bot(OWNER)
    store.record_delivery(OWNER, bot_id=bot["id"], notification_id=None, channel="webhook", status="failed", payload={"title": "x", "body": "y"})
    delivery = store.list_deliveries(OWNER, bot_id=bot["id"])[0]
    for _ in range(3):
        store.update_delivery(OWNER, delivery["id"], status="failed", error="still down")
    final = store.get_delivery(OWNER, delivery["id"])
    assert final["attempts"] == 4
    assert final["status"] == "dead"
    assert final["next_retry_at"] is None


def test_owner_scoped_market_closures_and_monitor_mute(bot_runtime):
    store, _executor = bot_runtime
    bot = store.ensure_default_bot(OWNER)
    closure = store.add_market_closure(OWNER, market="us", closure_date="2026-11-16", reason="test")
    assert closure["market"] == "us"
    assert store.owner_market_closed(OWNER, "us", "2026-11-16") is True
    assert store.owner_market_closed(OWNER, "hk", "2026-11-16") is False
    assert store.delete_market_closure(OWNER, closure["id"]) is True
    monitor = store.create_monitor(OWNER, bot_id=bot["id"], name="mute", symbol="US.AAPL", threshold=2)
    muted = store.update_monitor(OWNER, monitor["id"], muted_until="2026-10-30T00:00:00+00:00")
    assert muted["muted_until"] == "2026-10-30T00:00:00+00:00"


def test_plan_only_answer_detection_after_tools():
    from api.agent_pydantic import is_plan_only_answer

    evidence = [{"tool": "get_company_fundamentals", "status": "completed", "evidence": [{"id": "ev_1"}]}]
    assert is_plan_only_answer("我会先核验公司，然后拉取最新的 SEC 财务数据。", evidence) is True
    assert is_plan_only_answer("I'll first verify the company and then fetch the latest SEC filing.", evidence) is True
    assert is_plan_only_answer("苹果最新季度营收 1,094 亿美元，同比下降 2.1%。", evidence) is False
    assert is_plan_only_answer("我会先核验公司，然后拉取最新的 SEC 财务数据。", []) is False


def test_financial_filing_query_routing_helper():
    from api.agent_pydantic import requests_financial_filing

    assert requests_financial_filing("分析苹果最新 SEC 财报") is True
    assert requests_financial_filing("苹果 2025 财年利润表") is True
    assert requests_financial_filing("苹果最近股价走势") is False
    assert requests_financial_filing("苹果最新消息") is False


def test_create_agent_is_not_accidentally_empty():
    from api.agent_pydantic import create_agent

    agent = create_agent(api_key="test-key", base_url="https://example.invalid", model="test-model")
    assert agent is not None
    assert type(agent).__name__ == "Agent"


def test_artifacts_are_scoped_to_bot_and_completed_tasks(bot_runtime):
    import json

    store, _executor = bot_runtime
    bot = store.ensure_default_bot(OWNER)
    task, _ = store.create_task(
        owner_id=OWNER,
        query="苹果财报",
        session_id="artifact-session",
        language="zh-CN",
        research_mode="quick",
        idempotency_key="artifact-1",
    )
    store.ensure_bot_session(OWNER, bot["id"], "artifact-session")
    with store.db.connect(owner_id=OWNER, write=True) as conn:
        conn.execute(
            "UPDATE fb_tasks SET status='completed', result_json=? WHERE id=?",
            (json.dumps({"summary": "苹果财报摘要", "evidence": [{"id": "ev_1"}], "data_gaps": ["缺现金流"]}, ensure_ascii=False), task["id"]),
        )
    artifacts = store.list_artifacts(OWNER, bot_id=bot["id"])
    assert [row["id"] for row in artifacts] == [task["id"]]
    assert artifacts[0]["summary"] == "苹果财报摘要"
    assert artifacts[0]["evidence_count"] == 1
    assert artifacts[0]["data_gap_count"] == 1


def test_unexpected_model_behavior_keeps_the_last_draft_only(monkeypatch):
    """Historical failure, reproduced without any network.

    The provider streams prose instead of the ``final_result`` tool call, so
    pydantic-ai raises ``UnexpectedModelBehavior`` after its output retries.
    The already-streamed answer must survive as a partial result -- and exactly
    once, not once per failed attempt.
    """
    import asyncio
    import json

    import httpx
    from openai import AsyncOpenAI

    from api.agent_pydantic import AgentDeps, create_agent, run_agent_async

    attempts = []

    def chunk(delta, finish=None):
        payload = {
            "id": "offline",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "deepseek-flash",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        if finish:
            payload["usage"] = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        return ("data: " + json.dumps(payload) + "\n\n").encode()

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield chunk({"role": "assistant", "content": ""})
            yield chunk({"content": "## 已检索到的公告\n\n"})
            yield chunk({"content": "- 贵州茅台 2026 年半年度报告"})
            yield chunk({}, "stop")
            yield b"data: [DONE]\n\n"

        async def aclose(self):
            return None

    def handler(request):
        attempts.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Stream())

    async def scenario():
        client = AsyncOpenAI(
            api_key="offline",
            base_url="https://offline.invalid",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        monkeypatch.setattr("api.agent_pydantic.AsyncOpenAI", lambda **kw: client)
        try:
            deps = AgentDeps(
                stock_service=SimpleNamespace(),
                news_service=SimpleNamespace(),
                hotspot_service=SimpleNamespace(),
                portfolio_service=SimpleNamespace(),
                web_search_service=SimpleNamespace(),
                language="zh-CN",
            )
            agent = create_agent(api_key="offline", model="deepseek-flash")
            return await run_agent_async(agent, deps, "茅台最近有什么公告", lambda *args: None)
        finally:
            await client.close()

    response = asyncio.run(scenario())
    assert len(attempts) >= 2, "the structured-output retry must have happened"
    meta = response.payload["_meta"]
    assert meta["failure_type"] == "UnexpectedModelBehavior"
    assert meta["output_fallback"] == "text"
    assert response.summary == "## 已检索到的公告\n\n- 贵州茅台 2026 年半年度报告"
    assert any("结构化输出" in gap for gap in response.data_gaps)
    # The visible answer is a full reply; the downgrade is a label, not a half-answer.
    assert classify_response(response) == ("completed", "structured_output_fallback")
    assert response.payload["_meta"]["answer_basis"] == "model_knowledge"


def _plan_with_one_market_step(task_id: str, query: str):
    from api.financial_bot.plan import PlanStep, TaskPlan, ValidationRule

    return TaskPlan(
        task_id=task_id,
        original_query=query,
        steps=[
            PlanStep(
                step_id="market1",
                title="取到行情证据",
                kind="market",
                tools=["get_stock_analysis"],
                validation=ValidationRule(
                    kind="evidence", tools=["get_stock_analysis"], min_evidence=1, description="至少一条行情证据"
                ),
            )
        ],
    )


def test_model_only_answer_stays_completed_instead_of_partial(bot_runtime, monkeypatch):
    """No source data must not turn a usable answer into "partially completed"."""
    store, executor = bot_runtime
    use_owner(monkeypatch)

    async def runner(_owner, task, _progress):
        executor._plans[task["id"]] = _plan_with_one_market_step(task["id"], task["query"])
        return AgentResponse(
            intent="pydantic_ai_agent",
            summary="腾讯近期回调的可能原因：广告与游戏增速放缓、回购未完全对冲解禁压力、港股整体估值收缩。以上为判断，不是已核实的事实。",
            data_gaps=["本次没有取到可用的数据源；以下内容来自模型知识，未经实时数据核实。"],
            payload={"_meta": {"answer_basis": "model_knowledge"}},
        )

    executor.set_agent_runner(runner)
    task_id = client.post("/api/tasks", json={"query": "分析腾讯最近为什么下跌"}).json()["task"]["id"]
    task = wait_terminal(store, task_id)
    assert task["status"] == "completed", task
    # The basis travels to the client so the UI can label the answer.
    assert task["result"]["meta"]["answer_basis"] == "model_knowledge"


def test_plan_gaps_still_downgrade_a_sourced_answer(bot_runtime, monkeypatch):
    """The exemption above is narrow: an answer that claims sources still pays for plan gaps."""
    store, executor = bot_runtime
    use_owner(monkeypatch)

    async def runner(_owner, task, _progress):
        executor._plans[task["id"]] = _plan_with_one_market_step(task["id"], task["query"])
        return AgentResponse(
            intent="pydantic_ai_agent",
            summary="有来源的部分结论。",
            payload={"_meta": {"answer_basis": "source_data"}},
        )

    executor.set_agent_runner(runner)
    task_id = client.post("/api/tasks", json={"query": "分析腾讯最近为什么下跌"}).json()["task"]["id"]
    task = wait_terminal(store, task_id)
    assert task["status"] == "partial", task


def test_agent_answer_without_any_source_is_labelled_model_knowledge(monkeypatch):
    """The provider answered but no tool produced evidence: the reply ships with a label."""
    import asyncio
    import json

    import httpx
    from openai import AsyncOpenAI

    from api.agent_pydantic import AgentDeps, create_agent, run_agent_async

    output = {
        "summary": "腾讯的估值主要受游戏与广告增速影响；这一句是模型知识，不是本次检索到的数据。",
        "actions": [],
        "evidence_ids": [],
        "financial_fact_ids": [],
        "data_gaps": [],
    }
    encoded = json.dumps(output)

    def chunk(delta, finish=None):
        payload = {
            "id": "offline",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "deepseek-flash",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        if finish:
            payload["usage"] = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        return ("data: " + json.dumps(payload) + "\n\n").encode()

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield chunk({"role": "assistant", "content": ""})
            yield chunk({
                "tool_calls": [{
                    "index": 0,
                    "id": "call_final",
                    "type": "function",
                    "function": {"name": "final_result", "arguments": encoded},
                }]
            })
            yield chunk({}, "tool_calls")
            yield b"data: [DONE]\n\n"

        async def aclose(self):
            return None

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Stream())

    async def scenario():
        client = AsyncOpenAI(
            api_key="offline",
            base_url="https://offline.invalid",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        monkeypatch.setattr("api.agent_pydantic.AsyncOpenAI", lambda **kw: client)
        try:
            deps = AgentDeps(
                stock_service=SimpleNamespace(),
                news_service=SimpleNamespace(),
                hotspot_service=SimpleNamespace(),
                portfolio_service=SimpleNamespace(),
                web_search_service=SimpleNamespace(),
                language="zh-CN",
            )
            agent = create_agent(api_key="offline", model="deepseek-flash")
            return await run_agent_async(agent, deps, "腾讯的估值受什么影响", lambda *args: None)
        finally:
            await client.close()

    response = asyncio.run(scenario())
    assert response.payload["_meta"]["answer_basis"] == "model_knowledge"
    assert any("模型知识" in gap for gap in response.data_gaps)
    # The answer is delivered as a conclusion, not as a failure or a half-answer.
    assert classify_response(response) == ("completed", None)


def test_new_bot_never_inherits_an_older_conversation(bot_runtime, monkeypatch):
    """A conversation belongs to the Bot that started it, even if a new Bot's
    welcome/task call arrives with that session id."""
    store, _executor = bot_runtime
    use_owner(monkeypatch)
    default = store.ensure_default_bot(OWNER)
    old_session = "session-owned-by-default"
    store.ensure_bot_session(OWNER, default["id"], old_session)
    task, _ = store.create_task(
        owner_id=OWNER,
        query="贵州茅台今天涨停了吗",
        session_id=old_session,
        language="zh-CN",
        research_mode="quick",
        idempotency_key="old-session-task",
    )

    fresh = store.create_bot(OWNER, name="公司研究")
    # Binding is insert-only: the second Bot does not take the conversation over.
    bound = store.ensure_bot_session(OWNER, fresh["id"], old_session)
    assert bound["bot_id"] == default["id"]
    assert [row["id"] for row in store.list_sessions(OWNER, bot_id=fresh["id"])] == []

    welcome = client.post(
        f"/api/sessions/{old_session}/welcome",
        json={"session_id": old_session, "bot_id": fresh["id"], "language": "zh-CN"},
    )
    assert welcome.status_code == 409
    assert welcome.json()["detail"] == "session_belongs_to_another_bot"

    borrowed = client.post(
        "/api/tasks",
        json={"query": "继续问这个会话", "session_id": old_session, "bot_id": fresh["id"]},
        headers={"Idempotency-Key": "borrow-task"},
    )
    assert borrowed.status_code == 409
    assert borrowed.json()["detail"] == "session_belongs_to_another_bot"

    # The original owner still sees its history, and the new Bot sees none of it.
    assert [row["id"] for row in store.list_sessions(OWNER, bot_id=default["id"])] == [old_session]
    assert store.list_artifacts(OWNER, bot_id=fresh["id"]) == []
    assert store.get_task(OWNER, task["id"])["session_id"] == old_session


def test_bot_onboarding_uses_the_bot_own_instructions(bot_runtime, monkeypatch):
    """The opening is generated from the Bot's role, and falls back to null."""
    import asyncio

    from api.financial_bot import onboarding as onboarding_module
    from api.financial_bot.onboarding import BotOnboarding

    store, _executor = bot_runtime
    use_owner(monkeypatch)
    bot = store.create_bot(
        OWNER,
        name="半导体研究",
        instructions="只看半导体设备和材料的上市公司",
        prompt_config={
            "role": "半导体行业分析师",
            "markets": ["cn"],
            "output_style": "standard",
            "capabilities": {"web": True, "filings": True, "market": True, "news": False, "portfolio": False, "watchlist": False},
            "memory_enabled": True,
            "routines_enabled": True,
            "proactive_enabled": True,
        },
    )

    seen: dict[str, str] = {}

    async def fake_generate(self, bot_payload, *, language="zh-CN"):
        seen["instructions"] = str(bot_payload.get("instructions") or "")
        seen["capabilities"] = onboarding_module._capability_names(bot_payload)
        return {
            "greeting": "嗨，我是半导体研究，只盯半导体设备和材料。",
            "options": [
                {"label": "看一家设备公司", "ack": "好。", "templates": [{"label": "北方华创", "prompt": "分析北方华创 002371"}]},
                {"label": "只看材料环节", "ack": "好。", "templates": [{"label": "沪硅产业", "prompt": "分析沪硅产业 688126"}]},
            ],
        }

    monkeypatch.setattr(BotOnboarding, "generate", fake_generate)
    response = client.post(f"/api/bots/{bot['id']}/onboarding", json={"language": "zh-CN"})
    assert response.status_code == 200
    plan = response.json()["onboarding"]
    assert plan["options"][0]["label"] == "看一家设备公司"
    assert "半导体设备" in seen["instructions"]
    # A Bot without the portfolio capability is described without it.
    assert "持仓分析" not in seen["capabilities"]


def test_bot_onboarding_is_null_without_a_model(bot_runtime, monkeypatch):
    store, _executor = bot_runtime
    use_owner(monkeypatch)
    bot = store.ensure_default_bot(OWNER)
    import api.main as main
    monkeypatch.setattr(
        main.settings_store,
        "build_runtime_config",
        lambda config: SimpleNamespace(llm_api_key=None, llm_model="deepseek-flash", llm_base_url=None),
    )
    response = client.post(f"/api/bots/{bot['id']}/onboarding", json={"language": "zh-CN"})
    assert response.status_code == 200
    assert response.json()["onboarding"] is None


def test_structured_output_failure_is_retried_once_with_a_fresh_run(bot_runtime, monkeypatch):
    store, executor = bot_runtime
    use_owner(monkeypatch)
    calls = {"count": 0}

    async def runner(_owner, task, _progress):
        calls["count"] += 1
        if calls["count"] == 1:
            return AgentResponse(
                intent="pydantic_ai_agent",
                summary="第一遍只拿到正文。",
                data_gaps=["结构化输出校验失败；已保留可见回答。"],
                payload={"_meta": {"output_fallback": "text", "structured_output_validated": False}},
            )
        return AgentResponse(
            intent="pydantic_ai_agent",
            summary="## 结论\n重试后拿到了结构化回答。",
            payload={"_meta": {"structured_output_validated": True}},
        )

    executor.set_agent_runner(runner)
    task_id = client.post("/api/tasks", json={"query": "腾讯的广告收入怎么样"}).json()["task"]["id"]
    task = wait_terminal(store, task_id)
    assert calls["count"] == 2, "the fallback must trigger exactly one fresh retry"
    assert task["status"] == "completed"
    assert "重试后拿到了结构化回答" in (task["result"]["summary"] or "")


def test_findings_stream_as_their_own_notices(bot_runtime, monkeypatch):
    """Each piece of evidence becomes a notice event + message, not final text."""
    from types import SimpleNamespace

    store, executor = bot_runtime
    use_owner(monkeypatch)

    async def runner(_owner, task, progress):
        progress("tool_completed", 58, "估值已获取", {
            "tool": "get_valuations",
            "endpoint": "quantapi.51ifind.com",
            "finding": "估值：PE(TTM) 21.9 · PB 6.63 · 近5年分位 31.2%",
        })
        return AgentResponse(intent="pydantic_ai_agent", summary="结论", payload={"_meta": {}})

    executor.set_agent_runner(runner)
    task_id = client.post("/api/tasks", json={"query": "茅台贵吗", "session_id": "notice-session"}).json()["task"]["id"]
    wait_terminal(store, task_id)

    events = store.list_events(OWNER, task_id)
    notices = [event for event in events if event["kind"] == "notice"]
    assert notices and "PE(TTM) 21.9" in notices[0]["display_summary"]

    messages = store.list_messages(OWNER, "notice-session")
    notice_messages = [message for message in messages if message["kind"] == "notice"]
    assert notice_messages and "近5年分位 31.2%" in notice_messages[0]["content"]


def test_summary_keeps_notice_when_cited_source_has_no_link():
    from api.agent_pydantic import AgentOutput, build_agent_response
    source = {'id': 'ev_abc123', 'source': 'provider', 'url': None,
              'retrieved_at': '2026-11-04T00:00:00Z', 'data_as_of': '2026-06-30',
              'freshness': 'reported_period'}
    response = build_agent_response(AgentOutput(summary='收入已披露 [ev_abc123]。', evidence_ids=['ev_abc123']),
                                    [{'tool': 'get_company_fundamentals', 'data': {}, 'evidence': [source]}], 'zh-CN')
    assert '来源缺少可验证链接' in response.summary
    assert response.evidence[0].id == 'ev_abc123'


def test_unverified_statement_currency_is_visible_to_user():
    from api.agent_pydantic import AgentOutput, build_agent_response
    response = build_agent_response(AgentOutput(summary='财报币种尚未核实。'), [
        {'tool': 'get_company_fundamentals', 'data': {'currency': None, 'currency_basis': 'unverified', 'periods': []}, 'evidence': []}
    ], 'zh-CN')
    assert any('财报币种' in gap and '未核实' in gap for gap in response.data_gaps)


def test_price_monitor_notification_uses_readable_precision_and_chinese_direction(bot_runtime):
    from datetime import datetime, timezone
    store, _ = bot_runtime
    bot = store.ensure_default_bot(OWNER)
    monitor = store.create_monitor(OWNER, bot_id=bot['id'], name='腾讯测试', symbol='00700.HK',
        threshold=0.01, direction='both', interval_minutes=1, cooldown_minutes=120)
    store.record_monitor_check(OWNER, monitor['id'], checked_at=datetime.now(timezone.utc).isoformat(),
        price=395.20001220703125, change_pct=-0.5, data_as_of='2026-11-04', currency='HKD')
    body = store.list_notifications(OWNER, bot_id=bot['id'])[0]['body']
    assert '395.200012207' not in body
    assert '395.2 HKD' in body
    assert '双向' in body and '(both)' not in body
