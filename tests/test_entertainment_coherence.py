from __future__ import annotations

import random
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from entertainment.coherence import generated_text_defect
from entertainment.culture import CultureGenerationContext
from entertainment.culture_service import EntertainmentService as CultureService
from entertainment.generation import _candidate_is_sane
from entertainment.generation_v3 import (
    GenerationMode, GenerationResult, _rejection_reason,
)


class GeneratedChatQualityTests(unittest.TestCase):
    def test_equivalent_predicates_do_not_repeat_with_different_word_forms(self):
        for text in ('Ленивый будильник ушел отдыхать и ушел отдыхать.',
                     'Тихая кастрюля ушла отдыхать и ушел отдыхать.',
                     'Кофе просит официальный выходной и просит официальный выходной.'):
            with self.subTest(text=text):
                self.assertIsNotNone(generated_text_defect(text))

    def test_same_verb_with_new_object_and_negative_contrast_remain_valid(self):
        for text in ('Чайник попросил выходной и попросил печенье.',
                     'Кот не ушел отдыхать и ушел отдыхать.',
                     'Кот ушел отдыхать и не ушел отдыхать.'):
            with self.subTest(text=text):
                self.assertIsNone(generated_text_defect(text))

    def test_degree_adverb_is_not_a_second_predicative_adjective(self):
        for text in ('Тихая кастрюля была совершенно готова работать.',
                     'Холодильник был совершенно готов работать.',
                     'Ведро было совершенно готово работать.'):
            with self.subTest(text=text):
                self.assertIsNone(generated_text_defect(text))

    def test_clear_nominative_does_not_govern_an_impersonal_infinitive(self):
        self.assertIsNotNone(generated_text_defect('Старая клавиатура пришлась рано уйти.'))
        for text in ('Старой клавиатуре пришлось рано уйти.',
                     'Ведро пришлось оставить дома.',
                     'Мне ведро пришлось оставить дома.'):
            with self.subTest(text=text):
                self.assertIsNone(generated_text_defect(text))

    def test_dangling_clause_ends_and_clear_subject_agreement_fail(self):
        for text in ('Кот охраняет рукопись и теперь финал пишется на.',
                     'Читатели ждут продолжение а сегодня потерял рассказ но.',
                     'Завтра чайник пришла дописывать главу.',
                     'Сегодня кастрюля ушел на кухню.',
                     'Тихая кастрюля была совершенно готов работать.',
                     'Старая кастрюля стала счастливым.'):
            with self.subTest(text=text):
                self.assertIsNotNone(generated_text_defect(text))

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
        context = CultureGenerationContext(
            source_messages=["Сегодня очень громко в чате", "Завтра ждём экзамен"],
            context_messages=["Сегодня все учатся и готовятся"],
        )
        fake = GenerationResult(
            text="Бот достиг лимита генераций и не может ответить.",
            engine="v3", score=5.0, candidate_count=1, rejection_counts={},
        )
        receiver = SimpleNamespace(rng=random.Random(4))
        with (patch("entertainment.culture_service.generate_text", return_value=fake) as generator,
              patch("entertainment.culture_service.compose_memory_text", return_value=None)):
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
