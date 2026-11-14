"""PostgreSQL-only permanent pets; identity and exact art survive all restarts.

Messages are never retained: only Telegram identifiers and per-owner keyed
digests are persisted before unlock. Database constraints arbitrate collisions;
owner-row locks serialize progress, consent, issuance and care across processes.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
import math
from pathlib import Path
import secrets
from typing import Callable
import unicodedata

import asyncpg

from .core import Appearance, MessageEligibility, UNLOCK_MESSAGES, build_appearance, eligible_message

_MIGRATION_LOCK = 5066631877623157843
_TIMEOUT = 15
_CARE_ACTIONS = {"feed": (5, 20, "content"), "play": (-10, -5, "playful"), "sleep": (25, 0, "rested")}


@dataclass(frozen=True)
class Progress:
    count: int
    opted_in: bool
    counted: bool = False


@dataclass(frozen=True)
class PermanentPet:
    id: int
    owner_id: int
    appearance: Appearance
    svg: str
    png: bytes
    pixel_hash: str
    name: str | None
    origin_story: str
    created_at: datetime
    mood: str
    energy: int
    satiety: int
    renderer_version: str = ""


@dataclass(frozen=True)
class CareResult:
    pet: PermanentPet | None
    applied: bool = False
    reason: str = "not_found"
    retry_after: int = 0


def _now(value: datetime | None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Pet timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _pet(row: asyncpg.Record | None) -> PermanentPet | None:
    if row is None:
        return None
    dna = row["appearance_dna"]
    if isinstance(dna, str):
        dna = json.loads(dna)
    return PermanentPet(
        id=row["pet_id"], owner_id=row["owner_user_id"], appearance=Appearance(**dna),
        svg=row["portrait_svg"] or "", png=bytes(row["portrait_png"] or b""),
        pixel_hash=row["portrait_pixel_hash"] or "", name=row["name"],
        origin_story=row["origin_story"], created_at=row["created_at"],
        mood=row["mood"], energy=row["energy"], satiety=row["satiety"],
        renderer_version=row["renderer_version"] or "",
    )


class PostgresPetStore:
    """One small bounded pool using the existing DATABASE_URL, without fallback."""

    def __init__(self, database_url: str) -> None:
        if not database_url or not database_url.strip().startswith(("postgresql://", "postgres://")):
            raise ValueError("DATABASE_URL is required for permanent PostgreSQL pets")
        self._database_url = database_url.strip()
        self._pool: asyncpg.Pool | None = None
        self._initialize_lock = asyncio.Lock()
        self._ready = False

    async def initialize(self) -> None:
        async with self._initialize_lock:
            if self._ready:
                return
            self._pool = await asyncpg.create_pool(
                dsn=self._database_url, min_size=1, max_size=4,
                timeout=_TIMEOUT, command_timeout=_TIMEOUT,
            )
            try:
                async with self._pool.acquire(timeout=_TIMEOUT) as conn:
                    async with conn.transaction():
                        await conn.execute("SET LOCAL lock_timeout = '10s'")
                        await conn.execute("SELECT pg_advisory_xact_lock($1)", _MIGRATION_LOCK)
                        await conn.execute(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
                self._ready = True
            except BaseException:
                self._pool.terminate()
                self._pool = None
                raise

    async def close(self) -> None:
        async with self._initialize_lock:
            pool, self._pool = self._pool, None
            self._ready = False
            if pool is not None:
                try:
                    await asyncio.wait_for(pool.close(), timeout=_TIMEOUT)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pool.terminate()
                    raise

    async def opt_in(self, owner_user_id: int, *, now: datetime | None = None) -> None:
        if owner_user_id <= 0:
            raise ValueError("Pet owner must be a real Telegram user")
        now = _now(now)
        await self.initialize()
        async with self._pool.acquire(timeout=_TIMEOUT) as conn:
            await conn.execute("""INSERT INTO fosgen_pet_opt_ins
                (user_id, opted_in_at, message_digest_key) VALUES($1,$2,$3)
                ON CONFLICT(user_id) DO UPDATE SET revoked_at=NULL,
                    opted_in_at=CASE WHEN fosgen_pet_opt_ins.revoked_at IS NOT NULL
                        THEN EXCLUDED.opted_in_at ELSE fosgen_pet_opt_ins.opted_in_at END,
                    message_digest_key=COALESCE(fosgen_pet_opt_ins.message_digest_key, EXCLUDED.message_digest_key)
                """, owner_user_id, now, secrets.token_bytes(32))

    async def revoke(self, owner_user_id: int) -> None:
        await self.initialize()
        async with self._pool.acquire(timeout=_TIMEOUT) as conn:
            await conn.execute("UPDATE fosgen_pet_opt_ins SET revoked_at=COALESCE(revoked_at,NOW()) WHERE user_id=$1", owner_user_id)

    async def get(self, owner_id: int) -> PermanentPet | None:
        await self.initialize()
        async with self._pool.acquire(timeout=_TIMEOUT) as conn:
            return _pet(await conn.fetchrow("SELECT * FROM fosgen_pets WHERE owner_user_id=$1", owner_id))

    async def progress(self, owner_id: int) -> Progress:
        await self.initialize()
        async with self._pool.acquire(timeout=_TIMEOUT) as conn:
            row = await conn.fetchrow("SELECT qualifying_count, revoked_at FROM fosgen_pet_opt_ins WHERE user_id=$1", owner_id)
        return Progress(row["qualifying_count"], row["revoked_at"] is None) if row else Progress(0, False)

    async def record_message(self, message: MessageEligibility, *, now: datetime | None = None) -> Progress:
        # Consent in the database is authoritative, even for stale handler state.
        if not eligible_message(replace(message, opted_in=True)):
            return await self.progress(message.user_id)
        now = _now(now)
        await self.initialize()
        async with self._pool.acquire(timeout=_TIMEOUT) as conn:
            async with conn.transaction():
                owner = await conn.fetchrow("SELECT * FROM fosgen_pet_opt_ins WHERE user_id=$1 FOR UPDATE", message.user_id)
                if owner is None:
                    return Progress(0, False)
                count = owner["qualifying_count"]
                if owner["revoked_at"] is not None or count >= UNLOCK_MESSAGES:
                    return Progress(count, owner["revoked_at"] is None)
                if now < owner["opted_in_at"]:
                    return Progress(count, True)
                key = owner["message_digest_key"] or secrets.token_bytes(32)
                if owner["message_digest_key"] is None:
                    await conn.execute("UPDATE fosgen_pet_opt_ins SET message_digest_key=$2 WHERE user_id=$1", message.user_id, key)
                normalized = " ".join(unicodedata.normalize("NFKC", message.text).casefold().split())
                digest = hmac.new(key, normalized.encode("utf-8"), sha256).hexdigest()
                repeated = await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM fosgen_pet_message_events
                    WHERE user_id=$1 AND message_digest=$2 AND counted)""", message.user_id, digest)
                last = owner["last_counted_at"]
                counted = not repeated and (last is None or now >= last + timedelta(seconds=30))
                inserted = await conn.fetchval("""INSERT INTO fosgen_pet_message_events
                    (chat_id,message_id,user_id,counted_at,counted,message_digest) VALUES($1,$2,$3,$4,$5,$6)
                    ON CONFLICT DO NOTHING RETURNING message_id""", message.chat_id, message.message_id,
                    message.user_id, now, counted, digest)
                if inserted is None or not counted:
                    if inserted is not None:
                        # Retain a bounded replay window for rejected attempts.
                        # Pruned updates keep their old Telegram timestamp; the
                        # monotonic last_counted_at and counted HMACs still reject them.
                        await conn.execute("""DELETE FROM fosgen_pet_message_events
                            WHERE user_id=$1 AND NOT counted AND (chat_id,message_id) IN (
                                SELECT chat_id,message_id FROM fosgen_pet_message_events
                                WHERE user_id=$1 AND NOT counted
                                ORDER BY counted_at DESC,chat_id DESC,message_id DESC OFFSET 256
                            )""", message.user_id)
                    return Progress(count, True)
                await conn.execute("""UPDATE fosgen_pet_opt_ins SET qualifying_count=qualifying_count+1,
                    last_counted_at=$2 WHERE user_id=$1""", message.user_id, now)
                return Progress(count + 1, True, True)

    async def adopt(self, owner_user_id: int, *, appearance_factory: Callable[[int], Appearance] | None = None) -> PermanentPet | None:
        await self.initialize()
        async with self._pool.acquire(timeout=_TIMEOUT) as conn:
            async with conn.transaction():
                owner = await conn.fetchrow("SELECT * FROM fosgen_pet_opt_ins WHERE user_id=$1 FOR UPDATE", owner_user_id)
                existing = _pet(await conn.fetchrow("SELECT * FROM fosgen_pets WHERE owner_user_id=$1", owner_user_id))
                if existing is not None:
                    return existing
                if owner is None or owner["revoked_at"] is not None or owner["qualifying_count"] < UNLOCK_MESSAGES:
                    return None
                from .portrait import render_portrait
                from .render_svg import SUPPORTED
                from .stories import origin_story
                for attempt in range(16):
                    appearance = (appearance_factory(attempt) if appearance_factory else
                                  build_appearance(secrets.choice(sorted(SUPPORTED)), secrets.token_hex(32), attempt))
                    portrait = await asyncio.to_thread(render_portrait, appearance)
                    png = bytes(portrait.png)
                    row = await conn.fetchrow("""INSERT INTO fosgen_pets
                        (owner_user_id,species,appearance_dna,appearance_fingerprint,asset_version,
                         portrait_svg,portrait_png,portrait_sha256,portrait_pixel_hash,renderer_version,origin_story)
                        VALUES($1,$2,$3::jsonb,$4,$5,$6,$7,$8,$9,$10,$11)
                        ON CONFLICT DO NOTHING RETURNING *""", owner_user_id, appearance.species,
                        appearance.canonical_dna(), appearance.fingerprint(), appearance.asset_version,
                        portrait.svg, png, sha256(png).hexdigest(), portrait.pixel_hash,
                        getattr(portrait, "renderer_version", f"fosgen-svg-v{appearance.asset_version}"),
                        origin_story(appearance))
                    if row is not None:
                        await conn.execute("""INSERT INTO fosgen_pet_events(pet_id,event_type,event_data)
                            VALUES($1,'adopted',$2::jsonb)""", row["pet_id"], json.dumps({"species": appearance.species, "asset_version": appearance.asset_version}))
                        return _pet(row)
                raise RuntimeError("Could not reserve a unique permanent pet portrait")

    async def care(self, owner_id: int, action: str, request_id: str, *, now: datetime | None = None) -> CareResult:
        if action not in _CARE_ACTIONS:
            raise ValueError("Unknown pet care action")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise ValueError("Pet care request_id must contain 1..128 characters")
        now = _now(now)
        await self.initialize()
        async with self._pool.acquire(timeout=_TIMEOUT) as conn:
            async with conn.transaction():
                row = await conn.fetchrow("SELECT * FROM fosgen_pets WHERE owner_user_id=$1 FOR UPDATE", owner_id)
                if row is None:
                    return CareResult(None)
                pet_id = row["pet_id"]
                if await conn.fetchval("SELECT EXISTS(SELECT 1 FROM fosgen_pet_care_requests WHERE pet_id=$1 AND request_id=$2)", pet_id, request_id):
                    return CareResult(_pet(row), reason="duplicate")
                last = await conn.fetchval("SELECT MAX(created_at) FROM fosgen_pet_care_requests WHERE pet_id=$1 AND action=$2 AND applied", pet_id, action)
                retry_after = max(0, math.ceil((last + timedelta(seconds=60) - now).total_seconds())) if last else 0
                applied = retry_after == 0
                await conn.execute("""INSERT INTO fosgen_pet_care_requests(pet_id,request_id,action,applied,created_at)
                    VALUES($1,$2,$3,$4,$5)""", pet_id, request_id, action, applied, now)
                if not applied:
                    return CareResult(_pet(row), reason="cooldown", retry_after=retry_after)
                energy, satiety, mood = _CARE_ACTIONS[action]
                row = await conn.fetchrow("""UPDATE fosgen_pets SET energy=LEAST(100,GREATEST(1,energy+$2)),
                    satiety=LEAST(100,GREATEST(1,satiety+$3)),mood=$4 WHERE pet_id=$1 RETURNING *""", pet_id, energy, satiety, mood)
                await conn.execute("""INSERT INTO fosgen_pet_events(pet_id,event_type,event_data,created_at)
                    VALUES($1,$2,$3::jsonb,$4)""", pet_id, "care:" + action, json.dumps({"request_id": request_id}), now)
                return CareResult(_pet(row), applied=True, reason="applied")

    async def history(self, owner_id: int, limit: int = 10) -> list[dict]:
        await self.initialize()
        async with self._pool.acquire(timeout=_TIMEOUT) as conn:
            rows = await conn.fetch("""SELECT event_type,event_data,e.created_at FROM fosgen_pet_events AS e
                JOIN fosgen_pets AS p ON p.pet_id=e.pet_id WHERE p.owner_user_id=$1
                ORDER BY e.created_at DESC,e.event_id DESC LIMIT $2""", owner_id, min(50, max(1, limit)))
        return [{"event_type": row["event_type"], "event_data": json.loads(row["event_data"]),
                 "created_at": row["created_at"]} for row in rows]
