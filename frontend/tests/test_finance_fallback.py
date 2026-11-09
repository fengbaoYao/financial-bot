import asyncio

import pytest

from api.financial_bot import finance_fallback as fallback
from api.financial_bot.gap_synthesis import synthesize_gap


def test_find_companies_returns_both_sides_of_a_comparison():
    pairs = fallback._find_companies("茅台和五粮液哪个估值更便宜")
    codes = {code for code, _ in pairs}
    assert "sh600519" in codes
    assert any(code.startswith("sz000858") for code in codes)


def test_period_parser_and_statement_router():
    assert fallback._periods("最近 5 年营收") == 5
    assert fallback._statement("现金流怎么样") == "cashflow"
    assert fallback._statement("资产负债表") == "balance"
    assert fallback._statement("营收和净利润") == "income"


def test_financial_fallback_uses_source_backed_periods(monkeypatch):
    def fake_statements(code, statement, periods):
        return {
            "provider": "fuyao",
            "symbol": code,
            "statement": statement,
            "data_as_of": "2025-12-31",
            "retrieved_at": "2026-10-30T00:00:00+08:00",
            "currency": "CNY",
            "adjustment": "not_applicable",
            "periods": [
                {"period": "2025-12-31", "items": {"revenue": 168838102514.79, "net_profit": 85310324833.67}},
            ],
            "data_gaps": [],
        }

    monkeypatch.setattr("api.local_research.financial_statements", fake_statements)
    response = fallback.deterministic_financial_research("贵州茅台 2025 年营业收入", "zh")
    assert response is not None
    assert response.intent == "financial_snapshot"
    assert "168,838,102,514.79" not in response.summary
    assert "1,688.38 亿" in response.summary
    assert response.evidence[0].source == "fuyao"
    assert response.data_gaps == []


def test_financial_fallback_is_explicit_when_source_fails(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr("api.local_research.financial_statements", fail)
    response = fallback.deterministic_financial_research("贵州茅台 2025 年营业收入", "zh")
    assert response is not None
    assert response.intent == "error"
    assert response.data_gaps
    assert "请不" in response.summary


def test_multi_company_valuation_fallback(monkeypatch):
    class FakeProvider:
        configured = True

        def valuations(self, codes):
            return {
                "items": [
                    {"thscode": "600519.SH", "name": "贵州茅台", "pe_ttm": 21.0, "pe_mrq": 20.0, "pb_mrq": 6.8, "ps_ttm": 10.0, "pcf_ttm": 19.0},
                    {"thscode": "000858.SZ", "name": "五粮液", "pe_ttm": 23.0, "pe_mrq": 22.0, "pb_mrq": 2.4, "ps_ttm": 8.0, "pcf_ttm": 18.0},
                ],
                "as_of": "2026-10-30",
                "retrieved_at": "2026-10-30T00:00:00+08:00",
                "endpoint": "fuyao.aicubes.cn/api/a-share/valuations/snapshot",
                "raw_sha256": "x",
                "data_gaps": [],
            }

    from api.datasources import fuyao_provider as provider_module

    monkeypatch.setattr(provider_module, "_PROVIDER", FakeProvider())
    response = fallback.deterministic_financial_research("茅台和五粮液估值对比", "zh")
    assert response is not None
    assert response.intent == "valuation_snapshot"
    assert "600519.SH" in response.summary
    assert "000858.SZ" in response.summary
    assert response.payload["data_cards"][0]["kind"] == "valuations"


def test_gap_fill_can_be_disabled_without_calling_a_model(monkeypatch):
    monkeypatch.setenv("LLM_GAP_FILL", "0")
    assert asyncio.run(synthesize_gap("测试", "zh")) is None


def test_eastmoney_announcement_fallback_is_labelled_secondary(monkeypatch):
    import pandas as pd
    import akshare as ak
    from api.local_research import eastmoney_announcements

    monkeypatch.setattr(ak, "stock_individual_notice_report", lambda **kwargs: pd.DataFrame([{
        "代码": "600519", "名称": "贵州茅台", "公告标题": "贵州茅台年度报告",
        "公告类型": "财务报告", "公告日期": "2026-03-31", "网址": "https://example.org/notice",
    }]))
    data = eastmoney_announcements("sh600519", 3)
    assert data["provider"] == "eastmoney_announcements"
    assert data["announcements"][0]["title"] == "贵州茅台年度报告"
    assert any("二级" in gap for gap in data["data_gaps"])


def test_multi_company_financial_fallback(monkeypatch):
    def fake_statements(code, statement, periods):
        return {
            "provider": "fuyao", "symbol": code, "statement": statement,
            "data_as_of": "2025-12-31", "retrieved_at": "2026-10-30T00:00:00+08:00",
            "currency": "CNY", "adjustment": "not_applicable",
            "periods": [{"period": "2025-12-31", "items": {"revenue": 100.0, "net_profit": 10.0}}],
            "data_gaps": [],
        }

    monkeypatch.setattr("api.local_research.financial_statements", fake_statements)
    response = fallback.deterministic_financial_research("茅台和五粮液营业收入对比", "zh")
    assert response is not None
    assert response.intent == "financial_snapshot"
    assert "贵州茅台" in response.summary
    assert "五粮液" in response.summary
    assert "revenue" in response.summary
