"""Fuyao / 同花顺 A-share data adapter.

The provider is deliberately narrow and capability-based:

* ticker search / list
* A-share quote and daily history
* A-share financial statements and financial indicators
* A-share valuations
* A-share trading calendar
* THS/standard index constituents

It uses the public REST contract documented at https://fuyao.aicubes.cn.
The API key is read from ``FUYAO_API_KEY`` and is never logged, persisted, or
returned to the browser.  The adapter is inert when the key is absent; callers
must fall back to the existing Sina/Tencent/akshare paths.

The endpoint is new as of 2026-10-30 by explicit product decision.  This is the
one approved exception to the earlier "yfinance is the last new provider"
freeze; non-A-share coverage remains on yfinance / legacy providers.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

import requests

logger = logging.getLogger(__name__)

from ashare.stock_pool import normalize_stock_code
from api.datasources import health
from api.datasources.validation import validate_financial_periods, validate_price_bars

BASE_URL = "https://fuyao.aicubes.cn"
TZ_SH = timezone(timedelta(hours=8))
DEFAULT_TIMEOUT = (5, 25)

QUOTE_TTL = 30
HISTORY_TTL = 300
FINANCIAL_TTL = 6 * 60 * 60
INDICATOR_TTL = 6 * 60 * 60
VALUATION_TTL = 60 * 60
CALENDAR_TTL = 12 * 60 * 60
INDEX_TTL = 6 * 60 * 60
SEARCH_TTL = 60 * 60


class FuyaoError(RuntimeError):
    """Base error for Fuyao failures."""

    def __init__(self, message: str, *, code: int | None = None, request_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.request_id = request_id


class FuyaoNotConfigured(FuyaoError):
    """No FUYAO_API_KEY is configured; callers should use a legacy source."""


class FuyaoUnavailable(FuyaoError):
    """The provider is configured but temporarily unavailable or rate limited."""


class FuyaoUnsupportedSymbol(FuyaoError):
    """The provided code is not in the A-share universe supported by Fuyao."""


@dataclass(frozen=True)
class _CacheEntry:
    value: Any
    sha256: str
    expires_at: float


def _now() -> str:
    return datetime.now(TZ_SH).isoformat(timespec="seconds")


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def _iso_ms(value: Any) -> str | None:
    number = _number(value)
    if number is None:
        return None
    try:
        return datetime.fromtimestamp(number / 1000, TZ_SH).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _market(code: str) -> str:
    normalized = normalize_stock_code(code)
    lower = normalized.lower()
    if lower.startswith("sh"):
        return "sh"
    if lower.startswith("sz"):
        return "sz"
    if lower.startswith("bj"):
        return "bj"
    if lower.endswith(".sh"):
        return "sh"
    if lower.endswith(".sz"):
        return "sz"
    if lower.endswith(".bj"):
        return "bj"
    return "unknown"


def to_fuyao_thscode(stock_code: str) -> str:
    """Map a Financial Bot / common A-share code to Fuyao's thscode.

    ``sh600519`` -> ``600519.SH``; ``600519.SH`` stays as-is; bare ``600519``
    gets an inferred suffix.  Fuyao accepts .SH/.SZ/.BJ for A-share stocks.
    """
    raw = str(stock_code or "").strip().upper()
    if not raw:
        raise FuyaoUnsupportedSymbol("empty stock code")
    if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", raw):
        return raw
    if re.fullmatch(r"(SH|SZ|BJ)\d{6}", raw):
        return f"{raw[2:]}.{raw[:2]}"
    normalized = normalize_stock_code(raw)
    upper = normalized.upper()
    if re.fullmatch(r"(SH|SZ|BJ)\d{6}", upper):
        return f"{upper[2:]}.{upper[:2]}"
    if re.fullmatch(r"\d{6}", upper):
        if upper.startswith(("4", "8")):
            return f"{upper}.BJ"
        if upper.startswith(("5", "6", "9")):
            return f"{upper}.SH"
        return f"{upper}.SZ"
    raise FuyaoUnsupportedSymbol(f"Fuyao supports A-share thscodes only: {stock_code!r}")


def _endpoint(path: str) -> str:
    return f"fuyao.aicubes.cn{path}"


class FuyaoProvider:
    """Thread-safe, TTL-cached Fuyao REST client.

    The adapter returns JSON-friendly dictionaries, never raw requests objects.
    A caller can inject a session/clock for deterministic tests.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = BASE_URL,
        session: Any | None = None,
        cache_ttl: Optional[Mapping[str, int]] = None,
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
    ):
        self._api_key = (api_key if api_key is not None else os.getenv("FUYAO_API_KEY", "")).strip()
        self.base_url = base_url.rstrip("/")
        self._session = session or requests.Session()
        self._timeout = timeout
        self._cache: dict[tuple[Any, ...], _CacheEntry] = {}
        self._cache_lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(4)
        self._ttl = {
            "quote": QUOTE_TTL,
            "history": HISTORY_TTL,
            "financials": FINANCIAL_TTL,
            "indicators": INDICATOR_TTL,
            "valuations": VALUATION_TTL,
            "calendar": CALENDAR_TTL,
            "index": INDEX_TTL,
            "search": SEARCH_TTL,
            **(dict(cache_ttl or {})),
        }

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _get(self, path: str, params: Mapping[str, Any] | None = None, *, kind: str) -> tuple[Any, str]:
        if not self.configured:
            raise FuyaoNotConfigured("FUYAO_API_KEY is not configured")
        clean = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        cache_key = (path, tuple(sorted(clean.items())))
        now = time.monotonic()
        with self._cache_lock:
            entry = self._cache.get(cache_key)
            if entry and entry.expires_at > now:
                return entry.value, entry.sha256
        if not health.is_available("fuyao", kind):
            raise FuyaoUnavailable(f"Fuyao {kind} circuit is cooling down after repeated failures")
        with self._slots:
            try:
                response = self._session.get(
                    f"{self.base_url}{path}",
                    params=clean,
                    headers={
                        "X-api-key": self._api_key,
                        "Accept": "application/json",
                        "User-Agent": "Financial-Bot/1.0 (+server-side research)",
                    },
                    timeout=self._timeout,
                )
            except requests.RequestException as exc:
                health.record_failure("fuyao", kind, type(exc).__name__)
                raise FuyaoUnavailable(f"Fuyao request failed: {type(exc).__name__}") from exc
        if response.status_code == 429:
            health.record_failure("fuyao", kind, "HTTP 429")
            raise FuyaoUnavailable("Fuyao rate limit hit (HTTP 429)", code=4001)
        if response.status_code >= 500:
            health.record_failure("fuyao", kind, f"HTTP {response.status_code}")
            raise FuyaoUnavailable(f"Fuyao upstream HTTP {response.status_code}")
        if response.status_code >= 400:
            raise FuyaoUnavailable(f"Fuyao HTTP {response.status_code}")
        content = response.content or b""
        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            health.record_failure("fuyao", kind, "non_json")
            raise FuyaoUnavailable("Fuyao returned a non-JSON response") from exc
        if not isinstance(payload, dict):
            health.record_failure("fuyao", kind, "not_envelope")
            raise FuyaoUnavailable("Fuyao response is not an ApiResponse object")
        code = payload.get("code")
        request_id = payload.get("request_id")
        if code != 0:
            message = str(payload.get("message") or f"Fuyao business error {code}")
            if code in {4001, 5001, 5002, 5003}:
                health.record_failure("fuyao", kind, f"code={code}")
            if code == 4001:
                raise FuyaoUnavailable(message, code=code, request_id=request_id)
            raise FuyaoError(message, code=code if isinstance(code, int) else None, request_id=request_id)
        data = payload.get("data")
        sha = _sha(content)
        health.record_success("fuyao", kind)
        with self._cache_lock:
            self._cache[cache_key] = _CacheEntry(data, sha, now + self._ttl.get(kind, 300))
        return data, sha

    def _wrapped(self, data: Any, sha: str, path: str, *, kind: str = "") -> dict[str, Any]:
        return {
            "provider": "fuyao",
            "endpoint": _endpoint(path),
            "retrieved_at": _now(),
            "raw_sha256": sha,
            "raw_ref": None,
            "raw_retained": False,
            "data": data,
            "kind": kind,
        }

    # ---------------------------------------------------------------- metadata
    def search(self, query: str, *, asset_type: str = "a-share", limit: int = 10) -> dict[str, Any]:
        path = "/api/meta/tickers/search"
        data, sha = self._get(path, {"q": query, "asset_type": asset_type, "limit": max(1, min(50, int(limit)))}, kind="search")
        result = self._wrapped(data, sha, path, kind="ticker_search")
        result["items"] = (data or {}).get("item") or []
        result["as_of"] = _iso_ms((data or {}).get("timestamp"))
        return result

    # ---------------------------------------------------------------- prices
    def quote(self, stock_code: str) -> dict[str, Any]:
        thscode = to_fuyao_thscode(stock_code)
        path = "/api/a-share/prices/snapshot"
        data, sha = self._get(path, {"thscodes": thscode}, kind="quote")
        item = ((data or {}).get("item") or [None])[0]
        if not item:
            raise FuyaoError(f"Fuyao returned no quote for {thscode}", code=3002)
        price = _number(item.get("last_price"))
        if price is None:
            raise FuyaoError(f"Fuyao quote for {thscode} has no last_price", code=3002)
        return {
            "provider": "fuyao",
            "endpoint": _endpoint(path),
            "symbol": normalize_stock_code(stock_code),
            "thscode": thscode,
            "market": "cn",
            "price": price,
            "previous_close": _number(item.get("prev_price")),
            "open": _number(item.get("open_price")),
            "day_high": _number(item.get("high_price")),
            "day_low": _number(item.get("low_price")),
            "volume": _number(item.get("volume")),
            "turnover": _number(item.get("turnover")),
            "price_change": _number(item.get("price_change")),
            "price_change_ratio_pct": _number(item.get("price_change_ratio_pct")),
            "currency": "CNY",
            "as_of": _iso_ms((data or {}).get("timestamp")),
            "retrieved_at": _now(),
            "price_status": "delayed_or_last",
            "adjustment": "none",
            "raw_sha256": sha,
        }

    def history(
        self,
        stock_code: str,
        start_ms: int,
        end_ms: int,
        *,
        adjust: str = "forward",
        interval: str = "1d",
    ) -> dict[str, Any]:
        thscode = to_fuyao_thscode(stock_code)
        if adjust not in {"none", "forward", "backward"}:
            raise FuyaoError("adjust must be none, forward or backward")
        path = "/api/a-share/prices/historical"
        data, sha = self._get(
            path,
            {"thscode": thscode, "interval": interval, "start": int(start_ms), "end": int(end_ms), "adjust": adjust},
            kind="history",
        )
        bars = []
        for row in (data or {}).get("item") or []:
            date = _iso_ms(row.get("date_ms"))
            if not date:
                continue
            bars.append(
                {
                    "date": date,
                    "open": _number(row.get("open_price")),
                    "high": _number(row.get("high_price")),
                    "low": _number(row.get("low_price")),
                    "close": _number(row.get("close_price")),
                    "volume": _number(row.get("volume")),
                    "turnover": _number(row.get("turnover")),
                }
            )
        if not bars:
            raise FuyaoError(f"Fuyao returned no history for {thscode}", code=3002)
        errors = validate_price_bars(bars)
        if errors:
            health.record_failure("fuyao", "history", "invalid_bars")
            raise FuyaoError("Fuyao returned invalid price bars: " + "; ".join(errors[:3]), code=5003)
        mapping = {"none": "none", "forward": "qfq", "backward": "hfq"}
        return {
            "provider": "fuyao",
            "endpoint": _endpoint(path),
            "symbol": normalize_stock_code(stock_code),
            "thscode": thscode,
            "adjustment": mapping[adjust],
            "interval": interval,
            "bars": bars,
            "data_as_of": bars[-1]["date"],
            "retrieved_at": _now(),
            "raw_sha256": sha,
        }

    # ---------------------------------------------------------------- fundamentals
    _STATEMENTS = {
        "income": {
            "path": "/api/a-share/financials/income-statements",
            "label": "利润表",
            "map": {
                "operating_income": "revenue",
                "operating_profit": "operating_profit",
                "net_profit": "net_profit",
                "parent_holder_net_profit": "net_profit_parent",
                "basic_eps": "eps_basic",
            },
        },
        "balance": {
            "path": "/api/a-share/financials/balance-sheets",
            "label": "资产负债表",
            "map": {
                "assets_total": "total_assets",
                "total_debt": "total_liabilities",
                "holder_equity_total": "total_equity",
                "cash": "cash",
                "accounts_receivable": "accounts_receivable",
                "total_current_assets": "current_assets",
            },
        },
        "cashflow": {
            "path": "/api/a-share/financials/cash-flow-statements",
            "label": "现金流量表",
            "map": {
                "act_cash_flow_net": "cfo",
                "invest_cash_flow_net": "cfi",
                "financing_cash_flow_net": "cff",
                "cash_equivalents_net_addition": "net_cash_change",
            },
        },
    }

    def financial_statements(self, stock_code: str, statement: str = "income", periods: int = 8) -> dict[str, Any]:
        if statement not in self._STATEMENTS:
            raise FuyaoError("statement must be income, balance or cashflow")
        periods = max(1, min(20, int(periods)))
        spec = self._STATEMENTS[statement]
        thscode = to_fuyao_thscode(stock_code)
        path = spec["path"]
        data, sha = self._get(
            path,
            {"thscode": thscode, "period": "annual", "limit": periods},
            kind="financials",
        )
        rows = []
        for row in (data or {}).get("item") or []:
            period = _iso_ms(row.get("period_end_ms"))
            if not period:
                continue
            values = {}
            for source, target in spec["map"].items():
                value = _number(row.get(source))
                if value is not None:
                    values[target] = value
            if values:
                rows.append({"period": period, "items": values})
        if not rows:
            raise FuyaoError(f"Fuyao returned no {statement} statements for {thscode}", code=3002)
        errors = validate_financial_periods(rows)
        if errors:
            health.record_failure("fuyao", "financials", "invalid_periods")
            raise FuyaoError("Fuyao returned invalid financial periods: " + "; ".join(errors[:3]), code=5003)
        gaps = []
        if len(rows) < periods:
            gaps.append(f"仅返回 {len(rows)} 个报告期，少于请求的 {periods} 个")
        return {
            "provider": "fuyao",
            "endpoint": _endpoint(path),
            "symbol": normalize_stock_code(stock_code),
            "market": "CN",
            "thscode": thscode,
            "statement": statement,
            "statement_label": spec["label"],
            "report_type": statement,
            "data_as_of": rows[0]["period"],
            "retrieved_at": _now(),
            "currency": "CNY",
            "adjustment": "not_applicable",
            "periods": rows,
            "raw_sha256": sha,
            "raw_ref": None,
            "raw_retained": False,
            "data_gaps": gaps,
            "note": "同花顺 Fuyao A 股合并报表原值；累计口径，不做单季拆分。服务端仅保留原始响应哈希。",
        }

    def financial_indicators(self, stock_code: str, report: str) -> dict[str, Any]:
        thscode = to_fuyao_thscode(stock_code)
        path = "/api/a-share/financials/indicators"
        data, sha = self._get(path, {"thscode": thscode, "report": report}, kind="indicators")
        return {"provider": "fuyao", "endpoint": _endpoint(path), "thscode": thscode, "report": report,
                "abilities": (data or {}).get("abilities") or [], "retrieved_at": _now(), "raw_sha256": sha}

    def valuations(self, stock_codes: list[str]) -> dict[str, Any]:
        thscodes = [to_fuyao_thscode(code) for code in stock_codes]
        path = "/api/a-share/valuations/snapshot"
        cache_key = ",".join(sorted(thscodes))
        try:
            data, sha = self._get(path, {"thscodes": ",".join(thscodes)}, kind="valuations")
            items = []
            for row in (data or {}).get("item") or []:
                items.append({
                    "thscode": row.get("thscode"),
                    "ticker": row.get("ticker"),
                    "name": row.get("name"),
                    "pe_ttm": _number(row.get("pe_ttm")),
                    "pe_mrq": _number(row.get("pe_mrq")),
                    "pb_mrq": _number(row.get("pb_mrq")),
                    "ps_ttm": _number(row.get("ps_ttm")),
                    "pcf_ttm": _number(row.get("pcf_ttm")),
                })
            result = {
                "provider": "fuyao",
                "endpoint": _endpoint(path),
                "items": items,
                "as_of": _iso_ms((data or {}).get("timestamp")),
                "retrieved_at": _now(),
                "raw_sha256": sha,
                "data_gaps": [] if items else ["Fuyao 未返回估值记录"],
            }
            try:
                from api.datasources.last_good import put

                put("valuations", cache_key, result, provider="fuyao", data_as_of=result.get("as_of"), adjustment="not_applicable")
            except Exception:
                logger.debug("Valuation last-good write skipped", exc_info=True)
            return result
        except Exception:
            try:
                from api.datasources.last_good import get

                cached = get("valuations", cache_key)
                if cached and isinstance(cached.get("value"), dict):
                    stale = dict(cached["value"])
                    stale["stale"] = True
                    stale["cache_provider"] = cached.get("provider")
                    stale["cache_stored_at"] = cached.get("stored_at")
                    stale["retrieved_at"] = cached.get("stored_at") or stale.get("retrieved_at")
                    items = stale.get("items")
                    if not isinstance(items, list) or not items:
                        raise FuyaoUnavailable("last-good valuation cache is empty")
                    stale["data_gaps"] = [
                        *(stale.get("data_gaps") or []),
                        f"实时估值源不可用；返回 last-good 缓存（截至 {cached.get('data_as_of') or '未知'}）。",
                    ]
                    return stale
            except Exception:
                logger.debug("Valuation last-good read skipped", exc_info=True)
            raise

    # ---------------------------------------------------------------- calendar / index
    def trading_days(self, start_date: str, end_date: str) -> dict[str, Any]:
        path = "/api/a-share/calendar/trading-days"
        data, sha = self._get(path, {"start_date": start_date, "end_date": end_date}, kind="calendar")
        days = [row.get("date") for row in (data or {}).get("item") or [] if row.get("date")]
        return {"provider": "fuyao", "endpoint": _endpoint(path), "days": days,
                "retrieved_at": _now(), "raw_sha256": sha}

    def index_constituents(self, index_code: str) -> dict[str, Any]:
        thscode = str(index_code or "").strip().upper()
        if not re.fullmatch(r"\d{6}\.(SH|SZ|TI)", thscode):
            raise FuyaoUnsupportedSymbol("index_code must be like 000300.SH or 886042.TI")
        path = "/api/a-share-index/constituents/ths-stock-list"
        data, sha = self._get(path, {"thscode": thscode}, kind="index")
        members = [
            {"code": row.get("thscode"), "ticker": row.get("ticker"), "name": row.get("name")}
            for row in (data or {}).get("item") or []
        ]
        return {"provider": "fuyao", "endpoint": _endpoint(path), "index_code": thscode,
                "members": members, "member_count": len(members), "retrieved_at": _now(),
                "raw_sha256": sha, "data_gaps": [] if members else ["Fuyao 未返回成分股"]}


_PROVIDER: FuyaoProvider | None = None
_PROVIDER_LOCK = threading.Lock()


def get_fuyao_provider() -> FuyaoProvider:
    """Process-wide adapter/cache; reads FUYAO_API_KEY lazily."""
    global _PROVIDER
    with _PROVIDER_LOCK:
        if _PROVIDER is None:
            _PROVIDER = FuyaoProvider()
        return _PROVIDER
