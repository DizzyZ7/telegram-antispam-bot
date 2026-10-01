from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from entertainment.storage.migrations import migrate_v1_sqlite_if_needed
from entertainment.storage.sqlite import SQLiteEntertainmentStorage


def create_v1_database(path: Path) -> None:
    connection = sqlite3.connect(path)
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
        (-1001, 1, 77, 60, 100),
    )
    connection.execute(
        "INSERT INTO entertainment_messages(id, chat_id, user_id, text, created_at) VALUES(?, ?, ?, ?, ?)",
        (41, -1001, 7, "старое сообщение должно переехать", 101),
    )
    connection.commit()
    connection.close()


class EntertainmentMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_v1_data_moves_to_topic_zero_and_second_run_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "v1.db"
            target_path = root / "v2.db"
            create_v1_database(source)

            target = SQLiteEntertainmentStorage(target_path)
            await target.initialize()
            try:
                first = await migrate_v1_sqlite_if_needed(source, target)
                self.assertEqual(first.settings_imported, 1)
                self.assertEqual(first.messages_imported, 1)
                self.assertFalse(first.already_applied)
                self.assertEqual(
                    await target.recent_messages(-1001, 0),
                    ["старое сообщение должно переехать"],
                )
                settings = await target.get_settings(-1001)
                self.assertEqual(settings.laziness, 77)
                self.assertEqual(settings.cooldown_seconds, 60)

                second = await migrate_v1_sqlite_if_needed(source, target)
                self.assertTrue(second.already_applied)
                self.assertEqual(second.settings_imported, 0)
                self.assertEqual(second.messages_imported, 0)
                self.assertEqual(await target.message_count(-1001, None), 1)
                self.assertTrue(source.exists())
            finally:
                await target.close()

    async def test_existing_target_settings_are_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "v1.db"
            target_path = root / "v2.db"
            create_v1_database(source)

            target = SQLiteEntertainmentStorage(target_path)
            await target.initialize()
            try:
                from entertainment.models import EntertainmentSettings

                await target.save_settings(
                    -1001,
                    EntertainmentSettings(enabled=True, laziness=12, cooldown_seconds=333),
                )
                report = await migrate_v1_sqlite_if_needed(source, target)
                settings = await target.get_settings(-1001)
                self.assertEqual(settings.laziness, 12)
                self.assertEqual(settings.cooldown_seconds, 333)
                self.assertEqual(report.settings_imported, 0)
                self.assertEqual(report.messages_imported, 1)
            finally:
                await target.close()

    async def test_missing_source_is_safe_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = SQLiteEntertainmentStorage(Path(temp_dir) / "v2.db")
            await target.initialize()
            try:
                report = await migrate_v1_sqlite_if_needed(Path(temp_dir) / "missing.db", target)
                self.assertEqual(report.settings_imported, 0)
                self.assertEqual(report.messages_imported, 0)
                self.assertEqual(report.skipped, 0)
                self.assertFalse(report.already_applied)
            finally:
                await target.close()


if __name__ == "__main__":
    unittest.main()
