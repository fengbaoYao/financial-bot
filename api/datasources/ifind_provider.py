"""iFinD (同花顺) data provider - the product's highest-priority data source.

Contract (from the vendor's own skill package, `ifind-data-api` v1.1.0):

* base URL   ``https://quantapi.51ifind.com/api/v1`` (override with
  ``IFIND_DATA_API_BASE_URL``)
* auth       header ``access_token: <token>``, from ``IFIND_ACCESS_TOKEN``
  (``ifind_api_access_token`` is accepted as well, matching the skill's own
  configuration so one token serves both)
* envelope   ``{"errorcode": 0, "errmsg": "", "tables": [{"thscode": ...,
  "table": {...}}]}``; any non-zero ``errorcode`` is a failed request
* history    ``POST /history_data`` with the payload wrapped in ``reqBody``
  (a flat body returns -4210). Billed under 历史数据; -4318 trips a
  process-local circuit so /work and /bot stop retry-storming.
* quote      ``POST /real_time_quotation`` (实时行情 — prefer for latest price)
* basics     ``POST /basic_data_service`` / ``date_sequence`` / ``report_query``
  (基础数据 / 日期序列 / 公告 — still preferred when history is exhausted)

The adapter is inert without a token: it raises ``IfindUnavailable`` before any
network call, so the caller falls back to the next provider at no cost.
"""

from __future__ import annotations

import logging
import os
import re
import hashlib
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)
_REFRESH_LOCK = threading.Lock()
_REFRESHED_TOKENS: dict[str, Any] = {}


def _refresh_token() -> str:
    return (os.getenv("IFIND_REFRESH_TOKEN") or os.getenv("ifind_api_refresh_token") or "").strip()


def _cache_key(refresh: str, base_url: str) -> str:
    return hashlib.sha256((base_url + "\0" + refresh).encode()).hexdigest()


def _cached_access(refresh: str, base_url: str) -> str | None:
    if not refresh:
        return None
    key = _cache_key(refresh, base_url)
    bundle = _REFRESHED_TOKENS.get(key)
    try:
        return bundle.access_token if bundle and not bundle.is_stale() else None
    except Exception:
        return None

DEFAULT_BASE_URL = "https://quantapi.51ifind.com/api/v1"
TOKEN_ENV = ("IFIND_ACCESS_TOKEN", "ifind_api_access_token")
DEFAULT_TIMEOUT = 8

# Same-day history requests happen rarely; the quote cache stays short so a
# snapshot is never more than a minute old.
_QUOTE_TTL = 45.0

# Codes come from the vendor's own indicator dictionary (通用指标):
# 成交额 is `amt`, 换手率 `turn`, 涨跌幅 `pct_chg`.
# Only what the chart/analysis consumes: each extra indicator multiplies
# the billed cells, so amt/turn are fetched separately when a caller needs them.
_HISTORY_INDICATORS = "pre_close,open,high,low,close,volume"
_DATE_KEYS = ("date", "dates", "time", "tradeDate")


# The same task asks for the same bars several times (chart, analysis, tools).
# Each call is billed in "cells", so identical requests are cached briefly.
_TTL_SECONDS = {
    "history": 15 * 60,
    "valuation": 5 * 60,
    "announcements": 10 * 60,
    "pe_history": 60 * 60,
    "screen": 10 * 60,
}
_CACHE: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}

# history_data is billed under 历史数据. When the vendor returns -4318 the
# remaining monthly quota is zero; further identical calls only burn latency and
# can stall /work + /bot. Trip a process-local circuit until month rollover
# (Asia/Shanghai) or an explicit reset.
_HISTORY_ENDPOINTS = frozenset({"history_data"})
_HISTORY_QUOTA_EXHAUSTED_UNTIL: float | None = None
_NETWORK_UNAVAILABLE_UNTIL: float | None = None
_NETWORK_COOLDOWN_SEC = 120.0
HISTORY_QUOTA_NOTE_ZH = "iFinD 历史数据额度已用完，已改用实时/基本面/备用行情"
HISTORY_QUOTA_NOTE_EN = "iFinD historical market-data quota is exhausted; using realtime/fundamentals/fallback quotes."


