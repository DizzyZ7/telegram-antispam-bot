"""Balanced round persistence for the Lexicon mini-game.

This keeps the existing score tables and historical data intact. It changes only
how a new round winner is selected when players finish with equal points.
"""

from __future__ import annotations

import time
from typing import Any

from lexicon_scoring import player_rank_key


async def save_balanced_round(
    storage: Any,
    *,
    chat_id: int,
    players: list[Any],
    day_key: str,
    week_key: str,
    round_code: str,
    base_word: str,
    found_count: int,
    total_words: int,
) -> None:
    if not players:
        return

    connection = storage.connection
    if connection is None:
        raise RuntimeError("Mini-game storage is not initialized")

    ordered_players = sorted(players, key=player_rank_key)
    winner = ordered_players[0]
    now_ts = int(time.time())

    async with storage.lock:
        for player in ordered_players:
            is_winner = player.user_id == winner.user_id and winner.points > 0
            await storage._upsert_score(
                table_name="wordgame_scores",
                key_name=None,
                key_value=None,
                chat_id=chat_id,
                player=player,
                is_winner=is_winner,
                now_ts=now_ts,
            )
            await storage._upsert_score(
                table_name="wordgame_daily_scores",
                key_name="day_key",
                key_value=day_key,
                chat_id=chat_id,
                player=player,
                is_winner=is_winner,
                now_ts=now_ts,
            )
            await storage._upsert_score(
                table_name="wordgame_weekly_scores",
                key_name="week_key",
                key_value=week_key,
                chat_id=chat_id,
                player=player,
                is_winner=is_winner,
                now_ts=now_ts,
            )

        await connection.execute(
            """
            INSERT INTO wordgame_round_archive(
                chat_id, day_key, week_key, round_code, base_word,
                found_count, total_words, winner_name, winner_points, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                day_key,
                week_key,
                round_code,
                base_word,
                found_count,
                total_words,
                winner.name,
                winner.points,
                now_ts,
            ),
        )
        await connection.commit()
