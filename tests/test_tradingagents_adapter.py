"""TradingAgents' real research roles use only task-scoped supplied evidence."""
import pytest

from api.financial_bot.tradingagents_adapter import build_research_prompt


def test_bull_and_bear_prompts_use_upstream_roles_and_only_given_evidence():
    evidence = '[{"id":"source-1","text":"Revenue increased"}]'
    bull = build_research_prompt("bull", "AAPL", "What are the risks?", evidence)
    bear = build_research_prompt("bear", "AAPL", "What are the risks?", evidence, prior_argument="The bull sees growth [source-1]")
    assert "Bull Analyst" in bull and "Bear Analyst" in bear
    assert "source-1" in bull and "source-1" in bear
    assert "The bull sees growth" in bear
    assert "What are the risks?" in bull and "What are the risks?" in bear
    assert "untrusted" in bull.lower() and "untrusted" in bear.lower()


def test_unknown_role_rejected():
    with pytest.raises(ValueError):
        build_research_prompt("trader", "AAPL", "question", "evidence")


def test_risk_and_manager_are_evidence_focused_without_forced_transactions():
    risk = build_research_prompt("risk", "AAPL", "Assess risk", '[{"id":"ev_1"}]', 'Bull and bear disagree [ev_1]')
    manager = build_research_prompt("manager", "AAPL", "Assess risk", '[{"id":"ev_1"}]', 'Evidence is limited [ev_1]')
    assert "Conservative Risk Analyst" in risk
    assert "Research Manager" in manager
    for prompt in (risk, manager):
        assert "ev_1" in prompt
        assert "Assess risk" in prompt
        assert "Do not recommend" in prompt
        assert "stop-loss" not in prompt.lower()


def test_graph_executes_roles_in_order_and_passes_actual_results():
    import asyncio
    from api.financial_bot.tradingagents_adapter import run_research_flow

    async def run():
        seen = []
        async def review(role, prompt):
            seen.append((role, prompt))
            return {"summary": f"{role} reviewed evidence [ev_1]"}
        result = await run_research_flow("AAPL", "What risks?", '[{"id":"ev_1"}]', review)
        assert [role for role, _ in seen] == ["bull", "bear", "risk", "manager"]
        assert "bull reviewed evidence" in seen[1][1]
        assert "bear reviewed evidence" in seen[2][1]
        assert "risk reviewed evidence" in seen[3][1]
        assert [item["role"] for item in result] == ["bull", "bear", "risk", "manager"]
    asyncio.run(run())


def test_cancellation_stops_graph_before_later_roles():
    import asyncio
    from api.financial_bot.tradingagents_adapter import run_research_flow

    async def run():
        started = asyncio.Event()
        seen = []
        async def review(role, prompt):
            seen.append(role)
            started.set()
            await asyncio.Event().wait()
        task = asyncio.create_task(run_research_flow("AAPL", "risk", "evidence", review))
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert seen == ["bull"]
    asyncio.run(run())


def test_failed_role_is_not_presented_as_a_completed_review():
    import asyncio
    from api.financial_bot.tradingagents_adapter import run_research_flow

    async def run():
        async def review(role, prompt):
            return None if role == "bear" else {"summary": f"{role} result"}
        result = await run_research_flow("AAPL", "risk", "evidence", review)
        assert [item["role"] for item in result] == ["bull", "bear", "risk", "manager"]
        assert next(item for item in result if item["role"] == "bear")["status"] == "unavailable"
    asyncio.run(run())
