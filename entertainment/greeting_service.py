"""Reliable contextual greetings for explicitly human-triggered morning/night wishes.

An eligible greeting gets a curated text reply (no random probability).
Duplicate greetings are gently throttled in the same topic and same kind.
Ambient/supervisor messages are still controlled by Presence.
"""

from __future__ import annotations

import html
import random
from dataclasses import replace

from aiogram.types import Message

from .culture_service import EntertainmentService as PhaseCEntertainmentService
from .greetings import GreetingKind, choose_greeting_reply, detect_greeting
from .models import EntertainmentActionRecord, EntertainmentActionType
from .moderation_safety import is_safe_entertainment_reply

# This is an anti-flood/deduplication window, not a random chance to stay silent.
_GREETING_COOLDOWN_SECONDS = 2 * 60
_GREETING_HISTORY_SECONDS = 24 * 60 * 60
_GREETING_ACTION_HISTORY_LIMIT = 100


class EntertainmentService(PhaseCEntertainmentService):
    """Phase C plus reliable, non-offensive, text-first greetings."""

    @staticmethod
    def _is_human_greeting(message: Message) -> bool:
        sender = getattr(message, "from_user", None)
        if sender is None or bool(getattr(sender, "is_bot", False)):
            return False
        if str(getattr(message, "text", "") or "").lstrip().startswith("/"):
            return False
        return not any(
            getattr(message, name, None) is not None
            for name in ("forward_origin", "forward_date", "forward_from", "forward_sender_name")
        ) and not bool(getattr(message, "is_automatic_forward", False))

    async def observe_message(self, message: Message) -> None:
        """Reply to greetings even when short or learning consent is disabled.

        Existing Culture Memory ingestion remains unchanged. For ordinary
        consented text the parent evaluates the greeting exactly once.
        For one-token or opted-out greetings we evaluate separately but never
        store content behind the participant's consent decision.
        """
        greeting = detect_greeting(getattr(message, "text", None))
        eligible = (
            greeting is not None
            and self._is_human_greeting(message)
            and self._is_eligible_culture_sender(message)
        )
        legacy_eligible = self.is_eligible_learning_message(message) if eligible else False
        consent = True
        if eligible:
            get_consent = getattr(self.storage, "get_remember_enabled", None)
            if callable(get_consent):
                consent = bool(await get_consent(int(message.chat.id), int(message.from_user.id)))

        await super().observe_message(message)

        # Long, consented greetings run once inside Phase C's observe path.
        if eligible and (not legacy_eligible or not consent):
            await self.evaluate_topic(message)

    @staticmethod
    def _is_greeting_action(action: EntertainmentActionRecord) -> bool:
        return action.metadata.get("source") == "greeting" and isinstance(
            action.metadata.get("greeting_kind"),
            str,
        )

    def _fork_rng(self) -> random.Random:
        """Use randomness for phrasing only, never to suppress a greeting."""
        return random.Random(self.rng.getrandbits(64))

    async def _handle_greeting(
        self,
        message: Message,
        *,
        kind: GreetingKind,
        chat_id: int,
        topic_id: int,
        now: int,
        settings,
    ) -> EntertainmentActionRecord | None:
        history = tuple(
            await self.storage.recent_actions(
                int(chat_id),
                int(topic_id),
                since=int(now) - _GREETING_HISTORY_SECONDS,
                limit=_GREETING_ACTION_HISTORY_LIMIT,
            )
        )
        # Never answer twice to a retried Telegram update.
        message_id = getattr(message, "message_id", None)
        if message_id is not None and any(
            self._is_greeting_action(action)
            and action.trigger_message_id is not None
            and int(action.trigger_message_id) == int(message_id)
            for action in history
        ):
            return None

        # Different greeting kinds have independent windows, so a night wish
        # cannot silently prevent an unrelated morning wish.
        if any(
            self._is_greeting_action(action)
            and action.metadata.get("greeting_kind") == kind
            and 0 <= int(now) - int(action.created_at) < _GREETING_COOLDOWN_SECONDS
            for action in history
        ):
            return None

        context_messages = list(await self._generation_texts(int(chat_id), int(topic_id)))
        recent_replies = [
            str(action.metadata["output"])
            for action in history
            if self._is_greeting_action(action)
            and isinstance(action.metadata.get("output"), str)
            and action.metadata.get("output")
        ]
        reply = choose_greeting_reply(
            kind,
            context_messages=context_messages[-20:],
            recent_replies=recent_replies,
            rng=self._fork_rng(),
        )
        if not is_safe_entertainment_reply(reply.text):
            return None

        # Always send a textual greeting. Remembered stickers/animations stay
        # available for other autonomous actions, not as replacements here.
        await message.reply(html.escape(reply.text))
        record = EntertainmentActionRecord(
            id=None,
            chat_id=int(chat_id),
            topic_id=int(topic_id),
            action_type=EntertainmentActionType.CONTEXTUAL_REPLY,
            trigger_message_id=message_id,
            created_at=int(now),
            metadata={
                "source": "greeting",
                "direct": True,
                "mode": settings.behavior_mode.value,
                "greeting_kind": kind,
                "greeting_style": reply.style,
                "output": reply.text,
                "output_kind": "text",
            },
        )
        action_id = await self.storage.record_action(record)
        return replace(record, id=action_id)

    async def evaluate_topic(
        self,
        message: Message,
        *,
        supervisor: bool = False,
    ) -> EntertainmentActionRecord | None:
        if not supervisor:
            kind = detect_greeting(getattr(message, "text", None))
            if kind is not None:
                if not self._is_human_greeting(message):
                    return None
                chat_id = int(message.chat.id)
                topic_id = self._topic_id(message)
                now = int(self._now_fn())
                settings = await self.storage.get_settings(chat_id)
                if not settings.enabled or not settings.autonomous_text_enabled:
                    return None
                if self._quiet_hours_active(settings, now):
                    return None
                return await self._handle_greeting(
                    message, kind=kind, chat_id=chat_id, topic_id=topic_id,
                    now=now, settings=settings,
                )
        return await super().evaluate_topic(message, supervisor=supervisor)


__all__ = ["EntertainmentService"]
