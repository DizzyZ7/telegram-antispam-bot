"""Phase C Entertainment service with broad Culture Memory ingestion.

The Phase B service remains the compatibility/base implementation. This
subclass widens canonical learning, carries bootstrap state and can realize an
already-approved autonomy slot as one contextually relevant remembered media
item without changing the existing action budget.
"""

from __future__ import annotations

import html
import logging
from dataclasses import replace

from aiogram.types import Message

from .autonomy import ConversationPhase, DecisionContext, derive_phase, select_action
from .config import (
    BOOTSTRAP_TEXT_EVENT_THRESHOLD,
    GENERATION_SAMPLE_LIMIT,
    MEDIA_REPEAT_COOLDOWN_SECONDS,
    MIN_MESSAGES_TO_GENERATE,
)
from .culture import CultureGenerationContext, CultureMemorySnapshot, build_culture_context
from .media_culture import MediaCandidate, select_media_candidate
from .memory import classify_memory_event
from .models import (
    EntertainmentActionRecord,
    EntertainmentActionType,
    MemoryCounts,
    MemoryEvent,
    MemoryEventType,
)
from .service import (
    EntertainmentService as PhaseBEntertainmentService,
    _CULTURE_FALLBACK_CONTEXT_LIMIT,
    _CULTURE_HISTORICAL_WINDOW_COUNT,
    _CULTURE_HISTORICAL_WINDOW_SIZE,
)

LOGGER = logging.getLogger(__name__)
_MEDIA_ACTION_HISTORY_LIMIT = 200


