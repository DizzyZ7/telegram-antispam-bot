from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from lexicon_learning_unpin import LearningLexiconService


class FakeBot:
    def __init__(self) -> None:
        self.unpin_calls: list[dict[str, int]] = []

    async def unpin_chat_message(self, **kwargs: int) -> None:
        self.unpin_calls.append(dict(kwargs))


class LexiconAutoUnpinTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def make_service(bot: FakeBot) -> LearningLexiconService:
        service = object.__new__(LearningLexiconService)
        service.app = SimpleNamespace(bot=bot)
        service._lexicon_pin_message_ids = {}
        service._lexicon_pin_ready = {}
        return service

    async def test_unpins_exact_round_message(self) -> None:
        bot = FakeBot()
        service = self.make_service(bot)
        game_key = (-1002659916114, None)
        ready = asyncio.Event()
        ready.set()
        service._lexicon_pin_message_ids[game_key] = 4815
        service._lexicon_pin_ready[game_key] = ready

        removed = await service._unpin_round_message(game_key, game_key[0])

        self.assertTrue(removed)
        self.assertEqual(
            bot.unpin_calls,
            [{"chat_id": -1002659916114, "message_id": 4815}],
        )
        self.assertNotIn(game_key, service._lexicon_pin_message_ids)
        self.assertNotIn(game_key, service._lexicon_pin_ready)

    async def test_waits_until_pin_operation_is_finished(self) -> None:
        bot = FakeBot()
        service = self.make_service(bot)
        game_key = (-1002619489118, 14637)
        ready = asyncio.Event()
        service._lexicon_pin_message_ids[game_key] = 9001
        service._lexicon_pin_ready[game_key] = ready

        task = asyncio.create_task(service._unpin_round_message(game_key, game_key[0]))
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.assertEqual(bot.unpin_calls, [])

        ready.set()
        self.assertTrue(await task)
        self.assertEqual(
            bot.unpin_calls,
            [{"chat_id": -1002619489118, "message_id": 9001}],
        )

    async def test_preserves_other_chat_and_topic_pins(self) -> None:
        bot = FakeBot()
        service = self.make_service(bot)
        first_key = (-1002619489118, 14637)
        other_key = (-1002619489118, 42817)
        first_ready = asyncio.Event()
        other_ready = asyncio.Event()
        first_ready.set()
        other_ready.set()
        service._lexicon_pin_message_ids.update({first_key: 100, other_key: 200})
        service._lexicon_pin_ready.update({first_key: first_ready, other_key: other_ready})

        await service._unpin_round_message(first_key, first_key[0])

        self.assertEqual(
            bot.unpin_calls,
            [{"chat_id": -1002619489118, "message_id": 100}],
        )
        self.assertEqual(service._lexicon_pin_message_ids[other_key], 200)
        self.assertIs(service._lexicon_pin_ready[other_key], other_ready)


if __name__ == "__main__":
    unittest.main()
