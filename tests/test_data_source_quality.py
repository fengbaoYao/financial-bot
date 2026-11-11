from api.datasources import health, quality
from api.datasources.validation import validate_financial_periods, validate_price_bars


def test_capability_priority_puts_ifind_first_and_keeps_the_fallbacks():
    # 2026-11-02 policy: iFinD is the highest-priority source for everything it
    # is licensed to serve; the previous primaries become fallbacks.
    assert quality.priority("a_share_history")[:3] == ["ifind", "fuyao", "sina"]
    assert quality.priority("a_share_quote")[:2] == ["ifind", "fuyao"]
    assert quality.priority("a_share_financials")[:2] == ["ifind", "fuyao"]
    # Official disclosure stays available right behind iFinD for cross-checks.
    assert quality.priority("a_share_announcements")[:2] == ["ifind", "cninfo"]
    assert quality.priority("us_hk_history")[0] == "ifind"
    assert quality.priority("filings")[0] == "sec"
    assert quality.priority("web_search")[0] == "brave"


def test_quality_scores_reflect_observed_excellence_and_fallbacks():
    assert quality.tier("fuyao", "a_share_history") == "primary"
    assert quality.score("fuyao", "a_share_history") > quality.score("sina", "a_share_history")
    assert quality.score("fuyao", "a_share_history") > quality.score("akshare", "a_share_history")
    assert quality.tier("cninfo", "a_share_announcements") == "official"
    assert quality.tier("duckduckgo", "web_search") == "discovery"
    assert quality.tier("cni", "a_share_index_constituents") == "fallback"


def test_health_opens_after_three_failures_and_success_closes():
    health.reset()
    assert health.is_available("fuyao", "history")
    health.record_failure("fuyao", "history", "timeout")
    health.record_failure("fuyao", "history", "timeout")
    assert health.is_available("fuyao", "history")
    health.record_failure("fuyao", "history", "timeout")
    assert health.is_available("fuyao", "history") is False
    assert health.snapshot()[0]["open"] is True
    health.record_success("fuyao", "history")
    assert health.is_available("fuyao", "history") is True
    health.reset()


def test_price_bar_gate_rejects_structural_breakage():
    assert validate_price_bars([{
        "date": "2026-10-30", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10,
    }]) == []
    errors = validate_price_bars([{
        "date": "2026-10-30", "open": 2, "high": 1, "low": 0.5, "close": 1.5, "volume": -1,
    }])
    assert errors


def test_financial_period_gate_rejects_empty_or_non_numeric_periods():
    assert validate_financial_periods([{"period": "2025-12-31", "items": {"revenue": 100}}]) == []
    assert validate_financial_periods([{"period": "2025-12-31", "items": {}}])
    assert validate_financial_periods([{"period": "bad", "items": {"revenue": 100}}])
