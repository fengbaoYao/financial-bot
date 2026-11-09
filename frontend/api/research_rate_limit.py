"""Cross-request call-frequency limits for research and agent endpoints.

Single-run token budgets no longer interrupt deep synthesis by default. Abuse
and cost control instead use a sliding-window request rate keyed by owner, auth
user, or client IP. Limits are env-configurable.
"""

from __future__ import annotations

import os
from collections import defaultdict, deque
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


def _bounded_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


def research_rate_limit_config() -> tuple[int, int]:
    """Return (max_requests, window_seconds).

    Defaults: 30 research/agent starts per rolling hour. Set
    FINANCIAL_BOT_RESEARCH_RATE_LIMIT=0 to disable.
    """
    limit = _bounded_int("FINANCIAL_BOT_RESEARCH_RATE_LIMIT", 30, minimum=0, maximum=10_000)
    window = _bounded_int(
        "FINANCIAL_BOT_RESEARCH_RATE_WINDOW_SECONDS",
        3600,
        minimum=1,
        maximum=86_400,
    )
    return limit, window


class ResearchRateLimiter:
    """In-process sliding-window limiter for research starts."""

    def __init__(self) -> None:
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, *, subject: str, bucket: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        if limit <= 0:
            return True, 0
        now = datetime.now(timezone.utc).timestamp()
        key = (subject, bucket)
        with self._lock:
            events = self._events[key]
            cutoff = now - window_seconds
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, int(events[0] + window_seconds - now) + 1)
                return False, retry_after
            events.append(now)
        return True, 0

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


research_rate_limiter = ResearchRateLimiter()


def research_rate_subject(request: Request, *, owner_id: str | None = None) -> str:
    if owner_id:
        return f"owner:{owner_id}"
    user = getattr(request.state, "supabase_user", None)
    user_id = getattr(user, "id", None) if user is not None else None
    if user_id:
        return f"user:{user_id}"
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    host = forwarded or (request.client.host if request.client else "unknown")
    return f"ip:{host or 'unknown'}"


def enforce_research_rate_limit(
    request: Request,
    *,
    owner_id: str | None = None,
    language: str | None = None,
    bucket: str = "research",
) -> JSONResponse | None:
    """Return a 429 JSONResponse when the caller exceeds the research rate."""
    limit, window = research_rate_limit_config()
    if limit <= 0:
        return None
    subject = research_rate_subject(request, owner_id=owner_id)
    allowed, retry_after = research_rate_limiter.allow(
        subject=subject,
        bucket=bucket,
        limit=limit,
        window_seconds=window,
    )
    if allowed:
        return None
    zh = (language or "").lower().startswith("zh") or language in {None, "", "zh-CN"}
    if language == "en-US":
        zh = False
    detail = (
        f"研究调用过于频繁：每 {window} 秒最多 {limit} 次。请约 {retry_after} 秒后再试。"
        if zh
        else f"Research calls are rate-limited to {limit} per {window} seconds. Retry in about {retry_after} seconds."
    )
    return JSONResponse(
        status_code=429,
        content={
            "detail": detail,
            "code": "research_rate_limit",
            "limit": limit,
            "window_seconds": window,
            "retry_after": retry_after,
        },
        headers={"Retry-After": str(retry_after)},
    )


def research_rate_limit_snapshot() -> dict[str, Any]:
    limit, window = research_rate_limit_config()
    return {"limit": limit, "window_seconds": window, "enabled": limit > 0}
