"""Server-side owner resolution for Financial Bot.

Hosted mode requires a valid Supabase access token and never accepts an owner
from the request body, query string or a client-controlled header. The local
mode is an explicit local internal workspace (no account isolation) and is always
labelled as such in API responses. It may be shared by a few trusted developers
on one instance; it is not a multi-user authentication boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, Request

from api.supabase_auth import SupabaseUser, require_supabase_user
from api.supabase_config import SupabaseConfig

LOCAL_OWNER_ID = "00000000-0000-4000-8000-000000000001"
LOCAL_MODE = "local_single_user"
HOSTED_MODE = "supabase_authenticated"


@dataclass(frozen=True)
class OwnerContext:
    owner_id: str
    mode: str
    user: Optional[SupabaseUser] = None

    @property
    def is_local(self) -> bool:
        return self.mode == LOCAL_MODE

    @property
    def is_authenticated(self) -> bool:
        return self.mode == HOSTED_MODE

    def public(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "authenticated": self.is_authenticated,
            "owner_id": self.owner_id if self.is_local else None,
            "email": getattr(self.user, "email", None) if self.is_authenticated else None,
            "label": "本机内部开发模式（共享实例，无账号隔离）" if self.is_local else "已登录（服务端验证）",
        }


def resolve_owner(request: Request, config: SupabaseConfig) -> OwnerContext:
    if config.enabled and config.require_auth:
        user = require_supabase_user(request, config)
        return OwnerContext(owner_id=str(user.id), mode=HOSTED_MODE, user=user)
    return OwnerContext(owner_id=LOCAL_OWNER_ID, mode=LOCAL_MODE)


def require_not_local_multi_user(owner: OwnerContext) -> None:
    """Guard endpoints whose data cannot be shared between local sessions."""
    if owner.is_local:
        return
    if not owner.is_authenticated:  # defensive
        raise HTTPException(status_code=401, detail="需要服务端验证的登录状态。")
