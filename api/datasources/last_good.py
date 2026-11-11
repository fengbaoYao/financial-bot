"""Persistent last-good cache for price history.

The in-process DataFetcher cache disappears on restart.  This small JSON cache
keeps the most recent successful bars so a provider outage can return an
explicitly *stale* snapshot instead of no data.  It never stores credentials,
and stale data is always labelled as such.

Limits:
  * default max age: 720 hours (30 days);
  * at most 200 keys; oldest entries are pruned;
  * file mode 600, directory mode 700;
  * no write if the cache path is invalid (research continues without it).
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ashare.config import PROJECT_ROOT

_LOCK = threading.Lock()
_VERSION = 1
_SCHEMA_VERSION = 1
_MAX_ENTRIES = 200


def _path() -> Path:
    raw = os.getenv("LAST_GOOD_CACHE_PATH", "").strip()
    return Path(raw).expanduser() if raw else PROJECT_ROOT / "data" / "last-good-cache.json"


def _max_age_hours() -> float:
    try:
        return max(1.0, float(os.getenv("LAST_GOOD_MAX_AGE_HOURS", "720")))
    except ValueError:
        return 720.0


def _load() -> dict:
    path = _path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": _VERSION, "entries": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), dict):
        return {"version": _VERSION, "entries": {}}
    return payload


def _save(payload: dict) -> None:
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(path)
    except OSError:
        return


def put(namespace: str, key: str, value: Any, *, provider: str, data_as_of: str | None, adjustment: str = "unknown") -> None:
    """Store one successful result. Payloads must be JSON serializable."""
    if not namespace or not key:
        return
    now = datetime.now(timezone.utc)
    with _LOCK:
        payload = _load()
        entries = payload.setdefault("entries", {})
        entries[f"{namespace}:{key}"] = {
            "stored_at": now.isoformat(timespec="seconds"),
            "stored_ts": time.time(),
            "schema_version": _SCHEMA_VERSION,
            "provider": provider,
            "adjustment": adjustment,
            "data_as_of": data_as_of,
            "value": value,
        }
        if len(entries) > _MAX_ENTRIES:
            ordered = sorted(entries.items(), key=lambda item: float(item[1].get("stored_ts") or 0))
            for name, _ in ordered[: len(entries) - _MAX_ENTRIES]:
                entries.pop(name, None)
        _save(payload)


def get(namespace: str, key: str, *, max_age_hours: float | None = None) -> Optional[dict]:
    """Return a fresh-enough last-good entry, marked stale."""
    if not namespace or not key:
        return None
    max_age = _max_age_hours() if max_age_hours is None else max(0.1, float(max_age_hours))
    with _LOCK:
        payload = _load()
        entry = (payload.get("entries") or {}).get(f"{namespace}:{key}")
        if not isinstance(entry, dict):
            return None
        try:
            entry_version = int(entry.get("schema_version") or 0)
        except (TypeError, ValueError):
            return None
        if entry_version > _SCHEMA_VERSION:
            return None
        try:
            stored_ts = float(entry.get("stored_ts") or 0)
        except (TypeError, ValueError):
            return None
        if time.time() - stored_ts > max_age * 3600:
            return None
        return {
            "value": entry.get("value"),
            "provider": entry.get("provider") or "unknown",
            "adjustment": entry.get("adjustment") or "unknown",
            "data_as_of": entry.get("data_as_of"),
            "stored_at": entry.get("stored_at"),
            "schema_version": entry.get("schema_version") or 1,
            "stale": True,
        }


def snapshot() -> dict:
    with _LOCK:
        payload = _load()
        entries = payload.get("entries") or {}
        return {
            "path": str(_path()),
            "entries": len(entries),
            "keys": sorted(entries)[:50],
            "max_age_hours": _max_age_hours(),
        }


def clear() -> None:
    with _LOCK:
        _save({"version": _VERSION, "entries": {}})
