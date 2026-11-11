"""Phase 2 shadow comparison for yfinance.

Runs only when YFINANCE_SHADOW is enabled. It never changes a response, never
raises into the request path, and writes one JSONL record per comparison under
output/yfinance-shadow (or YFINANCE_SHADOW_DIR).
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from api.datasources.yfinance_provider import YFinanceProvider

logger = logging.getLogger(__name__)
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="yf-shadow")
_WRITE_LOCK = threading.Lock()
_LAST_SEEN: dict[str, float] = {}
_LAST_LOCK = threading.Lock()


def shadow_enabled() -> bool:
    raw = os.getenv("YFINANCE_SHADOW", "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def shadow_dir() -> Path:
    configured = os.getenv("YFINANCE_SHADOW_DIR", "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "output" / "yfinance-shadow"


def _sample_rate() -> float:
    try:
        return max(0.0, min(1.0, float(os.getenv("YFINANCE_SHADOW_SAMPLE", "1"))))
    except ValueError:
        return 1.0


def _should_sample(code: str) -> bool:
    now = time.monotonic()
    with _LAST_LOCK:
        previous = _LAST_SEEN.get(code)
        if previous is not None and now - previous < 90:
            return False
        _LAST_SEEN[code] = now
    return random.random() <= _sample_rate()


def compare_once(stock_code: str, response: Any, provider: Optional[YFinanceProvider] = None) -> dict[str, Any]:
    """Compare one existing analysis response with yfinance; pure/no side effects.

    The caller can use this in tests or an explicit diagnostic command.
    """
    code = str(stock_code or "").strip()
    primary_quote = getattr(response, "quote", None)
    primary_meta = getattr(response, "metadata", None) or {}
    primary_close = getattr(primary_quote, "current_price", None)
    chart = getattr(response, "chart_series", None) or []
    if primary_close is None and chart:
        primary_close = chart[-1].get("close")
    record: dict[str, Any] = {
        "compared_at": datetime.now(timezone.utc).isoformat(),
        "code": code,
        "primary_provider": primary_meta.get("provider") or primary_meta.get("source"),
        "primary_currency": primary_meta.get("currency"),
        "primary_adjustment": primary_meta.get("adjustment"),
        "primary_as_of": primary_meta.get("data_as_of") or (chart[-1].get("date") if chart else None),
        "primary_close": primary_close,
        "provider": "yfinance",
        "status": "error",
    }
    try:
        provider = provider or YFinanceProvider()
        quote = provider.quote(code)
        history = provider.history(code, period="10d")
        bars = history.get("bars") or []
        primary_date = str(record.get("primary_as_of") or "")[:10]
        matched = next((bar for bar in reversed(bars) if str(bar.get("date") or "")[:10] == primary_date), None) if primary_date else None
        comparison = matched or (bars[-1] if bars else None)
        primary_adjustment = str(record.get("primary_adjustment") or "").lower()
        use_adjusted = primary_adjustment in {"auto_adjust", "yahoo_adjusted", "adjusted"}
        yf_close = (comparison.get("adj_close") if use_adjusted else comparison.get("close")) if comparison else None
        yf_basis = "yahoo_adjusted" if use_adjusted else "close_raw"
        adjustment_match = (
            use_adjusted and yf_basis == "yahoo_adjusted"
        ) or (primary_adjustment in {"", "none", "not_applicable"} and yf_basis == "close_raw")
        record.update({
            "status": "ok" if matched else "date_mismatch",
            "symbol": quote.get("symbol") or history.get("symbol"),
            "yf_currency": quote.get("currency") or history.get("currency"),
            "yf_as_of": history.get("as_of"),
            "yf_compare_date": (comparison or {}).get("date"),
            "date_match": bool(matched),
            "comparison_basis": yf_basis,
            "yf_close": yf_close,
            "yf_price": quote.get("price"),
            "currency_match": record["primary_currency"] == (quote.get("currency") or history.get("currency")) if record["primary_currency"] else None,
            "adjustment_match": adjustment_match if record["primary_adjustment"] else None,
        })
        if matched and primary_close not in (None, 0) and yf_close is not None:
            record["close_diff_pct"] = round((float(yf_close) - float(primary_close)) / abs(float(primary_close)) * 100, 4)
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"[:300]
    return record


def _write(record: dict[str, Any]) -> None:
    directory = shadow_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
    line = json.dumps(record, ensure_ascii=False, default=str)
    with _WRITE_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def schedule_shadow(stock_code: str, response: Any) -> None:
    """Fire-and-forget shadow comparison; never affects the request."""
    if not shadow_enabled() or not _should_sample(str(stock_code or "")):
        return

    def run():
        try:
            _write(compare_once(stock_code, response))
        except Exception:
            logger.debug("yfinance shadow comparison failed", exc_info=True)

    try:
        _EXECUTOR.submit(run)
    except RuntimeError:
        # Interpreter shutdown or a closed executor: shadow data is optional.
        pass
