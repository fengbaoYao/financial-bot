from api.financial_bot.plan import deterministic_plan


def test_price_and_news_plan_includes_news_and_filings():
    plan = deterministic_plan("请分析腾讯控股（00700.HK）近三个月股价走势，结合新闻与公告说明原因")
    kinds = [step.kind for step in plan.steps]
    assert "market" in kinds
    assert "news" in kinds
    assert "filings" in kinds
    market = next(step for step in plan.steps if step.kind == "market")
    assert "get_benchmark_comparison" in market.tools
