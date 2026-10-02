from __future__ import annotations

import os
import unittest

from entertainment.models import MemoryEvent, MemoryEventType
from entertainment.storage.retention import PostgresEntertainmentStorage


def event(
    *,
    chat_id=-1888000111,
    topic_id=44,
    message_id=1,
    user_id=7,
    event_type=MemoryEventType.TEXT,
    text="текст",
    caption=None,
    file_id=None,
    created_at=100,
):
    return MemoryEvent(
        id=None,
        chat_id=chat_id,
        topic_id=topic_id,
        message_id=message_id,
        user_id=user_id,
        event_type=event_type,
        created_at=created_at,
        text=text,
        caption=caption,
        file_id=file_id,
        file_unique_id=(f"u-{file_id}" if file_id else None),
    )


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class CultureMemoryPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.storage = PostgresEntertainmentStorage(
            os.environ["TEST_DATABASE_URL"],
            memory_limit=3,
            prune_buffer=2,
        )
        await self.storage.initialize()
        pool = self.storage._require_pool()
        await pool.execute("DELETE FROM ent_memory_preferences")
        await pool.execute("DELETE FROM ent_memory_events")
        await pool.execute("DELETE FROM entertainment_messages WHERE chat_id IN (-1888000111, -1888000222)")
        await pool.execute("DELETE FROM ent_schema_migrations WHERE migration_key LIKE 'culture_memory_test_%'")

    async def asyncTearDown(self):
        pool = self.storage._require_pool()
        await pool.execute("DELETE FROM ent_memory_preferences")
        await pool.execute("DELETE FROM ent_memory_events")
        await pool.execute("DELETE FROM entertainment_messages WHERE chat_id IN (-1888000111, -1888000222)")
        await pool.execute("DELETE FROM ent_schema_migrations WHERE migration_key LIKE 'culture_memory_test_%'")
        await self.storage.close()

    async def test_crud_projection_duplicate_and_counts(self):
        first = await self.storage.add_event(event(message_id=1, text="первое", created_at=101))
        duplicate = await self.storage.add_event(event(topic_id=99, message_id=1, text="повтор", created_at=102))
        self.assertEqual(first, duplicate)
        await self.storage.add_event(
            event(message_id=2, event_type=MemoryEventType.EMOJI, text="😂", created_at=103)
        )
        await self.storage.add_event(
            event(message_id=3, event_type=MemoryEventType.PHOTO, text=None, caption="мем", file_id="photo", created_at=104)
        )
        rows = await self.storage.recent_events(-1888000111, 44, 10)
        self.assertEqual([row.message_id for row in rows], [1, 2, 3])
        self.assertEqual(await self.storage.recent_texts(-1888000111, 44, 10), ["первое", "мем"])
        counts = await self.storage.memory_counts(-1888000111, 44)
        self.assertEqual((counts.total, counts.text, counts.emoji, counts.photo), (3, 1, 1, 1))
        self.assertEqual((await self.storage.memory_counts(-1888000111, 99)).total, 0)

    async def test_preferences_deletion_and_scope_clear(self):
        self.assertTrue(await self.storage.get_remember_enabled(-1888000111, 7))
        await self.storage.set_remember_enabled(-1888000111, 7, False)
        self.assertFalse(await self.storage.get_remember_enabled(-1888000111, 7))
        self.assertTrue(await self.storage.get_remember_enabled(-1888000222, 7))

        await self.storage.add_event(event(message_id=11, user_id=7, topic_id=44))
        await self.storage.add_event(event(message_id=12, user_id=8, topic_id=44))
        await self.storage.add_event(event(message_id=13, user_id=7, topic_id=55))
        await self.storage.add_event(event(chat_id=-1888000222, message_id=11, user_id=7, topic_id=44))
        await self.storage.add_message(-1888000111, 44, 7, "legacy delete", message_id=201, created_at=100)
        await self.storage.add_message(-1888000111, 44, 8, "legacy keep", message_id=202, created_at=101)

        self.assertEqual(await self.storage.delete_user_memory(-1888000111, 7), 2)
        self.assertEqual(await self.storage.delete_legacy_user_messages(-1888000111, 7), 1)
        self.assertEqual([row.user_id for row in await self.storage.recent_events(-1888000111, 44, 10)], [8])
        self.assertEqual((await self.storage.memory_counts(-1888000222, 44)).total, 1)
        self.assertEqual(await self.storage.recent_messages(-1888000111, 44, 10), ["legacy keep"])
        self.assertEqual(await self.storage.clear_memory_scope(-1888000111, 44), 1)

    async def test_buffered_retention(self):
        for index in range(1, 6):
            await self.storage.add_event(event(message_id=index, created_at=100 + index, text=f"m{index}"))
        self.assertEqual((await self.storage.memory_counts(-1888000111, 44)).total, 5)
        await self.storage.add_event(event(message_id=6, created_at=106, text="m6"))
        self.assertEqual(
            [row.message_id for row in await self.storage.recent_events(-1888000111, 44, 10)],
            [4, 5, 6],
        )

    async def test_backfill_is_idempotent_and_preserves_fields(self):
        pool = self.storage._require_pool()
        legacy_id = await pool.fetchval(
            """
            INSERT INTO entertainment_messages(chat_id, topic_id, message_id, user_id, text, created_at)
            VALUES($1, $2, $3, $4, $5, $6) RETURNING id
            """,
            -1888000111, 77, 7001, 42, "старое сообщение", 555,
        )
        key = "culture_memory_test_backfill"
        self.assertEqual(await self.storage.backfill_legacy_memory(key), 1)
        rows = await self.storage.recent_events(-1888000111, 77, 10)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(
            (row.message_id, row.user_id, row.text, row.created_at, row.legacy_source_id),
            (7001, 42, "старое сообщение", 555, int(legacy_id)),
        )
        self.assertEqual(await self.storage.backfill_legacy_memory(key), 0)
        self.assertEqual(await self.storage.message_count(-1888000111, 77), 1)


if __name__ == "__main__":
    unittest.main()
