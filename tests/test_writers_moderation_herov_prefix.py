"""Regression coverage for the херов* moderation family."""

from __future__ import annotations

import unittest
from dataclasses import replace

import writers_moderation as moderation


class HerovModerationRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_lexicon = moderation.MODERATION_LEXICON
        prefixes = tuple(
            sorted(
                (*self.original_lexicon.prefix_mixed, ("херов", "obscene")),
                key=lambda item: len(item[0]),
                reverse=True,
            )
        )
        moderation.MODERATION_LEXICON = replace(
            self.original_lexicon,
            prefix_mixed=prefixes,
        )

    def tearDown(self) -> None:
        moderation.MODERATION_LEXICON = self.original_lexicon

    def test_reported_chat_phrase_is_blocked(self) -> None:
        text = "За время этой игры я понял - что у меня херовая реакция"
        self.assertEqual(moderation.detect_prohibited_language(text), "obscene")

    def test_common_inflections_are_blocked(self) -> None:
        for text in (
            "херовый день",
            "херовое настроение",
            "сделано херово",
            "херовенький результат",
            "xеровая реакция",
        ):
            with self.subTest(text=text):
                self.assertEqual(moderation.detect_prohibited_language(text), "obscene")

    def test_safe_words_are_not_caught_by_broad_her_prefix(self) -> None:
        for text in (
            "Херсон",
            "херсонский театр",
            "поезд идет в Херсон",
        ):
            with self.subTest(text=text):
                self.assertIsNone(moderation.detect_prohibited_language(text))


if __name__ == "__main__":
    unittest.main()
