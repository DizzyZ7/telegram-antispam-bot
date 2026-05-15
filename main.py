import asyncio
import logging
import os
import random
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import aiosqlite
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ChatType, ParseMode
from aiogram.filters import ChatMemberUpdatedFilter, Command, IS_MEMBER, IS_NOT_MEMBER
from aiogram.types import (
    ChatMemberUpdated,
    ChatPermissions,
    Message,
    MessageReactionUpdated,
    ReactionTypeCustomEmoji,
    ReactionTypeEmoji,
    ReactionTypePaid,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in environment variables")

DEFAULT_ALLOWED_CHATS = [-1002619489118, -1003237014529, -1003643412493]
SUMMARY_STORAGE_PATH = os.getenv("SUMMARY_STORAGE_PATH", "daily_summary.db")
SUMMARY_DEFAULT_TIME = os.getenv("SUMMARY_DEFAULT_TIME", "23:59")
SUMMARY_TIMEZONE = os.getenv("SUMMARY_TIMEZONE", "Europe/Moscow")
SUMMARY_MIN_MESSAGES = int(os.getenv("SUMMARY_MIN_MESSAGES", "12"))
MESSAGE_TTL_SECONDS = 24 * 60 * 60


def parse_allowed_chats() -> list[int]:
    raw = os.getenv("ALLOWED_CHATS")
    if not raw:
        return DEFAULT_ALLOWED_CHATS

    values = []
    for item in raw.replace(";", ",").split(","):
        chunk = item.strip()
        if not chunk:
            continue
        values.append(int(chunk))
    return values


ALLOWED_CHATS = parse_allowed_chats()
GROUP_CHAT_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()

pending_users = {}
passed_users = set()
failed_users = set()

storage = None
summary_service = None
scheduler_service = None

STOP_WORDS = {
    "это",
    "как",
    "что",
    "чтобы",
    "или",
    "для",
    "когда",
    "где",
    "почему",
    "который",
    "которая",
    "которые",
    "если",
    "потом",
    "пока",
    "сегодня",
    "вчера",
    "завтра",
    "просто",
    "очень",
    "будет",
    "быть",
    "можно",
    "нужно",
    "надо",
    "тоже",
    "еще",
    "ещё",
    "также",
    "через",
    "этот",
    "эта",
    "эти",
    "того",
    "того",
    "про",
    "после",
    "перед",
    "под",
    "над",
    "при",
    "без",
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "have",
    "has",
    "will",
    "would",
    "can",
    "could",
    "just",
    "about",
    "you",
    "your",
    "our",
    "they",
    "them",
}
TASK_CUES = ("нужно", "надо", "сделать", "добавить", "проверить", "починить", "todo", "задача")
DECISION_CUES = ("решили", "договорились", "итог", "будем", "приняли", "выбрали", "согласовали")
POSITIVE_WORDS = {"хорошо", "отлично", "супер", "класс", "спасибо", "done", "ok", "готово"}
NEGATIVE_WORDS = {"плохо", "ошибка", "сломалось", "проблема", "критично", "bug", "fail"}
ANSWER_CUES = ("сделаю", "сделал", "готово", "да", "нет", "потому", "проверил", "исправил")
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "]"
)


def user_tag(user):
    if user.username:
        return f"@{user.username}"
    return f"<a href='tg://user?id={user.id}'>{user.full_name}</a>"


def build_captcha(user_id):
    a = random.randint(1, 9)
    b = random.randint(1, 9)
    answer = a + b

    options = list({answer, answer + 1, answer - 1, answer + 2})
    random.shuffle(options)

    kb = InlineKeyboardBuilder()
    for opt in options:
        kb.button(
            text=str(opt),
            callback_data=f"captcha:{user_id}:{opt}",
        )
    kb.adjust(len(options))

    return f"{a} + {b} = ?", answer, kb.as_markup()


def is_group_chat(message: Message) -> bool:
    return message.chat.type in GROUP_CHAT_TYPES


