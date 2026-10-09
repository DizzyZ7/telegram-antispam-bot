"""Censored profanity regression corpus for the Writers chat."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram import Dispatcher

import writers_moderation as moderation

from writers_moderation import (
    ProhibitedLanguageFilter,
    WRITERS_PROFANITY_EXEMPT_TOPIC_IDS,
    contains_prohibited_language,
    detect_prohibited_language,
    register_writers_chat_handlers,
)

WRITERS_CHAT_ID = -1002619489118


def make_message(text: str, *, topic_id: int | None = None, caption: bool = False):
    return SimpleNamespace(
        chat=SimpleNamespace(id=WRITERS_CHAT_ID, username="chat_ikf"),
        from_user=SimpleNamespace(id=1234, is_bot=False),
        text=None if caption else text,
        caption=text if caption else None,
        message_id=4567,
        message_thread_id=topic_id,
        delete=AsyncMock(),
    )


class MaskedProfanityTests(unittest.TestCase):
    def test_masked_russian_words_and_unicode_stars(self):
        cases = (
            "бл*ть", "бл**дь", "х*й", "п***ц", "п*здец",
            "н*хуя", "НИ*УЯ СЕБЕ", "н*х*я", "с*ка", "м*дак",
            "долбо*б", "ё**ть", "за**ал", "х*йня",
            "х＊й", "бл✱ть", "п∗здец", "с﹡ка",
            "х*й и б***ь", "Да н*хуя себе",
        )
        for case in cases:
            with self.subTest(text=case):
                self.assertTrue(contains_prohibited_language(case))
                self.assertIsNotNone(detect_prohibited_language(case))

    def test_masked_english_and_mixed_scripts(self):
        for case in ("f*ck", "f***ing", "b*tch", "s*it", "p*zdets"):
            with self.subTest(text=case):
                self.assertTrue(contains_prohibited_language(case))

    def test_do_not_treat_arbitrary_asterisks_as_profanity(self):
        cases = (
            "2 * 3 = 6",
            "3*5=15",
            "**Это жирный текст**",
            "*Курсив* и **выделение**",
            "Глава *** уже опубликована",
            "П***р — сокращение имени персонажа",
            "Ссылка на *новую* историю",
            "Пишу **поэму**",
            "к***а",
            "Договорились, спасибо!",
            "Звездочки **** в тексте",
            "Это обычная с*ема рассказа",
        )
        for case in cases:
            with self.subTest(text=case):
                self.assertFalse(
                    contains_prohibited_language(case),
                    msg=(
                        f"category={detect_prohibited_language(case)!r} "
                        f"mask={moderation._detect_masked_profanity(case)!r} "
                        f"spaced={moderation.SPACED_TOKEN_PATTERN.findall(case)!r}"
                    ),
                )

    def test_existing_plain_profanity_still_detected(self):
        for case in ("НИХУЯ СЕБЕ", "блять", "пиздец", "fuuuck"):
            with self.subTest(text=case):
                self.assertTrue(contains_prohibited_language(case))


class MaskedProfanityMessageHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_masked_message_is_deleted_in_general_and_other_topics(self):
        app = SimpleNamespace(
            dp=Dispatcher(),
            ALLOWED_CHATS=[WRITERS_CHAT_ID],
            bot=SimpleNamespace(send_message=AsyncMock()),
        )
        register_writers_chat_handlers(app)
        handler = app.dp.message.handlers[0].callback
        language_filter = ProhibitedLanguageFilter()
        for topic_id in (None, 1, 12345):
            with self.subTest(topic_id=topic_id):
                message = make_message("н*хуя себе!", topic_id=topic_id)
                self.assertTrue(await language_filter(message))
                await handler(message)
                message.delete.assert_awaited_once()

    async def test_same_masked_word_is_exempt_only_in_original_three_topics(self):
        language_filter = ProhibitedLanguageFilter()
        self.assertEqual(
            WRITERS_PROFANITY_EXEMPT_TOPIC_IDS,
            frozenset({14637, 42817, 292358}),
        )
        for topic_id in sorted(WRITERS_PROFANITY_EXEMPT_TOPIC_IDS):
            for caption in (False, True):
                with self.subTest(topic_id=topic_id, caption=caption):
                    self.assertFalse(await language_filter(
                        make_message("бл*ть", topic_id=topic_id, caption=caption)
                    ))

    async def test_censored_caption_is_checked_and_neutral_caption_is_kept(self):
        language_filter = ProhibitedLanguageFilter()
        self.assertTrue(await language_filter(make_message(
            "Подпись: п***ц", topic_id=1, caption=True
        )))
        self.assertFalse(await language_filter(make_message(
            "Название: Луна и звезды ***", topic_id=1, caption=True
        )))


if __name__ == "__main__":
    unittest.main()
