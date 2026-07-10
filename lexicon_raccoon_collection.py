"""Persistent collection and leaderboard helpers for Lexicon raccoons.

Each found «енот» is stored once per chat and round. The event table is separate
from historical score tables, so existing points, wins and rounds remain intact.
"""

from __future__ import annotations

import time
from typing import Any, Literal

PeriodKind = Literal["all", "day", "week"]


async def _ensure_raccoon_table_locked(storage: Any) -> None:
    connection = storage.connection
    if connection is None:
        raise RuntimeError("Mini-game storage is not initialized")

    await connection.execute(
        """
        CREATE TABLE IF NOT EXISTS lexicon_raccoon_finds (
            chat_id INTEGER NOT NULL,
            round_code TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            day_key TEXT NOT NULL,
            week_key TEXT NOT NULL,
            base_word TEXT NOT NULL,
            found_at INTEGER NOT NULL,
            PRIMARY KEY(chat_id, round_code)
        )
        """
    )
    await connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_lexicon_raccoon_chat_user "
        "ON lexicon_raccoon_finds(chat_id, user_id, found_at DESC)"
    )
    await connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_lexicon_raccoon_chat_day "
        "ON lexicon_raccoon_finds(chat_id, day_key, user_id)"
    )
    await connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_lexicon_raccoon_chat_week "
        "ON lexicon_raccoon_finds(chat_id, week_key, user_id)"
    )


async def record_raccoon_find(
    storage: Any,
    *,
    chat_id: int,
    round_code: str,
    user_id: int,
    name: str,
    day_key: str,
    week_key: str,
    base_word: str,
) -> int:
    """Store one raccoon trophy and return the player's total collection size."""
    connection = storage.connection
    if connection is None:
        raise RuntimeError("Mini-game storage is not initialized")

    async with storage.lock:
        await _ensure_raccoon_table_locked(storage)

        # Keep the visible name fresh even when the user has renamed the account.
        await connection.execute(
            """
            UPDATE lexicon_raccoon_finds
            SET name = ?
            WHERE chat_id = ? AND user_id = ?
            """,
            (name, chat_id, user_id),
        )
        await connection.execute(
            """
            INSERT OR IGNORE INTO lexicon_raccoon_finds(
                chat_id, round_code, user_id, name, day_key, week_key,
                base_word, found_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                round_code,
                user_id,
                name,
                day_key,
                week_key,
                base_word,
                int(time.time()),
            ),
        )
        await connection.commit()

        async with connection.execute(
            """
            SELECT COUNT(*)
            FROM lexicon_raccoon_finds
            WHERE chat_id = ? AND user_id = ?
            """,
            (chat_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def raccoon_collection_counts(
    storage: Any,
    *,
    chat_id: int,
    user_id: int,
    day_key: str,
    week_key: str,
) -> tuple[int, int, int]:
    """Return total, today and week raccoon counts for one player."""
    connection = storage.connection
    if connection is None:
        raise RuntimeError("Mini-game storage is not initialized")

    async with storage.lock:
        await _ensure_raccoon_table_locked(storage)
        async with connection.execute(
            """
            SELECT
                COUNT(*),
                SUM(CASE WHEN day_key = ? THEN 1 ELSE 0 END),
                SUM(CASE WHEN week_key = ? THEN 1 ELSE 0 END)
            FROM lexicon_raccoon_finds
            WHERE chat_id = ? AND user_id = ?
            """,
            (day_key, week_key, chat_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()

    if row is None:
        return 0, 0, 0
    return int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)


def _score_table(period: PeriodKind) -> tuple[str, str | None]:
    if period == "day":
        return "wordgame_daily_scores", "day_key"
    if period == "week":
        return "wordgame_weekly_scores", "week_key"
    return "wordgame_scores", None


async def score_leaderboard_with_raccoons(
    storage: Any,
    *,
    chat_id: int,
    period: PeriodKind,
    period_key: str | None = None,
    limit: int = 7,
) -> list[tuple[str, int, int, int, int]]:
    """Return score leaderboard rows with raccoon collection counts attached."""
    connection = storage.connection
    if connection is None:
        raise RuntimeError("Mini-game storage is not initialized")

    table_name, key_name = _score_table(period)
    if key_name is None:
        score_where = "s.chat_id = ?"
        score_params: tuple[Any, ...] = (chat_id,)
        raccoon_where = "chat_id = ?"
        raccoon_params: tuple[Any, ...] = (chat_id,)
    else:
        if period_key is None:
            raise ValueError(f"period_key is required for period={period}")
        score_where = f"s.chat_id = ? AND s.{key_name} = ?"
        score_params = (chat_id, period_key)
        raccoon_where = f"chat_id = ? AND {key_name} = ?"
        raccoon_params = (chat_id, period_key)

    query = f"""
        SELECT
            s.name,
            s.total_points,
            s.wins,
            s.rounds,
            COALESCE(r.raccoons, 0)
        FROM {table_name} AS s
        LEFT JOIN (
            SELECT user_id, COUNT(*) AS raccoons
            FROM lexicon_raccoon_finds
            WHERE {raccoon_where}
            GROUP BY user_id
        ) AS r ON r.user_id = s.user_id
        WHERE {score_where}
        ORDER BY s.total_points DESC, s.wins DESC, s.best_points DESC
        LIMIT ?
    """

    async with storage.lock:
        await _ensure_raccoon_table_locked(storage)
        async with connection.execute(
            query,
            (*raccoon_params, *score_params, limit),
        ) as cursor:
            rows = await cursor.fetchall()

    return [
        (str(row[0]), int(row[1]), int(row[2]), int(row[3]), int(row[4]))
        for row in rows
    ]


async def raccoon_leaderboard(
    storage: Any,
    *,
    chat_id: int,
    limit: int = 10,
) -> list[tuple[str, int]]:
    """Return players ordered by the size of their raccoon collection."""
    connection = storage.connection
    if connection is None:
        raise RuntimeError("Mini-game storage is not initialized")

    async with storage.lock:
        await _ensure_raccoon_table_locked(storage)
        async with connection.execute(
            """
            SELECT name, COUNT(*) AS raccoons
            FROM lexicon_raccoon_finds
            WHERE chat_id = ?
            GROUP BY user_id
            ORDER BY raccoons DESC, MAX(found_at) ASC, name COLLATE NOCASE ASC
            LIMIT ?
            """,
            (chat_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()

    return [(str(row[0]), int(row[1])) for row in rows]


__all__ = [
    "raccoon_collection_counts",
    "raccoon_leaderboard",
    "record_raccoon_find",
    "score_leaderboard_with_raccoons",
]
