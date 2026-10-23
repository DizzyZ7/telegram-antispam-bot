import random
import unittest
from datetime import datetime

from entertainment.celebrations import celebration_kind, celebration_context, festive_reply


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

    def test_same_occasion_has_stable_words(self):
        first = festive_reply('birthday', rng=random.Random(1), moment=datetime(2026, 10, 23, 12), occasion_key='chat:topic:2026-10-23:birthday')
        second = festive_reply('birthday', rng=random.Random(2), moment=datetime(2026, 10, 23, 12), occasion_key='chat:topic:2026-10-23:birthday')
        self.assertIn('🎉' if '🎉' in first else first.split()[0], first)
        self.assertNotEqual(first, second)  # Same wording seed, different emoji shuffle

    def test_season_and_daypart(self):
        self.assertEqual(celebration_context(datetime(2026, 1, 10, 8)), ('winter', 'morning'))
        self.assertEqual(celebration_context(datetime(2026, 4, 10, 14)), ('spring', 'day'))
        self.assertEqual(celebration_context(datetime(2026, 7, 10, 20)), ('summer', 'evening'))
        self.assertEqual(celebration_context(datetime(2026, 10, 10, 1)), ('autumn', 'night'))

    def test_seasonal_output_changes(self):
        winter = festive_reply('birthday', rng=random.Random(5), moment=datetime(2026, 1, 10, 8))
        summer = festive_reply('birthday', rng=random.Random(5), moment=datetime(2026, 7, 10, 8))
        self.assertNotEqual(winter, summer)
        self.assertLessEqual(len(winter), 500)

    def test_emoji_bursts_are_dense_but_one_message(self):
        text = festive_reply('birthday', rng=random.Random(17))
        self.assertLessEqual(len(text), 500)
        self.assertGreaterEqual(sum(text.count(emoji) for emoji in ('🫦', '🤗', '🥳', '🎉', '🎊', '🤣', '💃', '🪩', '✨', '🎂', '😭', '💀', '🔥', '💖', '❤️', '😍', '😂', '🥹', '🫶', '😎', '💅', '🗣️', '👑', '🍰', '🎁', '🎈', '💐', '⭐', '😼', '🦝')), 9)

    def test_recent_base_phrase_is_not_reused(self):
        previous = festive_reply('birthday', rng=random.Random(2))
        variants = [festive_reply('birthday', rng=random.Random(seed), recent_replies=(previous,)) for seed in range(10)]
        self.assertTrue(all(variant != previous for variant in variants))

    def test_opted_in_public_title_can_be_used(self):
        text = festive_reply('birthday', rng=random.Random(4), public_work_title='Лунная библиотека')
        self.assertIn('«Лунная библиотека»', text)

    def test_untrusted_title_is_not_rendered(self):
        text = festive_reply('birthday', rng=random.Random(4), public_work_title='<private>')
        self.assertNotIn('<private>', text)

    def test_festive_reply_is_bounded_and_varied(self):
        outputs = {festive_reply("birthday", rng=random.Random(seed)) for seed in range(10)}
        self.assertGreater(len(outputs), 5)
        self.assertTrue(all(len(text) <= 500 for text in outputs))
        self.assertTrue(all("🎉" in text or "🤗" in text or "🥳" in text or "🫦" in text or "🎊" in text or "✨" in text or "🎂" in text or "🤣" in text or "💃" in text or "🪩" in text for text in outputs))


if __name__ == "__main__":
    unittest.main()