def _month_end_shanghai_monotonic() -> float:
    """Approx. monotonic deadline at the next Shanghai calendar-month boundary."""
    import time as _time
    from datetime import datetime, timedelta, timezone

    shanghai = timezone(timedelta(hours=8))
    now = datetime.now(shanghai)
    if now.month == 12:
        nxt = now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=5, second=0, microsecond=0)
    else:
        nxt = now.replace(month=now.month + 1, day=1, hour=0, minute=5, second=0, microsecond=0)
    seconds = max(60.0, (nxt - now).total_seconds())
    return _time.monotonic() + seconds


def history_quota_exhausted() -> bool:
    """True when history_data should not be called (env off or -4318 circuit)."""
    import time as _time

    try:
        from api.datasources.flags import ifind_history_enabled
        if not ifind_history_enabled():
            return True
    except Exception:
        pass
    global _HISTORY_QUOTA_EXHAUSTED_UNTIL
    until = _HISTORY_QUOTA_EXHAUSTED_UNTIL
    if until is None:
        return False
    if until < _time.monotonic():
        _HISTORY_QUOTA_EXHAUSTED_UNTIL = None
        return False
    return True


def mark_history_quota_exhausted(reason: str = "errorcode:-4318") -> None:
    global _HISTORY_QUOTA_EXHAUSTED_UNTIL
    _HISTORY_QUOTA_EXHAUSTED_UNTIL = _month_end_shanghai_monotonic()
    logger.warning("iFinD history_data quota circuit open (%s) until month rollover", reason)


def clear_history_quota_circuit() -> None:
    global _HISTORY_QUOTA_EXHAUSTED_UNTIL
    _HISTORY_QUOTA_EXHAUSTED_UNTIL = None


def network_unavailable() -> bool:
    import time as _time
    global _NETWORK_UNAVAILABLE_UNTIL
    until = _NETWORK_UNAVAILABLE_UNTIL
    if until is None:
        return False
    if until < _time.monotonic():
        _NETWORK_UNAVAILABLE_UNTIL = None
        return False
    return True


def mark_network_unavailable(reason: str = "network") -> None:
    import time as _time
    global _NETWORK_UNAVAILABLE_UNTIL
    _NETWORK_UNAVAILABLE_UNTIL = _time.monotonic() + _NETWORK_COOLDOWN_SEC
    logger.warning("iFinD network circuit open (%s) for %.0fs", reason, _NETWORK_COOLDOWN_SEC)



def history_quota_user_note(*, language: str = "zh") -> str | None:
    if not history_quota_exhausted():
        return None
    return HISTORY_QUOTA_NOTE_EN if str(language).lower().startswith("en") else HISTORY_QUOTA_NOTE_ZH


def is_history_quota_error(reason: str | None) -> bool:
    text = str(reason or "")
    return "errorcode:-4318" in text or "history_quota_exhausted" in text


def _cache_get(kind: str, key: tuple[Any, ...]) -> Optional[dict[str, Any]]:
    import time as _time

    entry = _CACHE.get((kind, *key))
    if not entry:
        return None
    expires_at, value = entry
    if expires_at < _time.monotonic():
        _CACHE.pop((kind, *key), None)
        return None
    return value


def _cache_put(kind: str, key: tuple[Any, ...], value: dict[str, Any]) -> None:
    import time as _time

    _CACHE[(kind, *key)] = (_time.monotonic() + _TTL_SECONDS.get(kind, 300), value)
    if len(_CACHE) > 512:
        oldest = sorted(_CACHE.items(), key=lambda item: item[1][0])[:128]
        for cache_key, _value in oldest:
            _CACHE.pop(cache_key, None)


