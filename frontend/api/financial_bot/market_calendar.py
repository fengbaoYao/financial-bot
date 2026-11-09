"""Small, offline exchange-holiday calendar used by the polling worker.

This is intentionally a conservative built-in table, not a live exchange feed.
It covers regular full-day closures for 2026-2027. Half-days and ad-hoc closures
are not modelled; the UI/docs must describe the calendar as approximate.
"""

from __future__ import annotations

import os
from datetime import date, time


def market_for_symbol(symbol: str) -> str:
    upper = (symbol or "").upper()
    if upper.endswith((".SS", ".SZ")) or upper.isdigit():
        return "cn"
    if upper.endswith(".HK"):
        return "hk"
    return "us"


def _dates(values: str) -> set[date]:
    return {date.fromisoformat(value) for value in values.split()}


# Full-day regular-session closures only.
HOLIDAYS: dict[str, set[date]] = {
    "us": _dates(
        "2026-01-01 2026-01-19 2026-02-16 2026-04-03 2026-05-25 "
        "2026-06-19 2026-07-03 2026-09-07 2026-11-26 2026-12-25 "
        "2027-01-01 2027-01-18 2027-02-15 2027-03-26 2027-05-31 "
        "2027-06-18 2027-07-05 2027-09-06 2027-11-25 2027-12-24"
    ),
    "hk": _dates(
        "2026-01-01 2026-02-17 2026-02-18 2026-02-19 2026-04-03 "
        "2026-04-04 2026-04-06 2026-04-07 2026-05-01 2026-05-25 "
        "2026-06-19 2026-07-01 2026-09-26 2026-10-01 2026-10-19 "
        "2026-12-25 2026-12-26 "
        "2027-01-01 2027-02-06 2027-02-08 2027-02-09 2027-03-26 "
        "2027-03-27 2027-03-29 2027-04-05 2027-05-01 2027-05-13 "
        "2027-06-09 2027-07-01 2027-09-16 2027-10-01 2027-10-08 "
        "2027-12-25 2027-12-27"
    ),
    "cn": _dates(
        "2026-01-01 2026-01-02 2026-01-03 2026-02-16 2026-02-17 "
        "2026-02-18 2026-02-19 2026-02-20 2026-02-21 2026-02-22 "
        "2026-04-04 2026-04-05 2026-04-06 2026-05-01 2026-05-02 "
        "2026-05-03 2026-05-04 2026-05-05 2026-06-19 2026-06-20 "
        "2026-06-21 2026-09-25 2026-09-26 2026-09-27 2026-10-01 "
        "2026-10-02 2026-10-03 2026-10-04 2026-10-05 2026-10-06 "
        "2026-10-07 "
        "2027-01-01 2027-02-05 2027-02-06 2027-02-07 2027-02-08 "
        "2027-02-09 2027-02-10 2027-02-11 2027-04-04 2027-04-05 "
        "2027-05-01 2027-05-02 2027-05-03 2027-05-04 2027-05-05 "
        "2027-06-09 2027-09-15 2027-10-01 2027-10-02 2027-10-03 "
        "2027-10-04 2027-10-05 2027-10-06 2027-10-07"
    ),
}


# Early closes. The value is the final regular-session clock time.
HALF_DAYS: dict[str, dict[date, time]] = {
    "us": {date(2026, 11, 27): time(13, 0), date(2026, 12, 24): time(13, 0)},
    "hk": {date(2026, 12, 24): time(12, 0), date(2026, 12, 31): time(12, 0)},
    "cn": {},
}


def ad_hoc_closures() -> set[date]:
    raw = os.environ.get("FINANCIAL_BOT_CALENDAR_CLOSURES", "")
    values = {item.strip() for item in raw.split(",") if item.strip()}
    result = set()
    for value in values:
        try:
            result.add(date.fromisoformat(value))
        except ValueError:
            continue
    return result


def is_market_holiday(symbol: str, day: date) -> bool:
    return day in HOLIDAYS.get(market_for_symbol(symbol), set()) or day in ad_hoc_closures()


def session_windows(symbol: str, day: date) -> list[tuple[time, time]]:
    market = market_for_symbol(symbol)
    if market == "cn":
        windows = [(time(9, 30), time(11, 30)), (time(13, 0), time(15, 0))]
    elif market == "hk":
        windows = [(time(9, 30), time(12, 0)), (time(13, 0), time(16, 0))]
    else:
        windows = [(time(9, 30), time(16, 0))]
    close_override = HALF_DAYS.get(market, {}).get(day)
    if close_override is not None and windows:
        start, _ = windows[-1]
        windows[-1] = (start, close_override)
    return windows
