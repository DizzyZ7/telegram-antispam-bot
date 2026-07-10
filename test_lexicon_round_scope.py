from __future__ import annotations

import unittest
from types import SimpleNamespace

from lexicon_round_scope import (
    normalize_round_thread_id,
    normalized_round_key,
    round_key_from_message,
    russian_word_count_form,
)

LEXICON_ONLY_CHAT_ID = -1002659916114
WRITERS_CHAT_ID = -1002619489118
WRITERS_TOPIC_A = 14637
WRITERS_TOPIC_B = 42817


def fake_message(
    chat_id: int,
    thread_id: int | None,
    *,
    is_forum: bool | None,
) -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, is_forum=is_forum),
        message_thread_id=thread_id,
    )


class LexiconRoundScopeTests(unittest.TestCase):
    def test_topicless_chat_always_uses_one_round(self) -> None:
        ids = {LEXICON_ONLY_CHAT_ID}
        start_key = round_key_from_message(
            fake_message(LEXICON_ONLY_CHAT_ID, None, is_forum=False),
            single_scope_chat_ids=ids,
        )
        service_thread_key = round_key_from_message(
            fake_message(LEXICON_ONLY_CHAT_ID, 1, is_forum=False),
            single_scope_chat_ids=ids,
        )
        unexpected_thread_key = round_key_from_message(
            fake_message(LEXICON_ONLY_CHAT_ID, 987654, is_forum=False),
            single_scope_chat_ids=ids,
        )

        self.assertEqual(start_key, (LEXICON_ONLY_CHAT_ID, None))
        self.assertEqual(service_thread_key, start_key)
        self.assertEqual(unexpected_thread_key, start_key)

    def test_non_forum_chat_ignores_any_thread_id(self) -> None:
        self.assertIsNone(
            normalize_round_thread_id(
                -100123,
                456,
                is_forum=False,
            )
        )

    def test_general_forum_topic_none_and_one_share_scope(self) -> None:
        no_thread = round_key_from_message(
            fake_message(WRITERS_CHAT_ID, None, is_forum=True)
        )
        general_topic = round_key_from_message(
            fake_message(WRITERS_CHAT_ID, 1, is_forum=True)
        )
        self.assertEqual(no_thread, general_topic)
        self.assertEqual(no_thread, (WRITERS_CHAT_ID, None))

    def test_real_writers_topics_remain_independent(self) -> None:
        topic_a = round_key_from_message(
            fake_message(WRITERS_CHAT_ID, WRITERS_TOPIC_A, is_forum=True)
        )
        topic_b = round_key_from_message(
            fake_message(WRITERS_CHAT_ID, WRITERS_TOPIC_B, is_forum=True)
        )

        self.assertEqual(topic_a, (WRITERS_CHAT_ID, WRITERS_TOPIC_A))
        self.assertEqual(topic_b, (WRITERS_CHAT_ID, WRITERS_TOPIC_B))
        self.assertNotEqual(topic_a, topic_b)

    def test_finish_lookup_normalizes_topicless_stored_key(self) -> None:
        ids = {LEXICON_ONLY_CHAT_ID}
        self.assertEqual(
            normalized_round_key(
                LEXICON_ONLY_CHAT_ID,
                1,
                single_scope_chat_ids=ids,
            ),
            (LEXICON_ONLY_CHAT_ID, None),
        )

    def test_russian_word_count_forms(self) -> None:
        expected = {
            1: "слово",
            2: "слова",
            4: "слова",
            5: "слов",
            11: "слов",
            21: "слово",
            22: "слова",
            83: "слова",
            84: "слова",
            85: "слов",
            111: "слов",
        }
        for count, form in expected.items():
            with self.subTest(count=count):
                self.assertEqual(russian_word_count_form(count), form)


if __name__ == "__main__":
    unittest.main()
