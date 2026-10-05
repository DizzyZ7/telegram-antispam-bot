"""Moderation-aware safety boundary around the production Entertainment service."""

from __future__ import annotations

from dataclasses import replace

from aiogram.types import Message

from .config import BOOTSTRAP_TEXT_EVENT_THRESHOLD, resolve_generation_engine
from .culture import CultureGenerationContext, CultureMemorySnapshot, build_culture_context
from .culture_service import CultureGenerationOutcome
from .generation_v3 import GenerationMode
from .models import MemoryEvent
from .moderation_safety import is_safe_entertainment_text
from .observability_service import (
    EntertainmentService as ObservabilityEntertainmentService,
    _CURRENT_GENERATION_SCOPE,
)
from .scoped_service import EntertainmentService as ScopedEntertainmentService


class EntertainmentService(ObservabilityEntertainmentService):
    """Prevent moderated language from entering, feeding or leaving Entertainment."""

    @staticmethod
    def _safe_text(value: str | None) -> bool:
        return is_safe_entertainment_text(value)

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
        metrics_scope: tuple[int, int] | None = None,
    ) -> CultureGenerationOutcome:
        """Retry unsafe output while recording exactly one generation attempt."""
        safe_trigger = trigger_text if self._safe_text(trigger_text) else None
        outcome = CultureGenerationOutcome(None, None, None)
        for _ in range(5):
            # Bypass only the observability wrapper while retaining the complete
            # scoped/presence/greeting/Culture Memory generation implementation.
            outcome = ScopedEntertainmentService._generate_culture_text(
                self,
                context,
                recent_outputs=recent_outputs,
                recent_signatures=recent_signatures,
                mode=mode,
                trigger_text=safe_trigger,
            )
            if outcome.text is None or self._safe_text(outcome.text):
                break
        else:
            outcome = CultureGenerationOutcome(None, None, None)

        result = getattr(outcome, "diagnostics", None)
        engine = result.engine if result is not None else resolve_generation_engine()
        scope = metrics_scope if metrics_scope is not None else _CURRENT_GENERATION_SCOPE.get()
        if scope is None:
            self._generation_metrics.record(
                mode=mode.value,
                engine=engine,
                result=result,
            )
        else:
            self._generation_metrics.record_for(
                scope[0],
                scope[1],
                mode=mode.value,
                engine=engine,
                result=result,
            )
        return outcome


__all__ = ["EntertainmentService"]
