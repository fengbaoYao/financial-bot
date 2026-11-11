import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

import api.main as main_module
from api.financial_bot.fallback_research import deterministic_price_research, find_company
from api.schemas import NewsItem


def bars(direction: float, days: int = 25):
    start = date(2026, 9, 1)
    close = 400.0
    rows = []
    for index in range(days):
        close = close * (1 + direction)
        rows.append(
            {
                "date": (start + timedelta(days=index)).isoformat(),
                "close": round(close, 2),
                "volume": 1_000_000 + index,
            }
        )
    return rows


def news(title: str, relation: str, day: str = "2026-09-20") -> NewsItem:
    return NewsItem(
        id=title,
        stock_code="00700.HK",
        stock_name="腾讯控股",
        source="example",
        published_at=f"{day}T09:00:00+08:00",
        title=title,
        summary="示例摘要",
        relation_type=relation,
        sentiment="neutral",
        impact_level=3 if relation == "direct" else 2,
        url="https://example.org/news",
    )


def install(monkeypatch, rows, items):
    analysis = SimpleNamespace(
        metadata={
            "provider": "yfinance(0700.HK)",
            "adjustment": "auto_adjust",
            "currency": "HKD",
            "data_as_of": rows[-1]["date"],
        },
        chart_series=rows,
    )
    monkeypatch.setattr(
        main_module.stock_service,
        "get_stock_analysis",
        lambda code, include_ai=True, **kwargs: analysis,
    )
    monkeypatch.setattr(
        main_module.news_service,
        "get_stock_news",
        lambda code, name=None, limit=20: items,
    )


def test_company_resolution_for_tencent_question():
    assert find_company("腾讯股票最近为什么暴跌？")[0] == "00700.HK"
    assert find_company("贵州茅台为什么下跌")[0] == "sh600519"
    assert find_company("AAPL为什么暴跌") is None or find_company("AAPL为什么暴跌")[0] == "US.AAPL"


def test_upward_prices_correct_the_crash_premise(monkeypatch):
    rows = bars(0.01)
    install(monkeypatch, rows, [news("腾讯控股回购股份", "direct")])
    response = asyncio.run(deterministic_price_research("腾讯股票最近为什么暴跌？", "zh-CN"))
    assert response is not None
    assert "前提需要修正" in response.summary
    assert "yfinance(0700.HK)" in response.summary
    assert "auto_adjust" in response.summary
    assert "未配置模型凭证" in response.summary
    assert response.payload["_meta"]["engine"] == "deterministic_source_only"


def test_downward_prices_do_not_invent_a_cause_and_topic_news_is_labeled(monkeypatch):
    rows = bars(-0.02)
    install(
        monkeypatch,
        rows,
        [
            news("腾讯控股宣布回购股份", "direct"),
            news("A股三大指数集体上涨", "topic"),
        ],
    )
    response = asyncio.run(deterministic_price_research("腾讯股票最近为什么暴跌？", "zh-CN"))
    assert response is not None
    assert "短期跌幅明显" in response.summary
    assert "不能证明下跌原因" in response.summary
    assert "公司关联未核实" in response.summary
    assert "A股三大指数集体上涨" in response.summary
    assert "腾讯控股宣布回购股份" in response.summary
    assert any(source.freshness == "topic_unverified" for source in response.evidence)
    assert any("因果" in gap for gap in response.data_gaps)


def test_no_company_returns_clarification_not_a_fabricated_answer(monkeypatch):
    response = asyncio.run(deterministic_price_research("这只股票最近为什么暴跌？", "zh-CN"))
    assert response is not None
    assert response.intent == "clarification"
    assert "没有识别出" in response.summary


def test_non_price_questions_are_not_intercepted(monkeypatch):
    assert asyncio.run(deterministic_price_research("对比腾讯和阿里巴巴", "zh-CN")) is None
