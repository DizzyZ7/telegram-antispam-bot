from __future__ import annotations

import random
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram import Dispatcher

from writers_moderation import WRITERS_RULES_URL
from zero_trust.config import ZeroTrustConfig
from zero_trust.models import ChallengeRecord, ChallengeStatus
from zero_trust.presentation import encode_callback
from zero_trust.service import AnswerKind, AnswerResult, ZeroTrustService

WRITERS_CHAT_ID = -1002619489118
TRADER_CHAT_ID = -1003237014529


class MemoryStorage:
    def __init__(self) -> None:
        self.rows: dict[int, ChallengeRecord] = {}
        self.next_id = 1

    async def create_challenge(self, **kwargs):
        chat_id = int(kwargs["chat_id"])
        user_id = int(kwargs["user_id"])
        created_at = int(kwargs["created_at"])
        for key, row in tuple(self.rows.items()):
            if (
                row.chat_id == chat_id
                and row.user_id == user_id
                and row.status in {ChallengeStatus.PENDING, ChallengeStatus.VERIFIED}
            ):
                self.rows[key] = replace(
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
            username=kwargs.get("username"),
            display_name=kwargs.get("display_name"),
        )
        self.rows[row.id] = row
        self.next_id += 1
        return row

    async def get_challenge(self, challenge_id):
        return self.rows.get(int(challenge_id))

    async def attach_message_id(self, challenge_id, message_id):
        row = self.rows[int(challenge_id)]
        self.rows[row.id] = replace(row, telegram_message_id=int(message_id))

    async def apply_answer(self, *, challenge_id, chat_id, user_id, answer, now):
        row = self.rows[int(challenge_id)]
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
        count = 0
        for key, row in tuple(self.rows.items()):
            if (
                row.chat_id == int(chat_id)
                and row.user_id == int(user_id)
                and row.status in {ChallengeStatus.PENDING, ChallengeStatus.VERIFIED}
            ):
                self.rows[key] = replace(
                    row,
                    status=ChallengeStatus.CANCELLED,
                    completed_at=int(now),
                )
                count += 1
        return count

    async def expire_stale(self, *, now):
        count = 0
        for key, row in tuple(self.rows.items()):
            if row.status is ChallengeStatus.PENDING and row.expires_at <= int(now):
                self.rows[key] = replace(
                    row,
                    status=ChallengeStatus.EXPIRED,
                    completed_at=int(now),
                )
                count += 1
        return count


class WritersScope:
    chat_id = WRITERS_CHAT_ID

    def matches(self, chat) -> bool:
        return int(chat.id) == self.chat_id


def user(user_id=77, *, is_bot=False, username="author", full_name="Author User"):
    return SimpleNamespace(
        id=user_id,
        is_bot=is_bot,
        username=username,
        full_name=full_name,
    )


def chat(chat_id):
    return SimpleNamespace(id=chat_id, username=None)


def join_event(chat_id, target_user):
    return SimpleNamespace(
        chat=chat(chat_id),
        new_chat_member=SimpleNamespace(user=target_user),
    )


def leave_event(chat_id, target_user):
    return SimpleNamespace(
        chat=chat(chat_id),
        new_chat_member=SimpleNamespace(user=target_user),
    )


def callback(chat_id, target_user, payload, *, message_id=500):
    return SimpleNamespace(
        data=payload,
        from_user=target_user,
        message=SimpleNamespace(
            chat=chat(chat_id),
            message_id=message_id,
            message_thread_id=None,
            delete=AsyncMock(),
        ),
        answer=AsyncMock(),
    )


def app():
    sent_ids = iter(range(1000, 1100))

    async def send_message(**kwargs):
        return SimpleNamespace(message_id=next(sent_ids))

    bot = SimpleNamespace(
        restrict_chat_member=AsyncMock(),
        send_message=AsyncMock(side_effect=send_message),
    )
    return SimpleNamespace(
        dp=Dispatcher(),
        bot=bot,
        safe_output_text=lambda value: value,
        user_tag=lambda value: f"@{value.username}" if value.username else value.full_name,
    )


def callbacks_by_name(app_obj, observer):
    return {
        item.callback.__name__: item.callback
        for item in getattr(app_obj.dp, observer).handlers
    }