def is_allowed_chat(chat_id: int) -> bool:
    return chat_id in ALLOWED_CHATS


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


def compact_line(text: str, limit: int = 120) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: limit - 1]}…"


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-zА-Яа-я0-9_]{3,}", text.lower())
    return [token for token in tokens if token not in STOP_WORDS and not token.isdigit()]


def extract_emojis(text: str) -> list[str]:
    return EMOJI_PATTERN.findall(text)


def is_emoji_only_text(text: str) -> bool:
    if not text.strip():
        return False
    if not extract_emojis(text):
        return False
    normalized = re.sub(r"[\s\u200d\ufe0f]", "", text)
    normalized = EMOJI_PATTERN.sub("", normalized)
    return normalized == ""


def reaction_key(reaction: object) -> str:
    if isinstance(reaction, ReactionTypeEmoji):
        return reaction.emoji
    if isinstance(reaction, ReactionTypeCustomEmoji):
        return f"custom:{reaction.custom_emoji_id}"
    if isinstance(reaction, ReactionTypePaid):
        return "paid"
    return "unknown"


def reaction_label(value: str) -> str:
    if value.startswith("custom:"):
        return "custom_emoji"
    if value == "paid":
        return "paid"
    if value == "unknown":
        return "unknown"
    return value


@dataclass(slots=True)
class StoredMessage:
    chat_id: int
    user_id: int
    username: str
    full_name: str
    text: str
    timestamp: int


@dataclass(slots=True)
class ParticipantStat:
    user_id: int
    username: str
    full_name: str
    message_count: int


@dataclass(slots=True)
class ChatSetting:
    chat_id: int
    summary_time: str
    enabled: bool
    last_sent_date: str | None


@dataclass(slots=True)
class DayEngagementStat:
    sticker_count: int
    reaction_count: int
    emoji_count: int
    emoji_messages: int
    emoji_only_messages: int
    top_reactions: list[tuple[str, int]]


