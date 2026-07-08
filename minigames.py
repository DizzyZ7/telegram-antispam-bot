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
EXTRA_WORDS_PATH = Path(os.getenv("SLOVODEL_WORDS_PATH", "/app/data/slovodel_words.txt"))
ROUND_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


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

    async def leaderboard(self, chat_id: int, limit: int = 5) -> list[tuple[str, int, int, int]]:
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
        self.active_word_games: dict[int, WordGameRound] = {}
        self.lock = asyncio.Lock()
        self.dictionary_words = self.load_dictionary_words()
        self.round_candidates = self.build_round_candidates()
        print(
            "SLOVODEL_DICTIONARY_READY "
            f"words={len(self.dictionary_words)} bases={len(BASE_WORDS)} playable={len(self.round_candidates)}",
            flush=True,
        )

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
                LOGGER.exception("Could not load extra Slovodel words from %s", EXTRA_WORDS_PATH)
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
    def difficulty_label(total_words: int) -> str:
        if total_words >= 80:
            return "CHAOS"
        if total_words >= 55:
            return "RUSH"
        if total_words >= 35:
            return "FLOW"
        return "FOCUS"

    @staticmethod
    def mask_hint(word: str) -> str:
        if len(word) <= 4:
            return word[0] + " _ " * (len(word) - 1)
        if len(word) <= 6:
            return word[0] + " " + " ".join("_" for _ in range(len(word) - 2)) + " " + word[-1]
        middle = ["_" for _ in range(len(word) - 4)]
        return " ".join([word[0], word[1], *middle, word[-2], word[-1]])

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
            return "Игрок"
        return user.username or user.full_name or str(user.id)

    def top_players(self, round_data: WordGameRound, limit: int = 5) -> list[PlayerResult]:
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
            "🟣 <b>FOSGEN WORD//RUN</b>\n"
            f"<code>ROUND #{round_data.round_code}</code> · режим <b>{self.difficulty_label(total)}</b>\n\n"
            f"<code>{self.spaced_word(round_data.base_word)}</code>\n\n"
            f"⏱ <b>{self.format_duration(ROUND_SECONDS)}</b> · 🔡 от <b>{round_data.min_length}</b> букв · "
            f"🎯 банк: <b>{total}</b> слов\n"
            "Пиши одно слово одним сообщением. Первый нашедший забирает очки.\n\n"
            "⚡ Комбо: длинные слова дают больше звезд.\n"
            "⌁ Панель: /game · Подсказка: /hint · Стоп: /stopgame"
        )

    def render_status(self, round_data: WordGameRound) -> str:
        left = int(round_data.ends_at - time.monotonic())
        found, total, percent, bar = self.round_stats(round_data)
        players = self.top_players(round_data, limit=5)
        lines = [
            "🟣 <b>WORD//RUN · LIVE</b>",
            f"<code>ROUND #{round_data.round_code}</code> · осталось <b>{self.format_duration(left)}</b>",
            f"<code>{bar}</code> <b>{percent}%</b>",
            f"Найдено: <b>{found}</b>/<b>{total}</b> · подсказки: <b>{round_data.hint_count}</b>/<b>{HINT_LIMIT}</b>",
            "",
            "<b>Таблица сейчас</b>",
        ]
        if players:
            for idx, player in enumerate(players, start=1):
                lines.append(self.compact_player_line(idx, player))
        else:
            lines.append("▫️ Пока никто не забрал слово.")
        lines.append("")
        lines.append("⌁ /hint · /stopgame · /game_top")
        return "\n".join(lines)

    def render_finish(self, round_data: WordGameRound, players: list[PlayerResult]) -> str:
        found, total, percent, bar = self.round_stats(round_data)
        longest_words = sorted(round_data.used_words, key=lambda word: (-len(word), word))[:6]
        lines = [
            "🏁 <b>WORD//RUN завершен</b>",
            f"<code>ROUND #{round_data.round_code}</code>",
            "",
            f"<code>{self.spaced_word(round_data.base_word)}</code>",
            "",
            f"<code>{bar}</code> <b>{percent}%</b>",
            f"Найдено: <b>{found}</b>/<b>{total}</b> · длительность: <b>{self.format_duration(ROUND_SECONDS)}</b>",
            "",
            "<b>Финальная таблица</b>",
        ]
        if players:
            for idx, player in enumerate(players, start=1):
                lines.append(self.compact_player_line(idx, player))
        else:
            lines.append("▫️ Раунд ушел в архив без очков.")

        if longest_words:
            lines.append("")
            lines.append("<b>Самые длинные найденные</b>")
            lines.append(" · ".join(self.app.safe_output_text(word) for word in longest_words))

        lines.extend(("", "↻ /minigame · 🏆 /game_top"))
        return "\n".join(lines)

    def render_help(self) -> str:
        return (
            "🟣 <b>FOSGEN MINIGAMES</b>\n\n"
            "<b>WORD//RUN</b> — быстрый словесный забег на 5 минут.\n"
            "Собираете слова из букв большого слова, кто первый нашел — забрал очки.\n\n"
            "▶️ /minigame — старт\n"
            "📊 /game — статус текущего раунда\n"
            "💡 /hint — подсказка\n"
            "🏁 /stopgame — завершить\n"
            "🏆 /game_top — общий рейтинг"
        )

    async def start_word_game(self, message: Message) -> None:
        if not self.app.is_group_chat(message) or not self.app.is_allowed_chat(message.chat.id):
            return
        if message.from_user is None or message.from_user.is_bot:
            return

        async with self.lock:
            active = self.active_word_games.get(message.chat.id)
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
            self.active_word_games[message.chat.id] = round_data
            round_data.finish_task = asyncio.create_task(self.finish_later(round_data))

        await message.answer(self.render_start(round_data))

    async def finish_later(self, round_data: WordGameRound) -> None:
        delay = max(0.0, round_data.ends_at - time.monotonic())
        await asyncio.sleep(delay)
        try:
            await self.finish_word_game(round_data.chat_id, forced=False)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Could not finish word game chat_id=%s", round_data.chat_id)

    async def finish_word_game(self, chat_id: int, forced: bool) -> None:
        async with self.lock:
            round_data = self.active_word_games.pop(chat_id, None)
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
        if message.chat.id not in self.active_word_games:
            await message.reply("🟣 Сейчас нет активного WORD//RUN. Старт: /minigame")
            return
        await self.finish_word_game(message.chat.id, forced=True)

    async def show_status(self, message: Message) -> None:
        if not self.app.is_group_chat(message) or not self.app.is_allowed_chat(message.chat.id):
            return
        round_data = self.active_word_games.get(message.chat.id)
        if round_data is None or round_data.ends_at <= time.monotonic():
            await message.reply(self.render_help())
            return
        if round_data.message_thread_id is not None and message.message_thread_id != round_data.message_thread_id:
            await message.reply("🟣 WORD//RUN идет в другой теме этого чата.")
            return
        await message.reply(self.render_status(round_data))

    async def give_hint(self, message: Message) -> None:
        if not self.app.is_group_chat(message) or not self.app.is_allowed_chat(message.chat.id):
            return
        round_data = self.active_word_games.get(message.chat.id)
        if round_data is None or round_data.ends_at <= time.monotonic():
            await message.reply("🟣 Сейчас нет активного WORD//RUN. Старт: /minigame")
            return
        if round_data.message_thread_id is not None and message.message_thread_id != round_data.message_thread_id:
            await message.reply("🟣 WORD//RUN идет в другой теме этого чата.")
            return

        async with round_data.lock:
            if round_data.hint_count >= HINT_LIMIT:
                await message.reply("💡 Лимит подсказок на раунд уже исчерпан.")
                return
            remaining = sorted(round_data.allowed_words - set(round_data.used_words), key=lambda word: (-self.word_points(word), word))
            if not remaining:
                await message.reply("💡 Подсказки не нужны: все известные слова уже нашли.")
                return
            hint_word = random.choice(remaining[: min(20, len(remaining))])
            round_data.hint_count += 1
            hint_number = round_data.hint_count

        await message.reply(
            "💡 <b>WORD//RUN · hint</b>\n"
            f"Слово на <b>{len(hint_word)}</b> букв: <code>{self.mask_hint(hint_word)}</code>\n"
            f"Потенциал: <b>+{self.word_points(hint_word)}🌟</b> · подсказка {hint_number}/{HINT_LIMIT}"
        )

    async def handle_word_guess(self, message: Message) -> None:
        if not self.app.is_group_chat(message) or not self.app.is_allowed_chat(message.chat.id):
            return
        if message.from_user is None or message.from_user.is_bot:
            return
        if not message.text or message.text.startswith("/"):
            return

        round_data = self.active_word_games.get(message.chat.id)
        if round_data is None or round_data.ends_at <= time.monotonic():
            return
        if round_data.message_thread_id is not None and message.message_thread_id != round_data.message_thread_id:
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
            await message.reply(
                f"⚡ <b>+{points}🌟</b> · {self.app.safe_output_text(word)}\n"
                f"У тебя: <b>{total_points}🌟</b> · прогресс раунда: <b>{found}/{total}</b> ({percent}%)"
            )

    async def show_leaderboard(self, message: Message) -> None:
        if not self.app.is_group_chat(message) or not self.app.is_allowed_chat(message.chat.id):
            return
        leaders = await self.storage.leaderboard(message.chat.id, limit=7)
        if not leaders:
            await message.reply("🏆 Рейтинг WORD//RUN пока пуст. Старт: /minigame")
            return
        lines = ["🏆 <b>FOSGEN WORD//RUN · рейтинг</b>", ""]
        for idx, (name, total_points, wins, rounds) in enumerate(leaders, start=1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(idx, "▫️")
            lines.append(
                f"{medal} <b>{self.app.safe_output_text(name)}</b> — {total_points}🌟 · "
                f"побед {wins} · игр {rounds}"
            )
        await message.reply("\n".join(lines))


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
            LOGGER.exception("Could not handle minigame guess chat_id=%s", event.chat.id)
        return await handler(event, data)


def _promote_last_message_handler(app: Any) -> None:
    handler = app.dp.message.handlers.pop()
    app.dp.message.handlers.insert(0, handler)


def register_minigame_handlers(app: Any, service: MiniGameService) -> None:
    dispatcher = app.dp
    dispatcher.message.outer_middleware(MiniGameGuessMiddleware(service))

    @dispatcher.message(Command(commands=["minigame", "wordgame", "slovodel"]))
    async def minigame_start(message: Message) -> None:
        await service.start_word_game(message)

    _promote_last_message_handler(app)

    @dispatcher.message(Command(commands=["game", "game_status", "games"]))
    async def minigame_status(message: Message) -> None:
        await service.show_status(message)

    _promote_last_message_handler(app)

    @dispatcher.message(Command(commands=["hint", "game_hint", "word_hint"]))
    async def minigame_hint(message: Message) -> None:
        await service.give_hint(message)

    _promote_last_message_handler(app)

    @dispatcher.message(Command(commands=["stopgame", "finishgame"]))
    async def minigame_stop(message: Message) -> None:
        await service.stop_word_game(message)

    _promote_last_message_handler(app)

    @dispatcher.message(Command(commands=["game_top", "minigame_top", "slovodel_top"]))
    async def minigame_top(message: Message) -> None:
        await service.show_leaderboard(message)

    _promote_last_message_handler(app)
    print("MINIGAMES_READY games=word_run mode=middleware dictionary=expanded ux=modern", flush=True)
