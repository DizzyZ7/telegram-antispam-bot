"""Real PostgreSQL issuance upgrades preserve every previously stored portrait.

Each test uses its own schema and never clears shared application tables.
"""
from datetime import datetime, timezone
from hashlib import sha256
import os
import unittest
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import uuid

from fosgen_pets.postgres import PostgresPetStore

URL = os.getenv("TEST_DATABASE_URL", "").strip()
START = datetime(2026, 1, 1, tzinfo=timezone.utc)


@unittest.skipUnless(URL, "TEST_DATABASE_URL is required (real PostgreSQL only)")
class V3PostgresIssuanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import asyncpg
        self.schema = "pet_v3_test_" + uuid.uuid4().hex
        self.admin = await asyncpg.connect(URL)
        await self.admin.execute(f'CREATE SCHEMA "{self.schema}"')
        parts = urlsplit(URL)
        query = dict(parse_qsl(parts.query))
        query["search_path"] = self.schema
        self.dsn = urlunsplit(parts._replace(query=urlencode(query)))
        self.store = PostgresPetStore(self.dsn)
        await self.store.initialize()

    async def asyncTearDown(self):
        await self.store.close()
        await self.admin.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
        await self.admin.close()

    async def ready(self, owner):
        await self.store.opt_in(owner, now=START)
        async with self.store._pool.acquire() as conn:
            await conn.execute("UPDATE fosgen_pet_opt_ins SET qualifying_count=100 WHERE user_id=$1", owner)

    async def test_unconfigured_new_adoption_keeps_v2(self):
        await self.ready(11)
        pet = await self.store.adopt(11)
        self.assertIsNotNone(pet)
        self.assertEqual(pet.appearance.asset_version, 2)
        self.assertEqual(pet.renderer_version, "fosgen-svg-v2")
        self.assertTrue(pet.png.startswith(b"\x89PNG\r\n\x1a\n"))

    async def test_configuring_v3_issues_v3_but_returns_original_v2_bytes(self):
        await self.ready(11)
        original = await self.store.adopt(11)
        self.assertEqual(original.appearance.asset_version, 2)
        await self.store.close()
        self.store = PostgresPetStore(self.dsn, asset_version=3)
        await self.store.initialize()
        def must_not_regenerate(attempt):
            raise AssertionError("An existing permanent portrait must not be regenerated")
        restored = await self.store.adopt(11, appearance_factory=must_not_regenerate)
        self.assertEqual((restored.id, restored.appearance.canonical_dna(), restored.svg,
                          restored.png, restored.pixel_hash, restored.renderer_version),
                         (original.id, original.appearance.canonical_dna(), original.svg,
                          original.png, original.pixel_hash, original.renderer_version))
        await self.ready(12)
        new_pet = await self.store.adopt(12)
        self.assertEqual(new_pet.appearance.asset_version, 3)
        self.assertEqual(new_pet.renderer_version, "fosgen-plush-v3")
        self.assertTrue(new_pet.png.startswith(b"\x89PNG\r\n\x1a\n"))
        async with self.store._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT asset_version,portrait_png,portrait_sha256,renderer_version "
                                      "FROM fosgen_pets WHERE owner_user_id=11")
        self.assertEqual((row["asset_version"], bytes(row["portrait_png"]), row["portrait_sha256"], row["renderer_version"]),
                         (2, original.png, sha256(original.png).hexdigest(), "fosgen-svg-v2"))


if __name__ == "__main__":
    unittest.main()
