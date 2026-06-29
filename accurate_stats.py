"""Reliable per-day Telegram statistics independent from legacy summary storage."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiosqlite
from aiogram import BaseMiddleware
from aiogram.filters import Command
from aiogram.types import Message, MessageReactionUpdated

LOGGER = logging.getLogger(__name__)
RETENTION_SECONDS = 8 * 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class Participant:
    username: str
    full_name: str
    message_count: int


@dataclass(frozen=True, slots=True)
class StatsSnapshot:
    text_messages: int
    stickers: int
    emoji_count: int
    emoji_messages: int
    emoji_only_messages: int
    reactions_added: int
    participants: tuple[Participant, ...]
    top_reactions: tuple[tuple[str, int], ...]
    tracking_started_at: int | None


class AccurateStatsStorage:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._connection: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self._database_path)
        self._connection.row_factory = aiosqlite.Row
        await self._connection.execute("PRAGMA journal_mode=WAL;")
        await self._connection.execute("PRAGMA synchronous=NORMAL;")
        await self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tracked_messages (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                full_name TEXT NOT NULL,
                kind TEXT NOT NULL,
                emoji_count INTEGER NOT NULL DEFAULT 0,
                emoji_only INTEGER NOT NULL DEFAULT 0,
                timestamp INTEGER NOT NULL,
                PRIMARY KEY(chat_id, message_id)
            )
            """
        )
        await self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_tracked_messages_chat_time "
            "ON tracked_messages(chat_id, timestamp)"
        )
        await self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS reaction_additions (
                event_key TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                reaction TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                timestamp INTEGER NOT NULL
            )
            """
        )
        await self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_reaction_additions_chat_time "
            "ON reaction_additions(chat_id, timestamp)"
        )
        await self._connection.commit()

    async def close(self) -> None:
        if self._connection is None:
            return
        await self._connection.close()
        self._connection = None

    async def add_message(
        self,
        *,
        chat_id: int,
        message_id: int,
        user_id: int,
        username: str,
        full_name: str,
        kind: str,
        emoji_count: int,
        emoji_only: bool,
        timestamp: int,
    ) -> None:
        assert self._connection is not None
        cutoff = timestamp - RETENTION_SECONDS
        async with self._lock:
            await self._connection.execute(
                "DELETE FROM tracked_messages WHERE timestamp < ?",
                (cutoff,),
            )
            await self._connection.execute(
                "DELETE FROM reaction_additions WHERE timestamp < ?",
                (cutoff,),
            )
            await self._connection.execute(
                """
                INSERT OR IGNORE INTO tracked_messages(
                    chat_id, message_id, user_id, username, full_name,
                    kind, emoji_count, emoji_only, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    message_id,
                    user_id,
                    username,
                    full_name,
                    kind,
                    emoji_count,
                    int(emoji_only),
                    timestamp,
                ),
            )
            await self._connection.commit()

    async def add_reaction_additions(
        self,
        *,
        chat_id: int,
        message_id: int,
        actor_key: str,
        old_reactions: tuple[str, ...],
        new_reactions: tuple[str, ...],
        timestamp: int,
    ) -> None:
        assert self._connection is not None
        old_counter = Counter(old_reactions)
        new_counter = Counter(new_reactions)
        additions = new_counter - old_counter
        if not additions:
            return

        cutoff = timestamp - RETENTION_SECONDS
        snapshot = json.dumps(
            {
                "old": sorted(old_counter.items()),
                "new": sorted(new_counter.items()),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

        async with self._lock:
            await self._connection.execute(
                "DELETE FROM reaction_additions WHERE timestamp < ?",
                (cutoff,),
            )
            for reaction, quantity in additions.items():
                digest = hashlib.sha256(
                    f"{chat_id}:{message_id}:{actor_key}:{timestamp}:{reaction}:{quantity}:{snapshot}".encode("utf-8")
                ).hexdigest()
                await self._connection.execute(
                    """
                    INSERT OR IGNORE INTO reaction_additions(
                        event_key, chat_id, message_id, reaction, quantity, timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (digest, chat_id, message_id, reaction, int(quantity), timestamp),
                )
            await self._connection.commit()

    async def snapshot(self, chat_id: int, start_ts: int, end_ts: int) -> StatsSnapshot:
        assert self._connection is not None
        async with self._lock:
            async with self._connection.execute(
                """
                SELECT
                    COUNT(CASE WHEN kind = 'text' THEN 1 END) AS text_messages,
                    COUNT(CASE WHEN kind = 'sticker' THEN 1 END) AS stickers,
                    COALESCE(SUM(CASE WHEN kind = 'text' THEN emoji_count ELSE 0 END), 0) AS emoji_count,
                    COALESCE(SUM(CASE WHEN kind = 'text' AND emoji_count > 0 THEN 1 ELSE 0 END), 0) AS emoji_messages,
                    COALESCE(SUM(CASE WHEN kind = 'text' AND emoji_only = 1 THEN 1 ELSE 0 END), 0) AS emoji_only_messages
                FROM tracked_messages
                WHERE chat_id = ? AND timestamp >= ? AND timestamp <= ?
                """,
                (chat_id, start_ts, end_ts),
            ) as cursor:
                totals = await cursor.fetchone()

            async with self._connection.execute(
                """
                SELECT username, full_name, COUNT(*) AS message_count
                FROM tracked_messages
                WHERE chat_id = ? AND kind = 'text' AND timestamp >= ? AND timestamp <= ?
                GROUP BY user_id
                ORDER BY message_count DESC, full_name COLLATE NOCASE ASC
                LIMIT 5
                """,
                (chat_id, start_ts, end_ts),
            ) as cursor:
                participant_rows = await cursor.fetchall()

            async with self._connection.execute(
                """
                SELECT COALESCE(SUM(quantity), 0) AS reactions_added
                FROM reaction_additions
                WHERE chat_id = ? AND timestamp >= ? AND timestamp <= ?
                """,
                (chat_id, start_ts, end_ts),
            ) as cursor:
                reaction_total = await cursor.fetchone()

            async with self._connection.execute(
                """
                SELECT reaction, SUM(quantity) AS total
                FROM reaction_additions
                WHERE chat_id = ? AND timestamp >= ? AND timestamp <= ?
                GROUP BY reaction
                ORDER BY total DESC, reaction ASC
                LIMIT 3
                """,
                (chat_id, start_ts, end_ts),
            ) as cursor:
                reaction_rows = await cursor.fetchall()

            async with self._connection.execute(
                """
                SELECT MIN(timestamp) AS tracking_started_at
                FROM (
                    SELECT timestamp FROM tracked_messages WHERE chat_id = ?
                    UNION ALL
                    SELECT timestamp FROM reaction_additions WHERE chat_id = ?
                )
                """,
                (chat_id, chat_id),
            ) as cursor:
                tracking_row = await cursor.fetchone()

        return StatsSnapshot(
            text_messages=int(totals["text_messages"] or 0),
            stickers=int(totals["stickers"] or 0),
            emoji_count=int(totals["emoji_count"] or 0),
            emoji_messages=int(totals["emoji_messages"] or 0),
            emoji_only_messages=int(totals["emoji_only_messages"] or 0),
            reactions_added=int(reaction_total["reactions_added"] or 0),
            participants=tuple(
                Participant(
                    username=row["username"] or "",
                    full_name=row["full_name"] or "",
                    message_count=int(row["message_count"]),
                )
                for row in participant_rows
            ),
            top_reactions=tuple(
                (row["reaction"] or "unknown", int(row["total"]))
                for row in reaction_rows
            ),
            tracking_started_at=(
                int(tracking_row["tracking_started_at"])
                if tracking_row and tracking_row["tracking_started_at"] is not None
                else None
            ),
        )


class AccurateStatsService:
    def __init__(self, app: Any, storage: AccurateStatsStorage, timezone_name: str) -> None:
        self.app = app
        self.storage = storage
        self.tz = ZoneInfo(timezone_name)

    def _local_time(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(self.tz)

    def _day_bounds(self, command_date: datetime) -> tuple[int, int, datetime]:
        local_now = self._local_time(command_date)
        local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(local_start.timestamp()), int(local_now.timestamp()), local_now

    async def track_message(self, message: Message) -> None:
        if not self.app.is_group_chat(message) or not self.app.is_allowed_chat(message.chat.id):
            return
        if message.from_user is None or message.from_user.is_bot:
            return
        if message.from_user.id in self.app.pending_users:
            return

        content = message.text or message.caption or ""
        if content.startswith("/"):
            return

        if message.sticker is not None:
            await self.storage.add_message(
                chat_id=message.chat.id,
                message_id=message.message_id,
                user_id=message.from_user.id,
                username=message.from_user.username or "",
                full_name=message.from_user.full_name or str(message.from_user.id),
                kind="sticker",
                emoji_count=0,
                emoji_only=False,
                timestamp=int(message.date.timestamp()),
            )
            return

        if not content:
            return

        emojis = self.app.extract_emojis(content)
        await self.storage.add_message(
            chat_id=message.chat.id,
            message_id=message.message_id,
            user_id=message.from_user.id,
            username=message.from_user.username or "",
            full_name=message.from_user.full_name or str(message.from_user.id),
            kind="text",
            emoji_count=len(emojis),
            emoji_only=self.app.is_emoji_only_text(content),
            timestamp=int(message.date.timestamp()),
        )

    async def track_reaction(self, update: MessageReactionUpdated) -> None:
        if update.chat.type not in self.app.GROUP_CHAT_TYPES or not self.app.is_allowed_chat(update.chat.id):
            return

        actor = getattr(update, "user", None) or getattr(update, "actor_chat", None)
        actor_id = getattr(actor, "id", "unknown")
        actor_kind = "user" if getattr(update, "user", None) is not None else "chat"
        old_reactions = tuple(self.app.reaction_key(item) for item in update.old_reaction)
        new_reactions = tuple(self.app.reaction_key(item) for item in update.new_reaction)
        await self.storage.add_reaction_additions(
            chat_id=update.chat.id,
            message_id=update.message_id,
            actor_key=f"{actor_kind}:{actor_id}",
            old_reactions=old_reactions,
            new_reactions=new_reactions,
            timestamp=int(update.date.timestamp()),
        )

    async def build_stats_text(self, chat_id: int, command_date: datetime) -> str:
        start_ts, end_ts, local_now = self._day_bounds(command_date)
        snapshot = await self.storage.snapshot(chat_id, start_ts, end_ts)
        period = f"00:00–{local_now:%H:%M} {local_now.tzname() or 'МСК'}"
        date_label = self.app.format_russian_datetime(local_now).split(" в ")[0]
        total_activity = snapshot.text_messages + snapshot.stickers + snapshot.reactions_added

        if total_activity == 0:
            lines = [f"📊 За {date_label}, {period} активности пока нет."]
        else:
            lines = [
                f"📊 Статистика за {date_label}",
                f"Период: <b>{period}</b>",
                f"Сообщений с текстом: <b>{snapshot.text_messages}</b>",
                f"Стикеров: <b>{snapshot.stickers}</b>",
                f"Эмодзи в текстах: <b>{snapshot.emoji_count}</b>",
                f"Сообщений с эмодзи: <b>{snapshot.emoji_messages}</b>",
                f"Сообщений только из эмодзи: <b>{snapshot.emoji_only_messages}</b>",
                f"Добавлено реакций: <b>{snapshot.reactions_added}</b>",
                "Самые активные участники:",
            ]
            if snapshot.participants:
                for participant in snapshot.participants:
                    name = participant.username or participant.full_name or "Пользователь"
                    lines.append(
                        f"— {self.app.safe_output_text(name)}: {participant.message_count}"
                    )
            else:
                lines.append("— Пока нет сообщений с текстом")

            if snapshot.top_reactions:
                lines.append("Топ добавленных реакций:")
                for reaction, count in snapshot.top_reactions:
                    lines.append(f"— {self.app.reaction_label(reaction)}: {count}")

        if snapshot.tracking_started_at is not None:
            started = datetime.fromtimestamp(snapshot.tracking_started_at, tz=self.tz)
            lines.extend(("", f"Точный учет ведется с {started:%d.%m.%Y %H:%M} {started.tzname() or 'МСК'}."))
        return "\n".join(lines)


class MessageTrackingMiddleware(BaseMiddleware):
    def __init__(self, service: AccurateStatsService) -> None:
        self.service = service

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        try:
            await self.service.track_message(event)
        except Exception:
            LOGGER.exception("Could not track message statistics chat_id=%s", event.chat.id)
        return await handler(event, data)


class ReactionTrackingMiddleware(BaseMiddleware):
    def __init__(self, service: AccurateStatsService) -> None:
        self.service = service

    async def __call__(
        self,
        handler: Callable[[MessageReactionUpdated, dict[str, Any]], Awaitable[Any]],
        event: MessageReactionUpdated,
        data: dict[str, Any],
    ) -> Any:
        try:
            await self.service.track_reaction(event)
        except Exception:
            LOGGER.exception("Could not track reaction statistics chat_id=%s", event.chat.id)
        return await handler(event, data)


def register_accurate_stats_handlers(app: Any, service: AccurateStatsService) -> None:
    dispatcher = app.dp
    dispatcher.message.outer_middleware(MessageTrackingMiddleware(service))
    dispatcher.message_reaction.outer_middleware(ReactionTrackingMiddleware(service))

    @dispatcher.message(Command("stats"))
    async def accurate_stats_command(message: Message) -> None:
        if not app.is_group_chat(message) or not app.is_allowed_chat(message.chat.id):
            return
        try:
            text = await service.build_stats_text(message.chat.id, message.date)
        except Exception:
            LOGGER.exception("Could not build accurate stats chat_id=%s", message.chat.id)
            await message.reply("⚠️ Не удалось собрать статистику. Попробуй еще раз через минуту.")
            return
        await message.reply(text)
        LOGGER.info("Accurate stats completed chat_id=%s message_id=%s", message.chat.id, message.message_id)

    stats_handler = dispatcher.message.handlers.pop()
    dispatcher.message.handlers.insert(0, stats_handler)
    print("ACCURATE_STATS_READY storage=persistent message_deduplication=on", flush=True)