class SummaryStorage:
    def __init__(self, db_path: str, ttl_seconds: int) -> None:
        self._db_path = db_path
        self._ttl_seconds = ttl_seconds
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA synchronous=NORMAL;")
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                full_name TEXT NOT NULL,
                text TEXT NOT NULL,
                timestamp INTEGER NOT NULL
            )
            """
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_chat_time ON messages(chat_id, timestamp)"
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_time ON messages(timestamp)"
        )
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER,
                event_type TEXT NOT NULL,
                event_value TEXT,
                quantity INTEGER NOT NULL,
                timestamp INTEGER NOT NULL
            )
            """
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_chat_time ON events(chat_id, timestamp)"
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_time ON events(timestamp)"
        )
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id INTEGER PRIMARY KEY,
                summary_time TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                last_sent_date TEXT
            )
            """
        )
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is None:
            return
        await self._conn.close()
        self._conn = None

    async def ensure_chat_setting(self, chat_id: int, summary_time: str) -> None:
        assert self._conn is not None
        async with self._lock:
            await self._conn.execute(
                """
                INSERT INTO chat_settings(chat_id, summary_time, enabled, last_sent_date)
                VALUES(?, ?, 1, NULL)
                ON CONFLICT(chat_id) DO NOTHING
                """,
                (chat_id, summary_time),
            )
            await self._conn.commit()

    async def set_chat_setting(self, chat_id: int, summary_time: str, enabled: bool) -> None:
        assert self._conn is not None
        async with self._lock:
            await self._conn.execute(
                """
                INSERT INTO chat_settings(chat_id, summary_time, enabled, last_sent_date)
                VALUES(?, ?, ?, NULL)
                ON CONFLICT(chat_id) DO UPDATE SET
                    summary_time=excluded.summary_time,
                    enabled=excluded.enabled
                """,
                (chat_id, summary_time, int(enabled)),
            )
            await self._conn.commit()

    async def get_chat_setting(self, chat_id: int, default_time: str) -> ChatSetting:
        assert self._conn is not None
        async with self._lock:
            async with self._conn.execute(
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
        assert self._conn is not None
        async with self._lock:
            async with self._conn.execute(
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
        assert self._conn is not None
        async with self._lock:
            await self._conn.execute(
                "UPDATE chat_settings SET last_sent_date = ? WHERE chat_id = ?",
                (sent_date, chat_id),
            )
            await self._conn.commit()

    async def add_message(
        self,
        chat_id: int,
        user_id: int,
        username: str,
        full_name: str,
        text: str,
        timestamp: int,
    ) -> None:
        assert self._conn is not None
        cutoff = timestamp - self._ttl_seconds
        async with self._lock:
            await self._conn.execute("DELETE FROM messages WHERE timestamp < ?", (cutoff,))
            await self._conn.execute(
                """
                INSERT INTO messages(chat_id, user_id, username, full_name, text, timestamp)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (chat_id, user_id, username, full_name, text, timestamp),
            )
            await self._conn.commit()

    async def add_event(
        self,
        chat_id: int,
        user_id: int | None,
        event_type: str,
        event_value: str | None,
        quantity: int,
        timestamp: int,
    ) -> None:
        if quantity <= 0:
            return

        assert self._conn is not None
        cutoff = timestamp - self._ttl_seconds
        async with self._lock:
            await self._conn.execute("DELETE FROM messages WHERE timestamp < ?", (cutoff,))
            await self._conn.execute("DELETE FROM events WHERE timestamp < ?", (cutoff,))
            await self._conn.execute(
                """
                INSERT INTO events(chat_id, user_id, event_type, event_value, quantity, timestamp)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (chat_id, user_id, event_type, event_value, quantity, timestamp),
            )
            await self._conn.commit()

    async def add_sticker_event(self, chat_id: int, user_id: int, sticker_emoji: str, timestamp: int) -> None:
        await self.add_event(
            chat_id=chat_id,
            user_id=user_id,
            event_type="sticker",
            event_value=sticker_emoji,
            quantity=1,
            timestamp=timestamp,
        )

    async def add_reaction_event(self, chat_id: int, reaction: str, quantity: int, timestamp: int) -> None:
        await self.add_event(
            chat_id=chat_id,
            user_id=None,
            event_type="reaction",
            event_value=reaction,
            quantity=quantity,
            timestamp=timestamp,
        )

    async def clear_chat_messages(self, chat_id: int) -> None:
        assert self._conn is not None
        async with self._lock:
            await self._conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
            await self._conn.execute("DELETE FROM events WHERE chat_id = ?", (chat_id,))
            await self._conn.commit()

    async def prune_expired_messages(self, now_ts: int) -> None:
        assert self._conn is not None
        cutoff = now_ts - self._ttl_seconds
        async with self._lock:
            await self._conn.execute("DELETE FROM messages WHERE timestamp < ?", (cutoff,))
            await self._conn.execute("DELETE FROM events WHERE timestamp < ?", (cutoff,))
            await self._conn.commit()

    async def get_messages_between(self, chat_id: int, start_ts: int, end_ts: int) -> list[StoredMessage]:
        assert self._conn is not None
        async with self._lock:
            async with self._conn.execute(
                """
                SELECT chat_id, user_id, username, full_name, text, timestamp
                FROM messages
                WHERE chat_id = ? AND timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp ASC
                """,
                (chat_id, start_ts, end_ts),
            ) as cursor:
                rows = await cursor.fetchall()

        return [
            StoredMessage(
                chat_id=row["chat_id"],
                user_id=row["user_id"],
                username=row["username"],
                full_name=row["full_name"],
                text=row["text"],
                timestamp=row["timestamp"],
            )
            for row in rows
        ]

    async def get_top_participants(self, chat_id: int, start_ts: int, end_ts: int, limit: int = 5) -> list[ParticipantStat]:
        assert self._conn is not None
        async with self._lock:
            async with self._conn.execute(
                """
                SELECT user_id, username, full_name, COUNT(*) AS message_count
                FROM messages
                WHERE chat_id = ? AND timestamp >= ? AND timestamp <= ?
                GROUP BY user_id
                ORDER BY message_count DESC
                LIMIT ?
                """,
                (chat_id, start_ts, end_ts, limit),
            ) as cursor:
                rows = await cursor.fetchall()

        return [
            ParticipantStat(
                user_id=row["user_id"],
                username=row["username"],
                full_name=row["full_name"],
                message_count=row["message_count"],
            )
            for row in rows
        ]

    async def get_engagement_events(
        self,
        chat_id: int,
        start_ts: int,
        end_ts: int,
    ) -> tuple[int, int, list[tuple[str, int]]]:
        assert self._conn is not None
        async with self._lock:
            async with self._conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN event_type = 'sticker' THEN quantity ELSE 0 END), 0) AS sticker_count,
                    COALESCE(SUM(CASE WHEN event_type = 'reaction' THEN quantity ELSE 0 END), 0) AS reaction_count
                FROM events
                WHERE chat_id = ? AND timestamp >= ? AND timestamp <= ?
                """,
                (chat_id, start_ts, end_ts),
            ) as cursor:
                totals_row = await cursor.fetchone()

            async with self._conn.execute(
                """
                SELECT event_value, SUM(quantity) AS total
                FROM events
                WHERE chat_id = ? AND timestamp >= ? AND timestamp <= ? AND event_type = 'reaction'
                GROUP BY event_value
                ORDER BY total DESC
                LIMIT 5
                """,
                (chat_id, start_ts, end_ts),
            ) as cursor:
                reaction_rows = await cursor.fetchall()

        sticker_count = int(totals_row["sticker_count"]) if totals_row else 0
        reaction_count = int(totals_row["reaction_count"]) if totals_row else 0
        top_reactions = [(row["event_value"] or "unknown", int(row["total"])) for row in reaction_rows]
        return sticker_count, reaction_count, top_reactions


