import sys
from types import SimpleNamespace

import pandas as pd
import pytest

import ashare.data as data_module
from api.services import NewsService, StockAnalysisService
from ashare.config import Config
from ashare.data import DataFetcher


def price_frame(columns=None) -> pd.DataFrame:
    index = pd.date_range("2026-10-20", periods=5, freq="D")
    return pd.DataFrame(
        {
            "open": [1, 2, 3, 4, 5],
            "high": [2, 3, 4, 5, 6],
            "low": [0.5, 1.5, 2.5, 3.5, 4.5],
            "close": [1.5, 2.5, 3.5, 4.5, 5.5],
            "volume": [10, 20, 30, 40, 50],
        },
        index=index,
    )


def yfinance_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": [1, 2, 3],
            "High": [2, 3, 4],
            "Low": [0.5, 1.5, 2.5],
            "Close": [1.5, 2.5, 3.5],
            "Volume": [10, 20, 30],
        },
        index=pd.date_range("2026-10-20", periods=3, freq="D"),
    )


def test_hk_provider_is_yahoo_not_akshare(monkeypatch):
    fake_yf = SimpleNamespace(download=lambda *args, **kwargs: yfinance_frame())
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)
    fetcher = DataFetcher()
    assert fetcher.fetch_stock_data("hk00700") is not None
    info = fetcher.provider_info("hk00700")
    assert info["provider"] == "yfinance(0700.HK)"
    assert info["adjustment"] == "auto_adjust"
    assert "akshare" not in info["provider"]


def test_us_provider_records_the_source_that_succeeded(monkeypatch):
    fake_yf = SimpleNamespace(download=lambda *args, **kwargs: yfinance_frame())
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)
    fetcher = DataFetcher()
    assert fetcher.fetch_stock_data("us.aapl") is not None
    assert fetcher.provider_info("us.aapl")["provider"] == "yfinance(AAPL)"


def test_a_share_primary_and_fallback_labels(monkeypatch):
    fetcher = DataFetcher()
    monkeypatch.setattr(
        data_module,
        "as_api",
        SimpleNamespace(get_price=lambda code, count=120, frequency="1d": price_frame()),
    )
    assert fetcher.fetch_stock_data("sh600519") is not None
    assert fetcher.provider_info("sh600519") == {"provider": "ashare", "adjustment": "unknown"}

    fallback = DataFetcher()
    monkeypatch.setattr(
        data_module,
        "as_api",
        SimpleNamespace(get_price=lambda code, count=120, frequency="1d": None),
    )
    chinese = price_frame().reset_index()
    chinese.columns = ["日期", "开盘", "最高", "最低", "收盘", "成交量"]
    chinese["开盘"] = chinese["开盘"] * 1.0
    fake_ak = SimpleNamespace(stock_zh_a_hist=lambda **kwargs: chinese)
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)
    assert fallback.fetch_stock_data("sh600519") is not None
    assert fallback.provider_info("sh600519") == {"provider": "akshare", "adjustment": "qfq"}


def test_unknown_provider_is_not_guessed():
    fetcher = DataFetcher()
    assert fetcher.provider_info("hk00700")["provider"] == "unknown"


def test_analysis_metadata_carries_actual_provider(monkeypatch):
    service = StockAnalysisService()
    frame = price_frame()
    bundle = SimpleNamespace(
        analysis={"基础数据": {"最新价格": 5.5, "涨跌幅": 1.2}, "股票名称": "腾讯控股", "股票代码": "HK.00700"},
        signals={"overall_score": 3, "overall_signal": "中性"},
        dataframe=frame,
        ai_text=None,
        ai_requested=False,
        ai_provider=None,
        ai_model=None,
        analyzer=SimpleNamespace(
            data_fetcher=SimpleNamespace(
                provider_info=lambda code: {"provider": "yfinance(0700.HK)", "adjustment": "auto_adjust"}
            )
        ),
    )
    monkeypatch.setattr(service, "build_analysis_bundle", lambda *args, **kwargs: bundle)
    response = service.get_stock_analysis("hk00700", include_ai=False)
    assert response.market == "hk"
    assert response.metadata["source"] == "yfinance(0700.HK)"
    assert response.metadata["provider"] == "yfinance(0700.HK)"
    assert response.metadata["adjustment"] == "auto_adjust"
    assert response.metadata["currency"] == "HKD"
    assert response.metadata["price_status"] == "historical_or_delayed"
    assert response.metadata["data_plan"]["capability"] == "quote_history"
    assert response.metadata["data_plan"]["market"] == "hk"


