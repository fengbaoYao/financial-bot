import json
from types import SimpleNamespace

import pandas as pd
import pytest

import api.local_research.providers as providers
from api.evidence import sources_for_tool


class FakeResponse:
    def __init__(self, *, payload=None, content=b"", text="", content_type="json"):
        self._payload = payload
        self.content = content
        self.text = text
        self.encoding = "utf-8"
        self.content_type = content_type

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_cninfo_announcements_parse_and_evidence_fields(monkeypatch):
    monkeypatch.setattr(providers, "_ORG_CACHE", {"600519": "gssh0600519"})

    def fake_post(url, data=None, headers=None, timeout=None):
        assert "hisAnnouncement/query" in url
        assert data["stock"] == "600519,gssh0600519"
        return FakeResponse(
            payload={
                "announcements": [
                    {
                        "announcementTitle": "贵州茅台2026年半年度报告",
                        "announcementTypeName": "半年报",
                        "announcementTime": 1786752000000,
                        "announcementId": 1225475868,
                    }
                ]
            },
            content=b'{"announcements":[]}',
        )

    monkeypatch.setattr(providers.requests, "post", fake_post)
    result = providers.cninfo_announcements("sh600519", limit=5)
    assert result["provider"] == "cninfo"
    assert result["announcements"][0]["title"].startswith("贵州茅台")
    assert result["announcements"][0]["url"].endswith("annoId=1225475868")
    assert result["raw_sha256"]
    assert result["raw_retained"] is False

    evidence = sources_for_tool(
        {"tool": "get_cninfo_announcements", "data": result, "retrieved_at": result["retrieved_at"]}
    )
    assert evidence[0].source == "巨潮资讯"
    assert evidence[0].raw_sha256 == result["raw_sha256"]
    assert evidence[0].freshness == "official_filing"


def test_ths_consensus_parse_reports_count_and_missing_years(monkeypatch):
    html = """
    <table>
      <tr><th>年度</th><th>预测机构数</th><th>最小值</th><th>均值</th><th>最大值</th></tr>
      <tr><td>2026</td><td>44</td><td>64.70</td><td>67.16</td><td>71.00</td></tr>
      <tr><td>2027</td><td>30</td><td>70.00</td><td>73.00</td><td>77.00</td></tr>
    </table>
    """
    monkeypatch.setattr(
        providers.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(text=html, content=html.encode("utf-8")),
    )
    result = providers.consensus_estimates("600519")
    assert result["provider"] == "ths"
    assert [row["period"] for row in result["years"]] == ["FY2026", "FY2027"]
    assert result["years"][0]["count"] == 44
    assert any("FY2028" in gap for gap in result["data_gaps"])

    evidence = sources_for_tool(
        {"tool": "get_consensus_estimates", "data": result, "retrieved_at": result["retrieved_at"]}
    )
    assert evidence[0].source == "同花顺一致预期"
    assert evidence[0].currency == "CNY"
    assert evidence[0].record_key == "FY2026"


def test_consensus_without_table_fails_loudly(monkeypatch):
    monkeypatch.setattr(
        providers.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(text="<html><body>no table</body></html>", content=b"x"),
    )

    def unavailable_fallback(_digits):
        raise providers.LocalDataError("Eastmoney fixture unavailable")

    monkeypatch.setattr(providers, "_eastmoney_consensus_estimates", unavailable_fallback)
    with pytest.raises(providers.LocalDataError):
        providers.consensus_estimates("600519")


def test_local_research_scope_can_be_disabled(monkeypatch):
    from api.local_research import local_research_enabled

    monkeypatch.setenv("FINANCIAL_BOT_LOCAL_RESEARCH_DATA", "0")
    assert local_research_enabled() is False
    monkeypatch.setenv("FINANCIAL_BOT_LOCAL_RESEARCH_DATA", "1")
    assert local_research_enabled() is True

    import api.main as main

    monkeypatch.setattr(main, "_cloud_auth_enabled", lambda: True)
    assert local_research_enabled() is False


