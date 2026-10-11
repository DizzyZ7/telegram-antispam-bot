"""Fosgen personality: deterministic creativity, explicit consent, isolation."""
from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from entertainment.moderation_safety import is_safe_entertainment_text
from entertainment.personality import (
    PERSONALITY_STYLES,
    PERSONALITY_STYLE_LABELS,
    choose_personality_line,
    detect_literary_topic,
    resolve_personality_style,
)
from entertainment.scoped_service import EntertainmentService
from entertainment.storage.windows import SQLiteEntertainmentStorage


WRITERS_CHAT = -1002619489118
LEXICON_CHAT = -1002659916114


class LowRandom(random.Random):
    def random(self) -> float:
        return 0.0

    def choice(self, seq):
        return seq[0]


class PersonalityCatalogTests(unittest.TestCase):
    def test_styles_require_exact_explicit_choice(self):
        self.assertEqual(resolve_personality_style("ирония"), "ironic")
        self.assertEqual(resolve_personality_style("МЯГКИЙ"), "gentle")
        self.assertEqual(resolve_personality_style("абсурд"), "absurd")
        self.assertIsNone(resolve_personality_style("профиль мне сами определите"))
        self.assertEqual(set(PERSONALITY_STYLE_LABELS), PERSONALITY_STYLES)

    def test_literary_topics_are_detected_without_personal_inferences(self):
        for phrase, expected in (
            ("Пишу главу", "writing"),
            ("Жду продолжения", "reading"),
            ("Мой персонаж спорит с сюжетом", "characters"),
            ("Подбираю цвета обложки", "covers"),
        ):
            with self.subTest(phrase=phrase):
                self.assertEqual(detect_literary_topic(phrase), expected)
        for phrase in ("Привет, ты тут?", "Мне 19 лет", "https://example.org/глава"):
            self.assertIsNone(detect_literary_topic(phrase))

    def test_curated_replies_are_topic_specific_novel_safe_and_bound(self):
        for phrase in ("Жду продолжения", "Дописать главу", "Сюжет снова сломался", "Нужна обложка"):
            for style in PERSONALITY_STYLES:
                recent: list[str] = []
                for _ in range(3):
                    choice = choose_personality_line(
                        style=style, trigger_text=phrase,
                        recent_outputs=recent, rng=LowRandom(),
                    )
                    self.assertIsNotNone(choice)
                    assert choice is not None
                    line, topic = choice
                    self.assertEqual(topic, detect_literary_topic(phrase))
                    self.assertTrue(is_safe_entertainment_text(line))
                    self.assertLessEqual(len(line), 350)
                    self.assertNotIn(phrase.casefold(), line.casefold())
                    self.assertNotIn(line, recent)
                    recent.append(line)
                self.assertIsNone(choose_personality_line(
                    style=style, trigger_text=phrase,
                    recent_outputs=recent, rng=LowRandom(),
                ))

    def test_generic_and_unsolicited_requests_never_generate(self):
        for style in ("", "none", "unselected"):
            self.assertIsNone(choose_personality_line(
                style=style, trigger_text="дописать главу",
                recent_outputs=[], rng=LowRandom(),
            ))
        self.assertIsNone(choose_personality_line(
            style="ironic", trigger_text="погода в городе",
            recent_outputs=[], rng=LowRandom(),
        ))
        self.assertIsNone(choose_personality_line(
            style="ironic", trigger_text="дописать главу",
            recent_outputs=[], rng=random.Random(1),
            engagement_probability=0,
        ))


class PersonalityStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = SQLiteEntertainmentStorage(Path(self.temp_dir.name) / "ent.sqlite")
        await self.storage.initialize()

    async def asyncTearDown(self):
        await self.storage.close()
        self.temp_dir.cleanup()

    async def test_opt_in_survives_restart_and_is_chat_and_user_isolated(self):
        self.assertIsNone(await self.storage.get_personality_style(WRITERS_CHAT, 10))
        await self.storage.set_personality_style(WRITERS_CHAT, 10, "ironic")
        self.assertEqual(await self.storage.get_personality_style(WRITERS_CHAT, 10), "ironic")
        self.assertIsNone(await self.storage.get_personality_style(WRITERS_CHAT, 11))
        self.assertIsNone(await self.storage.get_personality_style(LEXICON_CHAT, 10))
        await self.storage.close()
        await self.storage.initialize()
        self.assertEqual(await self.storage.get_personality_style(WRITERS_CHAT, 10), "ironic")
        await self.storage.set_personality_style(WRITERS_CHAT, 10, None)
        self.assertIsNone(await self.storage.get_personality_style(WRITERS_CHAT, 10))

    async def test_unknown_style_does_not_enter_database(self):
        with self.assertRaises(ValueError):
            await self.storage.set_personality_style(WRITERS_CHAT, 10, "infinite_power")
        self.assertIsNone(await self.storage.get_personality_style(WRITERS_CHAT, 10))


class PersonalityCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.storage = SimpleNamespace(
            get_personality_style=AsyncMock(return_value=None),
            set_personality_style=AsyncMock(),
            get_remember_enabled=AsyncMock(return_value=True),
            set_remember_enabled=AsyncMock(),
            delete_user_memory=AsyncMock(return_value=3),
            delete_legacy_user_messages=AsyncMock(return_value=2),
        )
        self.app = SimpleNamespace(bot=SimpleNamespace(id=99887))
        self.service = EntertainmentService(
            self.app, self.storage, {WRITERS_CHAT},
        )

    def message(self, text, *, chat=WRITERS_CHAT, topic=None):
        return SimpleNamespace(
            text=text,
            chat=SimpleNamespace(id=chat, type="supergroup"),
            from_user=SimpleNamespace(id=31415, is_bot=False),
            message_thread_id=topic,
            reply=AsyncMock(),
        )

    async def test_user_opt_in_and_off(self):
        message = self.message("/fun_style ирония")
        await self.service.configure_personality_style(message)
        self.storage.set_personality_style.assert_awaited_once_with(WRITERS_CHAT, 31415, "ironic")
        self.assertIn("ирония", message.reply.await_args.args[0])
        self.storage.set_personality_style.reset_mock()
        await self.service.configure_personality_style(self.message("/fun_style off"))
        self.storage.set_personality_style.assert_awaited_once_with(WRITERS_CHAT, 31415, None)

    async def test_refuse_opt_in_when_memory_is_disabled(self):
        self.storage.get_remember_enabled.return_value = False
        message = self.message("/fun_style абсурд")
        await self.service.configure_personality_style(message)
        self.storage.set_personality_style.assert_not_awaited()
        self.assertIn("/fun_remember_me", message.reply.await_args.args[0])

    async def test_excluded_writers_topics_and_lexicon_are_off_limits(self):
        for chat, topic in ((WRITERS_CHAT, 292358), (WRITERS_CHAT, 14637),
                            (WRITERS_CHAT, 42817), (LEXICON_CHAT, None)):
            with self.subTest(chat=chat, topic=topic):
                msg = self.message("/fun_style абсурд", chat=chat, topic=topic)
                await self.service.configure_personality_style(msg)
                msg.reply.assert_not_awaited()
        self.storage.set_personality_style.assert_not_awaited()

    async def test_memory_off_and_forget_remove_personality_preference(self):
        await self.service.set_remember_me(self.message("/fun_ignore_me"), False)
        self.storage.set_personality_style.assert_awaited_once_with(WRITERS_CHAT, 31415, None)
        self.storage.set_personality_style.reset_mock()
        removed = await self.service.delete_my_memory(self.message("/fun_delete_me"))
        self.assertEqual(removed, 5)
        self.storage.set_personality_style.assert_awaited_once_with(WRITERS_CHAT, 31415, None)


if __name__ == "__main__":
    unittest.main()
