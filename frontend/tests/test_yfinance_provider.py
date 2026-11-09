from types import SimpleNamespace

import pandas as pd
import pytest

from api.datasources.yfinance_provider import (
    NotSupportedSymbol,
    YFinanceProvider,
    to_yahoo_symbol,
)
from api.datasources.shadow import compare_once


class FakeFastInfo:
    last_price = 123.45
    previous_close = 120.0
    open = 121.0
    day_high = 125.0
    day_low = 119.0
    last_volume = 1000
    market_cap = 1_000_000
    currency = "USD"
    exchange = "NMS"


class FakeTicker:
    fast_info = FakeFastInfo()
    history_frame = pd.DataFrame(
        [
            {"Open": 120.0, "High": 124.0, "Low": 119.0, "Close": 121.0, "Adj Close": 120.5, "Volume": 900, "Dividends": 0.0, "Stock Splits": 0.0},
            {"Open": 121.0, "High": 126.0, "Low": 120.0, "Close": 123.45, "Adj Close": 123.0, "Volume": 1000, "Dividends": 0.0, "Stock Splits": 0.0},
        ],
        index=pd.to_datetime(["2026-10-28", "2026-10-29"]),
    )
    financials = pd.DataFrame(
        {"Revenue": [100.0, 110.0]},
        index=pd.to_datetime(["2025-09-30", "2025-12-31"]),
    ).T
    analyst_price_targets = {"current": 123.45, "high": 150.0, "low": 100.0, "mean": 130.0}
    recommendations = pd.DataFrame([{"period": "0m", "strongBuy": 10, "buy": 5, "hold": 2, "sell": 0, "strongSell": 0}])
    news = []
    calendar = {"Earnings Date": ["2026-11-05"]}

    def history(self, **kwargs):
        return self.history_frame

    def get_info(self):
        # Quote currency (HKD) deliberately differs from reporting currency (CNY).
        return {"currency": "HKD", "financialCurrency": "CNY"}


def provider():
    return YFinanceProvider(ticker_factory=lambda symbol: FakeTicker())


def test_symbol_mapping_for_supported_markets():
    assert to_yahoo_symbol("sh600519") == "600519.SS"
    assert to_yahoo_symbol("600519") == "600519.SS"
    assert to_yahoo_symbol("sz000001") == "000001.SZ"
    assert to_yahoo_symbol("HK.00700") == "0700.HK"
    assert to_yahoo_symbol("00700.HK") == "0700.HK"
    assert to_yahoo_symbol("US.AAPL") == "AAPL"
    assert to_yahoo_symbol("US.BRK.B") == "BRK-B"
    assert to_yahoo_symbol("^GSPC") == "^GSPC"
    with pytest.raises(NotSupportedSymbol):
        to_yahoo_symbol("bj430047")


def test_quote_and_history_are_json_friendly_with_provenance():
    p = provider()
    quote = p.quote("US.AAPL")
    assert quote["provider"] == "yfinance"
    assert quote["price"] == 123.45
    assert quote["currency"] == "USD"
    assert quote["raw_sha256"]

    history = p.history("US.AAPL", period="5d")
    assert len(history["bars"]) == 2
    assert history["as_of"] == "2026-10-29T00:00:00"
    assert history["bars"][-1]["close"] == 123.45
    assert history["bars"][-1]["adj_close"] == 123.0
    assert history["adjustment"] == "close_raw; adj_close=yahoo_adjusted"


def test_financials_and_estimates_are_normalized():
    p = provider()
    financials = p.financials("US.AAPL", "income", periods=2)
    assert len(financials["periods"]) == 2
    assert financials["periods"][0]["values"]["Revenue"] in {100.0, 110.0}
    assert financials["provider"] == "yfinance"

    estimates = p.estimates("US.AAPL")
    assert estimates["targets"]["mean"] == 130.0
    assert estimates["recommendations"]


def test_financials_report_the_provider_statement_currency_not_the_listing_market():
    financials = provider().financials("00700.HK", "income", periods=2)
    # HK listing, CNY reporting: the listing market must not decide the currency.
    assert financials["currency"] == "CNY"
    assert financials["currency_basis"] == "provider_financial_currency"
    assert not financials["data_gaps"]


def test_financials_leave_currency_unverified_when_provider_omits_it():
    class NoCurrencyTicker(FakeTicker):
        def get_info(self):
            return {"currency": "HKD"}

    result = YFinanceProvider(ticker_factory=lambda symbol: NoCurrencyTicker()).financials(
        "00700.HK", "income", periods=2)
    assert result["currency"] is None
    assert result["currency_basis"] == "unverified"
    assert any("unverified" in gap for gap in result["data_gaps"])


