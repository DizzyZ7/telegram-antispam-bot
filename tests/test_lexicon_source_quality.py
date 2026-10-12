"""Regression tests for the expanded, balanced Lexicon source pool."""
from __future__ import annotations

import unittest
from collections import Counter

from lexicon_source_quality import (
    select_quality_sources, source_quality, source_tier,
)
from lexicon_source_words import LONG_SOURCE_WORDS
from lexicon_source_words_extra2 import LONG_SOURCE_WORDS_EXTRA2
from lexicon_live_patch import _letters_mask, _letters_signature, _signature_fits
from writers_moderation import contains_prohibited_language


def make_answers(count: int, *, length: int = 5) -> set[str]:
    # Pure score fixture; test data are synthetic, not dictionary suggestions.
    return {
        ("я" * length) + str(number)
        for number in range(count)
    }


class SourceBankQualityTests(unittest.TestCase):
    def test_third_pack_grows_bank_by_at_least_150_unique_words(self):
        self.assertGreaterEqual(len(LONG_SOURCE_WORDS_EXTRA2), 150)
        self.assertGreaterEqual(len(set(LONG_SOURCE_WORDS_EXTRA2)), 150)
        self.assertEqual(len(LONG_SOURCE_WORDS), len(set(LONG_SOURCE_WORDS)))
        self.assertGreaterEqual(len(LONG_SOURCE_WORDS), 380)

    def test_curated_new_sources_are_clean_and_playable_shape(self):
        for source in LONG_SOURCE_WORDS_EXTRA2:
            with self.subTest(source=source):
                self.assertTrue(source.isalpha())
                self.assertEqual(source, source.lower())
                self.assertTrue(10 <= len(source) <= 24)
                self.assertFalse(contains_prohibited_language(source))

    def test_mixed_answer_lengths_outscore_short_answer_farm(self):
        one_note = {"я" * 4 + str(i) for i in range(80)}
        variety = {
            *{"я" * 4 + str(i) for i in range(28)},
            *{"б" * 7 + str(i) for i in range(36)},
            *{"в" * 10 + str(i) for i in range(16)},
        }
        self.assertGreater(source_quality("исследование", variety),
                           source_quality("исследование", one_note))

    def test_tier_bands_are_stable(self):
        self.assertEqual(source_tier(38), "classic")
        self.assertEqual(source_tier(74), "classic")
        self.assertEqual(source_tier(75), "rich")
        self.assertEqual(source_tier(149), "rich")
        self.assertEqual(source_tier(150), "legendary")

    def test_quotas_preserve_classic_rich_and_legendary_rounds(self):
        # Ten candidates per tier, all sources with distinct letter multisets.
        # Numeric tails are fine in this pure-quality-only fixture.
        sources = [
            (f"словарь{i}", make_answers(50))
            for i in range(10)
        ] + [
            (f"романистика{i}", make_answers(90, length=7))
            for i in range(10)
        ] + [
            (f"иллюстрация{i}", make_answers(180, length=8))
            for i in range(10)
        ]
        selected = select_quality_sources(sources, limit=10)
        self.assertEqual(len(selected), 10)
        tiers = Counter(source_tier(len(answers)) for _, answers in selected)
        self.assertGreaterEqual(tiers["classic"], 2)
        self.assertGreaterEqual(tiers["rich"], 5)
        self.assertGreaterEqual(tiers["legendary"], 3)

    def test_quota_fills_when_one_tier_is_missing(self):
        candidates = [
            (f"слово{i}", make_answers(100))
            for i in range(15)
        ]
        selected = select_quality_sources(candidates, limit=12)
        self.assertEqual(len(selected), 12)

    def test_equivalent_letter_bags_are_deduplicated(self):
        candidates = [
            ("абвгде", make_answers(70)),
            ("едгвба", make_answers(90)),
            ("книжечка", make_answers(95)),
        ]
        result = select_quality_sources(candidates, limit=10)
        self.assertEqual(len(result), 2)
        self.assertIn("едгвба", {word for word, _ in result})

    def test_public_source_bank_statistics_use_real_compiled_candidates(self):
        from lexicon_learning_raccoon import LearningLexiconService
        service = object.__new__(LearningLexiconService)
        service.round_candidates = [
            ("романтика", make_answers(40)),
            ("путешествие", make_answers(90)),
            ("конструирование", make_answers(155)),
        ]
        rendered = service.render_source_pool()
        self.assertIn("Готовых стартовых слов: <b>3</b>", rendered)
        self.assertIn("Классических (38–74 слов): <b>1</b>", rendered)
        self.assertIn("Богатых (75–149 слов): <b>1</b>", rendered)
        self.assertIn("Легендарных (150+ слов): <b>1</b>", rendered)
        self.assertNotIn("романтика", rendered)
        self.assertNotIn("путешествие", rendered)
        self.assertNotIn("конструирование", rendered)
        self.assertIn("/lexicon_pool", service.render_help())

    def test_letter_bitmask_never_rejects_a_really_buildable_word(self):
        sources = ("конструкторская", "программирование", "иллюстрация")
        words = ("кот", "корона", "станция", "роман", "слон", "программа")
        for source in sources:
            signature = _letters_signature(source)
            mask = _letters_mask(source)
            for word in words:
                with self.subTest(source=source, word=word):
                    fits = _signature_fits(_letters_signature(word), signature)
                    if fits:
                        self.assertFalse(_letters_mask(word) & ~mask)


if __name__ == "__main__":
    unittest.main()
