from api.financial_bot.work_research import (
    build_work_research_guidance,
    evaluate_work_research,
    should_replace_with_factual_fallback,
)


def test_work_plan_only_for_causal_questions_and_not_bot():
    assert build_work_research_guidance("腾讯股价") == ""
    assert build_work_research_guidance("为什么腾讯股价下跌？", bot_prompt="bot persona") == ""
    assert "get_benchmark_comparison" in build_work_research_guidance("为什么腾讯股价下跌？")


def test_stability_question_gets_an_explicit_answer_plan():
    guidance = build_work_research_guidance("帮我看下最近茅台为什么一直比较稳定，没有涨跌")
    assert "先核对‘稳定/没有涨跌’是否符合已取得的区间数据" in guidance
    assert "回答任务" in guidance


def test_missing_required_evidence_is_partial_and_explained():
    result = evaluate_work_research("为什么腾讯股价下跌？", [
        {"tool": "search_stocks", "data": [{"name": "腾讯控股", "code": "HK.00700"}]},
        {"tool": "get_stock_analysis", "data": {"stock_code": "HK.00700", "quote": {"change_pct": -2.1}}},
        {"tool": "get_benchmark_comparison", "status": "unavailable"},
        {"tool": "get_stock_news", "data": [{"title": "蓝色光标上涨", "relation_type": "topic"}]},
    ])
    assert result["status"] == "partial"
    assert "基准" in " ".join(result["data_gaps"])
    assert "直接关联" in " ".join(result["data_gaps"])


def test_relevant_company_news_and_complete_market_steps_pass():
    result = evaluate_work_research("为什么腾讯股价下跌？", [
        {"tool": "search_stocks", "data": [{"name": "腾讯控股", "code": "HK.00700"}]},
        {"tool": "get_stock_analysis", "data": {"stock_code": "HK.00700", "quote": {"change_pct": -2.1}}},
        {"tool": "get_benchmark_comparison", "data": {"benchmark": "HSI", "data": [1]}},
        {"tool": "get_stock_news", "data": [{"title": "腾讯控股业绩不及预期", "published_at": "2026-11-12T09:00:00+08:00", "relation_type": "direct", "sentiment": "bearish", "impact_level": 3}]},
    ])
    assert result["status"] == "completed"
    assert result["data_gaps"] == []


def test_missing_catalyst_does_not_replace_an_existing_synthesis():
    from types import SimpleNamespace

    completed_analysis = SimpleNamespace(
        intent="pydantic_ai_agent",
        summary="近20日上涨，短期回落；现有证据不能确认具体催化因素。",
        payload={"_meta": {"synthesis_incomplete": False}},
    )
    stalled = SimpleNamespace(
        intent="error",
        summary="## 研究综合未完成\n没有形成结论。",
        payload={"_meta": {"synthesis_incomplete": True}},
    )
    research_status = evaluate_work_research(
        "帮我看下最近茅台为什么一直比较稳定，没有涨跌",
        [
            {"tool": "search_stocks", "data": [{"name": "贵州茅台", "code": "SH.600519"}]},
            {"tool": "get_stock_analysis", "data": {"chart_series": [{"date": "2026-11-11", "close": 1409.4}]}},
            {"tool": "get_benchmark_comparison", "data": {"benchmark": "上证指数", "windows": [{"sessions": 20, "relative_pct": 11.34}]}},
            {"tool": "get_stock_news", "data": [{"title": "贵州茅台季度报告", "published_at": "2026-11-02", "relation_type": "direct"}]},
        ],
    )
    assert research_status["status"] == "partial"
    assert should_replace_with_factual_fallback(
        completed_analysis, "帮我看下最近茅台为什么一直比较稳定，没有涨跌"
    ) is False
    assert should_replace_with_factual_fallback(
        completed_analysis, "为什么腾讯股价下跌？"
    ) is True
    assert should_replace_with_factual_fallback(stalled) is True


