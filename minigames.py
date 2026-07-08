"""Chat mini-games for Fosgen."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite
from aiogram import BaseMiddleware
from aiogram.filters import Command
from aiogram.types import Message

from wordgame_dictionary import BASE_WORDS, BUILTIN_WORDS

LOGGER = logging.getLogger(__name__)
WORD_RE = re.compile(r"^[а-яё-]+$", re.IGNORECASE)
ROUND_SECONDS = 5 * 60
MIN_WORD_LENGTH = 4
MIN_SOLUTIONS_PER_ROUND = 28
HINT_LIMIT = 3
HINT_UNLOCK_SECONDS = 60
HINT_COOLDOWN_SECONDS = 75
MULTI_VOTE_PLAYER_THRESHOLD = 3
MULTI_VOTE_HINT_REQUESTS = 2
EXTRA_WORDS_PATH = Path(os.getenv("SLOVODEL_WORDS_PATH", "/app/data/slovodel_words.txt"))
ROUND_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
GameKey = tuple[int, int | None]


@dataclass(slots=True)
class PlayerResult:
    user_id: int
    name: str
    points: int = 0
    words: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class WordGameRound:
    chat_id: int
    round_code: str
    base_word: str
    allowed_words: set[str]
    min_length: int
    started_at: float
    ends_at: float
    message_thread_id: int | None
    players: dict[int, PlayerResult] = field(default_factory=dict)
    used_words: dict[str, int] = field(default_factory=dict)
    hint_count: int = 0
    hint_requesters: set[int] = field(default_factory=set)
    last_hint_at: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    finish_task: asyncio.Task[None] | None = None


class MiniGameStorage:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.connection: aiosqlite.Connection | None = None
        self.lock = asyncio.Lock()

    async def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = await aiosqlite.connect(self.database_path)
        await self.connection.execute("PRAGMA journal_mode=WAL;")
        await self.connection.execute("PRAGMA synchronous=NORMAL;")
        await self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS wordgame_scores (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                rounds INTEGER NOT NULL DEFAULT 0,
                wins INTEGER NOT NULL DEFAULT 0,
                total_points INTEGER NOT NULL DEFAULT 0,
                best_points INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(chat_id, user_id)
            )
            """
        )
        await self.connection.commit()

    async def close(self) -> None:
        if self.connection is None:
            return
        await self.connection.close()
        self.connection = None

    async def save_round(self, chat_id: int, players: list[PlayerResult]) -> None:
        if not players:
            return
        assert self.connection is not None
        best_score = max(player.points for player in players)
        now_ts = int(time.time())
        async with self.lock:
            for player in players:
                is_winner = player.points == best_score and best_score > 0
                await self.connection.execute(
                    """
                    INSERT INTO wordgame_scores(chat_id, user_id, name, rounds, wins, total_points, best_points, updated_at)
                    VALUES(?, ?, ?, 1, ?, ?, ?, ?)
                    ON CONFLICT(chat_id, user_id) DO UPDATE SET
                        name = excluded.name,
                        rounds = rounds + 1,
                        wins = wins + excluded.wins,
                        total_points = total_points + excluded.total_points,
                        best_points = MAX(best_points, excluded.best_points),
                        updated_at = excluded.updated_at
                    """,
                    (
                        chat_id,
                        player.user_id,
                        player.name,
                        1 if is_winner else 0,
                        player.points,
                        player.points,
                        now_ts,
                    ),
                )
            await self.connection.commit()

    async def leaderboard(self, chat_id: int, limit: int = 7) -> list[tuple[str, int, int, int]]:
        assert self.connection is not None
        async with self.lock:
            async with self.connection.execute(
                """
                SELECT name, total_points, wins, rounds
                FROM wordgame_scores
                WHERE chat_id = ?
                ORDER BY total_points DESC, wins DESC, best_points DESC
                LIMIT ?
                """,
                (chat_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
        return [(str(row[0]), int(row[1]), int(row[2]), int(row[3])) for row in rows]


class MiniGameService:
    def __init__(self, app: Any, storage: MiniGameStorage) -> None:
        self.app = app
        self.storage = storage
        self.active_word_games: dict[GameKey, WordGameRound] = {}
        self.lock = asyncio.Lock()
        self.dictionary_words = self.load_dictionary_words()
        self.round_candidates = self.build_round_candidates()
        print(
            "LEXICON_DICTIONARY_READY "
            f"words={len(self.dictionary_words)} bases={len(BASE_WORDS)} playable={len(self.round_candidates)}",
            flush=True,
        )

    @staticmethod
    def round_key(chat_id: int, message_thread_id: int | None) -> GameKey:
        return chat_id, message_thread_id

    @classmethod
    def round_key_from_message(cls, message: Message) -> GameKey:
        return cls.round_key(message.chat.id, message.message_thread_id)

    @classmethod
    def normalize_word(cls, value: str) -> str:
        return value.strip().lower().replace("ё", "е").replace("-", "")

    @classmethod
    def load_dictionary_words(cls) -> set[str]:
        words = {cls.normalize_word(word) for word in BUILTIN_WORDS}
        if EXTRA_WORDS_PATH.is_file():
            try:
                for line in EXTRA_WORDS_PATH.read_text(encoding="utf-8").splitlines():
                    word = cls.normalize_word(line)
                    if word and WORD_RE.match(word):
                        words.add(word)
            except Exception:
                LOGGER.exception("Could not load extra Lexicon words from %s", EXTRA_WORDS_PATH)
        return {
            word
            for word in words
            if len(word) >= MIN_WORD_LENGTH and WORD_RE.match(word)
        }

    @staticmethod
    def round_code() -> str:
        return "".join(random.choice(ROUND_CODE_ALPHABET) for _ in range(4))

    @staticmethod
    def spaced_word(value: str) -> str:
        return " ".join(value.upper())

    @staticmethod
    def format_duration(seconds: int) -> str:
        seconds = max(0, seconds)
        return f"{seconds // 60}:{seconds % 60:02d}"

    @staticmethod
    def can_build(word: str, base_word: str) -> bool:
        source = Counter(base_word)
        target = Counter(word)
        return all(source[letter] >= count for letter, count in target.items())

    @staticmethod
    def word_points(word: str) -> int:
        length = len(word)
        if length <= 4:
            return 1
        if length == 5:
            return 2
        if length == 6:
            return 4
        if length == 7:
            return 7
        return 10 + (length - 8) * 2

    @staticmethod
    def progress_bar(found: int, total: int, width: int = 10) -> str:
        if total <= 0:
            return "░" * width
        filled = min(width, round(width * found / total))
        return "▰" * filled + "▱" * (width - filled)

    @staticmethod
    def mask_hint(word: str) -> str:
        if len(word) <= 4:
            return " ".join([word[0], *("_" for _ in range(len(word) - 1))])
        if len(word) <= 6:
            return " ".join([word[0], *("_" for _ in range(len(word) - 2)), word[-1]])
        middle = ["_" for _ in range(len(word) - 4)]
        return " ".join([word[0], word[1], *middle, word[-2], word[-1]])

    def required_hint_votes(self, round_data: WordGameRound) -> int:
        if len(round_data.players) >= MULTI_VOTE_PLAYER_THRESHOLD:
            return MULTI_VOTE_HINT_REQUESTS
        return 1

    def build_round_candidates(self) -> list[tuple[str, set[str]]]:
        candidates: list[tuple[str, set[str]]] = []
        best_fallback: tuple[str, set[str]] | None = None
        for raw_base_word in BASE_WORDS:
            base_word = self.normalize_word(raw_base_word)
            allowed = {
                word
                for word in self.dictionary_words
                if word != base_word and self.can_build(word, base_word)
            }
            if best_fallback is None or len(allowed) > len(best_fallback[1]):
                best_fallback = (base_word, allowed)
            if len(allowed) >= MIN_SOLUTIONS_PER_ROUND:
                candidates.append((base_word, allowed))

        if candidates:
            return candidates
        return [best_fallback] if best_fallback is not None else []

    def choose_base_word(self) -> tuple[str, set[str]]:
        if not self.round_candidates:
            base_word = "литература"
            allowed = {
                word
                for word in self.dictionary_words
                if word != base_word and self.can_build(word, base_word)
            }
            return base_word, allowed

        candidates = sorted(self.round_candidates, key=lambda item: len(item[1]), reverse=True)
        pool = candidates[: max(6, min(len(candidates), 18))]
        base_word, allowed = random.choice(pool)
        return base_word, set(allowed)

    def player_name(self, message: Message) -> str:
        user = message.from_user
        if user is None:
            return "Автор"
        return user.username or user.full_name or str(user.id)

    def top_players(self, round_data: WordGameRound, limit: int = 7) -> list[PlayerResult]:
        return sorted(
            round_data.players.values(),
            key=lambda player: (-player.points, player.name.lower()),
        )[:limit]

    def compact_player_line(self, idx: int, player: PlayerResult) -> str:
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(idx, "▫️")
        best_words = sorted(player.words.items(), key=lambda item: (-item[1], item[0]))[:3]
        words_part = ""
        if best_words:
            words_part = "\n   " + " · ".join(f"{word} +{score}" for word, score in best_words)
        return f"{medal} <b>{self.app.safe_output_text(player.name)}</b> — {player.points}🌟{words_part}"

    def round_stats(self, round_data: WordGameRound) -> tuple[int, int, int, str]:
        found = len(round_data.used_words)
        total = len(round_data.allowed_words)
        percent = round(found * 100 / total) if total else 0
        bar = self.progress_bar(found, total)
        return found, total, percent, bar

    def render_start(self, round_data: WordGameRound) -> str:
        total = len(round_data.allowed_words)
        return (
            "📖 <b>ЛЕКСИКОН открыт</b>\n\n"
            f"<code>Раунд #{round_data.round_code}</code>\n"
            "Слово-источник:\n\n"
            f"<code>{self.spaced_word(round_data.base_word)}</code>\n\n"
            f"Из этих букв можно собрать <b>{total}</b> слов.\n"
            f"Минимум — <b>{round_data.min_length}</b> буквы.\n"
            f"Время до закрытия страницы — <b>{self.format_duration(ROUND_SECONDS)}</b>.\n\n"
            "Пишите слова прямо в чат.\n"
            "Первый, кто нашел слово, забирает его себе.\n\n"
            "Чем длиннее слово, тем больше звезд:\n"
            "4 буквы — 1🌟\n"
            "5 букв — 2🌟\n"
            "6 букв — 4🌟\n"
            "7 букв — 7🌟\n"
            "8+ букв — 10🌟 и выше\n\n"
            "Намеки открываются не сразу и не по одному голосу в активном раунде.\n\n"
            "⌁ Страница раунда: /game\n"
            "⌁ Намек: /hint\n"
            "⌁ Закрыть досрочно: /stopgame"
        )

    def render_status(self, round_data: WordGameRound) -> str:
        left = int(round_data.ends_at - time.monotonic())
        found, total, percent, bar = self.round_stats(round_data)
        players = self.top_players(round_data, limit=7)
        hint_votes_required = self.required_hint_votes(round_data)
        lines = [
            "📖 <b>ЛЕКСИКОН · текущая страница</b>",
            "",
            f"<code>Раунд #{round_data.round_code}</code>",
            f"Осталось: <b>{self.format_duration(left)}</b>",
            "",
            f"<code>{bar}</code> <b>{percent}%</b>",
            f"Найдено слов: <b>{found}</b> из <b>{total}</b>",
            f"Намеков использовано: <b>{round_data.hint_count}</b>/<b>{HINT_LIMIT}</b>",
            f"Голосов за следующий намек: <b>{len(round_data.hint_requesters)}</b>/<b>{hint_votes_required}</b>",
            "",
            "<b>Сейчас в тексте</b>",
            "",
        ]
        if players:
            for idx, player in enumerate(players, start=1):
                lines.append(self.compact_player_line(idx, player))
        else:
            lines.append("▫️ Страница еще чистая. Первое слово ждет автора.")
        lines.append("")
        lines.append("⌁ /hint · /stopgame · /game_top")
        return "\n".join(lines)

    def render_finish(self, round_data: WordGameRound, players: list[PlayerResult]) -> str:
        found, total, percent, bar = self.round_stats(round_data)
        beautiful_words = sorted(round_data.used_words, key=lambda word: (-len(word), word))[:6]
        lines = [
            "📕 <b>ЛЕКСИКОН закрыт</b>",
            "",
            f"<code>Раунд #{round_data.round_code}</code>",
            "",
            "Слово-источник:",
            f"<code>{self.spaced_word(round_data.base_word)}</code>",
            "",
            f"<code>{bar}</code> <b>{percent}%</b>",
            f"Чат нашел <b>{found}</b> слов из <b>{total}</b>.",
            "",
            "<b>Финальная страница</b>",
            "",
        ]
        if players:
            for idx, player in enumerate(players, start=1):
                lines.append(self.compact_player_line(idx, player))
        else:
            lines.append("▫️ Раунд закрылся пустой страницей.")

        if beautiful_words:
            lines.append("")
            lines.append("<b>Самые красивые находки раунда</b>")
            lines.append(" · ".join(self.app.safe_output_text(word) for word in beautiful_words))

        lines.extend(("", "Новая страница Лексикона: /minigame", "Рейтинг авторов: /game_top"))
        return "\n".join(lines)

    def render_help(self) -> str:
        return (
            "📖 <b>ЛЕКСИКОН</b>\n"
            "словесный раунд от Fosgen\n\n"
            "Собирайте слова из букв слова-источника.\n"
            "Кто первым нашел слово — забирает его на свою страницу.\n\n"
            "▶️ /minigame или /lexicon — открыть Лексикон\n"
            "📄 /game — текущая страница\n"
            "💡 /hint — намек на полях\n"
            "📕 /stopgame — закрыть страницу\n"
            "🏆 /game_top — рейтинг авторов"
        )

    async def start_word_game(self, message: Message) -> None:
        if not self.app.is_group_chat(message) or not self.app.is_allowed_chat(message.chat.id):
            return
        if message.from_user is None or message.from_user.is_bot:
            return

        game_key = self.round_key_from_message(message)
        async with self.lock:
            active = self.active_word_games.get(game_key)
            if active and active.ends_at > time.monotonic():
                await message.reply(self.render_status(active))
                return

            base_word, allowed = self.choose_base_word()
            now = time.monotonic()
            round_data = WordGameRound(
                chat_id=message.chat.id,
                round_code=self.round_code(),
                base_word=base_word,
                allowed_words=allowed,
                min_length=MIN_WORD_LENGTH,
                started_at=now,
                ends_at=now + ROUND_SECONDS,
                message_thread_id=message.message_thread_id,
            )
            self.active_word_games[game_key] = round_data
            round_data.finish_task = asyncio.create_task(self.finish_later(round_data))

        await message.answer(self.render_start(round_data))

    async def finish_later(self, round_data: WordGameRound) -> None:
        delay = max(0.0, round_data.ends_at - time.monotonic())
        await asyncio.sleep(delay)
        try:
            await self.finish_word_game(round_data.chat_id, round_data.message_thread_id, forced=False)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Could not finish Lexicon game chat_id=%s", round_data.chat_id)

    async def finish_word_game(self, chat_id: int, message_thread_id: int | None, forced: bool) -> None:
        game_key = self.round_key(chat_id, message_thread_id)
        async with self.lock:
            round_data = self.active_word_games.pop(game_key, None)
        if round_data is None:
            return
        if forced and round_data.finish_task is not None:
            round_data.finish_task.cancel()

        async with round_data.lock:
            players = sorted(round_data.players.values(), key=lambda player: (-player.points, player.name.lower()))
        await self.storage.save_round(chat_id, players)
        await self.app.bot.send_message(
            chat_id,
            self.render_finish(round_data, players),
            message_thread_id=round_data.message_thread_id,
        )

    async def stop_word_game(self, message: Message) -> None:
        if not self.app.is_group_chat(message) or not self.app.is_allowed_chat(message.chat.id):
            return
        if message.from_user is None or message.from_user.is_bot:
            return
        game_key = self.round_key_from_message(message)
        if game_key not in self.active_word_games:
            await message.reply("📖 В этой теме нет открытой страницы Лексикона. Старт: /minigame")
            return
        await self.finish_word_game(message.chat.id, message.message_thread_id, forced=True)

    async def show_status(self, message: Message) -> None:
        if not self.app.is_group_chat(message) or not self.app.is_allowed_chat(message.chat.id):
            return
        round_data = self.active_word_games.get(self.round_key_from_message(message))
        if round_data is None or round_data.ends_at <= time.monotonic():
            await message.reply(self.render_help())
            return
        await message.reply(self.render_status(round_data))

    async def give_hint(self, message: Message) -> None:
        if not self.app.is_group_chat(message) or not self.app.is_allowed_chat(message.chat.id):
            return
        if message.from_user is None or message.from_user.is_bot:
            return

        round_data = self.active_word_games.get(self.round_key_from_message(message))
        if round_data is None or round_data.ends_at <= time.monotonic():
            await message.reply("📖 В этой теме нет открытой страницы Лексикона. Старт: /minigame")
            return

        now = time.monotonic()
        async with round_data.lock:
            elapsed = int(now - round_data.started_at)
            if elapsed < HINT_UNLOCK_SECONDS:
                await message.reply(
                    "💡 Пометки на полях появятся чуть позже.\n"
                    f"Первый намек откроется через <b>{self.format_duration(HINT_UNLOCK_SECONDS - elapsed)}</b>."
                )
                return

            if round_data.hint_count >= HINT_LIMIT:
                await message.reply("💡 На этой странице больше нет свободных намеков.")
                return

            if round_data.last_hint_at and now - round_data.last_hint_at < HINT_COOLDOWN_SECONDS:
                wait_left = int(HINT_COOLDOWN_SECONDS - (now - round_data.last_hint_at))
                await message.reply(
                    "💡 Чернила предыдущей пометки еще не высохли.\n"
                    f"Следующий намек можно открыть через <b>{self.format_duration(wait_left)}</b>."
                )
                return

            if message.from_user.id in round_data.hint_requesters:
                votes_required = self.required_hint_votes(round_data)
                await message.reply(
                    "💡 Твой голос за намек уже записан.\n"
                    f"Сейчас: <b>{len(round_data.hint_requesters)}</b>/<b>{votes_required}</b>."
                )
                return

            round_data.hint_requesters.add(message.from_user.id)
            votes_required = self.required_hint_votes(round_data)
            if len(round_data.hint_requesters) < votes_required:
                await message.reply(
                    "💡 Голос за намек записан.\n"
                    f"Нужно еще: <b>{votes_required - len(round_data.hint_requesters)}</b>.\n"
                    f"Сейчас: <b>{len(round_data.hint_requesters)}</b>/<b>{votes_required}</b>."
                )
                return

            remaining = sorted(round_data.allowed_words - set(round_data.used_words), key=lambda word: (-self.word_points(word), word))
            if not remaining:
                await message.reply("💡 Намек не нужен: все известные слова уже найдены.")
                return
            hint_word = random.choice(remaining[: min(20, len(remaining))])
            round_data.hint_count += 1
            round_data.last_hint_at = now
            round_data.hint_requesters.clear()
            hint_number = round_data.hint_count

        await message.reply(
            "💡 <b>Пометка на полях</b>\n\n"
            f"Одно из ненайденных слов начинается на <b>{self.app.safe_output_text(hint_word[0])}</b>\n"
            f"и заканчивается на <b>{self.app.safe_output_text(hint_word[-1])}</b>.\n\n"
            f"В нем <b>{len(hint_word)}</b> букв.\n"
            f"За него дадут <b>+{self.word_points(hint_word)}🌟</b>.\n\n"
            f"Намек <b>{hint_number}</b>/<b>{HINT_LIMIT}</b>\n"
            f"<code>{self.mask_hint(hint_word)}</code>"
        )

    async def handle_word_guess(self, message: Message) -> None:
        if not self.app.is_group_chat(message) or not self.app.is_allowed_chat(message.chat.id):
            return
        if message.from_user is None or message.from_user.is_bot:
            return
        if not message.text or message.text.startswith("/"):
            return

        round_data = self.active_word_games.get(self.round_key_from_message(message))
        if round_data is None or round_data.ends_at <= time.monotonic():
            return

        raw_word = message.text.strip()
        if " " in raw_word or "\n" in raw_word:
            return
        if not WORD_RE.match(raw_word):
            return

        word = self.normalize_word(raw_word)
        if len(word) < round_data.min_length:
            return
        if word == round_data.base_word:
            return
        if not self.can_build(word, round_data.base_word):
            return
        if word not in round_data.allowed_words:
            return

        async with round_data.lock:
            if word in round_data.used_words:
                return
            points = self.word_points(word)
            player = round_data.players.get(message.from_user.id)
            if player is None:
                player = PlayerResult(user_id=message.from_user.id, name=self.player_name(message))
                round_data.players[message.from_user.id] = player
            player.points += points
            player.words[word] = points
            round_data.used_words[word] = message.from_user.id
            total_points = player.points
            found, total, percent, _ = self.round_stats(round_data)

        if points >= 7:
            label = "💎 <b>Редкая находка</b>" if points >= 10 else "✨ <b>Сильная находка</b>"
            await message.reply(
                f"{label}\n\n"
                f"{self.app.safe_output_text(player.name)} забирает слово:\n"
                f"<code>{self.app.safe_output_text(word)}</code>\n\n"
                f"+{points}🌟\n"
                f"Всего у автора: <b>{total_points}🌟</b>\n"
                f"Страница заполнена на <b>{percent}%</b> — {found}/{total}"
            )

    async def show_leaderboard(self, message: Message) -> None:
        if not self.app.is_group_chat(message) or not self.app.is_allowed_chat(message.chat.id):
            return
        leaders = await self.storage.leaderboard(message.chat.id, limit=7)
        if not leaders:
            await message.reply("🏆 Рейтинг Лексикона пока пуст. Открыть первую страницу: /minigame")
            return
        lines = ["🏆 <b>ЛЕКСИКОН · общий рейтинг</b>", ""]
        for idx, (name, total_points, wins, rounds) in enumerate(leaders, start=1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(idx, "▫️")
            lines.append(
                f"{medal} <b>{self.app.safe_output_text(name)}</b> — {total_points}🌟\n"
                f"   побед: {wins} · страниц сыграно: {rounds}"
            )
        await message.reply("\n\n".join(lines))


class MiniGameGuessMiddleware(BaseMiddleware):
    def __init__(self, service: MiniGameService) -> None:
        self.service = service

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        try:
            await self.service.handle_word_guess(event)
        except Exception:
            LOGGER.exception("Could not handle Lexicon guess chat_id=%s", event.chat.id)
        return await handler(event, data)


def _promote_last_message_handler(app: Any) -> None:
    handler = app.dp.message.handlers.pop()
    app.dp.message.handlers.insert(0, handler)


def register_minigame_handlers(app: Any, service: MiniGameService) -> None:
    dispatcher = app.dp
    dispatcher.message.outer_middleware(MiniGameGuessMiddleware(service))

    @dispatcher.message(Command(commands=["minigame", "lexicon", "leksikon", "wordgame", "slovodel"]))
    async def minigame_start(message: Message) -> None:
        await service.start_word_game(message)

    _promote_last_message_handler(app)

    @dispatcher.message(Command(commands=["game", "page", "lexicon_page", "games"]))
    async def minigame_status(message: Message) -> None:
        await service.show_status(message)

    _promote_last_message_handler(app)

    @dispatcher.message(Command(commands=["hint", "game_hint", "word_hint", "namyek"]))
    async def minigame_hint(message: Message) -> None:
        await service.give_hint(message)

    _promote_last_message_handler(app)

    @dispatcher.message(Command(commands=["stopgame", "finishgame", "close_lexicon"]))
    async def minigame_stop(message: Message) -> None:
        await service.stop_word_game(message)

    _promote_last_message_handler(app)

    @dispatcher.message(Command(commands=["game_top", "minigame_top", "lexicon_top", "slovodel_top"]))
    async def minigame_top(message: Message) -> None:
        await service.show_leaderboard(message)

    _promote_last_message_handler(app)
    print(
        "MINIGAMES_READY games=lexicon mode=middleware dictionary=expanded "
        "ux=literary scope=topic hints=throttled",
        flush=True,
    )
