"""Configuration for the Financial Bot domain.

Secrets are never read here. Only non-secret operational limits and storage
locations are resolved, and every default keeps the multi-user trial gate closed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class BotConfig:
    data_dir: Path
    database_url: str | None = None
    daily_task_limit: int = 200
    daily_token_limit: int = 2_000_000
    backup_dir: Path | None = None
    third_party_retention_verified: bool = False
    allow_local_single_user: bool = True
    enable_postgres: bool = False
    task_event_retention_days: int = 365

    @classmethod
    def from_env(cls, project_root: Path) -> "BotConfig":
        data_dir = Path(os.environ.get("FINANCIAL_BOT_DATA_DIR", project_root / "data" / "financial_bot"))
        database_url = os.environ.get("DATABASE_URL", "").strip() or None
        backup_raw = os.environ.get("FINANCIAL_BOT_BACKUP_DIR", "").strip()
        backup_dir = Path(backup_raw) if backup_raw else None
        return cls(
            data_dir=data_dir,
            database_url=database_url,
            daily_task_limit=_int_env("FINANCIAL_BOT_DAILY_TASK_LIMIT", 200, minimum=1, maximum=10_000),
            daily_token_limit=_int_env("FINANCIAL_BOT_DAILY_TOKEN_LIMIT", 2_000_000, minimum=0, maximum=2_000_000_000),
            backup_dir=backup_dir,
            third_party_retention_verified=_bool_env("FINANCIAL_BOT_THIRD_PARTY_RETENTION_VERIFIED", False),
            allow_local_single_user=_bool_env("FINANCIAL_BOT_ALLOW_LOCAL_SINGLE_USER", True),
            enable_postgres=bool(database_url),
            task_event_retention_days=_int_env("FINANCIAL_BOT_TASK_EVENT_RETENTION_DAYS", 365, minimum=1, maximum=3650),
        )
