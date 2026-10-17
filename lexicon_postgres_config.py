"""PostgreSQL source of truth for Lexicon admin words and raccoon sticker.

Use one bounded, reusable connection (rather than opening a brand-new TCP
connection for every /lexadd). Existing local files are imported without
overwriting newer PostgreSQL values. Repeated writes are idempotent.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

import asyncpg

from lexicon_approved_storage import APPROVED_WORD_RE, normalize_approved_word
from lexicon_raccoon_sticker import is_valid_sticker_file_id

LOGGER = logging.getLogger(__name__)
_STICKER_KEY = "raccoon_sticker_file_id"
_RESULT = TypeVar("_RESULT")
_RETRYABLE_SQLSTATES = frozenset({"53300", "57P01", "57P02", "57P03"})


def _is_retryable(exc: Exception) -> bool:
    """Retry only connection failures; never hide SQL/auth/schema errors."""
    state = getattr(exc, "sqlstate", None)
    return (
        isinstance(exc, (OSError, asyncio.TimeoutError, asyncpg.InterfaceError))
        or (isinstance(state, str) and (state.startswith("08") or state in _RETRYABLE_SQLSTATES))
    )


class LexiconPostgresConfig:
    def __init__(self, database_url: str) -> None:
        if not database_url.strip():
            raise ValueError("DATABASE_URL is required for PostgreSQL Lexicon persistence")
        self.database_url = database_url
        self._pool: asyncpg.Pool | None = None
        self._pool_lock = asyncio.Lock()

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            async with self._pool_lock:
                if self._pool is None:
                    # Other modules also have pools on BotHost. This one can
                    # use at most ONE database connection, with no eager open.
                    self._pool = await asyncpg.create_pool(
                        dsn=self.database_url,
                        min_size=0,
                        max_size=1,
                        timeout=5,
                        command_timeout=10,
                        max_inactive_connection_lifetime=45,
                    )
        return self._pool

    async def close(self) -> None:
        async with self._pool_lock:
            pool = self._pool
            self._pool = None
            if pool is not None:
                await pool.close()

    async def _run(
        self,
        operation: str,
        call: Callable[[asyncpg.Connection], Awaitable[_RESULT]],
    ) -> _RESULT:
        # INSERT and UPSERT operations below are safe to retry. Never retry
        # arbitrary database mutations here without assessing idempotency.
        for attempt in range(2):
            try:
                pool = await self._get_pool()
                async with pool.acquire(timeout=5) as conn:
                    return await call(conn)
            except Exception as exc:
                if attempt or not _is_retryable(exc):
                    raise
                LOGGER.warning(
                    "LEXICON_POSTGRES_TRANSIENT_FAILURE operation=%s error_type=%s sqlstate=%s retry=1",
                    operation,
                    type(exc).__name__,
                    getattr(exc, "sqlstate", "") or "-",
                )
                await asyncio.sleep(0.2)
        raise AssertionError("unreachable")

    async def bootstrap(
        self, local_words: set[str], local_sticker: str | None
    ) -> tuple[set[str], str | None]:
        """Import local files and return DB state; no records are deleted."""
        words = {
            normalized
            for word in local_words
            if (normalized := normalize_approved_word(word))
            and APPROVED_WORD_RE.fullmatch(normalized)
        }

        async def load(conn: asyncpg.Connection) -> tuple[set[str], str | None]:
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

        return await self._run("bootstrap", load)

    async def add_word(self, word: str) -> None:
        normalized = normalize_approved_word(word)
        if not APPROVED_WORD_RE.fullmatch(normalized):
            raise ValueError("Invalid Lexicon approved word")

        async def save(conn: asyncpg.Connection) -> None:
            await conn.execute(
                """
                INSERT INTO lexicon_approved_words (word)
                VALUES ($1) ON CONFLICT (word) DO NOTHING
                """,
                normalized,
            )

        await self._run("add_word", save)

    async def set_sticker(self, file_id: str) -> None:
        if not is_valid_sticker_file_id(file_id):
            raise ValueError("Invalid Telegram sticker file_id")

        async def save(conn: asyncpg.Connection) -> None:
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

        await self._run("set_sticker", save)

    async def inspect(self) -> tuple[int, bool]:
        """Read-only diagnostics: DB word count and INSERT privilege."""
        async def read(conn: asyncpg.Connection) -> tuple[int, bool]:
            count = await conn.fetchval("SELECT COUNT(*) FROM lexicon_approved_words")
            can_insert = await conn.fetchval(
                "SELECT has_table_privilege(current_user, 'lexicon_approved_words', 'INSERT')"
            )
            return int(count), bool(can_insert)

        return await self._run("inspect", read)
