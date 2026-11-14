"""Real PostgreSQL permanence, consent and concurrency contracts for pets.

Every test owns a fresh schema; no shared tables or application data are cleared.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import unittest
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import uuid

from fosgen_pets.core import MessageEligibility, build_appearance

URL = os.getenv("TEST_DATABASE_URL", "").strip()
START = datetime(2026, 1, 1, tzinfo=timezone.utc)

# A hand-preserved pre-migration deployment, deliberately without art/progress.
FOUNDATION = """
CREATE TABLE fosgen_pet_opt_ins (
 user_id BIGINT PRIMARY KEY, opted_in_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 revoked_at TIMESTAMPTZ);
CREATE TABLE fosgen_pet_message_events (
 chat_id BIGINT NOT NULL, message_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
 counted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), PRIMARY KEY(chat_id,message_id));
CREATE TABLE fosgen_pets (
 pet_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 owner_user_id BIGINT NOT NULL UNIQUE REFERENCES fosgen_pet_opt_ins(user_id),
 species TEXT NOT NULL, appearance_dna JSONB NOT NULL,
 appearance_fingerprint CHAR(64) NOT NULL UNIQUE,
 asset_version INTEGER NOT NULL DEFAULT 1, name TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), portrait_file_id TEXT,
 CHECK(asset_version > 0));
