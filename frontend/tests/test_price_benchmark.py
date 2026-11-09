from api.price_benchmark import compare_to_benchmark, default_benchmarks_for


def test_default_hk_benchmarks_prefer_hang_seng_tech():
    codes = [row["code"] for row in default_benchmarks_for("00700.HK")]
    assert codes[0] in {"3033.HK", "^HSTECH"}
    assert "3033.HK" in codes


def test_compare_to_benchmark_relative_windows():
    stock = [{"date": f"2026-0{i}-01", "close": 100 + i} for i in range(1, 8)]
    # pad to enough sessions with sequential days
    stock = [{"date": f"2026-01-{i:02d}", "close": 100 + i} for i in range(1, 65)]
    bench = [{"date": f"2026-01-{i:02d}", "close": 50 + i * 0.5} for i in range(1, 65)]
    payload = compare_to_benchmark(
        stock,
        bench,
        stock_code="00700.HK",
        benchmark_code="3033.HK",
        benchmark_label="恒生科技ETF (3033.HK)",
        provider="yfinance",
        sessions=(20, 60),
    )
    assert payload["windows"]
    assert payload["windows"][0]["sessions"] == 20
    assert "relative_pct" in payload["windows"][0]