class ZeroTrustHandlerTests(unittest.IsolatedAsyncioTestCase):
    def make_service(self):
        storage = MemoryStorage()
        service = ZeroTrustService(
            storage,
            ZeroTrustConfig(
                chat_ids=frozenset({WRITERS_CHAT_ID, TRADER_CHAT_ID}),
                challenge_ttl_seconds=300,
            ),
            rng=random.Random(3),
            now_fn=lambda: 1_000,
        )
        return storage, service

    def register(self, app_obj, service):
        from zero_trust.handlers import register_zero_trust_handlers

        register_zero_trust_handlers(
            app_obj,
            service,
            writers_scope=WritersScope(),
        )
        members = callbacks_by_name(app_obj, "chat_member")
        callbacks = callbacks_by_name(app_obj, "callback_query")
        return (
            members["zero_trust_join"],
            members["zero_trust_leave"],
            callbacks["zero_trust_callback"],
        )

    async def test_writers_pass_does_not_bypass_trader_challenge(self):
        storage, service = self.make_service()
        target = user()
        app_obj = app()
        on_join, _, on_callback = self.register(app_obj, service)

        await on_join(join_event(WRITERS_CHAT_ID, target))
        await on_join(join_event(TRADER_CHAT_ID, target))
        writers = next(row for row in storage.rows.values() if row.chat_id == WRITERS_CHAT_ID)
        trader = next(row for row in storage.rows.values() if row.chat_id == TRADER_CHAT_ID)

        cb = callback(
            WRITERS_CHAT_ID,
            target,
            encode_callback(writers.id, target.id, writers.expected_answer),
        )
        await on_callback(cb)

        self.assertEqual(storage.rows[writers.id].status, ChallengeStatus.PASSED)
        self.assertEqual(storage.rows[trader.id].status, ChallengeStatus.PENDING)

    async def test_trader_pass_does_not_bypass_writers_challenge(self):
        storage, service = self.make_service()
        target = user()
        app_obj = app()
        on_join, _, on_callback = self.register(app_obj, service)

        await on_join(join_event(WRITERS_CHAT_ID, target))
        await on_join(join_event(TRADER_CHAT_ID, target))
        writers = next(row for row in storage.rows.values() if row.chat_id == WRITERS_CHAT_ID)
        trader = next(row for row in storage.rows.values() if row.chat_id == TRADER_CHAT_ID)

        cb = callback(
            TRADER_CHAT_ID,
            target,
            encode_callback(trader.id, target.id, trader.expected_answer),
        )
        await on_callback(cb)

        self.assertEqual(storage.rows[trader.id].status, ChallengeStatus.PASSED)
        self.assertEqual(storage.rows[writers.id].status, ChallengeStatus.PENDING)

    async def test_bot_join_and_unprotected_join_do_nothing(self):
        _, service = self.make_service()
        app_obj = app()
        on_join, _, _ = self.register(app_obj, service)

        await on_join(join_event(WRITERS_CHAT_ID, user(is_bot=True)))
        await on_join(join_event(-999, user()))

        app_obj.bot.restrict_chat_member.assert_not_awaited()
        app_obj.bot.send_message.assert_not_awaited()

    async def test_db_failure_after_restriction_never_restores_permissions(self):
        service = SimpleNamespace(
            is_protected_chat=lambda chat_id: True,
            begin_join=AsyncMock(side_effect=RuntimeError("database down")),
            cancel_leave=AsyncMock(),
            answer=AsyncMock(),
            finalize_pass=AsyncMock(),
            attach_message_id=AsyncMock(),
        )
        app_obj = app()
        on_join, _, _ = self.register(app_obj, service)

        await on_join(join_event(TRADER_CHAT_ID, user()))

        self.assertEqual(app_obj.bot.restrict_chat_member.await_count, 1)
        permissions = app_obj.bot.restrict_chat_member.await_args.args[2]
        self.assertFalse(permissions.can_send_messages)
        app_obj.bot.send_message.assert_not_awaited()

    async def test_another_user_cannot_press_challenge_button(self):
        _, service = self.make_service()
        app_obj = app()
        on_join, _, on_callback = self.register(app_obj, service)
        target = user(77)
        intruder = user(88, username="intruder")
        await on_join(join_event(TRADER_CHAT_ID, target))
        challenge = next(iter(service.storage.rows.values()))
        app_obj.bot.restrict_chat_member.reset_mock()

        cb = callback(
            TRADER_CHAT_ID,
            intruder,
            encode_callback(challenge.id, target.id, challenge.expected_answer),
        )
        await on_callback(cb)

        self.assertEqual(service.storage.rows[challenge.id].status, ChallengeStatus.PENDING)
        app_obj.bot.restrict_chat_member.assert_not_awaited()
        cb.answer.assert_awaited_once_with("Это не твоя проверка", show_alert=True)

    async def test_stale_old_callback_cannot_finish_newer_challenge(self):
        storage, service = self.make_service()
        target = user()
        app_obj = app()
        on_join, _, on_callback = self.register(app_obj, service)

        await on_join(join_event(TRADER_CHAT_ID, target))
        old = max(storage.rows.values(), key=lambda row: row.id)
        await on_join(join_event(TRADER_CHAT_ID, target))
        new = max(storage.rows.values(), key=lambda row: row.id)
        app_obj.bot.restrict_chat_member.reset_mock()

        cb = callback(
            TRADER_CHAT_ID,
            target,
            encode_callback(old.id, target.id, old.expected_answer),
        )
        await on_callback(cb)

        self.assertEqual(storage.rows[old.id].status, ChallengeStatus.CANCELLED)
        self.assertEqual(storage.rows[new.id].status, ChallengeStatus.PENDING)
        app_obj.bot.restrict_chat_member.assert_not_awaited()

    async def test_wrong_answer_never_restores_permissions(self):
        storage, service = self.make_service()
        target = user()
        app_obj = app()
        on_join, _, on_callback = self.register(app_obj, service)
        await on_join(join_event(TRADER_CHAT_ID, target))
        challenge = next(iter(storage.rows.values()))
        app_obj.bot.restrict_chat_member.reset_mock()

        cb = callback(
            TRADER_CHAT_ID,
            target,
            encode_callback(challenge.id, target.id, challenge.expected_answer + 100),
        )
        await on_callback(cb)

        self.assertEqual(storage.rows[challenge.id].status, ChallengeStatus.PENDING)
        self.assertEqual(storage.rows[challenge.id].attempts, 1)
        app_obj.bot.restrict_chat_member.assert_not_awaited()
        cb.answer.assert_awaited_once_with("❌ Неверно", show_alert=True)

    async def test_permission_failure_leaves_verified_and_does_not_finalize(self):
        record = ChallengeRecord(
            id=9,
            chat_id=TRADER_CHAT_ID,
            user_id=77,
            expected_answer=12,
            attempts=0,
            status=ChallengeStatus.VERIFIED,
            created_at=1,
            expires_at=9999,
            verified_at=2,
        )
        service = SimpleNamespace(
            is_protected_chat=lambda chat_id: True,
            begin_join=AsyncMock(),
            cancel_leave=AsyncMock(),
            attach_message_id=AsyncMock(),
            answer=AsyncMock(return_value=AnswerResult(AnswerKind.VERIFIED, record)),
            finalize_pass=AsyncMock(),
        )
        app_obj = app()
        app_obj.bot.restrict_chat_member.side_effect = RuntimeError("Telegram unavailable")
        _, _, on_callback = self.register(app_obj, service)
        cb = callback(TRADER_CHAT_ID, user(), encode_callback(9, 77, 12))

        await on_callback(cb)

        service.finalize_pass.assert_not_awaited()
        cb.message.delete.assert_not_awaited()
        cb.answer.assert_awaited_once()
        self.assertTrue(cb.answer.await_args.kwargs["show_alert"])

    async def test_retry_from_verified_restores_access_then_finalizes(self):
        verified = ChallengeRecord(
            id=9,
            chat_id=TRADER_CHAT_ID,
            user_id=77,
            expected_answer=12,
            attempts=0,
            status=ChallengeStatus.VERIFIED,
            created_at=1,
            expires_at=9999,
            verified_at=2,
        )
        passed = replace(verified, status=ChallengeStatus.PASSED, completed_at=3)
        service = SimpleNamespace(
            is_protected_chat=lambda chat_id: True,
            begin_join=AsyncMock(),
            cancel_leave=AsyncMock(),
            attach_message_id=AsyncMock(),
            answer=AsyncMock(return_value=AnswerResult(AnswerKind.ALREADY_VERIFIED, verified)),
            finalize_pass=AsyncMock(return_value=passed),
        )
        app_obj = app()
        _, _, on_callback = self.register(app_obj, service)
        cb = callback(TRADER_CHAT_ID, user(), encode_callback(9, 77, 12))

        await on_callback(cb)

        app_obj.bot.restrict_chat_member.assert_awaited_once()
        restored = app_obj.bot.restrict_chat_member.await_args.args[2]
        self.assertTrue(restored.can_send_messages)
        service.finalize_pass.assert_awaited_once_with(
            challenge_id=9,
            chat_id=TRADER_CHAT_ID,
            user_id=77,
        )
        cb.message.delete.assert_awaited_once()
        cb.answer.assert_awaited_once_with("Испытание пройдено")

    async def test_leave_cancels_only_exact_chat_user(self):
        storage, service = self.make_service()
        target = user()
        app_obj = app()
        on_join, on_leave, _ = self.register(app_obj, service)
        await on_join(join_event(WRITERS_CHAT_ID, target))
        await on_join(join_event(TRADER_CHAT_ID, target))
        writers = next(row for row in storage.rows.values() if row.chat_id == WRITERS_CHAT_ID)
        trader = next(row for row in storage.rows.values() if row.chat_id == TRADER_CHAT_ID)

        await on_leave(leave_event(TRADER_CHAT_ID, target))

        self.assertEqual(storage.rows[trader.id].status, ChallengeStatus.CANCELLED)
        self.assertEqual(storage.rows[writers.id].status, ChallengeStatus.PENDING)

    async def test_writers_join_and_success_keep_rules_link(self):
        storage, service = self.make_service()
        target = user()
        app_obj = app()
        on_join, _, on_callback = self.register(app_obj, service)

        await on_join(join_event(WRITERS_CHAT_ID, target))
        join_text = app_obj.bot.send_message.await_args_list[0].kwargs["text"]
        self.assertIn(WRITERS_RULES_URL, join_text)
        challenge = next(iter(storage.rows.values()))

        cb = callback(
            WRITERS_CHAT_ID,
            target,
            encode_callback(challenge.id, target.id, challenge.expected_answer),
        )
        await on_callback(cb)
        success_text = app_obj.bot.send_message.await_args_list[-1].kwargs["text"]
        self.assertIn(WRITERS_RULES_URL, success_text)


if __name__ == "__main__":
    unittest.main()
