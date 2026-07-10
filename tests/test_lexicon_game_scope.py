from __future__ import annotations

import unittest
from types import SimpleNamespace

from lexicon_game_scope import (
    LEXICON_ONLY_CHAT_IDS,
    LexiconGameAppScope,
    LexiconOnlyChatFilter,
)


class FakeApp:
    def __init__(self) -> None:
        self.allowed_chats = {-1001111111111}
        self.marker = "delegated"

    def is_allowed_chat(self, chat_id: int) -> bool:
        return chat_id in self.allowed_chats


class LexiconGameScopeTests(unittest.IsolatedAsyncioTestCase):
    def test_lexicon_only_chat_is_not_added_to_global_scope(self) -> None:
        app = FakeApp()
        chat_id = -1002659916114
        scoped_app = LexiconGameAppScope(app, LEXICON_ONLY_CHAT_IDS)

        self.assertFalse(app.is_allowed_chat(chat_id))
        self.assertTrue(scoped_app.is_allowed_chat(chat_id))
        self.assertNotIn(chat_id, app.allowed_chats)

    def test_existing_allowed_chats_remain_available(self) -> None:
        app = FakeApp()
        scoped_app = LexiconGameAppScope(app, LEXICON_ONLY_CHAT_IDS)

        self.assertTrue(scoped_app.is_allowed_chat(-1001111111111))

    def test_unrelated_chat_stays_blocked(self) -> None:
        app = FakeApp()
        scoped_app = LexiconGameAppScope(app, LEXICON_ONLY_CHAT_IDS)

        self.assertFalse(scoped_app.is_allowed_chat(-1009999999999))

    def test_other_app_attributes_are_delegated(self) -> None:
        app = FakeApp()
        scoped_app = LexiconGameAppScope(app, LEXICON_ONLY_CHAT_IDS)

        self.assertEqual(scoped_app.marker, "delegated")

    async def test_guard_filter_matches_only_lexicon_chat(self) -> None:
        filter_ = LexiconOnlyChatFilter(LEXICON_ONLY_CHAT_IDS)
        lexicon_message = SimpleNamespace(chat=SimpleNamespace(id=-1002659916114))
        ordinary_message = SimpleNamespace(chat=SimpleNamespace(id=-1001111111111))

        self.assertTrue(await filter_(lexicon_message))
        self.assertFalse(await filter_(ordinary_message))


if __name__ == "__main__":
    unittest.main()