def test_financials_tolerate_a_provider_that_cannot_report_currency():
    class BrokenInfoTicker(FakeTicker):
        def get_info(self):
            raise RuntimeError("quote payload unavailable")

    result = YFinanceProvider(ticker_factory=lambda symbol: BrokenInfoTicker()).financials(
        "00700.HK", "income", periods=2)
    assert result["currency_basis"] == "unverified"


def test_empty_news_is_an_empty_success_not_an_error():
    result = provider().news("US.AAPL")
    assert result["status"] == "empty"
    assert result["items"] == []


def test_shadow_compare_is_read_only_and_reports_difference():
    response = SimpleNamespace(
        quote=SimpleNamespace(current_price=120.0),
        metadata={"provider": "primary", "currency": "USD", "adjustment": "none", "data_as_of": "2026-10-29"},
        chart_series=[{"date": "2026-10-29", "close": 120.0}],
    )
    record = compare_once("US.AAPL", response, provider=provider())
    assert record["status"] == "ok"
    assert record["primary_provider"] == "primary"
    assert record["yf_close"] == 123.45
    assert record["close_diff_pct"] == 2.875
    assert record["currency_match"] is True
    assert response.quote.current_price == 120.0


def test_shadow_compares_same_as_of_date_not_latest_day():
    response = SimpleNamespace(
        quote=SimpleNamespace(current_price=120.0),
        metadata={"provider": "primary", "currency": "USD", "adjustment": "none", "data_as_of": "2026-10-28"},
        chart_series=[{"date": "2026-10-28", "close": 120.0}],
    )
    record = compare_once("US.AAPL", response, provider=provider())
    assert record["status"] == "ok"
    assert record["date_match"] is True
    assert record["yf_compare_date"].startswith("2026-10-28")
    assert record["yf_close"] == 121.0
    assert record["close_diff_pct"] == 0.8333


def test_shadow_is_off_by_default(monkeypatch):
    from api.datasources.shadow import shadow_enabled

    monkeypatch.delenv("YFINANCE_SHADOW", raising=False)
    assert shadow_enabled() is False
    monkeypatch.setenv("YFINANCE_SHADOW", "1")
    assert shadow_enabled() is True


def test_yfinance_financials_and_estimates_become_evidence():
    from api.evidence import sources_for_tool

    p = provider()
    financials = p.financials("US.AAPL", "income", periods=2)
    financials["retrieved_at"] = "2026-10-29T00:00:00+00:00"
    evidence = sources_for_tool({"tool": "get_company_fundamentals", "data": financials, "retrieved_at": financials["retrieved_at"]})
    assert evidence[0].source == "yfinance"
    # Currency comes from the provider's statement-currency field, never from the listing.
    assert evidence[0].currency == "CNY"
    assert evidence[0].record_key
    assert any("Revenue=100.0" in excerpt["text"] for excerpt in evidence[0].excerpts)

    estimates = p.estimates("US.AAPL")
    estimates["retrieved_at"] = "2026-10-29T00:00:00+00:00"
    evidence = sources_for_tool({"tool": "get_analyst_estimates", "data": estimates, "retrieved_at": estimates["retrieved_at"]})
    assert evidence[0].source == "yfinance"
    assert "mean=130.0" in evidence[0].excerpts[0]["text"]


def test_us_hk_fundamentals_flag_can_be_disabled(monkeypatch):
    from api.datasources.flags import yfinance_us_hk_fundamentals

    monkeypatch.delenv("YFINANCE_US_HK_FUNDAMENTALS", raising=False)
    assert yfinance_us_hk_fundamentals() is True
    monkeypatch.setenv("YFINANCE_US_HK_FUNDAMENTALS", "0")
    assert yfinance_us_hk_fundamentals() is False


def test_provider_registry_declares_fuyao_exception_and_keeps_registration_closed():
    from api.datasources.registry import FROZEN_AFTER, PROVIDER_REGISTRY, registered_provider

    assert FROZEN_AFTER == "fuyao"
    assert PROVIDER_REGISTRY["fuyao"]["markets"] == ["cn"]
    assert PROVIDER_REGISTRY["yfinance"]["status"] == "newest"
    assert registered_provider("yfinance") is True
    assert registered_provider("fuyao") is True
    assert registered_provider("some-new-vendor") is False


def test_calendar_exposes_next_earnings_date():
    data = provider().calendar("US.AAPL")
    assert data["earnings_date"] == "2026-11-05"
    assert data["provider"] == "yfinance"
    assert data["is_estimate"] is True


def test_financial_reporting_currency_is_not_guessed_from_listing_market():
    financials = provider().financials('00700.HK', 'income')
    # The HK listing says HKD; the provider's statement currency says CNY.
    assert financials['currency'] == 'CNY'
    assert financials['currency'] != 'HKD'
    assert financials['currency_basis'] == 'provider_financial_currency'
    assert not financials['data_gaps']
