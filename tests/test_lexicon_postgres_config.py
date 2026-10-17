"""Regression coverage for durable Lexicon approvals and raccoon configuration."""

from __future__ import annotations

from contextlib import asynccontextmanager
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import asyncpg

from lexicon_learning import LEXICON_TEXT_COMMAND_RE, _reply_storage
from lexicon_learning_persistent import LearningLexiconService
from lexicon_learning_raccoon import LearningLexiconService as RaccoonService
from lexicon_postgres_config import LexiconPostgresConfig


FIRST_STICKER = "CAACAgIAAxkBAAAB1234567890"
SECOND_STICKER = "CAACAgIAAxkBAAAB9876543210"


class _FakePool:
    def __init__(self, connection: object, *, fail_first: bool = False) -> None:
        self.connection = connection
        self.fail_first = fail_first
        self.acquires = 0

    @asynccontextmanager
    async def acquire(self, *, timeout: int):
        self.acquires += 1
        if self.fail_first and self.acquires == 1:
            raise OSError("transient network failure")
        yield self.connection


class LexiconHandlerRegressionTests(unittest.IsolatedAsyncioTestCase):
    def test_lexadd_alias_matches_and_preserves_argument(self) -> None:
        for command in ("/lexadd слово", "/lex_add слово", "/lexicon_add слово"):
            with self.subTest(command=command):
                matched = LEXICON_TEXT_COMMAND_RE.match(command)
                self.assertIsNotNone(matched)
                self.assertEqual(matched.group(2), "слово")

    def test_storage_command_is_recognized(self) -> None:
        for command in ("/lex_storage", "/lexicon_storage"):
            self.assertIsNotNone(LEXICON_TEXT_COMMAND_RE.fullmatch(command))

    async def test_transient_connection_failure_retries_once(self) -> None:
        connection = SimpleNamespace(execute=AsyncMock(return_value="INSERT 0 1"))
        pool = _FakePool(connection, fail_first=True)
        db = LexiconPostgresConfig("postgresql://test:test@localhost:5432/test")
        db._get_pool = AsyncMock(return_value=pool)
        with patch("lexicon_postgres_config.asyncio.sleep", new_callable=AsyncMock) as sleep:
            await db.add_word("стик")
        self.assertEqual(pool.acquires, 2)
        connection.execute.assert_awaited_once()
        sleep.assert_awaited_once()

    async def test_invalid_table_privilege_does_not_retry(self) -> None:
        error = asyncpg.InsufficientPrivilegeError("permission denied for table")
        connection = SimpleNamespace(execute=AsyncMock(side_effect=error))
        pool = _FakePool(connection)
        db = LexiconPostgresConfig("postgresql://test:test@localhost:5432/test")
        db._get_pool = AsyncMock(return_value=pool)
        with self.assertRaises(asyncpg.InsufficientPrivilegeError):
            await db.add_word("стик")
        self.assertEqual(pool.acquires, 1)

    async def test_admin_diagnostics_report_health_without_db_secrets(self) -> None:
        fake_store = SimpleNamespace(inspect=AsyncMock(return_value=(7, True)))
        service = SimpleNamespace(lexicon_pg_storage=fake_store)
        message = SimpleNamespace(reply=AsyncMock())
        app = SimpleNamespace(safe_output_text=lambda text: text)
        with patch("lexicon_learning._is_chat_admin", new=AsyncMock(return_value=True)):
            await _reply_storage(app, service, message)
        output = message.reply.await_args.args[0]
        self.assertIn("PostgreSQL: соединение активно", output)
        self.assertIn("7", output)
        self.assertIn("Право INSERT", output)

    async def test_diagnostic_does_not_expose_exception_message(self) -> None:
        fake_store = SimpleNamespace(
            inspect=AsyncMock(side_effect=OSError("postgresql://user:secret@host/database"))
        )
        service = SimpleNamespace(lexicon_pg_storage=fake_store)
        message = SimpleNamespace(reply=AsyncMock())
        app = SimpleNamespace(safe_output_text=lambda text: text)
        with patch("lexicon_learning._is_chat_admin", new=AsyncMock(return_value=True)):
            await _reply_storage(app, service, message)
        output = message.reply.await_args.args[0]
        self.assertIn("OSError", output)
        self.assertNotIn("secret", output)

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
        await self.db.close()
        connection = await asyncpg.connect(self.url)
        try:
            await connection.execute("DROP TABLE IF EXISTS lexicon_config")
            await connection.execute("DROP TABLE IF EXISTS lexicon_approved_words")
        finally:
            await connection.close()

    async def test_reusable_single_connection_and_insert_permission(self) -> None:
        await self.db.bootstrap(set(), None)
        pool = self.db._pool
        self.assertIsNotNone(pool)
        await self.db.add_word("стик")
        await self.db.add_word("стик")
        self.assertIs(self.db._pool, pool)
        self.assertLessEqual(pool.get_size(), 1)
        count, can_insert = await self.db.inspect()
        self.assertEqual(count, 1)
        self.assertTrue(can_insert)

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
