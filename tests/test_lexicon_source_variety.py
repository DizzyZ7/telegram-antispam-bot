"""Regression tests for Lexicon starter variety and new candidate bank."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lexicon_dictionary_patch import DictionaryBackedLexiconService
from lexicon_source_variety import choose_varied_source
from lexicon_source_words import LONG_SOURCE_WORDS
from lexicon_source_words_extra4 import LONG_SOURCE_WORDS_EXTRA4
from minigames import MiniGameStorage
from writers_moderation import contains_prohibited_language


class PreferMaximumWeight:
    def choices(self, population, weights, *, k=1):
        assert k == 1
        return [population[max(range(len(weights)), key=lambda i: weights[i])]]


class LexiconStarterVarietyTests(unittest.TestCase):
    def test_adjacent_same_word_family_is_avoided_when_possible(self):
        candidates = {
            "электроавтоматика": set(range(55)),
            "электроустановка": set(range(65)),
            "мореплавание": set(range(45)),
        }
        result = choose_varied_source(
            list(candidates),
            candidates,
            ["электродвигатель"],
            rng=PreferMaximumWeight(),
        )
        self.assertEqual(result, "мореплавание")

    def test_reuses_family_if_all_available_sources_are_related(self):
        candidates = {
            "электроавтоматика": set(range(50)),
            "электроустановка": set(range(80)),
        }
        result = choose_varied_source(
            list(candidates),
            candidates,
            ["электродвигатель"],
            rng=PreferMaximumWeight(),
        )
        self.assertIn(result, candidates)

    def test_underplayed_tier_wins_against_saturated_tier(self):
        candidates = {
            "светотехника": set(range(50)),
            "мореплавание": set(range(170)),
        }
        for i in range(6):
            candidates[f"недавнее{i}"] = set(range(50))
        recent = [f"недавнее{i}" for i in range(6)]
        result = choose_varied_source(
            ["светотехника", "мореплавание"],
            candidates,
            recent,
            rng=PreferMaximumWeight(),
        )
        self.assertEqual(result, "мореплавание")

    def test_one_candidate_always_remains_playable(self):
        candidate = "исследовательница"
        self.assertEqual(
            choose_varied_source(
                [candidate], {candidate: set(range(60))}, [candidate]
            ),
            candidate,
        )

    def test_empty_available_pool_is_rejected(self):
        with self.assertRaises(ValueError):
            choose_varied_source([], {}, [])


class StarterBankPackTests(unittest.TestCase):
    def test_new_pack_has_90_unique_nouns_with_playable_shape(self):
        self.assertEqual(len(LONG_SOURCE_WORDS_EXTRA4), 90)
        self.assertEqual(len(LONG_SOURCE_WORDS_EXTRA4), len(set(LONG_SOURCE_WORDS_EXTRA4)))
        for word in LONG_SOURCE_WORDS_EXTRA4:
            with self.subTest(word=word):
                self.assertTrue(10 <= len(word) <= 24)
                self.assertRegex(word, r"^[а-яе]+$")
                self.assertFalse(contains_prohibited_language(word))
                self.assertIn(word, LONG_SOURCE_WORDS)
        self.assertEqual(len(LONG_SOURCE_WORDS), len(set(LONG_SOURCE_WORDS)))


class LexiconVarietyCycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = MiniGameStorage(Path(self.temp_dir.name) / "game.db")
        await self.storage.initialize()
        self.service = object.__new__(DictionaryBackedLexiconService)
        self.service.storage = self.storage
        self.service.active_word_games = {}
        self.service.round_candidates = [
            ("радиостанция", {"радио"}),
            ("хроматография", {"графика"}),
            ("полимеризация", {"полимер"}),
        ]

    async def asyncTearDown(self):
        await self.storage.close()
        self.temp_dir.cleanup()

    async def test_last_cycle_history_is_used_after_rollover(self):
        # Inspect the selection input without coupling to implementation RNG.
        observed = []

        def fake_pick(available, candidate_map, recent):
            observed.append(tuple(recent))
            return available[0]

        with patch("lexicon_dictionary_patch.choose_varied_source", side_effect=fake_pick):
            a, _ = await self.service._claim_next_source((-1001, 1))
            b, _ = await self.service._claim_next_source((-1001, 2))
            c, _ = await self.service._claim_next_source((-1001, 3))
            d, _ = await self.service._claim_next_source((-1001, 4))

        self.assertEqual(len({a, b, c}), 3)
        self.assertNotEqual(c, d)
        self.assertEqual(observed[0], ())
        self.assertEqual(observed[1][0], a)
        self.assertEqual(observed[2][0], b)
        self.assertEqual(observed[3][:3], (c, b, a))
        self.assertEqual(await self.service.source_cycle_status(), (2, 2))


if __name__ == "__main__":
    unittest.main()
