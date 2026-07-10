"""Persistent and balanced wrapper for the stable Lexicon learning service.

This module keeps admin-approved words crash-safe, applies a capped scoring
model and normalizes topicless/general-topic round keys.
"""

from __future__ import annotations

import logging
from typing import Any

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
from lexicon_round_scope import normalized_round_key, round_key_from_message
from lexicon_scoring import BALANCED_SCORING_TEXT, balanced_word_points, player_rank_key
from minigames import ATMOSPHERIC_WORDS, PlayerResult, WordGameRound

LOGGER = logging.getLogger(__name__)
STORAGE_ERROR_MESSAGE = "Не удалось надежно сохранить слово. Попробуй еще раз."
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

__all__ = ["LearningLexiconService", "register_lexicon_learning_handlers"]
