from __future__ import annotations

import random
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from entertainment import EntertainmentService
from entertainment.generation_v3 import GenerationResult


def obj(**kwargs):
    return SimpleNamespace(**kwargs)


def result(index: int) -> GenerationResult:
    return GenerationResult(
        text=f"SECRET RECENT OUTPUT {index}",
        engine="v3",
        score=5.1,
        candidate_count=11,
        rejection_counts={"exact_source": 2, "single_source": 1},
    )


class FakeBot:
    id = 999

    async def get_chat_member(self, *, chat_id: int, user_id: int):
        return obj(status="administrator")


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


class GenerationRecentHealthStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_shows_last_32_health_and_success_rate_delta_without_text_leak(self):
        store = obj(recent_actions=AsyncMock(return_value=[]))
        service = EntertainmentService(
            obj(bot=FakeBot()),
            store,
            {-1001},
            rng=random.Random(9),
            now_fn=lambda: 5000.0,
        )

        # First 8 successes fall outside the recent 32-attempt window.
        for index in range(8):
            service._generation_metrics.record_for(
                -1001,
                10,
                mode="direct_reply",
                engine="v3",
                result=result(index),
            )
        # Recent window: 8 successes + 24 no-output = 25% success.
        for index in range(8, 16):
            service._generation_metrics.record_for(
                -1001,
                10,
                mode="direct_reply",
                engine="v3",
                result=result(index),
            )
        for _ in range(24):
            service._generation_metrics.record_for(
                -1001,
                10,
                mode="autonomous",
                engine="v3",
                result=None,
            )

        message = FakeMessage()
        await service.show_generation_status(message)

        rendered = message.replies[-1]
        self.assertIn("Последние 32 попытки", rendered)
        self.assertIn("25.0%", rendered)
        self.assertIn("-15.0 п.п.", rendered)
        self.assertIn("Среднее кандидатов: <b>11.0</b>", rendered)
        self.assertIn("exact_source: <b>16</b>", rendered)
        self.assertNotIn("SECRET RECENT OUTPUT", rendered)


if __name__ == "__main__":
    unittest.main()
