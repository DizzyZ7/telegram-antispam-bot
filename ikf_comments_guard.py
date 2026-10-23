"""Scoped, conservative anti-advertising guard for IKF channel comments.

Does not classify literary discussion, sexuality, genres or media by itself.
Does not download or retain attachments.
"""
from __future__ import annotations

import logging
import time
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


CJK_RE = re.compile(r"[\\u3400-\\u9fff\\u3040-\\u30ff\\uac00-\\ud7af]")
CYRILLIC_RE = re.compile(r"[а-яё]", re.I)
EXTERNAL_LINK_RE = re.compile(r"(?:https?://|www\\.|t\\.me/|telegram\\.me/)", re.I)


def is_long_cjk_message(message: Message) -> bool:
    text = message.text or message.caption or ""
    if len(text) < 45:
        return False
    letters = [char for char in text if char.isalpha()]
    if len(letters) < 30:
        return False
    cjk_count = sum(bool(CJK_RE.fullmatch(char)) for char in letters)
    # Mixed Russian discussions of names, titles and quotations remain allowed.
    return cjk_count >= 30 and cjk_count / len(letters) >= 0.80 and not CYRILLIC_RE.search(text)


def canonical_message(message: Message) -> str:
    text = message.text or message.caption or ""
    return " ".join(normalize_ad_text(text).split()).strip()


def contains_external_link(message: Message) -> bool:
    return any(EXTERNAL_LINK_RE.search(value) for value in message_links(message))


class IKFCommentsGuard(BaseMiddleware):
    def __init__(self) -> None:
        self.last_messages: dict[int, tuple[str, float]] = {}

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
        user_id = event.from_user.id
        text = canonical_message(event)
        now = time.monotonic()
        previous = self.last_messages.get(user_id)
        # Keep bounded, short-lived metadata only; no copies of media or URLs in logs.
        if len(self.last_messages) > 2000:
            self.last_messages = {
                uid: item for uid, item in self.last_messages.items()
                if now - item[1] < 900
            }
        duplicate = bool(text and len(text) >= 12 and previous
                         and previous[0] == text and now - previous[1] < 900)
        if text:
            self.last_messages[user_id] = (text, now)

        ad = is_advertising(event)
        cjk = is_long_cjk_message(event)
        if not (ad or duplicate or cjk):
            return await handler(event, data)
        try:
            member = await event.bot.get_chat_member(event.chat.id, user_id)
            if member.status in {"administrator", "creator"}:
                return await handler(event, data)
            await event.bot.delete_message(event.chat.id, event.message_id)
            if ad or duplicate:
                # Telegram requires ban permissions separately from delete permissions.
                await event.bot.ban_chat_member(event.chat.id, user_id, revoke_messages=False)
                LOGGER.info("IKF_COMMENT_SPAM_BANNED chat_id=%s user_id=%s", event.chat.id, user_id)
            elif cjk:
                await event.bot.send_message(
                    event.chat.id,
                    "Пожалуйста, общайтесь в комментариях на русском языке. "
                    "Названия, имена и короткие цитаты на других языках разрешены.",
                    disable_notification=True,
                )
                LOGGER.info("IKF_COMMENT_LANGUAGE_REMOVED chat_id=%s message_id=%s", event.chat.id, event.message_id)
            return None
        except Exception:
            LOGGER.exception("IKF_COMMENT_MODERATION_FAILED chat_id=%s message_id=%s", event.chat.id, event.message_id)
            return None


def register_ikf_comments_guard(app: Any) -> None:
    app.dp.message.outer_middleware(IKFCommentsGuard())
    LOGGER.info("IKF_COMMENTS_GUARD_READY chat_id=%s mode=conservative", COMMENTS_CHAT_ID)
