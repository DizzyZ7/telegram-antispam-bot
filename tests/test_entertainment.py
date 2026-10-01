from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from entertainment import (
    MEMORY_LIMIT,
    EntertainmentService,
    EntertainmentSettings,
    EntertainmentStorage,
    generate_chat_text,
    parse_chat_ids,
)


class EntertainmentPureTests(unittest.TestCase):
    def test_parse_chat_ids_accepts_common_separators(self) -> None:
        self.assertEqual(
            parse_chat_ids("-1001, -1002; -1003\n-1004"),
            frozenset({-1001, -1002, -1003, -1004}),
        )

    def test_settings_exposes_spontaneous_probability(self) -> None:
        self.assertEqual(EntertainmentSettings(laziness=92).spontaneous_chance_percent, 8)
        self.assertEqual(EntertainmentSettings(laziness=100).spontaneous_chance_percent, 0)
        self.assertEqual(EntertainmentSettings(laziness=0).spontaneous_chance_percent, 100)

    def test_learning_filter_is_strictly_scoped(self) -> None:
        service = EntertainmentService(
            app=object(),
            storage=object(),  # type: ignore[arg-type]
            chat_ids={-10042},
            rng=random.Random(1),
        )

        def message(chat_id: int, text: str) -> SimpleNamespace:
            return SimpleNamespace(
                chat=SimpleNamespace(id=chat_id, type="supergroup"),
                from_user=SimpleNamespace(id=7, is_bot=False),
                text=text,
            )

        self.assertTrue(service.is_eligible_learning_message(message(-10042, "обычное сообщение чата")))
        self.assertFalse(service.is_eligible_learning_message(message(-10099, "обычное сообщение чата")))
        self.assertFalse(service.is_eligible_learning_message(message(-10042, "/command argument")))
        self.assertFalse(service.is_eligible_learning_message(message(-10042, "смотри https://example.com")))

    def test_generator_recombines_chat_style_without_exact_replay(self) -> None:
        messages = []
        subjects = ["кот", "енот", "робот", "гусь", "админ"]
        verbs = ["ищет", "любит", "несет", "прячет", "роняет"]
        objects = ["плед", "чайник", "диван", "мем", "тапок"]
        for index in range(35):
            messages.append(
                f"{subjects[index % len(subjects)]} "
                f"{verbs[(index * 2) % len(verbs)]} "
                f"{objects[(index * 3) % len(objects)]} сегодня"
            )

        originals = {item.casefold().rstrip(".") for item in messages}
        generated = None
        for seed in range(20):
            candidate = generate_chat_text(messages, rng=random.Random(seed))
            if candidate is not None:
                generated = candidate
                break

        self.assertIsNotNone(generated)
        assert generated is not None
        self.assertNotIn(generated.casefold().rstrip("."), originals)


class EntertainmentStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = EntertainmentStorage(Path(self.temp_dir.name) / "entertainment.db")
        await self.storage.initialize()

    async def asyncTearDown(self) -> None:
        await self.storage.close()
        self.temp_dir.cleanup()

    async def test_memory_is_isolated_between_chats(self) -> None:
        await self.storage.add_message(-1001, 1, "первый чат помнит кота")
        await self.storage.add_message(-1002, 2, "второй чат помнит енота")

        self.assertEqual(await self.storage.recent_messages(-1001), ["первый чат помнит кота"])
        self.assertEqual(await self.storage.recent_messages(-1002), ["второй чат помнит енота"])

    async def test_clear_chat_does_not_touch_other_chat(self) -> None:
        await self.storage.add_message(-1001, 1, "раз два три")
        await self.storage.add_message(-1002, 2, "четыре пять шесть")

        removed = await self.storage.clear_chat(-1001)

        self.assertEqual(removed, 1)
        self.assertEqual(await self.storage.message_count(-1001), 0)
        self.assertEqual(await self.storage.message_count(-1002), 1)

    async def test_settings_are_per_chat(self) -> None:
        await self.storage.save_settings(
            -1001,
            EntertainmentSettings(enabled=True, laziness=77, cooldown_seconds=60),
        )

        first = await self.storage.get_settings(-1001)
        second = await self.storage.get_settings(-1002)

        self.assertEqual(first.laziness, 77)
        self.assertEqual(first.cooldown_seconds, 60)
        self.assertEqual(second.laziness, 92)
        self.assertGreater(MEMORY_LIMIT, 0)


if __name__ == "__main__":
    unittest.main()
