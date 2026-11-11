from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import api.financial_bot.openmuse_client as openmuse_client
import api.financial_bot.routes as routes
from api.financial_bot.auth import LOCAL_MODE, LOCAL_OWNER_ID, OwnerContext
from api.financial_bot.config import BotConfig
from api.financial_bot.db import Database
from api.financial_bot.executor import TaskExecutor
from api.financial_bot.runtime import configure, reset
from api.financial_bot.store import FinancialBotStore
from api.main import app

client = TestClient(app)


@pytest.fixture()
def bot_runtime(tmp_path, monkeypatch):
    db = Database(sqlite_path=tmp_path / "bot.sqlite3")
    db.initialize()
    store = FinancialBotStore(db)
    configure(
        config=BotConfig(
            data_dir=tmp_path,
            daily_task_limit=20,
            daily_token_limit=1000,
            backup_dir=None,
            third_party_retention_verified=False,
        ),
        store=store,
        executor=TaskExecutor(store),
    )
    import api.main as main

    monkeypatch.setattr(
        main.settings_store,
        "build_runtime_config",
        lambda *args, **kwargs: SimpleNamespace(llm_api_key=None, llm_model="fixture-model"),
    )
    yield
    reset()


def set_owner(monkeypatch, owner):
    monkeypatch.setattr(routes, "resolve_owner", lambda request, config: owner)


def set_health(monkeypatch, health):
    class FakeClient:
        def check_health(self):
            return health

    monkeypatch.setattr(openmuse_client.OpenMuseClient, "from_env", classmethod(lambda cls: FakeClient()))


def test_local_status_maps_disabled_and_configuration_errors_without_connection_details(bot_runtime, monkeypatch):
    set_owner(monkeypatch, OwnerContext(owner_id=LOCAL_OWNER_ID, mode=LOCAL_MODE))
    monkeypatch.setenv("OPENMUSE_BASE_URL", "http://127.0.0.1:8787")
    monkeypatch.setenv("OPENMUSE_ACCESS_KEY", "secret-openmuse-key")

    set_health(monkeypatch, openmuse_client.OpenMuseHealth(available=False, error="disabled"))
    disabled = client.get("/api/bot/status").json()["openmuse"]
    assert disabled["status"] == "disabled"

    set_health(monkeypatch, openmuse_client.OpenMuseHealth(available=False, error="invalid_configuration"))
    invalid = client.get("/api/bot/status").json()["openmuse"]
    assert invalid["status"] == "configuration_error"
    assert "secret-openmuse-key" not in repr(disabled)
    assert "secret-openmuse-key" not in repr(invalid)
    assert "127.0.0.1" not in repr(disabled)
    assert "127.0.0.1" not in repr(invalid)


def test_local_status_distinguishes_unreachable_from_connected_health_only(bot_runtime, monkeypatch):
    set_owner(monkeypatch, OwnerContext(owner_id=LOCAL_OWNER_ID, mode=LOCAL_MODE))
    monkeypatch.setenv("OPENMUSE_SHARED_RESEARCH_ENABLED", "true")
    set_health(monkeypatch, openmuse_client.OpenMuseHealth(available=False, error="unavailable"))
    unreachable = client.get("/api/bot/status").json()["openmuse"]
    assert unreachable["status"] == "unreachable"

    set_health(
        monkeypatch,
        openmuse_client.OpenMuseHealth(
            available=True,
            mode="live",
            agent_configured=False,
            browser_configured=True,
            shared_research_enabled=True,
        ),
    )
    connected = client.get("/api/bot/status").json()["openmuse"]
    assert connected["status"] == "connected"
    assert connected["mode"] == "live"
    assert connected["agent_configured"] is False
    assert connected["browser_configured"] is True
    assert connected["shared_research_enabled"] is True
    assert "127.0.0.1" not in repr(connected)
    assert "secret" not in repr(connected).lower()


def test_shared_research_status_requires_upstream_and_local_flags(bot_runtime, monkeypatch):
    set_owner(monkeypatch, OwnerContext(owner_id=LOCAL_OWNER_ID, mode=LOCAL_MODE))
    monkeypatch.setenv("OPENMUSE_SHARED_RESEARCH_ENABLED", "true")
    set_health(monkeypatch, openmuse_client.OpenMuseHealth(
        available=True,
        mode="live",
        shared_research_enabled=False,
    ))
    upstream_disabled = client.get("/api/bot/status").json()["openmuse"]
    assert upstream_disabled["status"] == "connected"
    assert upstream_disabled["shared_research_enabled"] is False

    set_health(monkeypatch, openmuse_client.OpenMuseHealth(
        available=True,
        mode="live",
        shared_research_enabled=True,
    ))
    monkeypatch.setenv("OPENMUSE_SHARED_RESEARCH_ENABLED", "false")
    locally_disabled = client.get("/api/bot/status").json()["openmuse"]
    assert locally_disabled["status"] == "connected"
    assert locally_disabled["shared_research_enabled"] is False


def test_hosted_owner_never_contacts_openmuse_and_reports_unavailable(bot_runtime, monkeypatch):
    set_owner(
        monkeypatch,
        OwnerContext(owner_id="hosted-user", mode="supabase_authenticated"),
    )

    def forbidden(_cls):
        raise AssertionError("hosted status must never call OpenMuse")

    monkeypatch.setattr(openmuse_client.OpenMuseClient, "from_env", classmethod(forbidden))
    status = client.get("/api/bot/status").json()["openmuse"]
    assert status["status"] == "unavailable"
    assert status["reason"] == "local_single_user_only"