def test_a_share_analysis_currency_is_cny(monkeypatch):
    service = StockAnalysisService()
    frame = price_frame()
    bundle = SimpleNamespace(
        analysis={"基础数据": {"最新价格": 5.5, "涨跌幅": 1.2}, "股票名称": "贵州茅台", "股票代码": "sh600519"},
        signals={"overall_score": 3, "overall_signal": "中性"},
        dataframe=frame,
        ai_text=None,
        ai_requested=False,
        ai_provider=None,
        ai_model=None,
        analyzer=SimpleNamespace(
            data_fetcher=SimpleNamespace(
                provider_info=lambda code: {"provider": "sina", "adjustment": "none"}
            )
        ),
    )
    monkeypatch.setattr(service, "build_analysis_bundle", lambda *args, **kwargs: bundle)
    response = service.get_stock_analysis("sh600519", include_ai=False)
    assert response.metadata["currency"] == "CNY"
    assert response.metadata["provider"] == "sina"
    assert response.metadata["adjustment"] == "none"


def test_hk_weak_market_news_is_topic_not_direct(tmp_path, monkeypatch):
    config = Config(monitor_db_path=str(tmp_path / "monitor.db"))
    service = NewsService(config=config)
    monkeypatch.setattr(
        service.tracker,
        "fetch_stock_news",
        lambda code, name: [
            {
                "title": "腾讯控股宣布回购股份",
                "content": "腾讯控股在港交所披露回购安排。",
                "source": "hkex",
                "occurred_at": "2026-10-27T09:00:00+08:00",
                "url": "https://example.org/tencent",
            },
            {
                "title": "A股三大指数集体上涨",
                "content": "沪指涨逾1%，市场情绪回暖。",
                "source": "market",
                "occurred_at": "2026-10-27T15:00:00+08:00",
                "url": "https://example.org/market",
            },
        ],
    )
    monkeypatch.setattr(service.state_store, "get_recent_alerts", lambda limit=10: [])
    items = service.get_stock_news("hk00700", stock_name="腾讯控股", limit=10)
    by_title = {item.title: item for item in items}
    assert by_title["腾讯控股宣布回购股份"].relation_type == "direct"
    weak = by_title["A股三大指数集体上涨"]
    assert weak.relation_type == "topic"
    assert "unverified" in weak.raw_payload["association_basis"].lower()
    # Direct company news is prioritized ahead of the topic-level fallback.
    assert items[0].title == "腾讯控股宣布回购股份"


def test_a_share_adjustment_provenance_is_split(monkeypatch):
    import ashare.data as data_module
    from ashare.data import DataFetcher

    sina_fetcher = DataFetcher()
    monkeypatch.setattr(
        data_module,
        "as_api",
        SimpleNamespace(
            get_price_sina=lambda code, end_date="", count=120, frequency="1d": price_frame(),
            get_price_day_tx=lambda code, end_date="", count=120, frequency="1d": price_frame(),
        ),
    )
    assert sina_fetcher.fetch_stock_data("sh600519") is not None
    assert sina_fetcher.provider_info("sh600519") == {"provider": "sina", "adjustment": "none"}

    tencent_fetcher = DataFetcher()

    def fail_sina(*args, **kwargs):
        raise RuntimeError("sina unavailable")

    monkeypatch.setattr(
        data_module,
        "as_api",
        SimpleNamespace(
            get_price_sina=fail_sina,
            get_price_day_tx=lambda code, end_date="", count=120, frequency="1d": price_frame(),
        ),
    )
    assert tencent_fetcher.fetch_stock_data("sh600519") is not None
    assert tencent_fetcher.provider_info("sh600519") == {"provider": "tencent", "adjustment": "qfq"}


