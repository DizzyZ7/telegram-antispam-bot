"""Raccoon companion layer for the stable Lexicon service.

This module subclasses the production service instead of modifying its storage,
scoring, routing or dictionary internals. A Telegram sticker can be configured by
replying to it with /set_raccoon_sticker; its file_id survives redeploys in DATA_DIR.
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram.filters import Command
from aiogram.types import Message

from lexicon_learning import register_lexicon_learning_handlers as register_base_lexicon_learning_handlers
from lexicon_learning_persistent import LearningLexiconService as BaseLearningLexiconService
from lexicon_raccoon import RACCOON_WORD, raccoon_can_hide, raccoon_finder_name
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
    """Stable Lexicon service with a small per-round raccoon companion."""

    def __init__(self, app: Any, storage: Any) -> None:
        super().__init__(app, storage)
        self.raccoon_sticker_file_id = load_raccoon_sticker_file_id()
        self._raccoon_sticker_announced_rounds: set[tuple[int, int | None, str]] = set()
        print(
            "LEXICON_RACCOON_STICKER_READY "
            f"configured={'on' if self.raccoon_sticker_file_id else 'off'} "
            "command=/set_raccoon_sticker persistent=on",
            flush=True,
        )

    def _raccoon_line(self, round_data: WordGameRound, *, final: bool = False) -> str:
        finder_name = raccoon_finder_name(round_data)
        if finder_name is not None:
            return f"🦝 Енота нашел: <b>{self.app.safe_output_text(finder_name)}</b>"

        if raccoon_can_hide(round_data.base_word):
            if final:
                return "🦝 Енот прятался среди букв, но так и остался незамеченным."
            return "🦝 Где-то среди этих букв прячется енот. Кто его найдет?"

        return "🦝 Енот не обнаружен среди букв, но все равно играет с вами."

    def render_start(self, round_data: WordGameRound) -> str:
        rendered = super().render_start(round_data)
        return f"{rendered}\n\n{self._raccoon_line(round_data)}"

    def render_status(self, round_data: WordGameRound) -> str:
        rendered = super().render_status(round_data)
        return f"{rendered}\n\n{self._raccoon_line(round_data)}"

    def render_finish(self, round_data: WordGameRound, players: list[PlayerResult]) -> str:
        rendered = super().render_finish(round_data, players)
        return f"{rendered}\n\n{self._raccoon_line(round_data, final=True)}"

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
        """Delegate scoring, then send the sticker once when «енот» is newly found."""
        round_data = self.active_word_games.get(self.round_key_from_message(message))
        was_found = bool(round_data and RACCOON_WORD in round_data.used_words)

        await super().handle_word_guess(message)

        if round_data is None or was_found or RACCOON_WORD not in round_data.used_words:
            return

        owner_id = round_data.used_words.get(RACCOON_WORD)
        if message.from_user is None or owner_id != message.from_user.id:
            return

        announcement_key = (
            round_data.chat_id,
            round_data.message_thread_id,
            round_data.round_code,
        )
        if announcement_key in self._raccoon_sticker_announced_rounds:
            return
        self._raccoon_sticker_announced_rounds.add(announcement_key)
        await self._send_raccoon_sticker(round_data)


def register_lexicon_learning_handlers(app: Any, service: LearningLexiconService) -> None:
    """Register base Lexicon commands plus raccoon sticker configuration."""
    register_base_lexicon_learning_handlers(app, service)
    dispatcher = app.dp

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

    @dispatcher.message(Command("raccoon_sticker_status"))
    async def raccoon_sticker_status(message: Message) -> None:
        if not service.app.is_group_chat(message) or not service.app.is_allowed_chat(message.chat.id):
            return
        status = "настроен" if service.raccoon_sticker_file_id else "не настроен"
        await message.reply(
            f"🦝 Стикер енота: <b>{status}</b>.\n"
            "Для замены ответь на новый стикер командой <code>/set_raccoon_sticker</code>."
        )

    # Keep these commands above the Lexicon-only guard and legacy handlers.
    dispatcher.message.handlers.insert(0, dispatcher.message.handlers.pop())
    dispatcher.message.handlers.insert(0, dispatcher.message.handlers.pop())


print(
    "LEXICON_RACCOON_READY word=енот start=on found_line=on finish=on absent_companion=on sticker= configurable",
    flush=True,
)

__all__ = ["LearningLexiconService", "register_lexicon_learning_handlers"]
