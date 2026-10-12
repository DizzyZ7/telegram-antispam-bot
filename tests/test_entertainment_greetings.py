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

    def test_colloquial_greetings_are_detected_and_incidental_phrases_are_not(self) -> None:
        from entertainment.greetings import detect_greeting
        morning = (
            "Доброе утречко!", "доброго утра всем :)", "Утро доброе",
            "с добрым утречком", "Доброе утречко всем ☀️",
        )
        night = (
            "Спокойной ночки!", "доброй ноченьки", "спокушки",
            "споки ноки всем 😴", "всем сладких снов",
        )
        for greeting in morning:
            with self.subTest(greeting=greeting):
                self.assertEqual(detect_greeting(greeting), "morning")
        for greeting in night:
            with self.subTest(greeting=greeting):
                self.assertEqual(detect_greeting(greeting), "night")
        for ordinary in ("доброе утро было вчера", "утро будет добрым, когда закончу",
                         "вчера всем пожелал спокойной ночи"):
            with self.subTest(ordinary=ordinary):
                self.assertIsNone(detect_greeting(ordinary))

    def test_every_curated_greeting_passes_strict_output_safety(self) -> None:
        from entertainment.greetings import iter_greeting_texts
        from entertainment.moderation_safety import is_safe_entertainment_reply
        texts = list(iter_greeting_texts())
        self.assertGreaterEqual(len(texts), 80)
        for text in texts:
            with self.subTest(text=text):
                self.assertTrue(is_safe_entertainment_reply(text))

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

    def test_inflected_context_words_still_select_the_right_style(self) -> None:
        mod = self.module()

        reply = mod.choose_greeting_reply(
            "morning",
            context_messages=("художники рисуют иллюстрации для новых глав",),
            recent_replies=(),
            rng=random.Random(2),
        )

        self.assertEqual(reply.style, "art")

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
