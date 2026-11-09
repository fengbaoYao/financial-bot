from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from api.financial_bot.auth import OwnerContext
from api.financial_bot.db import Database
from api.financial_bot.executor import TaskExecutor
from api.financial_bot.plan import (
    PlanStep,
    TaskPlan,
    deterministic_plan,
    evaluate_plan,
    format_plan_context,
    plan_gaps,
    should_plan_query,
    should_use_llm_planner,
)
from api.financial_bot.runtime import configure, reset
from api.financial_bot.store import FinancialBotStore
import api.financial_bot.routes as routes
from api.main import app
from fastapi.testclient import TestClient

OWNER = "33333333-3333-4333-8333-333333333333"


def response(*steps, summary="已完成的回答。"):
    return SimpleNamespace(
        summary=summary,
        research_steps=[
            SimpleNamespace(
                tool=tool,
                status=status,
                evidence_count=evidence,
            )
            for tool, status, evidence in steps
        ],
        data_gaps=[],
        payload={"_meta": {}},
    )


def test_price_plan_requires_market_evidence_and_synthesis():
    plan = deterministic_plan("腾讯股价走势怎么样", plan_id="task_1")
    assert [step.kind for step in plan.steps] == ["resolve", "market", "synthesis"]
    evaluated = evaluate_plan(
        plan,
        response(
            ("search_stocks", "completed", 0),
            ("get_stock_analysis", "completed", 2),
        ),
    )
    assert evaluated.status == "completed"
    assert all(step.status == "completed" for step in evaluated.steps)
    assert plan_gaps(evaluated) == []


def test_missing_tool_evidence_prevents_false_completion():
    plan = deterministic_plan("腾讯2025年财报和营收", plan_id="task_2")
    assert any(step.kind == "filings" for step in plan.steps)
    evaluated = evaluate_plan(
        plan,
        response(
            ("search_stocks", "completed", 0),
            ("get_stock_news", "completed", 1),
        ),
    )
    assert evaluated.status == "partial"
    filings = next(step for step in evaluated.steps if step.kind == "filings")
    assert filings.status in {"retry", "failed"}
    assert filings.last_error
    gaps = plan_gaps(evaluated)
    assert gaps and "任务清单未完成" in gaps[0]
    assert "任务清单" in format_plan_context(evaluated)


def test_synthesis_alone_cannot_complete_tool_steps():
    plan = deterministic_plan("AAPL earnings", plan_id="task_3")
    evaluated = evaluate_plan(plan, response(summary="good story"))
    assert evaluated.status != "completed"
    assert evaluated.steps[-1].status == "completed"


