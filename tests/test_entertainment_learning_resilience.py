"""Entertainment PostgreSQL outages must not block Telegram handlers."""
from __future__ import annotations

import asyncio
import unittest
from contextlib import suppress
from types import SimpleNamespace
from unittest.mock import AsyncMock

from entertainment.router import EntertainmentLearningMiddleware


class LearningIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.release = asyncio.Event()
        self.observed = []

        async def observe(event):
            self.observed.append(event.message_id)
            await self.release.wait()

        self.service = SimpleNamespace(
            chat_ids={-1002619489118},
            observe_message=AsyncMock(side_effect=observe),
        )
        self.middleware = EntertainmentLearningMiddleware(
            self.service, max_pending=2, observe_timeout=4,
        )
        self.handler = AsyncMock(return_value="handler-completed")

    async def asyncTearDown(self):
        self.release.set()
        task = self.middleware._worker
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    def _message(self, message_id, chat_id=-1002619489118):
        return SimpleNamespace(
            message_id=message_id,
            chat=SimpleNamespace(id=chat_id),
        )

    async def test_database_stall_never_delays_telegram_handler(self):
        result = await asyncio.wait_for(
            self.middleware(self.handler, self._message(1), {}),
            timeout=0.3,
        )
        self.assertEqual(result, "handler-completed")
        self.handler.assert_awaited_once()
        self.release.set()
        await asyncio.wait_for(self.middleware._queue.join(), timeout=1)
        self.assertEqual(self.observed, [1])

    async def test_unrelated_chats_are_not_queued_or_observed(self):
        response = await self.middleware(
            self.handler, self._message(12, chat_id=-100999), {},
        )
        self.assertEqual(response, "handler-completed")
        self.assertIsNone(self.middleware._worker)
        self.service.observe_message.assert_not_called()

    async def test_queue_is_bounded_during_an_outage(self):
        for i in range(5):
            assert await self.middleware(self.handler, self._message(i), {}) == "handler-completed"
        self.assertLessEqual(self.middleware._queue.qsize(), 2)
        self.release.set()
        await asyncio.wait_for(self.middleware._queue.join(), timeout=1)
        self.assertLessEqual(self.service.observe_message.await_count, 2)
        self.assertEqual(self.handler.await_count, 5)


if __name__ == "__main__":
    unittest.main()