class DailySummaryService:
    def __init__(self, storage_backend: SummaryStorage, tz: ZoneInfo, min_messages: int) -> None:
        self.storage = storage_backend
        self.tz = tz
        self.min_messages = min_messages

    def _day_bounds(self) -> tuple[int, int]:
        now_local = datetime.now(self.tz)
        day_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(day_start_local.timestamp()), int(now_local.timestamp())

    @staticmethod
    def _display_name(stat: ParticipantStat) -> str:
        if stat.username:
            return f"@{stat.username}"
        return stat.full_name or str(stat.user_id)

    def _build_emoji_stats(self, messages: list[StoredMessage]) -> tuple[int, int, int]:
        emoji_total = 0
        emoji_messages = 0
        emoji_only_messages = 0

        for item in messages:
            emojis = extract_emojis(item.text)
            if not emojis:
                continue
            emoji_messages += 1
            emoji_total += len(emojis)
            if is_emoji_only_text(item.text):
                emoji_only_messages += 1
        return emoji_total, emoji_messages, emoji_only_messages

    async def _collect_day_data(
        self,
        chat_id: int,
    ) -> tuple[list[StoredMessage], list[ParticipantStat], DayEngagementStat]:
        start_ts, end_ts = self._day_bounds()
        messages = await self.storage.get_messages_between(chat_id, start_ts, end_ts)
        participants = await self.storage.get_top_participants(chat_id, start_ts, end_ts, limit=5)
        sticker_count, reaction_count, top_reactions = await self.storage.get_engagement_events(
            chat_id, start_ts, end_ts
        )
        emoji_count, emoji_messages, emoji_only_messages = self._build_emoji_stats(messages)
        engagement = DayEngagementStat(
            sticker_count=sticker_count,
            reaction_count=reaction_count,
            emoji_count=emoji_count,
            emoji_messages=emoji_messages,
            emoji_only_messages=emoji_only_messages,
            top_reactions=top_reactions,
        )
        return messages, participants, engagement

    def _extract_topics(self, messages: list[StoredMessage], limit: int = 3) -> list[str]:
        tokens = []
        for item in messages:
            tokens.extend(tokenize(item.text))
        if not tokens:
            return []
        freq = Counter(tokens)
        topics = []
        for token, _ in freq.most_common(limit):
            topics.append(token.capitalize())
        return topics

    def _extract_key_points(self, messages: list[StoredMessage], limit: int = 3) -> list[str]:
        points = []
        for item in messages:
            text = compact_line(item.text)
            lower = text.lower()
            if any(cue in lower for cue in DECISION_CUES):
                points.append(text)
                continue
            if any(cue in lower for cue in TASK_CUES):
                points.append(text)
                continue
            if len(text.split()) >= 8:
                points.append(text)
        unique = []
        seen = set()
        for point in points:
            key = point.lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(point)
            if len(unique) >= limit:
                break
        return unique

    def _extract_tasks_and_agreements(self, messages: list[StoredMessage], limit: int = 3) -> list[str]:
        items = []
        for message in messages:
            text = compact_line(message.text)
            lower = text.lower()
            if any(cue in lower for cue in TASK_CUES) or any(cue in lower for cue in DECISION_CUES):
                items.append(text)

        unique = []
        seen = set()
        for item in items:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
            if len(unique) >= limit:
                break
        return unique

    def _is_question_answered(self, messages: list[StoredMessage], idx: int) -> bool:
        source = messages[idx]
        for next_message in messages[idx + 1 : idx + 9]:
            if next_message.user_id == source.user_id:
                continue
            candidate = next_message.text.strip().lower()
            if not candidate:
                continue
            if "?" not in candidate:
                return True
            if any(cue in candidate for cue in ANSWER_CUES):
                return True
        return False

    def _extract_open_questions(self, messages: list[StoredMessage], limit: int = 3) -> list[str]:
        questions = []
        for idx, item in enumerate(messages):
            text = item.text.strip()
            if "?" not in text:
                continue
            if self._is_question_answered(messages, idx):
                continue
            questions.append(compact_line(text))

        unique = []
        seen = set()
        for question in questions:
            key = question.lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(question)
            if len(unique) >= limit:
                break
        return unique

    def _tone(self, messages: list[StoredMessage]) -> str:
        pos = 0
        neg = 0
        for item in messages:
            lower = item.text.lower()
            pos += sum(1 for token in POSITIVE_WORDS if token in lower)
            neg += sum(1 for token in NEGATIVE_WORDS if token in lower)

        score = pos - neg
        if score >= 2:
            mood = "в целом позитивным и рабочим"
        elif score <= -2:
            mood = "напряженным, с акцентом на проблемы"
        else:
            mood = "нейтральным и рабочим"

        volume = len(messages)
        if volume >= 120:
            activity = "Обсуждение было очень активным."
        elif volume >= 40:
            activity = "Обсуждение было активным."
        else:
            activity = "Обсуждение было спокойным."
        return f"В целом тон был {mood}. {activity}"

    async def build_stats_text(self, chat_id: int) -> str:
        messages, participants, engagement = await self._collect_day_data(chat_id)
        text_total = len(messages)
        total_activity = text_total + engagement.sticker_count + engagement.reaction_count

        if total_activity == 0:
            return "📊 За сегодня в чате пока нет данных активности."

        lines = [
            "📊 Краткая статистика за сегодня:",
            f"Текстовых сообщений: <b>{text_total}</b>",
            f"Стикеров: <b>{engagement.sticker_count}</b>",
            f"Эмодзи в текстах: <b>{engagement.emoji_count}</b>",
            f"Сообщений с эмодзи: <b>{engagement.emoji_messages}</b>",
            f"Сообщений только из эмодзи: <b>{engagement.emoji_only_messages}</b>",
            f"Реакций: <b>{engagement.reaction_count}</b>",
            "Самые активные участники:",
        ]

        if participants:
            for stat in participants:
                lines.append(f"— {self._display_name(stat)}: {stat.message_count}")
        else:
            lines.append("— Нет данных")

        if engagement.top_reactions:
            lines.append("Топ реакций:")
            for reaction, count in engagement.top_reactions[:3]:
                lines.append(f"— {reaction_label(reaction)}: {count}")

        if text_total < self.min_messages:
            lines.append("")
            lines.append("Сегодня в чате было мало сообщений для полноценной аналитики.")
        return "\n".join(lines)

    async def build_summary_text(self, chat_id: int) -> str:
        messages, participants, engagement = await self._collect_day_data(chat_id)
        text_total = len(messages)
        activity_total = text_total + engagement.sticker_count + engagement.reaction_count

        if text_total < self.min_messages and activity_total < self.min_messages:
            return "Сегодня в чате было мало сообщений для полноценной аналитики."

        topics = self._extract_topics(messages, limit=3)
        key_points = self._extract_key_points(messages, limit=3)
        tasks_and_agreements = self._extract_tasks_and_agreements(messages, limit=3)
        open_questions = self._extract_open_questions(messages, limit=3)
        tone = self._tone(messages)

        lines = ["Итоги дня в чате:", ""]
        lines.append("Сегодня в чате больше всего обсуждали:")
        if topics:
            for idx, topic in enumerate(topics, start=1):
                lines.append(f"{idx}. {topic}")
        else:
            lines.append("1. Недостаточно данных для устойчивого выделения тем")

        lines.extend(
            [
                "",
                "Активность:",
                f"Текстовых сообщений: {text_total}",
                f"Стикеров: {engagement.sticker_count}",
                f"Эмодзи в текстах: {engagement.emoji_count}",
                f"Сообщений только из эмодзи: {engagement.emoji_only_messages}",
                f"Реакций: {engagement.reaction_count}",
                "Самые активные участники:",
            ]
        )
        if participants:
            for stat in participants:
                lines.append(f"— {self._display_name(stat)}: {stat.message_count} сообщений")
        else:
            lines.append("— Нет данных")

        if engagement.top_reactions:
            lines.append("Топ реакций:")
            for reaction, count in engagement.top_reactions[:3]:
                lines.append(f"— {reaction_label(reaction)}: {count}")

        lines.extend(["", "Важные моменты:"])
        if key_points:
            for item in key_points:
                lines.append(f"— {item}")
        else:
            lines.append("— Явные важные решения или идеи не выделены")

        lines.extend(["", "Задачи и договоренности:"])
        if tasks_and_agreements:
            for item in tasks_and_agreements:
                lines.append(f"— {item}")
        else:
            lines.append("— Явные задачи или договоренности не зафиксированы")

        lines.extend(["", "Открытые вопросы:"])
        if open_questions:
            for question in open_questions:
                lines.append(f"— {question}")
        else:
            lines.append("— Критичных незакрытых вопросов не найдено")

        lines.extend(["", "Тон общения:", tone])
        return "\n".join(lines)


