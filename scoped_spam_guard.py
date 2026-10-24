"""Silent, chat-scoped deletion of high-confidence bot-promotion spam."""
from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any, Awaitable, Callable

from aiogram.types import Message

PROTECTED_CHAT_ID = -1002003254090
BOT_HANDLE = re.compile(r"(?i)@[a-z][a-z0-9_]{4,31}bot\b")
START_PAYLOAD = re.compile(r"(?i)^\s*/start(?:@[a-z][a-z0-9_]{4,31})?\s+\S+")
PROMO_MARKERS = ("点我搜索", "限时免费看", "酒店偷拍", "万能检索", "付费门槛免费看", "实时直播")


def is_high_confidence_spam(text: str | None) -> bool:
    if not text:
        return False
    handles = BOT_HANDLE.findall(text)
    if START_PAYLOAD.search(text) and handles:
        return True
    if len(handles) >= 3 and len(set(h.lower() for h in handles)) >= 2:
        return True
    markers = sum(marker in text for marker in PROMO_MARKERS)
    if markers >= 2 and len(text) >= 100:
        return True
    lines = [line.strip() for line in text.splitlines() if len(line.strip()) >= 8]
    repeated = any(count >= 3 for count in Counter(lines).values())
    return markers >= 1 and repeated and len(text) >= 150


class ScopedSpamDeletionMiddleware:
    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        if getattr(getattr(event, "chat", None), "id", None) != PROTECTED_CHAT_ID:
            return await handler(event, data)
        text = getattr(event, "text", None) or getattr(event, "caption", None)
        if not is_high_confidence_spam(text):
            return await handler(event, data)
        try:
            await event.delete()
            logging.info("SCOPED_SPAM_DELETED chat_id=%s message_id=%s", PROTECTED_CHAT_ID, event.message_id)
        except Exception:
            logging.exception("SCOPED_SPAM_DELETE_FAILED chat_id=%s message_id=%s", PROTECTED_CHAT_ID, event.message_id)
        return None


async def report_scoped_spam_permission(bot: Any) -> None:
    try:
        member = await bot.get_chat_member(PROTECTED_CHAT_ID, bot.id)
        status = getattr(member, "status", None)
        can_delete = status == "creator" or (
            status == "administrator" and bool(getattr(member, "can_delete_messages", False))
        )
        logging.info("SCOPED_SPAM_GUARD chat_id=%s can_delete=%s", PROTECTED_CHAT_ID, can_delete)
        if not can_delete:
            logging.warning("SCOPED_SPAM_GUARD_REQUIRES_DELETE_PERMISSION chat_id=%s", PROTECTED_CHAT_ID)
    except Exception:
        logging.exception("SCOPED_SPAM_GUARD_PERMISSION_CHECK_FAILED chat_id=%s", PROTECTED_CHAT_ID)
