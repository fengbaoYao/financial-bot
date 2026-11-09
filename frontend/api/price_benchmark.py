"""Same-period benchmark returns for price-move research (yfinance, no keys)."""

from __future__ import annotations

import math
from typing import Any

# Preferred benchmarks by equity market. Labels are shown to the model/UI.
# Hang Seng TECH Index ETF 3033.HK is used when ^HSTECH is unavailable.
HK_BENCHMARKS = (
    {"code": "3033.HK", "label": "恒生科技ETF (3033.HK)", "role": "hang_seng_tech"},
    {"code": "^HSTECH", "label": "恒生科技指数 (^HSTECH)", "role": "hang_seng_tech"},
    {"code": "02800.HK", "label": "盈富基金/恒生指数替代 (02800.HK)", "role": "hang_seng_substitute"},
)
US_BENCHMARKS = (
    {"code": "US.QQQ", "label": "Nasdaq-100 ETF (QQQ)", "role": "nasdaq100"},
    {"code": "US.SPY", "label": "S&P 500 ETF (SPY)", "role": "sp500"},
)
CN_BENCHMARKS = (
    {"code": "sh000300", "label": "沪深300 (000300.SH)", "role": "csi300"},
    {"code": "sh000001", "label": "上证指数 (000001.SH)", "role": "sse_composite"},
)


def default_benchmarks_for(stock_code: str) -> tuple[dict[str, str], ...]:
    code = (stock_code or "").upper()
    if code.endswith(".HK") or code.startswith("HK."):
        return HK_BENCHMARKS
    if code.startswith("US."):
        return US_BENCHMARKS
    return CN_BENCHMARKS


def _close_series(bars: list[dict]) -> list[dict]:
    rows = []
    for bar in bars or []:
        try:
            close = float(bar["close"])
            date = str(bar.get("date") or "")[:10]
            if close > 0 and math.isfinite(close) and date:
                rows.append({"date": date, "close": close})
        except (KeyError, TypeError, ValueError):
            continue
    rows.sort(key=lambda row: row["date"])
    deduped = []
    seen = set()
    for row in rows:
        if row["date"] in seen:
            continue
        seen.add(row["date"])
        deduped.append(row)
    return deduped


def _window_return(bars: list[dict], sessions: int) -> dict[str, Any] | None:
    if len(bars) <= sessions:
        return None
    first, last = bars[-1 - sessions], bars[-1]
    return {
        "sessions": sessions,
        "start": first["date"],
        "end": last["date"],
        "start_close": first["close"],
        "end_close": last["close"],
        "change_pct": round((last["close"] / first["close"] - 1) * 100, 4),
    }


def compare_to_benchmark(
    stock_bars: list[dict],
    benchmark_bars: list[dict],
    *,
    stock_code: str,
    benchmark_code: str,
    benchmark_label: str,
    provider: str,
    endpoint: str | None = None,
    sessions: tuple[int, ...] = (20, 60),
) -> dict[str, Any]:
    """Align stock vs benchmark on overlapping daily closes and compute returns."""
    stock = _close_series(stock_bars)
    bench = _close_series(benchmark_bars)
    gaps: list[str] = []
    if len(stock) < 5:
        gaps.append("标的日线不足以计算同期收益。")
    if len(bench) < 5:
        gaps.append(f"基准 {benchmark_label} 日线不足以计算同期收益。")
    stock_by_date = {row["date"]: row["close"] for row in stock}
    bench_by_date = {row["date"]: row["close"] for row in bench}
    overlap_dates = sorted(set(stock_by_date) & set(bench_by_date))
    aligned_stock = [{"date": d, "close": stock_by_date[d]} for d in overlap_dates]
    aligned_bench = [{"date": d, "close": bench_by_date[d]} for d in overlap_dates]
    if len(overlap_dates) < 5:
        gaps.append("标的与基准重叠交易日不足，无法确认相对强弱。")
    windows = []
    for count in sessions:
        stock_win = _window_return(aligned_stock, count)
        bench_win = _window_return(aligned_bench, count)
        if not stock_win or not bench_win:
            gaps.append(f"重叠日线不足以计算近 {count} 个交易日相对收益。")
            continue
        windows.append(
            {
                "sessions": count,
                "start": stock_win["start"],
                "end": stock_win["end"],
                "stock_change_pct": stock_win["change_pct"],
                "benchmark_change_pct": bench_win["change_pct"],
                "relative_pct": round(stock_win["change_pct"] - bench_win["change_pct"], 4),
            }
        )
    return {
        "provider": provider,
        "endpoint": endpoint,
        "stock_code": stock_code,
        "benchmark_code": benchmark_code,
        "benchmark_label": benchmark_label,
        "overlap_sessions": len(overlap_dates),
        "data_as_of": overlap_dates[-1] if overlap_dates else None,
        "windows": windows,
        "data_gaps": gaps,
        "price_status": "historical_or_delayed",
    }


def fetch_benchmark_comparison(stock_code: str, stock_bars: list[dict] | None = None) -> dict[str, Any]:
    """Best-effort yfinance benchmark comparison for the stock's market."""
    from api.datasources.yfinance_provider import get_yfinance_provider, YFinanceUnavailable

    provider = get_yfinance_provider()
    gaps: list[str] = []
    if stock_bars is None:
        try:
            stock_hist = provider.history(stock_code, period="6mo", interval="1d")
            stock_bars = stock_hist.get("bars") or []
            stock_endpoint = stock_hist.get("endpoint")
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "unavailable",
                "stock_code": stock_code,
                "data_gaps": [f"标的历史行情不可用（{type(exc).__name__}），无法计算基准对比。"],
            }
    else:
        stock_endpoint = "caller_supplied_bars"

    last_error = None
    for candidate in default_benchmarks_for(stock_code):
        try:
            hist = provider.history(candidate["code"], period="6mo", interval="1d")
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            gaps.append(f"{candidate['label']} 不可用（{type(exc).__name__}）。")
            continue
        payload = compare_to_benchmark(
            stock_bars,
            hist.get("bars") or [],
            stock_code=stock_code,
            benchmark_code=candidate["code"],
            benchmark_label=candidate["label"],
            provider="yfinance",
            endpoint=hist.get("endpoint") or stock_endpoint,
        )
        payload["benchmark_role"] = candidate["role"]
        payload["tried"] = [row["code"] for row in default_benchmarks_for(stock_code)]
        if gaps:
            payload["data_gaps"] = [*gaps, *payload.get("data_gaps", [])]
        if payload.get("windows"):
            # Mark substitute clearly when Hang Seng Tech itself was unavailable.
            if candidate["role"] != "hang_seng_tech" and stock_code.upper().endswith(".HK"):
                payload["data_gaps"] = [
                    *payload.get("data_gaps", []),
                    f"恒生科技指数/ETF 不可用，已用替代基准 {candidate['label']}。",
                ]
            return payload
        gaps.extend(payload.get("data_gaps") or [])
    return {
        "status": "unavailable",
        "stock_code": stock_code,
        "provider": "yfinance",
        "data_gaps": gaps
        or [
            f"基准对比失败（{type(last_error).__name__ if last_error else 'no_benchmark'}）；"
            "不要编造恒生科技同期涨跌。"
        ],
    }
