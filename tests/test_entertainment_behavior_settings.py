from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from entertainment.models import BehaviorMode, EntertainmentSettings
from entertainment.storage.sqlite import SQLiteEntertainmentStorage


class EntertainmentBehaviorSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_defaults_use_alive_mode(self) -> None:
        settings = EntertainmentSettings()
        self.assertEqual(settings.behavior_mode, BehaviorMode.ALIVE)
        self.assertEqual(settings.timezone, "Europe/Moscow")
        self.assertTrue(settings.autonomous_text_enabled)
        self.assertIsNone(settings.quiet_hours_start)
        self.assertIsNone(settings.quiet_hours_end)

    async def test_sqlite_round_trips_v2_settings(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        try:
            path = Path(temp_dir.name) / "settings.db"
            storage = SQLiteEntertainmentStorage(path)
            await storage.initialize()
            try:
                expected = EntertainmentSettings(
                    enabled=False,
                    behavior_mode=BehaviorMode.CALM,
                    quiet_hours_start=23,
                    quiet_hours_end=7,
                    timezone="Europe/Berlin",
                    autonomous_text_enabled=False,
                )
                await storage.save_settings(-1001, expected)
                self.assertEqual(await storage.get_settings(-1001), expected)
            finally:
                await storage.close()
        finally:
            temp_dir.cleanup()

    async def test_v1_settings_table_upgrades_without_using_legacy_frequency(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        try:
            path = Path(temp_dir.name) / "legacy-settings.db"
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
                INSERT INTO entertainment_chat_settings(
                    chat_id, enabled, laziness, cooldown_seconds, updated_at
                ) VALUES(?, ?, ?, ?, ?)
                """,
                (-1001, 0, 3, 5, 1),
            )
            connection.commit()
            connection.close()

            storage = SQLiteEntertainmentStorage(path)
            await storage.initialize()
            try:
                settings = await storage.get_settings(-1001)
                self.assertFalse(settings.enabled)
                self.assertEqual(settings.behavior_mode, BehaviorMode.ALIVE)
                self.assertNotEqual(settings.behavior_mode.value, "3")
            finally:
                await storage.close()
        finally:
            temp_dir.cleanup()

    async def test_invalid_stored_mode_normalizes_to_alive(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        try:
            path = Path(temp_dir.name) / "invalid-mode.db"
            storage = SQLiteEntertainmentStorage(path)
            await storage.initialize()
            try:
                assert storage.connection is not None
                await storage.connection.execute(
                    """
                    INSERT INTO entertainment_chat_settings(
                        chat_id, enabled, laziness, cooldown_seconds, updated_at,
                        behavior_mode, timezone, autonomous_text_enabled
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (-1002, 1, 99, 999, 1, "broken", "Europe/Moscow", 1),
                )
                await storage.connection.commit()
                self.assertEqual(
                    (await storage.get_settings(-1002)).behavior_mode,
                    BehaviorMode.ALIVE,
                )
            finally:
                await storage.close()
        finally:
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
