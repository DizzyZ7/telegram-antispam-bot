"""Explicit one-off birthday plans, independent of random entertainment replies."""

from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from time import time as wall_time
from typing import Any, Awaitable, Callable, Iterable
from zoneinfo import ZoneInfo

from aiogram.exceptions import (
    TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError,
    TelegramNotFound, TelegramRetryAfter, TelegramUnauthorizedError,
)

from .celebrations import celebration_kind
from .birthday_texts import birthday_greeting

LOGGER = logging.getLogger(__name__)
MOSCOW = ZoneInfo("Europe/Moscow")
_USERNAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{4,31}\Z")
_MENTIONS = re.compile(r"(?<![A-Za-z0-9_])@([A-Za-z][A-Za-z0-9_]{4,31})(?![A-Za-z0-9_])")


@dataclass(frozen=True)
class BirthdayPlan:
    event_id: str
    chat_id: int
    username: str
    local_date: str
    send_time: str
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, str) or not re.fullmatch(r"[a-z0-9_-]{1,80}", self.event_id):
            raise ValueError("Invalid birthday event ID")
        if type(self.chat_id) is not int or self.chat_id >= 0:
            raise ValueError("Birthday plan must address a group")
        if not isinstance(self.username, str) or not _USERNAME.fullmatch(self.username):
            raise ValueError("Invalid birthday recipient username")
        date.fromisoformat(self.local_date)
        time.fromisoformat(self.send_time)
        if (not isinstance(self.text, str) or not 1 <= len(self.text) <= 3500
                or len(self.text.encode("utf-16-le")) // 2 > 4000):
            raise ValueError("Invalid birthday greeting")

    def same_day(self, now: int) -> bool:
        return datetime.fromtimestamp(now, MOSCOW).date().isoformat() == self.local_date

    def due(self, now: int) -> bool:
        local = datetime.fromtimestamp(now, MOSCOW)
        return local.date().isoformat() == self.local_date and local.time() >= time.fromisoformat(self.send_time)


def load_birthday_plans(path: Path | None = None) -> tuple[BirthdayPlan, ...]:
    # Keep shipped plans inside the package: /app/data may be a persistent
    # runtime volume that hides files from the newly built container image.
    source = path or Path(__file__).with_name("birthday_plans.json")
    try:
        raw = source.read_text(encoding="utf-8")
        if len(raw) > 128_000:
            raise ValueError("Birthday plan file is too large")
        entries = json.loads(raw)
        if not isinstance(entries, list) or len(entries) > 20:
            raise ValueError("Invalid birthday plan list")
        plans = tuple(BirthdayPlan(**entry) for entry in entries)
        if len({(p.chat_id, p.event_id) for p in plans}) != len(plans):
            raise ValueError("Duplicate birthday plan ID")
        return plans
    except (OSError, TypeError, ValueError):
        LOGGER.exception("BIRTHDAY_PLANS_LOAD_FAILED")
        return ()


class BirthdayCoordinator:
    def __init__(self, bot: Any, storage: Any, chat_ids: Iterable[int], plans: Iterable[BirthdayPlan], *,
                 now_fn: Callable[[], float] = wall_time,
                 generic_allowed: Callable[[int, int], Awaitable[bool]] | None = None) -> None:
        self.bot = bot
        self.storage = storage
        self._now_fn = now_fn
        self._generic_allowed = generic_allowed
        allowed = frozenset(int(chat) for chat in chat_ids)
        self.chat_ids = allowed
        self.plans = tuple(p for p in plans if p.chat_id in allowed)

    async def _send(self, plan: BirthdayPlan, now: int, topic_id: int = 0) -> None:
        await self._deliver(plan.chat_id, plan.event_id, plan.text, now, topic_id)

    async def _deliver(self, chat_id: int, event_id: str, text: str, now: int, topic_id: int = 0) -> bool:
        if not await self.storage.claim_birthday_delivery(chat_id, event_id, now):
            return True
        current = int(self._now_fn())
        if datetime.fromtimestamp(current, MOSCOW).date() != datetime.fromtimestamp(now, MOSCOW).date():
            # A claim may have waited on another DB transaction across midnight.
            # Do not deliver an expired plan or spend the previous day's row now.
            await self.storage.release_birthday_delivery(chat_id, event_id)
            return False
        kwargs: dict[str, Any] = {"chat_id": chat_id, "text": html.escape(text)}
        if topic_id:
            kwargs["message_thread_id"] = topic_id
        try:
            sent = await self.bot.send_message(**kwargs)
        except TelegramNetworkError:
            await self.storage.finish_birthday_delivery(chat_id, event_id, int(self._now_fn()), "UNCERTAIN", None)
            LOGGER.error("BIRTHDAY_DELIVERY_UNCERTAIN event=%s", event_id)
            return True
        except (TelegramBadRequest, TelegramForbiddenError, TelegramNotFound,
                TelegramRetryAfter, TelegramUnauthorizedError):
            # An explicit Bot API rejection confirms there was no delivery.
            await self.storage.release_birthday_delivery(chat_id, event_id)
            LOGGER.warning("BIRTHDAY_DELIVERY_REJECTED event=%s", event_id)
            return False
        except Exception:
            # A lost response may follow a successful send. Keep the claim and
            # require inspection, rather than posting a second congratulations.
            await self.storage.finish_birthday_delivery(chat_id, event_id, int(self._now_fn()), "UNCERTAIN", None)
            LOGGER.exception("BIRTHDAY_DELIVERY_UNCERTAIN event=%s", event_id)
            return True
        sent_at = getattr(sent, "date", None)
        delivered_at = int(sent_at.timestamp()) if isinstance(sent_at, datetime) and sent_at.tzinfo else int(self._now_fn())
        await self.storage.finish_birthday_delivery(chat_id, event_id, delivered_at, "SENT", sent.message_id)
        LOGGER.info("BIRTHDAY_DELIVERY_SENT event=%s message_id=%s", event_id, sent.message_id)
        return True

    async def tick(self, now: int) -> None:
        for plan in self.plans:
            if plan.due(now):
                try:
                    await self._send(plan, now)
                except Exception:
                    LOGGER.exception("BIRTHDAY_DELIVERY_STORAGE_FAILED event=%s", plan.event_id)

    @staticmethod
    def _addresses(message: Any, plan: BirthdayPlan) -> bool:
        actor = getattr(message, "from_user", None)
        if actor is None or getattr(actor, "is_bot", False):
            return False
        if str(getattr(actor, "username", "") or "").casefold() == plan.username.casefold():
            return False
        text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
        target = plan.username.casefold()
        reply = getattr(message, "reply_to_message", None)
        reply_user = getattr(reply, "from_user", None)
        replies_to_target = str(getattr(reply_user, "username", "") or "").casefold() == target
        mentioned = {name.casefold() for name in _MENTIONS.findall(text)}
        entities = tuple(getattr(message, "entities", ()) or ()) + tuple(getattr(message, "caption_entities", ()) or ())
        for entity in entities:
            user = getattr(entity, "user", None)
            if user is None:
                continue
            username = getattr(user, "username", None)
            if username:
                mentioned.add(username.casefold())
            elif replies_to_target and getattr(user, "id", None) == getattr(reply_user, "id", None):
                mentioned.add(target)
            else:
                # An unresolved text-mention is another explicit recipient;
                # it cannot be silently ignored just because it lacks a handle.
                mentioned.add("unresolved-recipient")
        if mentioned - {target}:
            return False
        return replies_to_target or mentioned == {target}

    @staticmethod
    def _source_time(message: Any, now: int) -> int | None:
        value = getattr(message, "date", None)
        if not isinstance(value, datetime) or value.tzinfo is None:
            return None
        source = int(value.timestamp())
        if source > now + 60 or value.astimezone(MOSCOW).date() != datetime.fromtimestamp(now, MOSCOW).date():
            return None
        return source

    async def _generic_birthday(self, message: Any, now: int, source: int) -> None:
        if now - source > 600:
            return
        chat_id = int(message.chat.id)
        local_day = datetime.fromtimestamp(now, MOSCOW).date()
        key = (chat_id, local_day.isoformat())
        if self._generic_allowed is not None and not await self._generic_allowed(chat_id, now):
            return
        await self._deliver(chat_id, f"chat-birthday-{key[1]}", birthday_greeting(chat_id, local_day), now,
                            int(getattr(message, "message_thread_id", None) or 0))

    async def observe(self, message: Any, now: int) -> bool:
        chat = getattr(message, "chat", None)
        if getattr(chat, "id", None) not in self.chat_ids:
            return False
        if str(getattr(getattr(chat, "type", ""), "value", getattr(chat, "type", ""))) not in {"group", "supergroup"}:
            return False
        actor = getattr(message, "from_user", None)
        if actor is None or getattr(actor, "is_bot", False) or getattr(actor, "id", None) is None:
            return False
        if (any(getattr(message, field, None) is not None for field in
                ("forward_origin", "forward_date", "forward_from", "forward_sender_name"))
                or getattr(message, "is_automatic_forward", False)):
            return False
        text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
        if celebration_kind(text[:500]) != "birthday":
            return False
        source = self._source_time(message, now)
        if source is None:
            return False
        active_plans = [p for p in self.plans if p.chat_id == chat.id and p.same_day(now)]
        for plan in active_plans:
            if not self._addresses(message, plan):
                continue
            try:
                await self._send(plan, now, int(getattr(message, "message_thread_id", None) or 0))
            except Exception:
                LOGGER.exception("BIRTHDAY_DELIVERY_STORAGE_FAILED event=%s", plan.event_id)
            # Also suppress generic celebration after a duplicate or failed
            # targeted attempt; the deadline path can retry a definite failure.
            return True
        # Reserve this day's sole slot for the administrator-approved plan.
        if active_plans:
            return True
        try:
            await self._generic_birthday(message, now, source)
        except Exception:
            LOGGER.exception("BIRTHDAY_PARTY_STORAGE_FAILED chat_id=%s", chat.id)
        # A recognized birthday is handled even when the daily slot is already
        # consumed. No hourly fallback or duplicate from a different topic.
        return True
