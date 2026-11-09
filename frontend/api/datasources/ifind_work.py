"""Work-path helpers that reuse the bot's IfindProvider.

`/work` (classic research desk) and `/bot` must share one client and one set of
env vars (`IFIND_ACCESS_TOKEN` / `ifind_api_access_token`, `IFIND_ENABLED`,
`IFIND_PRIMARY`). This module is intentionally thin: it does not hold secrets
or duplicate HTTP logic.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from api.datasources.ifind_provider import (
    history_quota_exhausted,
    history_quota_user_note,
)

logger = logging.getLogger(__name__)

_SHANGHAI = timezone(timedelta(hours=8))


def _provider():
    from api.datasources.flags import ifind_primary
    from api.datasources.ifind_provider import IfindProvider

    if not ifind_primary():
        return None
    provider = IfindProvider()
    if not provider.is_configured:
        return None
    return provider


def try_quote(code: str) -> Optional[dict[str, Any]]:
    """Best-effort live/delayed snapshot. None means caller keeps history quote."""
    provider = _provider()
    if provider is None:
        return None
    try:
        return provider.quote(code)
    except Exception as exc:  # noqa: BLE001 - provider boundary
        reason = getattr(exc, "reason", type(exc).__name__)
        logger.info("iFinD work quote skipped for %s: %s", code, reason)
        return None


def try_announcements(
    code: str,
    *,
    days: int = 60,
    limit: int = 8,
) -> Optional[dict[str, Any]]:
    """Best-effort company announcements for the work news panel."""
    provider = _provider()
    if provider is None:
        return None
    end = datetime.now(_SHANGHAI).strftime("%Y-%m-%d")
    begin = (datetime.now(_SHANGHAI) - timedelta(days=max(1, days))).strftime("%Y-%m-%d")
    try:
        return provider.announcements(code, begin=begin, end=end, limit=limit)
    except Exception as exc:  # noqa: BLE001 - provider boundary
        reason = getattr(exc, "reason", type(exc).__name__)
        logger.info("iFinD work announcements skipped for %s: %s", code, reason)
        return None


def announcements_as_fetched_news(
    code: str,
    *,
    days: int = 60,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Map iFinD announcements into the shape NewsService._map_fetched_news expects."""
    payload = try_announcements(code, days=days, limit=limit)
    if not payload:
        return []
    rows: list[dict[str, Any]] = []
    from api.datasources.announcement_text import sanitize_announcement_title

    for item in payload.get("announcements") or []:
        title = sanitize_announcement_title(str(item.get("title") or "").strip())
        if not title:
            continue
        published = str(item.get("time") or item.get("date") or "").strip()
        rows.append(
            {
                "title": title,
                "content": title,
                "summary": title,
                "source": "iFinD公告",
                "occurred_at": published,
                "url": item.get("url"),
                "relation_type": "direct",
                "provider": "ifind",
                "endpoint": payload.get("endpoint"),
                "record_key": item.get("record_key"),
            }
        )
    return rows


def history_quota_note(*, language: str = "zh") -> Optional[str]:
    """User-facing note when iFinD 历史数据 is skipped."""
    return history_quota_user_note(language=language)


def history_blocked() -> bool:
    return history_quota_exhausted()


def try_valuation(code: str) -> Optional[dict[str, Any]]:
    """Best-effort valuation snapshot via basic_data_service (基础数据)."""
    provider = _provider()
    if provider is None:
        return None
    try:
        return provider.valuation(code)
    except Exception as exc:  # noqa: BLE001
        reason = getattr(exc, "reason", type(exc).__name__)
        logger.info("iFinD work valuation skipped for %s: %s", code, reason)
        return None


def try_high_frequency(
    code: str,
    *,
    minutes: int = 30,
    interval: int = 5,
) -> Optional[dict[str, Any]]:
    """Short intraday window via high_frequency (高频) when history is blocked."""
    provider = _provider()
    if provider is None:
        return None
    end = datetime.now(_SHANGHAI)
    start = end - timedelta(minutes=max(5, int(minutes)))
    try:
        return provider.high_frequency(
            code,
            start=start.strftime("%Y-%m-%d %H:%M:%S"),
            end=end.strftime("%Y-%m-%d %H:%M:%S"),
            interval=interval,
        )
    except Exception as exc:  # noqa: BLE001
        reason = getattr(exc, "reason", type(exc).__name__)
        logger.info("iFinD work high_frequency skipped for %s: %s", code, reason)
        return None
