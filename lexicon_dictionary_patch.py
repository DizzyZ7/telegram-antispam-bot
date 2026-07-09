"""Dictionary-strength patch for the Lexicon mini-game.

This layer keeps the core rule strict: only nouns in their base form are accepted.
But it removes the overly aggressive morphology score threshold that could reject
valid known words when pymorphy3 had several parses for the same token.
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram.types import ChatMemberAdministrator, Message

from lexicon_live_patch import MORPH, WORD_RE, PatchedMiniGameService
from minigames import MIN_WORD_LENGTH

LOGGER = logging.getLogger(__name__)


class DictionaryBackedLexiconService(PatchedMiniGameService):
    """Lexicon service using known dictionary noun lemmas as the acceptance source."""

    @staticmethod
    def is_valid_dictionary_lemma(word: str) -> bool:
        """Accept known Russian noun lemmas and reject inflected forms.

        Valid examples: станция, трактор, контур, утка.
        Rejected examples: станцией, станцию, станции, красивый, быстро.
        """
        if MORPH is None:
            return False
        if len(word) < MIN_WORD_LENGTH or not WORD_RE.match(word):
            return False

        for parse in MORPH.parse(word)[:8]:
            tag = parse.tag
            normal_form = parse.normal_form.replace("ё", "е")
            if getattr(parse, "is_known", True) is False:
                continue
            if tag.POS != "NOUN":
                continue
            if normal_form != word:
                continue
            if "nomn" in tag or normal_form == word:
                return True
        return False

    @classmethod
    def load_dictionary_words(cls) -> set[str]:
        words = super().load_dictionary_words()
        LOGGER.info("Lexicon dictionary loaded with %s base-form noun entries", len(words))
        return words

    @staticmethod
    def _can_pin_from_member(member: Any) -> bool:
        if getattr(member, "status", None) == "creator":
            return True
        if isinstance(member, ChatMemberAdministrator):
            return bool(getattr(member, "can_pin_messages", False))
        return bool(getattr(member, "can_pin_messages", False))

    async def pin_start_message(self, message: Message) -> None:
        """Pin the Lexicon source page, with a precise rights diagnostic.

        Telegram may show a bot as an administrator while the separate
        `can_pin_messages` permission is disabled. In that case pinning always
        fails, so we tell the chat owner exactly what to enable.
        """
        try:
            me = await self.app.bot.get_me()
            member = await self.app.bot.get_chat_member(message.chat.id, me.id)
            if not self._can_pin_from_member(member):
                await message.reply(
                    "📌 Fosgen видит страницу Лексикона, но не может ее закрепить.\n"
                    "Он администратор без отдельного права <b>Закрепление сообщений</b>.\n\n"
                    "Открой настройки чата → Администраторы → Fosgen → включи право закреплять сообщения."
                )
                LOGGER.warning(
                    "Lexicon pin skipped: missing can_pin_messages chat_id=%s bot_id=%s member=%r",
                    message.chat.id,
                    me.id,
                    member,
                )
                return
        except Exception:
            LOGGER.info("Could not pre-check Lexicon pin permissions", exc_info=True)

        try:
            await self.app.bot.pin_chat_message(
                chat_id=message.chat.id,
                message_id=message.message_id,
                disable_notification=True,
            )
            LOGGER.info("Lexicon source page pinned chat_id=%s message_id=%s", message.chat.id, message.message_id)
        except Exception as exc:
            LOGGER.warning(
                "Could not pin Lexicon round message chat_id=%s message_id=%s: %s",
                message.chat.id,
                message.message_id,
                exc,
                exc_info=True,
            )
            try:
                await message.reply(
                    "📌 Не смог закрепить страницу Лексикона.\n"
                    "Чаще всего причина — у Fosgen нет права <b>Закрепление сообщений</b>, "
                    "даже если он уже администратор."
                )
            except Exception:
                LOGGER.info("Could not send Lexicon pin failure notice", exc_info=True)
