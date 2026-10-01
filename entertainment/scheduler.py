"""Single-task supervisor for quiet/cooldown entertainment opportunities."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Protocol

LOGGER = logging.getLogger(__name__)


class SupervisorService(Protocol):
    async def run_supervisor_tick(self) -> None: ...


class EntertainmentSupervisor:
    """Own exactly one bounded background task for one service instance."""

    def __init__(self, service: SupervisorService, *, interval_seconds: float = 60.0) -> None:
        self.service = service
        self.interval_seconds = max(0.01, float(interval_seconds))
        self.task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self.task is not None and not self.task.done():
            return
        self.task = asyncio.create_task(self._run(), name="entertainment-supervisor")

    async def stop(self) -> None:
        task = self.task
        if task is None:
            return
        self.task = None
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        while True:
            try:
                await self.service.run_supervisor_tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Entertainment supervisor tick failed")
            await asyncio.sleep(self.interval_seconds)


__all__ = ["EntertainmentSupervisor"]
