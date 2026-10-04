"""Shared adaptive presence budget for all Entertainment participation."""

from __future__ import annotations

import logging

from aiogram.types import Message

from .autonomy import DecisionContext, derive_phase, presence_budget_allows
from .greeting_service import EntertainmentService as GreetingEntertainmentService
from .models import EntertainmentActionRecord

LOGGER = logging.getLogger(__name__)

_PRESENCE_HISTORY_SECONDS = 120 * 60
_PRESENCE_ACTION_HISTORY_LIMIT = 200


class EntertainmentService(GreetingEntertainmentService):
    """Gate text, remembered media and greetings through one topic-local budget."""

    async def _presence_context(
        self,
        message: Message,
        *,
        now: int,
    ) -> DecisionContext:
        chat_id = int(message.chat.id)
        topic_id = self._topic_id(message)
        settings = await self.storage.get_settings(chat_id)
        activity = await self.storage.activity_snapshot(chat_id, topic_id, now=int(now))
        phase = derive_phase(activity)
        actions = tuple(
            await self.storage.recent_actions(
                chat_id,
                topic_id,
                since=int(now) - _PRESENCE_HISTORY_SECONDS,
                limit=_PRESENCE_ACTION_HISTORY_LIMIT,
            )
        )
        last_action = max(
            (action for action in actions if int(action.created_at) <= int(now)),
            key=lambda action: (int(action.created_at), int(action.id or 0)),
            default=None,
        )
        human_messages = 0
        if last_action is not None:
            human_messages = await self.storage.human_messages_since(
                chat_id,
                topic_id,
                since=int(last_action.created_at) + 1,
            )
            # One-token greetings are deliberately outside the legacy text/activity
            # table. Count the explicit current human greeting once for the shared
            # budget rather than making it invisible to presence accounting.
            if not self.is_eligible_learning_message(message):
                text = getattr(message, "text", None)
                if isinstance(text, str) and text.strip():
                    human_messages += 1

        return DecisionContext(
            settings=settings,
            phase=phase,
            activity=activity,
            recent_actions=actions,
            human_messages_since_last_action=int(human_messages),
            memory_count=0,
            quiet_hours_active=self._quiet_hours_active(settings, int(now)),
            now=int(now),
        )

    async def evaluate_topic(
        self,
        message: Message,
        *,
        supervisor: bool = False,
    ) -> EntertainmentActionRecord | None:
        now = int(self._now_fn())
        context = await self._presence_context(message, now=now)
        if not presence_budget_allows(context):
            return None
        return await super().evaluate_topic(message, supervisor=supervisor)

    async def run_supervisor_tick(self) -> None:
        """Keep recently living scopes for two hours, never resurrect dead ones."""
        now = int(self._now_fn())
        for key, message in list(self._active_topics.items()):
            chat_id, topic_id = key
            try:
                recent_human = await self.storage.human_messages_since(
                    chat_id,
                    topic_id,
                    since=now - _PRESENCE_HISTORY_SECONDS,
                )
                if recent_human <= 0:
                    self._active_topics.pop(key, None)
                    continue
                await self.evaluate_topic(message, supervisor=True)
            except Exception:
                LOGGER.exception(
                    "Entertainment presence supervisor failed chat_id=%s topic_id=%s",
                    chat_id,
                    topic_id,
                )


__all__ = ["EntertainmentService"]
