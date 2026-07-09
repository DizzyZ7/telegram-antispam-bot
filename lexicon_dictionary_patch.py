"""Dictionary-strength patch for the Lexicon mini-game.

This layer keeps the core rule strict: only nouns in their base form are accepted.
But it removes the overly aggressive morphology score threshold that could reject
valid known words when pymorphy3 had several parses for the same token.
"""

from __future__ import annotations

import logging

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
