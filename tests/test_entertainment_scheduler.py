from __future__ import annotations

import asyncio
import random
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from entertainment.models import EntertainmentActionRecord, EntertainmentActionType
from entertainment.scheduler import EntertainmentSupervisor
from entertainment.service import EntertainmentService
from entertainment.storage.sqlite import SQLiteEntertainmentStorage


class CountingService:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.calls = 0
        self.fail_first = fail_first
        self.second_call = asyncio.Event()

    async def run_supervisor_tick(self) -> None:
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise RuntimeError("synthetic tick failure")
        if self.calls >= 2:
            self.second_call.set()


class SupervisorLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_is_idempotent_and_stop_is_clean_and_idempotent(self) -> None:
        service = CountingService()
        supervisor = EntertainmentSupervisor(service, interval_seconds=3600)

        supervisor.start()
        first_task = supervisor.task
        self.assertIsNotNone(first_task)
        supervisor.start()
        self.assertIs(supervisor.task, first_task)

        await supervisor.stop()
        self.assertIsNone(supervisor.task)
        self.assertTrue(first_task.done())
        await supervisor.stop()
        self.assertIsNone(supervisor.task)

    async def test_tick_failure_does_not_kill_supervisor_loop(self) -> None:
        service = CountingService(fail_first=True)
        supervisor = EntertainmentSupervisor(service, interval_seconds=0.01)
        supervisor.start()
        try:
            await asyncio.wait_for(service.second_call.wait(), timeout=1.0)
            self.assertGreaterEqual(service.calls, 2)
            self.assertIsNotNone(supervisor.task)
            self.assertFalse(supervisor.task.done())
        finally:
            await supervisor.stop()


class FakeBot:
    def __init__(self) -> None:
        self.id = 999
        self.sent: list[tuple[int, str, int | None]] = []

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        message_thread_id: int | None = None,
        **_: object,
    ) -> None:
        self.sent.append((chat_id, text, message_thread_id))


class FakeMessage:
    def __init__(self, *, message_id: int, text: str, topic_id: int = 10) -> None:
        self.chat = SimpleNamespace(id=-1001, type="supergroup")
        self.from_user = SimpleNamespace(id=7, is_bot=False)
        self.text = text
        self.message_thread_id = topic_id
        self.message_id = message_id
        self.reply_to_message = None
        self.replies: list[str] = []

    async def reply(self, text: str, **_: object) -> None:
        self.replies.append(text)


class SupervisorServiceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    NOW = 200_000

    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = SQLiteEntertainmentStorage(Path(self.temp_dir.name) / "scheduler.db")
        await self.storage.initialize()
        self.bot = FakeBot()
        self.service = EntertainmentService(
            SimpleNamespace(bot=self.bot),
            self.storage,
            {-1001},
            rng=random.Random(1),
            now_fn=lambda: float(self.NOW),
        )

    async def asyncTearDown(self) -> None:
        await self.storage.close()
        self.temp_dir.cleanup()

    async def seed_memory(self) -> None:
        for index in range(25):
            await self.storage.add_message(
                -1001,
                10,
                index % 5 + 1,
                f"историческая фраза номер {index} для памяти темы",
                message_id=1_000 + index,
                created_at=self.NOW - 2_000 - index,
            )

    async def test_supervisor_does_not_send_without_human_message_after_last_action(self) -> None:
        await self.seed_memory()
        await self.storage.record_action(
            EntertainmentActionRecord(
                id=None,
                chat_id=-1001,
                topic_id=10,
                action_type=EntertainmentActionType.CONTEXTUAL_REPLY,
                trigger_message_id=700,
                created_at=self.NOW - 500,
                metadata={"output": "предыдущее действие"},
            )
        )
        # This is the message that originally triggered the previous action. It is
        # not new human activity after that action.
        old_message = FakeMessage(message_id=700, text="старый триггер")
        await self.storage.add_message(
            -1001,
            10,
            7,
            old_message.text,
            message_id=700,
            created_at=self.NOW - 500,
        )
        self.service.remember_active_topic(old_message)

        with patch(
            "entertainment.service.generate_chat_text",
            return_value="не должна уйти без нового человека",
        ) as generator:
            await self.service.run_supervisor_tick()

        self.assertEqual(self.bot.sent, [])
        generator.assert_not_called()

    async def test_supervisor_uses_topic_send_and_persisted_budget_after_new_human_activity(self) -> None:
        await self.seed_memory()
        await self.storage.record_action(
            EntertainmentActionRecord(
                id=None,
                chat_id=-1001,
                topic_id=10,
                action_type=EntertainmentActionType.CONTEXTUAL_REPLY,
                trigger_message_id=701,
                created_at=self.NOW - 1_000,
                metadata={"output": "старое действие"},
            )
        )
        # Alive mode needs four human messages after the last action.
        latest: FakeMessage | None = None
        for offset in (900, 800, 700, 650):
            latest = FakeMessage(message_id=800 + offset, text=f"новая активность {offset}")
            await self.storage.add_message(
                -1001,
                10,
                7 + offset,
                latest.text,
                message_id=latest.message_id,
                created_at=self.NOW - offset,
            )
        assert latest is not None
        self.service.remember_active_topic(latest)

        with patch(
            "entertainment.service.generate_chat_text",
            return_value="тихая автономная реплика после активности",
        ):
            await self.service.run_supervisor_tick()

        self.assertEqual(
            self.bot.sent,
            [(-1001, "тихая автономная реплика после активности", 10)],
        )
        actions = await self.storage.recent_actions(-1001, 10, since=self.NOW - 1_800)
        self.assertEqual(len(actions), 2)
        self.assertEqual(actions[0].action_type, EntertainmentActionType.REMIXED_PHRASE)

        # No new human messages happened after the just-recorded supervisor action.
        await self.service.run_supervisor_tick()
        self.assertEqual(len(self.bot.sent), 1)

    async def test_one_broken_topic_does_not_block_other_topic(self) -> None:
        await self.seed_memory()
        for index in range(25):
            await self.storage.add_message(
                -1001,
                20,
                index % 5 + 20,
                f"память второй темы номер {index}",
                message_id=2_000 + index,
                created_at=self.NOW - 2_100 - index,
            )
        first = FakeMessage(message_id=901, text="активная тема с ошибкой", topic_id=10)
        second = FakeMessage(message_id=902, text="другая рабочая тема", topic_id=20)
        await self.storage.add_message(-1001, 10, 1, first.text, message_id=901, created_at=self.NOW - 400)
        await self.storage.add_message(-1001, 20, 2, second.text, message_id=902, created_at=self.NOW - 400)
        self.service.remember_active_topic(first)
        self.service.remember_active_topic(second)

        original_evaluate = self.service.evaluate_topic

        async def flaky(message: FakeMessage, *, supervisor: bool = False):
            if message.message_thread_id == 10:
                raise RuntimeError("topic-specific failure")
            return await original_evaluate(message, supervisor=supervisor)

        with patch.object(self.service, "evaluate_topic", side_effect=flaky), patch(
            "entertainment.service.generate_chat_text",
            return_value="вторая тема продолжает работать",
        ):
            await self.service.run_supervisor_tick()

        self.assertEqual(self.bot.sent, [(-1001, "вторая тема продолжает работать", 20)])


if __name__ == "__main__":
    unittest.main()