def test_old_direct_news_does_not_count_as_cause_for_recent_move():
    result = evaluate_work_research("腾讯控股近期为什么下跌？", [
        {"tool": "search_stocks", "data": [{"name": "腾讯控股", "code": "HK.00700"}]},
        {"tool": "get_stock_analysis", "data": {"stock_code": "HK.00700", "quote": {"change_pct": -2.1}}},
        {"tool": "get_benchmark_comparison", "data": {"benchmark": "HSI", "data": [1]}},
        {"tool": "get_stock_news", "data": [{"title": "腾讯控股中性公告", "published_at": "2026-10-22", "relation_type": "direct"}]},
        {"tool": "web_search", "data": [{"title": "腾讯分析"}], "evidence": [{"title": "腾讯分析"}]},
    ])
    assert result["status"] == "partial"
    assert any("讨论时段对齐" in gap for gap in result["data_gaps"])


def test_recent_neutral_or_opposite_direction_news_is_not_a_catalyst():
    base = [
        {"tool": "search_stocks", "data": [{"name": "腾讯控股", "code": "HK.00700"}]},
        {"tool": "get_stock_analysis", "data": {"stock_code": "HK.00700", "quote": {"change_pct": -2.1}}},
        {"tool": "get_benchmark_comparison", "data": {"benchmark": "HSI", "data": [1]}},
    ]
    for news in [
        {"title": "腾讯控股发布月报", "published_at": "2026-11-12", "relation_type": "direct", "sentiment": "neutral", "impact_level": 3},
        {"title": "腾讯控股回购", "published_at": "2026-11-12", "relation_type": "direct", "sentiment": "bullish", "impact_level": 3},
        {"title": "腾讯控股业务调整", "published_at": "2026-11-12", "relation_type": "direct", "sentiment": "bearish", "impact_level": 1},
    ]:
        result = evaluate_work_research("腾讯近期为什么下跌？", [*base, {"tool": "get_stock_news", "data": [news]}])
        assert result["status"] == "partial"


def test_explicit_period_controls_news_window():
    base = [
        {"tool": "search_stocks", "data": [{"name": "腾讯控股", "code": "HK.00700"}]},
        {"tool": "get_stock_analysis", "data": {"stock_code": "HK.00700", "quote": {"change_pct": -2.1}}},
        {"tool": "get_benchmark_comparison", "data": {"benchmark": "HSI", "data": [1]}},
        {"tool": "get_stock_news", "data": [{"title": "腾讯业绩不及预期", "published_at": "2026-10-22", "relation_type": "direct", "sentiment": "bearish", "impact_level": 3}]},
    ]
    assert evaluate_work_research("腾讯近期为什么下跌？", base)["status"] == "partial"
    assert evaluate_work_research("腾讯过去30天为什么下跌？", base)["status"] == "completed"


def test_partial_causal_fallback_includes_measured_stock_returns():
    from types import SimpleNamespace

    from api.agent_pydantic import AgentDeps, evidence_fallback

    deps = AgentDeps(*[SimpleNamespace() for _ in range(5)], language="zh-CN", user_query="为什么腾讯股价下跌？")
    deps.tool_results = [
        {"tool": "search_stocks", "data": [{"name": "腾讯控股", "code": "HK.00700"}], "evidence": []},
        {"tool": "get_stock_analysis", "data": {
            "stock_name": "腾讯控股", "stock_code": "HK.00700", "market": "hk",
            "quote": {"current_price": 90, "change": -3, "change_pct": -1.46}, "metadata": {"currency": "HKD", "data_as_of": "2026-11-12"},
            "chart_series": [
                {"date": "2026-11-05", "close": 100, "volume": 100},
                {"date": "2026-11-06", "close": 99, "volume": 100},
                {"date": "2026-11-07", "close": 98, "volume": 100},
                {"date": "2026-11-10", "close": 97, "volume": 100},
                {"date": "2026-11-11", "close": 95, "volume": 100},
                {"date": "2026-11-12", "close": 90, "volume": 100},
            ],
        }, "evidence": [{"id": "ev_quote", "source": "iFinD", "retrieved_at": "2026-11-12T00:00:00Z", "data_as_of": "2026-11-12"}]},
    ]
    response = evidence_fallback(deps, "涨跌原因尚未确认")
    assert "当日涨跌 -3.00（-1.46%）" in response.summary
    assert "5日 -10.0%" in response.summary


