from __future__ import annotations

import unittest
from types import SimpleNamespace

from lexicon_raccoon import RACCOON_WORD, raccoon_can_hide, raccoon_finder_name


class LexiconRaccoonTests(unittest.TestCase):
    def test_raccoon_hides_in_hyperspace(self) -> None:
        self.assertTrue(raccoon_can_hide("гиперпространство"))

    def test_raccoon_is_absent_without_required_letters(self) -> None:
        self.assertFalse(raccoon_can_hide("предпринимательница"))

    def test_finder_name_is_returned_after_raccoon_is_claimed(self) -> None:
        round_data = SimpleNamespace(
            used_words={RACCOON_WORD: 42},
            players={42: SimpleNamespace(name="DizZy_Z7")},
        )
        self.assertEqual(raccoon_finder_name(round_data), "DizZy_Z7")

    def test_finder_is_none_before_raccoon_is_found(self) -> None:
        round_data = SimpleNamespace(used_words={}, players={})
        self.assertIsNone(raccoon_finder_name(round_data))


if __name__ == "__main__":
    unittest.main()
