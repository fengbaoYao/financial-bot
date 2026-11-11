from api.agent_pydantic import AgentOutput, build_agent_response


def stock(code: str, name: str, price: float, market: str = "hk") -> dict:
    return {
        "tool": "get_stock_analysis",
        "status": "ok",
        "data": {
            "stock_code": code,
            "stock_name": name,
            "market": market,
            "quote": {"stock_code": code, "current_price": price},
            "technical_indicators": {"RSI": 30.0},
            "signal_summary": {"overall_score": 3, "overall_signal": "中性"},
            "technical_commentary": [],
            "ai_insight": {"enabled": False},
            "chart_series": [],
            "metadata": {"provider": "yfinance", "currency": "HKD"},
        },
    }


def news(code: str, name: str, title: str) -> dict:
    return {
        "tool": "get_stock_news",
        "status": "ok",
        "data": [
            {
                "id": f"{code}-news",
                "stock_code": code,
                "stock_name": name,
                "title": title,
                "summary": "fixture",
                "source": "example",
                "published_at": "2026-10-28T09:00:00+08:00",
                "relation_type": "direct",
                "sentiment": "neutral",
                "impact_level": 3,
            }
        ],
    }


def search(code: str, name: str) -> dict:
    return {
        "tool": "search_stocks",
        "status": "ok",
        "data": [{"code": code, "name": name, "market": "港股", "score": 100, "match_type": "exact_code"}],
    }


def test_peer_tools_do_not_hijack_the_subject_of_a_tencent_question():
    tools = [
        stock("00700.HK", "腾讯控股", 385.6, "hk"),
        stock("01810.HK", "小米集团", 45.2, "hk"),
        stock("09988.HK", "阿里巴巴", 120.3, "hk"),
        news("01810.HK", "小米集团", "小米汽车交付创新高"),
        news("00700.HK", "腾讯控股", "腾讯回购股份"),
        search("01810.HK", "小米集团"),
        {"tool": "web_search", "status": "ok", "data": [{"title": "peer context"}]},
    ]
    response = build_agent_response(
        AgentOutput(summary="腾讯近 20 个交易日走弱，但不是单日暴跌。"),
        tools,
        language="zh-CN",
        user_query="最近腾讯为什么暴跌",
    )
    payload = response.payload
    assert payload["stock_code"] == "00700.HK"
    assert payload["stock_name"] == "腾讯控股"
    assert payload["quote"]["current_price"] == 385.6
    assert payload["market"] == "hk"
    assert "stocks" not in payload
    assert [item["stock_code"] for item in payload["news"]] == ["00700.HK"]


def test_comparison_keeps_all_analyses_but_uses_the_first_named_company():
    tools = [
        stock("01810.HK", "小米集团", 45.2, "hk"),
        stock("00700.HK", "腾讯控股", 385.6, "hk"),
        news("01810.HK", "小米集团", "小米汽车交付创新高"),
        news("00700.HK", "腾讯控股", "腾讯回购股份"),
    ]
    response = build_agent_response(
        AgentOutput(summary="对比腾讯和小米的业务与财务。"),
        tools,
        language="zh-CN",
        user_query="对比腾讯和小米的业务与财务",
    )
    assert response.payload["stock_code"] == "00700.HK"
    assert [item["stock_code"] for item in response.payload["stocks"]] == ["00700.HK", "01810.HK"]


def test_single_analysis_is_still_the_subject_without_a_query_match():
    response = build_agent_response(
        AgentOutput(summary="招商银行技术面。"),
        [stock("sh600036", "招商银行", 42.5, "sh")],
        language="zh-CN",
        user_query="看看这只银行股",
    )
    assert response.payload["stock_code"] == "sh600036"
    assert response.payload["stock_name"] == "招商银行"
