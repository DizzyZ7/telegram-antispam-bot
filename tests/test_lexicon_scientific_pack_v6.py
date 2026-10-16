"""Tests for the large sourced Lexicon starter pack.

Only morphological noun lemmas and sources with >= 38 valid answers can enter
live gameplay; pack entries do not override production safety checks.
"""

from __future__ import annotations

import unittest

from lexicon_curated_words import CURATED_ANSWER_WORDS
from lexicon_learning_unpin import LearningLexiconService
from lexicon_source_words import LONG_SOURCE_WORDS
from lexicon_source_words_extra import LONG_SOURCE_WORDS_EXTRA
from lexicon_source_words_extra2 import LONG_SOURCE_WORDS_EXTRA2
from lexicon_source_words_extra3 import LONG_SOURCE_WORDS_EXTRA3
from lexicon_source_words_extra4 import LONG_SOURCE_WORDS_EXTRA4
from lexicon_source_words_extra5 import LONG_SOURCE_WORDS_EXTRA5
from writers_moderation import contains_prohibited_language


class LongSourceScienceV6Tests(unittest.TestCase):
    def test_new_pack_is_large_clean_and_deduplicated(self) -> None:
        self.assertGreaterEqual(len(LONG_SOURCE_WORDS_EXTRA5), 1500)
        self.assertEqual(len(LONG_SOURCE_WORDS_EXTRA5), len(set(LONG_SOURCE_WORDS_EXTRA5)))
        previous = set(LONG_SOURCE_WORDS_EXTRA) | set(LONG_SOURCE_WORDS_EXTRA2)
        previous |= set(LONG_SOURCE_WORDS_EXTRA3) | set(LONG_SOURCE_WORDS_EXTRA4)
        for word in LONG_SOURCE_WORDS_EXTRA5:
            with self.subTest(word=word):
                self.assertTrue(10 <= len(word) <= 24)
                self.assertRegex(word, r"^[а-яе]+$")
                self.assertNotIn(word, previous)
                self.assertFalse(contains_prohibited_language(word))
                self.assertIn(word, LONG_SOURCE_WORDS)

    def test_strict_noun_and_answer_compiler_accepts_many_new_pages(self) -> None:
        # This tests actual production filters, not just vocabulary size.
        # It runs only during CI, not bot startup or a Telegram guess.
        service = object.__new__(LearningLexiconService)
        service.dictionary_words = LearningLexiconService.load_dictionary_words()
        compiled = service.build_round_candidates()
        new_sources = set(LONG_SOURCE_WORDS_EXTRA5)
        playable_new = {
            source for source, answers in compiled
            if source in new_sources and len(answers) >= 38
        }
        self.assertGreaterEqual(
            len(playable_new), 1000,
            f"Expected 1000+ actually playable new starter words; got {len(playable_new)}",
        )

    def test_examples_cover_different_sciences(self) -> None:
        bank = set(LONG_SOURCE_WORDS_EXTRA5)
        # Representative areas, validated by the same live compiler.
        for stem in (
            "электро", "гидро", "бактери", "кристалл",
            "медицин", "астро", "радио", "информац",
        ):
            with self.subTest(stem=stem):
                self.assertTrue(
                    any(word.startswith(stem) for word in bank),
                    f"No long starter with science stem {stem}",
                )


if __name__ == "__main__":
    unittest.main()
