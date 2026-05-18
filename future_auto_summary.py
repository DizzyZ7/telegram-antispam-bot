"""Archived automatic daily summary feature.

This module is intentionally not imported by main.py. It keeps the removed
end-of-day auto message implementation so it can be restored later without
reconstructing the feature from history.

To restore it:
1. Add apscheduler back to requirements.txt.
2. Wrap SummaryStorage with AutoSummaryStorageAdapter and call initialize().
3. Register the settings handler with register_auto_summary_settings_handler().
4. Start AutoSummarySchedulerService from setup_services() and shut it down
   from shutdown_services().
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Protocol
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler


@dataclass(slots=True)
class ChatSetting:
    chat_id: int
    summary_time: str
    enabled: bool
    last_sent_date: str | None


class DailySummaryBackend(Protocol):
    async def build_summary_text(
        self,
        chat_id: int,
        chat_title: str | None = None,
        chat_username: str | None = None,
    ) -> str:
        ...


def normalize_time(value: str) -> str | None:
    value = value.strip()
    if not re.match(r"^\d{1,2}:\d{2}$", value):
        return None
    hh, mm = value.split(":")
    hour = int(hh)
    minute = int(mm)
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


class AutoSummaryStorageAdapter:
    """Adapter for the old chat_settings storage methods.

    The active SummaryStorage no longer owns these methods because automatic
    sending is disabled. This adapter expects the same _conn/_lock internals
    that SummaryStorage currently uses.
    """

    def __init__(self, storage_backend: Any) -> None:
        self.storage = storage_backend

    async def initialize(self) -> None:
        assert self.storage._conn is not None
        async with self.storage._lock:
            await self.storage._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_settings (
                    chat_id INTEGER PRIMARY KEY,
                    summary_time TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_sent_date TEXT
                )
                """
            )
            await self.storage._conn.commit()

    async def ensure_chat_setting(self, chat_id: int, summary_time: str) -> None:
        assert self.storage._conn is not None
        async with self.storage._lock:
            await self.storage._conn.execute(
                """
                INSERT INTO chat_settings(chat_id, summary_time, enabled, last_sent_date)
                VALUES(?, ?, 1, NULL)
                ON CONFLICT(chat_id) DO NOTHING
                """,
                (chat_id, summary_time),
            )
            await self.storage._conn.commit()

    async def set_chat_setting(self, chat_id: int, summary_time: str, enabled: bool) -> None:
        assert self.storage._conn is not None
        async with self.storage._lock:
            await self.storage._conn.execute(
                """
                INSERT INTO chat_settings(chat_id, summary_time, enabled, last_sent_date)
                VALUES(?, ?, ?, NULL)
                ON CONFLICT(chat_id) DO UPDATE SET
                    summary_time=excluded.summary_time,
                    enabled=excluded.enabled
                """,
                (chat_id, summary_time, int(enabled)),
            )
            await self.storage._conn.commit()

    async def get_chat_setting(self, chat_id: int, default_time: str) -> ChatSetting:
        assert self.storage._conn is not None
        async with self.storage._lock:
            async with self.storage._conn.execute(
                "SELECT chat_id, summary_time, enabled, last_sent_date FROM chat_settings WHERE chat_id = ?",
                (chat_id,),
            ) as cursor:
                row = await cursor.fetchone()

        if row is None:
            return ChatSetting(chat_id=chat_id, summary_time=default_time, enabled=True, last_sent_date=None)
        return ChatSetting(
            chat_id=row["chat_id"],
            summary_time=row["summary_time"],
            enabled=bool(row["enabled"]),
            last_sent_date=row["last_sent_date"],
        )

    async def get_enabled_chat_settings(self) -> list[ChatSetting]:
        assert self.storage._conn is not None
        async with self.storage._lock:
            async with self.storage._conn.execute(
                """
                SELECT chat_id, summary_time, enabled, last_sent_date
                FROM chat_settings
                WHERE enabled = 1
                """
            ) as cursor:
                rows = await cursor.fetchall()

        return [
            ChatSetting(
                chat_id=row["chat_id"],
                summary_time=row["summary_time"],
                enabled=bool(row["enabled"]),
                last_sent_date=row["last_sent_date"],
            )
            for row in rows
        ]

    async def mark_summary_sent(self, chat_id: int, sent_date: str) -> None:
        assert self.storage._conn is not None
        async with self.storage._lock:
            await self.storage._conn.execute(
                "UPDATE chat_settings SET last_sent_date = ? WHERE chat_id = ?",
                (sent_date, chat_id),
            )
            await self.storage._conn.commit()

    async def clear_chat_messages(self, chat_id: int) -> None:
        await self.storage.clear_chat_messages(chat_id)

    async def prune_expired_messages(self, now_ts: int) -> None:
        await self.storage.prune_expired_messages(now_ts)


