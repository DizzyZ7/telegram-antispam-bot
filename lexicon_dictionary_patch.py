"""Dictionary-strength patch for the Lexicon mini-game.

This layer keeps the core rule strict: only nouns in their base form are accepted.
It extends the source-word pool with curated long words and rotates all playable
sources without repeats until the current per-topic cycle is complete.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from aiogram.types import ChatMemberAdministrator, Message

from lexicon_live_patch import MORPH, WORD_RE, PatchedMiniGameService
from lexicon_source_cycle import choose_cycle_word
from lexicon_source_words import LONG_SOURCE_WORDS
from minigames import GameKey, MIN_WORD_LENGTH, ROUND_SECONDS, WordGameRound

LOGGER = logging.getLogger(__name__)
MIN_SOURCE_LENGTH = 10
MAX_SOURCE_LENGTH = 24
SOURCE_CYCLE_THREAD_NONE = 0
SOURCE_CYCLES_TO_KEEP = 3


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
        original_count = len(source_words)
        seen = set(source_words)
        added_count = 0

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
            added_count += 1

        LOGGER.info(
            "Lexicon source bank expanded: original=%s curated_added=%s total=%s",
            original_count,
            added_count,
            len(source_words),
        )
        return source_words

    async def _ensure_source_cycle_table_locked(self) -> None:
        connection = self.storage.connection
        if connection is None:
            raise RuntimeError("Mini-game storage is not initialized")

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS wordgame_source_cycle (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL,
                cycle_no INTEGER NOT NULL,
                source_word TEXT NOT NULL,
                used_at INTEGER NOT NULL,
                UNIQUE(chat_id, thread_id, cycle_no, source_word)
            )
            """
        )
        await connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_wordgame_source_cycle_scope "
            "ON wordgame_source_cycle(chat_id, thread_id, cycle_no, id)"
        )

    async def _claim_next_source(self, game_key: GameKey) -> tuple[str, set[str]]:
        """Atomically claim the next source for a chat topic.

        Every playable source is used once per cycle. State lives in SQLite, so a
        BotHost restart does not reset the cycle or cause early repetitions.
        """
        if not self.round_candidates:
            return self.choose_base_word()

        candidate_map = {
            base_word: set(allowed_words)
            for base_word, allowed_words in self.round_candidates
        }
        candidate_words = list(candidate_map)
        chat_id, message_thread_id = game_key
        thread_id = message_thread_id if message_thread_id is not None else SOURCE_CYCLE_THREAD_NONE
        connection = self.storage.connection
        if connection is None:
            LOGGER.warning("Lexicon source cycle fallback: storage is not initialized")
            return self.choose_base_word()

        try:
            async with self.storage.lock:
                await self._ensure_source_cycle_table_locked()

                async with connection.execute(
                    """
                    SELECT MAX(cycle_no)
                    FROM wordgame_source_cycle
                    WHERE chat_id = ? AND thread_id = ?
                    """,
                    (chat_id, thread_id),
                ) as cursor:
                    cycle_row = await cursor.fetchone()
                cycle_no = int(cycle_row[0]) if cycle_row and cycle_row[0] is not None else 1

                async with connection.execute(
                    """
                    SELECT source_word
                    FROM wordgame_source_cycle
                    WHERE chat_id = ? AND thread_id = ? AND cycle_no = ?
                    """,
                    (chat_id, thread_id, cycle_no),
                ) as cursor:
                    used_rows = await cursor.fetchall()
                used_words = {str(row[0]) for row in used_rows if str(row[0]) in candidate_map}

                async with connection.execute(
                    """
                    SELECT source_word
                    FROM wordgame_source_cycle
                    WHERE chat_id = ? AND thread_id = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (chat_id, thread_id),
                ) as cursor:
                    last_row = await cursor.fetchone()
                last_word = str(last_row[0]) if last_row else None

                source_word, restarted = choose_cycle_word(
                    candidate_words,
                    used_words,
                    last_word,
                )
                if restarted:
                    cycle_no += 1
                    used_words = set()

                await connection.execute(
                    """
                    INSERT INTO wordgame_source_cycle(
                        chat_id, thread_id, cycle_no, source_word, used_at
                    )
                    VALUES(?, ?, ?, ?, ?)
                    """,
                    (chat_id, thread_id, cycle_no, source_word, int(time.time())),
                )

                oldest_cycle_to_keep = max(1, cycle_no - SOURCE_CYCLES_TO_KEEP + 1)
                await connection.execute(
                    """
                    DELETE FROM wordgame_source_cycle
                    WHERE chat_id = ? AND thread_id = ? AND cycle_no < ?
                    """,
                    (chat_id, thread_id, oldest_cycle_to_keep),
                )
                await connection.commit()

            used_after_claim = len(used_words) + 1
            remaining = max(0, len(candidate_words) - used_after_claim)
            LOGGER.info(
                "LEXICON_SOURCE_CYCLE chat_id=%s thread_id=%s cycle=%s source=%s "
                "position=%s/%s remaining=%s restarted=%s",
                chat_id,
                message_thread_id,
                cycle_no,
                source_word,
                used_after_claim,
                len(candidate_words),
                remaining,
                restarted,
            )
            return source_word, candidate_map[source_word]
        except Exception:
            LOGGER.exception(
                "Lexicon source cycle failed; using safe random fallback chat_id=%s thread_id=%s",
                chat_id,
                message_thread_id,
            )
            return self.choose_base_word()

    async def start_word_game(self, message: Message) -> None:
        """Start a round with a persistent non-repeating source cycle."""
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

            base_word, allowed = await self._claim_next_source(game_key)
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

        sent_message = await message.answer(self.render_start(round_data))
        await self.pin_start_message(sent_message)

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
