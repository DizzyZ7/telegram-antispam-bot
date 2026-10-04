"""Contextual morning/night reactions layered over Culture Memory Phase C."""

from __future__ import annotations

import html
import logging
from dataclasses import replace

from aiogram.types import Message

from .config import (
    BOOTSTRAP_TEXT_EVENT_THRESHOLD,
    MEDIA_REPEAT_COOLDOWN_SECONDS,
)
from .culture_service import EntertainmentService as PhaseCEntertainmentService
from .greetings import GreetingKind, choose_greeting_reply, detect_greeting
from .media_culture import select_media_candidate
from .models import EntertainmentActionRecord, EntertainmentActionType

LOGGER = logging.getLogger(__name__)

_GREETING_RESPONSE_CHANCE = 0.40
_GREETING_MEDIA_CHANCE = 0.28
_GREETING_COOLDOWN_SECONDS = 20 * 60
_GREETING_HISTORY_SECONDS = 24 * 60 * 60
_GREETING_ACTION_HISTORY_LIMIT = 100
_MEDIA_ACTION_HISTORY_LIMIT = 200


class EntertainmentService(PhaseCEntertainmentService):
    """Phase C plus bounded, safe and context-aware social greetings."""

    async def observe_message(self, message: Message) -> None:
        """Keep one-token greetings on the normal broad-memory observation path."""
        greeting_kind = detect_greeting(getattr(message, "text", None))
        short_greeting = (
            greeting_kind is not None
            and self._is_eligible_culture_sender(message)
            and not self.is_eligible_learning_message(message)
        )

        await super().observe_message(message)

        # Parent Phase C already calls self.evaluate_topic() for legacy-eligible
        # text. Single-token greetings such as "споки" and "гн" are intentionally
        # outside that legacy path, so evaluate them once after broad ingestion.
        if short_greeting:
            await self.evaluate_topic(message)

    @staticmethod
    def _is_greeting_action(action: EntertainmentActionRecord) -> bool:
        return action.metadata.get("source") == "greeting" and isinstance(
            action.metadata.get("greeting_kind"),
            str,
        )

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
        if any(
            self._is_greeting_action(action)
            and 0 <= int(now) - int(action.created_at) <= _GREETING_COOLDOWN_SECONDS
            for action in history
        ):
            return None

        if self.rng.random() >= _GREETING_RESPONSE_CHANCE:
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
            rng=self.rng,
        )

        snapshot = await self._culture_memory_snapshot(
            int(chat_id),
            int(topic_id),
            trigger_text=getattr(message, "text", None),
            now=int(now),
        )
        media_actions = tuple(
            await self.storage.recent_actions(
                int(chat_id),
                int(topic_id),
                since=int(now) - MEDIA_REPEAT_COOLDOWN_SECONDS,
                limit=_MEDIA_ACTION_HISTORY_LIMIT,
            )
        )
        media_candidate = select_media_candidate(
            snapshot.recent_events,
            snapshot.historical_windows,
            context_messages=[*context_messages[-8:], str(getattr(message, "text", "") or "")],
            recent_actions=media_actions,
            now=int(now),
            textual_event_count=int(snapshot.counts.text) + int(snapshot.counts.emoji),
            bootstrap_threshold=BOOTSTRAP_TEXT_EVENT_THRESHOLD,
            repeat_cooldown_seconds=MEDIA_REPEAT_COOLDOWN_SECONDS,
            rng=self.rng,
        )

        if media_candidate is not None and self.rng.random() < _GREETING_MEDIA_CHANCE:
            try:
                await self._send_media_candidate(
                    chat_id=int(chat_id),
                    topic_id=int(topic_id),
                    candidate=media_candidate,
                )
            except Exception:
                LOGGER.warning(
                    "ENTERTAINMENT_GREETING_MEDIA_SEND_FAILED chat_id=%s topic_id=%s media_type=%s",
                    chat_id,
                    topic_id,
                    media_candidate.event.event_type.value,
                    exc_info=True,
                )
            else:
                event = media_candidate.event
                record = EntertainmentActionRecord(
                    id=None,
                    chat_id=int(chat_id),
                    topic_id=int(topic_id),
                    action_type=EntertainmentActionType.MEMORY_CALLBACK,
                    trigger_message_id=getattr(message, "message_id", None),
                    created_at=int(now),
                    metadata={
                        "source": "greeting",
                        "direct": True,
                        "mode": settings.behavior_mode.value,
                        "greeting_kind": kind,
                        "greeting_style": reply.style,
                        "media_type": event.event_type.value,
                        "media_file_unique_id": event.file_unique_id,
                        "media_score_bucket": media_candidate.score_bucket,
                        "media_source_class": media_candidate.source_class,
                    },
                )
                action_id = await self.storage.record_action(record)
                return replace(record, id=action_id)

        await message.reply(html.escape(reply.text))
        record = EntertainmentActionRecord(
            id=None,
            chat_id=int(chat_id),
            topic_id=int(topic_id),
            action_type=EntertainmentActionType.CONTEXTUAL_REPLY,
            trigger_message_id=getattr(message, "message_id", None),
            created_at=int(now),
            metadata={
                "source": "greeting",
                "direct": True,
                "mode": settings.behavior_mode.value,
                "greeting_kind": kind,
                "greeting_style": reply.style,
                "output": reply.text,
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
                chat_id = int(message.chat.id)
                topic_id = self._topic_id(message)
                now = int(self._now_fn())
                settings = await self.storage.get_settings(chat_id)
                if not settings.enabled or not settings.autonomous_text_enabled:
                    return None
                return await self._handle_greeting(
                    message,
                    kind=kind,
                    chat_id=chat_id,
                    topic_id=topic_id,
                    now=now,
                    settings=settings,
                )

        return await super().evaluate_topic(message, supervisor=supervisor)


__all__ = ["EntertainmentService"]
