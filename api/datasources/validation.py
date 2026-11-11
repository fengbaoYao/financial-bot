"""Small data-quality gates used before a provider result enters research.

The goal is not to reject imperfect data silently; it is to turn structurally
broken data into an explicit gap so the fallback source can run.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any


def _finite(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


def _day(value: Any) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None


def validate_price_bars(bars: list[dict]) -> list[str]:
    """Return structural errors for a daily bar list (empty means valid)."""
    errors: list[str] = []
    if not bars:
        return ["no price bars"]
    previous: date | None = None
    for index, bar in enumerate(bars):
        day = _day(bar.get("date"))
        if day is None:
            errors.append(f"bar[{index}] has no valid date")
            continue
        if previous is not None and day < previous:
            errors.append(f"bar[{index}] is out of order")
        previous = day
        for field in ("open", "high", "low", "close"):
            if not _finite(bar.get(field)):
                errors.append(f"bar[{index}] has invalid {field}")
        open_, high, low, close = (bar.get(field) for field in ("open", "high", "low", "close"))
        if all(_finite(value) for value in (open_, high, low, close)):
            if high + 1e-9 < max(open_, close, low):
                errors.append(f"bar[{index}] high is below OHLC body")
            if low - 1e-9 > min(open_, close, high):
                errors.append(f"bar[{index}] low is above OHLC body")
        volume = bar.get("volume")
        if volume is not None and (not _finite(volume) or float(volume) < 0):
            errors.append(f"bar[{index}] has invalid volume")
        if len(errors) >= 8:
            break
    return errors


def validate_financial_periods(rows: list[dict]) -> list[str]:
    """Return structural errors for normalized financial periods."""
    errors: list[str] = []
    if not rows:
        return ["no financial periods"]
    seen: set[str] = set()
    for index, row in enumerate(rows):
        period = str(row.get("period") or "")
        if _day(period) is None:
            errors.append(f"period[{index}] has no valid date")
        elif period in seen:
            errors.append(f"period[{index}] is duplicated")
        seen.add(period)
        items = row.get("items")
        if not isinstance(items, dict) or not items:
            errors.append(f"period[{index}] has no key items")
            continue
        if all(value is None for value in items.values()):
            errors.append(f"period[{index}] has only null values")
        for key, value in items.items():
            if value is not None and not _finite(value):
                errors.append(f"period[{index}].{key} is not numeric")
        if len(errors) >= 8:
            break
    return errors
