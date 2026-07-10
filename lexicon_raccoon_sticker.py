"""Persistent Telegram sticker configuration for the Lexicon raccoon.

Telegram stickers are resent by file_id. The value is stored in DATA_DIR so it
survives BotHost redeploys and process restarts.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
RACCOON_STICKER_PATH = DATA_DIR / "lexicon_raccoon_sticker.txt"
RACCOON_STICKER_BACKUP_PATH = DATA_DIR / "lexicon_raccoon_sticker.backup.txt"
FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,512}$")


def normalize_sticker_file_id(value: str) -> str:
    return value.strip()


def is_valid_sticker_file_id(value: str) -> bool:
    return bool(FILE_ID_RE.fullmatch(normalize_sticker_file_id(value)))


def load_raccoon_sticker_file_id() -> str | None:
    """Load the configured sticker from the primary file or its backup."""
    for path in (RACCOON_STICKER_PATH, RACCOON_STICKER_BACKUP_PATH):
        if not path.is_file():
            continue
        try:
            value = normalize_sticker_file_id(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        if is_valid_sticker_file_id(value):
            return value
    return None


def save_raccoon_sticker_file_id(file_id: str) -> str:
    """Atomically save a Telegram sticker file_id and retain a backup."""
    normalized = normalize_sticker_file_id(file_id)
    if not is_valid_sticker_file_id(normalized):
        raise ValueError("Invalid Telegram sticker file_id")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary_path = RACCOON_STICKER_PATH.with_suffix(".tmp")

    with temporary_path.open("w", encoding="utf-8") as file:
        file.write(normalized + "\n")
        file.flush()
        os.fsync(file.fileno())

    if RACCOON_STICKER_PATH.is_file():
        try:
            RACCOON_STICKER_BACKUP_PATH.write_text(
                RACCOON_STICKER_PATH.read_text(encoding="utf-8", errors="ignore"),
                encoding="utf-8",
            )
        except OSError:
            pass

    os.replace(temporary_path, RACCOON_STICKER_PATH)
    return normalized


__all__ = [
    "RACCOON_STICKER_PATH",
    "load_raccoon_sticker_file_id",
    "save_raccoon_sticker_file_id",
]