class SchedulerService:
    def __init__(
        self,
        tg_bot: Bot,
        storage_backend: SummaryStorage,
        summary_backend: DailySummaryService,
        tz: ZoneInfo,
    ) -> None:
        self.bot = tg_bot
        self.storage = storage_backend
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

            summary_text = await self.summary.build_summary_text(setting.chat_id)
            try:
                await self.bot.send_message(setting.chat_id, summary_text)
            except Exception:
                logging.exception("Failed to send daily summary for chat %s", setting.chat_id)
                continue

            await self.storage.mark_summary_sent(setting.chat_id, today_str)
            await self.storage.clear_chat_messages(setting.chat_id)


async def is_chat_admin(chat_id: int, user_id: int) -> bool:
    member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
    return member.status in {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}


async def handle_pending_user_message(message: Message) -> bool:
    if message.from_user is None:
        return False
    if message.from_user.id not in pending_users:
        return False
    try:
        await message.delete()
    except Exception:
        pass
    return True


@dp.chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
async def on_user_join(event: ChatMemberUpdated):
    chat_id = event.chat.id
    if not is_allowed_chat(chat_id):
        return

    user = event.new_chat_member.user
    if user.id in passed_users:
        return

    question, answer, keyboard = build_captcha(user.id)
    pending_users[user.id] = answer

    await bot.restrict_chat_member(
        chat_id,
        user.id,
        ChatPermissions(can_send_messages=False),
    )

    await bot.send_message(
        chat_id,
        f"👋 <b>{user.full_name}</b>, чтобы получить доступ к чату, реши капчу:\n\n<b>{question}</b>",
        reply_markup=keyboard,
    )


