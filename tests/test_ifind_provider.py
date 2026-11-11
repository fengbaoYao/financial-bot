"""iFinD adapter contract tests - no network, no real token."""
import pandas as pd
import pytest

from api.datasources import ifind_provider
from api.datasources.ifind_provider import IfindProvider, IfindUnavailable, to_ths_code

HISTORY_BODY = {
    "errorcode": 0,
    "errmsg": "",
    "tables": [
        {
            "thscode": "600519.SH",
            "table": {
                "date": ["2026-10-28", "2026-10-29", "2026-10-30"],
                "open": [1400.0, 1405.5, 1410.0],
                "high": [1410.0, 1420.0, 1440.0],
                "low": [1395.0, 1400.0, 1408.0],
                "close": [1405.0, 1415.0, 1439.1],
                "volume": [1000, 1200, 1500],
                "amount": [1.4e6, 1.7e6, 2.1e6],
            },
        }
    ],
}


def provider(monkeypatch, body, calls=None):
    monkeypatch.setenv("IFIND_ACCESS_TOKEN", "test-token")
    def transport(url, payload, headers):
        if calls is not None:
            calls.append((url, payload, headers))
        return body
    return IfindProvider(transport=transport)


@pytest.mark.parametrize(
    "code,expected",
    [
        ("sh600519", "600519.SH"),
        ("SH600519", "600519.SH"),
        ("sz000001", "000001.SZ"),
        ("600519", "600519.SH"),
        ("000001", "000001.SZ"),
        ("hk00700", "0700.HK"),
        ("00700.HK", "0700.HK"),
        ("AAPL.O", "AAPL.O"),
    ],
)
def test_symbol_mapping_is_explicit(code, expected):
    assert to_ths_code(code) == expected


def test_unmapped_symbols_refuse_instead_of_guessing():
    # A US ticker without an exchange suffix could silently resolve to the
    # wrong instrument, so the adapter refuses and the caller falls back.
    for code in ("US.AAPL", "AAPL", ""):
        with pytest.raises(IfindUnavailable):
            to_ths_code(code)


def test_history_wraps_the_body_and_reports_provenance(monkeypatch):
    calls = []
    client = provider(monkeypatch, HISTORY_BODY, calls)
    payload = client.history("sh600519", start="2026-10-01", end="2026-11-01", adjust="forward")

    url, body, headers = calls[0]
    assert url.endswith("/history_data")
    # Flat bodies are rejected by the vendor with -4210; the key is mandatory.
    assert list(body) == ["reqBody"]
    assert body["reqBody"]["codes"] == "600519.SH"
    assert body["reqBody"]["functionpara"]["CPS"] == "2"
    assert headers["access_token"] == "test-token"

    assert [bar["date"] for bar in payload["bars"]] == ["2026-10-28", "2026-10-29", "2026-10-30"]
    assert payload["bars"][-1]["close"] == 1439.1
    assert payload["data_as_of"] == "2026-10-30"
    assert payload["adjustment"] == "qfq"
    assert payload["provider"] == "ifind"


def test_business_error_code_is_a_fallback_not_a_crash(monkeypatch):
    client = provider(monkeypatch, {"errorcode": -4210, "errmsg": "输入参数错误", "tables": []})
    with pytest.raises(IfindUnavailable) as excinfo:
        client.history("sh600519", start="2026-10-01", end="2026-11-01")
    assert excinfo.value.reason.startswith("errorcode:-4210")


