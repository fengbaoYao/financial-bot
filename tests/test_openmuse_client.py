"""OpenMuse health checks stay opt-in and reveal only the health contract."""

from types import SimpleNamespace

from api.financial_bot.openmuse_client import OpenMuseClient


def test_health_check_is_disabled_by_default_without_network_call(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("disabled client must not make a request")

    monkeypatch.setattr("api.financial_bot.openmuse_client.requests.Session.get", forbidden)

    result = OpenMuseClient(base_url="http://127.0.0.1:8081").check_health()

    assert result.available is False
    assert result.error == "disabled"


def test_health_check_calls_only_health_endpoint_with_bounded_timeout_and_no_redirects(monkeypatch):
    captured = {}

    def fake_get(session, url, **kwargs):
        captured.update(url=url, trust_env=session.trust_env, **kwargs)
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "ok": True,
                "mode": "live",
                "agentConfigured": True,
                "browserConfigured": False,
            },
        )

    monkeypatch.setattr("api.financial_bot.openmuse_client.requests.Session.get", fake_get)

    result = OpenMuseClient(enabled=True, base_url="http://127.0.0.1:8081/").check_health()

    assert captured["url"] == "http://127.0.0.1:8081/api/health"
    assert captured["timeout"] == (2.0, 2.0)
    assert captured["allow_redirects"] is False
    assert captured["trust_env"] is False
    assert "headers" not in captured
    assert result.available is True
    assert result.mode == "live"
    assert result.agent_configured is True
    assert result.browser_configured is False
    assert not hasattr(result, "token")


def test_health_check_rejects_public_hosts_before_request(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("public targets must not be contacted")

    monkeypatch.setattr("api.financial_bot.openmuse_client.requests.Session.get", forbidden)

    result = OpenMuseClient(enabled=True, base_url="https://example.com").check_health()

    assert result.available is False
    assert result.error == "invalid_configuration"


def test_health_check_rejects_redirects_without_following_them(monkeypatch):
    monkeypatch.setattr(
        "api.financial_bot.openmuse_client.requests.Session.get",
        lambda *args, **kwargs: SimpleNamespace(status_code=302),
    )

    result = OpenMuseClient(enabled=True, base_url="https://192.168.1.20:8081").check_health()

    assert result.available is False
    assert result.error == "http_error"


def test_health_check_redacts_transport_error_details(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("authorization=super-secret token=do-not-return")

    monkeypatch.setattr("api.financial_bot.openmuse_client.requests.Session.get", fail)

    result = OpenMuseClient(enabled=True, base_url="http://localhost:8081").check_health()

    assert result.available is False
    assert result.error == "unavailable"
    assert "super-secret" not in repr(result)
    assert "do-not-return" not in repr(result)


def test_health_check_rejects_malformed_health_payload(monkeypatch):
    monkeypatch.setattr(
        "api.financial_bot.openmuse_client.requests.Session.get",
        lambda *args, **kwargs: SimpleNamespace(
            status_code=200,
            json=lambda: {"ok": True, "mode": "live", "token": "must-not-leak"},
        ),
    )

    result = OpenMuseClient(enabled=True, base_url="http://localhost:8081").check_health()

    assert result.available is False
    assert result.error == "invalid_response"
    assert "must-not-leak" not in repr(result)


def test_health_check_contains_unexpected_types_in_remote_payload(monkeypatch):
    monkeypatch.setattr(
        "api.financial_bot.openmuse_client.requests.Session.get",
        lambda *args, **kwargs: SimpleNamespace(
            status_code=200,
            json=lambda: {
                "ok": True,
                "mode": [],
                "agentConfigured": True,
                "browserConfigured": False,
            },
        ),
    )

    result = OpenMuseClient(enabled=True, base_url="http://localhost:8081").check_health()

    assert result.available is False
    assert result.error == "invalid_response"


def test_health_check_allows_private_ipv6_deployment(monkeypatch):
    monkeypatch.setattr(
        "api.financial_bot.openmuse_client.requests.Session.get",
        lambda *args, **kwargs: SimpleNamespace(
            status_code=200,
            json=lambda: {"ok": True, "mode": "sample", "agentConfigured": False, "browserConfigured": False},
        ),
    )

    result = OpenMuseClient(enabled=True, base_url="http://[fd00::12]:8081").check_health()

    assert result.available is True


def test_health_check_rejects_subdomains_and_unspecified_addresses(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("untrusted targets must not be contacted")

    monkeypatch.setattr("api.financial_bot.openmuse_client.requests.Session.get", forbidden)

    for url in ("http://untrusted.localhost:8081", "http://0.0.0.0:8081"):
        result = OpenMuseClient(enabled=True, base_url=url).check_health()
        assert result.available is False
        assert result.error == "invalid_configuration"


def test_environment_configuration_defaults_to_disabled(monkeypatch):
    monkeypatch.delenv("OPENMUSE_ENABLED", raising=False)
    monkeypatch.setenv("OPENMUSE_BASE_URL", "http://127.0.0.1:8081")
    monkeypatch.setenv("OPENMUSE_TIMEOUT_SECONDS", "200")

    client = OpenMuseClient.from_env()

    assert client.enabled is False
    assert client.timeout == (10.0, 10.0)
