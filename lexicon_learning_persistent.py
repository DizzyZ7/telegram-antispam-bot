"""Persistent and balanced wrapper for the stable Lexicon learning service.

This module keeps admin-approved words crash-safe, applies a capped scoring
model, normalizes topicless/general-topic round keys and explains letter deficits.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from aiogram.types import Message

from lexicon_approved_storage import (
    APPROVED_WORDS_JOURNAL_PATH,
    append_approved_word,
    load_approved_words,
    save_approved_snapshot,
)
from lexicon_balanced_storage import save_balanced_round
from lexicon_game_scope import LEXICON_ONLY_CHAT_IDS
from lexicon_learning import (
    LearningLexiconService as BaseLearningLexiconService,
    register_lexicon_learning_handlers,
    validate_lexicon_word,
)
from lexicon_letter_feedback import (
    format_missing_letters,
    missing_letter_counts,
    missing_letters_label,
)
from lexicon_round_scope import (
    normalized_round_key,
    round_key_from_message,
    russian_word_count_form,
)
from lexicon_scoring import BALANCED_SCORING_TEXT, balanced_word_points, player_rank_key
from minigames import (
    ATMOSPHERIC_WORDS,
    MIN_WORD_LENGTH,
    ROUND_SECONDS,
    WORD_RE,
    PlayerResult,
    WordGameRound,
)

LOGGER = logging.getLogger(__name__)
STORAGE_ERROR_MESSAGE = "Не удалось надежно сохранить слово. Попробуй еще раз."
MISSING_LETTER_NOTICE_COOLDOWN_SECONDS = 4.0
LEGACY_SCORING_TEXT = (
    "Чем длиннее слово, тем больше звезд:\n"
    "4 буквы — 1🌟\n"
    "5 букв — 2🌟\n"
    "6 букв — 4🌟\n"
    "7 букв — 7🌟\n"
    "8+ букв — 10🌟 и выше"
)


class LearningLexiconService(BaseLearningLexiconService):
    """Learning Lexicon with durable words, balanced scoring and stable scopes."""

    @classmethod
    def round_key(cls, chat_id: int, message_thread_id: int | None) -> tuple[int, int | None]:
        return normalized_round_key(
            chat_id,
            message_thread_id,
            single_scope_chat_ids=LEXICON_ONLY_CHAT_IDS,
        )

    @classmethod
    def round_key_from_message(cls, message: Any) -> tuple[int, int | None]:
        return round_key_from_message(
            message,
            single_scope_chat_ids=LEXICON_ONLY_CHAT_IDS,
        )

    async def start_word_game(self, message: Message) -> None:
        """Start a round using the same canonical scope later commands will query."""
        if not self.app.is_group_chat(message) or not self.app.is_allowed_chat(message.chat.id):
            return
        if message.from_user is None or message.from_user.is_bot:
            return

        game_key = self.round_key_from_message(message)
        normalized_thread_id = game_key[1]

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
                message_thread_id=normalized_thread_id,
            )
            self.active_word_games[game_key] = round_data
            round_data.finish_task = asyncio.create_task(self.finish_later(round_data))

        LOGGER.info(
            "LEXICON_ROUND_STARTED chat_id=%s raw_thread_id=%s normalized_thread_id=%s forum=%s",
            message.chat.id,
            message.message_thread_id,
            normalized_thread_id,
            getattr(message.chat, "is_forum", None),
        )
        sent_message = await message.answer(self.render_start(round_data))
        await self.pin_start_message(sent_message)

    async def handle_word_guess(self, message: Message) -> None:
        """Explain missing source letters before delegating valid attempts."""
        if not self.app.is_group_chat(message) or not self.app.is_allowed_chat(message.chat.id):
            return
        if message.from_user is None or message.from_user.is_bot:
            return
        if not message.text or message.text.startswith("/"):
            return

        game_key = self.round_key_from_message(message)
        round_data = self.active_word_games.get(game_key)
        if round_data is None or round_data.ends_at <= time.monotonic():
            return

        raw_word = message.text.strip()
        if " " in raw_word or "\n" in raw_word or not WORD_RE.fullmatch(raw_word):
            return

        word = self.normalize_word(raw_word)
        if len(word) < round_data.min_length or word == round_data.base_word:
            await super().handle_word_guess(message)
            return

        missing = missing_letter_counts(word, round_data.base_word)
        if missing:
            now = time.monotonic()
            notice_key = (message.chat.id, game_key[1], message.from_user.id)
            notice_map = getattr(self, "_missing_letter_notice_at", None)
            if notice_map is None:
                notice_map = {}
                self._missing_letter_notice_at = notice_map

            previous_notice = float(notice_map.get(notice_key, 0.0))
            if now - previous_notice >= MISSING_LETTER_NOTICE_COOLDOWN_SECONDS:
                notice_map[notice_key] = now
                formatted = format_missing_letters(missing)
                label = missing_letters_label(missing)
                saved_line = (
                    "\n\nСлово сохранено в словаре Лексикона и сможет сыграть в другом раунде."
                    if word in self.approved_words
                    else ""
                )
                await message.reply(
                    "📖 <b>Слово не подходит к этой странице</b>\n\n"
                    f"<code>{self.app.safe_output_text(word)}</code>\n"
                    f"Не хватает {label}: <b>{self.app.safe_output_text(formatted)}</b>.\n"
                    "Этих букв нет или их недостаточно в слове-источнике."
                    f"{saved_line}"
                )
                LOGGER.info(
                    "LEXICON_WORD_MISSING_LETTERS chat_id=%s thread_id=%s user_id=%s word=%s missing=%s approved=%s",
                    message.chat.id,
                    game_key[1],
                    message.from_user.id,
                    word,
                    formatted,
                    word in self.approved_words,
                )
            return

        await super().handle_word_guess(message)

    @classmethod
    def load_approved_words(cls) -> set[str]:
        try:
            words = load_approved_words()

            # One-time migration of the old canonical txt into the append-only
            # journal. Existing words are never deleted during this operation.
            if words and not APPROVED_WORDS_JOURNAL_PATH.is_file():
                for word in sorted(words):
                    append_approved_word(word)

            # Heal/recreate the canonical snapshot from all available copies.
            save_approved_snapshot(words)
            print(
                f"LEXICON_APPROVED_STORAGE_READY words={len(words)} "
                "snapshot=on journal=on backup=on atomic=on",
                flush=True,
            )
            return words
        except Exception:
            LOGGER.exception("Could not load redundant approved Lexicon storage")
            # Last-resort compatibility path from the original service. This still
            # reads the existing canonical file instead of discarding user data.
            return super().load_approved_words()

    @classmethod
    def save_approved_words(cls) -> None:
        save_approved_snapshot(cls.approved_words)

    def add_approved_word(self, raw_word: str, *, admin_override: bool = True) -> tuple[bool, str]:
        ok, result = validate_lexicon_word(self, raw_word, admin_override=admin_override)
        if not ok:
            return False, result

        word = result
        if word in self.approved_words:
            self.dictionary_words.add(word)
            return True, word

        # Journal first. We never tell the admin that a word was added until its
        # durable append has succeeded.
        try:
            append_approved_word(word)
        except Exception:
            LOGGER.exception("Could not append approved Lexicon word=%s", word)
            return False, STORAGE_ERROR_MESSAGE

        self.approved_words.add(word)
        self.dictionary_words.add(word)

        # Snapshot failure is non-fatal because the journal already contains the
        # word and load_approved_words() restores it on the next process start.
        try:
            save_approved_snapshot(self.approved_words)
        except Exception:
            LOGGER.exception("Could not refresh approved Lexicon snapshot word=%s", word)

        for _key, round_data in self.active_word_games.items():
            if self.can_build(word, round_data.base_word):
                round_data.allowed_words.add(word)
        return True, word

    @staticmethod
    def word_points(word: str) -> int:
        return balanced_word_points(word)

    def top_players(self, round_data: WordGameRound, limit: int = 7) -> list[PlayerResult]:
        return sorted(round_data.players.values(), key=player_rank_key)[:limit]

    def render_start(self, round_data: WordGameRound) -> str:
        rendered = super().render_start(round_data)
        total = len(round_data.allowed_words)
        legacy_count_line = f"В стартовом словаре страницы уже есть <b>{total}</b> слов."
        correct_count_line = (
            f"В стартовом словаре страницы уже есть <b>{total}</b> "
            f"{russian_word_count_form(total)}."
        )
        rendered = rendered.replace(legacy_count_line, correct_count_line, 1)

        if LEGACY_SCORING_TEXT not in rendered:
            LOGGER.warning("Lexicon scoring description marker was not found in start message")
            return rendered
        return rendered.replace(LEGACY_SCORING_TEXT, BALANCED_SCORING_TEXT, 1)

    def compact_found_reply(
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
        return (
            "✒️ <b>Слово принято</b>\n\n"
            f"{self.app.safe_output_text(player.name)} записывает:\n"
            f"<code>{self.app.safe_output_text(word)}</code>\n\n"
            f"+{points}🌟 · всего: <b>{total_points}🌟</b>\n"
            f"Слов у автора: <b>{len(player.words)}</b>\n"
            f"Страница заполнена на <b>{percent}%</b> — {found}/{total}"
        )

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
        if word in ATMOSPHERIC_WORDS:
            return (
                "🕯 <b>Атмосферная находка</b>\n\n"
                f"<code>{self.app.safe_output_text(word)}</code>\n\n"
                f"+{points}🌟 · всего: <b>{total_points}🌟</b>\n"
                f"Слов у автора: <b>{len(player.words)}</b>\n"
                f"Страница заполнена на <b>{percent}%</b> — {found}/{total}"
            )

        if points >= 4:
            label = "💎 <b>Редкая находка</b>" if points == 5 else "✨ <b>Сильная находка</b>"
            return (
                f"{label}\n\n"
                f"{self.app.safe_output_text(player.name)} забирает слово:\n"
                f"<code>{self.app.safe_output_text(word)}</code>\n\n"
                f"+{points}🌟 · всего: <b>{total_points}🌟</b>\n"
                f"Слов у автора: <b>{len(player.words)}</b>\n"
                f"Страница заполнена на <b>{percent}%</b> — {found}/{total}"
            )

        return self.compact_found_reply(
            player=player,
            word=word,
            points=points,
            total_points=total_points,
            found=found,
            total=total,
            percent=percent,
        )

    async def finish_word_game(self, chat_id: int, message_thread_id: int | None, forced: bool) -> None:
        game_key = self.round_key(chat_id, message_thread_id)
        async with self.lock:
            round_data = self.active_word_games.pop(game_key, None)
        if round_data is None:
            return
        if forced and round_data.finish_task is not None:
            round_data.finish_task.cancel()

        async with round_data.lock:
            players = sorted(round_data.players.values(), key=player_rank_key)
            found_count = len(round_data.used_words)
            total_words = len(round_data.allowed_words)

        await save_balanced_round(
            self.storage,
            chat_id=chat_id,
            players=players,
            day_key=self.today_key(),
            week_key=self.week_key(),
            round_code=round_data.round_code,
            base_word=round_data.base_word,
            found_count=found_count,
            total_words=total_words,
        )
        await self.app.bot.send_message(
            chat_id,
            self.render_finish(round_data, players),
            message_thread_id=round_data.message_thread_id,
        )


print(
    "LEXICON_SCORING_READY model=capped_v2 points=1-5 tie_break=words_then_longest historical=preserved",
    flush=True,
)
print(
    "LEXICON_ROUND_SCOPE_READY topicless=single general_topic=normalized real_topics=isolated",
    flush=True,
)
print(
    "LEXICON_LETTER_FEEDBACK_READY missing_letters=on approved_words=preserved cooldown=4s",
    flush=True,
)

__all__ = ["LearningLexiconService", "register_lexicon_learning_handlers"]
