"""Phase C Entertainment service with broad Culture Memory ingestion.

The Phase B service remains the compatibility/base implementation. This
subclass widens canonical learning and carries Phase C bootstrap state while
human activity and autonomy cadence stay on the Phase B text path.
"""

from __future__ import annotations

from aiogram.types import Message

from .config import BOOTSTRAP_TEXT_EVENT_THRESHOLD, GENERATION_SAMPLE_LIMIT
from .culture import CultureGenerationContext, build_culture_context
from .memory import classify_memory_event
from .service import (
    EntertainmentService as PhaseBEntertainmentService,
    _CULTURE_HISTORICAL_WINDOW_COUNT,
    _CULTURE_HISTORICAL_WINDOW_SIZE,
)


class EntertainmentService(PhaseBEntertainmentService):
    """Phase C service: remember commands and other-bot culture safely."""

    def _is_eligible_culture_sender(self, message: Message) -> bool:
        if not self.is_allowed_chat(message.chat.id):
            return False
        if self._chat_type_value(message) not in {"group", "supergroup"}:
            return False
        from_user = getattr(message, "from_user", None)
        if from_user is None:
            return False
        sender_id = getattr(from_user, "id", None)
        if sender_id is None:
            return False
        bot_id = getattr(getattr(self.app, "bot", None), "id", None)
        return bot_id is None or int(sender_id) != int(bot_id)

    async def observe_message(self, message: Message) -> None:
        """Store broad Culture Memory without broadening human activity."""
        if not self._is_eligible_culture_sender(message):
            return

        add_event = getattr(self.storage, "add_event", None)
        get_remember_enabled = getattr(self.storage, "get_remember_enabled", None)
        if not callable(add_event) or not callable(get_remember_enabled):
            # Legacy/minimal backends intentionally retain the old human-text-only
            # behavior. Broad learning requires canonical Culture Memory.
            await self._observe_legacy_only(message)
            return

        settings = await self.storage.get_settings(message.chat.id)
        if not settings.enabled:
            return

        from_user = message.from_user
        if from_user is None:
            return
        user_id = int(from_user.id)
        sender_is_bot = bool(getattr(from_user, "is_bot", False))

        # Human privacy preferences continue to govern all human canonical
        # events, including commands and media. Other-bot culture is chat-level
        # context and is not tied to a human opt-out row.
        if not sender_is_bot and not await get_remember_enabled(int(message.chat.id), user_id):
            return

        topic_id = self._topic_id(message)
        now = int(self._now_fn())
        event = classify_memory_event(
            message,
            chat_id=int(message.chat.id),
            topic_id=topic_id,
            created_at=now,
        )
        if event is None:
            return

        await add_event(event)

        # Keep the Phase B human activity/autonomy path exactly as-is. Commands,
        # other-bot events and media-only events enrich memory but never create
        # human activity or trigger an autonomy evaluation by themselves.
        if not self.is_eligible_learning_message(message):
            return

        text = message.text.strip()
        await self.storage.add_message(
            chat_id=message.chat.id,
            topic_id=topic_id,
            user_id=user_id,
            text=text,
            message_id=getattr(message, "message_id", None),
            created_at=now,
        )
        self.remember_active_topic(message)
        await self.evaluate_topic(message)

    async def _culture_generation_context(
        self,
        chat_id: int,
        topic_id: int,
        *,
        trigger_text: str | None = None,
        now: int | None = None,
    ) -> CultureGenerationContext:
        """Build Phase C context with topic-local bootstrap maturity."""
        recent_events = getattr(self.storage, "recent_events", None)
        sample_event_windows = getattr(self.storage, "sample_event_windows", None)
        memory_counts = getattr(self.storage, "memory_counts", None)
        if callable(recent_events) and callable(sample_event_windows) and callable(memory_counts):
            timestamp = int(self._now_fn()) if now is None else int(now)
            recent = await recent_events(
                int(chat_id),
                int(topic_id),
                GENERATION_SAMPLE_LIMIT,
            )
            historical = await sample_event_windows(
                int(chat_id),
                int(topic_id),
                window_count=_CULTURE_HISTORICAL_WINDOW_COUNT,
                window_size=_CULTURE_HISTORICAL_WINDOW_SIZE,
                seed=self._culture_seed(chat_id, topic_id, timestamp),
            )
            counts = await memory_counts(int(chat_id), int(topic_id))
            return build_culture_context(
                recent,
                historical,
                trigger_text=trigger_text,
                textual_event_count=int(counts.text) + int(counts.emoji),
                bootstrap_threshold=BOOTSTRAP_TEXT_EVENT_THRESHOLD,
            )

        return await super()._culture_generation_context(
            chat_id,
            topic_id,
            trigger_text=trigger_text,
            now=now,
        )


__all__ = ["EntertainmentService"]
