from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from entertainment.models import MemoryEvent, MemoryEventType
from entertainment.storage.retention import (
    PostgresEntertainmentStorage,
    SQLiteEntertainmentStorage,
)


def event(*, chat_id: int, message_id: int, text: str) -> MemoryEvent:
    return MemoryEvent(
        id=None,
        chat_id=chat_id,
        topic_id=10,
        message_id=message_id,
        user_id=7,
        event_type=MemoryEventType.TEXT,
        created_at=100 + message_id,
        text=text,
    )


class SQLiteMessagePurgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = SQLiteEntertainmentStorage(Path(self.tmp.name) / "entertainment.db")
        await self.storage.initialize()

    async def asyncTearDown(self):
        await self.storage.close()
        self.tmp.cleanup()

    async def test_delete_message_memory_removes_both_projections_only_for_target_chat(self):
        chat_id = -1001
        other_chat = -2002
        await self.storage.add_event(event(chat_id=chat_id, message_id=301, text="удалить меня"))
        await self.storage.add_message(chat_id, 10, 7, "удалить меня", message_id=301, created_at=401)
        await self.storage.add_event(event(chat_id=chat_id, message_id=302, text="оставить меня"))
        await self.storage.add_message(chat_id, 10, 7, "оставить меня", message_id=302, created_at=402)
        await self.storage.add_event(event(chat_id=other_chat, message_id=301, text="чужой чат"))
        await self.storage.add_message(other_chat, 10, 7, "чужой чат", message_id=301, created_at=403)

        deleted = await self.storage.delete_message_memory(chat_id, 301)

        self.assertEqual(deleted, 2)
        self.assertEqual(
            [item.message_id for item in await self.storage.recent_events(chat_id, 10, 10)],
            [302],
        )
        self.assertEqual(await self.storage.recent_messages(chat_id, 10, 10), ["оставить меня"])
        self.assertEqual(
            [item.message_id for item in await self.storage.recent_events(other_chat, 10, 10)],
            [301],
        )
        self.assertEqual(await self.storage.recent_messages(other_chat, 10, 10), ["чужой чат"])


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class PostgresMessagePurgeTests(unittest.IsolatedAsyncioTestCase):
    CHAT_ID = -1888000333
    OTHER_CHAT_ID = -1888000444

    async def asyncSetUp(self):
        self.storage = PostgresEntertainmentStorage(os.environ["TEST_DATABASE_URL"])
        await self.storage.initialize()
        pool = self.storage._require_pool()
        await pool.execute(
            "DELETE FROM ent_memory_events WHERE chat_id IN ($1, $2)",
            self.CHAT_ID,
            self.OTHER_CHAT_ID,
        )
        await pool.execute(
            "DELETE FROM entertainment_messages WHERE chat_id IN ($1, $2)",
            self.CHAT_ID,
            self.OTHER_CHAT_ID,
        )

    async def asyncTearDown(self):
        pool = self.storage._require_pool()
        await pool.execute(
            "DELETE FROM ent_memory_events WHERE chat_id IN ($1, $2)",
            self.CHAT_ID,
            self.OTHER_CHAT_ID,
        )
        await pool.execute(
            "DELETE FROM entertainment_messages WHERE chat_id IN ($1, $2)",
            self.CHAT_ID,
            self.OTHER_CHAT_ID,
        )
        await self.storage.close()

    async def test_delete_message_memory_removes_both_projections_only_for_target_chat(self):
        await self.storage.add_event(event(chat_id=self.CHAT_ID, message_id=301, text="удалить меня"))
        await self.storage.add_message(self.CHAT_ID, 10, 7, "удалить меня", message_id=301, created_at=401)
        await self.storage.add_event(event(chat_id=self.CHAT_ID, message_id=302, text="оставить меня"))
        await self.storage.add_message(self.CHAT_ID, 10, 7, "оставить меня", message_id=302, created_at=402)
        await self.storage.add_event(event(chat_id=self.OTHER_CHAT_ID, message_id=301, text="чужой чат"))
        await self.storage.add_message(self.OTHER_CHAT_ID, 10, 7, "чужой чат", message_id=301, created_at=403)

        deleted = await self.storage.delete_message_memory(self.CHAT_ID, 301)

        self.assertEqual(deleted, 2)
        self.assertEqual(
            [item.message_id for item in await self.storage.recent_events(self.CHAT_ID, 10, 10)],
            [302],
        )
        self.assertEqual(await self.storage.recent_messages(self.CHAT_ID, 10, 10), ["оставить меня"])
        self.assertEqual(
            [item.message_id for item in await self.storage.recent_events(self.OTHER_CHAT_ID, 10, 10)],
            [301],
        )
        self.assertEqual(await self.storage.recent_messages(self.OTHER_CHAT_ID, 10, 10), ["чужой чат"])


if __name__ == "__main__":
    unittest.main()