def test_plan_store_round_trip(tmp_path):
    db = Database(sqlite_path=tmp_path / "plans.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    configure(store=store, executor=TaskExecutor(store))
    try:
        plan = deterministic_plan("腾讯股价", plan_id="task_pg")
        saved = store.save_plan(
            OWNER,
            "task_pg",
            bot_id=None,
            session_id=None,
            original_query=plan.original_query,
            status="running",
            source=plan.source,
            steps=plan.steps,
            termination_condition=plan.termination_condition,
        )
        assert saved["status"] == "running"
        assert len(saved["steps"]) == len(plan.steps)
        loaded = store.get_plan(OWNER, "task_pg")
        assert loaded is not None
        assert loaded["steps"][0]["step_id"] == "s1"
    finally:
        reset()


def test_planner_is_reserved_for_complex_or_multi_step_requests():
    assert should_plan_query("你好") is False
    assert should_plan_query("腾讯股价") is True
    assert should_use_llm_planner("腾讯股价") is False
    assert should_use_llm_planner("获取银行板块财报，计算PE/PB分位数，生成对比表格并导出csv") is True


def test_llm_normalized_plan_rejects_unknown_tools():
    from api.financial_bot.plan import PlanOutput, normalize_llm_plan

    output = PlanOutput(
        steps=[
            {
                "step_id": "s1",
                "title": "导出 CSV",
                "kind": "synthesis",
                "tools": ["shell_exec"],
                "depends_on": [],
                "validation": {"kind": "tool_success", "tools": ["shell_exec"], "min_evidence": 0, "description": "x"},
                "retry_max": 1,
            }
        ]
    )
    plan = normalize_llm_plan(output, query="导出 CSV", plan_id="task_4")
    assert all(tool not in {"shell_exec"} for step in plan.steps for tool in step.tools)


def test_executor_gate_downgrades_completed_answer_with_incomplete_plan(tmp_path, monkeypatch):
    db = Database(sqlite_path=tmp_path / "gate.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    executor = TaskExecutor(store)
    configure(store=store, executor=executor)
    try:
        task, _ = store.create_task(
            owner_id=OWNER,
            query="腾讯股价走势",
            session_id=None,
            language="zh-CN",
            research_mode="quick",
            idempotency_key="gate-1",
        )
        plan = deterministic_plan("腾讯股价走势", plan_id=task["id"])
        executor._plans[task["id"]] = plan

        async def fake_runner(_owner, _task, _progress):
            return SimpleNamespace(
                intent="pydantic_ai_agent",
                summary="已经拿到腾讯标的。",
                actions=[],
                citations=[],
                clarification=None,
                payload={"_meta": {}},
                research_steps=[SimpleNamespace(tool="search_stocks", status="completed", reason_code=None, endpoint=None, evidence_count=0, retrieved_at=None)],
                evidence=[],
                data_gaps=[],
            )

        executor.set_agent_runner(fake_runner)
        asyncio.run(executor._execute(OwnerContext(owner_id=OWNER, mode="local_single_user"), task["id"]))
        after = store.get_task(OWNER, task["id"])
        saved = store.get_plan(OWNER, task["id"])
        assert after is not None
        assert after["status"] == "partial"
        assert saved is not None
        assert saved["status"] == "partial"
        assert any(step["status"] in {"retry", "failed"} for step in saved["steps"])
        assert any("任务清单未完成" in gap for gap in after["result"]["data_gaps"])
    finally:
        reset()


def test_plan_endpoint_is_owner_scoped(tmp_path, monkeypatch):
    db = Database(sqlite_path=tmp_path / "plan-api.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    configure(store=store, executor=TaskExecutor(store))
    monkeypatch.setattr(
        routes,
        "resolve_owner",
        lambda request, config: OwnerContext(owner_id=OWNER, mode="supabase_authenticated"),
    )
    try:
        task, _ = store.create_task(
            owner_id=OWNER,
            query="腾讯股价",
            session_id=None,
            language="zh-CN",
            research_mode="quick",
            idempotency_key="plan-api-1",
        )
        plan = deterministic_plan("腾讯股价", plan_id=task["id"])
        store.save_plan(
            OWNER,
            task["id"],
            bot_id=None,
            session_id=None,
            original_query=plan.original_query,
            status="running",
            source=plan.source,
            steps=plan.steps,
            termination_condition=plan.termination_condition,
        )
        response = TestClient(app).get(f"/api/tasks/{task['id']}/plan")
        assert response.status_code == 200
        assert response.json()["plan"]["steps"][0]["step_id"] == "s1"
        missing = TestClient(app).get("/api/tasks/task_missing/plan")
        assert missing.status_code == 404
    finally:
        reset()


def test_prepare_plan_persists_before_agent_runs(tmp_path):
    db = Database(sqlite_path=tmp_path / "prepare.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    executor = TaskExecutor(store)
    configure(store=store, executor=executor)
    try:
        bot = store.ensure_default_bot(OWNER)
        task, _ = store.create_task(
            owner_id=OWNER,
            query="腾讯股价",
            session_id=None,
            language="zh-CN",
            research_mode="quick",
            idempotency_key="prepare-1",
        )
        plan = asyncio.run(executor._prepare_plan(
            OwnerContext(owner_id=OWNER, mode="local_single_user"), task, bot, True
        ))
        assert plan is not None
        assert plan.source == "template"
        saved = store.get_plan(OWNER, task["id"])
        assert saved is not None
        assert saved["status"] == "running"
        assert saved["steps"][0]["status"] == "pending"
    finally:
        reset()


def test_restart_recovery_resets_plan_and_marks_resumable(tmp_path):
    db = Database(sqlite_path=tmp_path / "resume.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    configure(store=store, executor=TaskExecutor(store))
    try:
        task, _ = store.create_task(
            owner_id=OWNER,
            query="腾讯股价",
            session_id=None,
            language="zh-CN",
            research_mode="quick",
            idempotency_key="resume-recovery",
        )
        store.transition(OWNER, task["id"], "running")
        plan = deterministic_plan("腾讯股价", plan_id=task["id"])
        plan.steps[0].status = "completed"
        plan.steps[1].status = "running"
        plan.status = "running"
        store.save_plan(
            OWNER,
            task["id"],
            bot_id=None,
            session_id=None,
            original_query=plan.original_query,
            status=plan.status,
            source=plan.source,
            steps=plan.steps,
            termination_condition=plan.termination_condition,
        )
        assert store.recover_interrupted_tasks() == 1
        recovered = store.get_task(OWNER, task["id"])
        saved = store.get_plan(OWNER, task["id"])
        assert recovered["status"] == "queued"
        assert recovered["status_reason"] == "resume_pending"
        assert saved["status"] == "partial"
        assert saved["steps"][0]["status"] == "completed"
        assert saved["steps"][1]["status"] == "pending"
        assert [row["id"] for row in store.list_resumable_tasks()] == [task["id"]]
    finally:
        reset()


def test_resume_task_resets_failed_step_attempts(tmp_path):
    db = Database(sqlite_path=tmp_path / "resume2.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    configure(store=store, executor=TaskExecutor(store))
    try:
        task, _ = store.create_task(
            owner_id=OWNER,
            query="腾讯股价",
            session_id=None,
            language="zh-CN",
            research_mode="quick",
            idempotency_key="resume-2",
        )
        plan = deterministic_plan("腾讯股价", plan_id=task["id"])
        plan.status = "partial"
        plan.steps[1].status = "failed"
        plan.steps[1].attempts = 2
        plan.steps[1].last_error = "no data"
        store.save_plan(
            OWNER,
            task["id"],
            bot_id=None,
            session_id=None,
            original_query=plan.original_query,
            status="partial",
            source=plan.source,
            steps=plan.steps,
            termination_condition=plan.termination_condition,
        )
        store.transition(OWNER, task["id"], "running")
        store.transition(OWNER, task["id"], "partial", reason="plan_incomplete")
        resumed_task, resumed_plan = store.resume_task(OWNER, task["id"])
        assert resumed_task["status"] == "queued"
        assert resumed_plan["steps"][1]["status"] == "pending"
        assert resumed_plan["steps"][1]["attempts"] == 0
    finally:
        reset()


def test_executor_resume_reuses_saved_plan_instead_of_replanning(tmp_path):
    db = Database(sqlite_path=tmp_path / "resume3.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    executor = TaskExecutor(store)
    configure(store=store, executor=executor)
    try:
        bot = store.ensure_default_bot(OWNER)
        task, _ = store.create_task(
            owner_id=OWNER,
            query="腾讯股价",
            session_id=None,
            language="zh-CN",
            research_mode="quick",
            idempotency_key="resume-3",
        )
        plan = deterministic_plan("腾讯股价", plan_id=task["id"])
        plan.steps[0].status = "completed"
        plan.status = "partial"
        store.save_plan(
            OWNER,
            task["id"],
            bot_id=bot["id"],
            session_id=None,
            original_query=plan.original_query,
            status="partial",
            source=plan.source,
            steps=plan.steps,
            termination_condition=plan.termination_condition,
        )
        resumed = asyncio.run(executor._prepare_plan(
            OwnerContext(owner_id=OWNER, mode="local_single_user"), task, bot, True, resume=True
        ))
        assert resumed is not None
        assert resumed.source == "template"
        assert resumed.steps[0].status == "completed"
        assert resumed.status == "running"
        assert store.get_plan(OWNER, task["id"])["status"] == "running"
    finally:
        reset()


def test_resume_endpoint_starts_executor_with_resume(tmp_path, monkeypatch):
    db = Database(sqlite_path=tmp_path / "resume-api.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    executor = TaskExecutor(store)
    configure(store=store, executor=executor)
    monkeypatch.setattr(
        routes,
        "resolve_owner",
        lambda request, config: OwnerContext(owner_id=OWNER, mode="supabase_authenticated"),
    )
    started = []

    class FakeExecutor:
        def start(self, owner, task_id, *, resume=False):
            started.append((task_id, resume))

    monkeypatch.setattr(routes, "_executor", lambda: FakeExecutor())
    try:
        task, _ = store.create_task(
            owner_id=OWNER,
            query="腾讯股价",
            session_id=None,
            language="zh-CN",
            research_mode="quick",
            idempotency_key="resume-api-1",
        )
        plan = deterministic_plan("腾讯股价", plan_id=task["id"])
        plan.status = "partial"
        plan.steps[1].status = "failed"
        store.save_plan(
            OWNER,
            task["id"],
            bot_id=None,
            session_id=None,
            original_query=plan.original_query,
            status="partial",
            source=plan.source,
            steps=plan.steps,
            termination_condition=plan.termination_condition,
        )
        store.transition(OWNER, task["id"], "running")
        store.transition(OWNER, task["id"], "partial", reason="plan_incomplete")
        response = TestClient(app).post(f"/api/tasks/{task['id']}/resume")
        assert response.status_code == 202
        assert response.json()["task"]["status"] == "queued"
        assert started == [(task["id"], True)]
    finally:
        reset()


def test_runtime_initializes_local_resume_for_resumable_plan(tmp_path):
    from api.financial_bot import runtime

    db = Database(sqlite_path=tmp_path / "resume-runtime.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    configured = []
    fake_executor = SimpleNamespace(start=lambda owner, task_id, resume=False: configured.append((task_id, resume)))
    configure(store=store, executor=fake_executor)
    try:
        task, _ = store.create_task(
            owner_id=OWNER,
            query="腾讯股价",
            session_id=None,
            language="zh-CN",
            research_mode="quick",
            idempotency_key="resume-runtime",
        )
        store.transition(OWNER, task["id"], "running")
        plan = deterministic_plan("腾讯股价", plan_id=task["id"])
        store.save_plan(
            OWNER,
            task["id"],
            bot_id=None,
            session_id=None,
            original_query=plan.original_query,
            status="running",
            source=plan.source,
            steps=plan.steps,
            termination_condition=plan.termination_condition,
        )
        runtime.initialize()
        assert configured == []
        # The test owner is a Supabase-style owner, so auto-resume must not
        # invent a user identity; the task remains queued for manual resume.
        assert store.get_task(OWNER, task["id"])["status"] == "queued"
        assert store.get_task(OWNER, task["id"])["status_reason"] == "resume_pending"
    finally:
        reset()


def test_runtime_auto_resumes_local_owner(tmp_path):
    from api.financial_bot import runtime
    from api.financial_bot.auth import LOCAL_OWNER_ID

    db = Database(sqlite_path=tmp_path / "resume-local.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    configured = []
    fake_executor = SimpleNamespace(start=lambda owner, task_id, resume=False: configured.append((task_id, resume)))
    configure(store=store, executor=fake_executor)
    try:
        task, _ = store.create_task(
            owner_id=LOCAL_OWNER_ID,
            query="腾讯股价",
            session_id=None,
            language="zh-CN",
            research_mode="quick",
            idempotency_key="resume-local",
        )
        store.transition(LOCAL_OWNER_ID, task["id"], "running")
        plan = deterministic_plan("腾讯股价", plan_id=task["id"])
        store.save_plan(
            LOCAL_OWNER_ID,
            task["id"],
            bot_id=None,
            session_id=None,
            original_query=plan.original_query,
            status="running",
            source=plan.source,
            steps=plan.steps,
            termination_condition=plan.termination_condition,
        )
        runtime.initialize()
        assert configured == [(task["id"], True)]
    finally:
        reset()


def test_executor_auto_retries_incomplete_plan_once(tmp_path):
    db = Database(sqlite_path=tmp_path / "auto-retry.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    executor = TaskExecutor(store)
    configure(store=store, executor=executor)
    try:
        task, _ = store.create_task(
            owner_id=OWNER,
            query="腾讯股价走势",
            session_id=None,
            language="zh-CN",
            research_mode="quick",
            idempotency_key="auto-retry-1",
        )
        executor._plans[task["id"]] = deterministic_plan("腾讯股价走势", plan_id=task["id"])
        calls = []

        def response(tools, summary):
            return SimpleNamespace(
                intent="pydantic_ai_agent", summary=summary, actions=[], citations=[],
                clarification=None, payload={"_meta": {}}, evidence=[], data_gaps=[],
                research_steps=[SimpleNamespace(
                    tool=tool, status="completed", reason_code=None, endpoint=None,
                    evidence_count=1, retrieved_at=None,
                ) for tool in tools],
            )

        async def fake_run(_owner, _task, *, resume=False):
            calls.append(resume)
            if len(calls) == 1:
                return response(["search_stocks"], "先确认了标的。")
            executor._plans[_task["id"]] = deterministic_plan("腾讯股价走势", plan_id=_task["id"])
            return response(["search_stocks", "get_stock_analysis"], "标的和行情都已核验。")

        executor._run_agent = fake_run
        asyncio.run(executor._execute(OwnerContext(owner_id=OWNER, mode="local_single_user"), task["id"]))
        after = store.get_task(OWNER, task["id"])
        saved = store.get_plan(OWNER, task["id"])
        assert calls == [False, True]
        assert after["status"] == "completed"
        assert saved["status"] == "completed"
    finally:
        reset()


def test_executor_retries_a_direct_agent_exception_once(tmp_path):
    db = Database(sqlite_path=tmp_path / "exception-retry.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    executor = TaskExecutor(store)
    configure(store=store, executor=executor)
    try:
        task, _ = store.create_task(
            owner_id=OWNER,
            query="腾讯股价走势",
            session_id=None,
            language="zh-CN",
            research_mode="quick",
            idempotency_key="exception-retry-1",
        )
        calls = []

        async def fake_run(_owner, _task, *, resume=False):
            calls.append(resume)
            if len(calls) == 1:
                raise RuntimeError("transient model failure")
            return SimpleNamespace(
                intent="pydantic_ai_agent", summary="重试成功。", actions=[], citations=[],
                clarification=None, payload={"_meta": {}}, research_steps=[], evidence=[], data_gaps=[],
            )

        executor._run_agent = fake_run
        asyncio.run(executor._execute(OwnerContext(owner_id=OWNER, mode="local_single_user"), task["id"]))
        after = store.get_task(OWNER, task["id"])
        assert calls == [False, True]
        assert after["status"] == "completed"
    finally:
        reset()


def test_executor_aggregates_usage_across_plan_retry(tmp_path):
    db = Database(sqlite_path=tmp_path / "usage-retry.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    executor = TaskExecutor(store)
    configure(store=store, executor=executor)
    try:
        task, _ = store.create_task(
            owner_id=OWNER,
            query="腾讯股价走势",
            session_id=None,
            language="zh-CN",
            research_mode="quick",
            idempotency_key="usage-retry-1",
        )
        executor._plans[task["id"]] = deterministic_plan("腾讯股价走势", plan_id=task["id"])
        calls = []

        def response(tools, summary, usage):
            return SimpleNamespace(
                intent="pydantic_ai_agent", summary=summary, actions=[], citations=[],
                clarification=None, payload={"_meta": {"usage": usage, "estimated_cost_usd": usage.get("cost_usd", 0)}},
                evidence=[], data_gaps=[], research_steps=[SimpleNamespace(
                    tool=tool, status="completed", reason_code=None, endpoint=None,
                    evidence_count=1, retrieved_at=None,
                ) for tool in tools],
            )

        async def fake_run(_owner, _task, *, resume=False):
            calls.append(resume)
            if len(calls) == 1:
                return response(["search_stocks"], "先确认标的。", {"input_tokens": 10, "output_tokens": 20, "cost_usd": 0.01})
            executor._plans[_task["id"]] = deterministic_plan("腾讯股价走势", plan_id=_task["id"])
            return response(["search_stocks", "get_stock_analysis"], "补齐。", {"input_tokens": 30, "output_tokens": 40, "cost_usd": 0.02})

        executor._run_agent = fake_run
        asyncio.run(executor._execute(OwnerContext(owner_id=OWNER, mode="local_single_user"), task["id"]))
        after = store.get_task(OWNER, task["id"])
        assert calls == [False, True]
        assert after["input_tokens"] == 40
        assert after["output_tokens"] == 60
        assert after["cost_usd"] == pytest.approx(0.03)
    finally:
        reset()
