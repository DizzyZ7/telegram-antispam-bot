from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram import Dispatcher

from writers_moderation import register_writers_chat_handlers


def module():
    return SimpleNamespace(
        dp=Dispatcher(),
        ALLOWED_CHATS=[],
        bot=SimpleNamespace(send_message=AsyncMock()),
    )


def message(*, delete_side_effect=None):
    delete = AsyncMock()
    if delete_side_effect is not None:
        delete.side_effect = delete_side_effect
    return SimpleNamespace(
        chat=SimpleNamespace(id=-1002619489118),
        from_user=SimpleNamespace(id=77),
        message_id=555,
        message_thread_id=10,
        delete=delete,
    )


class WritersModerationMemoryPurgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_moderation_delete_invokes_memory_purge_hook(self):
        app = module()
        purge = AsyncMock(return_value=2)
        register_writers_chat_handlers(app, on_message_deleted=purge)
        handler = app.dp.message.handlers[0].callback
        target = message()

        await handler(target)

        target.delete.assert_awaited_once()
        purge.assert_awaited_once_with(-1002619489118, 555)

    async def test_failed_telegram_delete_does_not_claim_or_purge_memory(self):
        app = module()
        purge = AsyncMock(return_value=2)
        register_writers_chat_handlers(app, on_message_deleted=purge)
        handler = app.dp.message.handlers[0].callback
        target = message(delete_side_effect=RuntimeError("no rights"))

        await handler(target)

        target.delete.assert_awaited_once()
        purge.assert_not_awaited()
        app.bot.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