class EntertainmentService(PhaseBEntertainmentService):
    """Phase C service: learn broad culture and reuse remembered media safely."""

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

    @staticmethod
    def _filter_scope_events(
        events: list[MemoryEvent],
        *,
        chat_id: int,
        topic_id: int,
    ) -> list[MemoryEvent]:
        return [
            event
            for event in events
            if int(event.chat_id) == int(chat_id) and int(event.topic_id) == int(topic_id)
        ]

    async def _culture_memory_snapshot(
        self,
        chat_id: int,
        topic_id: int,
        *,
        trigger_text: str | None = None,
        now: int | None = None,
    ) -> CultureMemorySnapshot:
        """Read one bounded Culture Memory snapshot for text, emoji and media."""
        recent_events = getattr(self.storage, "recent_events", None)
        sample_event_windows = getattr(self.storage, "sample_event_windows", None)
        memory_counts = getattr(self.storage, "memory_counts", None)

        if callable(recent_events) and callable(sample_event_windows) and callable(memory_counts):
            timestamp = int(self._now_fn()) if now is None else int(now)
            raw_recent = list(
                await recent_events(
                    int(chat_id),
                    int(topic_id),
                    GENERATION_SAMPLE_LIMIT,
                )
            )
            raw_historical = list(
                await sample_event_windows(
                    int(chat_id),
                    int(topic_id),
                    window_count=_CULTURE_HISTORICAL_WINDOW_COUNT,
                    window_size=_CULTURE_HISTORICAL_WINDOW_SIZE,
                    seed=self._culture_seed(chat_id, topic_id, timestamp),
                )
            )
            counts = await memory_counts(int(chat_id), int(topic_id))

            recent = self._filter_scope_events(
                raw_recent,
                chat_id=int(chat_id),
                topic_id=int(topic_id),
            )
            historical = [
                self._filter_scope_events(
                    list(window),
                    chat_id=int(chat_id),
                    topic_id=int(topic_id),
                )
                for window in raw_historical
            ]
            historical = [window for window in historical if window]
            generation = build_culture_context(
                recent,
                historical,
                trigger_text=trigger_text,
                textual_event_count=int(counts.text) + int(counts.emoji),
                bootstrap_threshold=BOOTSTRAP_TEXT_EVENT_THRESHOLD,
                weight_seed=self._culture_seed(chat_id, topic_id, timestamp),
            )
            return CultureMemorySnapshot(
                recent_events=recent,
                historical_windows=historical,
                counts=counts,
                generation=generation,
            )

        messages = await self._generation_texts(int(chat_id), int(topic_id))
        context_messages = list(messages[-_CULTURE_FALLBACK_CONTEXT_LIMIT:])
        cleaned_trigger = (
            " ".join(trigger_text.split()).strip()
            if isinstance(trigger_text, str)
            else ""
        )
        if cleaned_trigger:
            context_messages.extend([cleaned_trigger, cleaned_trigger])
        generation = CultureGenerationContext(
            source_messages=list(messages),
            context_messages=context_messages,
            recent_event_count=len(messages),
            historical_event_count=0,
            conversation_run_count=0,
        )
        return CultureMemorySnapshot(
            recent_events=[],
            historical_windows=[],
            counts=MemoryCounts(total=len(messages), text=len(messages)),
            generation=generation,
        )

    async def _culture_generation_context(
        self,
        chat_id: int,
        topic_id: int,
        *,
        trigger_text: str | None = None,
        now: int | None = None,
    ) -> CultureGenerationContext:
        """Compatibility projection of the shared Phase C snapshot."""
        snapshot = await self._culture_memory_snapshot(
            chat_id,
            topic_id,
            trigger_text=trigger_text,
            now=now,
        )
        return snapshot.generation

    async def _send_media_candidate(
        self,
        *,
        chat_id: int,
        topic_id: int,
        candidate: MediaCandidate,
    ) -> None:
        """Send one remembered Telegram media item by file_id without captions."""
        event = candidate.event
        thread_id = int(topic_id) or None
        if event.event_type is MemoryEventType.STICKER:
            await self.app.bot.send_sticker(
                chat_id=int(chat_id),
                sticker=event.file_id,
                message_thread_id=thread_id,
            )
            return
        if event.event_type is MemoryEventType.PHOTO:
            await self.app.bot.send_photo(
                chat_id=int(chat_id),
                photo=event.file_id,
                message_thread_id=thread_id,
            )
            return
        if event.event_type is MemoryEventType.ANIMATION:
            await self.app.bot.send_animation(
                chat_id=int(chat_id),
                animation=event.file_id,
                message_thread_id=thread_id,
            )
            return
        raise ValueError(f"Unsupported remembered media type: {event.event_type.value}")

    @staticmethod
    def _previous_non_direct_media_blocks(
        recent_actions: tuple[EntertainmentActionRecord, ...],
        *,
        selected_is_direct: bool,
    ) -> bool:
        """Prevent consecutive autonomous media callbacks unless current is direct."""
        if selected_is_direct or not recent_actions:
            return False
        previous = max(
            recent_actions,
            key=lambda action: (int(action.created_at), int(action.id or 0)),
        )
        if previous.action_type is not EntertainmentActionType.MEMORY_CALLBACK:
            return False
        return not bool(previous.metadata.get("direct", False))

    async def _send_text_fallback(
        self,
        message: Message,
        *,
        chat_id: int,
        topic_id: int,
        supervisor: bool,
        generated: str,
    ) -> None:
        escaped = html.escape(generated)
        if supervisor:
            await self.app.bot.send_message(
                chat_id=int(chat_id),
                text=escaped,
                message_thread_id=(int(topic_id) or None),
            )
        else:
            await message.reply(escaped)

    async def evaluate_topic(
        self,
        message: Message,
        *,
        supervisor: bool = False,
    ) -> EntertainmentActionRecord | None:
        """Realize one existing autonomy slot as media or Phase B text."""
        chat_id = int(message.chat.id)
        topic_id = self._topic_id(message)
        now = int(self._now_fn())
        settings = await self.storage.get_settings(chat_id)
        if not settings.enabled or not settings.autonomous_text_enabled:
            return None

        memory_count = await self.storage.message_count(chat_id, topic_id)
        if memory_count < MIN_MESSAGES_TO_GENERATE:
            return None

        activity = await self.storage.activity_snapshot(chat_id, topic_id, now=now)
        phase = derive_phase(activity)
        if supervisor and phase not in {ConversationPhase.QUIET, ConversationPhase.COOLDOWN}:
            return None

        # This query remains the unchanged 30-minute decision/budget window.
        recent_actions = tuple(
            await self.storage.recent_actions(
                chat_id,
                topic_id,
                since=now - 30 * 60,
                limit=20,
            )
        )
        last_action = recent_actions[0] if recent_actions else None
        human_messages = (
            await self.storage.human_messages_since(
                chat_id,
                topic_id,
                since=int(last_action.created_at) + 1,
            )
            if last_action is not None
            else 0
        )
        decision = DecisionContext(
            settings=settings,
            phase=phase,
            activity=activity,
            recent_actions=recent_actions,
            human_messages_since_last_action=human_messages,
            memory_count=memory_count,
            quiet_hours_active=self._quiet_hours_active(settings, now),
            now=now,
        )
        selected = select_action(
            decision,
            self._build_candidates(phase, message),
            rng=self.rng,
        )
        if selected is None:
            return None

        trigger_text = (
            getattr(message, "text", None)
            if selected.action_type is EntertainmentActionType.CONTEXTUAL_REPLY
            else None
        )
        snapshot = await self._culture_memory_snapshot(
            chat_id,
            topic_id,
            trigger_text=trigger_text,
            now=now,
        )

        recent_outputs, recent_signatures = self._recent_generation_metadata(recent_actions)
        generated, emoji_signature = self._generate_culture_text(
            snapshot.generation,
            recent_outputs=recent_outputs,
            recent_signatures=recent_signatures,
        )

        # Anti-repeat media history is intentionally separate from the 30-minute
        # decision window, but remains hard-bounded.
        media_actions = tuple(
            await self.storage.recent_actions(
                chat_id,
                topic_id,
                since=now - MEDIA_REPEAT_COOLDOWN_SECONDS,
                limit=_MEDIA_ACTION_HISTORY_LIMIT,
            )
        )
        media_candidate = select_media_candidate(
            snapshot.recent_events,
            snapshot.historical_windows,
            context_messages=snapshot.generation.context_messages,
            recent_actions=media_actions,
            now=now,
            textual_event_count=int(snapshot.counts.text) + int(snapshot.counts.emoji),
            bootstrap_threshold=BOOTSTRAP_TEXT_EVENT_THRESHOLD,
            repeat_cooldown_seconds=MEDIA_REPEAT_COOLDOWN_SECONDS,
        )

        media_allowed = (
            media_candidate is not None
            and (phase is not ConversationPhase.PEAK or selected.is_direct)
            and not self._previous_non_direct_media_blocks(
                recent_actions,
                selected_is_direct=selected.is_direct,
            )
        )

        if media_allowed and media_candidate is not None:
            try:
                await self._send_media_candidate(
                    chat_id=chat_id,
                    topic_id=topic_id,
                    candidate=media_candidate,
                )
            except Exception:
                LOGGER.warning(
                    "ENTERTAINMENT_MEDIA_SEND_FAILED chat_id=%s topic_id=%s media_type=%s",
                    chat_id,
                    topic_id,
                    media_candidate.event.event_type.value,
                    exc_info=True,
                )
            else:
                event = media_candidate.event
                record = EntertainmentActionRecord(
                    id=None,
                    chat_id=chat_id,
                    topic_id=topic_id,
                    action_type=EntertainmentActionType.MEMORY_CALLBACK,
                    trigger_message_id=(
                        selected.trigger_message_id
                        if selected.trigger_message_id is not None
                        else getattr(message, "message_id", None)
                    ),
                    created_at=now,
                    metadata={
                        "phase": phase.value,
                        "mode": settings.behavior_mode.value,
                        "memory_count": memory_count,
                        "source": "supervisor" if supervisor else "message",
                        "direct": bool(selected.is_direct),
                        "media_type": event.event_type.value,
                        "media_file_unique_id": event.file_unique_id,
                        "media_score_bucket": media_candidate.score_bucket,
                        "media_source_class": media_candidate.source_class,
                        "culture_recent_events": snapshot.generation.recent_event_count,
                        "culture_historical_events": snapshot.generation.historical_event_count,
                        "culture_runs": snapshot.generation.conversation_run_count,
                    },
                )
                action_id = await self.storage.record_action(record)
                stored_record = replace(record, id=action_id)
                LOGGER.info(
                    "ENTERTAINMENT_AUTONOMOUS_ACTION chat_id=%s topic_id=%s action=%s phase=%s mode=%s memory=%s source=%s media_type=%s direct=%s",
                    chat_id,
                    topic_id,
                    EntertainmentActionType.MEMORY_CALLBACK.value,
                    phase.value,
                    settings.behavior_mode.value,
                    memory_count,
                    record.metadata["source"],
                    event.event_type.value,
                    int(selected.is_direct),
                )
                return stored_record

        # No usable media, blocked consecutive media, or Telegram media failure:
        # fall back once to the text realization of the same already-approved slot.
        if generated is None:
            return None
        await self._send_text_fallback(
            message,
            chat_id=chat_id,
            topic_id=topic_id,
            supervisor=supervisor,
            generated=generated,
        )

        record = EntertainmentActionRecord(
            id=None,
            chat_id=chat_id,
            topic_id=topic_id,
            action_type=selected.action_type,
            trigger_message_id=(
                selected.trigger_message_id
                if selected.trigger_message_id is not None
                else getattr(message, "message_id", None)
            ),
            created_at=now,
            metadata={
                "phase": phase.value,
                "mode": settings.behavior_mode.value,
                "output": generated,
                "memory_count": memory_count,
                "source": "supervisor" if supervisor else "message",
                "direct": bool(selected.is_direct),
                "emoji_signature": emoji_signature,
                "culture_recent_events": snapshot.generation.recent_event_count,
                "culture_historical_events": snapshot.generation.historical_event_count,
                "culture_runs": snapshot.generation.conversation_run_count,
            },
        )
        action_id = await self.storage.record_action(record)
        stored_record = replace(record, id=action_id)
        LOGGER.info(
            "ENTERTAINMENT_AUTONOMOUS_ACTION chat_id=%s topic_id=%s action=%s phase=%s mode=%s memory=%s source=%s direct=%s culture_recent=%s culture_historical=%s",
            chat_id,
            topic_id,
            selected.action_type.value,
            phase.value,
            settings.behavior_mode.value,
            memory_count,
            record.metadata["source"],
            int(selected.is_direct),
            snapshot.generation.recent_event_count,
            snapshot.generation.historical_event_count,
        )
        return stored_record


__all__ = ["EntertainmentService"]
