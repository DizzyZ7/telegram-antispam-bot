"""Durable group walks on the existing bounded Pets PostgreSQL pool.

The opt-in row is the per-owner serialization point shared with adoption.
Daily uniqueness is global, not per chat/topic. A persisted end time is the
completion authority; each unique row contributes exactly one 20-XP reward.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import ceil
import json

from .core import Appearance
from .postgres import PostgresPetStore, _now, _TIMEOUT
from .walks import (DIARY_PAGE_SIZE, ROUTES, WALK_MINUTES, WALK_XP,
                    activity, level_for_xp, walk_day)


@dataclass(frozen=True)
class Walk:
    id: int
    pet_id: int
    species: str
    chat_id: int
    message_id: int
    thread_id: int | None
    day: date
    route: str
    activity: str
    started_at: datetime
    ends_at: datetime


@dataclass(frozen=True)
class WalkResult:
    walk: Walk | None = None
    reason: str = "pet_missing"


@dataclass(frozen=True)
class Growth:
    xp: int = 0
    completed_walks: int = 0
    active_until: datetime | None = None

    @property
    def level(self) -> int:
        return level_for_xp(self.xp)


@dataclass(frozen=True)
class WalkDiary:
    day: date
    walks: tuple[Walk, ...] = ()
    total: int = 0
    page: int = 1

    @property
    def pages(self) -> int:
        return max(1, ceil(self.total / DIARY_PAGE_SIZE))


def _integer(value: int, lower: int, upper: int) -> bool:
    return type(value) is int and lower <= value <= upper


def _walk(row) -> Walk:
    return Walk(row["walk_id"], row["pet_id"], row["species"], row["chat_id"],
                row["message_id"], row["thread_id"], row["walk_day"], row["route"],
                row["activity"], row["started_at"], row["ends_at"])


_SELECT = "SELECT w.*, p.species FROM fosgen_pet_walks w JOIN fosgen_pets p USING(pet_id) "


class PetWalkStore:
    def __init__(self, permanent_store: PostgresPetStore):
        self.store = permanent_store

    async def start(self, owner: int, chat: int, message: int, route: str, *,
                    thread_id: int | None = None, now: datetime | None = None) -> WalkResult:
        if (not _integer(owner, 1, 2**63 - 1) or not _integer(chat, -(2**63) + 1, -1)
                or not _integer(message, 1, 2**31 - 1)
                or (thread_id is not None and not _integer(thread_id, 1, 2**31 - 1))
                or not isinstance(route, str) or route not in ROUTES):
            raise ValueError("Invalid walk parameters")
        now = _now(now)
        day = walk_day(now)
        await self.store.initialize()
        async with self.store._pool.acquire(timeout=_TIMEOUT) as conn:
            async with conn.transaction():
                owner_row = await conn.fetchval("SELECT user_id FROM fosgen_pet_opt_ins WHERE user_id=$1 FOR UPDATE", owner)
                if owner_row is None:
                    return WalkResult()
                previous = await conn.fetchrow(_SELECT +
                    "WHERE w.owner_user_id=$1 AND w.chat_id=$2 AND w.message_id=$3", owner, chat, message)
                if previous is not None:
                    return WalkResult(_walk(previous), "replay")
                fresh = await conn.fetchval("""INSERT INTO fosgen_pet_walk_request_watermarks
                    (owner_user_id,chat_id,last_message_id) VALUES($1,$2,$3)
                    ON CONFLICT(owner_user_id,chat_id) DO UPDATE SET last_message_id=EXCLUDED.last_message_id
                    WHERE fosgen_pet_walk_request_watermarks.last_message_id < EXCLUDED.last_message_id
                    RETURNING last_message_id""", owner, chat, message)
                if fresh is None:
                    return WalkResult(reason="replay")
                active = await conn.fetchrow(_SELECT +
                    "WHERE w.owner_user_id=$1 AND w.ends_at>$2 ORDER BY w.ends_at DESC LIMIT 1", owner, now)
                if active is not None:
                    return WalkResult(_walk(active), "active")
                previous = await conn.fetchrow(_SELECT +
                    "WHERE w.owner_user_id=$1 AND w.walk_day=$2", owner, day)
                if previous is not None:
                    return WalkResult(_walk(previous), "daily_limit")
                pet = await conn.fetchrow("SELECT pet_id,species,appearance_dna FROM fosgen_pets WHERE owner_user_id=$1", owner)
                if pet is None:
                    return WalkResult()
                dna = pet["appearance_dna"]
                appearance = Appearance(**(json.loads(dna) if isinstance(dna, str) else dna))
                text = activity(appearance, day, route)
                row = await conn.fetchrow("""INSERT INTO fosgen_pet_walks
                    (pet_id,owner_user_id,chat_id,message_id,thread_id,walk_day,route,activity,started_at,ends_at)
                    VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                    ON CONFLICT DO NOTHING RETURNING *""", pet["pet_id"], owner, chat, message,
                    thread_id, day, route, text, now, now + timedelta(minutes=WALK_MINUTES))
                if row is None:
                    # A conflicting Telegram ID cannot disclose another owner's walk.
                    return WalkResult(reason="request_conflict")
                return WalkResult(_walk(dict(row, species=pet["species"])), "started")

    async def growth(self, owner: int, *, now: datetime | None = None) -> Growth:
        if not _integer(owner, 1, 2**63 - 1):
            raise ValueError("Invalid walk owner")
        now = _now(now)
        await self.store.initialize()
        async with self.store._pool.acquire(timeout=_TIMEOUT) as conn:
            row = await conn.fetchrow("""SELECT COUNT(*) FILTER (WHERE ends_at <= $2) AS completed,
                MAX(ends_at) FILTER (WHERE ends_at > $2) AS active_until
                FROM fosgen_pet_walks WHERE owner_user_id=$1""", owner, now)
        count = row["completed"]
        return Growth(count * WALK_XP, count, row["active_until"])

    async def diary(self, chat: int, day: date, *, page: int = 1,
                    now: datetime | None = None) -> WalkDiary:
        if (not _integer(chat, -(2**63) + 1, -1) or type(day) is not date
                or not _integer(page, 1, 100000)):
            raise ValueError("Invalid walk diary parameters")
        # Validate the optional caller clock even though history itself is timeless.
        _now(now)
        await self.store.initialize()
        async with self.store._pool.acquire(timeout=_TIMEOUT) as conn:
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                total = await conn.fetchval("SELECT COUNT(*) FROM fosgen_pet_walks WHERE chat_id=$1 AND walk_day=$2", chat, day)
                rows = await conn.fetch(_SELECT + "WHERE w.chat_id=$1 AND w.walk_day=$2 ORDER BY w.walk_id LIMIT $3 OFFSET $4",
                                        chat, day, DIARY_PAGE_SIZE, (page - 1) * DIARY_PAGE_SIZE)
        return WalkDiary(day, tuple(_walk(row) for row in rows), total, page)