class IfindUnavailable(RuntimeError):
    """iFinD could not answer; the caller must fall back to another provider."""

    def __init__(self, reason: str, *, quota_exhausted: bool = False, endpoint: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.quota_exhausted = bool(quota_exhausted) or is_history_quota_error(reason)
        self.endpoint = endpoint


def _token() -> Optional[str]:
    for name in TOKEN_ENV:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return None


def configured() -> bool:
    """Whether a token exists. Everything else is decided per request."""
    return _token() is not None


def to_ths_code(code: str) -> str:
    """Map our internal codes to the vendors' ``600519.SH`` form.

    US tickers are only accepted when the caller already knows the exchange
    suffix (``AAPL.O`` / ``IBM.N``): guessing it would silently return the wrong
    instrument, so unsuffixed US symbols raise and the caller falls back.
    """
    raw = (code or "").strip().upper()
    if not raw:
        raise IfindUnavailable("empty_code")
    if raw.startswith("US."):
        symbol = raw[3:]
        if "." in symbol:
            return symbol
        raise IfindUnavailable("unsupported_us_symbol")
    if raw.startswith("SH") or raw.startswith("SZ") or raw.startswith("BJ"):
        market, digits = raw[:2], raw[2:]
        if digits.isdigit():
            return f"{digits}.{market}"
    if raw.startswith("HK"):
        digits = raw[2:]
        if digits.isdigit():
            return _hk_code(digits)
    if raw.endswith(".HK"):
        return _hk_code(raw[:-3])
    if "." in raw:
        head, _, suffix = raw.rpartition(".")
        if head and suffix.isalpha():
            # The caller passed an explicit vendor symbol (AAPL.O, BABA.N, ...).
            return raw
    if raw[:1].isdigit():
        if raw.endswith(".SH") or raw.endswith(".SZ") or raw.endswith(".BJ"):
            return raw
        if raw.isdigit() and len(raw) <= 5:
            return _hk_code(raw)
        if raw.isdigit():
            # 6-digit mainland codes: 6/68/9 -> Shanghai, 0/3 -> Shenzhen.
            return f"{raw}.{'SH' if raw[0] in {'6', '9'} else 'SZ'}"
    raise IfindUnavailable("unsupported_code")


def _today_shanghai() -> str:
    """Trading date the vendor expects: YYYY-MM-DD in Asia/Shanghai."""
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")


# Indicator codes and parameter templates come from the vendor's own dictionary
# (`get_indicators.py` / `get_indicator_params.py`); order of indiparams is
# positional, so it must not be reshuffled.
_VALUATION_SPECS: tuple[tuple[str, str, str | None], ...] = (
    ("ths_pe_ttm_stock", "pe", "100"),      # TTM 基准日=报表截止日期
    ("ths_pb_latest_stock", "pb", "100"),   # 基准日=报表截止日期
    ("ths_ps_ttm_stock", "ps", "100"),      # TTM 基准日=报表截止日期
    ("ths_market_value_stock", "market_cap", None),
)


def _hk_code(digits: str) -> str:
    """iFinD uses four-digit HK symbols (0700.HK); 00700.HK is rejected -4210."""
    try:
        value = int(digits)
    except (TypeError, ValueError):
        return f"{digits.zfill(4)}.HK"
    if value <= 0 or value > 99999:
        raise IfindUnavailable("unsupported_code")
    return f"{value:04d}.HK"


class IfindProvider:
    """Minimal, typed access to the two endpoints the product needs today."""

    def __init__(
        self,
        *,
        token: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        transport: Optional[Callable[[str, dict, dict], Any]] = None,
    ):
        self.base_url = (
            base_url
            or os.getenv("IFIND_DATA_API_BASE_URL")
            or DEFAULT_BASE_URL
        ).rstrip("/")
        self._token = token.strip() if isinstance(token, str) and token.strip() else (_cached_access(_refresh_token(), self.base_url) or _token())
        self.timeout = timeout
        # Injectable for tests: transport(url, payload, headers) -> dict
        self._transport = transport

    @property
    def is_configured(self) -> bool:
        return bool(self._token or (os.getenv("IFIND_REFRESH_TOKEN") or os.getenv("ifind_api_refresh_token") or "").strip())

    # ------------------------------------------------------------------ plumbing
    def _refresh_access_token(self) -> bool:
        refresh = _refresh_token()
        if not refresh:
            return False
        try:
            from tonghuashun_ifind_skill.auth import exchange_refresh_token
            with _REFRESH_LOCK:
                cached = _cached_access(refresh, self.base_url)
                if cached and cached != self._token:
                    self._token = cached
                    return True
                bundle = exchange_refresh_token(refresh, base_url=self.base_url, timeout=self.timeout)
                self._token = bundle.access_token
                _REFRESHED_TOKENS[_cache_key(refresh, self.base_url)] = bundle
                return bool(self._token)
        except Exception as exc:
            logger.warning("iFinD token refresh failed: %s", type(exc).__name__)
            return False

    def _post(self, endpoint: str, payload: dict[str, Any], *, _refreshed: bool = False) -> dict[str, Any]:
        if not self._token:
            if not _refreshed and self._refresh_access_token():
                return self._post(endpoint, payload, _refreshed=True)
            raise IfindUnavailable("not_configured")
        if self._transport is None and network_unavailable():
            raise IfindUnavailable("network_circuit_open")
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = {"Content-Type": "application/json", "access_token": self._token}
        if self._transport is not None:
            data = self._transport(url, payload, headers)
        else:
            import requests

            try:
                response = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
            except Exception as exc:  # noqa: BLE001 - network boundary
                mark_network_unavailable(type(exc).__name__)
                raise IfindUnavailable(f"network:{type(exc).__name__}") from None
            if response.status_code in (401, 403):
                if response.status_code == 401 and not _refreshed and self._refresh_access_token():
                    return self._post(endpoint, payload, _refreshed=True)
                raise IfindUnavailable(f"auth:{response.status_code}")
            if not 200 <= response.status_code < 300:
                raise IfindUnavailable(f"http:{response.status_code}")
            try:
                data = response.json()
            except ValueError:
                raise IfindUnavailable("non_json") from None
        if not isinstance(data, dict):
            raise IfindUnavailable("unexpected_shape")
        code = data.get("errorcode")
        if code not in (None, 0, "0"):
            if str(code) == "-1301" and not _refreshed and self._refresh_access_token():
                return self._post(endpoint, payload, _refreshed=True)
            message = str(data.get("errmsg") or "").strip()[:200]
            reason = f"errorcode:{code}:{message}"
            endpoint_name = endpoint.lstrip("/")
            if str(code) == "-4318" and endpoint_name in _HISTORY_ENDPOINTS:
                mark_history_quota_exhausted(reason)
                raise IfindUnavailable(reason, quota_exhausted=True, endpoint=endpoint_name)
            raise IfindUnavailable(reason, endpoint=endpoint_name)
        return data

    @staticmethod
    def _first_table(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return ``(entry, table)``.

        The vendor puts the symbol and, for series endpoints, the dates on the
        entry (``{"thscode", "time", "table"}``); only the indicator arrays live
        under ``table``.  Parsing ``table["date"]`` silently loses every row.
        """
        tables = data.get("tables")
        if not isinstance(tables, list) or not tables:
            raise IfindUnavailable("empty_tables")
        entry = tables[0]
        if not isinstance(entry, dict):
            raise IfindUnavailable("unexpected_shape")
        table = entry.get("table")
        if not isinstance(table, dict):
            raise IfindUnavailable("unexpected_shape")
        return entry, table

    # -------------------------------------------------------------------- reads
    def history(
        self,
        code: str,
        *,
        start: str,
        end: str,
        adjust: str = "none",
        interval: str = "D",
        currency: str = "YSHB",
    ) -> dict[str, Any]:
        """Daily bars. ``adjust``: none | forward (前复权) | backward (后复权)."""
        if history_quota_exhausted():
            raise IfindUnavailable(
                "history_quota_exhausted:-4318",
                quota_exhausted=True,
                endpoint="history_data",
            )
        ths_code = to_ths_code(code)
        cache_key = (ths_code, start, end, adjust, interval, currency)
        cached = _cache_get("history", cache_key)
        if cached is not None:
            return cached
        cps = {"none": None, "forward": "2", "backward": "1"}.get(adjust)
        functionpara: dict[str, Any] = {"Interval": interval, "Currency": currency, "Fill": "Previous"}
        if cps:
            functionpara["CPS"] = cps
        body = {
            "reqBody": {
                "codes": ths_code,
                "indicators": _HISTORY_INDICATORS,
                "startdate": start,
                "enddate": end,
                "functionpara": functionpara,
            }
        }
        data = self._post("history_data", body)
        entry, table = self._first_table(data)
        dates = self._dates(entry, table)
        bars: list[dict[str, Any]] = []
        for index, day in enumerate(dates):
            close = _at(table.get("close"), index)
            if close is None:
                continue
            bars.append(
                {
                    "date": day,
                    "open": _at(table.get("open"), index),
                    "high": _at(table.get("high"), index),
                    "low": _at(table.get("low"), index),
                    "close": close,
                    "volume": _at(table.get("volume"), index),
                    "amount": _at(table.get("amt"), index),
                    "turnover": _at(table.get("turn"), index),
                }
            )
        if not bars:
            raise IfindUnavailable("empty_history")
        result = {
            "bars": bars,
            "provider": "ifind",
            "thscode": ths_code,
            "adjustment": {"none": "none", "forward": "qfq", "backward": "hfq"}[adjust],
            "currency": currency,
            "data_as_of": bars[-1]["date"],
            "request_id": str(data.get("perf") or ""),
        }
        _cache_put("history", cache_key, result)
        return result

    def quote(self, code: str) -> dict[str, Any]:
        ths_code = to_ths_code(code)
        body = {
            "codes": ths_code,
            "indicators": "latest,change,changeRatio,preClose,volume,amount,tradeDate,tradeTime",
        }
        data = self._post("real_time_quotation", body)
        _, table = self._first_table(data)
        latest = _at(table.get("latest"), 0)
        if latest is None:
            raise IfindUnavailable("empty_quote")
        trade_date = _raw_at(table.get("tradeDate"), 0)
        trade_time = _raw_at(table.get("tradeTime"), 0)
        return {
            "provider": "ifind",
            "thscode": ths_code,
            "price": latest,
            "change": _at(table.get("change"), 0),
            "change_pct": _at(table.get("changeRatio"), 0),
            "previous_close": _at(table.get("preClose"), 0),
            "volume": _at(table.get("volume"), 0),
            "amount": _at(table.get("amount"), 0),
            "data_as_of": " ".join(part for part in (str(trade_date or ""), str(trade_time or "")) if part) or None,
            "price_status": "delayed_or_snapshot",
        }

    def valuation(self, code: str, *, as_of: str | None = None) -> dict[str, Any]:
        """Valuation snapshot (PE / PB / PS / market cap) for one code."""
        ths_code = to_ths_code(code)
        day = as_of or _today_shanghai()
        cached = _cache_get("valuation", (ths_code, day))
        if cached is not None:
            return cached
        indipara = [
            {"indicator": indicator, "indiparams": [day] if param is None else [day, param]}
            for indicator, _name, param in _VALUATION_SPECS
        ]
        data = self._post("basic_data_service", {"codes": ths_code, "indipara": indipara})
        _, table = self._first_table(data)
        values = {name: _at(table.get(indicator), 0) for indicator, name, _param in _VALUATION_SPECS}
        if all(value is None for value in values.values()):
            raise IfindUnavailable("empty_valuation")
        result = {
            "provider": "ifind",
            "thscode": ths_code,
            "as_of": day,
            "pe": values["pe"],
            "pb": values["pb"],
            "ps": values["ps"],
            "market_cap": values["market_cap"],
        }
        _cache_put("valuation", (ths_code, day), result)
        return result

    def announcements(
        self,
        code: str,
        *,
        begin: str,
        end: str,
        limit: int = 8,
    ) -> dict[str, Any]:
        """Announcement titles plus PDF links for one code."""
        ths_code = to_ths_code(code)
        body = {
            "codes": ths_code,
            # Date filters are top-level keys; put them in functionpara and the
            # vendor silently ignores the filter.
            "beginrDate": begin,
            "endrDate": end,
            "outputpara": "reportDate:Y,thscode:Y,secName:Y,ctime:Y,reportTitle:Y,pdfURL:Y,seq:Y",
        }
        data = self._post("report_query", body)
        _, table = self._first_table(data)
        titles = table.get("reportTitle") if isinstance(table.get("reportTitle"), list) else []
        dates = table.get("reportDate") if isinstance(table.get("reportDate"), list) else []
        times = table.get("ctime") if isinstance(table.get("ctime"), list) else []
        urls = table.get("pdfURL") if isinstance(table.get("pdfURL"), list) else []
        seqs = table.get("seq") if isinstance(table.get("seq"), list) else []
        rows = []
        from api.datasources.announcement_text import sanitize_announcement_title
        from api.evidence import safe_source_url

        for index, title in enumerate(titles[: max(1, int(limit))]):
            text = sanitize_announcement_title(str(title or "").strip())
            if not text:
                continue
            rows.append(
                {
                    "title": text,
                    "date": str(_raw_at(dates, index) or "")[:10],
                    "time": _raw_at(times, index),
                    "url": safe_source_url(_raw_at(urls, index)),
                    "record_key": _raw_at(seqs, index),
                }
            )
        if not rows:
            raise IfindUnavailable("empty_announcements")
        return {
            "provider": "ifind",
            "thscode": ths_code,
            "endpoint": "quantapi.51ifind.com/api/v1/report_query",
            "data_as_of": rows[0]["date"],
            "announcements": rows,
        }

    def edb(self, indicators: str, *, start: str, end: str) -> dict[str, Any]:
        """Macro / industry series. Requires an EDB entitlement on the account."""
        body = {"indicators": indicators, "startdate": start, "enddate": end}
        data = self._post("edb_service", body)
        entry, table = self._first_table(data)
        dates = self._dates(entry, table)
        series = []
        for key, values in table.items():
            if key in _DATE_KEYS or not isinstance(values, list):
                continue
            series.append(
                {
                    "indicator": key,
                    "points": [
                        {"date": day, "value": _at(values, index)}
                        for index, day in enumerate(dates)
                        if _at(values, index) is not None
                    ],
                }
            )
        if not series:
            raise IfindUnavailable("empty_edb")
        return {"provider": "ifind", "series": series, "data_as_of": dates[-1] if dates else None}

    def series(
        self,
        code: str,
        *,
        indicator: str,
        indiparams: list[str],
        start: str,
        end: str,
    ) -> dict[str, Any]:
        """Daily indicator series via ``date_sequence`` (PE history, etc.)."""
        ths_code = to_ths_code(code)
        body = {
            "codes": ths_code,
            "indipara": [{"indicator": indicator, "indiparams": list(indiparams)}],
            "startdate": start,
            "enddate": end,
        }
        data = self._post("date_sequence", body)
        entry, table = self._first_table(data)
        values = table.get(indicator)
        if not isinstance(values, list) or not values:
            raise IfindUnavailable("empty_series")
        dates = self._dates(entry, table)
        points = []
        for index, day in enumerate(dates):
            value = _at(values, index)
            if value is not None:
                points.append({"date": day, "value": value})
        if not points:
            raise IfindUnavailable("empty_series")
        return {"provider": "ifind", "thscode": ths_code, "indicator": indicator, "points": points}

    def pe_history(self, code: str, *, years: int = 5) -> dict[str, Any]:
        """Five years of PE(TTM) plus where today sits in that range."""
        from datetime import datetime, timedelta, timezone

        ths_code = to_ths_code(code)
        cached = _cache_get("pe_history", (ths_code, years, _today_shanghai()))
        if cached is not None:
            return cached
        end = datetime.now(timezone(timedelta(hours=8)))
        start = end - timedelta(days=365 * max(1, int(years)))
        data = self.series(
            code,
            indicator="ths_pe_ttm_stock",
            indiparams=["100"],
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
        )
        values = sorted(point["value"] for point in data["points"])
        latest = data["points"][-1]["value"]
        below = sum(1 for value in values if value <= latest)
        data["percentile"] = round(below / len(values) * 100, 1)
        data["min"] = values[0]
        data["max"] = values[-1]
        data["years"] = years
        _cache_put("pe_history", (ths_code, years, _today_shanghai()), data)
        return data

    def high_frequency(
        self,
        code: str,
        *,
        start: str,
        end: str,
        interval: int = 5,
    ) -> dict[str, Any]:
        """Intraday bars (1/3/5/10/15/30/60 minutes). Free tier: last year only."""
        if interval not in {1, 3, 5, 10, 15, 30, 60}:
            raise IfindUnavailable("bad_interval")
        ths_code = to_ths_code(code)
        body = {
            "codes": ths_code,
            "indicators": "open,high,low,close,volume",
            "starttime": start,
            "endtime": end,
            "functionpara": {"Interval": str(interval)},
        }
        data = self._post("high_frequency", body)
        entry, table = self._first_table(data)
        times = entry.get("time") if isinstance(entry.get("time"), list) else []
        bars = []
        for index, moment in enumerate(times):
            close = _at(table.get("close"), index)
            if close is None:
                continue
            bars.append({
                "time": str(moment),
                "open": _at(table.get("open"), index),
                "high": _at(table.get("high"), index),
                "low": _at(table.get("low"), index),
                "close": close,
                "volume": _at(table.get("volume"), index),
            })
        if not bars:
            raise IfindUnavailable("empty_high_frequency")
        return {"provider": "ifind", "thscode": ths_code, "interval_minutes": interval, "bars": bars}

    def smart_query(self, query: str) -> dict[str, Any]:
        """Route a natural-language query with the installed iFinD skill."""
        try:
            from tonghuashun_ifind_skill.routing import build_route_plan, build_realtime_plan, build_history_plan, build_generic_smart_query_plan, extract_entity_from_search_payload, ResolvedEntity
        except ImportError as exc:
            raise IfindUnavailable("skill_not_installed") from exc

        def lookup(text: str):
            try:
                raw = self._post("smart_stock_picking", {"searchstring": f"{text} 股票代码 股票简称", "searchtype": "stock"})
                return extract_entity_from_search_payload(text, raw)
            except IfindUnavailable:
                return None

        candidates = [value for value in re.findall(r"\b[A-Z]{2,5}\b", query)
                      if value not in {"HK", "SH", "SZ", "US", "USD", "HKD", "CNY", "ETF", "GDP", "EPS", "ROE", "PE", "PB", "AI", "TTM", "CEO", "CFO", "IPO", "API", "SEC", "MACD", "RSI", "AND", "VS"}]
        candidates.extend(value.upper() for value in re.findall(r"(?<![A-Za-z])([a-z]{2,5})(?=\s*(?:股价|股票|财报|营收|估值|公告))", query))
        candidates = list(dict.fromkeys(candidates))
        if len(set(candidates)) > 1:
            raise IfindUnavailable("skill_multi_symbol_unsupported")
        us_ticker = candidates[0] if len(candidates) == 1 and not re.search(r"\d{4,6}\.(?:HK|SH|SZ)\b", query, re.I) else None
        if us_ticker:
            plan = build_generic_smart_query_plan(query)
            searchstring = plan.payload.get("searchstring", query)
            if re.search(r"(?:怎么样|表现如何|how\s+is\s+.+doing)[？?\s]*$", query, re.I) and not re.search(r"财报|营收|净利|公告|估值|dividend|earnings|revenue|valuation", query, re.I):
                searchstring = f"{us_ticker} 最新价"
            plan = __import__("dataclasses").replace(plan, payload={**plan.payload, "searchstring": searchstring, "searchtype": "US_stock"})
        else:
            plan = build_route_plan(query, entity_lookup=lookup)
        if (plan.intent == "quote_realtime" and plan.entity
                and re.search(r"近(?:一|1)(?:个)?月", query)):
            import calendar
            from datetime import date
            today = date.fromisoformat(_today_shanghai())
            year = today.year if today.month > 1 else today.year - 1
            month = today.month - 1 if today.month > 1 else 12
            start = date(year, month, min(today.day, calendar.monthrange(year, month)[1]))
            plan = build_history_plan(plan.entity, query=query, today=today, start_date=start.isoformat(), end_date=today.isoformat())
        if (plan.intent == "quote_realtime"
                and re.search(r"公告|回购|分红|减持|增持|解禁|停牌|复牌|研报|融资|持股|buyback|dividend|filing", query, re.I)):
            plan = build_generic_smart_query_plan(query, entity=plan.entity)
        hk_symbols = re.findall(r"(?<!\d)(\d{4,5}\.HK)\b", query, re.I)
        if (not plan.endpoint and len(hk_symbols) == 1
                and re.search(r"最新|现价|报价|latest|quote", query, re.I)
                and not re.search(r"历史|走势|近[一二三四五六七八九十\d]+(?:个)?(?:天|日|周|月|年)|history|historical", query, re.I)):
            symbol = to_ths_code(hk_symbols[0])
            plan = build_realtime_plan(ResolvedEntity(raw=hk_symbols[0], symbol=symbol, name=None, entity_type="stock"))
        if (plan.intent == "quote_realtime"
                and re.search(r"历史|走势|近[一二三四五六七八九十\d]+(?:个)?(?:天|日|周|月|年)|history|historical", query, re.I)):
            raise IfindUnavailable("skill_route_mismatch")
        if not plan.endpoint or not isinstance(plan.payload, dict):
            raise IfindUnavailable("skill_route_unavailable")
        allowed = {"real_time_quotation", "cmd_history_quotation", "smart_stock_picking", "date_sequence", "basic_data_service"}
        endpoint = plan.endpoint.lstrip("/")
        if endpoint not in allowed:
            raise IfindUnavailable("skill_endpoint_unavailable")
        if endpoint == "cmd_history_quotation" and history_quota_exhausted():
            raise IfindUnavailable("history_quota_exhausted:-4318", quota_exhausted=True, endpoint=endpoint)
        result = self._post(endpoint, plan.payload)
        if not result.get("tables"):
            raise IfindUnavailable("skill_empty_result")
        if us_ticker:
            codes = []
            substantive = False
            for entry in result.get("tables") or []:
                if not isinstance(entry, dict):
                    continue
                table = entry.get("table") or {}
                for key, values in table.items():
                    if ("代码" in key or key.lower() in {"code", "thscode"}) and isinstance(values, list):
                        codes.extend(str(value).upper() for value in values if value)
                    elif not any(term in key.lower() for term in ("简称", "名称", "name")) and isinstance(values, list):
                        substantive = substantive or any(value is not None and str(value).strip() not in {"", "--", "null"} for value in values)
            if not any(code.split(".")[0] == us_ticker for code in codes):
                raise IfindUnavailable("skill_identity_mismatch")
            if not substantive:
                raise IfindUnavailable("skill_no_substantive_data")
        if endpoint == "real_time_quotation":
            tables = result.get("tables") or []
            if not any(
                _at((entry.get("table") or {}).get("latest"), 0) is not None
                for entry in tables if isinstance(entry, dict)
            ):
                raise IfindUnavailable("skill_empty_result")
        return {"provider": "ifind", "endpoint": endpoint, "intent": plan.intent, "data": result}

    def screen(self, query: str, *, market: str = "stock", limit: int = 30) -> dict[str, Any]:
        """Natural-language screening (THS_WC).

        Column names come back in Chinese and depend on the query, so they are
        passed through as-is instead of being mapped to a fixed schema.
        """
        body = {"searchstring": str(query or "").strip(), "searchtype": market}
        if not body["searchstring"]:
            raise IfindUnavailable("empty_query")
        data = self._post("smart_stock_picking", body)
        _, table = self._first_table(data)
        columns = [name for name, values in table.items() if isinstance(values, list) and values]
        if not columns:
            raise IfindUnavailable("empty_screen")
        total = max(len(table[name]) for name in columns)
        code_column = next(
            (name for name in columns if "代码" in name or name.lower() in {"thscode", "code"}),
            columns[0],
        )
        rows = []
        for index in range(min(total, max(1, int(limit)))):
            rows.append({name: (_raw_at(table[name], index) or "") for name in columns})
        return {
            "provider": "ifind",
            "endpoint": "quantapi.51ifind.com/api/v1/smart_stock_picking",
            "query": body["searchstring"],
            "market": market,
            "count": total,
            "code_column": code_column,
            "columns": columns,
            "rows": rows,
        }

    def constituents(self, block_id: str, *, day: str | None = None, limit: int = 400) -> dict[str, Any]:
        """Block/index constituents via the p03291 report.

        ``block_id`` must come from the vendor's block dictionary; guessing it
        returns an empty list rather than an error.
        """
        date = (day or _today_shanghai()).replace("-", "")
        body = {
            "reportname": "p03291",
            "functionpara": {"date": date, "blockname": block_id, "iv_type": "allcontract"},
            "outputpara": "p03291_f001,p03291_f002,p03291_f003,p03291_f004",
        }
        data = self._post("data_pool", body)
        _, table = self._first_table(data)
        codes = table.get("p03291_f002")
        names = table.get("p03291_f003")
        if not isinstance(codes, list) or not codes:
            raise IfindUnavailable("empty_constituents")
        rows = []
        for index, code in enumerate(codes[: max(1, int(limit))]):
            text = str(code or "").strip()
            if not text:
                continue
            rows.append({"thscode": text, "name": _raw_at(names, index)})
        return {"provider": "ifind", "block_id": block_id, "date": date, "count": len(codes), "rows": rows}

    @staticmethod
    def _dates(entry: dict[str, Any], table: dict[str, Any]) -> list[str]:
        for source in (entry, table):
            for key in _DATE_KEYS:
                values = source.get(key)
                if isinstance(values, list) and values:
                    return [str(value) for value in values]
        raise IfindUnavailable("missing_dates")


def _raw_at(values: Any, index: int) -> Optional[str]:
    if not isinstance(values, list) or index >= len(values):
        return None
    value = values[index]
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _at(values: Any, index: int) -> Optional[float]:
    if not isinstance(values, list) or index >= len(values):
        return None
    value = values[index]
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
