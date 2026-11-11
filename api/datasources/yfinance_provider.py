"""Small yfinance adapter with explicit symbols, provenance and no API key.

Phase 1 of docs/yfinance-source-plan.md. This module does not replace any
existing data path yet; the shadow runner consumes it read-only.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import pandas as pd

QUOTE_TTL = 60
HISTORY_TTL = 300
FINANCIAL_TTL = 6 * 60 * 60
ESTIMATE_TTL = 60 * 60
NEWS_TTL = 300
ENDPOINT = "query1.finance.yahoo.com"


class YFinanceUnavailable(RuntimeError):
    """The provider could not return usable data; this is an explicit gap."""


class NotSupportedSymbol(YFinanceUnavailable):
    """The requested market/symbol cannot be represented as a Yahoo symbol."""


@dataclass(frozen=True)
class _CacheEntry:
    value: Any
    expires_at: float


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def _date_text(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _currency_for_market(market: str) -> str | None:
    return {"us": "USD", "hk": "HKD", "cn": "CNY", "sh": "CNY", "sz": "CNY", "bj": "CNY"}.get(market)


def _reporting_currency(ticker) -> str | None:
    """Read the statement currency from the quote payload, never from the market.

    A Hong Kong listing frequently reports in CNY, so inferring the reporting
    currency from the listing market would be wrong. Yahoo publishes the two
    separately; when that field is missing or unreadable the currency stays
    unverified rather than guessed.
    """
    try:
        info = ticker.get_info()
    except Exception:
        return None
    if not isinstance(info, dict):
        return None
    value = str(info.get("financialCurrency") or "").strip().upper()
    return value if re.fullmatch(r"[A-Z]{3}", value) else None


def to_yahoo_symbol(stock_code: str) -> str:
    """Map a Financial Bot code to a Yahoo symbol.

    A-share: sh600519 -> 600519.SS, sz000001 -> 000001.SZ.
    Beijing codes are not supported by the verified yfinance path.
    HK: HK.00700 / 00700.HK -> 0700.HK.
    US: US.BRK.B -> BRK-B.
    """
    raw = str(stock_code or "").strip()
    if not raw:
        raise NotSupportedSymbol("empty stock code")
    upper = raw.upper()
    if upper.startswith("^"):
        return upper
    if upper.startswith("BJ"):
        raise NotSupportedSymbol(f"Beijing Stock Exchange is not verified on yfinance: {stock_code}")
    if upper.startswith("US."):
        ticker = upper[3:].replace(".", "-")
        if re.fullmatch(r"[A-Z][A-Z0-9-]{0,9}", ticker):
            return ticker
        raise NotSupportedSymbol(f"unsupported US ticker: {stock_code}")
    if upper.endswith((".SS", ".SZ")):
        return upper
    if upper.endswith(".HK"):
        hk_digits = upper[:-3].strip()
        if re.fullmatch(r"\d{4,5}", hk_digits):
            return f"{int(hk_digits):04d}.HK"
        raise NotSupportedSymbol(f"unsupported HK code: {stock_code}")
    prefix = upper[:2]
    digits = upper[2:] if prefix in {"SH", "SZ", "HK"} else upper
    digits = digits.lstrip(".").removesuffix(".HK")
    if prefix == "HK" or upper.endswith(".HK"):
        if not re.fullmatch(r"\d{4,5}", digits):
            raise NotSupportedSymbol(f"unsupported HK code: {stock_code}")
        return f"{int(digits):04d}.HK"
    if prefix in {"SH", "SZ"}:
        if not re.fullmatch(r"\d{6}", digits):
            raise NotSupportedSymbol(f"unsupported A-share code: {stock_code}")
        return f"{digits}.{'SS' if prefix == 'SH' else 'SZ'}"
    if re.fullmatch(r"\d{6}", digits):
        if digits.startswith(("4", "8")):
            raise NotSupportedSymbol(f"Beijing Stock Exchange is not verified on yfinance: {stock_code}")
        return f"{digits}.{'SS' if digits.startswith(('5', '6', '9')) else 'SZ'}"
    if re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", upper):
        return upper.replace(".", "-")
    raise NotSupportedSymbol(f"unsupported symbol: {stock_code}")


def _market_for_symbol(symbol: str) -> str:
    if symbol.endswith(".SS") or symbol.endswith(".SZ"):
        return "cn"
    if symbol.endswith(".HK"):
        return "hk"
    return "us"


class YFinanceProvider:
    """Thread-safe, TTL-cached yfinance reads.

    The adapter returns JSON-friendly dictionaries rather than yfinance objects so
    the rest of the application never depends on yfinance internals.
    """

    def __init__(self, ticker_factory: Optional[Callable[[str], Any]] = None, *, cache_ttl: Optional[dict[str, int]] = None):
        self._ticker_factory = ticker_factory
        self._cache: dict[str, _CacheEntry] = {}
        self._cache_lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(2)
        self._ttl = {
            "quote": QUOTE_TTL,
            "history": HISTORY_TTL,
            "financials": FINANCIAL_TTL,
            "estimates": ESTIMATE_TTL,
            "news": NEWS_TTL,
            **(cache_ttl or {}),
        }

    def _ticker(self, symbol: str):
        if self._ticker_factory is not None:
            return self._ticker_factory(symbol)
        import yfinance as yf

        return yf.Ticker(symbol)

    def _cached(self, key: str, kind: str, loader: Callable[[], Any]) -> Any:
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

        now = time.monotonic()
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is not None and entry.expires_at > now:
                return entry.value
        # Cap wait for a free slot + the Yahoo call itself so /work never spins forever.
        acquired = self._slots.acquire(timeout=12)
        if not acquired:
            raise YFinanceUnavailable(f"yfinance busy; timed out waiting for slot ({key})")
        try:
            pool = ThreadPoolExecutor(max_workers=1)
            try:
                value = pool.submit(loader).result(timeout=10)
            except FuturesTimeout as exc:
                raise YFinanceUnavailable(f"yfinance timed out for {key}") from exc
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
        finally:
            self._slots.release()
        with self._cache_lock:
            self._cache[key] = _CacheEntry(value, now + self._ttl.get(kind, 300))
        return value

    def symbol(self, stock_code: str) -> str:
        return to_yahoo_symbol(stock_code)

    def quote(self, stock_code: str) -> dict[str, Any]:
        symbol = self.symbol(stock_code)

        def load():
            fast = self._ticker(symbol).fast_info
            def value(name):
                try:
                    return getattr(fast, name)
                except Exception:
                    return None
            price = _number(value("last_price"))
            if price is None:
                raise YFinanceUnavailable(f"yfinance returned no last price for {symbol}")
            result = {
                "provider": "yfinance",
                "endpoint": ENDPOINT,
                "symbol": symbol,
                "market": _market_for_symbol(symbol),
                "price": price,
                "previous_close": _number(value("previous_close")),
                "open": _number(value("open")),
                "day_high": _number(value("day_high")),
                "day_low": _number(value("day_low")),
                "volume": _number(value("last_volume")),
                "market_cap": _number(value("market_cap")),
                "currency": value("currency") or _currency_for_market(_market_for_symbol(symbol)),
                "exchange": value("exchange"),
                "as_of": None,
                "retrieved_at": _now(),
                "price_status": "delayed_or_last",
                "adjustment": "none",
            }
            result["raw_sha256"] = _sha({k: v for k, v in result.items() if k != "raw_sha256"})
            return result

        return self._cached(f"quote:{symbol}", "quote", load)

    def calendar(self, stock_code: str) -> dict[str, Any]:
        """Return the next earnings date when yfinance exposes one."""
        symbol = self.symbol(stock_code)

        def load():
            raw = self._ticker(symbol).calendar or {}
            if hasattr(raw, "to_dict"):
                raw = raw.to_dict()
            value = raw.get("Earnings Date") if isinstance(raw, dict) else None
            if isinstance(value, (list, tuple)):
                value = value[0] if value else None
            if value is None:
                raise YFinanceUnavailable(f"yfinance returned no earnings calendar for {symbol}")
            text = _date_text(value)
            result = {
                "provider": "yfinance",
                "endpoint": ENDPOINT,
                "symbol": symbol,
                "market": _market_for_symbol(symbol),
                "earnings_date": text[:10] if text else None,
                "retrieved_at": _now(),
                "is_estimate": True,
            }
            result["raw_sha256"] = _sha({k: v for k, v in result.items() if k != "raw_sha256"})
            return result

        return self._cached(f"calendar:{symbol}", "calendar", load)

    def history(self, stock_code: str, period: str = "1y", interval: str = "1d", *, start: str | None = None, end: str | None = None, auto_adjust: bool = False) -> dict[str, Any]:
        symbol = self.symbol(stock_code)
        def load():
            kwargs = {"interval": interval, "auto_adjust": auto_adjust, "actions": True}
            if start or end:
                kwargs.update({"start": start, "end": end})
            else:
                kwargs["period"] = period
            frame = self._ticker(symbol).history(**kwargs)
            if frame is None or frame.empty:
                raise YFinanceUnavailable(f"yfinance returned no history for {symbol}")
            bars = []
            for index, row in frame.tail(1250).iterrows():
                bars.append({
                    "date": _date_text(index),
                    "open": _number(row.get("Open")),
                    "high": _number(row.get("High")),
                    "low": _number(row.get("Low")),
                    "close": _number(row.get("Close")),
                    "adj_close": _number(row.get("Adj Close")),
                    "volume": _number(row.get("Volume")),
                    "dividends": _number(row.get("Dividends")),
                    "splits": _number(row.get("Stock Splits")),
                })
            last = bars[-1]
            result = {
                "provider": "yfinance",
                "endpoint": ENDPOINT,
                "symbol": symbol,
                "market": _market_for_symbol(symbol),
                "currency": _currency_for_market(_market_for_symbol(symbol)),
                "period": period,
                "interval": interval,
                "start": start,
                "end": end,
                "as_of": last["date"],
                "retrieved_at": _now(),
                "bars": bars,
                "adjustment": "auto_adjust" if auto_adjust else "close_raw; adj_close=yahoo_adjusted",
                "price_status": "historical_or_delayed",
            }
            result["raw_sha256"] = _sha({"symbol": symbol, "bars": bars})
            return result

        return self._cached(f"history:{symbol}:{period}:{interval}:{start}:{end}:{auto_adjust}", "history", load)

    def financials(self, stock_code: str, statement: str = "income", quarterly: bool = False, periods: int = 4) -> dict[str, Any]:
        symbol = self.symbol(stock_code)
        attributes = {
            ("income", False): "financials",
            ("income", True): "quarterly_financials",
            ("balance", False): "balance_sheet",
            ("balance", True): "quarterly_balance_sheet",
            ("cashflow", False): "cashflow",
            ("cashflow", True): "quarterly_cashflow",
        }
        if (statement, quarterly) not in attributes:
            raise YFinanceUnavailable("statement must be income, balance or cashflow")
        attribute = attributes[(statement, quarterly)]

        def load():
            ticker = self._ticker(symbol)
            frame = getattr(ticker, attribute)
            if frame is None or frame.empty:
                raise YFinanceUnavailable(f"yfinance returned no {statement} statement for {symbol}")
            columns = list(frame.columns)[:max(1, min(8, periods))]
            rows = []
            for period in columns:
                values = {}
                for label, value in frame[period].items():
                    values[str(label)] = _number(value)
                rows.append({"period": _date_text(period), "values": values})
            # The statement frame carries no currency metadata. Read the
            # provider's separate reporting-currency field when it exists.
            reporting_currency = _reporting_currency(ticker)
            result = {
                "provider": "yfinance",
                "endpoint": ENDPOINT,
                "symbol": symbol,
                "market": _market_for_symbol(symbol),
                "statement": statement,
                "quarterly": quarterly,
                # Listing/quote currency does not establish reporting currency.
                "currency": reporting_currency,
                "currency_basis": "provider_financial_currency" if reporting_currency else "unverified",
                "data_gaps": [] if reporting_currency else [
                    "Financial reporting currency is unverified; do not infer it from the listing market or numerical values."
                ],
                "as_of": rows[0]["period"],
                "retrieved_at": _now(),
                "periods": rows,
                "adjustment": "not_applicable",
            }
            result["raw_sha256"] = _sha({"symbol": symbol, "statement": statement,
                                        "currency": reporting_currency, "periods": rows})
            return result

        return self._cached(f"financials:{symbol}:{statement}:{quarterly}:{periods}", "financials", load)

    def estimates(self, stock_code: str) -> dict[str, Any]:
        symbol = self.symbol(stock_code)

        def load():
            ticker = self._ticker(symbol)
            targets = ticker.analyst_price_targets or {}
            recommendations = ticker.recommendations
            recs = []
            if recommendations is not None and not recommendations.empty:
                for _, row in recommendations.tail(8).iterrows():
                    recs.append({str(k): (v.item() if hasattr(v, "item") else v) for k, v in row.items()})
            result = {
                "provider": "yfinance",
                "endpoint": ENDPOINT,
                "symbol": symbol,
                "market": _market_for_symbol(symbol),
                "currency": _currency_for_market(_market_for_symbol(symbol)),
                "as_of": None,
                "retrieved_at": _now(),
                "targets": {str(k): _number(v) if isinstance(v, (int, float)) else v for k, v in dict(targets).items()},
                "recommendations": recs,
                "adjustment": "not_applicable",
            }
            result["raw_sha256"] = _sha({"symbol": symbol, "targets": result["targets"], "recommendations": recs})
            return result

        return self._cached(f"estimates:{symbol}", "estimates", load)

    def news(self, stock_code: str) -> dict[str, Any]:
        symbol = self.symbol(stock_code)

        def load():
            raw = getattr(self._ticker(symbol), "news", []) or []
            items = []
            for row in raw[:20]:
                item = row if isinstance(row, dict) else {}
                content = item.get("content") if isinstance(item.get("content"), dict) else item
                title = str(content.get("title") or "").strip()
                if not title:
                    continue
                items.append({
                    "title": title,
                    "publisher": content.get("publisher") or content.get("provider"),
                    "published_at": content.get("pubDate") or content.get("providerPublishTime"),
                    "url": content.get("canonicalUrl", {}).get("url") if isinstance(content.get("canonicalUrl"), dict) else content.get("link"),
                })
            return {
                "provider": "yfinance",
                "endpoint": ENDPOINT,
                "symbol": symbol,
                "status": "ok" if items else "empty",
                "items": items,
                "retrieved_at": _now(),
            }

        return self._cached(f"news:{symbol}", "news", load)

    def actions(self, stock_code: str) -> dict[str, Any]:
        symbol = self.symbol(stock_code)

        def load():
            ticker = self._ticker(symbol)
            dividends = ticker.dividends
            splits = ticker.splits
            return {
                "provider": "yfinance",
                "endpoint": ENDPOINT,
                "symbol": symbol,
                "dividends": [{"date": _date_text(i), "value": _number(v)} for i, v in dividends.tail(20).items()] if dividends is not None else [],
                "splits": [{"date": _date_text(i), "value": _number(v)} for i, v in splits.tail(20).items()] if splits is not None else [],
                "retrieved_at": _now(),
            }

        return self._cached(f"actions:{symbol}", "financials", load)


_PROVIDER = YFinanceProvider()


def get_yfinance_provider() -> YFinanceProvider:
    """Process-wide adapter/cache used by the additive fundamentals tools."""
    return _PROVIDER