def test_index_constituents_parse_and_evidence(monkeypatch):
    frame = pd.DataFrame([
        {"日期Date": "20260930", "指数代码IndexCode": "000300", "成份券代码ConstituentCode": "300308", "成份券名称ConstituentName": "中际旭创", "交易所Exchange": "深圳证券交易所", "权重(%)weight": "50.000"},
        {"日期Date": "20260930", "指数代码IndexCode": "000300", "成份券代码ConstituentCode": "600519", "成份券名称ConstituentName": "贵州茅台", "交易所Exchange": "上海证券交易所", "权重(%)weight": "50.000"},
    ])
    monkeypatch.setattr(pd, "read_excel", lambda *args, **kwargs: frame)
    monkeypatch.setattr(
        providers.requests, "get",
        lambda *args, **kwargs: FakeResponse(content=b"xlsx"),
    )
    result = providers.index_constituents("000300")
    assert result["provider"] == "csindex"
    assert result["member_count"] == 2
    assert result["members"][0]["code"] == "SZ300308"
    assert result["weight_total_percent"] == 100.0
    evidence = sources_for_tool(
        {"tool": "get_index_constituents", "data": result, "retrieved_at": result["retrieved_at"]}
    )
    assert evidence[0].source == "中证指数"
    assert evidence[0].record_key == "000300"
    assert evidence[0].freshness == "official_index_snapshot"


def test_financial_statements_parse_and_evidence(monkeypatch):
    payload = {
        "result": {"data": {"report_list": {
            "20260630": {"data": [
                {"item_title": "营业总收入", "item_value": "92278072083.21", "item_tongbi": "0.013"},
                {"item_title": "净利润", "item_value": "46033330566.78"},
            ]},
            "20260331": {"data": [
                {"item_title": "营业总收入", "item_value": "50000000000"},
                {"item_title": "净利润", "item_value": "25000000000"},
            ]},
        }}}
    }
    monkeypatch.setattr(
        providers.requests, "get",
        lambda *args, **kwargs: FakeResponse(payload=payload, content=b"json"),
    )
    result = providers.financial_statements("600519", "income", 2)
    assert result["statement"] == "income"
    assert [row["period"] for row in result["periods"]] == ["2026-06-30", "2026-03-31"]
    assert result["periods"][0]["items"]["revenue_total"] == 92278072083.21
    evidence = sources_for_tool(
        {"tool": "get_financial_statements", "data": result, "retrieved_at": result["retrieved_at"]}
    )
    assert evidence[0].source == "新浪财报"
    assert evidence[0].currency == "CNY"
    assert evidence[0].data_as_of == "2026-06-30"


def test_quote_and_history_from_different_providers_have_separate_evidence():
    refs = sources_for_tool({
        'tool': 'get_stock_analysis', 'retrieved_at': '2026-11-04T01:32:59Z',
        'data': {'stock_code': '00700.HK', 'quote': {'current_price': 393.4},
                 'chart_series': [{'date': '2026-11-03', 'close': 397.2}],
                 'metadata': {'source': 'yfinance_adapter(0700.HK)', 'quote_provider': 'ifind',
                              'ifind_quote': {'provider': 'ifind', 'data_as_of': '2026-11-04 09:32:59',
                                              'price_status': 'delayed_or_snapshot'},
                              'currency': 'HKD', 'adjustment': 'auto_adjust'}}})
    assert len(refs) == 2
    quote = next(ref for ref in refs if ref.source == 'ifind')
    history = next(ref for ref in refs if ref.source == 'yfinance_adapter(0700.HK)')
    assert quote.data_as_of == '2026-11-04 09:32:59'
    assert quote.freshness == 'delayed_or_snapshot'
    assert history.data_as_of == '2026-11-03'
    assert history.freshness == 'historical_or_delayed'
    assert quote.id != history.id
