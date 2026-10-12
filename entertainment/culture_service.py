"""Phase C Entertainment service with broad Culture Memory ingestion.

The Phase B service remains the compatibility/base implementation. This
subclass widens canonical learning, carries bootstrap state, integrates the
chat-only Generation v3 engine and can realize an already-approved autonomy
slot as one contextually relevant remembered media item without changing the
existing action budget.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass, replace

from aiogram.types import Message

from .autonomy import ConversationPhase, DecisionContext, derive_phase, select_action
from .banter import choose_banter_line, recent_banter_context
from .config import (
    BOOTSTRAP_TEXT_EVENT_THRESHOLD,
    GENERATION_SAMPLE_LIMIT,
    MEDIA_REPEAT_COOLDOWN_SECONDS,
    MIN_MESSAGES_TO_GENERATE,
)
from .culture import (
    CultureGenerationContext,
    CultureMemorySnapshot,
    apply_emoji_style,
    build_culture_context,
)
from .generation import generate_text
from .generation_v3 import GenerationMode, GenerationRequest, GenerationResult
from .media_culture import MediaCandidate, select_media_candidate
from .slang import choose_slang_callback, learn_topic_slang
from .memory import classify_memory_event
from .models import (
    EntertainmentActionRecord,
    EntertainmentActionType,
    MemoryCounts,
    MemoryEvent,
    MemoryEventType,
)
from .novelty import is_novel_generated_text
from .personality import (
    PERSONALITY_USER_COOLDOWN_SECONDS,
    choose_personality_line,
)
from .output_balance import recent_literary_context, should_send_media
from .moderation_safety import is_safe_entertainment_reply, is_safe_entertainment_text
from .service import (
    EntertainmentService as PhaseBEntertainmentService,
    _CULTURE_FALLBACK_CONTEXT_LIMIT,
    _CULTURE_HISTORICAL_WINDOW_COUNT,
    _CULTURE_HISTORICAL_WINDOW_SIZE,
)

LOGGER = logging.getLogger(__name__)
_MEDIA_ACTION_HISTORY_LIMIT = 200


@dataclass(frozen=True, slots=True)
class CultureGenerationOutcome:
    """Text result compatible with old two-value unpacking plus safe diagnostics."""

    text: str | None
    emoji_signature: str | None
    diagnostics: GenerationResult | None

    def __iter__(self):
        yield self.text
        yield self.emoji_signature


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
            culture_seed = self._culture_seed(chat_id, topic_id, timestamp)
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
                    seed=culture_seed,
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
                weight_seed=culture_seed,
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
        snapshot = await self._culture_memory_snapshot(
            chat_id,
            topic_id,
            trigger_text=trigger_text,
            now=now,
        )
        return snapshot.generation

    @staticmethod
    def _generation_score_bucket(score: float) -> str:
        if score < 2.0:
            return "low"
        if score < 4.0:
            return "medium"
        if score < 6.0:
            return "high"
        return "very_high"

    def _generate_culture_text(
        self,
        context: CultureGenerationContext,
        *,
        recent_outputs: list[str],
        recent_signatures: set[str],
        mode: GenerationMode = GenerationMode.AUTONOMOUS,
        trigger_text: str | None = None,
    ) -> CultureGenerationOutcome:
        """Generate through v3/v2 facade, validate text, then apply emoji culture."""
        if not context.source_messages:
            return CultureGenerationOutcome(None, None, None)

        request = GenerationRequest(
            source_messages=list(context.source_messages),
            context_messages=list(context.context_messages),
            trigger_text=trigger_text,
            mode=mode,
            recent_bot_outputs=list(recent_outputs),
        )
        for _ in range(5):
            result = generate_text(request, rng=self.rng)
            if result is None:
                continue
            generated = result.text
            if not is_novel_generated_text(
                generated,
                context.source_messages,
                recent_outputs,
            ):
                continue
            styled, signature = apply_emoji_style(
                generated,
                context,
                recent_signatures=recent_signatures,
                rng=self.rng,
            )
            return CultureGenerationOutcome(styled, signature, result)
        return CultureGenerationOutcome(None, None, None)

    async def _send_media_candidate(
        self,
        *,
        chat_id: int,
        topic_id: int,
        candidate: MediaCandidate,
    ) -> None:
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

        generation_mode = (
            GenerationMode.DIRECT_REPLY
            if selected.action_type is EntertainmentActionType.CONTEXTUAL_REPLY
            else GenerationMode.AUTONOMOUS
        )
        trigger_text = (
            getattr(message, "text", None)
            if generation_mode is GenerationMode.DIRECT_REPLY
            else None
        )
        snapshot = await self._culture_memory_snapshot(
            chat_id,
            topic_id,
            trigger_text=trigger_text,
            now=now,
        )
        recent_outputs, recent_signatures = self._recent_generation_metadata(recent_actions)
        generation_outcome = self._generate_culture_text(
            snapshot.generation,
            recent_outputs=recent_outputs,
            recent_signatures=recent_signatures,
            mode=generation_mode,
            trigger_text=trigger_text,
        )
        generated, emoji_signature = generation_outcome
        generation_result = getattr(generation_outcome, "diagnostics", None)
        # Extra fail-closed boundary even for mocked/legacy generators.
        if generated is not None and not is_safe_entertainment_reply(generated):
            generated, emoji_signature, generation_result = None, None, None

        personality_theme: str | None = None
        personality_style: str | None = None
        personality_personal = False
        banter_topic: str | None = None
        slang_term: str | None = None

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

        # v3 is deliberately conservative and sometimes yields no viable
        # phrase. Before falling back to a sticker again, try one *original*
        # contextual literary joke. Only fresh human conversation from this
        # topic is eligible; no stale memory or personal profile is inferred.
        if generated is None:
            fresh_topic_text = recent_literary_context(
                snapshot.recent_events, now=now,
            )
            if fresh_topic_text and is_safe_entertainment_text(fresh_topic_text):
                history_24h = await self.storage.recent_actions(
                    chat_id, topic_id, since=now - 24 * 60 * 60, limit=100,
                )
                history_texts = [
                    str(action.metadata.get("output") or "")
                    for action in history_24h
                    if isinstance(action.metadata, dict)
                ]
                # Try several styles before giving up. No random "free" new
                # messages: autonomy has already authorized this exact slot.
                styles = ["ironic", "absurd", "gentle"]
                self.rng.shuffle(styles)
                for recovery_style in styles:
                    recovered = choose_personality_line(
                        style=recovery_style,
                        trigger_text=fresh_topic_text,
                        recent_outputs=history_texts,
                        rng=self.rng,
                        engagement_probability=1.0,
                    )
                    if recovered and is_safe_entertainment_text(recovered[0]):
                        generated, personality_theme = recovered
                        personality_style = recovery_style
                        generation_result = None
                        emoji_signature = None
                        break

        # Fresh community slang is learned from persisted, consent-respecting
        # Culture Memory: no new profile/dictionary and no per-user labels.
        # Prefer a callback only occasionally, never override good v3 text.
        if generated is None and self.rng.random() < 0.40:
            terms = learn_topic_slang(
                snapshot.recent_events, snapshot.historical_windows, now=now,
            )
            if terms:
                history_24h = await self.storage.recent_actions(
                    chat_id, topic_id, since=now - 24 * 60 * 60, limit=100,
                )
                recent_lines = [
                    str(action.metadata.get("output") or "")
                    for action in history_24h
                    if isinstance(action.metadata, dict)
                ]
                slang_choice = choose_slang_callback(
                    terms, recent_outputs=recent_lines, rng=self.rng,
                )
                if slang_choice is not None:
                    generated, slang_term = slang_choice
                    generated, emoji_signature = apply_emoji_style(
                        generated, snapshot.generation,
                        recent_signatures=recent_signatures, rng=self.rng,
                    )
                    if not is_safe_entertainment_reply(generated):
                        generated, emoji_signature, slang_term = None, None, None

        # Text Recovery v2: v3 sometimes fails outside literary discussions.
        # Only an ALREADY-approved action, fresh human context in this topic,
        # original non-personal lines and the existing 24h anti-repeat policy.
        if generated is None:
            fresh_banter = recent_banter_context(snapshot.recent_events, now=now)
            if fresh_banter and is_safe_entertainment_text(fresh_banter):
                history_24h = await self.storage.recent_actions(
                    chat_id, topic_id, since=now - 24 * 60 * 60, limit=100,
                )
                recent_lines = [
                    str(action.metadata.get("output") or "")
                    for action in history_24h
                    if isinstance(action.metadata, dict)
                ]
                recovered = choose_banter_line(
                    trigger_text=fresh_banter,
                    recent_outputs=recent_lines,
                    rng=self.rng,
                )
                if recovered and is_safe_entertainment_text(recovered[0]):
                    generated, emoji_signature = apply_emoji_style(
                        recovered[0],
                        snapshot.generation,
                        recent_signatures=recent_signatures,
                        rng=self.rng,
                    )
                    banter_topic = recovered[1]
                    generation_result = None

        # Previously media always won over an already-generated reply. Keep
        # funny stickers/photos, but reserve most slots for good text when it
        # exists. If no safe text can be produced, media remains available.
        if media_allowed and media_candidate is not None:
            media_allowed = should_send_media(
                media_actions,
                text_ready=generated is not None,
                direct_reply=selected.is_direct,
                rng=self.rng,
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
                        "output_kind": "media",
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
                return replace(record, id=action_id)

        # Relationship Engine runs AFTER the media decision, so an image or
        # sticker cannot consume a person's rare personalized-response slot.
        # This does not create another action: the autonomy/presence budget
        # has already approved this exact message.
        if generated is not None or generation_mode is GenerationMode.DIRECT_REPLY:
            style_for_line: str | None = None
            preferred_topic: str | None = None
            eligible_user_id: int | None = None
            chance = 0.0
            if generation_mode is GenerationMode.DIRECT_REPLY and not supervisor:
                sender = getattr(message, "from_user", None)
                read_style = getattr(self.storage, "get_personality_style", None)
                read_consent = getattr(self.storage, "get_remember_enabled", None)
                reserve = getattr(self.storage, "try_reserve_personality_reply", None)
                if (
                    sender is not None
                    and not bool(getattr(sender, "is_bot", False))
                    and callable(read_style)
                    and callable(read_consent)
                    and callable(reserve)
                    and await read_consent(chat_id, int(sender.id))
                ):
                    style_for_line = await read_style(chat_id, int(sender.id))
                    if style_for_line:
                        read_topic = getattr(self.storage, "get_personality_topic", None)
                        if callable(read_topic):
                            preferred_topic = await read_topic(chat_id, int(sender.id))
                        eligible_user_id = int(sender.id)
                        chance = 0.28
            elif generation_mode is GenerationMode.AUTONOMOUS and not supervisor and generated:
                # Rare community-wide literary interventions are original
                # authored jokes about current CHAT context, not a person.
                style_for_line = self.rng.choice(("gentle", "ironic", "absurd"))
                chance = 0.08

            if style_for_line:
                recent_day = await self.storage.recent_actions(
                    chat_id, topic_id, since=now - 24 * 60 * 60, limit=100,
                )
                recent_lines = [
                    str(action.metadata.get("output") or "")
                    for action in recent_day
                    if isinstance(action.metadata, dict)
                ]
                request_text = (
                    trigger_text if eligible_user_id is not None
                    else (
                        snapshot.generation.context_messages[-1]
                        if snapshot.generation.context_messages else None
                    )
                )
                choice = choose_personality_line(
                    style=style_for_line,
                    trigger_text=request_text,
                    recent_outputs=recent_lines,
                    rng=self.rng,
                    engagement_probability=chance,
                    preferred_topic=preferred_topic,
                )
                if choice is not None and is_safe_entertainment_text(choice[0]):
                    allowed = True
                    if eligible_user_id is not None:
                        # Atomic in PostgreSQL: two workers cannot both send
                        # personalized jokes to one person during this window.
                        allowed = await reserve(
                            chat_id, eligible_user_id, now=now,
                            cooldown_seconds=PERSONALITY_USER_COOLDOWN_SECONDS,
                        )
                    if allowed:
                        generated, personality_theme = choice
                        personality_style = style_for_line
                        personality_personal = eligible_user_id is not None
                        generation_result = None
                        emoji_signature = None

        # Protect the final send after any personality/banter/slang override.
        # Media callbacks are handled separately above.
        if generated is None or not is_safe_entertainment_reply(generated):
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
                "output_kind": "text",
                "memory_count": memory_count,
                "source": "supervisor" if supervisor else "message",
                "direct": bool(selected.is_direct),
                "emoji_signature": emoji_signature,
                "culture_recent_events": snapshot.generation.recent_event_count,
                "culture_historical_events": snapshot.generation.historical_event_count,
                "culture_runs": snapshot.generation.conversation_run_count,
                "generation_engine": (
                    "community_slang_v1" if slang_term else
                    "banter_v1" if banter_topic else
                    "personality_v1" if personality_style else
                    generation_result.engine if generation_result is not None else "unknown"
                ),
                "personality_theme": personality_theme,
                "banter_topic": banter_topic,
                "slang_callback": slang_term is not None,
                "personality_style": personality_style,
                "personality_personal": personality_personal,
                "generation_mode": generation_mode.value,
                "generation_candidate_count": (
                    generation_result.candidate_count if generation_result is not None else 0
                ),
                "generation_score_bucket": (
                    self._generation_score_bucket(generation_result.score)
                    if generation_result is not None
                    else "none"
                ),
                "generation_rejections": (
                    dict(generation_result.rejection_counts)
                    if generation_result is not None
                    else {}
                ),
            },
        )
        action_id = await self.storage.record_action(record)
        return replace(record, id=action_id)


__all__ = ["CultureGenerationOutcome", "EntertainmentService"]