def test_us_hk_history_uses_yfinance_adapter_when_enabled(monkeypatch):
    monkeypatch.setenv("YFINANCE_PRIMARY_US_HK", "1")
    import api.datasources.yfinance_provider as yf_provider

    bars = [
        {"date": "2026-10-27T00:00:00-04:00", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10},
        {"date": "2026-10-28T00:00:00-05:00", "open": 2.0, "high": 3.0, "low": 1.5, "close": 2.5, "volume": 20},
    ]
    monkeypatch.setattr(
        yf_provider,
        "get_yfinance_provider",
        lambda: SimpleNamespace(history=lambda *args, **kwargs: {"bars": bars, "symbol": "0700.HK"}),
    )
    fetcher = DataFetcher()
    assert fetcher.fetch_stock_data("hk00700", count=10) is not None
    assert fetcher.provider_info("hk00700") == {
        "provider": "yfinance_adapter(0700.HK)",
        "adjustment": "auto_adjust",
    }


def test_a_share_falls_back_to_yfinance_after_existing_paths(monkeypatch):
    monkeypatch.setenv("YFINANCE_A_SHARE_FALLBACK", "1")
    import api.datasources.yfinance_provider as yf_provider

    bars = [
        {"date": "2026-10-27T00:00:00-04:00", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10},
        {"date": "2026-10-28T00:00:00-05:00", "open": 2.0, "high": 3.0, "low": 1.5, "close": 2.5, "volume": 20},
    ]
    monkeypatch.setattr(
        yf_provider,
        "get_yfinance_provider",
        lambda: SimpleNamespace(history=lambda *args, **kwargs: {"bars": bars, "symbol": "600519.SS"}),
    )
    monkeypatch.setattr(data_module, "as_api", SimpleNamespace(get_price=lambda *args, **kwargs: None))
    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(stock_zh_a_hist=lambda **kwargs: None))
    fetcher = DataFetcher()
    assert fetcher.fetch_stock_data("sh600519", count=10) is not None
    assert fetcher.provider_info("sh600519") == {
        "provider": "yfinance_adapter(600519.SS)",
        "adjustment": "none",
    }


def test_provider_label_is_cleared_after_each_fetch(monkeypatch):
    """Provenance must be consumed by the fetch that produced it.

    `_last_fetch_source` was a sticky instance field: it was reset only at the
    start of a fetch, so any code path that read provenance after an early
    return (cache hit, restored last-good frame, raised provider) saw the label
    of whichever provider had run last -- e.g. DIA reporting QQQ's provider.
    """
    fetcher = DataFetcher()
    monkeypatch.setattr(data_module, "is_hk_index", lambda code: False)
    monkeypatch.setattr(data_module, "is_hk_stock", lambda code: False)
    monkeypatch.setattr(data_module, "is_us_stock", lambda code: True)

    def fake_us(code, count, frequency):
        fetcher._mark_source(f"yfinance({code.split('.')[-1]})", "auto_adjust")
        return price_frame()

    monkeypatch.setattr(fetcher, "_fetch_stock_data_us", fake_us)
    assert fetcher.fetch_stock_data("us.qqq") is not None

    # After the fetch is attributed, the pending label must be gone so the next
    # symbol cannot inherit it.
    assert fetcher._last_fetch_source is None
    assert fetcher._last_fetch_adjustment is None
    assert fetcher.provider_info("us.qqq")["provider"] == "yfinance(QQQ)"
