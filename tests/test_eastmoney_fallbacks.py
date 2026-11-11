import pandas as pd
import pytest

from api.datasources import eastmoney_fundamentals
from api.local_research import providers


def test_eastmoney_us_financials_normalizes_periods(monkeypatch):
    frame = pd.DataFrame([
        {"REPORT_DATE": "2025-09-27 00:00:00", "ITEM_NAME": "主营收入", "AMOUNT": 100.0},
        {"REPORT_DATE": "2025-09-27 00:00:00", "ITEM_NAME": "净利润", "AMOUNT": 20.0},
        {"REPORT_DATE": "2024-09-28 00:00:00", "ITEM_NAME": "主营收入", "AMOUNT": 90.0},
    ])

    class FakeAk:
        @staticmethod
        def stock_financial_us_report_em(**kwargs):
            return frame

    monkeypatch.setitem(__import__("sys").modules, "akshare", FakeAk)
    data = eastmoney_fundamentals.eastmoney_financials("US.AAPL", "income", False, 2)
    assert data["provider"] == "eastmoney"
    assert data["periods"][0]["period"] == "2025-09-27"
    assert data["periods"][0]["values"]["主营收入"] == 100.0
    assert len(data["periods"]) == 2


def test_eastmoney_hk_financials_uses_hk_frame(monkeypatch):
    frame = pd.DataFrame([
        {"REPORT_DATE": "2025-12-31 00:00:00", "STD_ITEM_NAME": "营业额", "AMOUNT": 200.0},
    ])

    class FakeAk:
        @staticmethod
        def stock_financial_hk_report_em(**kwargs):
            return frame

    monkeypatch.setitem(__import__("sys").modules, "akshare", FakeAk)
    data = eastmoney_fundamentals.eastmoney_financials("00700.HK", "income", False, 1)
    assert data["market"] == "hk"
    assert data["currency"] == "HKD"
    assert data["periods"][0]["values"]["营业额"] == 200.0


def test_consensus_falls_back_to_eastmoney_and_keeps_provider(monkeypatch):
    monkeypatch.setattr(providers, "_consensus_estimates_ths", lambda *a: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(providers, "_eastmoney_consensus_estimates", lambda digits: {
        "provider": "eastmoney_forecast", "years": [{"period": "FY2026", "mean": 1.2, "min": None, "max": None, "count": 5}],
        "data_gaps": ["secondary"], "data_as_of": "2026-10-30",
    })
    data = providers.consensus_estimates("sh600519")
    assert data["provider"] == "eastmoney_forecast"
