from __future__ import annotations

import unittest
from dataclasses import dataclass, field

from lexicon_scoring import balanced_word_points, player_rank_key, points_for_length


@dataclass
class FakePlayer:
    name: str
    points: int
    words: dict[str, int] = field(default_factory=dict)


class LexiconScoringTests(unittest.TestCase):
    def test_length_bands(self) -> None:
        expected = {
            3: 0,
            4: 1,
            5: 2,
            6: 2,
            7: 3,
            8: 3,
            9: 4,
            10: 4,
            11: 5,
            20: 5,
            32: 5,
        }
        for length, points in expected.items():
            with self.subTest(length=length):
                self.assertEqual(points_for_length(length), points)

    def test_three_long_words_cannot_beat_fifty_five_short_words(self) -> None:
        long_score = 3 * balanced_word_points("а" * 24)
        short_score = 55 * balanced_word_points("слон")
        self.assertEqual(long_score, 15)
        self.assertEqual(short_score, 55)
        self.assertLess(long_score, short_score)

    def test_length_bonus_remains_meaningful(self) -> None:
        long_score = 12 * balanced_word_points("а" * 12)
        short_score = 55 * balanced_word_points("слон")
        self.assertGreater(long_score, short_score)

    def test_more_words_break_equal_score_tie(self) -> None:
        broad = FakePlayer("Больше слов", 20, {f"слово{i}": 1 for i in range(10)})
        narrow = FakePlayer("Меньше слов", 20, {f"термин{i}": 5 for i in range(4)})
        ordered = sorted([narrow, broad], key=player_rank_key)
        self.assertIs(ordered[0], broad)

    def test_longest_word_is_third_tie_break(self) -> None:
        longer = FakePlayer("Длиннее", 20, {"короткое": 10, "необыкновенность": 10})
        shorter = FakePlayer("Короче", 20, {"обычное": 10, "словарик": 10})
        ordered = sorted([shorter, longer], key=player_rank_key)
        self.assertIs(ordered[0], longer)


if __name__ == "__main__":
    unittest.main()
