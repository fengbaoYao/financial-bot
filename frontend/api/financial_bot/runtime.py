"""Process-wide Financial Bot runtime wiring.

`initialize()` is called from the FastAPI lifespan. Tests call `configure()` with
an isolated SQLite database instead, so no test ever touches the developer's
real task or memory data.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from api.financial_bot.config import BotConfig
from api.financial_bot.db import Database
from api.financial_bot.executor import TaskExecutor
from api.financial_bot.store import FinancialBotStore

_lock = threading.RLock()
_config: BotConfig | None = None
_store: FinancialBotStore | None = None
_executor: TaskExecutor | None = None
_recovered = 0
_openmuse_delete_recovery = None


def configure(
    *,
    config: BotConfig | None = None,
    store: FinancialBotStore | None = None,
    executor: TaskExecutor | None = None,
) -> None:
    global _config, _store, _executor, _recovered
    with _lock:
        if config is not None:
            _config = config
        if store is not None:
            _store = store
        if executor is not None:
            _executor = executor
        if _store is not None and _executor is None:
            _executor = TaskExecutor(_store)
        _recovered = 0


def reset() -> None:
    global _config, _store, _executor, _recovered
    with _lock:
        _config = None
        _store = None
        _executor = None
        _recovered = 0


def get_config(project_root: Path | None = None) -> BotConfig:
    global _config
    with _lock:
        if _config is None:
            root = project_root or Path(__file__).resolve().parents[2]
            _config = BotConfig.from_env(root)
        return _config


def get_store(project_root: Path | None = None) -> FinancialBotStore:
    global _config, _store
    with _lock:
        if _store is None:
            config = _config or BotConfig.from_env(project_root or Path(__file__).resolve().parents[2])
            _config = config
            db = Database(sqlite_path=config.data_dir / "financial_bot.sqlite3", database_url=config.database_url)
            db.initialize()
            _store = FinancialBotStore(
                db,
                backup_dir=config.backup_dir,
                third_party_retention_verified=config.third_party_retention_verified,
            )
        return _store


def get_executor(project_root: Path | None = None) -> TaskExecutor:
    global _executor
    with _lock:
        if _executor is None:
            store = _store or get_store(project_root)
            _executor = TaskExecutor(store)
        return _executor


def initialize(project_root: Path | None = None) -> int:
    """Create the store and reconcile tasks left running by a previous process.

    Local tasks with a persisted plan are resumed automatically. Hosted tasks
    are left queued with ``resume_pending`` so an authenticated user can resume
    them without the server inventing a user identity.
    """
    global _recovered
    store = get_store(project_root)
    executor = get_executor(project_root)
    _recovered = store.recover_interrupted_tasks()
    if os.environ.get("FINANCIAL_BOT_AUTO_RESUME", "1").strip() != "0":
        try:
            from api.financial_bot.auth import LOCAL_MODE, LOCAL_OWNER_ID, OwnerContext

            for row in store.list_resumable_tasks():
                if row.get("owner_id") != LOCAL_OWNER_ID:
                    continue
                executor.start(
                    OwnerContext(owner_id=LOCAL_OWNER_ID, mode=LOCAL_MODE),
                    row["id"],
                    resume=True,
                )
        except Exception:
            # A failed auto-resume must not prevent the API from starting.
            pass
    return _recovered


def recovered_task_count() -> int:
    return _recovered


async def start_openmuse_deletion_recovery(
    owner,
    *,
    config=None,
    transport=None,
    interval_seconds: float = 30.0,
) -> bool:
    """Start local SQLite deletion recovery only after explicit OpenMuse opt-in."""
    from api.financial_bot.auth import LOCAL_MODE, LOCAL_OWNER_ID, OwnerContext
    from api.financial_bot.openmuse_delete_recovery import OpenMuseDeletionRecovery
    from api.financial_bot.openmuse_tasks import OpenMuseTasksClient, OpenMuseTasksConfig

    if (
        not isinstance(owner, OwnerContext)
        or owner.mode != LOCAL_MODE
        or owner.owner_id != LOCAL_OWNER_ID
        or owner.user is not None
    ):
        return False
    effective_config = config or OpenMuseTasksConfig.from_env()
    if not isinstance(effective_config, OpenMuseTasksConfig) or not effective_config.enabled:
        return False
    if not effective_config.base_url or not effective_config.access_key:
        return False
    store = get_store()
    # The durable mapping table is deliberately unavailable to the hosted
    # authenticated database role; never start a worker against that backend.
    if store.db.dialect != "sqlite":
        return False
    global _openmuse_delete_recovery
    with _lock:
        if _openmuse_delete_recovery is not None:
            return True
        client = OpenMuseTasksClient(effective_config, transport=transport, store=store)
        recovery = OpenMuseDeletionRecovery(
            store,
            client,
            owner,
            interval_seconds=interval_seconds,
        )
        _openmuse_delete_recovery = recovery
    try:
        await recovery.start()
    except Exception:
        with _lock:
            if _openmuse_delete_recovery is recovery:
                _openmuse_delete_recovery = None
        await recovery.stop()
        raise
    return True


async def stop_openmuse_deletion_recovery() -> None:
    global _openmuse_delete_recovery
    with _lock:
        recovery = _openmuse_delete_recovery
        _openmuse_delete_recovery = None
    if recovery is not None:
        await recovery.stop()
