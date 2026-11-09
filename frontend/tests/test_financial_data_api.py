from fastapi.testclient import TestClient
import pytest


def test_financial_research_bridge_requires_loopback_token(monkeypatch):
    monkeypatch.delenv("FINANCIAL_RESEARCH_SHARED_TOKEN", raising=False)
    from api.main import app

    response = TestClient(app, client=("127.0.0.1", 12345)).post(
        "/api/financial-research/query",
        json={"query": "茅台最新行情"},
        headers={"Authorization": "Bearer anything"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "financial_research_access_denied"}


def test_financial_research_bridge_returns_ifind_data_without_fallback(monkeypatch):
    monkeypatch.setenv("FINANCIAL_RESEARCH_SHARED_TOKEN", "r" * 48)
    monkeypatch.setattr("api.datasources.flags.ifind_enabled", lambda: True)
    monkeypatch.setattr(
        "api.datasources.ifind_provider.IfindProvider.smart_query",
        lambda self, query: {"provider": "ifind", "data": {"tables": [{"thscode": "sh600519", "table": {"最新价": [1680]}}]}},
    )
    monkeypatch.setattr(
        "api.main.stock_service.get_stock_analysis",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fallback must not run")),
    )
    from api.main import app

    response = TestClient(app, client=("127.0.0.1", 12345)).post(
        "/api/financial-research/query",
        json={"query": "贵州茅台最新行情"},
        headers={"Authorization": f"Bearer {'r' * 48}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "partial"
    assert data["primary_provider"] == "iFinD"
    assert data["provider"] == "iFinD"
    assert data["data_as_of"] is None
    assert data["records"][0]["data"]["tables"][0]["table"]["最新价"] == [1680]
    assert data["data_gaps"] == ["iFinD returned data, but its observation time could not be verified."]
    assert data["attempts"] == [{"provider": "iFinD", "status": "ok", "reason": None}]


def test_financial_research_bridge_accepts_bounded_openmuse_context(monkeypatch):
    monkeypatch.setenv("FINANCIAL_RESEARCH_SHARED_TOKEN", "l" * 48)
    monkeypatch.setattr("api.datasources.flags.ifind_enabled", lambda: True)
    monkeypatch.setattr(
        "api.datasources.ifind_provider.IfindProvider.smart_query",
        lambda self, query: {"provider": "ifind", "data": {"tables": [{"thscode": "sh600519", "table": {"收入": [1]}}]}},
    )
    from api.main import app

    query = "贵州茅台" + "盈利能力分析" * 800
    response = TestClient(app, client=("127.0.0.1", 12345)).post(
        "/api/financial-research/query",
        json={"query": query},
        headers={"Authorization": f"Bearer {'l' * 48}"},
    )

    assert response.status_code == 200
    assert len(response.json()["query"]) > 4000


@pytest.mark.parametrize(
    ("quote_provider", "expected_status", "expected_provider"),
    [("tencent", "partial", "tencent"), ("unknown", "unavailable", None)],
)
def test_financial_research_bridge_falls_back_and_keeps_provenance(monkeypatch, quote_provider, expected_status, expected_provider):
    monkeypatch.setenv("FINANCIAL_RESEARCH_SHARED_TOKEN", "s" * 48)
    monkeypatch.setattr("api.datasources.flags.ifind_enabled", lambda: True)
    monkeypatch.setattr(
        "api.datasources.ifind_provider.IfindProvider.smart_query",
        lambda self, query: (_ for _ in ()).throw(RuntimeError("provider detail must not leak")),
    )
    monkeypatch.setattr("ashare.search.stock_searcher.get_stock_info", lambda code: {"code": "sh600519", "name": "贵州茅台"})
    monkeypatch.setattr("ashare.search.stock_searcher.search_stocks", lambda *args, **kwargs: [{"code": "sh600519", "name": "贵州茅台"}])

    class FakeAnalysis:
        def model_dump(self, mode="json"):
            return {
                "stock_code": "sh600519",
                "quote": {"price": 1680},
                "chart_series": [],
                "metadata": {
                    "provider": "akshare",
                    "data_as_of": "2026-11-04",
                    "quote_provider": quote_provider,
                    "ifind_quote": {"provider": quote_provider, "data_as_of": None},
                },
            }

    monkeypatch.setattr("api.main.stock_service.get_stock_analysis", lambda *args, **kwargs: FakeAnalysis())
    from api.main import app

    response = TestClient(app, client=("127.0.0.1", 12345)).post(
        "/api/financial-research/query",
        json={"query": "贵州茅台最新行情"},
        headers={"Authorization": f"Bearer {'s' * 48}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == expected_status
    assert data["provider"] == expected_provider
    if expected_status == "unavailable":
        assert data["records"] == []
        assert data["data_gaps"]
        return
    assert data["data_as_of"] is None
    assert data["attempts"][0]["provider"] == "iFinD"
    assert data["attempts"][0]["status"] == "failed"
    assert data["attempts"][1]["provider"] == "tencent"
    assert "provider detail must not leak" not in response.text
    assert any("observation time" in gap for gap in data["data_gaps"])


@pytest.mark.parametrize(
    ("returned_code", "expected_reason"),
    [("00001.HK", "identity_mismatch"), (None, "identity_unverified")],
)
def test_financial_research_bridge_discards_unverified_ifind_security(monkeypatch, returned_code, expected_reason):
    monkeypatch.setenv("FINANCIAL_RESEARCH_SHARED_TOKEN", "t" * 48)
    monkeypatch.setattr("api.datasources.flags.ifind_enabled", lambda: True)
    returned = {"table": {"最新价": [300]}}
    if returned_code:
        returned["thscode"] = returned_code
    monkeypatch.setattr(
        "api.datasources.ifind_provider.IfindProvider.smart_query",
        lambda self, query: {"provider": "ifind", "data": {"tables": [returned]}},
    )
    monkeypatch.setattr("ashare.search.stock_searcher.get_stock_info", lambda code: None)
    monkeypatch.setattr("ashare.search.stock_searcher.search_stocks", lambda *args, **kwargs: [])
    from api.main import app

    response = TestClient(app, client=("127.0.0.1", 12345)).post(
        "/api/financial-research/query",
        json={"query": "请查 00700.HK 最新行情"},
        headers={"Authorization": f"Bearer {'t' * 48}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "unavailable"
    assert data["provider"] is None
    assert data["records"] == []
    assert data["attempts"][0]["reason"] == expected_reason
    assert any("different security" in gap or "without a verifiable security identifier" in gap for gap in data["data_gaps"])


def test_financial_research_bridge_normalizes_hong_kong_leading_zero(monkeypatch):
    monkeypatch.setenv("FINANCIAL_RESEARCH_SHARED_TOKEN", "u" * 48)
    monkeypatch.setattr("api.datasources.flags.ifind_enabled", lambda: True)
    monkeypatch.setattr(
        "api.datasources.ifind_provider.IfindProvider.smart_query",
        lambda self, query: {"provider": "ifind", "data": {"tables": [{"thscode": "0700.HK", "table": {"最新价": [300]}}]}},
    )
    monkeypatch.setattr(
        "api.main.stock_service.get_stock_analysis",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fallback must not run")),
    )
    from api.main import app

    response = TestClient(app, client=("127.0.0.1", 12345)).post(
        "/api/financial-research/query",
        json={"query": "请查 00700.HK 最新行情"},
        headers={"Authorization": f"Bearer {'u' * 48}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "partial"
    assert data["provider"] == "iFinD"
    assert data["attempts"][0]["status"] == "ok"


def test_financial_research_bridge_does_not_assume_fallback_request_code_is_verified(monkeypatch):
    monkeypatch.setenv("FINANCIAL_RESEARCH_SHARED_TOKEN", "v" * 48)
    monkeypatch.setattr("api.datasources.flags.ifind_enabled", lambda: False)
    monkeypatch.setattr("ashare.search.stock_searcher.get_stock_info", lambda code: {"code": "00700.HK", "name": "Tencent"})

    class MissingIdentityAnalysis:
        def model_dump(self, mode="json"):
            return {"quote": {"price": 300}, "chart_series": [], "metadata": {"provider": "tencent"}}

    monkeypatch.setattr("api.main.stock_service.get_stock_analysis", lambda *args, **kwargs: MissingIdentityAnalysis())
    from api.main import app

    response = TestClient(app, client=("127.0.0.1", 12345)).post(
        "/api/financial-research/query",
        json={"query": "请查 00700.HK 最新行情"},
        headers={"Authorization": f"Bearer {'v' * 48}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "unavailable"
    assert data["records"] == []
    assert data["attempts"][-1]["reason"] == "fallback_identity_unverified"
    assert any("fallback source did not return a verifiable security identifier" in gap for gap in data["data_gaps"])
