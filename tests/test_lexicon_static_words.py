from __future__ import annotations

import re
import unittest

from lexicon_curated_words import CURATED_ANSWER_WORDS, CURATED_ANSWER_WORDS_BASE
from lexicon_curated_words_extra import CURATED_ANSWER_WORDS_EXTRA
from lexicon_curated_words_extra2 import CURATED_ANSWER_WORDS_EXTRA2
from lexicon_source_words import LONG_SOURCE_WORDS, LONG_SOURCE_WORDS_BASE
from lexicon_source_words_extra import LONG_SOURCE_WORDS_EXTRA


RUSSIAN_WORD_RE = re.compile(r"^[а-яе]+$")


class LexiconStaticWordTests(unittest.TestCase):
    def assert_clean_words(self, words: frozenset[str]) -> None:
        for word in words:
            with self.subTest(word=word):
                self.assertEqual(word, word.lower())
                self.assertGreaterEqual(len(word), 4)
                self.assertLessEqual(len(word), 32)
                self.assertRegex(word, RUSSIAN_WORD_RE)

    def assert_clean_sources(self, words: tuple[str, ...]) -> None:
        self.assertEqual(len(words), len(set(words)))
        for word in words:
            with self.subTest(word=word):
                self.assertEqual(word, word.lower())
                self.assertGreaterEqual(len(word), 10)
                self.assertLessEqual(len(word), 24)
                self.assertRegex(word, RUSSIAN_WORD_RE)

    def test_base_curated_answers_are_clean(self) -> None:
        self.assertGreater(len(CURATED_ANSWER_WORDS_BASE), 150)
        self.assert_clean_words(CURATED_ANSWER_WORDS_BASE)

    def test_first_extra_curated_answers_are_clean(self) -> None:
        self.assertGreater(len(CURATED_ANSWER_WORDS_EXTRA), 250)
        self.assert_clean_words(CURATED_ANSWER_WORDS_EXTRA)

    def test_second_extra_curated_answers_are_clean(self) -> None:
        self.assertGreater(len(CURATED_ANSWER_WORDS_EXTRA2), 250)
        self.assert_clean_words(CURATED_ANSWER_WORDS_EXTRA2)

    def test_combined_curated_answers_are_clean(self) -> None:
        self.assertGreater(len(CURATED_ANSWER_WORDS), 500)
        self.assertEqual(
            CURATED_ANSWER_WORDS,
            CURATED_ANSWER_WORDS_BASE
            | CURATED_ANSWER_WORDS_EXTRA
            | CURATED_ANSWER_WORDS_EXTRA2,
        )
        self.assert_clean_words(CURATED_ANSWER_WORDS)

    def test_base_long_sources_are_clean(self) -> None:
        self.assertGreater(len(LONG_SOURCE_WORDS_BASE), 100)
        self.assert_clean_sources(LONG_SOURCE_WORDS_BASE)

    def test_extra_long_sources_are_clean(self) -> None:
        self.assertGreater(len(LONG_SOURCE_WORDS_EXTRA), 100)
        self.assert_clean_sources(LONG_SOURCE_WORDS_EXTRA)

    def test_combined_long_sources_are_clean(self) -> None:
        self.assertGreater(len(LONG_SOURCE_WORDS), 200)
        self.assert_clean_sources(LONG_SOURCE_WORDS)
        self.assertTrue(set(LONG_SOURCE_WORDS_BASE).issubset(LONG_SOURCE_WORDS))
        self.assertTrue(set(LONG_SOURCE_WORDS_EXTRA).issubset(LONG_SOURCE_WORDS))


if __name__ == "__main__":
    unittest.main()
