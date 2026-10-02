from __future__ import annotations

import os
import unittest

from entertainment.models import MemoryEvent, MemoryEventType
from entertainment.storage.retention import PostgresEntertainmentStorage


def event(message_id: int, *, topic_id: int = 44, created_at: int = 1000) -> MemoryEvent:
    return MemoryEvent(
        id=None,
        chat_id=-1888000111,
        topic_id=topic_id,
        message_id=message_id,
        user_id=message_id % 3 + 1,
        event_type=MemoryEventType.TEXT,
        created_at=created_at,
        text=f"message-{message_id}",
    )


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class CultureWindowPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.storage = PostgresEntertainmentStorage(
            os.environ["TEST_DATABASE_URL"], memory_limit=500, prune_buffer=50
        )
        await self.storage.initialize()
        pool = self.storage._require_pool()
        await pool.execute("DELETE FROM ent_memory_events WHERE chat_id = -1888000111")

    async def asyncTearDown(self):
        pool = self.storage._require_pool()
        await pool.execute("DELETE FROM ent_memory_events WHERE chat_id = -1888000111")
        await self.storage.close()

    async def test_postgres_window_sampling_matches_contract(self):
        for message_id in range(1, 31):
            await self.storage.add_event(event(message_id))
        for message_id in range(101, 111):
            await self.storage.add_event(event(message_id, topic_id=99))

        first = await self.storage.sample_event_windows(
            -1888000111, 44, window_count=3, window_size=4, seed=42
        )
        second = await self.storage.sample_event_windows(
            -1888000111, 44, window_count=3, window_size=4, seed=42
        )

        ids = [[item.message_id for item in window] for window in first]
        self.assertEqual(ids, [[item.message_id for item in window] for window in second])
        self.assertEqual(len(ids), 3)
        for window_ids in ids:
            self.assertEqual(window_ids, list(range(window_ids[0], window_ids[0] + len(window_ids))))
            self.assertLessEqual(len(window_ids), 4)
        self.assertTrue(all(item.topic_id == 44 for window in first for item in window))
        self.assertTrue(any(window_ids[0] <= 5 for window_ids in ids))

    async def test_postgres_sparse_history_is_safe(self):
        for message_id in range(1, 4):
            await self.storage.add_event(event(message_id, created_at=1000 + message_id))

        windows = await self.storage.sample_event_windows(
            -1888000111, 44, window_count=8, window_size=10, seed=7
        )

        self.assertEqual([[item.message_id for item in window] for window in windows], [[1, 2, 3]])


if __name__ == "__main__":
    unittest.main()
