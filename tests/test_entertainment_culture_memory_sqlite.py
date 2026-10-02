import tempfile
import unittest
from pathlib import Path

from entertainment.models import MemoryEvent, MemoryEventType
from entertainment.storage.retention import SQLiteEntertainmentStorage


def event(
    *,
    chat_id=-1001,
    topic_id=10,
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


class CultureMemorySQLiteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = SQLiteEntertainmentStorage(
            Path(self.tmp.name) / "ent.db",
            memory_limit=3,
            prune_buffer=2,
        )
        await self.storage.initialize()

    async def asyncTearDown(self):
        await self.storage.close()
        self.tmp.cleanup()

    async def test_crud_projection_counts_and_topic_isolation(self):
        await self.storage.add_event(event(message_id=1, text="первое", created_at=101))
        await self.storage.add_event(
            event(
                message_id=2,
                event_type=MemoryEventType.EMOJI,
                text="😂🔥",
                created_at=102,
            )
        )
        await self.storage.add_event(
            event(
                message_id=3,
                event_type=MemoryEventType.PHOTO,
                text=None,
                caption="подпись мема",
                file_id="photo-1",
                created_at=103,
            )
        )
        await self.storage.add_event(event(topic_id=99, message_id=4, text="другая тема", created_at=104))

        rows = await self.storage.recent_events(-1001, 10, limit=10)
        self.assertEqual([row.message_id for row in rows], [1, 2, 3])
        self.assertEqual(await self.storage.recent_texts(-1001, 10, limit=10), ["первое", "подпись мема"])
        counts = await self.storage.memory_counts(-1001, 10)
        self.assertEqual((counts.total, counts.text, counts.emoji, counts.photo), (3, 1, 1, 1))

    async def test_duplicate_chat_message_id_is_idempotent(self):
        first = await self.storage.add_event(event(message_id=50, text="один"))
        second = await self.storage.add_event(event(topic_id=20, message_id=50, text="повтор"))
        self.assertEqual(first, second)
        self.assertEqual((await self.storage.memory_counts(-1001, 10)).total, 1)
        self.assertEqual((await self.storage.memory_counts(-1001, 20)).total, 0)

    async def test_preferences_default_true_and_are_per_chat(self):
        self.assertTrue(await self.storage.get_remember_enabled(-1001, 7))
        await self.storage.set_remember_enabled(-1001, 7, False)
        self.assertFalse(await self.storage.get_remember_enabled(-1001, 7))
        self.assertTrue(await self.storage.get_remember_enabled(-1002, 7))
        await self.storage.set_remember_enabled(-1001, 7, True)
        self.assertTrue(await self.storage.get_remember_enabled(-1001, 7))

    async def test_user_deletion_preserves_other_users_and_chats(self):
        await self.storage.add_event(event(message_id=1, user_id=7, text="delete me"))
        await self.storage.add_event(event(message_id=2, user_id=8, text="keep me"))
        await self.storage.add_event(event(chat_id=-1002, message_id=1, user_id=7, text="other chat"))
        await self.storage.add_message(-1001, 10, 7, "legacy delete", message_id=101, created_at=100)
        await self.storage.add_message(-1001, 10, 8, "legacy keep", message_id=102, created_at=101)

        self.assertEqual(await self.storage.delete_user_memory(-1001, 7), 1)
        self.assertEqual(await self.storage.delete_legacy_user_messages(-1001, 7), 1)
        self.assertEqual([e.user_id for e in await self.storage.recent_events(-1001, 10, 10)], [8])
        self.assertEqual((await self.storage.memory_counts(-1002, 10)).total, 1)
        self.assertEqual(await self.storage.recent_messages(-1001, 10, 10), ["legacy keep"])

    async def test_clear_memory_scope_only_clears_requested_topic(self):
        await self.storage.add_event(event(topic_id=10, message_id=1))
        await self.storage.add_event(event(topic_id=20, message_id=2))
        self.assertEqual(await self.storage.clear_memory_scope(-1001, 10), 1)
        self.assertEqual((await self.storage.memory_counts(-1001, 10)).total, 0)
        self.assertEqual((await self.storage.memory_counts(-1001, 20)).total, 1)

    async def test_buffered_retention_prunes_oldest_batch(self):
        for index in range(1, 6):
            await self.storage.add_event(event(message_id=index, created_at=100 + index, text=f"m{index}"))
        self.assertEqual((await self.storage.memory_counts(-1001, 10)).total, 5)

        await self.storage.add_event(event(message_id=6, created_at=106, text="m6"))
        rows = await self.storage.recent_events(-1001, 10, 10)
        self.assertEqual([row.message_id for row in rows], [4, 5, 6])


if __name__ == "__main__":
    unittest.main()
