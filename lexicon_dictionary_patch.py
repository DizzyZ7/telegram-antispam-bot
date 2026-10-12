"""Dictionary-strength patch for the Lexicon mini-game.

This layer keeps the core rule strict: only nouns in their base form are accepted.
It extends the source-word pool with curated long words and rotates sources
without repeats inside each persisted global cycle.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

from aiogram.types import ChatMemberAdministrator, Message

from lexicon_live_patch import MORPH, WORD_RE, PatchedMiniGameService
from lexicon_source_words import LONG_SOURCE_WORDS
from minigames import GameKey, MIN_WORD_LENGTH, WordGameRound
from writers_moderation import contains_prohibited_language

LOGGER = logging.getLogger(__name__)
MIN_SOURCE_LENGTH = 10
MAX_SOURCE_LENGTH = 24
SOURCE_CYCLE_THREAD_NONE = 0


class LexiconSourceExhausted(RuntimeError):
    """No source available now (empty pool or all candidates active)."""


class LexiconSourceStorageError(RuntimeError):
    """Durable cycle reservation failed; do not bypass rotation on errors."""


def _source_letter_bag(word: str) -> str:
    """Canonical multiset; exclude equivalent starter words within each cycle."""
    return "".join(sorted(word.casefold().replace("ё", "е")))




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
            if not WORD_RE.match(word) or contains_prohibited_language(word):
                continue
            # Curated packs are ideas, not proof of grammatical validity:
            # only canonical noun lemmas may appear as starting words.
            if MORPH is not None and not cls.is_valid_dictionary_lemma(word):
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

    async def _ensure_source_claims_locked(self) -> None:
        """Upgrade the v3 permanent ledger into a resumable cyclic ledger.

        Claims represent ONLY the currently running global cycle. The state
        marker is durable: old cycle/archive data is imported exactly once,
        otherwise historic words would return after every bot restart.
        """
        connection = self.storage.connection
        if connection is None:
            raise LexiconSourceStorageError("Lexicon database not initialized")
        await self._ensure_source_cycle_table_locked()
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS wordgame_source_claims (
                source_word TEXT PRIMARY KEY,
                letter_bag TEXT NOT NULL UNIQUE,
                chat_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL,
                used_at INTEGER NOT NULL
            )
            """
        )
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS wordgame_source_cycle_state (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                cycle_no INTEGER NOT NULL CHECK(cycle_no >= 1),
                last_word TEXT NOT NULL DEFAULT ''
            )
            """
        )
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS wordgame_source_cycle_history (
                cycle_no INTEGER NOT NULL,
                source_word TEXT NOT NULL,
                letter_bag TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL,
                used_at INTEGER NOT NULL,
                PRIMARY KEY(cycle_no, letter_bag)
            )
            """
        )
        if getattr(self, "_source_claims_imported", False):
            return
        async with connection.execute(
            "SELECT cycle_no FROM wordgame_source_cycle_state WHERE id = 1"
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            # Reuse all pre-v4 reservations, including old empty rounds still
            # present in legacy cycles and finished rounds in the archive.
            async with connection.execute(
                "SELECT source_word, chat_id, thread_id, used_at "
                "FROM wordgame_source_cycle ORDER BY id ASC"
            ) as cursor:
                cycle_rows = await cursor.fetchall()
            async with connection.execute(
                "SELECT base_word, chat_id, created_at "
                "FROM wordgame_round_archive ORDER BY id ASC"
            ) as cursor:
                archive_rows = await cursor.fetchall()

            for word, chat_id, thread_id, stamp in cycle_rows:
                normalized = str(word or "").casefold().replace("ё", "е")
                if normalized:
                    await connection.execute(
                        "INSERT OR IGNORE INTO wordgame_source_claims"
                        "(source_word,letter_bag,chat_id,thread_id,used_at)"
                        "VALUES(?,?,?,?,?)",
                        (normalized, _source_letter_bag(normalized),
                         int(chat_id), int(thread_id), int(stamp)),
                    )
            for word, chat_id, stamp in archive_rows:
                normalized = str(word or "").casefold().replace("ё", "е")
                if normalized:
                    await connection.execute(
                        "INSERT OR IGNORE INTO wordgame_source_claims"
                        "(source_word,letter_bag,chat_id,thread_id,used_at)"
                        "VALUES(?,?,?,?,?)",
                        (normalized, _source_letter_bag(normalized),
                         int(chat_id), SOURCE_CYCLE_THREAD_NONE, int(stamp)),
                    )
            async with connection.execute(
                "SELECT source_word FROM wordgame_source_claims ORDER BY rowid DESC LIMIT 1"
            ) as cursor:
                previous_row = await cursor.fetchone()
            last_word = str(previous_row[0]) if previous_row else ""
            await connection.execute(
                "INSERT INTO wordgame_source_cycle_state(id,cycle_no,last_word) "
                "VALUES(1,1,?)", (last_word,),
            )
            await connection.commit()
            LOGGER.info(
                "LEXICON_SOURCE_CYCLE_MIGRATED old_cycle_rows=%s "
                "old_archive_rows=%s",
                len(cycle_rows), len(archive_rows),
            )
        self._source_claims_imported = True

    async def source_cycle_status(self) -> tuple[int, int] | None:
        """Current cycle number and as-yet-unused source count, spoiler-free."""
        connection = self.storage.connection
        if connection is None:
            return None
        try:
            async with self.storage.lock:
                await self._ensure_source_claims_locked()
                async with connection.execute(
                    "SELECT cycle_no FROM wordgame_source_cycle_state WHERE id = 1"
                ) as cursor:
                    row = await cursor.fetchone()
                async with connection.execute(
                    "SELECT letter_bag FROM wordgame_source_claims"
                ) as cursor:
                    used_bags = {str(item[0]) for item in await cursor.fetchall()}
                used_bags.update(
                    _source_letter_bag(round_data.base_word)
                    for round_data in self.active_word_games.values()
                )
                candidate_bags = {
                    _source_letter_bag(word) for word, _answers in self.round_candidates
                }
                remaining = len(candidate_bags - used_bags)
                return int(row[0]), remaining
        except Exception:
            LOGGER.exception("Could not read current Lexicon cycle")
            return None

    async def count_unplayed_sources(self) -> int | None:
        """Backwards compatible source-pool counter for the current cycle."""
        status = await self.source_cycle_status()
        return status[1] if status is not None else None

    async def _claim_next_source(self, game_key: GameKey) -> tuple[str, set[str]]:
        """Durably claim one source per global cycle, then restart the cycle.

        The lock + BEGIN IMMEDIATE protect concurrent topics, and UNIQUE
        letter_bag blocks anagrams inside each cycle. Active rounds never
        clash with the next selection, including across a cycle boundary.
        """
        candidate_map = {
            word: set(allowed) for word, allowed in self.round_candidates
        }
        if not candidate_map:
            raise LexiconSourceExhausted("Lexicon has no playable sources")
        connection = self.storage.connection
        if connection is None:
            raise LexiconSourceStorageError("Lexicon database unavailable")

        chat_id, message_thread_id = game_key
        thread_id = (
            message_thread_id if message_thread_id is not None
            else SOURCE_CYCLE_THREAD_NONE
        )
        try:
            async with self.storage.lock:
                await self._ensure_source_claims_locked()
                await connection.execute("BEGIN IMMEDIATE")
                try:
                    async with connection.execute(
                        "SELECT cycle_no,last_word FROM wordgame_source_cycle_state "
                        "WHERE id = 1"
                    ) as cursor:
                        state = await cursor.fetchone()
                    if state is None:
                        raise LexiconSourceStorageError("Cycle state not initialized")
                    cycle_no, last_word = int(state[0]), str(state[1])
                    async with connection.execute(
                        "SELECT letter_bag FROM wordgame_source_claims"
                    ) as cursor:
                        used_bags = {str(row[0]) for row in await cursor.fetchall()}
                    active_bags = {
                        _source_letter_bag(round_data.base_word)
                        for round_data in self.active_word_games.values()
                    }
                    available = [
                        word for word in candidate_map
                        if _source_letter_bag(word) not in used_bags
                        and _source_letter_bag(word) not in active_bags
                    ]
                    restarted = False
                    if not available:
                        # When all current sources have been claimed, start a
                        # new round of the full set, not a permanently dead game.
                        # Never reuse a still-active page, even on cycle change.
                        available = [
                            word for word in candidate_map
                            if _source_letter_bag(word) not in active_bags
                        ]
                        if not available:
                            raise LexiconSourceExhausted(
                                "Every source is currently held by active rounds"
                            )
                        # Avoid immediately repeating the last word when there
                        # is any other suitable candidate.
                        non_last = [
                            word for word in available
                            if _source_letter_bag(word) != _source_letter_bag(last_word)
                        ]
                        if non_last:
                            available = non_last
                        await connection.execute(
                            "INSERT OR IGNORE INTO wordgame_source_cycle_history"
                            "(cycle_no,source_word,letter_bag,chat_id,thread_id,used_at)"
                            " SELECT ?,source_word,letter_bag,chat_id,thread_id,used_at"
                            " FROM wordgame_source_claims",
                            (cycle_no,),
                        )
                        await connection.execute("DELETE FROM wordgame_source_claims")
                        cycle_no += 1
                        restarted = True

                    source_word = random.choice(available)
                    await connection.execute(
                        "INSERT INTO wordgame_source_claims"
                        "(source_word,letter_bag,chat_id,thread_id,used_at)"
                        "VALUES(?,?,?,?,?)",
                        (source_word, _source_letter_bag(source_word),
                         int(chat_id), int(thread_id), int(time.time())),
                    )
                    await connection.execute(
                        "UPDATE wordgame_source_cycle_state "
                        "SET cycle_no = ?, last_word = ? WHERE id = 1",
                        (cycle_no, source_word),
                    )
                    await connection.commit()
                    LOGGER.info(
                        "LEXICON_SOURCE_CLAIMED chat_id=%s thread_id=%s "
                        "cycle=%s source=%s available_before=%s restarted=%s",
                        chat_id, message_thread_id, cycle_no, source_word,
                        len(available), restarted,
                    )
                    return source_word, candidate_map[source_word]
                except Exception:
                    await connection.rollback()
                    raise
        except LexiconSourceExhausted:
            raise
        except Exception as exc:
            LOGGER.exception(
                "Lexicon source cycle reservation failed chat_id=%s "
                "thread_id=%s", chat_id, message_thread_id,
            )
            raise LexiconSourceStorageError(
                "Unable to reserve a cycle-unique source"
            ) from exc

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

            try:
                base_word, allowed = await self._claim_next_source(game_key)
            except LexiconSourceExhausted:
                await message.reply(
                    "📚 Все стартовые слова сейчас заняты активными играми. "
                    "Следующая страница откроется после завершения одной из них."
                )
                return
            except LexiconSourceStorageError:
                await message.reply(
                    "📚 Не удалось проверить историю стартовых слов. "
                    "Чтобы не нарушить порядок раундов, новая страница пока не открыта."
                )
                return
            now = time.monotonic()
            round_seconds = self.round_duration_seconds(len(allowed))
            round_data = WordGameRound(
                chat_id=message.chat.id,
                round_code=self.round_code(),
                base_word=base_word,
                allowed_words=allowed,
                min_length=MIN_WORD_LENGTH,
                started_at=now,
                ends_at=now + round_seconds,
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
