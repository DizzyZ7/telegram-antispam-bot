"""Pettest lifecycle, ownership, DNA and deletion tests."""
import asyncio
from pathlib import Path
import tempfile
import unittest

from fosgen_pets.pettest import TestPetStore, parse_admin_ids, parse_command, render_png


class PettestTests(unittest.TestCase):
    def test_commands(self):
        self.assertEqual(parse_command("/pettest"), ("create", None))
        self.assertEqual(parse_command("/pettest list"), ("list", None))
        self.assertEqual(parse_command("/pettest LIST"), ("list", None))
        self.assertEqual(parse_command("/pettest 12"), ("show", 12))
        self.assertEqual(parse_command("/pettest 12 delete"), ("delete", 12))
        self.assertEqual(parse_command("/pettest 12 DELETE"), ("delete", 12))
        for bad in ("/pettest -1 delete", "/pettest 0 delete", "/pettest 1 wipe", "/pettest a"):
            with self.assertRaises(ValueError):
                parse_command(bad)

    def test_bulk_delete_command_variants(self):
        from fosgen_pets.pettest import parse_request
        for command in ("/pettest id 1-27 delete", "/pettest 1-27 delete"):
            request = parse_request(command)
            self.assertEqual(request.operation, "delete_many")
            self.assertEqual(request.identifiers, tuple(range(1, 28)))
        request = parse_request("/pettest 1,3,5-8 DELETE")
        self.assertEqual(request.identifiers, (1, 3, 5, 6, 7, 8))
        self.assertEqual(parse_request("/pettest all delete").operation, "delete_all")
        self.assertEqual(parse_command("/pettest id 1-27 delete"), ("delete_many", None))
        self.assertEqual(parse_command("/pettest 12 delete"), ("delete", 12))

    def test_admin_ids(self):
        self.assertEqual(parse_admin_ids("123, 456"), frozenset({123, 456}))
        with self.assertRaises(ValueError):
            parse_admin_ids("123,wrong")

    def test_create_show_delete_and_owner_isolation(self):
        async def scenario(path):
            store = TestPetStore(path)
            pet = await store.create(123)
            self.assertEqual(pet.owner_id, 123)
            self.assertIsNotNone(await TestPetStore(path).get(pet.id, 123))
            self.assertEqual(await store.list(123), [(pet.id, pet.appearance.species)])
            self.assertIsNone(await store.get(pet.id, 456))
            self.assertFalse(await store.delete(pet.id, 456))
            self.assertTrue(await store.delete(pet.id, 123))
            self.assertIsNone(await store.get(pet.id, 123))
            self.assertEqual(await store.list(123), [])
            self.assertFalse(await store.delete(pet.id, 123))
            second = await store.create(123)
            self.assertNotEqual(pet.id, second.id)
        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(scenario(Path(folder) / "pettest.sqlite3"))

    def test_render_png(self):
        async def scenario(path):
            pet = await TestPetStore(path).create(123)
            png = render_png(pet.appearance)
            self.assertTrue(png.startswith(bytes.fromhex("89504e470d0a1a0a")))
        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(scenario(Path(folder) / "pettest.sqlite3"))


if __name__ == "__main__":
    unittest.main()
