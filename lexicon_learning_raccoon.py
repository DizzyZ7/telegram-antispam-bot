"""Raccoon companion, collection and sticker layer for the stable Lexicon service.

The raccoon stays secret during the round. When found, the trophy is recorded once
in SQLite, shown in page titles and included in score and collection leaderboards.
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram.filters import Command
from aiogram.types import Message

from lexicon_learning import register_lexicon_learning_handlers as register_base_lexicon_learning_handlers
from lexicon_learning_persistent import LearningLexiconService as BaseLearningLexiconService
from lexicon_raccoon import RACCOON_WORD, raccoon_can_hide, raccoon_finder_name
from lexicon_raccoon_collection import (
    raccoon_collection_counts,
    raccoon_leaderboard,
    record_raccoon_find,
    score_leaderboard_with_raccoons,
)
from lexicon_raccoon_sticker import load_raccoon_sticker_file_id, save_raccoon_sticker_file_id
from minigames import PlayerResult, WordGameRound

LOGGER = logging.getLogger(__name__)


async def _is_chat_admin(service: Any, message: Message) -> bool:
    if message.from_user is None:
        return False
    try:
        member = await service.app.bot.get_chat_member(message.chat.id, message.from_user.id)
        return getattr(member, "status", None) in {"creator", "administrator"}
    except Exception:
        LOGGER.info("Could not check admin status for raccoon sticker command", exc_info=True)
        return False


class LearningLexiconService(BaseLearningLexiconService):
    """Stable Lexicon service with secret raccoon trophies and collections."""

    def __init__(self, app: Any, storage: Any) -> None:
        super().__init__(app, storage)
        self.raccoon_sticker_file_id = load_raccoon_sticker_file_id()
        self._raccoon_recorded_rounds: set[tuple[int, str]] = set()
        self._raccoon_sticker_announced_rounds: set[tuple[int, int | None, str]] = set()
        self._raccoon_collection_totals: dict[tuple[int, str], int] = {}
        print(
            "LEXICON_RACCOON_STICKER_READY "
            f"configured={'on' if self.raccoon_sticker_file_id else 'off'} "
            "command=/set_raccoon_sticker persistent=on",
            flush=True,
        )

    def _raccoon_title_line(self, round_data: WordGameRound) -> str:
        finder_name = raccoon_finder_name(round_data)
        if finder_name is not None:
            total = self._raccoon_collection_totals.get(
                (round_data.chat_id, round_data.round_code)
            )
            collection = f" · в коллекции: <b>{total}</b>" if total is not None else ""
            return (
                "🦝 Енота нашел — "
                f"<b>{self.app.safe_output_text(finder_name)}</b>{collection}"
            )

        if raccoon_can_hide(round_data.base_word):
            return "🦝 Енот страницы — остался незамеченным."
        return "🦝 Енот страницы — в буквах не обнаружен, но раунд поддержал."

    def achievement_lines(self, round_data: WordGameRound) -> list[str]:
        """Place the raccoon inside «Титулы страницы», never after the final text."""
        lines = super().achievement_lines(round_data)
        if not lines:
            return lines

        # Base format begins with an empty line and the title header. Put the
        # raccoon directly below that header, before the other page titles.
        insert_at = 2 if len(lines) >= 2 else len(lines)
        lines.insert(insert_at, self._raccoon_title_line(round_data))
        return lines

    def strong_found_reply(
        self,
        *,
        player: PlayerResult,
        word: str,
        points: int,
        total_points: int,
        found: int,
        total: int,
        percent: int,
    ) -> str:
        if word == RACCOON_WORD:
            return (
                "🦝 <b>Енот найден!</b>\n\n"
                f"🦝 Енота нашел: <b>{self.app.safe_output_text(player.name)}</b>\n\n"
                f"<code>{RACCOON_WORD}</code>\n"
                f"+{points}🌟 · всего: <b>{total_points}🌟</b>\n"
                f"Слов у автора: <b>{len(player.words)}</b>\n"
                f"Страница заполнена на <b>{percent}%</b> — {found}/{total}"
            )

        return super().strong_found_reply(
            player=player,
            word=word,
            points=points,
            total_points=total_points,
            found=found,
            total=total,
            percent=percent,
        )

    async def _record_raccoon(self, round_data: WordGameRound) -> int | None:
        owner_id = round_data.used_words.get(RACCOON_WORD)
        player = round_data.players.get(owner_id) if owner_id is not None else None
        if player is None:
            return None

        record_key = (round_data.chat_id, round_data.round_code)
        if record_key in self._raccoon_recorded_rounds:
            return self._raccoon_collection_totals.get(record_key)

        try:
            total = await record_raccoon_find(
                self.storage,
                chat_id=round_data.chat_id,
                round_code=round_data.round_code,
                user_id=player.user_id,
                name=player.name,
                day_key=self.today_key(),
                week_key=self.week_key(),
                base_word=round_data.base_word,
            )
        except Exception:
            LOGGER.exception(
                "Could not record raccoon trophy chat_id=%s round=%s user_id=%s",
                round_data.chat_id,
                round_data.round_code,
                player.user_id,
            )
            return None

        self._raccoon_recorded_rounds.add(record_key)
        self._raccoon_collection_totals[record_key] = total
        LOGGER.info(
            "LEXICON_RACCOON_COLLECTED chat_id=%s round=%s user_id=%s total=%s",
            round_data.chat_id,
            round_data.round_code,
            player.user_id,
            total,
        )
        return total

    async def _send_raccoon_sticker(self, round_data: WordGameRound) -> None:
        file_id = self.raccoon_sticker_file_id
        if not file_id:
            return

        params: dict[str, Any] = {
            "chat_id": round_data.chat_id,
            "sticker": file_id,
        }
        if round_data.message_thread_id is not None:
            params["message_thread_id"] = round_data.message_thread_id

        try:
            await self.app.bot.send_sticker(**params)
            LOGGER.info(
                "LEXICON_RACCOON_STICKER_SENT chat_id=%s thread_id=%s round=%s",
                round_data.chat_id,
                round_data.message_thread_id,
                round_data.round_code,
            )
        except Exception:
            LOGGER.exception(
                "Could not send raccoon sticker chat_id=%s thread_id=%s round=%s",
                round_data.chat_id,
                round_data.message_thread_id,
                round_data.round_code,
            )

    async def handle_word_guess(self, message: Message) -> None:
        """Delegate scoring, record the trophy and send its sticker once."""
        round_data = self.active_word_games.get(self.round_key_from_message(message))
        was_found = bool(round_data and RACCOON_WORD in round_data.used_words)

        await super().handle_word_guess(message)

        if round_data is None or was_found or RACCOON_WORD not in round_data.used_words:
            return

        owner_id = round_data.used_words.get(RACCOON_WORD)
        if message.from_user is None or owner_id != message.from_user.id:
            return

        await self._record_raccoon(round_data)

        announcement_key = (
            round_data.chat_id,
            round_data.message_thread_id,
            round_data.round_code,
        )
        if announcement_key in self._raccoon_sticker_announced_rounds:
            return
        self._raccoon_sticker_announced_rounds.add(announcement_key)
        await self._send_raccoon_sticker(round_data)

    async def finish_word_game(
        self,
        chat_id: int,
        message_thread_id: int | None,
        forced: bool,
    ) -> None:
        """Retry trophy persistence before rendering final page titles."""
        game_key = self.round_key(chat_id, message_thread_id)
        round_data = self.active_word_games.get(game_key)
        if round_data is not None and RACCOON_WORD in round_data.used_words:
            await self._record_raccoon(round_data)

        try:
            await super().finish_word_game(chat_id, message_thread_id, forced)
        finally:
            if round_data is not None:
                record_key = (round_data.chat_id, round_data.round_code)
                announcement_key = (
                    round_data.chat_id,
                    round_data.message_thread_id,
                    round_data.round_code,
                )
                self._raccoon_recorded_rounds.discard(record_key)
                self._raccoon_sticker_announced_rounds.discard(announcement_key)
                self._raccoon_collection_totals.pop(record_key, None)

    async def show_leaderboard(self, message: Message) -> None:
        if not self.app.is_group_chat(message) or not self.app.is_allowed_chat(message.chat.id):
            return
        leaders = await score_leaderboard_with_raccoons(
            self.storage,
            chat_id=message.chat.id,
            period="all",
            limit=7,
        )
        if not leaders:
            await message.reply("🏆 Рейтинг Лексикона пока пуст. Открыть первую страницу: /minigame")
            return

        lines = ["🏆 <b>ЛЕКСИКОН · общий рейтинг</b>", ""]
        for idx, (name, total_points, wins, rounds, raccoons) in enumerate(leaders, start=1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(idx, "▫️")
            lines.append(
                f"{medal} <b>{self.app.safe_output_text(name)}</b> — {total_points}🌟\n"
                f"   побед: {wins} · страниц: {rounds} · коллекция: {raccoons} 🦝"
            )
        await message.reply("\n\n".join(lines))

    async def show_day_leaderboard(self, message: Message) -> None:
        if not self.app.is_group_chat(message) or not self.app.is_allowed_chat(message.chat.id):
            return
        leaders = await score_leaderboard_with_raccoons(
            self.storage,
            chat_id=message.chat.id,
            period="day",
            period_key=self.today_key(),
            limit=7,
        )
        if not leaders:
            await message.reply("🏆 Сегодня в Лексиконе еще нет сыгранных страниц. Открыть страницу: /minigame")
            return

        lines = [f"🏆 <b>ЛЕКСИКОН · сегодня</b> · {self.today_label()}", ""]
        for idx, (name, total_points, wins, rounds, raccoons) in enumerate(leaders, start=1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(idx, "▫️")
            lines.append(
                f"{medal} <b>{self.app.safe_output_text(name)}</b> — {total_points}🌟\n"
                f"   побед: {wins} · страниц: {rounds} · найдено енотов: {raccoons} 🦝"
            )
        await message.reply("\n\n".join(lines))

    async def show_week_leaderboard(self, message: Message) -> None:
        if not self.app.is_group_chat(message) or not self.app.is_allowed_chat(message.chat.id):
            return
        leaders = await score_leaderboard_with_raccoons(
            self.storage,
            chat_id=message.chat.id,
            period="week",
            period_key=self.week_key(),
            limit=7,
        )
        if not leaders:
            await message.reply("🏆 На этой неделе в Лексиконе еще нет сыгранных страниц. Старт: /minigame")
            return

        lines = [f"🏆 <b>ЛЕКСИКОН · неделя</b> · {self.week_label()}", ""]
        for idx, (name, total_points, wins, rounds, raccoons) in enumerate(leaders, start=1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(idx, "▫️")
            lines.append(
                f"{medal} <b>{self.app.safe_output_text(name)}</b> — {total_points}🌟\n"
                f"   побед: {wins} · страниц: {rounds} · найдено енотов: {raccoons} 🦝"
            )
        await message.reply("\n\n".join(lines))

    async def show_profile(self, message: Message) -> None:
        if not self.app.is_group_chat(message) or not self.app.is_allowed_chat(message.chat.id):
            return
        if message.from_user is None or message.from_user.is_bot:
            return

        day_key = self.today_key()
        week_key = self.week_key()
        profile = await self.storage.profile(
            message.chat.id,
            message.from_user.id,
            day_key,
            week_key,
        )
        raccoons_total, raccoons_today, raccoons_week = await raccoon_collection_counts(
            self.storage,
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            day_key=day_key,
            week_key=week_key,
        )

        if profile is None:
            if raccoons_total:
                await message.reply(
                    "👤 <b>Твоя страница Лексикона</b>\n\n"
                    f"Коллекция енотов: <b>{raccoons_total} 🦝</b>\n"
                    "Очки текущего раунда появятся после закрытия страницы."
                )
            else:
                await message.reply("📖 У тебя пока нет страницы в Лексиконе. Открыть первую: /minigame")
            return

        lines = [
            "👤 <b>Твоя страница Лексикона</b>",
            "",
            f"Автор: <b>{self.app.safe_output_text(profile.name)}</b>",
            f"Всего: <b>{profile.total_points}🌟</b>",
            f"Побед: <b>{profile.wins}</b> · страниц сыграно: <b>{profile.rounds}</b>",
            f"Лучший раунд: <b>{profile.best_points}🌟</b>",
            f"Коллекция енотов: <b>{raccoons_total} 🦝</b>",
            "",
            f"Сегодня: <b>{profile.today_points}🌟</b> · побед {profile.today_wins} · страниц {profile.today_rounds} · енотов {raccoons_today}",
            f"Неделя: <b>{profile.week_points}🌟</b> · побед {profile.week_wins} · страниц {profile.week_rounds} · енотов {raccoons_week}",
        ]
        await message.reply("\n".join(lines))

    async def show_raccoon_leaderboard(self, message: Message) -> None:
        if not self.app.is_group_chat(message) or not self.app.is_allowed_chat(message.chat.id):
            return

        leaders = await raccoon_leaderboard(
            self.storage,
            chat_id=message.chat.id,
            limit=10,
        )
        if not leaders:
            await message.reply(
                "🦝 Коллекция енотов пока пуста. Первый енот еще маскируется под обычное слово."
            )
            return

        lines = ["🦝 <b>ЛЕКСИКОН · коллекционеры енотов</b>", ""]
        for idx, (name, raccoons) in enumerate(leaders, start=1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(idx, "▫️")
            lines.append(
                f"{medal} <b>{self.app.safe_output_text(name)}</b> — <b>{raccoons}</b> 🦝"
            )
        lines.extend(("", "Каждый енот найден среди букв и бережно отправлен в коллекцию."))
        await message.reply("\n".join(lines))

    def render_help(self) -> str:
        return f"{super().render_help()}\n🦝 /raccoon_top — коллекционеры енотов"


def _promote_last_message_handler(dispatcher: Any) -> None:
    dispatcher.message.handlers.insert(0, dispatcher.message.handlers.pop())


def register_lexicon_learning_handlers(app: Any, service: LearningLexiconService) -> None:
    """Register base Lexicon commands, raccoon collection and sticker settings."""
    register_base_lexicon_learning_handlers(app, service)
    dispatcher = app.dp

    @dispatcher.message(Command(commands=["raccoon_top", "enot_top"]))
    async def raccoon_top(message: Message) -> None:
        await service.show_raccoon_leaderboard(message)

    _promote_last_message_handler(dispatcher)

    @dispatcher.message(Command("set_raccoon_sticker"))
    async def set_raccoon_sticker(message: Message) -> None:
        if not service.app.is_group_chat(message) or not service.app.is_allowed_chat(message.chat.id):
            return
        if not await _is_chat_admin(service, message):
            await message.reply("🦝 Настраивать стикер енота могут только администраторы чата.")
            return

        replied = message.reply_to_message
        sticker = replied.sticker if replied is not None else None
        if sticker is None:
            await message.reply(
                "🦝 Сначала отправь нужный стикер, затем ответь на него командой "
                "<code>/set_raccoon_sticker</code>."
            )
            return

        try:
            file_id = save_raccoon_sticker_file_id(sticker.file_id)
        except Exception:
            LOGGER.exception("Could not save raccoon sticker file_id")
            await message.reply("🦝 Не удалось сохранить этот стикер. Попробуй другой.")
            return

        service.raccoon_sticker_file_id = file_id
        await message.reply(
            "🦝 <b>Стикер енота сохранен.</b>\n"
            "Теперь Fosgen отправит его, когда кто-нибудь найдет слово <code>енот</code>."
        )

        params: dict[str, Any] = {"chat_id": message.chat.id, "sticker": file_id}
        normalized_thread_id = service.round_key_from_message(message)[1]
        if normalized_thread_id is not None:
            params["message_thread_id"] = normalized_thread_id
        try:
            await service.app.bot.send_sticker(**params)
        except Exception:
            LOGGER.exception("Raccoon sticker was saved but test send failed")
            await message.reply(
                "⚠️ Стикер сохранен, но Fosgen не смог отправить его для проверки. "
                "Проверь право отправлять стикеры в этом чате."
            )

    _promote_last_message_handler(dispatcher)

    @dispatcher.message(Command("raccoon_sticker_status"))
    async def raccoon_sticker_status(message: Message) -> None:
        if not service.app.is_group_chat(message) or not service.app.is_allowed_chat(message.chat.id):
            return
        status = "настроен" if service.raccoon_sticker_file_id else "не настроен"
        await message.reply(
            f"🦝 Стикер енота: <b>{status}</b>.\n"
            "Для замены ответь на новый стикер командой <code>/set_raccoon_sticker</code>."
        )

    _promote_last_message_handler(dispatcher)


print(
    "LEXICON_RACCOON_READY word=енот secret=on titles=on collection_db=on "
    "score_tops=on raccoon_top=on sticker=configurable",
    flush=True,
)

__all__ = ["LearningLexiconService", "register_lexicon_learning_handlers"]
