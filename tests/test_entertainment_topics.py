from __future__ import annotations

import random
import unittest
from types import SimpleNamespace

from entertainment import EntertainmentService
from entertainment.models import EntertainmentSettings, normalize_topic_id


class RecordingStorage:
    def __init__(self) -> None:
        self.added: list[tuple[int, int, int, str, int | None]] = []
        self.messages: dict[tuple[int, int], list[str]] = {}

    async def get_settings(self, chat_id: int) -> EntertainmentSettings:
        return EntertainmentSettings(enabled=True, laziness=100, cooldown_seconds=5)

    async def save_settings(self, chat_id: int, settings: EntertainmentSettings) -> None:
        return None

    async def add_message(
        self,
        chat_id: int,
        topic_id: int,
        user_id: int,
        text: str,
        *,
        message_id: int | None = None,
    ) -> None:
        self.added.append((chat_id, topic_id, user_id, text, message_id))
        self.messages.setdefault((chat_id, topic_id), []).append(text)

    async def recent_messages(self, chat_id: int, topic_id: int, limit: int = 900) -> list[str]:
        return list(self.messages.get((chat_id, topic_id), []))[-limit:]

    async def message_count(self, chat_id: int, topic_id: int | None = None) -> int:
        if topic_id is None:
            return sum(len(values) for (stored_chat_id, _), values in self.messages.items() if stored_chat_id == chat_id)
        return len(self.messages.get((chat_id, topic_id), []))

    async def clear_scope(self, chat_id: int, topic_id: int | None = None) -> int:
        return 0


class EntertainmentTopicTests(unittest.IsolatedAsyncioTestCase):
    def test_normalize_topic_id_maps_missing_to_zero(self) -> None:
        self.assertEqual(normalize_topic_id(None), 0)
        self.assertEqual(normalize_topic_id(0), 0)
        self.assertEqual(normalize_topic_id(123), 123)

    async def test_observe_message_records_forum_topic_and_message_id(self) -> None:
        storage = RecordingStorage()
        service = EntertainmentService(
            app=object(),
            storage=storage,
            chat_ids={-10042},
            rng=random.Random(1),
        )
        message = SimpleNamespace(
            chat=SimpleNamespace(id=-10042, type="supergroup"),
            from_user=SimpleNamespace(id=7, is_bot=False),
            text="это сообщение отдельной темы",
            message_thread_id=321,
            message_id=9001,
        )

        await service.observe_message(message)

        self.assertEqual(
            storage.added,
            [(-10042, 321, 7, "это сообщение отдельной темы", 9001)],
        )

    async def test_two_topics_in_same_chat_are_recorded_separately(self) -> None:
        storage = RecordingStorage()
        service = EntertainmentService(
            app=object(),
            storage=storage,
            chat_ids={-10042},
            rng=random.Random(2),
        )

        def message(topic_id: int, message_id: int, text: str) -> SimpleNamespace:
            return SimpleNamespace(
                chat=SimpleNamespace(id=-10042, type="supergroup"),
                from_user=SimpleNamespace(id=7, is_bot=False),
                text=text,
                message_thread_id=topic_id,
                message_id=message_id,
            )

        await service.observe_message(message(111, 1, "первая тема говорит про кота"))
        await service.observe_message(message(222, 2, "вторая тема говорит про енота"))

        self.assertEqual(storage.messages[(-10042, 111)], ["первая тема говорит про кота"])
        self.assertEqual(storage.messages[(-10042, 222)], ["вторая тема говорит про енота"])


if __name__ == "__main__":
    unittest.main()
