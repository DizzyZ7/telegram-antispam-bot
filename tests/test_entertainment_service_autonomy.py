from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from entertainment.models import (
    BehaviorMode,
    EntertainmentActionRecord,
    EntertainmentActionType,
    EntertainmentSettings,
)
from entertainment.service import EntertainmentService
from entertainment.storage.sqlite import SQLiteEntertainmentStorage


class FakeMessage:
    def __init__(
        self,
        *,
        chat_id: int = -1001,
        topic_id: int = 10,
        user_id: int = 7,
        text: str = "новое человеческое сообщение",
        message_id: int = 900,
        reply_to_bot: bool = False,
    ) -> None:
        self.chat = SimpleNamespace(id=chat_id, type="supergroup")
        self.from_user = SimpleNamespace(id=user_id, is_bot=False)
        self.text = text
        self.message_thread_id = topic_id
        self.message_id = message_id
        self.reply_to_message = None
        if reply_to_bot:
            self.reply_to_message = SimpleNamespace(
                from_user=SimpleNamespace(id=999, is_bot=True)
            )
        self.replies: list[str] = []

    async def reply(self, text: str, **_: object) -> None:
        self.replies.append(text)


class EntertainmentServiceAutonomyTests(unittest.IsolatedAsyncioTestCase):
    NOW = 100_000

    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = SQLiteEntertainmentStorage(Path(self.temp_dir.name) / "service.db")
        await self.storage.initialize()
        self.app = SimpleNamespace(bot=SimpleNamespace(id=999))

    async def asyncTearDown(self) -> None:
        await self.storage.close()
        self.temp_dir.cleanup()

    def service(self, seed: int = 1) -> EntertainmentService:
        return EntertainmentService(
            self.app,
            self.storage,
            {-1001},
            rng=random.Random(seed),
            now_fn=lambda: float(self.NOW),
        )

    async def seed_memory(self, *, chat_id: int = -1001, topic_id: int = 10) -> None:
        for index in range(25):
            await self.storage.add_message(
                chat_id,
                topic_id,
                index % 5 + 1,
                f"старое сообщение номер {index} про чат и мемы",
                message_id=100 + index,
                created_at=self.NOW - 2_000 - index,
            )

    async def test_observe_uses_v2_engine_and_records_action(self) -> None:
        await self.seed_memory()
        await self.storage.save_settings(
            -1001,
            EntertainmentSettings(
                behavior_mode=BehaviorMode.ALIVE,
                laziness=100,
                cooldown_seconds=3600,
            ),
        )
        message = FakeMessage()
        with patch(
            "entertainment.service.generate_chat_text",
            return_value="совсем новая автономная реплика чата",
        ):
            await self.service().observe_message(message)

        self.assertEqual(message.replies, ["совсем новая автономная реплика чата"])
        actions = await self.storage.recent_actions(-1001, 10, since=self.NOW - 1_800)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].action_type, EntertainmentActionType.REMIXED_PHRASE)
        self.assertEqual(actions[0].trigger_message_id, 900)
        self.assertEqual(actions[0].metadata["output"], "совсем новая автономная реплика чата")
        self.assertEqual(actions[0].metadata["mode"], "alive")

    async def test_persisted_previous_action_blocks_fresh_service_after_restart(self) -> None:
        await self.seed_memory()
        await self.storage.record_action(
            EntertainmentActionRecord(
                id=None,
                chat_id=-1001,
                topic_id=10,
                action_type=EntertainmentActionType.CONTEXTUAL_REPLY,
                trigger_message_id=800,
                created_at=self.NOW - 400,
                metadata={"output": "предыдущая реплика"},
            )
        )

        fresh_service = self.service(seed=2)
        message = FakeMessage(message_id=901)
        with patch(
            "entertainment.service.generate_chat_text",
            return_value="эта реплика не должна отправиться",
        ) as generator:
            await fresh_service.observe_message(message)

        self.assertEqual(message.replies, [])
        generator.assert_not_called()
        actions = await self.storage.recent_actions(-1001, 10, since=self.NOW - 1_800)
        self.assertEqual(len(actions), 1)

    async def test_peak_suppresses_ordinary_action(self) -> None:
        await self.seed_memory()
        for index in range(12):
            await self.storage.add_message(
                -1001,
                10,
                index % 3 + 1,
                f"быстрое сообщение пика {index}",
                message_id=400 + index,
                created_at=self.NOW - 120 + index,
            )
        message = FakeMessage(message_id=902)
        with patch(
            "entertainment.service.generate_chat_text",
            return_value="на пике бот должен молчать",
        ) as generator:
            await self.service(seed=3).observe_message(message)

        self.assertEqual(message.replies, [])
        generator.assert_not_called()

    async def test_direct_reply_can_be_selected_during_peak(self) -> None:
        await self.seed_memory()
        for index in range(12):
            await self.storage.add_message(
                -1001,
                10,
                index % 3 + 1,
                f"сообщение активного пика {index}",
                message_id=500 + index,
                created_at=self.NOW - 120 + index,
            )
        message = FakeMessage(message_id=903, reply_to_bot=True)
        with patch(
            "entertainment.service.generate_chat_text",
            return_value="ответ на прямое обращение во время пика",
        ):
            await self.service(seed=4).observe_message(message)

        self.assertEqual(message.replies, ["ответ на прямое обращение во время пика"])
        actions = await self.storage.recent_actions(-1001, 10, since=self.NOW - 1_800)
        self.assertEqual(actions[0].action_type, EntertainmentActionType.CONTEXTUAL_REPLY)

    async def test_generation_retries_when_first_result_fails_novelty(self) -> None:
        await self.seed_memory()
        copied = "старое сообщение номер 0 про чат и мемы"
        novel = "новая комбинация слов совсем иначе звучит"
        message = FakeMessage(message_id=904)
        with patch(
            "entertainment.service.generate_chat_text",
            side_effect=[copied, novel],
        ) as generator:
            await self.service(seed=5).observe_message(message)

        self.assertEqual(generator.call_count, 2)
        self.assertEqual(message.replies, [novel])
        actions = await self.storage.recent_actions(-1001, 10, since=self.NOW - 1_800)
        self.assertEqual(actions[0].metadata["output"], novel)

    async def test_no_action_is_recorded_when_all_generation_attempts_fail(self) -> None:
        await self.seed_memory()
        copied = "старое сообщение номер 0 про чат и мемы"
        message = FakeMessage(message_id=905)
        with patch(
            "entertainment.service.generate_chat_text",
            return_value=copied,
        ) as generator:
            await self.service(seed=6).observe_message(message)

        self.assertEqual(generator.call_count, 5)
        self.assertEqual(message.replies, [])
        self.assertEqual(
            await self.storage.recent_actions(-1001, 10, since=self.NOW - 1_800),
            [],
        )


if __name__ == "__main__":
    unittest.main()
