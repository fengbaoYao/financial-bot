import pandas as pd

import ashare.data as data_module
from ashare.data import DataFetcher
from api.datasources import fuyao_provider as provider_module
from api.local_research import providers as local_providers


class FakeFuyao:
    configured = True

    def history(self, code, start_ms, end_ms, **kwargs):
        return {
            "bars": [
                {"date": "2026-10-20", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
                {"date": "2026-10-21", "open": 2, "high": 3, "low": 1.5, "close": 2.5, "volume": 20},
            ],
            "adjustment": "qfq",
        }

    def financial_statements(self, code, statement, periods):
        return {
            "provider": "fuyao",
            "symbol": code,
            "periods": [{"period": "2024-12-31", "items": {"revenue": 100}}],
            "data_gaps": [],
        }


def test_data_fetcher_prefers_configured_fuyao(monkeypatch):
    monkeypatch.setattr(provider_module, "_PROVIDER", FakeFuyao())
    monkeypatch.setattr(data_module, "as_api", None)
    fetcher = DataFetcher()
    frame = fetcher.fetch_stock_data("sh600519", count=2)
    assert frame is not None
    assert fetcher.provider_info("sh600519") == {"provider": "fuyao", "adjustment": "qfq"}
    assert list(frame["close"]) == [1.5, 2.5]


def test_local_financials_use_fuyao_when_configured(monkeypatch):
    monkeypatch.setattr(provider_module, "_PROVIDER", FakeFuyao())
    monkeypatch.setattr("api.datasources.flags.fuyao_financials", lambda: True)
    data = local_providers.financial_statements("sh600519", "income", 1)
    assert data["provider"] == "fuyao"
    assert data["periods"][0]["items"]["revenue"] == 100
