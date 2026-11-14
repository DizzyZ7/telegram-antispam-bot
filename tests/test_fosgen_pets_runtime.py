"""Optional Pets startup cannot take down existing moderation."""
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace


class PetsConfigTests(unittest.TestCase):
    def test_disabled_does_not_require_database_or_chat_ids(self):
        from fosgen_pets.runtime import PetsConfig
        self.assertFalse(PetsConfig.from_env({}).enabled)

    def test_enabled_requires_explicit_valid_group_ids(self):
        from fosgen_pets.runtime import PetsConfig
        for value in ("", "123", "-100,broken", "0", "-999999999999999999999"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                PetsConfig.from_env({"FOSGEN_PETS_ENABLED": "1", "FOSGEN_PETS_CHAT_IDS": value})
        config = PetsConfig.from_env({"FOSGEN_PETS_ENABLED": "1", "FOSGEN_PETS_CHAT_IDS": "-100,-200,-100"})
        self.assertEqual(config.chat_ids, frozenset({-100, -200}))


class PetsRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_dsn_does_not_crash_existing_bot(self):
        from fosgen_pets.runtime import start_pet_runtime
        with self.assertLogs("fosgen_pets.runtime", "WARNING") as logs:
            runtime = await start_pet_runtime(SimpleNamespace(ALLOWED_CHATS=[-100]), database_url="invalid-private-data", environ={"FOSGEN_PETS_ENABLED": "1", "FOSGEN_PETS_CHAT_IDS": "-100"})
        self.assertIsNone(runtime)
        self.assertNotIn("private", " ".join(logs.output))

    async def test_invalid_lab_configuration_does_not_disable_moderation(self):
        from pathlib import Path
        from fosgen_pets.runtime import register_pettest_runtime
        with self.assertLogs("fosgen_pets.runtime", "WARNING"):
            result = register_pettest_runtime(SimpleNamespace(), data_dir=Path("/tmp/unused-pets"), environ={"FOSGEN_PETTEST_ENABLED": "1", "FOSGEN_PETTEST_ADMIN_IDS": "wrong"})
        self.assertFalse(result)

    async def test_disabled_has_no_storage_or_handler_side_effects(self):
        from fosgen_pets.runtime import start_pet_runtime
        with patch("fosgen_pets.runtime.PostgresPetStore") as store:
            runtime = await start_pet_runtime(SimpleNamespace(), database_url=None, environ={})
        self.assertIsNone(runtime)
        store.assert_not_called()

    async def test_database_failure_leaves_existing_application_operational(self):
        from fosgen_pets.runtime import start_pet_runtime
        store = SimpleNamespace(initialize=AsyncMock(side_effect=OSError("private dsn must not be logged")), close=AsyncMock())
        app = SimpleNamespace(ALLOWED_CHATS=[-100])
        with patch("fosgen_pets.runtime.PostgresPetStore", return_value=store), self.assertLogs("fosgen_pets.runtime", "WARNING") as logs:
            runtime = await start_pet_runtime(app, database_url="postgresql://private", environ={"FOSGEN_PETS_ENABLED": "1", "FOSGEN_PETS_CHAT_IDS": "-100"})
        self.assertIsNone(runtime)
        self.assertNotIn("private", " ".join(logs.output))
        self.assertEqual(store.close.await_count, 1)

    async def test_no_implicit_expansion_to_other_or_lexicon_only_chats(self):
        from fosgen_pets.runtime import start_pet_runtime
        app = SimpleNamespace(ALLOWED_CHATS=[-100, -200])
        store = SimpleNamespace(initialize=AsyncMock(), close=AsyncMock())
        with patch("fosgen_pets.runtime.PostgresPetStore", return_value=store), patch("fosgen_pets.runtime.register_pet_handlers") as register:
            runtime = await start_pet_runtime(app, database_url="postgresql://local", excluded_chat_ids=frozenset({-200}), environ={"FOSGEN_PETS_ENABLED": "1", "FOSGEN_PETS_CHAT_IDS": "-100,-200,-300"})
        self.assertIsNotNone(runtime)
        self.assertEqual(register.call_args.kwargs["chat_ids"], frozenset({-100}))
        await runtime.stop()
        self.assertEqual(store.close.await_count, 1)
