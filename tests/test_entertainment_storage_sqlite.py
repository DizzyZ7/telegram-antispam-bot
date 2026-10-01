from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from entertainment.models import EntertainmentSettings
from entertainment.storage.sqlite import SQLiteEntertainmentStorage


class SQLiteEntertainmentStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "entertainment.db"
        self.storage = SQLiteEntertainmentStorage(self.database_path)
        await self.storage.initialize()

    async def asyncTearDown(self) -> None:
        await self.storage.close()
        self.temp_dir.cleanup()

    async def test_messages_are_isolated_by_chat_and_topic(self) -> None:
        await self.storage.add_message(-1001, 10, 1, "topic ten message", message_id=101)
        await self.storage.add_message(-1001, 20, 2, "topic twenty message", message_id=202)
        await self.storage.add_message(-1002, 10, 3, "other chat message", message_id=303)

        self.assertEqual(await self.storage.recent_messages(-1001, 10), ["topic ten message"])
        self.assertEqual(await self.storage.recent_messages(-1001, 20), ["topic twenty message"])
        self.assertEqual(await self.storage.recent_messages(-1002, 10), ["other chat message"])

    async def test_count_and_clear_scope_keep_siblings_isolated(self) -> None:
        await self.storage.add_message(-1001, 10, 1, "one")
        await self.storage.add_message(-1001, 20, 1, "two")
        await self.storage.add_message(-1002, 10, 2, "three")

        self.assertEqual(await self.storage.message_count(-1001, None), 2)
        self.assertEqual(await self.storage.message_count(-1001, 10), 1)

        removed = await self.storage.clear_scope(-1001, 10)
        self.assertEqual(removed, 1)
        self.assertEqual(await self.storage.message_count(-1001, 20), 1)
        self.assertEqual(await self.storage.message_count(-1002, None), 1)

        removed_chat = await self.storage.clear_scope(-1001, None)
        self.assertEqual(removed_chat, 1)
        self.assertEqual(await self.storage.message_count(-1002, None), 1)

    async def test_settings_remain_isolated_by_chat(self) -> None:
        await self.storage.save_settings(
            -1001,
            EntertainmentSettings(enabled=True, laziness=71, cooldown_seconds=90),
        )
        first = await self.storage.get_settings(-1001)
        second = await self.storage.get_settings(-1002)
        self.assertEqual(first.laziness, 71)
        self.assertEqual(first.cooldown_seconds, 90)
        self.assertEqual(second, EntertainmentSettings())


class SQLiteEntertainmentUpgradeTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_message_table_upgrades_in_place(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        try:
            path = Path(temp_dir.name) / "legacy.db"
            connection = sqlite3.connect(path)
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
                "INSERT INTO entertainment_messages(chat_id, user_id, text, created_at) VALUES(?, ?, ?, ?)",
                (-1001, 7, "legacy message survives", 1),
            )
            connection.commit()
            connection.close()

            storage = SQLiteEntertainmentStorage(path)
            await storage.initialize()
            try:
                self.assertEqual(await storage.recent_messages(-1001, 0), ["legacy message survives"])
                await storage.add_message(-1001, 99, 8, "new topic message", message_id=500)
                self.assertEqual(await storage.recent_messages(-1001, 99), ["new topic message"])
            finally:
                await storage.close()
        finally:
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
