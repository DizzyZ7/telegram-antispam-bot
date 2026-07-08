"""Chat mini-games for Fosgen."""

from __future__ import annotations

import asyncio
import logging
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

LOGGER = logging.getLogger(__name__)
WORD_RE = re.compile(r"^[а-яё-]+$", re.IGNORECASE)
DEFAULT_ROUND_SECONDS = 5 * 60
DEFAULT_MIN_LENGTH = 4

WORD_BANK: dict[str, set[str]] = {
    "водоочистка": {
        "вода", "водка", "воск", "восток", "водосток", "вставка", "доставка", "ставка", "сводка",
        "сотка", "сито", "диск", "кот", "код", "сок", "ток", "висок", "исток", "скат", "свод",
        "отвод", "откос", "доска", "совок", "точка", "отсев", "виток",
    },
    "литература": {
        "литр", "тира", "рулет", "артерия", "титр", "театр", "тату", "траур", "рута", "трал",
        "лирика", "лирик", "литера", "ария", "аура", "утро", "тело", "лето",
    },
    "электростанция": {
        "станция", "электрон", "сектор", "стекло", "строка", "соринка", "картон", "корсет",
        "тостер", "танец", "тесак", "стена", "сцена", "цена", "трос", "трон", "крон", "конец",
        "рацион", "тонер", "окрас", "искатель", "секатор", "стол", "соль", "кино", "лист", "слон",
    },
    "компьютеризация": {
        "компьютер", "терция", "комета", "тема", "метро", "мотор", "моряк", "рюмка", "юрист",
        "карта", "корт", "торт", "токарь", "материк", "юзер", "термин", "монета", "цензор",
        "рация", "цитата", "кот", "ком", "рот", "мир", "тир", "яма", "заря", "зима",
    },
    "микроорганизм": {
        "организм", "орган", "мороз", "знамя", "игрок", "мираж", "морг", "гром", "роман",
        "норма", "микрон", "корм", "кран", "мрак", "рана", "роза", "зима", "мир", "маг",
        "моряк", "марш", "марка", "игра", "гора", "нога", "срок", "знак", "коза",
    },
    "космонавтика": {
        "космос", "космонавт", "автомат", "станок", "нитка", "такси", "наука", "маска", "осина",
        "квант", "икона", "канат", "нотка", "скот", "ток", "кот", "сон", "нос", "мост", "мотив",
        "акт", "тон", "атом", "кино", "скат", "сани", "вата", "воск", "окно", "сова",
    },
    "параллелограмм": {
        "программа", "мораль", "рампа", "пара", "гора", "роман", "морг", "грамм", "рама", "рога",
        "лама", "мама", "море", "поле", "орел", "лего", "перо", "порог", "пламя", "мера",
    },
    "самоорганизация": {
        "организация", "орган", "гараж", "зима", "мир", "рация", "знамя", "сазан", "мороз", "норма",
        "знак", "игра", "гора", "нога", "роза", "магия", "сани", "зона", "марина", "омар", "мозаика",
    },
    "гиперпространство": {
        "пространство", "герой", "трасса", "страна", "растение", "спор", "трон", "торс", "грант",
        "сито", "рост", "порт", "нос", "рот", "сон", "гость", "сорт", "перо", "гипс", "пирс",
        "степь", "опера", "автор", "трава", "право", "опыт", "нерв", "игра", "гора",
    },
    "инфраструктура": {
        "структура", "фрукт", "труба", "турист", "страна", "ткань", "тариф", "кафтан",
        "рутина", "устав", "факт", "фарт", "стук", "кран", "курс", "рант", "танк", "рука",
        "арфа", "фауна", "искра", "утка", "нить", "тиран", "сани", "рана", "аура",
    },
}