@dp.callback_query(F.data.startswith("captcha:"))
async def captcha_handler(callback):
    if callback.message is None:
        return
    chat_id = callback.message.chat.id
    if not is_allowed_chat(chat_id):
        return

    _, target_user_id, value = callback.data.split(":")
    target_user_id = int(target_user_id)
    value = int(value)

    if callback.from_user.id != target_user_id:
        await callback.answer("Это не твоя проверка", show_alert=True)
        return

    if target_user_id not in pending_users:
        await callback.answer("Проверка уже завершена")
        return

    if value == pending_users[target_user_id]:
        pending_users.pop(target_user_id, None)
        passed_users.add(target_user_id)
        failed_users.discard(target_user_id)

        await bot.restrict_chat_member(
            chat_id,
            target_user_id,
            ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        )

        try:
            await callback.message.delete()
        except Exception:
            pass

        await bot.send_message(chat_id, f"✅ {user_tag(callback.from_user)} прошел испытание")
        await callback.answer("Испытание пройдено")
    else:
        failed_users.add(target_user_id)
        await callback.answer("❌ Неверно", show_alert=True)


@dp.message(Command("captcha_stats"))
async def captcha_stats_cmd(message: Message):
    if not is_group_chat(message) or not is_allowed_chat(message.chat.id):
        return

    await message.reply(
        f"📊 Статистика антиспама:\n"
        f"⏳ Ожидают: <b>{len(pending_users)}</b>\n"
        f"✅ Прошли испытание: <b>{len(passed_users)}</b>\n"
        f"❌ Были ошибки: <b>{len(failed_users)}</b>"
    )


