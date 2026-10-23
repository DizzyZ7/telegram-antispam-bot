import random
import unittest

from entertainment.voice import choose_voice_variant


class VoiceVariationTests(unittest.TestCase):
    def test_no_change_without_history(self):
        self.assertEqual(choose_voice_variant("Снова кот.", [], rng=random.Random(1)), "Снова кот.")

    def test_questions_are_never_rewritten(self):
        history = ["раз.", "два.", "три."]
        self.assertEqual(choose_voice_variant("Что происходит?", history, rng=random.Random(1)), "Что происходит?")

    def test_repeated_flat_cadence_can_vary(self):
        history = ["раз.", "два.", "три."]
        self.assertEqual(choose_voice_variant("Кот вернулся.", history, rng=random.Random(1)), "Кот вернулся!")

    def test_recent_exclamation_prevents_forced_emphasis(self):
        history = ["раз.", "два!", "три."]
        self.assertEqual(choose_voice_variant("Кот вернулся.", history, rng=random.Random(1)), "Кот вернулся.")


if __name__ == "__main__":
    unittest.main()
