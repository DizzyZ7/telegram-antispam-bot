"""Generation observability layered on top of the hard-scoped Phase C service."""

from __future__ import annotations

import html
from collections.abc import Mapping
from typing import Any

from aiogram.types import Message

from .config import resolve_generation_engine
from .culture import CultureGenerationContext
from .generation_metrics import GenerationMetricsSnapshot, ScopedGenerationMetrics, aggregate_generation_actions
from .generation_v3 import GenerationMode
from .scoped_service import EntertainmentService as ScopedEntertainmentService

_GENERATION_STATUS_WINDOW_SECONDS = 24 * 60 * 60
_GENERATION_STATUS_ACTION_LIMIT = 200


def _format_counts(counts: Mapping[str, int], *, empty: str = "нет данных") -> str:
    if not counts:
        return empty
    return ", ".join(
        f"{html.escape(str(key))}: <b>{int(value)}</b>"
        for key, value in sorted(counts.items(), key=lambda item: (-int(item[1]), str(item[0])))
    )


def _format_rejections(snapshot: GenerationMetricsSnapshot) -> str:
    if not snapshot.rejection_counts:
        return "нет"
    top = sorted(snapshot.rejection_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    return ", ".join(
        f"{html.escape(reason)}: <b>{count}</b>" for reason, count in top
    )


class EntertainmentService(ScopedEntertainmentService):
    """Hard-scoped Entertainment service with privacy-safe generation telemetry."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._generation_metrics = ScopedGenerationMetrics()

    def _generate_culture_text(
        self,
        context: CultureGenerationContext,
        *,
        recent_outputs: list[str],
        recent_signatures: set[str],
        mode: GenerationMode = GenerationMode.AUTONOMOUS,
        trigger_text: str | None = None,
    ):
        """Record one aggregate metric for one service-level generation request."""
        outcome = super()._generate_culture_text(
            context,
            recent_outputs=recent_outputs,
            recent_signatures=recent_signatures,
            mode=mode,
            trigger_text=trigger_text,
        )
        result = getattr(outcome, "diagnostics", None)
        engine = result.engine if result is not None else resolve_generation_engine()
        self._generation_metrics.record(
            mode=mode.value,
            engine=engine,
            result=result,
        )
        return outcome

    async def show_generation_status(self, message: Message) -> None:
        """Show admins bounded generation health without exposing conversation text."""
        if not self._message_scope_allowed(message):
            return
        if not await self._is_admin(message):
            await message.reply("⚙️ Статус генератора доступен только администрации чата.")
            return

        chat_id = int(message.chat.id)
        topic_id = self._topic_id(message)
        now = int(self._now_fn())
        actions = await self.storage.recent_actions(
            chat_id,
            topic_id,
            since=now - _GENERATION_STATUS_WINDOW_SECONDS,
            limit=_GENERATION_STATUS_ACTION_LIMIT,
        )
        persisted = aggregate_generation_actions(actions)
        live = self._generation_metrics.snapshot()
        engine = resolve_generation_engine()

        live_rate = f"{live.success_rate * 100:.1f}%" if live.attempts else "нет данных"

        text = (
            "🧪 <b>Generation v3 · status</b>\n\n"
            f"Активный движок: <b>{html.escape(engine)}</b>\n"
            "<b>Live с запуска процесса</b>\n"
            f"Попытки: <b>{live.attempts}</b> · успешно: <b>{live.successes}</b> · "
            f"no-output: <b>{live.no_output}</b> · success rate: <b>{live_rate}</b>\n"
            f"Движки: {_format_counts(live.engine_counts)}\n"
            f"Режимы: {_format_counts(live.mode_counts)}\n"
            f"Среднее кандидатов: <b>{live.average_candidate_count:.1f}</b>\n"
            f"Отбраковки: {_format_rejections(live)}\n\n"
            "<b>История темы за 24 часа</b>\n"
            f"Сохраненных успешных генераций: <b>{persisted.successes}</b>\n"
            f"Движки: {_format_counts(persisted.engine_counts)}\n"
            f"Режимы: {_format_counts(persisted.mode_counts)}\n"
            f"Среднее кандидатов: <b>{persisted.average_candidate_count:.1f}</b>\n"
            f"Отбраковки: {_format_rejections(persisted)}\n\n"
            "Rollback: <code>ENTERTAINMENT_GENERATION_ENGINE=v2</code>\n"
            "🔒 Эта статистика не читает и не выводит исходные сообщения, контекст или триггер.\n"
            "ℹ️ no-output доступен только live и обнуляется после рестарта процесса."
        )
        await message.reply(text)


__all__ = ["EntertainmentService"]
