"""PostgreSQL source of truth for Lexicon admin words and raccoon sticker.

Old /app/data files are imported without deleting or overwriting existing DB
entries. Configuration survives container replacement even if /app/data does not.
"""

from __future__ import annotations

import asyncpg

from lexicon_approved_storage import APPROVED_WORD_RE, normalize_approved_word
from lexicon_raccoon_sticker import is_valid_sticker_file_id

_STICKER_KEY = "raccoon_sticker_file_id"


class LexiconPostgresConfig:
    def __init__(self, database_url: str) -> None:
        if not database_url.strip():
            raise ValueError("DATABASE_URL is required for PostgreSQL Lexicon persistence")
        self.database_url = database_url

    async def bootstrap(
        self, local_words: set[str], local_sticker: str | None
    ) -> tuple[set[str], str | None]:
        """Migrate local data and return complete DB state (idempotently)."""
        words = {
            normalized
            for word in local_words
            if (normalized := normalize_approved_word(word))
            and APPROVED_WORD_RE.fullmatch(normalized)
        }
        conn = await asyncpg.connect(self.database_url)
        try:
            async with conn.transaction():
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS lexicon_approved_words (
                        word TEXT PRIMARY KEY,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS lexicon_config (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                if words:
                    await conn.executemany(
                        """
                        INSERT INTO lexicon_approved_words (word)
                        VALUES ($1) ON CONFLICT (word) DO NOTHING
                        """,
                        [(word,) for word in sorted(words)],
                    )
                if local_sticker and is_valid_sticker_file_id(local_sticker):
                    # An old local backup must never revert a newer DB setting.
                    await conn.execute(
                        """
                        INSERT INTO lexicon_config (key, value)
                        VALUES ($1, $2) ON CONFLICT (key) DO NOTHING
                        """,
                        _STICKER_KEY,
                        local_sticker,
                    )
                stored_words = await conn.fetch("SELECT word FROM lexicon_approved_words")
                stored_sticker = await conn.fetchval(
                    "SELECT value FROM lexicon_config WHERE key = $1", _STICKER_KEY
                )
            approved = {
                normalized
                for row in stored_words
                if (normalized := normalize_approved_word(str(row["word"])))
                and APPROVED_WORD_RE.fullmatch(normalized)
            }
            sticker = (
                str(stored_sticker)
                if stored_sticker and is_valid_sticker_file_id(str(stored_sticker))
                else None
            )
            return approved, sticker
        finally:
            await conn.close()

    async def add_word(self, word: str) -> None:
        normalized = normalize_approved_word(word)
        if not APPROVED_WORD_RE.fullmatch(normalized):
            raise ValueError("Invalid Lexicon approved word")
        conn = await asyncpg.connect(self.database_url)
        try:
            await conn.execute(
                """
                INSERT INTO lexicon_approved_words (word)
                VALUES ($1) ON CONFLICT (word) DO NOTHING
                """,
                normalized,
            )
        finally:
            await conn.close()

    async def set_sticker(self, file_id: str) -> None:
        if not is_valid_sticker_file_id(file_id):
            raise ValueError("Invalid Telegram sticker file_id")
        conn = await asyncpg.connect(self.database_url)
        try:
            await conn.execute(
                """
                INSERT INTO lexicon_config (key, value)
                VALUES ($1, $2)
                ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value, updated_at = NOW()
                """,
                _STICKER_KEY,
                file_id,
            )
        finally:
            await conn.close()
