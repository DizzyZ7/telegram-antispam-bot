"""Moderation-aware safety boundary around the production Entertainment service."""

from __future__ import annotations

from dataclasses import replace

from aiogram.types import Message

from writers_moderation import contains_prohibited_language

from .config import BOOTSTRAP_TEXT_EVENT_THRESHOLD
from .culture import CultureGenerationContext, CultureMemorySnapshot, build_culture_context
from .culture_service import CultureGenerationOutcome
from .generation_v3 import GenerationMode
from .models import MemoryEvent
from .observability_service import EntertainmentService as ObservabilityEntertainmentService


class EntertainmentService(ObservabilityEntertainmentService):
    """Prevent moderated language from entering or leaving Entertainment memory."""

    @staticmethod
    def _safe_text(value: str | None) -> bool:
        return not isinstance(value, str) or not contains_prohibited_language(value)

    @classmethod
    def _safe_event(cls, event: MemoryEvent) -> bool:
        return cls._safe_text(event.text) and cls._safe_text(event.caption)

    async def observe_message(self, message: Message) -> None:
        # Preserve the outer hard topic policy before even inspecting message text.
        scope_allowed = getattr(self, "_message_scope_allowed", None)
        if callable(scope_allowed) and not scope_allowed(message):
            return

        if not self._safe_text(getattr(message, "text", None)):
            return
        if not self._safe_text(getattr(message, "caption", None)):
            return
        await super().observe_message(message)

    async def _culture_memory_snapshot(
        self,
        chat_id: int,
        topic_id: int,
        *,
        trigger_text: str | None = None,
        now: int | None = None,
    ) -> CultureMemorySnapshot:
        snapshot = await super()._culture_memory_snapshot(
            chat_id,
            topic_id,
            trigger_text=trigger_text,
            now=now,
        )
        safe_trigger = trigger_text if self._safe_text(trigger_text) else None
        safe_recent = [event for event in snapshot.recent_events if self._safe_event(event)]
        safe_historical = [
            [event for event in window if self._safe_event(event)]
            for window in snapshot.historical_windows
        ]
        safe_historical = [window for window in safe_historical if window]

        if snapshot.recent_events or snapshot.historical_windows:
            timestamp = int(self._now_fn()) if now is None else int(now)
            generation = build_culture_context(
                safe_recent,
                safe_historical,
                trigger_text=safe_trigger,
                textual_event_count=int(snapshot.counts.text) + int(snapshot.counts.emoji),
                bootstrap_threshold=BOOTSTRAP_TEXT_EVENT_THRESHOLD,
                weight_seed=self._culture_seed(chat_id, topic_id, timestamp),
            )
        else:
            safe_sources = [
                value
                for value in snapshot.generation.source_messages
                if self._safe_text(value)
            ]
            safe_context = [
                value
                for value in snapshot.generation.context_messages
                if self._safe_text(value)
            ]
            generation = replace(
                snapshot.generation,
                source_messages=safe_sources,
                context_messages=safe_context,
                recent_event_count=len(safe_sources),
            )

        return CultureMemorySnapshot(
            recent_events=safe_recent,
            historical_windows=safe_historical,
            counts=snapshot.counts,
            generation=generation,
        )

    def _generate_culture_text(
        self,
        context: CultureGenerationContext,
        *,
        recent_outputs: list[str],
        recent_signatures: set[str],
        mode: GenerationMode = GenerationMode.AUTONOMOUS,
        trigger_text: str | None = None,
    ) -> CultureGenerationOutcome:
        safe_trigger = trigger_text if self._safe_text(trigger_text) else None
        for _ in range(5):
            outcome = super()._generate_culture_text(
                context,
                recent_outputs=recent_outputs,
                recent_signatures=recent_signatures,
                mode=mode,
                trigger_text=safe_trigger,
            )
            if outcome.text is None:
                return outcome
            if self._safe_text(outcome.text):
                return outcome
        return CultureGenerationOutcome(None, None, None)


__all__ = ["EntertainmentService"]
