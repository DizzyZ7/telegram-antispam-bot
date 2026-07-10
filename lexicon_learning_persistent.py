"""Persistent wrapper for the stable Lexicon learning service.

This module changes only storage of admin-approved words. Game handlers, routing,
validation and round logic remain in lexicon_learning.py.
"""

from __future__ import annotations

import logging

from lexicon_approved_storage import (
    APPROVED_WORDS_JOURNAL_PATH,
    append_approved_word,
    load_approved_words,
    save_approved_snapshot,
)
from lexicon_learning import (
    LearningLexiconService as BaseLearningLexiconService,
    register_lexicon_learning_handlers,
    validate_lexicon_word,
)

LOGGER = logging.getLogger(__name__)
STORAGE_ERROR_MESSAGE = "Не удалось надежно сохранить слово. Попробуй еще раз."


class LearningLexiconService(BaseLearningLexiconService):
    """Learning Lexicon with journaled, atomic approved-word persistence."""

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


__all__ = ["LearningLexiconService", "register_lexicon_learning_handlers"]
