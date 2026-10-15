from __future__ import annotations

import random
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from entertainment.coherence import generated_text_defect
from entertainment.culture_service import EntertainmentService as CultureService
from entertainment.generation import _candidate_is_sane
from entertainment.generation_v3 import (
    GenerationMode, GenerationResult, _rejection_reason,
)


class GeneratedChatQualityTests(unittest.TestCase):
    def test_real_fosgen_gibberish_and_fake_system_limits_are_rejected(self):
        bad = (
            "Учебу делаю а нет утро чат достиг лимита лимит генераций.",
            "Чат достиг лимита генераций и теперь все осталось на потом.",
            "Кажется бот исчерпал лимит ответов и ушел на перерыв.",
            "Сервер достиг лимита токенов и закончил обсуждение.",
            "Бухучет бухучета сегодня решил уйти на перерыв.",
        )
        for phrase in bad:
            with self.subTest(phrase=phrase):
                self.assertIsNotNone(generated_text_defect(phrase))
                self.assertIsNotNone(_rejection_reason(
                    phrase, records=[], recent_outputs=set(),
                ))
                self.assertFalse(_candidate_is_sane(
                    phrase.split(), phrase,
                    min_words=4, originals=set(),
                ))

    def test_regular_chat_memes_and_human_punctuation_stay_allowed(self):
        allowed = (
            "Учеба снова решила устроить нам внеплановый экзамен.",
            "Хаха, бухгалтерия еще тот квест! Удачи на экзамене.",
            "Внутренний кот доволен, даже сервер перестал грустить.",
            "В протоколе ответа нет, зато есть новая интрига.",
            "Автобус скоро приедет к вокзалу и заберет всех домой.",
            "Очень очень смешно, но экзамен уже завтра.",
        )
        for phrase in allowed:
            with self.subTest(phrase=phrase):
                self.assertIsNone(generated_text_defect(phrase))

    def test_final_culture_generation_gate_blocks_even_mocked_unsafe_generator(self):
        # A compatibility/mock engine must not bypass the quality gate.
        context = SimpleNamespace(
            source_messages=["Сегодня очень громко в чате", "Завтра ждём экзамен"],
            context_messages=["Сегодня все учатся и готовятся"],
        )
        fake = GenerationResult(
            text="Бот достиг лимита генераций и не может ответить.",
            engine="v3", score=5.0, candidate_count=1, rejection_counts={},
        )
        receiver = SimpleNamespace(rng=random.Random(4))
        with patch("entertainment.culture_service.generate_text", return_value=fake) as generator:
            result = CultureService._generate_culture_text(
                receiver, context,
                recent_outputs=[], recent_signatures=set(),
                mode=GenerationMode.DIRECT_REPLY,
                trigger_text="как экзамен?",
            )
        self.assertIsNone(result.text)
        self.assertIsNone(result.diagnostics)
        self.assertEqual(generator.call_count, 5)


if __name__ == "__main__":
    unittest.main()
