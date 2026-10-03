from __future__ import annotations

import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from entertainment import EntertainmentService
from entertainment.router import register_entertainment_handlers

WRITERS_CHAT_ID = -1002619489118
BLOCKED_TOPICS = (292358, 14637, 42817)


class FakeMessage:
    def __init__(self, topic_id: int) -> None:
        self.chat = SimpleNamespace(id=WRITERS_CHAT_ID, type="supergroup")
        self.from_user = SimpleNamespace(id=7, is_bot=False)
        self.message_thread_id = topic_id
        self.message_id = 100
        self.text = "/fun_generation_status"
        self.reply_to_message = None
        self.replies: list[str] = []

    async def reply(self, text: str, **_kwargs: object) -> None:
        self.replies.append(text)


class GenerationStatusRouterTests(unittest.IsolatedAsyncioTestCase):
    def test_router_registers_generation_status_aliases(self):
        source = inspect.getsource(register_entertainment_handlers)
        self.assertIn(
            'Command(commands=["fun_generation_status", "fun_gen_status"])',
            source,
        )
        self.assertIn("await service.show_generation_status(message)", source)

    async def test_blocked_topics_short_circuit_status_before_admin_or_storage_reads(self):
        bot = SimpleNamespace(
            id=999,
            get_chat_member=AsyncMock(
                side_effect=AssertionError("blocked status must not check admin")
            ),
        )
        storage = SimpleNamespace(
            recent_actions=AsyncMock(
                side_effect=AssertionError("blocked status must not read actions")
            ),
        )
        service = EntertainmentService(
            SimpleNamespace(bot=bot),
            storage,
            {WRITERS_CHAT_ID},
            now_fn=lambda: 5000.0,
        )

        for topic_id in BLOCKED_TOPICS:
            message = FakeMessage(topic_id)
            await service.show_generation_status(message)
            self.assertEqual(message.replies, [])

        bot.get_chat_member.assert_not_awaited()
        storage.recent_actions.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
