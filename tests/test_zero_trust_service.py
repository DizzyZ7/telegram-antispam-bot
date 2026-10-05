import random
import unittest
from dataclasses import replace

from zero_trust.config import ZeroTrustConfig
from zero_trust.models import ChallengeRecord, ChallengeStatus


class FakeStorage:
    def __init__(self) -> None:
        self.rows: dict[int, ChallengeRecord] = {}
        self.next_id = 1
        self.expire_calls: list[int] = []
        self.cancel_calls: list[tuple[int, int, int]] = []
        self.attach_calls: list[tuple[int, int]] = []

    async def create_challenge(self, **kwargs):
        chat_id = int(kwargs["chat_id"])
        user_id = int(kwargs["user_id"])
        created_at = int(kwargs["created_at"])
        for challenge_id, row in tuple(self.rows.items()):
            if (
                row.chat_id == chat_id
                and row.user_id == user_id
                and row.status in {ChallengeStatus.PENDING, ChallengeStatus.VERIFIED}
            ):
                self.rows[challenge_id] = replace(
                    row,
                    status=ChallengeStatus.CANCELLED,
                    completed_at=created_at,
                )
        row = ChallengeRecord(
            id=self.next_id,
            chat_id=chat_id,
            user_id=user_id,
            expected_answer=int(kwargs["expected_answer"]),
            attempts=0,
            status=ChallengeStatus.PENDING,
            created_at=created_at,
            expires_at=int(kwargs["expires_at"]),
            username=kwargs["username"],
            display_name=kwargs["display_name"],
        )
        self.rows[row.id] = row
        self.next_id += 1
        return row

    async def get_challenge(self, challenge_id: int):
        return self.rows.get(int(challenge_id))

    async def attach_message_id(self, challenge_id: int, message_id: int):
        self.attach_calls.append((challenge_id, message_id))
        row = self.rows[challenge_id]
        self.rows[challenge_id] = replace(row, telegram_message_id=message_id)

    async def apply_answer(self, *, challenge_id, chat_id, user_id, answer, now):
        row = self.rows.get(int(challenge_id))
        if row is None:
            raise LookupError(challenge_id)
        if row.chat_id != int(chat_id) or row.user_id != int(user_id):
            return row
        if row.status is ChallengeStatus.VERIFIED:
            return row
        if row.status is not ChallengeStatus.PENDING:
            return row
        if int(now) >= row.expires_at:
            row = replace(row, status=ChallengeStatus.EXPIRED, completed_at=int(now))
        elif int(answer) == row.expected_answer:
            row = replace(row, status=ChallengeStatus.VERIFIED, verified_at=int(now))
        else:
            row = replace(row, attempts=row.attempts + 1)
        self.rows[row.id] = row
        return row

    async def mark_passed(self, *, challenge_id, chat_id, user_id, now):
        row = self.rows[int(challenge_id)]
        if (
            row.chat_id == int(chat_id)
            and row.user_id == int(user_id)
            and row.status is ChallengeStatus.VERIFIED
        ):
            row = replace(row, status=ChallengeStatus.PASSED, completed_at=int(now))
            self.rows[row.id] = row
        return row

    async def cancel_active(self, *, chat_id, user_id, now):
        self.cancel_calls.append((int(chat_id), int(user_id), int(now)))
        count = 0
        for challenge_id, row in tuple(self.rows.items()):
            if (
                row.chat_id == int(chat_id)
                and row.user_id == int(user_id)
                and row.status in {ChallengeStatus.PENDING, ChallengeStatus.VERIFIED}
            ):
                self.rows[challenge_id] = replace(
                    row,
                    status=ChallengeStatus.CANCELLED,
                    completed_at=int(now),
                )
                count += 1
        return count

    async def expire_stale(self, *, now):
        now = int(now)
        self.expire_calls.append(now)
        count = 0
        for challenge_id, row in tuple(self.rows.items()):
            if row.status is ChallengeStatus.PENDING and row.expires_at <= now:
                self.rows[challenge_id] = replace(
                    row,
                    status=ChallengeStatus.EXPIRED,
                    completed_at=now,
                )
                count += 1
        return count


class Clock:
    def __init__(self, value: int) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


class ZeroTrustServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.storage = FakeStorage()
        self.clock = Clock(1_000)
        self.config = ZeroTrustConfig(
            chat_ids=frozenset({-1002619489118, -1003237014529}),
            challenge_ttl_seconds=300,
        )

    def _service(self):
        from zero_trust.service import ZeroTrustService

        return ZeroTrustService(
            self.storage,
            self.config,
            rng=random.Random(7),
            now_fn=self.clock,
        )

    async def test_same_user_gets_independent_challenges_in_two_chats(self):
        service = self._service()
        writers, _ = await service.begin_join(
            chat_id=-1002619489118,
            user_id=77,
            username="author",
            display_name="Author",
        )
        trader, _ = await service.begin_join(
            chat_id=-1003237014529,
            user_id=77,
            username="author",
            display_name="Author",
        )

        self.assertNotEqual(writers.id, trader.id)
        self.assertEqual(writers.chat_id, -1002619489118)
        self.assertEqual(trader.chat_id, -1003237014529)
        self.assertEqual(self.storage.rows[writers.id].status, ChallengeStatus.PENDING)
        self.assertEqual(self.storage.rows[trader.id].status, ChallengeStatus.PENDING)

    async def test_historical_pass_never_suppresses_rejoin(self):
        service = self._service()
        first, prompt = await service.begin_join(
            chat_id=-1003237014529,
            user_id=77,
            username=None,
            display_name="A",
        )
        result = await service.answer(
            challenge_id=first.id,
            chat_id=first.chat_id,
            user_id=first.user_id,
            answer=prompt.answer,
        )
        self.assertEqual(result.kind.value, "verified")
        passed = await service.finalize_pass(
            challenge_id=first.id,
            chat_id=first.chat_id,
            user_id=first.user_id,
        )
        self.assertEqual(passed.status, ChallengeStatus.PASSED)

        self.clock.value += 10
        second, _ = await service.begin_join(
            chat_id=-1003237014529,
            user_id=77,
            username=None,
            display_name="A",
        )
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(second.status, ChallengeStatus.PENDING)

    async def test_stale_or_cross_chat_identity_returns_stale(self):
        from zero_trust.service import AnswerKind

        service = self._service()
        challenge, prompt = await service.begin_join(
            chat_id=-1002619489118,
            user_id=77,
            username=None,
            display_name=None,
        )

        missing = await service.answer(
            challenge_id=999,
            chat_id=-1002619489118,
            user_id=77,
            answer=prompt.answer,
        )
        wrong_chat = await service.answer(
            challenge_id=challenge.id,
            chat_id=-1003237014529,
            user_id=77,
            answer=prompt.answer,
        )
        wrong_user = await service.answer(
            challenge_id=challenge.id,
            chat_id=-1002619489118,
            user_id=88,
            answer=prompt.answer,
        )

        self.assertIs(missing.kind, AnswerKind.STALE)
        self.assertIs(wrong_chat.kind, AnswerKind.STALE)
        self.assertIs(wrong_user.kind, AnswerKind.STALE)
        self.assertEqual(self.storage.rows[challenge.id].status, ChallengeStatus.PENDING)

    async def test_wrong_correct_expired_and_verified_retry_classification(self):
        from zero_trust.service import AnswerKind

        service = self._service()
        challenge, prompt = await service.begin_join(
            chat_id=-1002619489118,
            user_id=77,
            username=None,
            display_name=None,
        )
        wrong = await service.answer(
            challenge_id=challenge.id,
            chat_id=challenge.chat_id,
            user_id=77,
            answer=prompt.answer + 100,
        )
        verified = await service.answer(
            challenge_id=challenge.id,
            chat_id=challenge.chat_id,
            user_id=77,
            answer=prompt.answer,
        )
        retry = await service.answer(
            challenge_id=challenge.id,
            chat_id=challenge.chat_id,
            user_id=77,
            answer=prompt.answer,
        )

        self.assertIs(wrong.kind, AnswerKind.WRONG)
        self.assertIs(verified.kind, AnswerKind.VERIFIED)
        self.assertIs(retry.kind, AnswerKind.ALREADY_VERIFIED)

        self.clock.value = 2_000
        expired_challenge, expired_prompt = await service.begin_join(
            chat_id=-1003237014529,
            user_id=77,
            username=None,
            display_name=None,
        )
        self.clock.value = expired_challenge.expires_at
        expired = await service.answer(
            challenge_id=expired_challenge.id,
            chat_id=expired_challenge.chat_id,
            user_id=77,
            answer=expired_prompt.answer,
        )
        self.assertIs(expired.kind, AnswerKind.EXPIRED)

    async def test_start_expires_stale_pending_and_cancel_leave_is_exact(self):
        service = self._service()
        writers, _ = await service.begin_join(
            chat_id=-1002619489118,
            user_id=77,
            username=None,
            display_name=None,
        )
        trader, _ = await service.begin_join(
            chat_id=-1003237014529,
            user_id=77,
            username=None,
            display_name=None,
        )
        self.clock.value = writers.expires_at
        expired_count = await service.start()
        self.assertEqual(expired_count, 2)
        self.assertEqual(self.storage.expire_calls, [writers.expires_at])

        self.clock.value += 10
        fresh_writers, _ = await service.begin_join(
            chat_id=-1002619489118,
            user_id=77,
            username=None,
            display_name=None,
        )
        fresh_trader, _ = await service.begin_join(
            chat_id=-1003237014529,
            user_id=77,
            username=None,
            display_name=None,
        )
        cancelled = await service.cancel_leave(chat_id=-1002619489118, user_id=77)
        self.assertEqual(cancelled, 1)
        self.assertEqual(self.storage.rows[fresh_writers.id].status, ChallengeStatus.CANCELLED)
        self.assertEqual(self.storage.rows[fresh_trader.id].status, ChallengeStatus.PENDING)

    async def test_attach_message_id_and_protection_scope(self):
        service = self._service()
        challenge, _ = await service.begin_join(
            chat_id=-1002619489118,
            user_id=77,
            username=None,
            display_name=None,
        )
        await service.attach_message_id(challenge.id, 555)

        self.assertTrue(service.is_protected_chat(-1002619489118))
        self.assertFalse(service.is_protected_chat(-999))
        self.assertEqual(self.storage.attach_calls, [(challenge.id, 555)])
        self.assertEqual(self.storage.rows[challenge.id].telegram_message_id, 555)


if __name__ == "__main__":
    unittest.main()