"""


@unittest.skipUnless(URL, "TEST_DATABASE_URL is required (real PostgreSQL only)")
class PostgresPetStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from fosgen_pets.postgres import PostgresPetStore
        import asyncpg

        self.schema = "pet_test_" + uuid.uuid4().hex
        self.admin = await asyncpg.connect(URL)
        await self.admin.execute(f'CREATE SCHEMA "{self.schema}"')
        parts = urlsplit(URL)
        query = dict(parse_qsl(parts.query))
        query["search_path"] = self.schema
        self.dsn = urlunsplit(parts._replace(query=urlencode(query)))
        self.store = PostgresPetStore(self.dsn)
        await self.store.initialize()

    async def asyncTearDown(self) -> None:
        await self.store.close()
        await self.admin.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
        await self.admin.close()

    def message(self, number: int, owner: int = 11, *, chat: int = -100,
                text: str | None = None, **flags) -> MessageEligibility:
        return MessageEligibility(owner, chat, number, text or f"Lovely kitten number {number}",
                                  opted_in=True, **flags)

    async def ready(self, owner: int = 11) -> None:
        await self.store.opt_in(owner, now=START)
        async with self.store._pool.acquire() as conn:
            await conn.execute("UPDATE fosgen_pet_opt_ins SET qualifying_count=100 WHERE user_id=$1", owner)

    async def adopt(self, owner: int = 11):
        return await self.store.adopt(owner, appearance_factory=lambda attempt:
            build_appearance("kitten", f"fixture-{owner}", attempt))

    async def test_99_messages_do_not_unlock_and_100th_does(self) -> None:
        await self.store.opt_in(11, now=START)
        for n in range(1, 100):
            progress = await self.store.record_message(self.message(n), now=START + timedelta(seconds=30*n))
            self.assertTrue(progress.counted)
        self.assertEqual(progress.count, 99)
        self.assertIsNone(await self.adopt())
        progress = await self.store.record_message(self.message(100), now=START + timedelta(seconds=3000))
        self.assertEqual(progress.count, 100)
        self.assertIsNotNone(await self.adopt())
        progress = await self.store.record_message(self.message(101), now=START + timedelta(seconds=3030))
        self.assertEqual(progress.count, 100)
        self.assertFalse(progress.counted)

    async def test_replay_repeat_text_and_cross_chat_cooldown_do_not_count(self) -> None:
        await self.store.opt_in(11, now=START)
        first = await self.store.record_message(self.message(1, text="Hello lovely kitten"), now=START)
        self.assertEqual(first.count, 1)
        for message, seconds in ((self.message(1), 60),
                                 (self.message(2, chat=-200), 29),
                                 (self.message(3, text="  HELLO   lovely kitten  "), 60),
                                 (self.message(2, chat=-200), 120)):
            result = await self.store.record_message(message, now=START + timedelta(seconds=seconds))
            self.assertEqual(result.count, 1)
            self.assertFalse(result.counted)
        result = await self.store.record_message(self.message(4), now=START + timedelta(seconds=30))
        self.assertEqual(result.count, 2)

    async def test_consent_revocation_preserves_progress_and_pet(self) -> None:
        self.assertEqual((await self.store.record_message(self.message(1), now=START)).count, 0)
        await self.ready()
        pet = await self.adopt()
        await self.store.revoke(11)
        progress = await self.store.record_message(self.message(2), now=START + timedelta(hours=1))
        self.assertFalse(progress.opted_in)
        self.assertEqual(progress.count, 100)
        self.assertEqual((await self.store.get(11)).id, pet.id)
        await self.store.opt_in(11, now=START + timedelta(hours=2))
        self.assertEqual((await self.store.progress(11)).count, 100)
        self.assertEqual((await self.adopt()).id, pet.id)

    async def test_ineligible_messages_are_not_persisted(self) -> None:
        await self.store.opt_in(11, now=START)
        for n, options in enumerate(({"is_bot": True}, {"moderated": True},
                                     {"text": "/help kitten"}, {"text": "Hi"}), 1):
            result = await self.store.record_message(self.message(n, **options), now=START)
            self.assertEqual(result.count, 0)
        async with self.store._pool.acquire() as conn:
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM fosgen_pet_message_events"), 0)

    async def test_rejected_message_metadata_is_bounded_without_losing_progress(self) -> None:
        await self.store.opt_in(11, now=START)
        await self.store.record_message(self.message(1), now=START)
        for number in range(2, 302):
            await self.store.record_message(self.message(number), now=START + timedelta(seconds=29))
        async with self.store._pool.acquire() as conn:
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM fosgen_pet_message_events WHERE user_id=11 AND NOT counted"), 256)
        accepted = await self.store.record_message(self.message(302), now=START + timedelta(seconds=30))
        self.assertEqual(accepted.count, 2)
        # A pruned Telegram update retains its source timestamp and cannot earn progress.
        replay = await self.store.record_message(self.message(2), now=START + timedelta(seconds=29))
        self.assertFalse(replay.counted)
        self.assertEqual(replay.count, 2)

    async def test_non_text_payload_is_ignored_without_crashing(self) -> None:
        await self.store.opt_in(11, now=START)
        message = MessageEligibility(11, -100, 1, None, opted_in=True)
        self.assertEqual((await self.store.record_message(message, now=START)).count, 0)

    async def test_messages_predating_current_consent_never_count(self) -> None:
        await self.store.opt_in(11, now=START)
        early = await self.store.record_message(self.message(1), now=START - timedelta(seconds=1))
        self.assertEqual(early.count, 0)
        current = await self.store.record_message(self.message(2), now=START)
        self.assertEqual(current.count, 1)
        await self.store.revoke(11)
        await self.store.opt_in(11, now=START + timedelta(minutes=5))
        stale = await self.store.record_message(self.message(3), now=START + timedelta(minutes=4))
        self.assertEqual(stale.count, 1)
        self.assertFalse(stale.counted)

    async def test_dedup_survives_revoke_reopt_in_and_database_reconnect(self) -> None:
        from fosgen_pets.postgres import PostgresPetStore
        await self.store.opt_in(11, now=START)
        self.assertTrue((await self.store.record_message(self.message(1), now=START)).counted)
        await self.store.revoke(11)
        await self.store.opt_in(11, now=START + timedelta(minutes=2))
        await self.store.close()
        self.store = PostgresPetStore(self.dsn)
        repeated = self.message(2, text="LOVELY kitten number 1")
        progress = await self.store.record_message(repeated, now=START + timedelta(minutes=3))
        self.assertEqual(progress.count, 1)
        self.assertFalse(progress.counted)

    async def test_parallel_100th_message_and_adoption_issue_exactly_one_pet(self) -> None:
        await self.store.opt_in(11, now=START)
        async with self.store._pool.acquire() as conn:
            await conn.execute("UPDATE fosgen_pet_opt_ins SET qualifying_count=99 WHERE user_id=11")
        progress = await asyncio.gather(*(self.store.record_message(self.message(100), now=START) for _ in range(8)))
        self.assertEqual(sum(p.counted for p in progress), 1)
        pets = await asyncio.gather(*(self.adopt() for _ in range(8)))
        self.assertEqual(len({pet.id for pet in pets}), 1)
        async with self.store._pool.acquire() as conn:
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM fosgen_pets"), 1)

    async def test_restart_returns_exact_saved_art_and_identity(self) -> None:
        from fosgen_pets.postgres import PostgresPetStore
        await self.ready()
        original = await self.adopt()
        self.assertTrue(original.png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(len(original.pixel_hash), 64)
        self.assertIn("котенок", original.origin_story)
        await self.store.close()
        self.store = PostgresPetStore(self.dsn)
        await self.store.initialize()
        restored = await self.store.adopt(11, appearance_factory=lambda _: (_ for _ in ()).throw(AssertionError("must not regenerate")))
        self.assertEqual((restored.id, restored.png, restored.svg, restored.pixel_hash),
                         (original.id, original.png, original.svg, original.pixel_hash))
        self.assertEqual(restored.origin_story, original.origin_story)
        async with self.store._pool.acquire() as conn:
            saved_hash = await conn.fetchval("SELECT portrait_sha256 FROM fosgen_pets WHERE owner_user_id=11")
        self.assertEqual(saved_hash, hashlib.sha256(original.png).hexdigest())

    async def test_database_rejects_identity_art_changes_deletion_and_truncation(self) -> None:
        import asyncpg
        await self.ready()
        original = await self.adopt()
        async with self.store._pool.acquire() as conn:
            for assignment in ("owner_user_id=12", "species='fox'", "appearance_dna='{}'::jsonb",
                               "appearance_fingerprint=repeat('a',64)", "asset_version=99",
                               "portrait_svg='<svg/>'", "portrait_png='bad'::bytea",
                               "portrait_pixel_hash=repeat('b',64)", "portrait_sha256=repeat('c',64)",
                               "renderer_version='replacement'"):
                with self.subTest(assignment=assignment), self.assertRaises(asyncpg.CheckViolationError):
                    await conn.execute(f"UPDATE fosgen_pets SET {assignment} WHERE owner_user_id=11")
            with self.assertRaises(asyncpg.CheckViolationError):
                await conn.execute("DELETE FROM fosgen_pets WHERE owner_user_id=11")
            with self.assertRaises(asyncpg.CheckViolationError):
                await conn.execute("TRUNCATE fosgen_pets CASCADE")
        self.assertEqual((await self.store.get(11)).png, original.png)

    async def test_care_is_persistent_idempotent_cooldown_and_owner_scoped(self) -> None:
        await self.ready(11)
        await self.ready(12)
        await self.adopt(11)
        other = await self.adopt(12)
        changed = await self.store.care(11, "play", "request-one", now=START)
        self.assertTrue(changed.applied)
        duplicate = await self.store.care(11, "play", "request-one", now=START + timedelta(minutes=2))
        self.assertFalse(duplicate.applied)
        cooldown = await self.store.care(11, "play", "request-two", now=START + timedelta(seconds=30))
        self.assertFalse(cooldown.applied)
        self.assertEqual(cooldown.reason, "cooldown")
        self.assertEqual(cooldown.retry_after, 30)
        self.assertEqual((await self.store.get(12)).energy, other.energy)
        self.assertEqual((await self.store.get(12)).mood, other.mood)
        self.assertEqual((await self.store.care(99, "play", "request-one", now=START)).reason, "not_found")
        history = await self.store.history(11)
        self.assertEqual(sum(event["event_type"] == "care:play" for event in history), 1)
        self.assertTrue(all(1 <= value <= 100 for value in (changed.pet.energy, changed.pet.satiety)))

    async def test_colliding_dna_is_retried_without_replacing_first_pet(self) -> None:
        await self.ready(11)
        await self.ready(12)
        first = await self.adopt(11)
        second = await self.store.adopt(12, appearance_factory=lambda attempt:
            first.appearance if attempt == 0 else build_appearance("kitten", "second-unique", attempt))
        self.assertNotEqual(first.appearance.fingerprint(), second.appearance.fingerprint())
        self.assertNotEqual(first.pixel_hash, second.pixel_hash)
        self.assertEqual((await self.store.get(11)).png, first.png)

    async def test_collision_exhaustion_is_bounded_and_does_not_issue_another_pet(self) -> None:
        await self.ready(11)
        await self.ready(12)
        first = await self.adopt(11)
        attempts = []
        def collision(attempt):
            attempts.append(attempt)
            return first.appearance
        with self.assertRaisesRegex(RuntimeError, "unique permanent pet"):
            await self.store.adopt(12, appearance_factory=collision)
        self.assertEqual(attempts, list(range(16)))
        self.assertIsNone(await self.store.get(12))
        self.assertEqual((await self.store.get(11)).png, first.png)

    async def test_database_rejects_duplicate_pixels_even_with_different_dna(self) -> None:
        import asyncpg
        await self.ready(11)
        await self.ready(12)
        first = await self.adopt(11)
        different = build_appearance("kitten", "different-dna")
        async with self.store._pool.acquire() as conn:
            with self.assertRaises(asyncpg.UniqueViolationError):
                await conn.execute("""INSERT INTO fosgen_pets
                    (owner_user_id,species,appearance_dna,appearance_fingerprint,asset_version,
                     portrait_svg,portrait_png,portrait_sha256,portrait_pixel_hash,renderer_version)
                    VALUES(12,$1,$2::jsonb,$3,$4,$5,$6,$7,$8,$9)""", different.species,
                    different.canonical_dna(), different.fingerprint(), different.asset_version,
                    first.svg, first.png, hashlib.sha256(first.png).hexdigest(), first.pixel_hash,
                    first.renderer_version)
        self.assertIsNone(await self.store.get(12))

    async def test_parallel_care_retries_apply_once_and_actions_have_separate_cooldowns(self) -> None:
        await self.ready()
        await self.adopt()
        results = await asyncio.gather(*(self.store.care(11, "play", "same-request", now=START) for _ in range(8)))
        self.assertEqual(sum(result.applied for result in results), 1)
        pet = await self.store.get(11)
        self.assertEqual((pet.energy, pet.satiety), (60, 65))
        self.assertTrue((await self.store.care(11, "feed", "feed-one", now=START)).applied)
        self.assertTrue((await self.store.care(11, "sleep", "sleep-one", now=START)).applied)
        for n in range(1, 20):
            result = await self.store.care(11, "play", f"play-{n}", now=START + timedelta(seconds=60*n))
        self.assertEqual((result.pet.energy, result.pet.satiety), (1, 1))

    async def test_metadata_contains_only_keyed_digests_and_identifiers(self) -> None:
        await self.store.opt_in(11, now=START)
        await self.store.opt_in(12, now=START)
        text = "This private sentence must never be saved"
        await self.store.record_message(self.message(1, text=text), now=START)
        await self.store.record_message(self.message(2, 12, text=text), now=START)
        async with self.store._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM fosgen_pet_message_events ORDER BY user_id")
        encoded = json.dumps([dict(row) for row in rows], default=str)
        self.assertNotIn(text, encoded)
        self.assertNotEqual(rows[0]["message_digest"], rows[1]["message_digest"])
        self.assertNotEqual(rows[0]["message_digest"], hashlib.sha256(text.casefold().encode()).hexdigest())

    async def test_foundation_migrates_additively_and_existing_pet_is_never_rendered(self) -> None:
        from fosgen_pets.postgres import PostgresPetStore
        await self.store.close()
        await self.admin.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
        await self.admin.execute(f'CREATE SCHEMA "{self.schema}"')
        await self.admin.execute(f'SET search_path TO "{self.schema}"')
        await self.admin.execute(FOUNDATION)
        appearance = build_appearance("kitten", "legacy")
        await self.admin.execute("INSERT INTO fosgen_pet_opt_ins(user_id) VALUES(11)")
        await self.admin.execute("INSERT INTO fosgen_pet_message_events VALUES(-100,1,11,$1)", START)
        await self.admin.execute("""INSERT INTO fosgen_pets(owner_user_id,species,appearance_dna,
            appearance_fingerprint,asset_version) VALUES(11,$1,$2::jsonb,$3,$4)""",
            appearance.species, appearance.canonical_dna(), appearance.fingerprint(), appearance.asset_version)
        self.store = PostgresPetStore(self.dsn)
        await self.store.initialize()
        await self.store.initialize()
        self.assertEqual((await self.store.progress(11)).count, 1)
        pet = await self.store.adopt(11, appearance_factory=lambda _: (_ for _ in ()).throw(AssertionError("legacy rerender")))
        self.assertEqual(pet.appearance, appearance)
        self.assertEqual(pet.png, b"")
        self.assertEqual(pet.svg, "")


if __name__ == "__main__":
    unittest.main()
