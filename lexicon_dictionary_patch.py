"""Dictionary-strength patch for the Lexicon mini-game.

This layer keeps the core rule strict: only nouns in their base form are accepted.
It also extends the source-word pool with curated long words. Every source still
passes the engine's answer-count filter before it can appear in a live round.
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram.types import ChatMemberAdministrator, Message

from lexicon_live_patch import MORPH, WORD_RE, PatchedMiniGameService
from lexicon_source_words import LONG_SOURCE_WORDS
from minigames import MIN_WORD_LENGTH

LOGGER = logging.getLogger(__name__)
MIN_SOURCE_LENGTH = 10
MAX_SOURCE_LENGTH = 24


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

    @classmethod
    def load_source_words(cls) -> list[str]:
        """Return original plus curated long source words without duplicates.

        Curated sources are trusted as source material, but they are not guaranteed
        a live round: build_round_candidates still rejects words that produce too
        few valid answers.
        """
        source_words = list(super().load_source_words())
        seen = set(source_words)

        for raw_word in LONG_SOURCE_WORDS:
            word = cls.normalize_word(raw_word)
            if word in seen:
                continue
            if not (MIN_SOURCE_LENGTH <= len(word) <= MAX_SOURCE_LENGTH):
                continue
            if not WORD_RE.match(word):
                continue
            seen.add(word)
            source_words.append(word)

        LOGGER.info(
            "Lexicon source bank expanded: original=%s curated=%s total=%s",
            len(source_words) - sum(1 for word in LONG_SOURCE_WORDS if cls.normalize_word(word) in seen),
            len(LONG_SOURCE_WORDS),
            len(source_words),
        )
        return source_words

    @staticmethod
    def _can_pin_from_member(member: Any) -> bool:
        if getattr(member, "status", None) == "creator":
            return True
        if isinstance(member, ChatMemberAdministrator):
            return bool(getattr(member, "can_pin_messages", False))
        return bool(getattr(member, "can_pin_messages", False))

    async def pin_start_message(self, message: Message) -> None:
        """Pin the Lexicon source page, with a precise rights diagnostic."""
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
