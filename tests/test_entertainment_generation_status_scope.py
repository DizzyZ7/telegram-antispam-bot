from __future__ import annotations

import random
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from entertainment import EntertainmentService
from entertainment.generation_v3 import GenerationResult


def obj(**kwargs):
    return SimpleNamespace(**kwargs)


def result() -> GenerationResult:
    return GenerationResult(
        text="новая локальная фраза",
        engine="v3",
        score=5.0,
        candidate_count=8,
        rejection_counts={},
    )


class FakeMessage:
    def __init__(self) -> None:
        self.chat = obj(id=-1001, type="supergroup")
        self.from_user = obj(id=7, is_bot=False)
        self.message_thread_id = 10
        self.message_id = 900
        self.text = "/fun_generation_status"
        self.reply_to_message = None
        self.replies: list[str] = []

    async def reply(self, text: str, **_kwargs: object) -> None:
        self.replies.append(text)


class FakeBot:
    id = 999

    async def get_chat_member(self, *, chat_id: int, user_id: int):
        return obj(status="administrator")


class GenerationStatusScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_live_block_uses_only_current_chat_topic_scope(self):
        store = obj(recent_actions=AsyncMock(return_value=[]))
        service = EntertainmentService(
            obj(bot=FakeBot()),
            store,
            {-1001},
            rng=random.Random(3),
            now_fn=lambda: 5000.0,
        )

        service._generation_metrics.record_for(
            -1001,
            10,
            mode="direct_reply",
            engine="v3",
            result=result(),
        )
        service._generation_metrics.record_for(
            -1001,
            11,
            mode="autonomous",
            engine="v3",
            result=None,
        )
        service._generation_metrics.record_for(
            -1001,
            11,
            mode="autonomous",
            engine="v3",
            result=None,
        )

        message = FakeMessage()
        await service.show_generation_status(message)

        rendered = message.replies[-1]
        self.assertIn(
            "Попытки: <b>1</b> · успешно: <b>1</b> · no-output: <b>0</b>",
            rendered,
        )
        self.assertNotIn("Попытки: <b>3</b>", rendered)


if __name__ == "__main__":
    unittest.main()
