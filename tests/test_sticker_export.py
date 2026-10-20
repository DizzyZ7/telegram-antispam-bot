"""Regression tests for explicit WebM export or opt-in Fosgen pack creation."""

import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sticker_maker.handlers import (
    MAX_PENDING_RESULTS, StickerState, choice_keyboard, handle_sticker_choice,
)


class ChoiceCacheTests(unittest.TestCase):
    def test_choose_buttons_and_token_lifetime(self):
        state = StickerState()
        token = state.remember(11, b"test-webm")
        item = state.get_result(token, 11)
        self.assertIsNotNone(item)
        self.assertEqual(item.webm, b"test-webm")
        self.assertIsNone(state.get_result(token, 22))
        data = [
            button.callback_data
            for row in choice_keyboard(token).inline_keyboard
            for button in row
        ]
        self.assertEqual(data, [f"sticker:file:{token}", f"sticker:pack:{token}"])
        item.expires_at = time.monotonic() - 1
        self.assertIsNone(state.get_result(token, 11))

    def test_memory_bounded_and_latest_per_user(self):
        state = StickerState()
        old = state.remember(33, b"a")
        newest = state.remember(33, b"b")
        self.assertIsNone(state.get_result(old, 33))
        self.assertEqual(state.get_result(newest, 33).webm, b"b")
        for i in range(100, 100 + MAX_PENDING_RESULTS + 4):
            state.remember(i, b"x")
        self.assertLessEqual(len(state.results), MAX_PENDING_RESULTS)


class ChoiceHandlerTests(unittest.IsolatedAsyncioTestCase):
    def make_callback(self, action="file", *, user=123, private=True):
        bot = SimpleNamespace(
            send_document=AsyncMock(), send_sticker=AsyncMock(),
            send_message=AsyncMock(), get_me=AsyncMock(),
        )
        state = StickerState()
        token = state.remember(123, b"sample-webm")
        callback = SimpleNamespace(
            data=f"sticker:{action}:{token}",
            from_user=SimpleNamespace(id=user, is_bot=False),
            message=SimpleNamespace(chat=SimpleNamespace(type="private" if private else "supergroup")),
            bot=bot,
            answer=AsyncMock(),
        )
        return state, bot, callback

    async def test_export_as_document_does_not_create_pack(self):
        state, bot, callback = self.make_callback("file")
        with patch("sticker_maker.handlers.save_to_personal_pack", new_callable=AsyncMock) as add:
            await handle_sticker_choice(callback, state)
            add.assert_not_awaited()
            bot.send_sticker.assert_not_awaited()
        bot.send_document.assert_awaited_once()
        self.assertEqual(bot.send_document.await_args.kwargs["document"].filename, "fosgen_sticker.webm")
        self.assertIn("/addsticker", bot.send_document.await_args.kwargs["caption"])
        await handle_sticker_choice(callback, state)
        bot.send_document.assert_awaited_once()

    async def test_add_to_pack_only_when_clicked(self):
        state, bot, callback = self.make_callback("pack")
        with patch(
            "sticker_maker.handlers.save_to_personal_pack",
            new_callable=AsyncMock, return_value="fosgen_123_by_testbot",
        ) as add:
            await handle_sticker_choice(callback, state)
            await handle_sticker_choice(callback, state)
            add.assert_awaited_once()
        self.assertEqual(bot.send_sticker.await_count, 2)
        self.assertEqual(bot.send_sticker.await_args.kwargs["chat_id"], 123)

    async def test_forbidden_other_user(self):
        state, bot, callback = self.make_callback("pack", user=456)
        with patch("sticker_maker.handlers.save_to_personal_pack", new_callable=AsyncMock) as add:
            await handle_sticker_choice(callback, state)
            add.assert_not_awaited()
        bot.send_document.assert_not_awaited()
        bot.send_sticker.assert_not_awaited()
        self.assertTrue(callback.answer.await_args.kwargs["show_alert"])

    async def test_forbidden_group(self):
        state, bot, callback = self.make_callback("pack", private=False)
        await handle_sticker_choice(callback, state)
        bot.send_sticker.assert_not_awaited()
        bot.send_document.assert_not_awaited()

    async def test_invalid_action_rejected(self):
        state, bot, callback = self.make_callback("delete")
        await handle_sticker_choice(callback, state)
        bot.send_sticker.assert_not_awaited()
        bot.send_document.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
