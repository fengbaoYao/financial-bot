"""Bounded lifecycle runner for durable OpenMuse task deletion intents."""

from __future__ import annotations

import asyncio
import logging

from api.financial_bot.auth import OwnerContext
from api.financial_bot.openmuse_delete_reconciler import OpenMuseDeletionReconciler
from api.financial_bot.openmuse_tasks import OpenMuseTasksClient

logger = logging.getLogger(__name__)


class OpenMuseDeletionRecovery:
    def __init__(
        self,
        store,
        client: OpenMuseTasksClient,
        owner: OwnerContext,
        *,
        interval_seconds: float = 30.0,
        batch_size: int = 5,
        pass_timeout_seconds: float = 60.0,
    ) -> None:
        self.store = store
        self.reconciler = OpenMuseDeletionReconciler(store, client)
        self.client = client
        self.owner = owner
        self.interval_seconds = max(0.05, min(float(interval_seconds), 300.0))
        self.batch_size = max(1, min(int(batch_size), 5))
        self.pass_timeout_seconds = max(1.0, min(float(pass_timeout_seconds), 60.0))
        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="openmuse-deletion-recovery")

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=self.pass_timeout_seconds + 1.0)
            except asyncio.TimeoutError:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        await self.client.close()

    async def _run(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self.reconciler.reconcile(self.owner, limit=self.batch_size),
                    timeout=self.pass_timeout_seconds,
                )
                self.store.finalize_pending_session_deletions(
                    self.owner.owner_id,
                    limit=self.batch_size,
                )
            except asyncio.TimeoutError:
                logger.warning("OpenMuse deletion recovery pass timed out; mappings remain pending")
            except Exception as exc:
                # Reconciler and HTTP errors can contain user data or secrets.
                logger.warning(
                    "OpenMuse deletion recovery pass failed (%s); pending records are preserved",
                    type(exc).__name__,
                )
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass
