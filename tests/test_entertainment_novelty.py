from __future__ import annotations

import unittest

from entertainment.novelty import is_novel_generated_text


class EntertainmentNoveltyTests(unittest.TestCase):
    def test_exact_source_replay_is_rejected(self) -> None:
        self.assertFalse(
            is_novel_generated_text(
                "Сегодня опять идем гулять",
                ["Сегодня опять идем гулять"],
                [],
            )
        )

    def test_case_punctuation_and_whitespace_normalized_replay_is_rejected(self) -> None:
        self.assertFalse(
            is_novel_generated_text(
                "  СЕГОДНЯ,   опять идем гулять!!! ",
                ["сегодня опять идем гулять"],
                [],
            )
        )

    def test_recent_bot_output_replay_is_rejected(self) -> None:
        self.assertFalse(
            is_novel_generated_text(
                "это уже говорил бот",
                ["совсем другой человеческий текст"],
                ["Это уже говорил бот!"],
            )
        )

    def test_near_copy_with_seventy_percent_four_gram_coverage_is_rejected(self) -> None:
        source = "один два три четыре пять шесть семь восемь девять десять"
        candidate = "один два три четыре пять шесть семь восемь новое слово"
        self.assertFalse(is_novel_generated_text(candidate, [source], []))

    def test_genuinely_recombined_sentence_is_allowed(self) -> None:
        sources = [
            "сегодня после работы идем гулять в парк",
            "вчера обсуждали новый релиз и смешные баги",
            "кофе утром спасает весь наш чат",
        ]
        candidate = "после релиза кофе спасает смешные обсуждения"
        self.assertTrue(is_novel_generated_text(candidate, sources, []))

    def test_short_non_exact_text_without_four_grams_is_allowed(self) -> None:
        self.assertTrue(
            is_novel_generated_text(
                "кофе спасает чат",
                ["кофе спасает рабочий чат сегодня"],
                [],
            )
        )


if __name__ == "__main__":
    unittest.main()
