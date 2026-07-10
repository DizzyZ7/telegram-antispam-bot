from __future__ import annotations

import unittest
from types import SimpleNamespace

from lexicon_game_scope import (
    LEXICON_ONLY_CHAT_IDS,
    LexiconGameAppScope,
    LexiconOnlyChatFilter,
    enforce_lexicon_only_isolation,
)


WRITERS_CHAT_ID = -1002619489118
LEXICON_ONLY_CHAT_ID = -1002659916114
OTHER_ALLOWED_CHAT_ID = -1001111111111
UNRELATED_CHAT_ID = -1009999999999
WRITERS_TOPIC_ID = 14637


class FakeApp:
    def __init__(self) -> None:
        self.ALLOWED_CHATS = [
            WRITERS_CHAT_ID,
            OTHER_ALLOWED_CHAT_ID,
            LEXICON_ONLY_CHAT_ID,
        ]
        self.marker = "delegated"

    def is_allowed_chat(self, chat_id: int) -> bool:
        return chat_id in self.ALLOWED_CHATS


class LexiconGameScopeTests(unittest.IsolatedAsyncioTestCase):
    def test_isolation_removes_only_new_lexicon_chat(self) -> None:
        app = FakeApp()

        isolated_ids = enforce_lexicon_only_isolation(app, LEXICON_ONLY_CHAT_IDS)

        self.assertEqual(isolated_ids, LEXICON_ONLY_CHAT_IDS)
        self.assertNotIn(LEXICON_ONLY_CHAT_ID, app.ALLOWED_CHATS)
        self.assertIn(WRITERS_CHAT_ID, app.ALLOWED_CHATS)
        self.assertIn(OTHER_ALLOWED_CHAT_ID, app.ALLOWED_CHATS)

    def test_lexicon_proxy_keeps_old_chat_and_adds_new_chat_only_for_game(self) -> None:
        app = FakeApp()
        enforce_lexicon_only_isolation(app, LEXICON_ONLY_CHAT_IDS)
        scoped_app = LexiconGameAppScope(app, LEXICON_ONLY_CHAT_IDS)

        self.assertTrue(app.is_allowed_chat(WRITERS_CHAT_ID))
        self.assertTrue(scoped_app.is_allowed_chat(WRITERS_CHAT_ID))

        self.assertFalse(app.is_allowed_chat(LEXICON_ONLY_CHAT_ID))
        self.assertTrue(scoped_app.is_allowed_chat(LEXICON_ONLY_CHAT_ID))

        self.assertFalse(app.is_allowed_chat(UNRELATED_CHAT_ID))
        self.assertFalse(scoped_app.is_allowed_chat(UNRELATED_CHAT_ID))

    def test_old_writers_topic_is_not_intercepted_by_lexicon_only_guard(self) -> None:
        filter_ = LexiconOnlyChatFilter(LEXICON_ONLY_CHAT_IDS)
        old_chat_topic_message = SimpleNamespace(
            chat=SimpleNamespace(id=WRITERS_CHAT_ID),
            message_thread_id=WRITERS_TOPIC_ID,
        )
        new_chat_message = SimpleNamespace(
            chat=SimpleNamespace(id=LEXICON_ONLY_CHAT_ID),
            message_thread_id=None,
        )

        self.assertFalse(await filter_(old_chat_topic_message))
        self.assertTrue(await filter_(new_chat_message))

    def test_existing_allowed_chat_remains_available(self) -> None:
        app = FakeApp()
        enforce_lexicon_only_isolation(app, LEXICON_ONLY_CHAT_IDS)
        scoped_app = LexiconGameAppScope(app, LEXICON_ONLY_CHAT_IDS)

        self.assertTrue(scoped_app.is_allowed_chat(OTHER_ALLOWED_CHAT_ID))

    def test_other_app_attributes_are_delegated(self) -> None:
        app = FakeApp()
        scoped_app = LexiconGameAppScope(app, LEXICON_ONLY_CHAT_IDS)

        self.assertEqual(scoped_app.marker, "delegated")


if __name__ == "__main__":
    unittest.main()