@dp.message(Command("stats"))
async def stats_cmd(message: Message):
    if not is_group_chat(message) or not is_allowed_chat(message.chat.id):
        return

    assert summary_service is not None
    text = await summary_service.build_stats_text(message.chat.id)
    await message.reply(text)


@dp.message(Command(commands=["summary", "today"]))
async def summary_cmd(message: Message):
    if not is_group_chat(message) or not is_allowed_chat(message.chat.id):
        return

    assert summary_service is not None
    text = await summary_service.build_summary_text(message.chat.id)
    await message.reply(text)


@dp.message(Command("reset_stats"))
async def reset_stats_cmd(message: Message):
    if not is_group_chat(message) or not is_allowed_chat(message.chat.id):
        return
    if message.from_user is None:
        return

    if not await is_chat_admin(message.chat.id, message.from_user.id):
        await message.reply("Команда доступна только администраторам чата.")
        return

    assert storage is not None
    await storage.clear_chat_messages(message.chat.id)
    await message.reply("Статистика по текущему чату очищена.")


@dp.message(Command("summary_settings"))
async def summary_settings_cmd(message: Message):
    if not is_group_chat(message) or not is_allowed_chat(message.chat.id):
        return
    if message.from_user is None:
        return

    if not await is_chat_admin(message.chat.id, message.from_user.id):
        await message.reply("Команда доступна только администраторам чата.")
        return

    assert storage is not None
    setting = await storage.get_chat_setting(message.chat.id, SUMMARY_DEFAULT_TIME)

    parts = message.text.split(maxsplit=1) if message.text else ["/summary_settings"]
    if len(parts) == 1:
        status = "включена" if setting.enabled else "выключена"
        await message.reply(
            f"Текущие настройки сводки:\n"
            f"Статус: <b>{status}</b>\n"
            f"Время: <b>{setting.summary_time}</b> ({SUMMARY_TIMEZONE})\n\n"
            f"Примеры:\n"
            f"/summary_settings 23:59\n"
            f"/summary_settings off\n"
            f"/summary_settings on"
        )
        return

    value = parts[1].strip().lower()
    if value in {"off", "disable", "0"}:
        await storage.set_chat_setting(message.chat.id, setting.summary_time, enabled=False)
        await message.reply("Автоматическая дневная сводка выключена.")
        return

    if value in {"on", "enable", "1"}:
        await storage.set_chat_setting(message.chat.id, setting.summary_time, enabled=True)
        await message.reply(
            f"Автоматическая дневная сводка включена. Время отправки: <b>{setting.summary_time}</b> ({SUMMARY_TIMEZONE})."
        )
        return

    normalized = normalize_time(value)
    if normalized is None:
        await message.reply("Неверный формат времени. Используйте HH:MM, например 23:59.")
        return

    await storage.set_chat_setting(message.chat.id, normalized, enabled=True)
    await message.reply(
        f"Время автоматической сводки обновлено: <b>{normalized}</b> ({SUMMARY_TIMEZONE})."
    )


