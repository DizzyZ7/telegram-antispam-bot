"""Real PostgreSQL regression: per-chat opt-in style survives and deletes."""
from __future__ import annotations

import os
import unittest

from entertainment.storage.windows import PostgresEntertainmentStorage


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL missing")
class PostgreSQLPersonalityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.storage = PostgresEntertainmentStorage(os.environ["TEST_DATABASE_URL"])
        await self.storage.initialize()
        self.chat = -990031
        self.owner = 314159
        pool = self.storage._require_pool()
        await pool.execute(
            "DELETE FROM ent_personality_preferences WHERE chat_id=$1",
            self.chat,
        )

    async def asyncTearDown(self) -> None:
        if self.storage.pool is not None:
            await self.storage._require_pool().execute(
                "DELETE FROM ent_personality_preferences WHERE chat_id=$1",
                self.chat,
            )
        await self.storage.close()

    async def test_opt_in_is_isolated_persistent_and_reversible(self):
        self.assertIsNone(await self.storage.get_personality_style(self.chat, self.owner))
        await self.storage.set_personality_style(self.chat, self.owner, "gentle")
        self.assertEqual(await self.storage.get_personality_style(self.chat, self.owner), "gentle")
        self.assertIsNone(await self.storage.get_personality_style(self.chat, self.owner + 1))
        self.assertIsNone(await self.storage.get_personality_style(self.chat - 1, self.owner))
        await self.storage.close()
        await self.storage.initialize()
        self.assertEqual(await self.storage.get_personality_style(self.chat, self.owner), "gentle")
        await self.storage.set_personality_style(self.chat, self.owner, "absurd")
        self.assertEqual(await self.storage.get_personality_style(self.chat, self.owner), "absurd")
        await self.storage.set_personality_style(self.chat, self.owner, None)
        self.assertIsNone(await self.storage.get_personality_style(self.chat, self.owner))

    async def test_topic_and_atomic_personal_reply_cooldown_survive_restart(self):
        await self.storage.set_personality_style(self.chat, self.owner, "ironic")
        self.assertTrue(await self.storage.set_personality_topic(
            self.chat, self.owner, "characters",
        ))
        self.assertFalse(await self.storage.set_personality_topic(
            self.chat, self.owner + 1, "characters",
        ))
        self.assertEqual(
            await self.storage.get_personality_topic(self.chat, self.owner),
            "characters",
        )
        self.assertTrue(await self.storage.try_reserve_personality_reply(
            self.chat, self.owner, now=90000, cooldown_seconds=21600,
        ))
        self.assertFalse(await self.storage.try_reserve_personality_reply(
            self.chat, self.owner, now=90001, cooldown_seconds=21600,
        ))
        await self.storage.close()
        await self.storage.initialize()
        self.assertEqual(
            await self.storage.get_personality_topic(self.chat, self.owner),
            "characters",
        )
        self.assertFalse(await self.storage.try_reserve_personality_reply(
            self.chat, self.owner, now=100000, cooldown_seconds=21600,
        ))
        self.assertTrue(await self.storage.try_reserve_personality_reply(
            self.chat, self.owner, now=111600, cooldown_seconds=21600,
        ))
        await self.storage.set_personality_topic(self.chat, self.owner, None)
        self.assertIsNone(
            await self.storage.get_personality_topic(self.chat, self.owner)
        )
        await self.storage.set_personality_style(self.chat, self.owner, None)
        self.assertFalse(await self.storage.try_reserve_personality_reply(
            self.chat, self.owner, now=200000, cooldown_seconds=21600,
        ))

    async def test_migration_adds_columns_without_replacing_previous_data(self):
        # The previous release already stores styles. The new schema must
        # keep that row when applying the relationship migration repeatedly.
        await self.storage.set_personality_style(self.chat, self.owner, "gentle")
        await self.storage.close()
        await self.storage.initialize()
        self.assertEqual(
            await self.storage.get_personality_style(self.chat, self.owner),
            "gentle",
        )
        fields = await self.storage._require_pool().fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='ent_personality_preferences'"
        )
        columns = {row["column_name"] for row in fields}
        self.assertIn("preferred_topic", columns)
        self.assertIn("last_personalized_at", columns)

    async def test_unsupported_styles_never_persist(self):
        with self.assertRaises(ValueError):
            await self.storage.set_personality_style(self.chat, self.owner, "unknown")
        self.assertIsNone(await self.storage.get_personality_style(self.chat, self.owner))


if __name__ == "__main__":
    unittest.main()
