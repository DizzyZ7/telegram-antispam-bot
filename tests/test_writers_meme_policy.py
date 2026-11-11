"""The unwanted word is a word family, not a broad Russian substring."""
import unittest

from entertainment.moderation_safety import (
    is_safe_entertainment_reply, is_safe_entertainment_source, is_safe_search_reply,
)
from writers_meme_policy import contains_meme_words, rewrite_meme_words


class MemePolicyTests(unittest.TestCase):
    def test_replaces_all_occurrences_preserving_suffix_and_case(self):
        for source, expected in (
            ('я могнул, а они могнули!', 'я муркнул, а они муркнули!'),
            ('Могнешь? МОГНУЛА, могнёшь.', 'Муркнешь? МУРКНУЛА, муркнёшь.'),
            ('могнуть могну могнут могнет могнем могните',
             'муркнуть муркну муркнут муркнет муркнем муркните'),
            ('<b>Могнул</b>\n😸 могнул.', '<b>Муркнул</b>\n😸 муркнул.'),
        ):
            with self.subTest(source=source):
                self.assertTrue(contains_meme_words(source))
                self.assertEqual(rewrite_meme_words(source), expected)

    def test_harmless_near_matches_are_unchanged(self):
        for source in ('мог, могли, могучий, могут, помогнуть', 'вымогнули',
                       'могнулище', 'кот муркнул и уснул', ''):
            with self.subTest(source=source):
                self.assertFalse(contains_meme_words(source))
                self.assertEqual(rewrite_meme_words(source), source)
        self.assertFalse(contains_meme_words(None))

    def test_forbidden_meme_cannot_reenter_entertainment_or_search(self):
        for text in ('Кот могнул и пошел спать.', 'Облака могнули на диван.'):
            with self.subTest(text=text):
                self.assertFalse(is_safe_entertainment_source(text))
                self.assertFalse(is_safe_entertainment_reply(text))
                self.assertFalse(is_safe_search_reply(text))
        self.assertTrue(is_safe_entertainment_reply('Кот муркнул и пошел спать.'))
