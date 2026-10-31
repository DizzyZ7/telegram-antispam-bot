import random
import unittest

from entertainment.grammar_composer import generate_grammar_message, _agree_past, _vocabulary, _adjectives, _agree_adjective
from entertainment.moderation_safety import is_safe_entertainment_reply


class GrammarComposerTests(unittest.TestCase):
    def test_adjective_agreement(self):
        self.assertEqual(_agree_adjective("странный", "masc"), "странный")
        self.assertEqual(_agree_adjective("странный", "femn"), "странная")
        self.assertEqual(_agree_adjective("странный", "neut"), "странное")

    def test_learns_adjective_lemmas(self):
        self.assertIn("странный", _adjectives(["Странная сковорода", "Странные роботы"]))

    def test_gender_agreement(self):
        self.assertEqual(_agree_past("объявил", "femn"), "объявила")
        self.assertEqual(_agree_past("объявил", "neut"), "объявило")
        self.assertEqual(_agree_past("объявил", "masc"), "объявил")

    def test_learns_noun_lemmas_not_chat_verbs(self):
        words = _vocabulary(["Холодильники работают", "Сковорода приехала", "Сковороды"])
        self.assertIn(("сковорода", "femn"), words)
        self.assertIn(("холодильник", "masc"), words)
        self.assertNotIn(("работают", "masc"), words)

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
