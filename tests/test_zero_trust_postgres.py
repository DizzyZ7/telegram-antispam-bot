import asyncio
import os
import unittest

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()


@unittest.skipUnless(TEST_DATABASE_URL, "TEST_DATABASE_URL is required")
class ZeroTrustPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from zero_trust.storage import PostgresZeroTrustStorage

        self.storage = PostgresZeroTrustStorage(TEST_DATABASE_URL)
        await self.storage.initialize()
        assert self.storage.pool is not None
        await self.storage.pool.execute("TRUNCATE zero_trust_challenges RESTART IDENTITY")

    async def asyncTearDown(self):
        await self.storage.close()

    async def _create(self, *, chat_id=-1001, user_id=77, answer=12, created_at=100):
        return await self.storage.create_challenge(
            chat_id=chat_id,
            user_id=user_id,
            expected_answer=answer,
            created_at=created_at,
            expires_at=created_at + 300,
            username="tester",
            display_name="Test User",
        )

    async def test_initialize_creates_durable_table(self):
        assert self.storage.pool is not None
        table_name = await self.storage.pool.fetchval(
            "SELECT to_regclass('public.zero_trust_challenges')::text"
        )
        self.assertEqual(table_name, "zero_trust_challenges")

    async def test_same_user_has_independent_challenges_in_different_chats(self):
        first = await self._create(chat_id=-1002619489118)
        second = await self._create(chat_id=-1003237014529, created_at=101)

        self.assertNotEqual(first.id, second.id)
        self.assertEqual(first.status.value, "pending")
        self.assertEqual(second.status.value, "pending")
        self.assertEqual((await self.storage.get_challenge(first.id)).chat_id, -1002619489118)
        self.assertEqual((await self.storage.get_challenge(second.id)).chat_id, -1003237014529)

    async def test_rejoin_cancels_only_previous_active_session_for_same_chat_user(self):
        first = await self._create(created_at=100)
        other_chat = await self._create(chat_id=-2002, created_at=101)
        second = await self._create(created_at=102)

        first_after = await self.storage.get_challenge(first.id)
        other_after = await self.storage.get_challenge(other_chat.id)
        self.assertEqual(first_after.status.value, "cancelled")
        self.assertEqual(first_after.completed_at, 102)
        self.assertEqual(other_after.status.value, "pending")
        self.assertEqual(second.status.value, "pending")

    async def test_concurrent_duplicate_joins_leave_exactly_one_active_session(self):
        first, second = await asyncio.gather(
            self._create(created_at=100),
            self._create(created_at=101),
        )
        history = await self.storage.history_for(-1001, 77, limit=10)
        active = [row for row in history if row.status.value in {"pending", "verified"}]

        self.assertEqual(len(history), 2)
        self.assertEqual(len(active), 1)
        self.assertIn(active[0].id, {first.id, second.id})
        self.assertEqual(
            {row.status.value for row in history},
            {"pending", "cancelled"},
        )

    async def test_wrong_answer_mutates_only_exact_challenge(self):
        first = await self._create(chat_id=-1001)
        second = await self._create(chat_id=-1002, created_at=101)

        result = await self.storage.apply_answer(
            challenge_id=first.id,
            chat_id=-1001,
            user_id=77,
            answer=999,
            now=120,
        )
        second_after = await self.storage.get_challenge(second.id)

        self.assertEqual(result.status.value, "pending")
        self.assertEqual(result.attempts, 1)
        self.assertEqual(second_after.attempts, 0)
        self.assertEqual(second_after.status.value, "pending")

    async def test_identity_mismatch_cannot_transition_challenge(self):
        challenge = await self._create()
        result = await self.storage.apply_answer(
            challenge_id=challenge.id,
            chat_id=-9999,
            user_id=77,
            answer=12,
            now=120,
        )
        after = await self.storage.get_challenge(challenge.id)

        self.assertEqual(result.status.value, "pending")
        self.assertEqual(after.status.value, "pending")
        self.assertEqual(after.attempts, 0)

    async def test_expired_pending_challenge_cannot_verify(self):
        challenge = await self.storage.create_challenge(
            chat_id=-1001,
            user_id=77,
            expected_answer=12,
            created_at=100,
            expires_at=110,
            username=None,
            display_name=None,
        )
        result = await self.storage.apply_answer(
            challenge_id=challenge.id,
            chat_id=-1001,
            user_id=77,
            answer=12,
            now=110,
        )

        self.assertEqual(result.status.value, "expired")
        self.assertEqual(result.completed_at, 110)

    async def test_correct_answer_verifies_then_mark_passed_finalizes(self):
        challenge = await self._create()
        verified = await self.storage.apply_answer(
            challenge_id=challenge.id,
            chat_id=-1001,
            user_id=77,
            answer=12,
            now=120,
        )
        passed = await self.storage.mark_passed(
            challenge_id=challenge.id,
            chat_id=-1001,
            user_id=77,
            now=121,
        )

        self.assertEqual(verified.status.value, "verified")
        self.assertEqual(verified.verified_at, 120)
        self.assertEqual(passed.status.value, "passed")
        self.assertEqual(passed.completed_at, 121)

    async def test_history_survives_storage_recreation(self):
        challenge = await self._create()
        await self.storage.apply_answer(
            challenge_id=challenge.id,
            chat_id=-1001,
            user_id=77,
            answer=12,
            now=120,
        )
        await self.storage.mark_passed(
            challenge_id=challenge.id,
            chat_id=-1001,
            user_id=77,
            now=121,
        )
        await self.storage.close()

        from zero_trust.storage import PostgresZeroTrustStorage

        reopened = PostgresZeroTrustStorage(TEST_DATABASE_URL)
        await reopened.initialize()
        try:
            history = await reopened.history_for(-1001, 77)
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0].status.value, "passed")
            self.assertEqual(history[0].id, challenge.id)
        finally:
            await reopened.close()


if __name__ == "__main__":
    unittest.main()
