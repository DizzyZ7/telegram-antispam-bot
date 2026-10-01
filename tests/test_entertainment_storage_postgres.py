from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from entertainment.models import EntertainmentSettings
from entertainment.storage.migrations import MIGRATION_KEY, migrate_v1_sqlite_if_needed
from entertainment.storage.postgres import PostgresEntertainmentStorage


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class PostgresEntertainmentStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        assert os.environ.get("TEST_DATABASE_URL")
        self.storage = PostgresEntertainmentStorage(os.environ["TEST_DATABASE_URL"])
        await self.storage.initialize()
        for chat_id in (-990001, -990002, -990003):
            await self.storage.clear_scope(chat_id, None)
        pool = self.storage._require_pool()
        await pool.execute(
            "DELETE FROM entertainment_chat_settings WHERE chat_id IN ($1, $2, $3)",
            -990001,
            -990002,
            -990003,
        )
        await pool.execute(
            "DELETE FROM ent_schema_migrations WHERE migration_key = $1",
            MIGRATION_KEY,
        )

    async def asyncTearDown(self) -> None:
        for chat_id in (-990001, -990002, -990003):
            await self.storage.clear_scope(chat_id, None)
        pool = self.storage._require_pool()
        await pool.execute(
            "DELETE FROM entertainment_chat_settings WHERE chat_id IN ($1, $2, $3)",
            -990001,
            -990002,
            -990003,
        )
        await pool.execute(
            "DELETE FROM ent_schema_migrations WHERE migration_key = $1",
            MIGRATION_KEY,
        )
        await self.storage.close()

    async def test_settings_and_messages_match_sqlite_contract(self) -> None:
        await self.storage.save_settings(
            -990001,
            EntertainmentSettings(enabled=True, laziness=63, cooldown_seconds=77),
        )
        settings = await self.storage.get_settings(-990001)
        self.assertEqual(settings.laziness, 63)
        self.assertEqual(settings.cooldown_seconds, 77)

        await self.storage.add_message(-990001, 10, 1, "alpha topic", message_id=1)
        await self.storage.add_message(-990001, 20, 2, "beta topic", message_id=2)
        await self.storage.add_message(-990002, 10, 3, "other chat", message_id=3)

        self.assertEqual(await self.storage.recent_messages(-990001, 10), ["alpha topic"])
        self.assertEqual(await self.storage.recent_messages(-990001, 20), ["beta topic"])
        self.assertEqual(await self.storage.message_count(-990001, None), 2)

        removed = await self.storage.clear_scope(-990001, 10)
        self.assertEqual(removed, 1)
        self.assertEqual(await self.storage.message_count(-990001, 20), 1)
        self.assertEqual(await self.storage.message_count(-990002, None), 1)

    async def test_v1_sqlite_import_into_postgres_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "legacy.db"
            connection = sqlite3.connect(source_path)
            connection.execute(
                """
                CREATE TABLE entertainment_chat_settings (
                    chat_id INTEGER PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    laziness INTEGER NOT NULL DEFAULT 92,
                    cooldown_seconds INTEGER NOT NULL DEFAULT 45,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE entertainment_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO entertainment_chat_settings(chat_id, enabled, laziness, cooldown_seconds, updated_at) VALUES(?, ?, ?, ?, ?)",
                (-990003, 1, 74, 55, 100),
            )
            connection.execute(
                "INSERT INTO entertainment_messages(id, chat_id, user_id, text, created_at) VALUES(?, ?, ?, ?, ?)",
                (9001, -990003, 7, "legacy postgres migration", 101),
            )
            connection.commit()
            connection.close()

            first = await migrate_v1_sqlite_if_needed(source_path, self.storage)
            self.assertEqual(first.settings_imported, 1)
            self.assertEqual(first.messages_imported, 1)
            self.assertFalse(first.already_applied)
            self.assertEqual(
                await self.storage.recent_messages(-990003, 0),
                ["legacy postgres migration"],
            )
            settings = await self.storage.get_settings(-990003)
            self.assertEqual(settings.laziness, 74)
            self.assertEqual(settings.cooldown_seconds, 55)

            second = await migrate_v1_sqlite_if_needed(source_path, self.storage)
            self.assertTrue(second.already_applied)
            self.assertEqual(second.messages_imported, 0)
            self.assertEqual(await self.storage.message_count(-990003, None), 1)
            self.assertTrue(source_path.exists())


if __name__ == "__main__":
    unittest.main()
