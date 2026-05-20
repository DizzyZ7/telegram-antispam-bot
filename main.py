import asyncio
import logging
import os
import random
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from html import escape as html_escape
from zoneinfo import ZoneInfo

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
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

DEFAULT_ALLOWED_CHATS = [-1002619489118, -1003237014529, -1003643412493, -1003687304800]
SUMMARY_STORAGE_PATH = os.getenv("SUMMARY_STORAGE_PATH", "daily_summary.db")
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

STOP_WORDS = {
    "это",
    "вот",
    "там",
    "тут",
    "так",
    "же",
    "ли",
    "бы",
    "ну",
    "да",
    "нет",
    "ага",
    "ой",
    "уже",
    "все",
    "всё",
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
    "тому",
    "тем",
    "тех",
    "кто",
    "кого",
    "кому",
    "чем",
    "чего",
    "чему",
    "про",
    "после",
    "перед",
    "под",
    "над",
    "при",
    "без",
    "меня",
    "мне",
    "мной",
    "мною",
    "мой",
    "моя",
    "мое",
    "моё",
    "мои",
    "тебя",
    "тебе",
    "тобой",
    "твой",
    "твоя",
    "твое",
    "твоё",
    "твои",
    "вас",
    "вам",
    "вами",
    "ваш",
    "ваша",
    "ваше",
    "ваши",
    "нас",
    "нам",
    "нами",
    "наш",
    "наша",
    "наше",
    "наши",
    "себя",
    "себе",
    "собой",
    "его",
    "ему",
    "ним",
    "ней",
    "нее",
    "неё",
    "она",
    "оно",
    "они",
    "них",
    "ими",
    "сам",
    "сама",
    "сами",
    "само",
    "было",
    "была",
    "были",
    "есть",
    "раз",
    "ладно",
    "короче",
    "вообще",
    "именно",
    "может",
    "куда",
    "сюда",
    "туда",
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
TELEGRAM_MENTION_PATTERN = re.compile(r"(?<![\w/])@([A-Za-z0-9_]{1,32})")
URL_PATTERN = re.compile(r"https?://\S+|t\.me/\S+")
RUSSIAN_MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def remove_telegram_mentions(text: str) -> str:
    return TELEGRAM_MENTION_PATTERN.sub(r"\1", text)


def safe_output_text(text: str) -> str:
    return html_escape(remove_telegram_mentions(text), quote=False)


def format_russian_datetime(value: datetime) -> str:
    month = RUSSIAN_MONTHS[value.month - 1]
    return f"{value.day} {month} {value.year} в {value:%H:%M}"


def ensure_sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    if text[-1] not in ".!?":
        return f"{text}."
    return text


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
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is None:
            return
        await self._conn.close()
        self._conn = None

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
            return safe_output_text(stat.username)
        return safe_output_text(stat.full_name or str(stat.user_id))

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

    @staticmethod
    def _clean_summary_source(text: str) -> str:
        cleaned = remove_telegram_mentions(text)
        cleaned = URL_PATTERN.sub("", cleaned)
        cleaned = EMOJI_PATTERN.sub("", cleaned)
        return compact_line(cleaned, limit=150)

    @staticmethod
    def _clean_news_point(text: str) -> str:
        text = compact_line(text, limit=240)
        text = text.strip(" -–—")
        return ensure_sentence(text)

    @staticmethod
    def _chat_title(chat_title: str | None, chat_id: int) -> str:
        if chat_title:
            return safe_output_text(compact_line(chat_title, limit=80))
        return f"Чат {chat_id}"

    @staticmethod
    def _chat_source(chat_title: str | None, chat_username: str | None, chat_id: int) -> str:
        if chat_username:
            return safe_output_text(chat_username)
        if chat_title:
            return safe_output_text(compact_line(chat_title, limit=80))
        return f"chat {chat_id}"

    @staticmethod
    def _is_news_candidate(text: str, tokens: list[str]) -> bool:
        if len(text) < 14:
            return False
        if len(tokens) < 2:
            return False
        if len(text.split()) < 3:
            return False

        lower = text.lower().strip(" .,!?)(")
        low_value_phrases = {
            "доброе утро",
            "доброй ночи",
            "спокойной ночи",
            "всем привет",
            "привет всем",
            "спасибо большое",
        }
        return lower not in low_value_phrases

    @staticmethod
    def _is_summary_candidate(text: str, tokens: list[str]) -> bool:
        if len(text) < 18:
            return False
        lower_text = text.lower()
        if any(cue in lower_text for cue in TASK_CUES) or any(cue in lower_text for cue in DECISION_CUES):
            return False
        if len(tokens) < 3:
            return False
        words = text.split()
        if len(words) < 4:
            return False

        lower = lower_text.strip(" .,!?)(")
        low_value_phrases = {
            "доброе утро",
            "доброй ночи",
            "спокойной ночи",
            "всем привет",
            "привет всем",
            "спасибо большое",
        }
        if lower in low_value_phrases:
            return False
        return True

    @staticmethod
    def _token_similarity(left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        return len(left & right) / len(left | right)

    def _extract_discussion_points(self, messages: list[StoredMessage], limit: int = 4) -> list[str]:
        candidates = []
        token_freq = Counter()

        for idx, item in enumerate(messages):
            text = self._clean_summary_source(item.text)
            if not text or is_emoji_only_text(text):
                continue
            tokens = tokenize(text)
            if not self._is_summary_candidate(text, tokens):
                continue
            unique_tokens = set(tokens)
            candidates.append((idx, text, unique_tokens, tokens))
            token_freq.update(unique_tokens)

        scored = []
        for idx, text, unique_tokens, tokens in candidates:
            score = sum(token_freq[token] for token in unique_tokens)
            scored.append((score, idx, text, unique_tokens))

        selected = []
        selected_tokens = []
        for _, idx, text, unique_tokens in sorted(scored, key=lambda item: (-item[0], item[1])):
            if any(self._token_similarity(unique_tokens, existing) >= 0.55 for existing in selected_tokens):
                continue
            selected.append((idx, text))
            selected_tokens.append(unique_tokens)
            if len(selected) >= limit:
                break

        selected.sort(key=lambda item: item[0])
        return [text for _, text in selected]

    def _extract_news_points(self, messages: list[StoredMessage], limit: int = 5) -> list[str]:
        candidates = []
        token_freq = Counter()

        for idx, item in enumerate(messages):
            text = self._clean_summary_source(item.text)
            if not text or is_emoji_only_text(text):
                continue
            tokens = tokenize(text)
            if not self._is_news_candidate(text, tokens):
                continue

            unique_tokens = set(tokens)
            candidates.append((idx, text, unique_tokens))
            token_freq.update(unique_tokens)

        scored = []
        for idx, text, unique_tokens in candidates:
            lower = text.lower()
            score = sum(token_freq[token] for token in unique_tokens)
            if any(cue in lower for cue in DECISION_CUES):
                score += 5
            if any(cue in lower for cue in TASK_CUES):
                score += 4
            if "?" in text:
                score += 1
            if 50 <= len(text) <= 220:
                score += 2
            scored.append((score, idx, text, unique_tokens))

        selected = []
        selected_tokens = []
        for _, idx, text, unique_tokens in sorted(scored, key=lambda item: (-item[0], item[1])):
            if any(self._token_similarity(unique_tokens, existing) >= 0.5 for existing in selected_tokens):
                continue
            selected.append((idx, self._clean_news_point(text)))
            selected_tokens.append(unique_tokens)
            if len(selected) >= limit:
                break

        selected.sort(key=lambda item: item[0])
        return [text for _, text in selected]

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

    async def build_summary_text(
        self,
        chat_id: int,
        chat_title: str | None = None,
        chat_username: str | None = None,
    ) -> str:
        messages, _, engagement = await self._collect_day_data(chat_id)
        text_total = len(messages)
        activity_total = text_total + engagement.sticker_count + engagement.reaction_count
        now_local = datetime.now(self.tz)
        title = self._chat_title(chat_title, chat_id)
        source = self._chat_source(chat_title, chat_username, chat_id)
        date_label = format_russian_datetime(now_local)

        if text_total < self.min_messages and activity_total < self.min_messages:
            return "\n".join(
                [
                    f"<b>{title}</b>",
                    f"{source} • {date_label}",
                    "Сегодня в чате пока мало содержательных сообщений для нормальной новостной сводки.",
                    "",
                    "<b>Cocoon AI Summary</b>",
                    "Недостаточно данных: за текущие сутки не набралось обсуждений, из которых можно собрать дайджест.",
                ]
            )

        news_points = self._extract_news_points(messages, limit=5)
        if not news_points:
            return "\n".join(
                [
                    f"<b>{title}</b>",
                    f"{source} • {date_label}",
                    "Сегодня в чате были сообщения, но без устойчивой темы для новостной сводки.",
                    "",
                    "<b>Cocoon AI Summary</b>",
                    "Содержательные новости дня не выделены: сообщения слишком короткие или повторяются без контекста.",
                ]
            )

        lead = ensure_sentence(
            f"{title} — {safe_output_text(news_points[0])}"
        )
        overview = ensure_sentence(
            f"{title} - дневная новостная сводка чата за текущие сутки. Главное: {safe_output_text(news_points[0])}"
        )

        lines = [
            f"<b>{title}</b>",
            f"{source} • {date_label}",
            lead,
            "",
            "<b>Cocoon AI Summary</b>",
            overview,
            "",
        ]
        for point in news_points:
            lines.append(f"⬤) {safe_output_text(point)}")
        return "\n".join(lines)


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
        f"👋 <b>{safe_output_text(user.full_name or str(user.id))}</b>, чтобы получить доступ к чату, реши капчу:\n\n<b>{question}</b>",
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

        await callback.answer("Испытание пройдено")
    else:
        failed_users.add(target_user_id)
        await callback.answer("❌ Неверно", show_alert=True)


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
    text = await summary_service.build_summary_text(
        message.chat.id,
        chat_title=message.chat.title,
        chat_username=message.chat.username,
    )
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer(text)


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
    global storage, summary_service
    tz = ZoneInfo(SUMMARY_TIMEZONE)

    storage = SummaryStorage(SUMMARY_STORAGE_PATH, MESSAGE_TTL_SECONDS)
    await storage.initialize()
    summary_service = DailySummaryService(storage_backend=storage, tz=tz, min_messages=SUMMARY_MIN_MESSAGES)


async def shutdown_services() -> None:
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
