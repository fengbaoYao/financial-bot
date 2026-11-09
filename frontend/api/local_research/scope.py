"""Feature/scope guard for local-only research data sources.

The local mode may be shared by a few trusted developers on one instance; it
still has no account isolation and is not a hosted multi-user deployment.
"""

from __future__ import annotations

import os


def local_research_enabled() -> bool:
    """Enabled by default for the local single-user mode.

    Hosted authentication (Supabase require_auth) force-disables it because the
    source terms only cover local internal research, not multi-user trial use.
    """
    raw = os.getenv("FINANCIAL_BOT_LOCAL_RESEARCH_DATA", "1").strip().lower()
    if raw not in {"1", "true", "yes", "on"}:
        return False
    try:
        from api.main import _cloud_auth_enabled

        if _cloud_auth_enabled():
            return False
    except Exception:
        # During isolated imports/tests there is no app; env flag remains authoritative.
        pass
    return True


def local_research_scope() -> dict[str, object]:
    return {
        "enabled": local_research_enabled(),
        "scope": "local_internal_only",
        "providers": [
            "cninfo_announcements",
            "ths_consensus_eps",
            "index_constituents",
            "financial_statements",
        ],
        "authorization_note": (
            "公开网页接口，仅限本机内部研究；未取得多用户展示、缓存或再分发授权。"
            "托管多用户模式下强制关闭。"
        ),
        "raw_retention": "仅保留响应 SHA-256；不落盘第三方正文",
    }
