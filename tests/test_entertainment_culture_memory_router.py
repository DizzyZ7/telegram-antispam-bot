from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from entertainment.models import MemoryEvent, MemoryEventType
from entertainment.router import register_entertainment_handlers
from entertainment.service import EntertainmentService
from entertainment.storage.retention import SQLiteEntertainmentStorage


class FakeBot:
    id = 999

    async def get_chat_member(self, *, chat_id: int, user_id: int):
        return SimpleNamespace(status="administrator")


class FakeMessage:
    def __init__(self, *, topic_id: int = 10, user_id: int = 7) -> None:
        self.chat = SimpleNamespace(id=-1001, type="supergroup")
        self.from_user = SimpleNamespace(id=user_id, is_bot=False)
        self.message_thread_id = topic_id
        self.message_id = 999
        self.text = "/fun"
        self.reply_to_message = None
        self.replies: list[tuple[str, object | None]] = []

    async def reply(self, text: str, **kwargs: object) -> None:
        self.replies.append((text, kwargs.get("reply_markup")))


def memory_event(
    message_id: int,
    event_type: MemoryEventType,
    *,
    topic_id: int = 10,
    user_id: int = 7,
    text: str | None = None,
    caption: str | None = None,
    file_id: str | None = None,
) -> MemoryEvent:
    return MemoryEvent(
        id=None,
        chat_id=-1001,
        topic_id=topic_id,
        message_id=message_id,
        user_id=user_id,
        event_type=event_type,
        created_at=100 + message_id,
        text=text,
        caption=caption,
        file_id=file_id,
        file_unique_id=f"u-{message_id}" if file_id else None,
    )


class CultureMemoryPrivacyUiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = SQLiteEntertainmentStorage(Path(self.tmp.name) / "culture.db")
        await self.storage.initialize()
        self.service = EntertainmentService(SimpleNamespace(bot=FakeBot()), self.storage, {-1001})

    async def asyncTearDown(self) -> None:
        await self.storage.close()
        self.tmp.cleanup()

    def test_router_declares_all_three_scoped_privacy_commands(self):
        source = inspect.getsource(register_entertainment_handlers)
        self.assertIn('Command(commands=["fun_ignore_me"])', source)
        self.assertIn('Command(commands=["fun_remember_me"])', source)
        self.assertIn('Command(commands=["fun_delete_me"])', source)
        self.assertIn("await service.set_remember_me(message, False)", source)
        self.assertIn("await service.set_remember_me(message, True)", source)
        self.assertIn("await service.delete_my_memory(message)", source)

    async def test_privacy_methods_reply_and_persist(self):
        message = FakeMessage(user_id=77)
        await self.service.set_remember_me(message, False)
        self.assertFalse(await self.storage.get_remember_enabled(-1001, 77))
        self.assertIn("не запомина", message.replies[-1][0].casefold())

        await self.service.set_remember_me(message, True)
        self.assertTrue(await self.storage.get_remember_enabled(-1001, 77))
        self.assertIn("снова", message.replies[-1][0].casefold())

        await self.storage.add_event(memory_event(1, MemoryEventType.TEXT, user_id=77, text="удали меня"))
        await self.storage.add_message(-1001, 10, 77, "legacy delete", message_id=1001, created_at=100)
        deleted = await self.service.delete_my_memory(message)
        self.assertEqual(deleted, 2)
        self.assertIn("2", message.replies[-1][0])
        self.assertIn("telegram", message.replies[-1][0].casefold())

    async def test_fun_panel_shows_culture_counts_compactly(self):
        await self.storage.add_event(memory_event(1, MemoryEventType.TEXT, text="текст"))
        await self.storage.add_event(memory_event(2, MemoryEventType.EMOJI, text="😂"))
        await self.storage.add_event(memory_event(3, MemoryEventType.STICKER, file_id="s"))
        await self.storage.add_event(memory_event(4, MemoryEventType.PHOTO, file_id="p"))
        await self.storage.add_event(memory_event(5, MemoryEventType.ANIMATION, file_id="a"))

        message = FakeMessage()
        await self.service.show_panel(message)
        text = message.replies[-1][0]
        self.assertIn("5", text)
        self.assertIn("Текст: <b>1</b>", text)
        self.assertIn("Emoji: <b>1</b>", text)
        self.assertIn("Стикеры: <b>1</b>", text)
        self.assertIn("Фото/анимации: <b>2</b>", text)

    async def test_fun_forget_clears_both_memories_only_in_current_topic(self):
        await self.storage.add_event(memory_event(1, MemoryEventType.TEXT, topic_id=10, text="culture 10"))
        await self.storage.add_event(memory_event(2, MemoryEventType.TEXT, topic_id=20, text="culture 20"))
        await self.storage.add_message(-1001, 10, 7, "legacy 10", message_id=1010, created_at=100)
        await self.storage.add_message(-1001, 20, 7, "legacy 20", message_id=1020, created_at=100)

        message = FakeMessage(topic_id=10)
        await self.service.forget_chat(message)

        self.assertEqual((await self.storage.memory_counts(-1001, 10)).total, 0)
        self.assertEqual(await self.storage.message_count(-1001, 10), 0)
        self.assertEqual((await self.storage.memory_counts(-1001, 20)).total, 1)
        self.assertEqual(await self.storage.message_count(-1001, 20), 1)
        self.assertIn("Другие темы", message.replies[-1][0])


if __name__ == "__main__":
    unittest.main()
