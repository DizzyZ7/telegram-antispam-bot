from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from entertainment import EntertainmentService
from entertainment.storage.sqlite import SQLiteEntertainmentStorage


class SQLiteLongHorizonActivityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = SQLiteEntertainmentStorage(Path(self.temp_dir.name) / "presence.db")
        await self.storage.initialize()

    async def asyncTearDown(self) -> None:
        await self.storage.close()
        self.temp_dir.cleanup()

    async def test_activity_snapshot_counts_exact_60_and_120_minute_windows(self) -> None:
        now = 20_000
        samples = (
            (1, now - 20),
            (2, now - 300),
            (3, now - 900),
            (4, now - 3_600),
            (5, now - 3_601),
            (6, now - 7_200),
            (7, now - 7_201),
        )
        for user_id, created_at in samples:
            await self.storage.add_message(-1001, 10, user_id, f"sample {user_id}", created_at=created_at)
        await self.storage.add_message(-1001, 20, 99, "other topic", created_at=now - 10)

        snapshot = await self.storage.activity_snapshot(-1001, 10, now=now)
        self.assertEqual(snapshot.messages_60m, 4)
        self.assertEqual(snapshot.messages_120m, 6)
        self.assertEqual(snapshot.active_users_60m, 4)
        self.assertEqual(snapshot.messages_15m, 3)
        self.assertEqual(snapshot.seconds_since_human, 20.0)


class SupervisorPresenceHorizonTests(unittest.IsolatedAsyncioTestCase):
    NOW = 200_000

    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = SQLiteEntertainmentStorage(Path(self.temp_dir.name) / "supervisor-presence.db")
        await self.storage.initialize()
        self.bot = SimpleNamespace(id=999)
        self.service = EntertainmentService(
            SimpleNamespace(bot=self.bot), self.storage, {-1001}, now_fn=lambda: float(self.NOW)
        )

    async def asyncTearDown(self) -> None:
        await self.storage.close()
        self.temp_dir.cleanup()

    @staticmethod
    def message(topic_id: int = 10):
        return SimpleNamespace(
            chat=SimpleNamespace(id=-1001, type="supergroup"),
            from_user=SimpleNamespace(id=7, is_bot=False),
            text="remembered active topic",
            message_thread_id=topic_id,
            message_id=100,
            reply_to_message=None,
        )

    async def test_supervisor_keeps_scope_with_human_activity_inside_two_hours(self) -> None:
        message = self.message()
        await self.storage.add_message(-1001, 10, 7, "seventy minutes ago", created_at=self.NOW - 70 * 60)
        self.service.remember_active_topic(message)
        with patch.object(self.service, "evaluate_topic", new=AsyncMock(return_value=None)) as evaluate:
            await self.service.run_supervisor_tick()
        evaluate.assert_awaited_once_with(message, supervisor=True)
        self.assertIn((-1001, 10), self.service._active_topics)

    async def test_supervisor_drops_scope_after_two_hours_without_humans(self) -> None:
        message = self.message()
        await self.storage.add_message(-1001, 10, 7, "older than two hours", created_at=self.NOW - 121 * 60)
        self.service.remember_active_topic(message)
        with patch.object(self.service, "evaluate_topic", new=AsyncMock(return_value=None)) as evaluate:
            await self.service.run_supervisor_tick()
        evaluate.assert_not_awaited()
        self.assertNotIn((-1001, 10), self.service._active_topics)


if __name__ == "__main__":
    unittest.main()
