"""Collect *observed* community membership changes, never scrape subscribers.

Telegram Bot API cannot list all channel subscribers. If Fosgen is an admin
and polling includes chat_member, Telegram sends future changes of membership.
The status table is informational; the final submission always checks live.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from aiogram.types import ChatMemberUpdated

LOGGER = logging.getLogger(__name__)


def membership_state(member: Any) -> tuple[str, bool]:
    raw = getattr(member, "status", "")
    status = str(getattr(raw, "value", raw)).casefold()
    if status == "restricted":
        return status, getattr(member, "is_member", False) is True
    return status, status in {"member", "administrator", "creator", "owner"}


def register_community_membership_handler(
    app: Any, storage: Any, membership_chat_id: int
) -> None:
    observer = getattr(getattr(app, "dp", None), "chat_member", None)
    if observer is None:
        # Minimal test app fixtures may not instantiate an aiogram Dispatcher.
        return

    @observer()
    async def observed_community_member(update: ChatMemberUpdated) -> None:
        if int(update.chat.id) != int(membership_chat_id):
            return
        member = update.new_chat_member
        subject = getattr(member, "user", None)
        if subject is None or bool(getattr(subject, "is_bot", False)):
            return
        status, is_member = membership_state(member)
        occurred = getattr(update, "date", None)
        now = int(occurred.timestamp()) if occurred is not None else int(time.time())
        try:
            await storage.record_community_member(
                chat_id=int(update.chat.id),
                user_id=int(subject.id),
                status=status,
                is_member=is_member,
                source="chat_member_update",
                now=now,
            )
        except Exception:
            LOGGER.exception(
                "WRITERS_MEMBER_UPDATE_STORE_FAILED chat_id=%s",
                membership_chat_id,
            )


__all__ = ["membership_state", "register_community_membership_handler"]
