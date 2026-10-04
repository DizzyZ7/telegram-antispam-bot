from __future__ import annotations

import importlib
import random
import unittest


class GreetingBehaviorTests(unittest.TestCase):
    def module(self):
        try:
            return importlib.import_module("entertainment.greetings")
        except ModuleNotFoundError as exc:
            self.fail(f"greeting behavior module is missing: {exc}")

    def test_detects_morning_and_night_greetings_without_matching_normal_sentences(self) -> None:
        mod = self.module()
        detect = mod.detect_greeting

        self.assertEqual(detect("доброе утро"), "morning")
        self.assertEqual(detect("Доброе всем утро!"), "morning")
        self.assertEqual(detect("спокойной ночи всем"), "night")
        self.assertEqual(detect("споки"), "night")
        self.assertEqual(detect("гн"), "night")
        self.assertIsNone(detect("утром я поеду в мастерскую"))
        self.assertIsNone(detect("ночью дописал главу"))

    def test_context_changes_style_without_profiling_a_user(self) -> None:
        mod = self.module()

        literary = mod.choose_greeting_reply(
            "night",
            context_messages=("редактор дочитал главу", "рукопись почти готова"),
            recent_replies=(),
            rng=random.Random(1),
        )
        technical = mod.choose_greeting_reply(
            "morning",
            context_messages=("сервер снова подняли", "код прошел ci и деплой"),
            recent_replies=(),
            rng=random.Random(1),
        )

        self.assertEqual(literary.style, "literary")
        self.assertEqual(technical.style, "technical")
        self.assertTrue(literary.text)
        self.assertTrue(technical.text)
        self.assertNotEqual(literary.text, technical.text)

    def test_recent_reply_is_not_repeated_when_an_alternative_exists(self) -> None:
        mod = self.module()
        first = mod.choose_greeting_reply(
            "night",
            context_messages=("рисунок палитра иллюстрация",),
            recent_replies=(),
            rng=random.Random(4),
        )
        second = mod.choose_greeting_reply(
            "night",
            context_messages=("рисунок палитра иллюстрация",),
            recent_replies=(first.text,),
            rng=random.Random(4),
        )

        self.assertEqual(first.style, "art")
        self.assertEqual(second.style, "art")
        self.assertNotEqual(first.text, second.text)

    def test_all_curated_replies_avoid_personal_attacks(self) -> None:
        mod = self.module()
        banned = {
            "тупой",
            "тупые",
            "идиот",
            "дебил",
            "урод",
            "жирный",
            "нищий",
            "бездарь",
        }
        corpus = " ".join(mod.iter_greeting_texts()).casefold()
        for word in banned:
            self.assertNotIn(word, corpus)


if __name__ == "__main__":
    unittest.main()
