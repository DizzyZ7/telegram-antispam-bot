"""Scoped, conservative anti-advertising guard for IKF channel comments.

Does not classify literary discussion, sexuality, genres or media by itself.
Does not download or retain attachments.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message

LOGGER = logging.getLogger(__name__)
COMMENTS_CHAT_ID = -1002576898711
URL_RE = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/(?:[A-Za-z0-9_+/-]+)", re.I)
BOT_URL_RE = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/(?:[A-Za-z0-9_]{5,32}bot)(?:[/?#]|$)", re.I)
BOT_MENTION_RE = re.compile(r"(?<![\w])@[A-Za-z0-9_]{4,29}bot\b", re.I)
EARN_RE = re.compile(r"(?:заработ|доход|инвест|прибыл|выплат|крипт|подработ)", re.I)
CTA_RE = re.compile(r"(?:запусти|запустить|переходи|переходите|подпишись|жми|нажми|бота)", re.I)
LATIN_LOOKALIKES = str.maketrans({
    "a": "а", "b": "в", "c": "с", "e": "е", "h": "н", "k": "к",
    "m": "м", "o": "о", "p": "р", "t": "т", "x": "х", "y": "у",
    "0": "о", "3": "з",
})


def message_links(message: Message) -> list[str]:
    text = message.text or message.caption or ""
    urls = [text]
    for entity in (* (message.entities or ()), * (message.caption_entities or ())):
        if getattr(entity, "type", None) == "text_link" and getattr(entity, "url", None):
            urls.append(entity.url)
    markup = getattr(message, "reply_markup", None)
    if markup:
        for row in getattr(markup, "inline_keyboard", ()):
            for button in row:
                if getattr(button, "url", None):
                    urls.append(button.url)
    return urls


def has_telegram_bot_link(message: Message) -> bool:
    return any(BOT_URL_RE.search(value) or BOT_MENTION_RE.search(value) for value in message_links(message))


def normalize_ad_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = normalized.translate(LATIN_LOOKALIKES)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return normalized


def is_advertising(message: Message) -> bool:
    if has_telegram_bot_link(message):
        return True
    text = message.text or message.caption or ""
    if not text:
        return False
    normalized = normalize_ad_text(text)
    repeated_cta = len(re.findall(r"запуст", normalized)) >= 2
    if repeated_cta and "бот" in normalized:
        return True
    if URL_RE.search(text) and EARN_RE.search(normalized) and CTA_RE.search(normalized):
        return True
    return False


class IKFCommentsGuard(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or event.chat.id != COMMENTS_CHAT_ID:
            return await handler(event, data)
        if event.from_user is None or event.from_user.is_bot or event.sender_chat is not None:
            return await handler(event, data)
        if not is_advertising(event):
            return await handler(event, data)
        try:
            member = await event.bot.get_chat_member(event.chat.id, event.from_user.id)
            if member.status in {"administrator", "creator"}:
                return await handler(event, data)
            await event.bot.delete_message(event.chat.id, event.message_id)
            LOGGER.info("IKF_COMMENT_AD_REMOVED chat_id=%s message_id=%s", event.chat.id, event.message_id)
            return None
        except Exception:
            LOGGER.exception("IKF_COMMENT_AD_DELETE_FAILED chat_id=%s message_id=%s", event.chat.id, event.message_id)
            return await handler(event, data)


def register_ikf_comments_guard(app: Any) -> None:
    app.dp.message.outer_middleware(IKFCommentsGuard())
    LOGGER.info("IKF_COMMENTS_GUARD_READY chat_id=%s mode=conservative", COMMENTS_CHAT_ID)
