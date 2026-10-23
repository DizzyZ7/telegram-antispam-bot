import random
import unittest

from entertainment.celebrations import celebration_kind, festive_reply


class CelebrationTests(unittest.TestCase):
    def test_birthday(self):
        for message in ("С днем рождения, Катя!", "С днём рождения!", "С ДР!", "Happy birthday!"):
            self.assertEqual(celebration_kind(message), "birthday", message)

    def test_congratulation(self):
        self.assertEqual(celebration_kind("Поздравляю с победой!"), "congratulation")
        self.assertEqual(celebration_kind("С праздником, ребята!"), "congratulation")

    def test_no_false_positive(self):
        for message in ("обычное сообщение", "/fun поздравляю", "вчера был день рождения"):
            self.assertIsNone(celebration_kind(message))

    def test_festive_reply_is_bounded_and_varied(self):
        outputs = {festive_reply("birthday", rng=random.Random(seed)) for seed in range(10)}
        self.assertGreater(len(outputs), 5)
        self.assertTrue(all(len(text) <= 500 for text in outputs))
        self.assertTrue(all("🎉" in text or "🤗" in text or "🥳" in text or "🫦" in text or "🎊" in text or "✨" in text or "🎂" in text or "🤣" in text or "💃" in text or "🪩" in text for text in outputs))


if __name__ == "__main__":
    unittest.main()
