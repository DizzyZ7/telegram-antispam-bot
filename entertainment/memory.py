"""Telegram message classification for Entertainment Culture Memory."""

from __future__ import annotations

import re
from typing import Any

from aiogram.types import Message
from writers_moderation import contains_prohibited_language

from .models import MemoryEvent, MemoryEventType

_EMOJI_RE = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "]"
)
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]", re.UNICODE)
_COMMAND_RE = re.compile(r"^/[A-Za-z0-9_]+(?:@[A-Za-z0-9_]+)?(?:\s|$)")


def _value(obj: object, name: str, default: Any = None) -> Any:
    return getattr(obj, name, default)


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _is_prohibited_text(value: object) -> bool:
    cleaned = _clean_text(value)
    return bool(cleaned and contains_prohibited_language(cleaned))


def _is_emoji_only(text: str) -> bool:
    if not _EMOJI_RE.search(text):
        return False
    remainder = _EMOJI_RE.sub("", text)
    remainder = re.sub(r"[\s\u200d\ufe0f]", "", remainder)
    return not _WORD_RE.search(remainder)


def _is_command_text(text: str) -> bool:
    return bool(_COMMAND_RE.match(text.lstrip()))


def _reply_id(message: Message) -> int | None:
    reply = _value(message, "reply_to_message")
    value = _value(reply, "message_id") if reply is not None else None
    return int(value) if value is not None else None


def _is_forwarded(message: Message) -> bool:
    return any(
        _value(message, name) is not None
        for name in ("forward_origin", "forward_date", "forward_from", "forward_sender_name")
    ) or bool(_value(message, "is_automatic_forward", False))


def _base_event(
    message: Message,
    *,
    chat_id: int,
    topic_id: int,
    created_at: int,
    event_type: MemoryEventType,
    **kwargs: object,
) -> MemoryEvent | None:
    from_user = _value(message, "from_user")
    user_id = _value(from_user, "id") if from_user is not None else None
    if user_id is None:
        return None
    message_id = _value(message, "message_id")
    return MemoryEvent(
        id=None,
        chat_id=int(chat_id),
        topic_id=int(topic_id),
        message_id=int(message_id) if message_id is not None else None,
        user_id=int(user_id),
        event_type=event_type,
        created_at=int(created_at),
        reply_to_message_id=_reply_id(message),
        is_forwarded=_is_forwarded(message),
        sender_is_bot=bool(_value(from_user, "is_bot", False)),
        **kwargs,
    )


def classify_memory_event(
    message: Message,
    *,
    chat_id: int,
    topic_id: int,
    created_at: int,
) -> MemoryEvent | None:
    """Convert one supported Telegram message into a canonical event.

    The classifier is intentionally metadata-only for media: it never downloads
    files and never copies surrounding chat text into another event. Prohibited
    text/captions are rejected before they can enter Culture Memory.
    """

    sticker = _value(message, "sticker")
    if sticker is not None:
        file_id = _clean_text(_value(sticker, "file_id"))
        if file_id is None:
            return None
        return _base_event(
            message,
            chat_id=chat_id,
            topic_id=topic_id,
            created_at=created_at,
            event_type=MemoryEventType.STICKER,
            file_id=file_id,
            file_unique_id=_clean_text(_value(sticker, "file_unique_id")),
            sticker_emoji=_clean_text(_value(sticker, "emoji")),
            sticker_set_name=_clean_text(_value(sticker, "set_name")),
            media_width=_value(sticker, "width"),
            media_height=_value(sticker, "height"),
            metadata={
                "animated": bool(_value(sticker, "is_animated", False)),
                "video": bool(_value(sticker, "is_video", False)),
            },
        )

    photos = _value(message, "photo")
    if photos:
        if _is_prohibited_text(_value(message, "caption")):
            return None
        valid = [item for item in photos if _clean_text(_value(item, "file_id"))]
        if not valid:
            return None
        photo = max(
            valid,
            key=lambda item: (
                int(_value(item, "file_size", 0) or 0),
                int(_value(item, "width", 0) or 0) * int(_value(item, "height", 0) or 0),
            ),
        )
        return _base_event(
            message,
            chat_id=chat_id,
            topic_id=topic_id,
            created_at=created_at,
            event_type=MemoryEventType.PHOTO,
            caption=_clean_text(_value(message, "caption")),
            file_id=_clean_text(_value(photo, "file_id")),
            file_unique_id=_clean_text(_value(photo, "file_unique_id")),
            media_width=_value(photo, "width"),
            media_height=_value(photo, "height"),
        )

    animation = _value(message, "animation")
    if animation is not None:
        if _is_prohibited_text(_value(message, "caption")):
            return None
        file_id = _clean_text(_value(animation, "file_id"))
        if file_id is None:
            return None
        return _base_event(
            message,
            chat_id=chat_id,
            topic_id=topic_id,
            created_at=created_at,
            event_type=MemoryEventType.ANIMATION,
            caption=_clean_text(_value(message, "caption")),
            file_id=file_id,
            file_unique_id=_clean_text(_value(animation, "file_unique_id")),
            media_width=_value(animation, "width"),
            media_height=_value(animation, "height"),
            media_duration=_value(animation, "duration"),
        )

    text = _clean_text(_value(message, "text"))
    if text is None or contains_prohibited_language(text):
        return None
    event_type = MemoryEventType.EMOJI if _is_emoji_only(text) else MemoryEventType.TEXT
    return _base_event(
        message,
        chat_id=chat_id,
        topic_id=topic_id,
        created_at=created_at,
        event_type=event_type,
        text=text,
        is_command=_is_command_text(text),
    )


__all__ = ["classify_memory_event"]