@dp.message(F.text)
async def collect_text_messages(message: Message):
    if not is_group_chat(message) or not is_allowed_chat(message.chat.id):
        return
    if message.from_user is None:
        return

    if await handle_pending_user_message(message):
        return

    if not message.text or message.text.startswith("/"):
        return

    assert storage is not None
    timestamp = int(message.date.timestamp())
    username = message.from_user.username or ""
    full_name = message.from_user.full_name or str(message.from_user.id)

    await storage.add_message(
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        username=username,
        full_name=full_name,
        text=message.text,
        timestamp=timestamp,
    )


@dp.message(F.sticker)
async def collect_sticker_messages(message: Message):
    if not is_group_chat(message) or not is_allowed_chat(message.chat.id):
        return
    if message.from_user is None:
        return

    if await handle_pending_user_message(message):
        return

    assert storage is not None
    sticker_emoji = message.sticker.emoji if message.sticker and message.sticker.emoji else "sticker"
    await storage.add_sticker_event(
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        sticker_emoji=sticker_emoji,
        timestamp=int(message.date.timestamp()),
    )


@dp.message_reaction()
async def collect_reaction_updates(update: MessageReactionUpdated):
    chat_id = update.chat.id
    if not is_allowed_chat(chat_id):
        return
    if update.chat.type not in GROUP_CHAT_TYPES:
        return

    old_counter = Counter(reaction_key(item) for item in update.old_reaction)
    new_counter = Counter(reaction_key(item) for item in update.new_reaction)
    delta = new_counter - old_counter
    if not delta:
        return

    assert storage is not None
    timestamp = int(update.date.timestamp())
    for key, count in delta.items():
        await storage.add_reaction_event(
            chat_id=chat_id,
            reaction=key,
            quantity=int(count),
            timestamp=timestamp,
        )


async def setup_services() -> None:
    global storage, summary_service, scheduler_service
    tz = ZoneInfo(SUMMARY_TIMEZONE)

    storage = SummaryStorage(SUMMARY_STORAGE_PATH, MESSAGE_TTL_SECONDS)
    await storage.initialize()
    for chat_id in ALLOWED_CHATS:
        await storage.ensure_chat_setting(chat_id, SUMMARY_DEFAULT_TIME)

    summary_service = DailySummaryService(storage_backend=storage, tz=tz, min_messages=SUMMARY_MIN_MESSAGES)
    scheduler_service = SchedulerService(
        tg_bot=bot,
        storage_backend=storage,
        summary_backend=summary_service,
        tz=tz,
    )
    scheduler_service.start()


async def shutdown_services() -> None:
    if scheduler_service is not None:
        await scheduler_service.shutdown()
    if storage is not None:
        await storage.close()


async def main():
    logging.basicConfig(level=logging.INFO)
    await setup_services()
    allowed_updates = dp.resolve_used_update_types()
    try:
        await dp.start_polling(bot, allowed_updates=allowed_updates)
    finally:
        await shutdown_services()


if __name__ == "__main__":
    asyncio.run(main())
