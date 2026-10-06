from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram import Dispatcher

from tests.test_zero_trust_handlers import TRADER_CHAT_ID, callback, user
from zero_trust.models import ChallengeRecord, ChallengeStatus
from zero_trust.presentation import encode_callback
from zero_trust.service import AnswerKind, AnswerResult


class ZeroTrustFinalizeCompensationTests(unittest.IsolatedAsyncioTestCase):
    async def test_finalize_failure_restricts_user_again_and_keeps_challenge(self):
        from zero_trust.handlers import register_zero_trust_handlers

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
        service = SimpleNamespace(
            is_protected_chat=lambda chat_id: True,
            begin_join=AsyncMock(),
            cancel_leave=AsyncMock(),
            attach_message_id=AsyncMock(),
            answer=AsyncMock(return_value=AnswerResult(AnswerKind.VERIFIED, verified)),
            finalize_pass=AsyncMock(side_effect=RuntimeError("database unavailable")),
        )
        bot = SimpleNamespace(
            restrict_chat_member=AsyncMock(),
            send_message=AsyncMock(),
        )
        app = SimpleNamespace(
            dp=Dispatcher(),
            bot=bot,
            safe_output_text=lambda value: value,
            user_tag=lambda value: f"@{value.username}",
        )
        register_zero_trust_handlers(app, service)
        handler = next(
            item.callback
            for item in app.dp.callback_query.handlers
            if item.callback.__name__ == "zero_trust_callback"
        )
        cb = callback(TRADER_CHAT_ID, user(), encode_callback(9, 77, 12))

        await handler(cb)

        self.assertEqual(bot.restrict_chat_member.await_count, 2)
        restored = bot.restrict_chat_member.await_args_list[0].args[2]
        compensated = bot.restrict_chat_member.await_args_list[1].args[2]
        self.assertTrue(restored.can_send_messages)
        self.assertFalse(compensated.can_send_messages)
        cb.message.delete.assert_not_awaited()
        bot.send_message.assert_not_awaited()
        cb.answer.assert_awaited_once()
        self.assertTrue(cb.answer.await_args.kwargs["show_alert"])
        self.assertIn("огранич", cb.answer.await_args.args[0].lower())


if __name__ == "__main__":
    unittest.main()
