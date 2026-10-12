"""Direct multi-turn Fosgen dialogue layered above the production safety service.

No external model, API, token budget or user-text replay. This layer only
handles human replies to *recent verified entertainment text* from this bot,
not other bot commands, unrelated chat or arbitrary forwarded bot messages.
"""
from __future__ import annotations

import asyncio
import html
import logging
import random
from dataclasses import replace

from aiogram.types import Message

from .dialogue import (
    DIALOGUE_BURST_LIMIT, DIALOGUE_BURST_SECONDS, DIALOGUE_COOLDOWN_SECONDS,
    MAX_DIALOGUE_TURNS, SESSION_SECONDS, choose_continuation,
)
from .greetings import detect_greeting
from .models import EntertainmentActionRecord, EntertainmentActionType
from .moderation_safety import is_safe_entertainment_reply, is_safe_entertainment_source
from .moderation_safety_service import EntertainmentService as SafetyEntertainmentService

LOGGER = logging.getLogger(__name__)
_MAX_HISTORY = 120


def _normalized(value: str | None) -> str:
    return " ".join(html.unescape(value or "").casefold().split())


class EntertainmentService(SafetyEntertainmentService):
    """Direct-reply chat memory via existing topic-scoped action storage."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._dialogue_locks: dict[tuple[int, int], asyncio.Lock] = {}

    @staticmethod
    def _human_direct_reply(message: Message) -> bool:
        # Parent validates the exact identity of the bot replied to.
        sender = getattr(message, "from_user", None)
        text = getattr(message, "text", None)
        if sender is None or bool(getattr(sender, "is_bot", False)):
            return False
        if not isinstance(text, str) or not text.strip() or text.lstrip().startswith("/"):
            return False
        if getattr(message, "is_automatic_forward", False) or any(
            getattr(message, prop, None) is not None
            for prop in ("forward_origin", "forward_date", "forward_from", "forward_sender_name")
        ):
            return False
        return is_safe_entertainment_source(text)

    @staticmethod
    def _action_matches_reply(action: EntertainmentActionRecord, reply: Message) -> bool:
        meta = action.metadata
        if not isinstance(meta, dict) or meta.get("output_kind") != "text":
            return False
        output = meta.get("output")
        if not isinstance(output, str) or not output:
            return False
        reply_id = getattr(reply, "message_id", None)
        expected_id = meta.get("bot_message_id")
        if expected_id is not None and reply_id is not None:
            return int(expected_id) == int(reply_id) and _normalized(
                getattr(reply, "text", None)
            ) == _normalized(output)
        # Legacy output before bot_message_id was stored. Exact full text from
        # the original bot message is required, never mere keyword similarity.
        return _normalized(getattr(reply, "text", None)) == _normalized(output)

    async def observe_message(self, message: Message) -> None:
        """Make one-token replies conversational without altering memory consent.

        Consented normal messages are observed and evaluated by the parent
        exactly once; opted-out/short messages may reply with a safe authored
        phrase, but are never inserted into learned Culture Memory here.
        """
        is_reply = (
            self._human_direct_reply(message)
            and self._is_direct_trigger(message)
            and detect_greeting(getattr(message, "text", None)) is None
            and self._message_scope_allowed(message)
        )
        regular_observation = self.is_eligible_learning_message(message) if is_reply else False
        consent = True
        if is_reply:
            read_consent = getattr(self.storage, "get_remember_enabled", None)
            if callable(read_consent):
                consent = bool(await read_consent(
                    int(message.chat.id), int(message.from_user.id)
                ))

        await super().observe_message(message)

        if is_reply and (not regular_observation or not consent):
            await self.evaluate_topic(message)

    async def _dialogue_reply(
        self, message: Message, *, chat_id: int, topic_id: int, now: int
    ) -> tuple[bool, EntertainmentActionRecord | None]:
        """Return (matched_entertainment_reply, sent_record_or_none)."""
        replied = getattr(message, "reply_to_message", None)
        if replied is None:
            return False, None
        scope = (chat_id, topic_id)
        lock = self._dialogue_locks.setdefault(scope, asyncio.Lock())
        async with lock:
            history = tuple(
                await self.storage.recent_actions(
                    chat_id, topic_id,
                    since=now - SESSION_SECONDS,
                    limit=_MAX_HISTORY,
                )
            )
            sorted_actions = sorted(
                history,
                key=lambda x: (int(x.created_at), int(x.id or 0)),
                reverse=True,
            )
            parent = next(
                (action for action in sorted_actions
                 if self._action_matches_reply(action, replied)),
                None,
            )
            if parent is None:
                return False, None

            # Matched one of Fosgen's own entertainment messages. Refuse to
            # switch to autonomous generation when the reply is inappropriate.
            message_id = getattr(message, "message_id", None)
            if message_id is not None and any(
                item.metadata.get("source") == "dialogue_v1"
                and item.trigger_message_id == message_id
                for item in history
            ):
                return True, None

            recent_dialogue = [
                item for item in history
                if item.metadata.get("source") == "dialogue_v1"
                and 0 <= now - int(item.created_at) < DIALOGUE_BURST_SECONDS
            ]
            if len(recent_dialogue) >= DIALOGUE_BURST_LIMIT:
                return True, None
            if recent_dialogue and any(
                0 <= now - int(item.created_at) < DIALOGUE_COOLDOWN_SECONDS
                for item in recent_dialogue
            ):
                return True, None

            old_stage = (
                int(parent.metadata.get("dialogue_stage", 0))
                if parent.metadata.get("source") == "dialogue_v1"
                else 0
            )
            if old_stage >= MAX_DIALOGUE_TURNS:
                return True, None

            recent_outputs = [
                str(action.metadata["output"])
                for action in sorted_actions
                if isinstance(action.metadata.get("output"), str)
            ]
            choice = choose_continuation(
                incoming_text=message.text,
                previous_text=str(parent.metadata.get("output", "")),
                previous_scene=(
                    str(parent.metadata["dialogue_scene"])
                    if parent.metadata.get("source") == "dialogue_v1"
                    and isinstance(parent.metadata.get("dialogue_scene"), str)
                    else None
                ),
                previous_stage=old_stage,
                recent_outputs=recent_outputs,
                rng=random.Random(self.rng.getrandbits(64)),
            )
            if choice is None or not is_safe_entertainment_reply(choice.text):
                return True, None
            sent = await message.reply(html.escape(choice.text))
            sent_id = getattr(sent, "message_id", None)
            settings = await self.storage.get_settings(chat_id)
            action = EntertainmentActionRecord(
                id=None,
                chat_id=chat_id,
                topic_id=topic_id,
                action_type=EntertainmentActionType.CONTEXTUAL_REPLY,
                trigger_message_id=message_id,
                created_at=now,
                metadata={
                    "source": "dialogue_v1",
                    "mode": settings.behavior_mode.value,
                    "direct": True,
                    "output_kind": "text",
                    "output": choice.text,
                    "dialogue_scene": choice.scene,
                    "dialogue_intent": choice.intent,
                    "dialogue_stage": choice.stage,
                    "bot_message_id": int(sent_id) if sent_id is not None else None,
                },
            )
            action_id = await self.storage.record_action(action)
            LOGGER.info(
                "FOSGEN_DIALOGUE_REPLY chat_id=%s topic_id=%s stage=%s intent=%s scene=%s",
                chat_id, topic_id, choice.stage, choice.intent, choice.scene,
            )
            return True, replace(action, id=action_id)

    async def evaluate_topic(
        self,
        message: Message,
        *,
        supervisor: bool = False,
    ) -> EntertainmentActionRecord | None:
        if (
            not supervisor
            and self._human_direct_reply(message)
            and detect_greeting(getattr(message, "text", None)) is None
            and self._is_direct_trigger(message)
            and self._message_scope_allowed(message)
        ):
            now = int(self._now_fn())
            chat_id = int(message.chat.id)
            topic_id = self._topic_id(message)
            settings = await self.storage.get_settings(chat_id)
            if not settings.enabled or not settings.autonomous_text_enabled:
                return None
            if self._quiet_hours_active(settings, now):
                return None
            matched, record = await self._dialogue_reply(
                message, chat_id=chat_id, topic_id=topic_id, now=now,
            )
            if matched:
                return record
        return await super().evaluate_topic(message, supervisor=supervisor)


__all__ = ["EntertainmentService"]
