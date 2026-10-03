from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from entertainment import EntertainmentService
from entertainment.storage.retention import SQLiteEntertainmentStorage


class FakeMessage:
    def __init__(
        self,
        *,
        message_id: int,
        user_id: int,
        is_bot: bool = False,
        text: str | None = None,
        sticker: object | None = None,
        topic_id: int = 10,
    ) -> None:
        self.chat = SimpleNamespace(id=-1001, type="supergroup")
        self.from_user = SimpleNamespace(id=user_id, is_bot=is_bot)
        self.message_id = message_id
        self.message_thread_id = topic_id
        self.text = text
        self.caption = None
        self.sticker = sticker
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


def sticker(file_id: str = "sticker-file") -> object:
    return SimpleNamespace(
        file_id=file_id,
        file_unique_id=f"uniq-{file_id}",
        emoji="😂",
        set_name="memes",
        width=512,
        height=512,
        is_animated=False,
        is_video=False,
    )


class BootstrapLearningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = SQLiteEntertainmentStorage(Path(self.tmp.name) / "ent.db")
        await self.storage.initialize()
        self.bot = SimpleNamespace(id=999)
        self.service = EntertainmentService(
            SimpleNamespace(bot=self.bot),
            self.storage,
            {-1001},
            now_fn=lambda: 123456.0,
        )

    async def asyncTearDown(self) -> None:
        await self.storage.close()
        self.tmp.cleanup()

    async def test_human_command_is_remembered_but_not_human_activity(self) -> None:
        await self.service.observe_message(
            FakeMessage(message_id=1, user_id=7, text="/spawn legendary")
        )
        events = await self.storage.recent_events(-1001, 10, 10)
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0].is_command)
        self.assertFalse(events[0].sender_is_bot)
        self.assertEqual(await self.storage.message_count(-1001, 10), 0)
        self.assertEqual(
            await self.storage.human_messages_since(-1001, 10, since=0),
            0,
        )

    async def test_other_bot_command_and_sticker_are_remembered_without_activity(self) -> None:
        await self.service.observe_message(
            FakeMessage(message_id=2, user_id=55, is_bot=True, text="/spawn mythic")
        )
        await self.service.observe_message(
            FakeMessage(message_id=3, user_id=55, is_bot=True, sticker=sticker())
        )
        events = await self.storage.recent_events(-1001, 10, 10)
        self.assertEqual([event.message_id for event in events], [2, 3])
        self.assertTrue(all(event.sender_is_bot for event in events))
        self.assertTrue(events[0].is_command)
        self.assertEqual(await self.storage.message_count(-1001, 10), 0)
        self.assertEqual(
            await self.storage.human_messages_since(-1001, 10, since=0),
            0,
        )

    async def test_entertainment_bot_never_learns_from_itself(self) -> None:
        await self.service.observe_message(
            FakeMessage(message_id=4, user_id=999, is_bot=True, text="/spawn self-loop")
        )
        self.assertEqual((await self.storage.memory_counts(-1001, 10)).total, 0)

    async def test_human_opt_out_blocks_commands_and_media(self) -> None:
        await self.storage.set_remember_enabled(-1001, 7, False)
        await self.service.observe_message(
            FakeMessage(message_id=5, user_id=7, text="/spawn private")
        )
        await self.service.observe_message(
            FakeMessage(message_id=6, user_id=7, sticker=sticker("private-sticker"))
        )
        self.assertEqual((await self.storage.memory_counts(-1001, 10)).total, 0)

    def test_bootstrap_threshold_default_is_ten_thousand(self) -> None:
        from entertainment import config

        self.assertEqual(config.BOOTSTRAP_TEXT_EVENT_THRESHOLD, 10_000)


if __name__ == "__main__":
    unittest.main()
