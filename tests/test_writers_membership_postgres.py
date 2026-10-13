"""PostgreSQL evidence of known channel members and DM notice throttling."""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

from writers_submission.membership import WritersCommunityMembershipRegistry
from writers_submission.storage import PostgresWritersSubmissionStorage

URL = os.getenv("TEST_DATABASE_URL", "").strip()
CHAT = -100999888777


@unittest.skipUnless(URL, "TEST_DATABASE_URL is required")
class WritersCommunityRegistryPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.storage = PostgresWritersSubmissionStorage(URL)
        await self.storage.initialize()
        self.registry = WritersCommunityMembershipRegistry(self.storage)
        await self.registry.initialize()
        await self.storage.pool.execute(
            "DELETE FROM writers_community_members WHERE chat_id=$1", CHAT,
        )

    async def asyncTearDown(self):
        await self.storage.pool.execute(
            "DELETE FROM writers_community_members WHERE chat_id=$1", CHAT,
        )
        await self.storage.close()

    async def test_verified_membership_persists_and_event_order_is_monotone(self):
        await self.registry.observe(
            chat_id=CHAT, user_id=42,
            member=SimpleNamespace(status="member"),
            source="lookup", at=100,
        )
        await self.registry.observe(
            chat_id=CHAT, user_id=43,
            member=SimpleNamespace(status="left"),
            source="event", at=102,
        )
        self.assertEqual(await self.registry.counts(CHAT), (1, 1))

        # An old delayed event must not revert a newer verification.
        await self.registry.observe(
            chat_id=CHAT, user_id=42,
            member=SimpleNamespace(status="left"),
            source="event", at=90,
        )
        self.assertEqual(await self.registry.counts(CHAT), (1, 1))

        await self.registry.observe(
            chat_id=CHAT, user_id=42,
            member=SimpleNamespace(status="left"),
            source="event", at=120,
        )
        self.assertEqual(await self.registry.counts(CHAT), (0, 2))

        await self.storage.close()
        self.storage = PostgresWritersSubmissionStorage(URL)
        await self.storage.initialize()
        self.registry = WritersCommunityMembershipRegistry(self.storage)
        await self.registry.initialize()
        self.assertEqual(await self.registry.counts(CHAT), (0, 2))

    async def test_one_private_join_reminder_per_day_even_across_restarts(self):
        await self.registry.observe(
            chat_id=CHAT, user_id=42,
            member=SimpleNamespace(status="left"),
            source="lookup", at=100,
        )
        self.assertTrue(await self.registry.claim_join_notice(CHAT, 42, at=100))
        self.assertFalse(await self.registry.claim_join_notice(CHAT, 42, at=101))
        self.assertFalse(await self.registry.claim_join_notice(CHAT, 42, at=86_499))
        self.assertTrue(await self.registry.claim_join_notice(CHAT, 42, at=86_501))
        await self.registry.observe(
            chat_id=CHAT, user_id=42,
            member=SimpleNamespace(status="member"),
            source="lookup", at=86_502,
        )
        self.assertFalse(await self.registry.claim_join_notice(CHAT, 42, at=200_000))

    async def test_no_membership_check_results_are_leaked_across_communities(self):
        await self.registry.observe(
            chat_id=CHAT, user_id=42,
            member=SimpleNamespace(status="member"),
            source="lookup", at=100,
        )
        self.assertEqual(await self.registry.counts(CHAT + 1), (0, 0))
        self.assertFalse(await self.registry.claim_join_notice(CHAT + 1, 42, at=101))


if __name__ == "__main__":
    unittest.main()
