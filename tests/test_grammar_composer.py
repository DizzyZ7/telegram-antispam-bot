import random
import unittest

from entertainment.grammar_composer import generate_grammar_message, _agree_past, _vocabulary
from entertainment.moderation_safety import is_safe_entertainment_reply


class GrammarComposerTests(unittest.TestCase):\n    def test_gender_agreement(self):\n        self.assertEqual(_agree_past("объявил", "femn"), "объявила")\n        self.assertEqual(_agree_past("объявил", "neut"), "объявило")\n        self.assertEqual(_agree_past("объявил", "masc"), "объявил")\n\n    def test_learns_noun_lemmas_not_chat_verbs(self):\n        words = _vocabulary(["Холодильники работают", "Сковорода приехала", "Сковороды"])\n        self.assertIn(("сковорода", "femn"), words)\n        self.assertIn(("холодильник", "masc"), words)\n        self.assertNotIn(("работают", "masc"), words)\n
    def test_new_complete_safe_sentences(self):
        sources = ["Кот гуляет", "Робот работает", "Пельмень приехал"]
        text = generate_grammar_message(sources, [], random.Random(4))
        self.assertIsNotNone(text)
        self.assertTrue(text.endswith("."))
        self.assertTrue(is_safe_entertainment_reply(text))
        self.assertNotIn(text, sources)

    def test_does_not_repeat_prior_output(self):
        sources = ["Кот гуляет", "Робот работает"]
        first = generate_grammar_message(sources, [], random.Random(4))
        second = generate_grammar_message(sources, [first], random.Random(4))
        self.assertNotEqual(first, second)

    def test_does_not_leak_obscene_chat_tokens(self):
        text = generate_grammar_message(
            ["проебать день", "Кот гуляет"], [], random.Random(2)
        )
        self.assertIsNotNone(text)
        self.assertTrue(is_safe_entertainment_reply(text))


if __name__ == "__main__":
    unittest.main()
