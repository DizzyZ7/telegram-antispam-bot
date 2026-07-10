"""Crash-safe persistent storage for admin-approved Lexicon words.

The existing /app/data/lexicon_approved_words.txt remains the canonical snapshot.
Every new word is also appended to a journal before the snapshot is replaced.
A backup snapshot is retained, so deploys or interrupted writes cannot erase the
community dictionary.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Iterable

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
APPROVED_WORDS_PATH = DATA_DIR / "lexicon_approved_words.txt"
APPROVED_WORDS_BACKUP_PATH = DATA_DIR / "lexicon_approved_words.backup.txt"
APPROVED_WORDS_JOURNAL_PATH = DATA_DIR / "lexicon_approved_words.journal.log"
APPROVED_WORD_RE = re.compile(r"^[а-яе]{4,32}$")


def normalize_approved_word(value: str) -> str:
    return value.strip().lower().replace("ё", "е").replace("-", "")


def _read_word_file(path: Path) -> set[str]:
    if not path.is_file():
        return set()

    words: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        word = normalize_approved_word(line)
        if APPROVED_WORD_RE.fullmatch(word):
            words.add(word)
    return words


def load_approved_words() -> set[str]:
    """Load the union of snapshot, backup and append-only journal."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    words: set[str] = set()
    for path in (
        APPROVED_WORDS_PATH,
        APPROVED_WORDS_BACKUP_PATH,
        APPROVED_WORDS_JOURNAL_PATH,
    ):
        words.update(_read_word_file(path))
    return words


def append_approved_word(word: str) -> str:
    """Durably append one approved word before changing the main snapshot."""
    normalized = normalize_approved_word(word)
    if not APPROVED_WORD_RE.fullmatch(normalized):
        raise ValueError(f"Invalid approved Lexicon word: {word!r}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with APPROVED_WORDS_JOURNAL_PATH.open("a", encoding="utf-8") as file:
        file.write(normalized + "\n")
        file.flush()
        os.fsync(file.fileno())
    return normalized


def save_approved_snapshot(words: Iterable[str]) -> None:
    """Atomically replace the canonical snapshot while retaining a backup."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    normalized_words = {
        normalize_approved_word(word)
        for word in words
        if APPROVED_WORD_RE.fullmatch(normalize_approved_word(word))
    }
    payload = "\n".join(sorted(normalized_words)) + ("\n" if normalized_words else "")
    temporary_path = APPROVED_WORDS_PATH.with_suffix(".tmp")

    with temporary_path.open("w", encoding="utf-8") as file:
        file.write(payload)
        file.flush()
        os.fsync(file.fileno())

    if APPROVED_WORDS_PATH.is_file():
        try:
            shutil.copy2(APPROVED_WORDS_PATH, APPROVED_WORDS_BACKUP_PATH)
        except OSError:
            # The journal still protects every newly approved word.
            pass

    os.replace(temporary_path, APPROVED_WORDS_PATH)
