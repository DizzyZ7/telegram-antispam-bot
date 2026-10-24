"""Regression tests for the isolated spam guard."""
import unittest
from unittest.mock import AsyncMock, SimpleNamespace

from scoped_spam_guard import PROTECTED_CHAT_ID, ScopedSpamDeletionMiddleware, is_high_confidence_spam


class SpamGuardTests(unittest.IsolatedAsyncioTestCase):
    def test_patterns(self):
        self.assertTrue(is_high_confidence_spam("/start 579 @g64da6e9b11cebot\n@ga6bb2803656ebot"))
        self.assertTrue(is_high_confidence_spam(("点我搜索 酒店偷拍 限时免费看\n" * 12)))
        self.assertFalse(is_high_confidence_spam("你好！今天怎么样？"))
        self.assertFalse(is_high_confidence_spam("Посмотри @somebot, он полезный"))
        self.assertFalse(is_high_confidence_spam("/start"))
        self.assertFalse(is_high_confidence_spam(None))

    async def test_scoped_deletion(self):
        guard = ScopedSpamDeletionMiddleware()
        handler = AsyncMock(return_value="handled")
        event = SimpleNamespace(chat=SimpleNamespace(id=PROTECTED_CHAT_ID), text="/start 579 @g64da6e9b11cebot", caption=None, message_id=7, delete=AsyncMock())
        self.assertIsNone(await guard(handler, event, {}))
        event.delete.assert_awaited_once()
        handler.assert_not_awaited()

    async def test_other_chat_untouched(self):
        guard = ScopedSpamDeletionMiddleware()
        handler = AsyncMock(return_value="handled")
        event = SimpleNamespace(chat=SimpleNamespace(id=-100999), text="/start 579 @g64da6e9b11cebot", caption=None, message_id=7, delete=AsyncMock())
        self.assertEqual(await guard(handler, event, {}), "handled")
        event.delete.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
