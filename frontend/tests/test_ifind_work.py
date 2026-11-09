"""Work-path helpers must reuse IfindProvider and stay inert without a token."""

from __future__ import annotations

import api.datasources.ifind_provider as provider_mod
from api.datasources import ifind_work


def test_try_quote_uses_shared_provider(monkeypatch):
    monkeypatch.setenv("IFIND_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("IFIND_ENABLED", "1")
    monkeypatch.setenv("IFIND_PRIMARY", "1")
    calls = []

    def fake_quote(self, code):
        calls.append(code)
        return {
            "provider": "ifind",
            "thscode": "600519.SH",
            "price": 100.0,
            "change": 1.0,
            "change_pct": 1.0,
            "volume": 1.0,
            "data_as_of": "2026-11-03 10:00:00",
            "price_status": "delayed_or_snapshot",
        }

    monkeypatch.setattr(provider_mod.IfindProvider, "quote", fake_quote)
    result = ifind_work.try_quote("sh600519")
    assert result["provider"] == "ifind"
    assert result["price"] == 100.0
    assert calls == ["sh600519"]


def test_try_quote_inert_without_token(monkeypatch):
    monkeypatch.delenv("IFIND_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("ifind_api_access_token", raising=False)
    monkeypatch.setenv("IFIND_PRIMARY", "1")
    assert ifind_work.try_quote("sh600519") is None


def test_announcements_as_fetched_news(monkeypatch):
    monkeypatch.setenv("IFIND_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("IFIND_PRIMARY", "1")

    def fake_announcements(self, code, *, begin, end, limit=8):
        return {
            "provider": "ifind",
            "endpoint": "quantapi.51ifind.com/api/v1/report_query",
            "announcements": [
                {
                    "title": "测试公告",
                    "date": "2026-11-03",
                    "time": "2026-11-03 09:00:00",
                    "url": "https://example.com/a",
                    "record_key": "1",
                }
            ],
        }

    monkeypatch.setattr(provider_mod.IfindProvider, "announcements", fake_announcements)
    rows = ifind_work.announcements_as_fetched_news("sh600519", limit=3)
    assert len(rows) == 1
    assert rows[0]["source"] == "iFinD公告"
    assert rows[0]["relation_type"] == "direct"
    assert rows[0]["provider"] == "ifind"


def test_announcements_sanitize_noisy_titles(monkeypatch):
    monkeypatch.setenv("IFIND_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("IFIND_PRIMARY", "1")

    def fake_announcements(self, code, *, begin, end, limit=8):
        return {
            "provider": "ifind",
            "endpoint": "quantapi.51ifind.com/api/v1/report_query",
            "announcements": [
                {
                    "title": (
                        "00700, 05093, 05094, 05542, TENCENT N2801, "
                        "Publication of Pricing of US$1,300,000,000 6.10% Senior Notes Due 2036"
                    ),
                    "date": "2026-10-22",
                    "time": "2026-10-22 09:00:00",
                    "url": "https://example.com/b",
                    "record_key": "2",
                }
            ],
        }

    monkeypatch.setattr(provider_mod.IfindProvider, "announcements", fake_announcements)
    # Provider path also sanitizes; fake returns pre-provider shape used by try_announcements.
    # announcements_as_fetched_news sanitizes again.
    rows = ifind_work.announcements_as_fetched_news("00700.HK", limit=3)
    assert len(rows) == 1
    assert "05093" not in rows[0]["title"]
    assert "票据" in rows[0]["title"] or "Senior Notes" in rows[0]["title"]
