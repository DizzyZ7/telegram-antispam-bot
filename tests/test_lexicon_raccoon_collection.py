from __future__ import annotations

import asyncio
import unittest

import aiosqlite

from lexicon_raccoon_collection import (
    raccoon_collection_counts,
    raccoon_leaderboard,
    record_raccoon_find,
    score_leaderboard_with_raccoons,
)


class FakeStorage:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self.connection = connection
        self.lock = asyncio.Lock()


class RaccoonCollectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.connection = await aiosqlite.connect(":memory:")
        self.storage = FakeStorage(self.connection)
        await self.connection.executescript(
            """
            CREATE TABLE wordgame_scores (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                rounds INTEGER NOT NULL,
                wins INTEGER NOT NULL,
                total_points INTEGER NOT NULL,
                best_points INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(chat_id, user_id)
            );
            CREATE TABLE wordgame_daily_scores (
                day_key TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                rounds INTEGER NOT NULL,
                wins INTEGER NOT NULL,
                total_points INTEGER NOT NULL,
                best_points INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(day_key, chat_id, user_id)
            );
            CREATE TABLE wordgame_weekly_scores (
                week_key TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                rounds INTEGER NOT NULL,
                wins INTEGER NOT NULL,
                total_points INTEGER NOT NULL,
                best_points INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(week_key, chat_id, user_id)
            );
            """
        )
        await self.connection.commit()

    async def asyncTearDown(self) -> None:
        await self.connection.close()

    async def _record(
        self,
        *,
        round_code: str,
        user_id: int = 10,
        name: str = "Collector",
        day_key: str = "2026-07-11",
        week_key: str = "2026-W28",
    ) -> int:
        return await record_raccoon_find(
            self.storage,
            chat_id=-1001,
            round_code=round_code,
            user_id=user_id,
            name=name,
            day_key=day_key,
            week_key=week_key,
            base_word="гиперпространство",
        )

    async def test_same_round_is_counted_once(self) -> None:
        first_total = await self._record(round_code="AAAA")
        repeated_total = await self._record(round_code="AAAA")

        self.assertEqual(first_total, 1)
        self.assertEqual(repeated_total, 1)

    async def test_different_rounds_grow_collection(self) -> None:
        await self._record(round_code="AAAA")
        total = await self._record(round_code="BBBB")

        self.assertEqual(total, 2)
        counts = await raccoon_collection_counts(
            self.storage,
            chat_id=-1001,
            user_id=10,
            day_key="2026-07-11",
            week_key="2026-W28",
        )
        self.assertEqual(counts, (2, 2, 2))

    async def test_score_top_contains_raccoon_collection(self) -> None:
        await self.connection.executemany(
            """
            INSERT INTO wordgame_scores(
                chat_id, user_id, name, rounds, wins,
                total_points, best_points, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (-1001, 10, "Collector", 4, 2, 40, 15, 1),
                (-1001, 20, "Reader", 5, 1, 55, 18, 1),
            ],
        )
        await self.connection.commit()

        await self._record(round_code="AAAA", user_id=10, name="Collector")
        await self._record(round_code="BBBB", user_id=10, name="Collector")
        await self._record(round_code="CCCC", user_id=20, name="Reader")

        rows = await score_leaderboard_with_raccoons(
            self.storage,
            chat_id=-1001,
            period="all",
            limit=7,
        )

        self.assertEqual(rows[0], ("Reader", 55, 1, 5, 1))
        self.assertEqual(rows[1], ("Collector", 40, 2, 4, 2))

    async def test_raccoon_top_orders_by_collection_size(self) -> None:
        await self._record(round_code="AAAA", user_id=10, name="Collector")
        await self._record(round_code="BBBB", user_id=10, name="Collector")
        await self._record(round_code="CCCC", user_id=20, name="Reader")

        rows = await raccoon_leaderboard(self.storage, chat_id=-1001, limit=10)

        self.assertEqual(rows, [("Collector", 2), ("Reader", 1)])


if __name__ == "__main__":
    unittest.main()
