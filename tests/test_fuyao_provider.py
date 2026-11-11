import json

import pytest

from api.datasources.fuyao_provider import (
    FuyaoError,
    FuyaoNotConfigured,
    FuyaoProvider,
    FuyaoUnavailable,
    to_fuyao_thscode,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload
        self.content = json.dumps(payload).encode("utf-8")
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.payload)


def envelope(data):
    return {"code": 0, "message": "success", "request_id": "test-request", "data": data}


def test_fuyao_symbol_mapping():
    assert to_fuyao_thscode("sh600519") == "600519.SH"
    assert to_fuyao_thscode("600519.SH") == "600519.SH"
    assert to_fuyao_thscode("sz000001") == "000001.SZ"
    assert to_fuyao_thscode("000001") == "000001.SZ"
    assert to_fuyao_thscode("830799") == "830799.BJ"
    with pytest.raises(Exception):
        to_fuyao_thscode("HK.00700")


def test_fuyao_is_inert_without_key():
    provider = FuyaoProvider(api_key="", session=FakeSession(envelope({"item": []})))
    assert provider.configured is False
    with pytest.raises(FuyaoNotConfigured):
        provider.quote("sh600519")


def test_fuyao_quote_normalizes_and_keeps_key_header():
    payload = envelope({
        "timestamp": 1784275991000,
        "item": [{
            "thscode": "600519.SH",
            "ticker": "600519",
            "last_price": 1277.8,
            "price_change": 21.8,
            "price_change_ratio_pct": 1.735669,
            "open_price": 1252.08,
            "high_price": 1282,
            "low_price": 1250.21,
            "prev_price": 1256,
            "volume": 3098875,
            "turnover": 3937375200,
        }],
    })
    session = FakeSession(payload)
    provider = FuyaoProvider(api_key="test-key", session=session)
    data = provider.quote("600519.SH")
    assert data["price"] == 1277.8
    assert data["price_change_ratio_pct"] == 1.735669
    assert data["currency"] == "CNY"
    assert data["provider"] == "fuyao"
    assert session.calls[0][1]["headers"]["X-api-key"] == "test-key"
    assert "test-key" not in repr(data)
    # The same request is served from the TTL cache.
    provider.quote("600519.SH")
    assert len(session.calls) == 1


def test_fuyao_history_normalizes_bars():
    payload = envelope({
        "timestamp": 1747584000000,
        "item": [{
            "date_ms": 1747526400000,
            "open_price": 1611.602,
            "high_price": 1626.602,
            "low_price": 1601.722,
            "close_price": 1602.612,
            "volume": 3142572.0,
            "turnover": 5401389334.87,
        }],
    })
    provider = FuyaoProvider(api_key="test", session=FakeSession(payload))
    data = provider.history("sh600519", 1747440000000, 1747699200000)
    assert data["bars"][0]["date"] == "2025-05-18"
    assert data["bars"][0]["close"] == 1602.612
    assert data["adjustment"] == "qfq"


def test_fuyao_financials_preserve_periods_and_map_fields():
    payload = envelope({
        "timestamp": 1735574400000,
        "item": [{
            "thscode": "600519.SH",
            "period_end_ms": 1735574400000,
            "operating_income": 174144000000,
            "operating_profit": 124000000000,
            "net_profit": 93000000000,
            "parent_holder_net_profit": 86000000000,
            "basic_eps": 68.5,
        }],
    })
    provider = FuyaoProvider(api_key="test", session=FakeSession(payload))
    data = provider.financial_statements("sh600519", "income", 1)
    assert data["provider"] == "fuyao"
    assert data["periods"][0]["period"] == "2024-12-31"
    assert data["periods"][0]["items"]["revenue"] == 174144000000
    assert data["periods"][0]["items"]["eps_basic"] == 68.5
    assert data["data_gaps"] == []


def test_fuyao_valuations_snapshot():
    payload = envelope({
        "timestamp": 1784736000000,
        "total": 1,
        "item": [{
            "thscode": "600519.SH",
            "ticker": "600519",
            "name": "贵州茅台",
            "pe_ttm": 21.3567,
            "pe_mrq": 20.8841,
            "pb_mrq": 7.1532,
            "ps_ttm": 10.3284,
            "pcf_ttm": 19.7716,
        }],
    })
    provider = FuyaoProvider(api_key="test", session=FakeSession(payload))
    data = provider.valuations(["sh600519"])
    assert data["items"][0]["name"] == "贵州茅台"
    assert data["items"][0]["pb_mrq"] == 7.1532


def test_fuyao_business_error_and_rate_limit():
    provider = FuyaoProvider(
        api_key="test",
        session=FakeSession({"code": 2001, "message": "unauthorized", "request_id": "r"}),
    )
    with pytest.raises(FuyaoError) as exc:
        provider.quote("600519.SH")
    assert exc.value.code == 2001

    class RateSession:
        def get(self, *args, **kwargs):
            return FakeResponse({"code": 4001, "message": "rate limited"}, status_code=429)

    with pytest.raises(FuyaoUnavailable):
        FuyaoProvider(api_key="test", session=RateSession()).quote("600519.SH")


def test_fuyao_registry_declares_a_share_capabilities():
    from api.datasources.registry import FROZEN_AFTER, PROVIDER_REGISTRY, registered_provider

    assert FROZEN_AFTER == "fuyao"
    assert registered_provider("fuyao") is True
    assert PROVIDER_REGISTRY["fuyao"]["markets"] == ["cn"]
    assert "valuations" in PROVIDER_REGISTRY["fuyao"]["capabilities"]