def test_recovery_synthesizes_sensitive_queries_from_the_existing_plan_and_evidence():
    import asyncio
    from types import SimpleNamespace

    from api.agent_pydantic import AgentDeps, AgentOutput, run_recovery_async
    from api.financial_bot.plan import deterministic_plan

    for query in ("腾讯今天发布了什么公告", "腾讯最新报价是多少"):
        deps = AgentDeps(*[SimpleNamespace() for _ in range(5)], language="zh-CN", user_query=query)
        deps.research_plan = deterministic_plan(query, plan_id="plan-test")
        seen = []

        class RecoveryAgent:
            async def run(self, prompt, **_kwargs):
                seen.append(prompt)
                return SimpleNamespace(output=AgentOutput(summary="已按已检索的行情/公告证据给出结论。"))

        response = asyncio.run(run_recovery_async(RecoveryAgent(), deps, query))
        assert seen and "禁止使用模型记忆补充当前行情、公告、新闻或事件事实" in seen[0]
        assert "最终回答目标" in seen[0]
        assert response.payload["_meta"]["evidence_synthesis_recovery"] is True
        assert "llm_data_gap_fill" not in response.payload["_meta"]
        assert not any("模型知识补齐" in gap for gap in response.data_gaps)


def test_work_plan_exposes_only_current_step_tools_and_advances_after_each_result():
    from types import SimpleNamespace

    from api.agent_pydantic import AgentDeps, refresh_work_plan_progress, work_plan_tools_for_request
    from api.financial_bot.plan import PlanStep, TaskPlan, ValidationRule

    deps = AgentDeps(*[SimpleNamespace() for _ in range(5)], user_query="茅台走势和投资价值")
    deps.plan_before_research = True
    deps.research_plan = TaskPlan(
        task_id="plan-test", original_query=deps.user_query,
        answer_intent="评估走势和投资价值", answer_objective="结合数据给出结论",
        steps=[
            PlanStep(step_id="s1", title="确认标的", kind="resolve", tools=["search_stocks"],
                     validation=ValidationRule(kind="tool_success", tools=["search_stocks"])),
            PlanStep(step_id="s2", title="核验走势", kind="market",
                     tools=["get_stock_analysis", "get_benchmark_comparison", "ifind_smart_query"],
                     validation=ValidationRule(kind="evidence", min_evidence=1)),
            PlanStep(step_id="s3", title="综合回答", kind="synthesis", tools=[],
                     validation=ValidationRule(kind="summary")),
        ],
    )
    available = {"search_stocks", "get_stock_analysis", "get_benchmark_comparison", "ifind_smart_query", "web_search"}

    assert work_plan_tools_for_request(deps, available) == {"search_stocks"}
    refresh_work_plan_progress(deps)
    assert deps.research_plan.steps[0].status == "running"
    deps.tool_results.append({"tool": "search_stocks", "status": "completed"})
    refresh_work_plan_progress(deps)
    assert deps.research_plan.steps[0].status == "completed"
    assert deps.research_plan.steps[1].status == "running"
    assert work_plan_tools_for_request(deps, available) == {
        "get_stock_analysis", "get_benchmark_comparison"
    }
    deps.tool_results.extend([
        {"tool": "get_stock_analysis", "status": "completed"},
        {"tool": "get_benchmark_comparison", "status": "unavailable"},
    ])
    refresh_work_plan_progress(deps)
    assert deps.research_plan.steps[1].status == "failed"
    assert deps.research_plan.steps[2].status == "running"
    assert work_plan_tools_for_request(deps, available) == set()
