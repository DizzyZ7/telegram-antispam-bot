from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram import Dispatcher

from zero_trust.config import TRADER_XER_CHAT_ID, ZeroTrustConfig


class ZeroTrustStartupTests(unittest.IsolatedAsyncioTestCase):
    def test_enabled_security_requires_postgres_database_url(self):
        from zero_trust.runtime import require_database_url

        config = ZeroTrustConfig(chat_ids=frozenset({TRADER_XER_CHAT_ID}), challenge_ttl_seconds=300)
        for value in (None, "", "sqlite:///tmp/bot.db", "https://db.invalid"):
            with self.subTest(value=value):
                with self.assertRaises(RuntimeError):
                    require_database_url(config, value)

    def test_disabled_security_does_not_require_database(self):
        from zero_trust.runtime import require_database_url

        config = ZeroTrustConfig(chat_ids=frozenset(), challenge_ttl_seconds=300)
        self.assertIsNone(require_database_url(config, None))

    def test_postgres_url_is_returned_unchanged(self):
        from zero_trust.runtime import require_database_url

        config = ZeroTrustConfig(chat_ids=frozenset({TRADER_XER_CHAT_ID}), challenge_ttl_seconds=300)
        for value in (
            "postgresql://user:pass@db:5432/app",
            "postgres://user:pass@db:5432/app",
        ):
            with self.subTest(value=value):
                self.assertEqual(require_database_url(config, value), value)

    def test_diagnostics_report_both_scopes_ttl_and_missing_trader_warning(self):
        from zero_trust.runtime import build_startup_diagnostics

        config = ZeroTrustConfig(chat_ids=frozenset({-1002619489118}), challenge_ttl_seconds=420)
        diagnostics = build_startup_diagnostics(config, [-1002619489118, TRADER_XER_CHAT_ID])

        self.assertIn("ZERO_TRUST_CHAT_IDS=-1002619489118", diagnostics.info)
        self.assertIn(
            "ALLOWED_CHATS=-1003237014529,-1002619489118",
            diagnostics.info,
        )
        self.assertIn("TTL_SECONDS=420", diagnostics.info)
        self.assertIsNotNone(diagnostics.warning)
        self.assertIn(str(TRADER_XER_CHAT_ID), diagnostics.warning)

    def test_default_security_scope_survives_legacy_allowlist_override(self):
        import os
        from unittest.mock import patch

        with patch.dict(
            os.environ,
            {"ALLOWED_CHATS": "-1002619489118"},
            clear=True,
        ):
            config = ZeroTrustConfig.from_env()
        self.assertIn(TRADER_XER_CHAT_ID, config.chat_ids)

    async def test_disable_legacy_captcha_ownership_removes_handlers_and_pending_gate(self):
        from zero_trust.runtime import disable_legacy_captcha_ownership

        dp = Dispatcher()

        @dp.chat_member()
        async def on_user_join(event):
            return None

        @dp.chat_member()
        async def unrelated_member_handler(event):
            return None

        @dp.callback_query()
        async def captcha_handler(callback):
            return None

        @dp.callback_query()
        async def unrelated_callback_handler(callback):
            return None

        async def old_pending_gate(message):
            return True

        module = SimpleNamespace(
            dp=dp,
            pending_users={77: 12},
            passed_users={77},
            failed_users={88},
            handle_pending_user_message=old_pending_gate,
        )

        result = disable_legacy_captcha_ownership(module)

        self.assertEqual(result.removed_chat_member_handlers, 1)
        self.assertEqual(result.removed_callback_handlers, 1)
        self.assertEqual(
            [handler.callback.__name__ for handler in dp.chat_member.handlers],
            ["unrelated_member_handler"],
        )
        self.assertEqual(
            [handler.callback.__name__ for handler in dp.callback_query.handlers],
            ["unrelated_callback_handler"],
        )
        self.assertEqual(module.pending_users, {})
        self.assertEqual(module.passed_users, set())
        self.assertEqual(module.failed_users, set())
        self.assertFalse(await module.handle_pending_user_message(SimpleNamespace()))

    def test_log_startup_diagnostics_emits_warning_for_missing_trader(self):
        from zero_trust.runtime import log_startup_diagnostics

        config = ZeroTrustConfig(chat_ids=frozenset({-1002619489118}), challenge_ttl_seconds=300)
        logger = logging.getLogger("test.zero_trust.startup")
        with self.assertLogs(logger, level="INFO") as captured:
            log_startup_diagnostics(config, [-1002619489118], logger=logger)

        joined = "\n".join(captured.output)
        self.assertIn("ZERO_TRUST_SECURITY_SCOPE", joined)
        self.assertIn("ZERO_TRUST_SECURITY_WARNING", joined)
        self.assertIn(str(TRADER_XER_CHAT_ID), joined)


if __name__ == "__main__":
    unittest.main()