class AutoSummarySchedulerService:
    def __init__(
        self,
        tg_bot: Bot,
        settings_storage: AutoSummaryStorageAdapter,
        summary_backend: DailySummaryBackend,
        tz: ZoneInfo,
    ) -> None:
        self.bot = tg_bot
        self.storage = settings_storage
        self.summary = summary_backend
        self.tz = tz
        self.scheduler = AsyncIOScheduler(timezone=tz)

    def start(self) -> None:
        self.scheduler.add_job(self._run_auto_summary_tick, "cron", second=0)
        self.scheduler.add_job(self._cleanup_tick, "interval", minutes=30)
        self.scheduler.start()

    async def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    async def _cleanup_tick(self) -> None:
        now_ts = int(datetime.now(self.tz).timestamp())
        await self.storage.prune_expired_messages(now_ts)

    async def _run_auto_summary_tick(self) -> None:
        now_local = datetime.now(self.tz)
        current_hm = now_local.strftime("%H:%M")
        today_str = now_local.date().isoformat()

        settings = await self.storage.get_enabled_chat_settings()
        for setting in settings:
            if setting.summary_time != current_hm:
                continue
            if setting.last_sent_date == today_str:
                continue

            chat_title = None
            chat_username = None
            try:
                chat = await self.bot.get_chat(setting.chat_id)
                chat_title = getattr(chat, "title", None)
                chat_username = getattr(chat, "username", None)
            except Exception:
                logging.exception("Failed to load chat metadata for summary %s", setting.chat_id)

            summary_text = await self.summary.build_summary_text(
                setting.chat_id,
                chat_title=chat_title,
                chat_username=chat_username,
            )
            try:
                await self.bot.send_message(setting.chat_id, summary_text)
            except Exception:
                logging.exception("Failed to send daily summary for chat %s", setting.chat_id)
                continue

            await self.storage.mark_summary_sent(setting.chat_id, today_str)
            await self.storage.clear_chat_messages(setting.chat_id)


def register_auto_summary_settings_handler(
    dp: Dispatcher,
    settings_storage: AutoSummaryStorageAdapter,
    summary_default_time: str,
    summary_timezone: str,
    is_group_chat: Callable[[Message], bool],
    is_allowed_chat: Callable[[int], bool],
    is_chat_admin: Callable[[int, int], Awaitable[bool]],
    normalize_summary_time: Callable[[str], str | None] = normalize_time,
) -> None:
    @dp.message(Command("summary_settings"))
    async def summary_settings_cmd(message: Message) -> None:
        if not is_group_chat(message) or not is_allowed_chat(message.chat.id):
            return
        if message.from_user is None:
            return

        if not await is_chat_admin(message.chat.id, message.from_user.id):
            await message.reply("Команда доступна только администраторам чата.")
            return

        setting = await settings_storage.get_chat_setting(message.chat.id, summary_default_time)

        parts = message.text.split(maxsplit=1) if message.text else ["/summary_settings"]
        if len(parts) == 1:
            status = "включена" if setting.enabled else "выключена"
            await message.reply(
                f"Текущие настройки сводки:\n"
                f"Статус: <b>{status}</b>\n"
                f"Время: <b>{setting.summary_time}</b> ({summary_timezone})\n\n"
                f"Примеры:\n"
                f"/summary_settings 23:59\n"
                f"/summary_settings off\n"
                f"/summary_settings on"
            )
            return

        value = parts[1].strip().lower()
        if value in {"off", "disable", "0"}:
            await settings_storage.set_chat_setting(message.chat.id, setting.summary_time, enabled=False)
            await message.reply("Автоматическая дневная сводка выключена.")
            return

        if value in {"on", "enable", "1"}:
            await settings_storage.set_chat_setting(message.chat.id, setting.summary_time, enabled=True)
            await message.reply(
                f"Автоматическая дневная сводка включена. Время отправки: "
                f"<b>{setting.summary_time}</b> ({summary_timezone})."
            )
            return

        normalized = normalize_summary_time(value)
        if normalized is None:
            await message.reply("Неверный формат времени. Используйте HH:MM, например 23:59.")
            return

        await settings_storage.set_chat_setting(message.chat.id, normalized, enabled=True)
        await message.reply(
            f"Время автоматической сводки обновлено: <b>{normalized}</b> ({summary_timezone})."
        )
