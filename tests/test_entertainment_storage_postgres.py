from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from entertainment.models import (
    BehaviorMode,
    EntertainmentActionRecord,
    EntertainmentActionType,
    EntertainmentSettings,
)
from entertainment.storage.migrations import MIGRATION_KEY, migrate_v1_sqlite_if_needed
from entertainment.storage.postgres import PostgresEntertainmentStorage


TEST_CHAT_IDS = (-990001, -990002, -990003, -990004)


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class PostgresEntertainmentStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        assert os.environ.get("TEST_DATABASE_URL")
        self.storage = PostgresEntertainmentStorage(os.environ["TEST_DATABASE_URL"])
        await self.storage.initialize()
        for chat_id in TEST_CHAT_IDS:
            await self.storage.clear_scope(chat_id, None)
        pool = self.storage._require_pool()
        await pool.execute("DELETE FROM ent_actions WHERE chat_id = ANY($1::bigint[])", list(TEST_CHAT_IDS))
        await pool.execute(
            "DELETE FROM entertainment_chat_settings WHERE chat_id = ANY($1::bigint[])",
            list(TEST_CHAT_IDS),
        )
        await pool.execute(
            "DELETE FROM ent_schema_migrations WHERE migration_key = $1",
            MIGRATION_KEY,
        )

    async def asyncTearDown(self) -> None:
        for chat_id in TEST_CHAT_IDS:
            await self.storage.clear_scope(chat_id, None)
        pool = self.storage._require_pool()
        await pool.execute("DELETE FROM ent_actions WHERE chat_id = ANY($1::bigint[])", list(TEST_CHAT_IDS))
        await pool.execute(
            "DELETE FROM entertainment_chat_settings WHERE chat_id = ANY($1::bigint[])",
            list(TEST_CHAT_IDS),
        )
        await pool.execute(
            "DELETE FROM ent_schema_migrations WHERE migration_key = $1",
            MIGRATION_KEY,
        )
        await self.storage.close()

    async def test_settings_and_messages_match_sqlite_contract(self) -> None:
        expected = EntertainmentSettings(
            enabled=True,
            behavior_mode=BehaviorMode.ACTIVE,
            quiet_hours_start=1,
            quiet_hours_end=6,
            timezone="Europe/Berlin",
            autonomous_text_enabled=False,
            laziness=63,
            cooldown_seconds=77,
        )
        await self.storage.save_settings(-990001, expected)
        settings = await self.storage.get_settings(-990001)
        self.assertEqual(settings, expected)

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

    async def test_activity_snapshot_matches_sqlite_window_semantics(self) -> None:
        now = 20_000
        await self.storage.add_message(-990001, 30, 1, "fresh one", created_at=19_980)
        await self.storage.add_message(-990001, 30, 2, "fresh two", created_at=19_940)
        await self.storage.add_message(-990001, 30, 1, "five edge", created_at=19_700)
        await self.storage.add_message(-990001, 30, 3, "previous", created_at=19_699)
        await self.storage.add_message(-990001, 30, 4, "previous older", created_at=19_450)
        await self.storage.add_message(-990001, 30, 5, "fifteen edge", created_at=19_100)
        await self.storage.add_message(-990001, 30, 6, "sixty edge", created_at=16_400)
        await self.storage.add_message(-990001, 30, 7, "older than sixty", created_at=16_399)
        await self.storage.add_message(-990001, 30, 8, "one twenty edge", created_at=12_800)
        await self.storage.add_message(-990001, 30, 9, "too old", created_at=12_799)
        await self.storage.add_message(-990001, 40, 99, "sibling", created_at=19_990)

        snapshot = await self.storage.activity_snapshot(-990001, 30, now=now)
        self.assertEqual(snapshot.messages_1m, 2)
        self.assertEqual(snapshot.messages_5m, 3)
        self.assertEqual(snapshot.messages_previous_5m, 2)
        self.assertEqual(snapshot.messages_15m, 6)
        self.assertEqual(snapshot.messages_60m, 7)
        self.assertEqual(snapshot.messages_120m, 9)
        self.assertEqual(snapshot.active_users_5m, 2)
        self.assertEqual(snapshot.active_users_60m, 6)
        self.assertEqual(snapshot.seconds_since_human, 20.0)

    async def test_action_history_matches_sqlite_contract(self) -> None:
        first_id = await self.storage.record_action(
            EntertainmentActionRecord(
                id=None,
                chat_id=-990001,
                topic_id=50,
                action_type=EntertainmentActionType.REMIXED_PHRASE,
                trigger_message_id=501,
                created_at=30_000,
                metadata={"phase": "active", "score": 0.75},
            )
        )
        second_id = await self.storage.record_action(
            EntertainmentActionRecord(
                id=None,
                chat_id=-990001,
                topic_id=50,
                action_type=EntertainmentActionType.CONTEXTUAL_REPLY,
                trigger_message_id=502,
                created_at=30_100,
                metadata={"phase": "cooldown"},
            )
        )
        await self.storage.record_action(
            EntertainmentActionRecord(
                id=None,
                chat_id=-990001,
                topic_id=51,
                action_type=EntertainmentActionType.MEMORY_CALLBACK,
                trigger_message_id=None,
                created_at=30_200,
                metadata={"isolated": True},
            )
        )
        await self.storage.add_message(-990001, 50, 1, "after", created_at=30_150)

        actions = await self.storage.recent_actions(-990001, 50, since=29_000)
        self.assertEqual([action.id for action in actions], [second_id, first_id])
        self.assertEqual(actions[0].metadata, {"phase": "cooldown"})
        self.assertEqual(actions[1].metadata["score"], 0.75)
        self.assertEqual(await self.storage.human_messages_since(-990001, 50, since=30_100), 1)

    async def test_invalid_stored_mode_normalizes_to_alive(self) -> None:
        pool = self.storage._require_pool()
        await pool.execute(
            """
            INSERT INTO entertainment_chat_settings(
                chat_id, enabled, laziness, cooldown_seconds, behavior_mode,
                timezone, autonomous_text_enabled, updated_at
            ) VALUES($1, TRUE, 1, 5, $2, 'Europe/Moscow', TRUE, 1)
            """,
            -990004,
            "broken",
        )
        settings = await self.storage.get_settings(-990004)
        self.assertEqual(settings.behavior_mode, BehaviorMode.ALIVE)

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
            self.assertEqual(settings.behavior_mode, BehaviorMode.ALIVE)
            self.assertEqual(settings.laziness, 74)
            self.assertEqual(settings.cooldown_seconds, 55)

            second = await migrate_v1_sqlite_if_needed(source_path, self.storage)
            self.assertTrue(second.already_applied)
            self.assertEqual(second.messages_imported, 0)
            self.assertEqual(await self.storage.message_count(-990003, None), 1)
            self.assertTrue(source_path.exists())


if __name__ == "__main__":
    unittest.main()