def test_missing_token_fails_before_any_request(monkeypatch):
    monkeypatch.delenv("IFIND_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("ifind_api_access_token", raising=False)
    calls = []
    client = IfindProvider(transport=lambda *args: calls.append(args) or {})
    assert client.is_configured is False
    with pytest.raises(IfindUnavailable) as excinfo:
        client.history("sh600519", start="2026-10-01", end="2026-11-01")
    assert excinfo.value.reason == "not_configured"
    assert calls == []


def test_quote_uses_the_snapshot_endpoint(monkeypatch):
    body = {
        "errorcode": 0,
        "tables": [
            {
                "thscode": "0700.HK",
                "table": {
                    "latest": [393.2],
                    "change": [9.6],
                    "changeRatio": [2.5],
                    "preClose": [383.6],
                    "volume": [1200000],
                    "amount": [4.7e8],
                    "tradeDate": ["2026-11-02"],
                    "tradeTime": ["15:59:59"],
                },
            }
        ],
    }
    calls = []
    client = provider(monkeypatch, body, calls)
    quote = client.quote("hk00700")
    assert calls[0][0].endswith("/real_time_quotation")
    assert quote["price"] == 393.2
    assert quote["change_pct"] == 2.5
    assert quote["data_as_of"] == "2026-11-02 15:59:59"


def test_data_fetcher_tries_ifind_before_the_legacy_chain(monkeypatch):
    """Wiring test: a configured iFinD answers the A-share fetch on its own."""
    from ashare import data as data_module

    monkeypatch.setenv("IFIND_ACCESS_TOKEN", "test-token")
    monkeypatch.delenv("IFIND_ENABLED", raising=False)
    monkeypatch.delenv("IFIND_PRIMARY", raising=False)

    def fake_history(self, code, *, start, end, adjust="none", **kwargs):
        return {
            "bars": [
                {"date": "2026-10-28", "open": 1400.0, "high": 1410.0, "low": 1395.0, "close": 1405.0, "volume": 1000},
                {"date": "2026-10-29", "open": 1405.0, "high": 1420.0, "low": 1400.0, "close": 1415.0, "volume": 1200},
            ],
            "provider": "ifind",
            "adjustment": "qfq",
            "data_as_of": "2026-10-29",
        }

    monkeypatch.setattr(ifind_provider.IfindProvider, "history", fake_history)
    # Any legacy path would be a bug here: fail loudly if it is reached.
    monkeypatch.setattr(data_module.DataFetcher, "_fetch_a_share_fuyao", lambda self, *a, **k: pytest.fail("fuyao reached"))
    monkeypatch.setattr(data_module.DataFetcher, "_fetch_a_share", lambda self, *a, **k: pytest.fail("legacy reached"))

    fetcher = data_module.DataFetcher()
    frame = fetcher.fetch_stock_data("sh600519", count=2, frequency="1d")
    assert isinstance(frame, pd.DataFrame) and len(frame) == 2
    assert list(frame.columns) == ["open", "close", "high", "low", "volume"]
    assert fetcher.provider_info("sh600519") == {"provider": "ifind", "adjustment": "qfq"}


# Captured from a real `history_data` call (2026-11-02). The vendor puts the
# dates on the entry (`time`), not inside `table`; parsing `table["date"]`
# silently produced zero rows and the provider fell back for every request.
REAL_HISTORY_SHAPE = {
    "errorcode": 0,
    "errmsg": "",
    "datatype": "DATA_SERIES",
    "tables": [
        {
            "thscode": "600519.SH",
            "time": ["2026-10-30", "2026-11-02"],
            "table": {
                "open": [1363.0, 1410.0],
                "close": [1392.05, 1439.1],
                "volume": [5992729, 8269122],
            },
        }
    ],
}


def test_history_reads_dates_from_the_entry_not_the_table(monkeypatch):
    client = provider(monkeypatch, REAL_HISTORY_SHAPE)
    payload = client.history("sh600519", start="2026-10-28", end="2026-11-02", adjust="forward")
    assert [bar["date"] for bar in payload["bars"]] == ["2026-10-30", "2026-11-02"]
    assert payload["bars"][-1]["close"] == 1439.1
    assert payload["data_as_of"] == "2026-11-02"


def test_hk_symbols_use_four_digits(monkeypatch):
    # 00700.HK is rejected by the vendor with -4210; 0700.HK is the real symbol.
    body = {
        "errorcode": 0,
        "tables": [{"thscode": "0700.HK", "time": ["2026-11-02"], "table": {"close": [392.2]}}],
    }
    calls = []
    client = provider(monkeypatch, body, calls)
    payload = client.history("hk00700", start="2026-11-01", end="2026-11-02", adjust="forward")
    assert calls[0][1]["reqBody"]["codes"] == "0700.HK"
    assert payload["bars"][0]["close"] == 392.2


# Captured from real responses (2026-11-02, 600519.SH).
REAL_VALUATION_SHAPE = {
    "errorcode": 0,
    "tables": [{"thscode": "600519.SH", "table": {
        "ths_pe_ttm_stock": [21.896008878325],
        "ths_pb_latest_stock": [6.6333369704611],
        "ths_ps_ttm_stock": [10.313316812024],
        "ths_market_value_stock": [1798992431999.1],
    }}],
}
REAL_ANNOUNCEMENT_SHAPE = {
    "errorcode": 0,
    "tables": [{"table": {
        "reportDate": ["2026-10-31", "2026-10-31"],
        "thscode": ["600519.SH", "600519.SH"],
        "secName": ["贵州茅台", "贵州茅台"],
        "ctime": ["2026-10-30 18:31:41", "2026-10-30 18:31:37"],
        "reportTitle": ["贵州茅台：关于高级管理人员薪酬兑现方案的公告", "贵州茅台：2026年第三季度报告"],
        "pdfURL": ["http://ft.10jqka.com.cn/a", "http://ft.10jqka.com.cn/b"],
        "seq": ["5375059387", "5375059388"],
    }}],
}


def test_valuation_snapshot_uses_ttm_parameters(monkeypatch):
    calls = []
    client = provider(monkeypatch, REAL_VALUATION_SHAPE, calls)
    snapshot = client.valuation("sh600519", as_of="2026-11-02")
    payload = calls[0][1]
    assert calls[0][0].endswith("/basic_data_service")
    assert [item["indicator"] for item in payload["indipara"]] == [
        "ths_pe_ttm_stock", "ths_pb_latest_stock", "ths_ps_ttm_stock", "ths_market_value_stock",
    ]
    # Parameters are positional; TTM/benchmark selectors must be present.
    assert payload["indipara"][0]["indiparams"] == ["2026-11-02", "100"]
    assert payload["indipara"][3]["indiparams"] == ["2026-11-02"]
    assert round(snapshot["pe"], 2) == 21.9
    assert round(snapshot["pb"], 2) == 6.63
    assert snapshot["market_cap"] > 1e12


def test_announcements_keep_dates_and_pdf_links(monkeypatch):
    calls = []
    client = provider(monkeypatch, REAL_ANNOUNCEMENT_SHAPE, calls)
    data = client.announcements("sh600519", begin="2026-09-01", end="2026-11-02", limit=2)
    # Date filters must sit at the top level; functionpara silently ignores them.
    body = calls[0][1]
    assert body["beginrDate"] == "2026-09-01" and body["endrDate"] == "2026-11-02"
    assert "outputpara" in body and "pdfURL:Y" in body["outputpara"]
    assert [row["date"] for row in data["announcements"]] == ["2026-10-31", "2026-10-31"]
    assert data["announcements"][0]["url"].startswith("http")
    assert data["data_as_of"] == "2026-10-31"


def test_edb_reports_a_permission_gap_instead_of_faking_series(monkeypatch):
    client = provider(monkeypatch, {"errorcode": -4318, "errmsg": "usage exceeded", "tables": []})
    with pytest.raises(IfindUnavailable) as excinfo:
        client.edb("M001620326", start="2026-01-01", end="2026-11-02")
    assert "errorcode:-4318" in excinfo.value.reason


def test_screening_keeps_vendor_columns_and_caps_rows(monkeypatch):
    body = {
        "errorcode": 0,
        "tables": [{"table": {
            "股票代码": ["601169.SH", "601857.SH", "600887.SH"],
            "股票简称": ["北京银行", "中国石油", "伊利股份"],
            "市盈率(pe)[20261102]": ["4.165", "12.354", "16.500"],
            "股票市场类型": ["沪深300", "沪深300", "沪深300"],
        }}],
    }
    calls = []
    client = provider(monkeypatch, body, calls)
    result = client.screen("市盈率低于20倍的沪深300成分股", limit=2)
    assert calls[0][0].endswith("/smart_stock_picking")
    assert calls[0][1]["searchtype"] == "stock"
    assert result["count"] == 3
    assert len(result["rows"]) == 2
    # Chinese, query-dependent column names are passed through untouched.
    assert result["rows"][0]["股票简称"] == "北京银行"
    assert "市盈率(pe)[20261102]" in result["columns"]


def test_constituents_need_a_real_block_id(monkeypatch):
    body = {
        "errorcode": 0,
        "tables": [{"table": {
            "p03291_f001": ["2026/11/02", "2026/11/02"],
            "p03291_f002": ["000001.SZ", "000002.SZ"],
            "p03291_f003": ["平安银行", "万科A"],
        }}],
    }
    calls = []
    client = provider(monkeypatch, body, calls)
    result = client.constituents("001045011006001", day="2026-11-02")
    sent = calls[0][1]
    assert sent["reportname"] == "p03291"
    assert sent["functionpara"]["blockname"] == "001045011006001"
    assert sent["functionpara"]["date"] == "20261102"
    assert result["rows"][0] == {"thscode": "000001.SZ", "name": "平安银行"}


def test_index_codes_map_to_vendor_board_ids():
    from api.datasources.ifind_blocks import block_for_index

    # Values come from the vendor board dictionary; guessing an id yields an
    # empty list rather than an error, so the mapping is explicit.
    assert block_for_index("000300") == ("001045011006001", "沪深300")
    assert block_for_index("000300.SH") == ("001045011006001", "沪深300")
    assert block_for_index("399006.SZ")[1] == "创业板指"
    assert block_for_index("999999") is None
    assert block_for_index("") is None


def test_pe_history_reports_percentile_from_real_shape(monkeypatch):
    body = {
        "errorcode": 0,
        "tables": [{
            "thscode": "600519.SH",
            "time": ["2026-10-30", "2026-10-31", "2026-11-02"],
            "table": {"ths_pe_ttm_stock": [19.0, 21.0, 22.0]},
        }],
    }
    client = provider(monkeypatch, body)
    data = client.series("sh600519", indicator="ths_pe_ttm_stock", indiparams=["100"], start="2026-10-30", end="2026-11-02")
    assert [point["value"] for point in data["points"]] == [19.0, 21.0, 22.0]


def test_valuation_chart_is_labelled_as_a_multiple():
    # The chart bubble switches to "×" and shows the percentile note for PE.
    from api.agent_pydantic import AgentOutput, build_agent_response

    output = AgentOutput(summary="估值不贵。")
    tool_results = [{
        "tool": "get_valuations",
        "data": {
            "provider": "ifind",
            "items": [{"ticker": "sh600519", "pe_ttm": 21.9}],
            "pe_percentile": 31.2,
            "pe_range": {"min": 17.7, "max": 55.0, "years": 5},
            "pe_series": [{"date": "2026-11-01", "value": 21.5}, {"date": "2026-11-02", "value": 21.9}],
        },
    }]
    response = build_agent_response(output, tool_results, "zh-CN")
    chart = response.payload["chart"]
    assert chart["metric"] == "pe"
    assert "31.2%" in chart["note"]
    assert chart["series"][-1] == {"date": "2026-11-02", "close": 21.9}


def test_high_frequency_bars_keep_intraday_timestamps(monkeypatch):
    body = {
        "errorcode": 0,
        "tables": [{
            "thscode": "600519.SH",
            "time": ["2026-11-02 10:00", "2026-11-02 10:30"],
            "table": {"open": [1410.0, 1438.97], "high": [1443.32, 1443.55],
                      "low": [1405.0, 1430.0], "close": [1438.97, 1431.47], "volume": [1200, 900]},
        }],
    }
    calls = []
    client = provider(monkeypatch, body, calls)
    data = client.high_frequency("sh600519", start="2026-11-02 09:30:00", end="2026-11-02 15:00:00", interval=30)
    assert calls[0][0].endswith("/high_frequency")
    assert calls[0][1]["functionpara"]["Interval"] == "30"
    assert data["bars"][0]["time"] == "2026-11-02 10:00"
    assert data["bars"][-1]["close"] == 1431.47


def test_history_quota_circuit_skips_network(monkeypatch):
    from api.datasources import ifind_provider as mod

    monkeypatch.setenv("IFIND_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("IFIND_HISTORY_ENABLED", "1")
    mod.clear_history_quota_circuit()
    calls = []
    client = IfindProvider(
        transport=lambda url, payload, headers: calls.append((url, payload))
        or {"errorcode": -4318, "errmsg": "usage of data has exceeded this month", "tables": []}
    )
    with pytest.raises(IfindUnavailable) as first:
        client.history("sh600519", start="2026-10-01", end="2026-11-01")
    assert first.value.quota_exhausted is True
    assert len(calls) == 1
    assert mod.history_quota_exhausted() is True
    assert "历史数据额度已用完" in (mod.history_quota_user_note() or "")

    with pytest.raises(IfindUnavailable) as second:
        client.history("sh600519", start="2026-10-01", end="2026-11-01")
    assert "history_quota_exhausted" in second.value.reason
    # Second call must not hit the network again.
    assert len(calls) == 1
    mod.clear_history_quota_circuit()


def test_quote_still_works_when_history_circuit_open(monkeypatch):
    from api.datasources import ifind_provider as mod

    mod.clear_history_quota_circuit()
    mod.mark_history_quota_exhausted("test")
    body = {
        "errorcode": 0,
        "tables": [{"table": {
            "latest": [1439.1],
            "change": [47.1],
            "changeRatio": [3.38],
            "preClose": [1392.0],
            "volume": [1000],
            "amount": [1.0],
            "tradeDate": ["2026-11-03"],
            "tradeTime": ["15:00:00"],
        }}],
    }
    client = provider(monkeypatch, body)
    snap = client.quote("sh600519")
    assert snap["price"] == 1439.1
    mod.clear_history_quota_circuit()


def test_announcements_do_not_publish_credential_bearing_pdf_url(monkeypatch):
    from api.evidence import safe_source_url
    body = {'errorcode': 0, 'tables': [{'table': {'reportTitle': ['腾讯控股：公告'],
            'reportDate': ['2026-11-04'], 'pdfURL': ['https://vendor.example/document?token=secret&userid=123&ip=127.0.0.1'], 'seq': ['42']}}]}
    result = provider(monkeypatch, body).announcements('00700.HK', begin='2026-11-01', end='2026-11-04')
    assert result['announcements'][0]['url'] is None
    assert safe_source_url('https://vendor.example/document?access_token=secret') is None
    assert safe_source_url('https://vendor.example/document?id=42') == 'https://vendor.example/document?id=42'