@dataclass(slots=True)
class PlayerResult:
    user_id: int
    name: str
    points: int = 0
    words: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class WordGameRound:
    chat_id: int
    base_word: str
    allowed_words: set[str]
    min_length: int
    ends_at: float
    message_thread_id: int | None
    players: dict[int, PlayerResult] = field(default_factory=dict)
    used_words: dict[str, int] = field(default_factory=dict)
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

    @staticmethod
    def normalize_word(value: str) -> str:
        return value.strip().lower().replace("ё", "е").replace("-", "")

    @staticmethod
    def spaced_word(value: str) -> str:
        return " ".join(value.upper())

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

    def choose_base_word(self) -> tuple[str, set[str]]:
        base_word = random.choice(list(WORD_BANK))
        normalized_base = self.normalize_word(base_word)
        allowed = {self.normalize_word(item) for item in WORD_BANK[base_word]}
        allowed.discard(normalized_base)
        allowed = {
            item
            for item in allowed
            if len(item) >= DEFAULT_MIN_LENGTH and self.can_build(item, normalized_base)
        }
        return normalized_base, allowed

    def player_name(self, message: Message) -> str:
        user = message.from_user
        if user is None:
            return "Игрок"
        return user.username or user.full_name or str(user.id)

    def result_lines(self, players: list[PlayerResult]) -> list[str]:
        if not players:
            return ["Пока никто не набрал очков."]
        lines = []
        for idx, player in enumerate(players, start=1):
            best_words = sorted(player.words.items(), key=lambda item: (-item[1], item[0]))[:3]
            suffix = ""
            if best_words:
                suffix = " — " + ", ".join(f"{word} +{score}" for word, score in best_words)
            lines.append(f"{idx}. {self.app.safe_output_text(player.name)} — {player.points}🌟{suffix}")
        return lines

    async def start_word_game(self, message: Message) -> None:
        if not self.app.is_group_chat(message) or not self.app.is_allowed_chat(message.chat.id):
            return
        if message.from_user is None or message.from_user.is_bot:
            return

        async with self.lock:
            active = self.active_word_games.get(message.chat.id)
            if active and active.ends_at > time.monotonic():
                left = max(1, int(active.ends_at - time.monotonic()))
                await message.reply(f"🖍 Словодел уже идет. Осталось примерно {left // 60}м {left % 60}с.")
                return

            base_word, allowed = self.choose_base_word()
            round_data = WordGameRound(
                chat_id=message.chat.id,
                base_word=base_word,
                allowed_words=allowed,
                min_length=DEFAULT_MIN_LENGTH,
                ends_at=time.monotonic() + DEFAULT_ROUND_SECONDS,
                message_thread_id=message.message_thread_id,
            )
            self.active_word_games[message.chat.id] = round_data
            round_data.finish_task = asyncio.create_task(self.finish_later(round_data))

        await message.answer(
            "🏁 🖍 <b>Словодел начался!</b>\n\n"
            f"{self.spaced_word(base_word)}\n\n"
            f"Собирайте слова от <b>{DEFAULT_MIN_LENGTH}</b> букв из букв большого слова.\n"
            "Пишите слова прямо в чат. Один найденный вариант засчитывается первому игроку.\n"
            "Идет 5 минут, все играют параллельно.\n\n"
            "Команды: /stopgame — завершить, /game_top — рейтинг."
        )

    async def finish_later(self, round_data: WordGameRound) -> None:
        delay = max(0.0, round_data.ends_at - time.monotonic())
        await asyncio.sleep(delay)
        try:
            await self.finish_word_game(round_data.chat_id, forced=False)
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

        lines = [
            "🏁 🖍 <b>Словодел окончен!</b> 🏆 Результаты:",
            "",
            self.spaced_word(round_data.base_word),
            "",
            *self.result_lines(players),
            "",
            f"🕰 5м  🔡 {round_data.min_length} бкв  👥 Параллельно",
            "Играть еще: /minigame",
        ]
        await self.app.bot.send_message(
            chat_id,
            "\n".join(lines),
            message_thread_id=round_data.message_thread_id,
        )

    async def stop_word_game(self, message: Message) -> None:
        if not self.app.is_group_chat(message) or not self.app.is_allowed_chat(message.chat.id):
            return
        if message.from_user is None or message.from_user.is_bot:
            return
        if message.chat.id not in self.active_word_games:
            await message.reply("Сейчас нет активного Словодела. Запуск: /minigame")
            return
        await self.finish_word_game(message.chat.id, forced=True)

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

        if points >= 7:
            await message.reply(f"+{points}🌟 за <b>{self.app.safe_output_text(word)}</b>")

    async def show_leaderboard(self, message: Message) -> None:
        if not self.app.is_group_chat(message) or not self.app.is_allowed_chat(message.chat.id):
            return
        leaders = await self.storage.leaderboard(message.chat.id, limit=5)
        if not leaders:
            await message.reply("🏆 В Словоделе пока нет рейтинга. Запуск: /minigame")
            return
        lines = ["🏆 <b>Рейтинг Словодела</b>", ""]
        for idx, (name, total_points, wins, rounds) in enumerate(leaders, start=1):
            lines.append(f"{idx}. {self.app.safe_output_text(name)} — {total_points}🌟, побед: {wins}, игр: {rounds}")
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

    @dispatcher.message(Command(commands=["stopgame", "finishgame"]))
    async def minigame_stop(message: Message) -> None:
        await service.stop_word_game(message)

    _promote_last_message_handler(app)

    @dispatcher.message(Command(commands=["game_top", "minigame_top", "slovodel_top"]))
    async def minigame_top(message: Message) -> None:
        await service.show_leaderboard(message)

    _promote_last_message_handler(app)
    print("MINIGAMES_READY games=slovodel mode=middleware", flush=True)
