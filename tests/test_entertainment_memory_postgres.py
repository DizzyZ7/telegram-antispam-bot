from __future__ import annotations

import asyncio
import os
import unittest

from entertainment.storage import PostgresEntertainmentStorage


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class PostgresEntertainmentMemoryRetentionTests(unittest.TestCase):
    def test_prunes_topic_memory_in_batches(self) -> None:
        async def scenario() -> None:
            storage = PostgresEntertainmentStorage(
                os.environ["TEST_DATABASE_URL"],
                memory_limit=3,
                prune_buffer=2,
            )
            await storage.initialize()
            chat_id = -1999000111
            topic_id = 771
            try:
                await storage.clear_scope(chat_id, topic_id)
                for index in range(5):
                    await storage.add_message(chat_id, topic_id, index + 1, f"pg-message-{index}")
                self.assertEqual(await storage.message_count(chat_id, topic_id), 5)

                await storage.add_message(chat_id, topic_id, 99, "pg-trigger-prune")
                self.assertEqual(await storage.message_count(chat_id, topic_id), 3)
                self.assertEqual(
                    await storage.recent_messages(chat_id, topic_id, limit=10),
                    ["pg-message-3", "pg-message-4", "pg-trigger-prune"],
                )
            finally:
                await storage.clear_scope(chat_id, topic_id)
                await storage.close()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
