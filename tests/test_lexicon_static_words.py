from __future__ import annotations

import re
import unittest

from lexicon_curated_words import CURATED_ANSWER_WORDS, CURATED_ANSWER_WORDS_BASE
from lexicon_curated_words_extra import CURATED_ANSWER_WORDS_EXTRA
from lexicon_source_words import LONG_SOURCE_WORDS


RUSSIAN_WORD_RE = re.compile(r"^[а-яе]+$")


class LexiconStaticWordTests(unittest.TestCase):
    def assert_clean_words(self, words: frozenset[str]) -> None:
        for word in words:
            with self.subTest(word=word):
                self.assertEqual(word, word.lower())
                self.assertGreaterEqual(len(word), 4)
                self.assertLessEqual(len(word), 32)
                self.assertRegex(word, RUSSIAN_WORD_RE)

    def test_base_curated_answers_are_clean(self) -> None:
        self.assertGreater(len(CURATED_ANSWER_WORDS_BASE), 150)
        self.assert_clean_words(CURATED_ANSWER_WORDS_BASE)

    def test_extra_curated_answers_are_clean(self) -> None:
        self.assertGreater(len(CURATED_ANSWER_WORDS_EXTRA), 250)
        self.assert_clean_words(CURATED_ANSWER_WORDS_EXTRA)

    def test_combined_curated_answers_are_clean(self) -> None:
        self.assertGreater(len(CURATED_ANSWER_WORDS), 400)
        self.assertEqual(
            CURATED_ANSWER_WORDS,
            CURATED_ANSWER_WORDS_BASE | CURATED_ANSWER_WORDS_EXTRA,
        )
        self.assert_clean_words(CURATED_ANSWER_WORDS)

    def test_long_sources_are_clean(self) -> None:
        self.assertGreater(len(LONG_SOURCE_WORDS), 80)
        self.assertEqual(len(LONG_SOURCE_WORDS), len(set(LONG_SOURCE_WORDS)))
        for word in LONG_SOURCE_WORDS:
            with self.subTest(word=word):
                self.assertEqual(word, word.lower())
                self.assertGreaterEqual(len(word), 10)
                self.assertLessEqual(len(word), 24)
                self.assertRegex(word, RUSSIAN_WORD_RE)


if __name__ == "__main__":
    unittest.main()
