from __future__ import annotations

import os
import unittest

from entertainment.models import EntertainmentSettings
from entertainment.storage.postgres import PostgresEntertainmentStorage


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class PostgresEntertainmentStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        assert os.environ.get("TEST_DATABASE_URL")
        self.storage = PostgresEntertainmentStorage(os.environ["TEST_DATABASE_URL"])
        await self.storage.initialize()
        await self.storage.clear_scope(-990001, None)
        await self.storage.clear_scope(-990002, None)

    async def asyncTearDown(self) -> None:
        await self.storage.clear_scope(-990001, None)
        await self.storage.clear_scope(-990002, None)
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


if __name__ == "__main__":
    unittest.main()
