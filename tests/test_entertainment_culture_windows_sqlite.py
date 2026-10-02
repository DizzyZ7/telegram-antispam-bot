import tempfile
import unittest
from pathlib import Path

from entertainment.models import MemoryEvent, MemoryEventType
from entertainment.storage.retention import SQLiteEntertainmentStorage


def event(message_id: int, *, topic_id: int = 10, created_at: int = 1000) -> MemoryEvent:
    return MemoryEvent(
        id=None,
        chat_id=-1001,
        topic_id=topic_id,
        message_id=message_id,
        user_id=message_id % 3 + 1,
        event_type=MemoryEventType.TEXT,
        created_at=created_at,
        text=f"message-{message_id}",
    )


class CultureWindowSQLiteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = SQLiteEntertainmentStorage(
            Path(self.tmp.name) / "ent.db",
            memory_limit=500,
            prune_buffer=50,
        )
        await self.storage.initialize()

    async def asyncTearDown(self):
        await self.storage.close()
        self.tmp.cleanup()

    async def test_sampling_is_deterministic_contiguous_and_topic_isolated(self):
        for message_id in range(1, 31):
            await self.storage.add_event(event(message_id))
        for message_id in range(101, 111):
            await self.storage.add_event(event(message_id, topic_id=99))

        first = await self.storage.sample_event_windows(
            -1001, 10, window_count=3, window_size=4, seed=42
        )
        second = await self.storage.sample_event_windows(
            -1001, 10, window_count=3, window_size=4, seed=42
        )

        first_ids = [[item.message_id for item in window] for window in first]
        second_ids = [[item.message_id for item in window] for window in second]
        self.assertEqual(first_ids, second_ids)
        self.assertEqual(len(first_ids), 3)
        for ids in first_ids:
            self.assertEqual(ids, list(range(ids[0], ids[0] + len(ids))))
            self.assertLessEqual(len(ids), 4)
        self.assertTrue(all(item.topic_id == 10 for window in first for item in window))
        self.assertTrue(any(ids[0] <= 5 for ids in first_ids))

    async def test_sparse_history_returns_one_bounded_window_without_failure(self):
        for message_id in range(1, 4):
            await self.storage.add_event(event(message_id, created_at=1000 + message_id))

        windows = await self.storage.sample_event_windows(
            -1001, 10, window_count=8, window_size=10, seed=7
        )

        self.assertEqual([[item.message_id for item in window] for window in windows], [[1, 2, 3]])

    async def test_same_timestamp_uses_message_id_as_stable_order(self):
        for message_id in (7, 3, 9, 5, 1):
            await self.storage.add_event(event(message_id, created_at=2000))

        windows = await self.storage.sample_event_windows(
            -1001, 10, window_count=1, window_size=10, seed=1
        )

        self.assertEqual([item.message_id for item in windows[0]], [1, 3, 5, 7, 9])


if __name__ == "__main__":
    unittest.main()
