"""Regression coverage for durable Lexicon approvals and raccoon configuration."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import asyncpg

from lexicon_learning import LEXICON_TEXT_COMMAND_RE
from lexicon_learning_persistent import LearningLexiconService
from lexicon_learning_raccoon import LearningLexiconService as RaccoonService
from lexicon_postgres_config import LexiconPostgresConfig


FIRST_STICKER = "CAACAgIAAxkBAAAB1234567890"
SECOND_STICKER = "CAACAgIAAxkBAAAB9876543210"


class LexiconHandlerRegressionTests(unittest.IsolatedAsyncioTestCase):
    def test_lexadd_alias_matches_and_preserves_argument(self) -> None:
        for command in ("/lexadd слово", "/lex_add слово", "/lexicon_add слово"):
            with self.subTest(command=command):
                matched = LEXICON_TEXT_COMMAND_RE.match(command)
                self.assertIsNotNone(matched)
                self.assertEqual(matched.group(2), "слово")

    async def test_failed_database_write_does_not_approve_word(self) -> None:
        service = object.__new__(LearningLexiconService)
        service.approved_words = set()
        service.dictionary_words = set()
        service.active_word_games = {}
        service.lexicon_pg_storage = SimpleNamespace(
            add_word=AsyncMock(side_effect=RuntimeError("DB unavailable"))
        )
        with patch(
            "lexicon_learning_persistent.validate_lexicon_word",
            return_value=(True, "скворец"),
        ):
            ok, _message = await service.add_approved_word_durable("скворец")
        self.assertFalse(ok)
        self.assertNotIn("скворец", service.approved_words)
        self.assertNotIn("скворец", service.dictionary_words)

    async def test_db_commit_is_acknowledged_even_if_local_volume_is_missing(self) -> None:
        service = object.__new__(LearningLexiconService)
        service.approved_words = set()
        service.dictionary_words = set()
        service.active_word_games = {}
        store = SimpleNamespace(add_word=AsyncMock())
        service.lexicon_pg_storage = store
        with (
            patch(
                "lexicon_learning_persistent.validate_lexicon_word",
                return_value=(True, "скворец"),
            ),
            patch("lexicon_learning_persistent.append_approved_word", side_effect=OSError),
        ):
            ok, word = await service.add_approved_word_durable("скворец")
        self.assertTrue(ok)
        self.assertEqual(word, "скворец")
        store.add_word.assert_awaited_once_with("скворец")
        self.assertIn("скворец", service.approved_words)

    async def test_raccoon_sticker_db_failure_preserves_previous_value(self) -> None:
        service = object.__new__(RaccoonService)
        service.raccoon_sticker_file_id = FIRST_STICKER
        service.lexicon_pg_storage = SimpleNamespace(
            set_sticker=AsyncMock(side_effect=RuntimeError("DB unavailable"))
        )
        with self.assertRaises(RuntimeError):
            await service.configure_raccoon_sticker(SECOND_STICKER)
        self.assertEqual(service.raccoon_sticker_file_id, FIRST_STICKER)

    async def test_raccoon_delivery_failure_is_not_success(self) -> None:
        service = object.__new__(RaccoonService)
        service.raccoon_sticker_file_id = FIRST_STICKER
        service.app = SimpleNamespace(
            bot=SimpleNamespace(send_sticker=AsyncMock(side_effect=RuntimeError("Telegram error")))
        )
        round_data = SimpleNamespace(
            chat_id=-1001, message_thread_id=None, round_code="ROUND"
        )
        self.assertFalse(await service._send_raccoon_sticker(round_data))


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not set")
class LexiconPostgresConfigTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.url = os.environ["TEST_DATABASE_URL"]
        self.db = LexiconPostgresConfig(self.url)
        connection = await asyncpg.connect(self.url)
        try:
            await connection.execute("DROP TABLE IF EXISTS lexicon_config")
            await connection.execute("DROP TABLE IF EXISTS lexicon_approved_words")
        finally:
            await connection.close()

    async def asyncTearDown(self) -> None:
        connection = await asyncpg.connect(self.url)
        try:
            await connection.execute("DROP TABLE IF EXISTS lexicon_config")
            await connection.execute("DROP TABLE IF EXISTS lexicon_approved_words")
        finally:
            await connection.close()

    async def test_migrate_files_and_survive_empty_local_volume(self) -> None:
        words, sticker = await self.db.bootstrap(
            {"дрель", "тропинка"}, FIRST_STICKER
        )
        self.assertEqual(words, {"дрель", "тропинка"})
        self.assertEqual(sticker, FIRST_STICKER)

        await self.db.add_word("дубрава")
        await self.db.set_sticker(SECOND_STICKER)

        # New container starts with no files: DB is still authoritative.
        restored_words, restored_sticker = await self.db.bootstrap(set(), None)
        self.assertEqual(restored_words, {"дрель", "тропинка", "дубрава"})
        self.assertEqual(restored_sticker, SECOND_STICKER)

        # Old backup imports new words without reverting the new sticker.
        restored_words, restored_sticker = await self.db.bootstrap(
            {"берлога"}, FIRST_STICKER
        )
        self.assertIn("берлога", restored_words)
        self.assertEqual(restored_sticker, SECOND_STICKER)


if __name__ == "__main__":
    unittest.main()
