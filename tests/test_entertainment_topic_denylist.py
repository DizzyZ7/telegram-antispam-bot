from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from entertainment import EntertainmentService
from entertainment.config import ENTERTAINMENT_BLOCKED_TOPIC_SCOPES
from entertainment.storage.retention import SQLiteEntertainmentStorage

WRITERS_CHAT_ID = -1002619489118
BLOCKED_TOPICS = {292358, 14637, 42817}


class FakeMessage:
    def __init__(
        self,
        *,
        chat_id: int = WRITERS_CHAT_ID,
        topic_id: int,
        message_id: int = 1,
        user_id: int = 7,
        text: str = "обычное сообщение для памяти",
    ) -> None:
        self.chat = SimpleNamespace(id=chat_id, type="supergroup")
        self.from_user = SimpleNamespace(id=user_id, is_bot=False)
        self.message_id = message_id
        self.message_thread_id = topic_id
        self.text = text
        self.caption = None
        self.sticker = None
        self.photo = None
        self.animation = None
        self.reply_to_message = None
        self.forward_origin = None
        self.forward_date = None
        self.forward_from = None
        self.forward_sender_name = None
        self.is_automatic_forward = False
        self.replies: list[str] = []

    async def reply(self, text: str, **_: object) -> None:
        self.replies.append(text)


class TopicDenylistTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = SQLiteEntertainmentStorage(Path(self.tmp.name) / "ent.db")
        await self.storage.initialize()
        self.bot = SimpleNamespace(id=999)
        self.service = EntertainmentService(
            SimpleNamespace(bot=self.bot),
            self.storage,
            {WRITERS_CHAT_ID},
            now_fn=lambda: 123456.0,
        )

    async def asyncTearDown(self) -> None:
        await self.storage.close()
        self.tmp.cleanup()

    def test_default_denylist_is_scoped_to_writers_chat(self) -> None:
        self.assertEqual(
            {topic_id for chat_id, topic_id in ENTERTAINMENT_BLOCKED_TOPIC_SCOPES if chat_id == WRITERS_CHAT_ID},
            BLOCKED_TOPICS,
        )
        self.assertNotIn((-1009999999999, 292358), ENTERTAINMENT_BLOCKED_TOPIC_SCOPES)

    async def test_blocked_topics_are_not_learned_or_marked_active(self) -> None:
        for index, topic_id in enumerate(BLOCKED_TOPICS, start=1):
            message = FakeMessage(topic_id=topic_id, message_id=index)
            await self.service.observe_message(message)
            self.assertEqual((await self.storage.memory_counts(WRITERS_CHAT_ID, topic_id)).total, 0)
            self.assertEqual(await self.storage.message_count(WRITERS_CHAT_ID, topic_id), 0)
            self.assertNotIn((WRITERS_CHAT_ID, topic_id), self.service._active_topics)

    async def test_blocked_short_greeting_is_not_learned_or_answered(self) -> None:
        message = FakeMessage(topic_id=292358, text="споки")

        await self.service.observe_message(message)

        self.assertEqual((await self.storage.memory_counts(WRITERS_CHAT_ID, 292358)).total, 0)
        self.assertEqual(message.replies, [])

    async def test_same_topic_id_in_other_chat_is_not_globally_blocked(self) -> None:
        other_chat = -1009999999999
        service = EntertainmentService(
            SimpleNamespace(bot=self.bot),
            self.storage,
            {other_chat},
            now_fn=lambda: 123456.0,
        )
        message = FakeMessage(chat_id=other_chat, topic_id=292358)
        await service.observe_message(message)
        self.assertEqual((await self.storage.memory_counts(other_chat, 292358)).total, 1)

    async def test_blocked_topic_evaluation_returns_before_storage_reads(self) -> None:
        storage = SimpleNamespace(
            get_settings=AsyncMock(side_effect=AssertionError("blocked topic must not evaluate")),
        )
        service = EntertainmentService(
            SimpleNamespace(bot=self.bot),
            storage,
            {WRITERS_CHAT_ID},
            now_fn=lambda: 123456.0,
        )
        result = await service.evaluate_topic(FakeMessage(topic_id=292358))
        self.assertIsNone(result)
        storage.get_settings.assert_not_awaited()

    async def test_blocked_greeting_returns_before_greeting_runtime_storage_reads(self) -> None:
        storage = SimpleNamespace(
            get_settings=AsyncMock(side_effect=AssertionError("blocked greeting must not evaluate")),
        )
        service = EntertainmentService(
            SimpleNamespace(bot=self.bot),
            storage,
            {WRITERS_CHAT_ID},
            now_fn=lambda: 123456.0,
        )

        result = await service.evaluate_topic(
            FakeMessage(topic_id=14637, text="спокойной ночи")
        )

        self.assertIsNone(result)
        storage.get_settings.assert_not_awaited()

    async def test_all_blocked_topics_stop_before_culture_snapshot_and_generation(self) -> None:
        culture_read = AsyncMock(side_effect=AssertionError("blocked topic read Culture Memory"))
        with (
            patch.object(self.service, "_culture_generation_context", culture_read),
            patch(
                "entertainment.culture_service.generate_text",
                side_effect=AssertionError("blocked topic reached generation"),
            ) as generator,
        ):
            for topic_id in sorted(BLOCKED_TOPICS):
                result = await self.service.evaluate_topic(FakeMessage(topic_id=topic_id))
                self.assertIsNone(result)

        culture_read.assert_not_awaited()
        generator.assert_not_called()

    async def test_manual_generation_is_silent_in_blocked_topic(self) -> None:
        message = FakeMessage(topic_id=14637)
        await self.service.generate_now(message)
        self.assertEqual(message.replies, [])


if __name__ == "__main__":
    unittest.main()
