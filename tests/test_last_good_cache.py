import json

import pandas as pd

from api.datasources import last_good
from ashare.data import DataFetcher


def _bars():
    return [
        {"date": "2026-10-29", "open": 1.0, "high": 1.2, "low": 0.9, "close": 1.1, "volume": 100},
        {"date": "2026-10-30", "open": 1.1, "high": 1.3, "low": 1.0, "close": 1.2, "volume": 120},
    ]


def test_last_good_round_trip_is_marked_stale(tmp_path, monkeypatch):
    monkeypatch.setenv("LAST_GOOD_CACHE_PATH", str(tmp_path / "cache.json"))
    last_good.put(
        "price_history", "sh600519:1d", {"bars": _bars()},
        provider="fuyao", adjustment="qfq", data_as_of="2026-10-30",
    )
    entry = last_good.get("price_history", "sh600519:1d")
    assert entry is not None
    assert entry["stale"] is True
    assert entry["provider"] == "fuyao"
    assert entry["data_as_of"] == "2026-10-30"
    assert json.loads((tmp_path / "cache.json").read_text())["entries"]


def test_last_good_expires(tmp_path, monkeypatch):
    monkeypatch.setenv("LAST_GOOD_CACHE_PATH", str(tmp_path / "cache.json"))
    last_good.put("price_history", "sh600519:1d", {"bars": _bars()}, provider="fuyao", data_as_of="2020-01-01")
    path = tmp_path / "cache.json"
    payload = json.loads(path.read_text())
    key = "price_history:sh600519:1d"
    payload["entries"][key]["stored_ts"] = 0
    path.write_text(json.dumps(payload))
    assert last_good.get("price_history", "sh600519:1d", max_age_hours=1) is None


def test_data_fetcher_falls_back_to_stale_price(tmp_path, monkeypatch):
    monkeypatch.setenv("LAST_GOOD_CACHE_PATH", str(tmp_path / "cache.json"))
    last_good.put(
        "price_history", "sh600519:1d", {"bars": _bars()},
        provider="fuyao", adjustment="qfq", data_as_of="2026-10-30",
    )
    fetcher = DataFetcher()
    monkeypatch.setattr(fetcher, "_fetch_a_share_fuyao", lambda *args: None)
    monkeypatch.setattr(fetcher, "_fetch_a_share", lambda *args: None)
    monkeypatch.setattr(fetcher, "_fetch_stock_data_akshare", lambda *args: None)
    monkeypatch.setenv("YFINANCE_A_SHARE_FALLBACK", "0")
    frame = fetcher.fetch_stock_data("sh600519", count=2)
    assert frame is not None
    assert len(frame) == 2
    assert fetcher.provider_info("sh600519")["provider"].startswith("last_good_cache(")
    assert fetcher.freshness_info("sh600519")["stale"] is True
    assert fetcher.freshness_info("sh600519")["data_as_of"] == "2026-10-30"


def test_financial_last_good_is_used_when_live_fails(tmp_path, monkeypatch):
    from api.local_research import providers

    monkeypatch.setenv("LAST_GOOD_CACHE_PATH", str(tmp_path / "cache.json"))
    live = {
        "symbol": "sh600519", "market": "CN", "provider": "fuyao",
        "endpoint": "fuyao", "statement": "income", "statement_label": "利润表",
        "data_as_of": "2025-12-31", "retrieved_at": "2026-10-30T00:00:00+08:00",
        "currency": "CNY", "adjustment": "not_applicable",
        "periods": [{"period": "2025-12-31", "items": {"revenue": 100.0}}], "data_gaps": [],
    }
    monkeypatch.setattr(providers, "_financial_statements_live", lambda *a, **k: live)
    first = providers.financial_statements("sh600519", "income", 1)
    assert first["stale"] if "stale" in first else True
    monkeypatch.setattr(providers, "_financial_statements_live", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    stale = providers.financial_statements("sh600519", "income", 1)
    assert stale["stale"] is True
    assert stale["provider"] == "fuyao"
    assert stale["periods"][0]["items"]["revenue"] == 100.0
    assert any("last-good" in gap for gap in stale["data_gaps"])


def test_valuation_last_good_is_used_when_provider_is_unavailable(tmp_path, monkeypatch):
    from api.datasources import fuyao_provider

    monkeypatch.setenv("LAST_GOOD_CACHE_PATH", str(tmp_path / "cache.json"))
    cached = {
        "provider": "fuyao", "endpoint": "fuyao", "items": [{"thscode": "600519.SH", "pe_ttm": 21.0}],
        "as_of": "2026-10-30", "retrieved_at": "2026-10-30T00:00:00+08:00",
        "raw_sha256": "x", "data_gaps": [],
    }
    last_good.put("valuations", "600519.SH", cached, provider="fuyao", data_as_of="2026-10-30")
    monkeypatch.setattr(fuyao_provider, "_PROVIDER", fuyao_provider.FuyaoProvider())
    monkeypatch.delenv("FUYAO_API_KEY", raising=False)
    stale = fuyao_provider.get_fuyao_provider().valuations(["sh600519"])
    assert stale["stale"] is True
    assert stale["items"][0]["pe_ttm"] == 21.0
    assert any("last-good" in gap for gap in stale["data_gaps"])


def test_invalid_financial_cache_is_not_returned(tmp_path, monkeypatch):
    import pytest
    from api.local_research import providers

    monkeypatch.setenv("LAST_GOOD_CACHE_PATH", str(tmp_path / "cache.json"))
    bad = {
        "provider": "fuyao", "periods": [{"period": "bad", "items": {}}],
        "data_as_of": "2025-12-31", "data_gaps": [],
    }
    last_good.put("financial_statements", "sh600519:income", bad, provider="fuyao", data_as_of="2025-12-31")
    monkeypatch.setattr(providers, "_financial_statements_live", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(RuntimeError, match="down"):
        providers.financial_statements("sh600519", "income", 1)
