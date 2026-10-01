from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from entertainment.models import BehaviorMode, EntertainmentSettings
from entertainment.service import EntertainmentService
from entertainment.storage.sqlite import SQLiteEntertainmentStorage


class FakeBot:
    def __init__(self, *, status: str = "administrator") -> None:
        self.id = 999
        self.status = status

    async def get_chat_member(self, *, chat_id: int, user_id: int):
        return SimpleNamespace(status=self.status)


class FakeMessage:
    def __init__(self, text: str = "/fun") -> None:
        self.chat = SimpleNamespace(id=-1001, type="supergroup")
        self.from_user = SimpleNamespace(id=7, is_bot=False)
        self.text = text
        self.message_thread_id = 10
        self.message_id = 123
        self.reply_to_message = None
        self.replies: list[tuple[str, object | None]] = []

    async def reply(self, text: str, **kwargs: object) -> None:
        self.replies.append((text, kwargs.get("reply_markup")))


class FakeCallback:
    def __init__(self, message: FakeMessage, data: str) -> None:
        self.message = message
        self.data = data
        self.from_user = SimpleNamespace(id=7, is_bot=False)
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, *, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


class EntertainmentBehaviorUiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = SQLiteEntertainmentStorage(Path(self.temp_dir.name) / "behavior-ui.db")
        await self.storage.initialize()
        self.bot = FakeBot()
        self.service = EntertainmentService(SimpleNamespace(bot=self.bot), self.storage, {-1001})

    async def asyncTearDown(self) -> None:
        await self.storage.close()
        self.temp_dir.cleanup()

    async def test_panel_shows_behavior_modes_without_legacy_numeric_controls(self) -> None:
        await self.storage.save_settings(
            -1001,
            EntertainmentSettings(behavior_mode=BehaviorMode.ACTIVE),
        )
        message = FakeMessage()

        await self.service.show_panel(message)

        text, markup = message.replies[-1]
        self.assertIn("Активный", text)
        self.assertNotIn("Лень", text)
        self.assertNotIn("кулдаун", text.casefold())
        self.assertIsNotNone(markup)
        callbacks = {
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
            if button.callback_data
        }
        self.assertTrue(
            {
                "fun:mode:calm",
                "fun:mode:alive",
                "fun:mode:active",
                "fun:status",
                "fun:disable",
            }.issubset(callbacks)
        )

    async def test_admin_can_persist_behavior_mode(self) -> None:
        message = FakeMessage()

        await self.service.set_behavior_mode(message, BehaviorMode.CALM)

        settings = await self.storage.get_settings(-1001)
        self.assertEqual(settings.behavior_mode, BehaviorMode.CALM)
        self.assertIn("Спокойный", message.replies[-1][0])

    async def test_non_admin_cannot_change_behavior_mode(self) -> None:
        self.bot.status = "member"
        message = FakeMessage()

        await self.service.set_behavior_mode(message, BehaviorMode.ACTIVE)

        settings = await self.storage.get_settings(-1001)
        self.assertEqual(settings.behavior_mode, BehaviorMode.ALIVE)
        self.assertIn("администра", message.replies[-1][0].casefold())

    async def test_mode_callback_uses_callback_actor_and_persists(self) -> None:
        message = FakeMessage()
        callback = FakeCallback(message, "fun:mode:active")

        await self.service.handle_callback(callback)

        settings = await self.storage.get_settings(-1001)
        self.assertEqual(settings.behavior_mode, BehaviorMode.ACTIVE)
        self.assertTrue(callback.answers)

    async def test_legacy_numeric_commands_only_point_to_fun_and_do_not_mutate(self) -> None:
        await self.storage.save_settings(
            -1001,
            EntertainmentSettings(laziness=77, cooldown_seconds=60),
        )
        laziness = FakeMessage("/fun_laziness 0")
        cooldown = FakeMessage("/fun_cooldown 5")

        await self.service.set_laziness(laziness)
        await self.service.set_cooldown(cooldown)

        settings = await self.storage.get_settings(-1001)
        self.assertEqual(settings.laziness, 77)
        self.assertEqual(settings.cooldown_seconds, 60)
        self.assertIn("/fun", laziness.replies[-1][0])
        self.assertIn("/fun", cooldown.replies[-1][0])


if __name__ == "__main__":
    unittest.main()
