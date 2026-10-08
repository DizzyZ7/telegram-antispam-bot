import os
import unittest
from unittest.mock import patch

from writers_submission.config import WritersSubmissionConfig


class WritersSubmissionConfigTests(unittest.TestCase):
    def _enabled_env(self) -> dict[str, str]:
        return {
            "WRITERS_SUBMISSION_ENABLED": "1",
            "WRITERS_SUBMISSION_MODERATION_MODE": "group",
            "WRITERS_SUBMISSION_PUBLIC_URL": "https://example.test/writers/",
            "WRITERS_SUBMISSION_MOD_CHAT_ID": "-100111",
            "WRITERS_SUBMISSION_FILE_CHAT_ID": "-100222",
            "WRITERS_SUBMISSION_MODERATOR_IDS": "12345,67890",
        }

    def test_disabled_config_does_not_require_submission_settings(self):
        with patch.dict(os.environ, {"WRITERS_SUBMISSION_ENABLED": "0"}, clear=True):
            config = WritersSubmissionConfig.from_env(
                bot_token=None,
                database_url=None,
                writers_chat_id=None,
            )

        self.assertFalse(config.enabled)
        self.assertIsNone(config.public_url)
        self.assertEqual(config.moderator_ids, frozenset())

    def test_enabled_config_requires_every_security_boundary(self):
        base = self._enabled_env()
        required_cases = (
            ("database_url", None, "DATABASE_URL"),
            ("bot_token", None, "BOT_TOKEN"),
            ("writers_chat_id", None, "WRITERS_CHAT_ID"),
        )
        for label, missing_value, expected in required_cases:
            with self.subTest(label=label), patch.dict(os.environ, base, clear=True):
                kwargs = {
                    "bot_token": "token",
                    "database_url": "postgresql://user:pass@db/name",
                    "writers_chat_id": -1002619489118,
                }
                kwargs[label] = missing_value
                with self.assertRaisesRegex(ValueError, expected):
                    WritersSubmissionConfig.from_env(**kwargs)

        env_required = (
            ("WRITERS_SUBMISSION_PUBLIC_URL", "WRITERS_SUBMISSION_PUBLIC_URL"),
            ("WRITERS_SUBMISSION_MOD_CHAT_ID", "WRITERS_SUBMISSION_MOD_CHAT_ID"),
            ("WRITERS_SUBMISSION_FILE_CHAT_ID", "WRITERS_SUBMISSION_FILE_CHAT_ID"),
            ("WRITERS_SUBMISSION_MODERATOR_IDS", "WRITERS_SUBMISSION_MODERATOR_IDS"),
        )
        for key, expected in env_required:
            env = dict(base)
            env.pop(key)
            with self.subTest(key=key), patch.dict(os.environ, env, clear=True):
                with self.assertRaisesRegex(ValueError, expected):
                    WritersSubmissionConfig.from_env(
                        bot_token="token",
                        database_url="postgresql://user:pass@db/name",
                        writers_chat_id=-1002619489118,
                    )

    def test_default_mode_routes_moderation_to_owner_even_with_stale_group_env(self):
        env = self._enabled_env()
        env.pop("WRITERS_SUBMISSION_MODERATION_MODE")
        # These legacy variables MUST NOT be used to deliver pending cards.
        env["WRITERS_SUBMISSION_MOD_CHAT_ID"] = "-1002629000293"
        env["WRITERS_SUBMISSION_MODERATOR_IDS"] = "12345,67890"
        with patch.dict(os.environ, env, clear=True):
            config = WritersSubmissionConfig.from_env(
                bot_token="token", database_url="postgresql://db",
                writers_chat_id=-1002619489118,
            )
        self.assertEqual(config.moderation_mode, "owner")
        self.assertEqual(config.moderation_chat_id, 2039781854)
        self.assertEqual(config.owner_user_id, 2039781854)
        self.assertEqual(config.moderator_ids, frozenset({2039781854}))
        self.assertEqual(config.file_chat_id, -100222)

    def test_owner_mode_does_not_need_moderation_group_or_extra_moderators(self):
        env = self._enabled_env()
        for key in ("WRITERS_SUBMISSION_MODERATION_MODE",
                    "WRITERS_SUBMISSION_MOD_CHAT_ID",
                    "WRITERS_SUBMISSION_MODERATOR_IDS"):
            env.pop(key)
        with patch.dict(os.environ, env, clear=True):
            config = WritersSubmissionConfig.from_env(
                bot_token="token", database_url="postgresql://db",
                writers_chat_id=-1002619489118,
            )
        self.assertEqual(config.moderation_chat_id, 2039781854)
        self.assertEqual(config.moderator_ids, frozenset({2039781854}))

    def test_owner_override_sets_moderation_and_approval_recipient(self):
        env = self._enabled_env()
        env["WRITERS_SUBMISSION_MODERATION_MODE"] = "owner"
        env["WRITERS_SUBMISSION_OWNER_USER_ID"] = "987654321"
        with patch.dict(os.environ, env, clear=True):
            config = WritersSubmissionConfig.from_env(
                bot_token="token", database_url="postgresql://db",
                writers_chat_id=-1002619489118,
            )
        self.assertEqual(config.owner_user_id, 987654321)
        self.assertEqual(config.moderation_chat_id, 987654321)
        self.assertEqual(config.moderator_ids, frozenset({987654321}))

    def test_group_mode_requires_real_negative_group_id_and_allowlist(self):
        env = self._enabled_env()
        env["WRITERS_SUBMISSION_MOD_CHAT_ID"] = "2039781854"
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "must identify a group"):
                WritersSubmissionConfig.from_env(
                    bot_token="token", database_url="postgresql://db",
                    writers_chat_id=-1002619489118,
                )
        env["WRITERS_SUBMISSION_MOD_CHAT_ID"] = "-100111"
        env["WRITERS_SUBMISSION_MODERATOR_IDS"] = ""
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "MODERATOR_IDS"):
                WritersSubmissionConfig.from_env(
                    bot_token="token", database_url="postgresql://db",
                    writers_chat_id=-1002619489118,
                )
        env["WRITERS_SUBMISSION_MODERATION_MODE"] = "unknown"
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "MODERATION_MODE"):
                WritersSubmissionConfig.from_env(
                    bot_token="token", database_url="postgresql://db",
                    writers_chat_id=-1002619489118,
                )

    def test_owner_private_recipient_can_be_configured(self):
        env = self._enabled_env()
        env["WRITERS_SUBMISSION_OWNER_USER_ID"] = "2039781854"
        with patch.dict(os.environ, env, clear=True):
            config = WritersSubmissionConfig.from_env(
                bot_token="token",
                database_url="postgresql://user:pass@db/name",
                writers_chat_id=-1002619489118,
            )
        self.assertEqual(config.owner_user_id, 2039781854)

        env["WRITERS_SUBMISSION_OWNER_USER_ID"] = "-1002629000293"
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ValueError):
                WritersSubmissionConfig.from_env(
                    bot_token="token",
                    database_url="postgresql://user:pass@db/name",
                    writers_chat_id=-1002619489118,
                )

    def test_enabled_defaults_are_exact(self):
        with patch.dict(os.environ, self._enabled_env(), clear=True):
            config = WritersSubmissionConfig.from_env(
                bot_token="token",
                database_url="postgresql://user:pass@db/name",
                writers_chat_id=-1002619489118,
            )

        self.assertTrue(config.enabled)
        self.assertEqual(config.init_data_max_age_seconds, 900)
        self.assertEqual(config.session_ttl_seconds, 43200)
        self.assertEqual(config.max_file_bytes, 20971520)
        self.assertEqual(config.max_files, 3)
        self.assertEqual(config.rate_limit_window_seconds, 60)
        self.assertEqual(config.bind_host, "0.0.0.0")
        self.assertEqual(config.port, 8080)
        self.assertEqual(config.moderator_ids, frozenset({12345, 67890}))
        self.assertEqual(config.moderation_mode, "group")
        self.assertEqual(config.moderation_chat_id, -100111)

    def test_host_port_fallback_and_explicit_override(self):
        for values, expected in (
            ({"PORT": "3000"}, 3000),
            ({"PORT": "3000", "WRITERS_SUBMISSION_PORT": "8081"}, 8081),
        ):
            env = {**self._enabled_env(), **values}
            with self.subTest(values=values), patch.dict(os.environ, env, clear=True):
                config = WritersSubmissionConfig.from_env(
                    bot_token="token",
                    database_url="postgresql://user:pass@db/name",
                    writers_chat_id=-1002619489118,
                )
                self.assertEqual(config.port, expected)

    def test_public_url_requires_https_except_loopback_development(self):
        for allowed in (
            "https://example.test/writers/",
            "http://127.0.0.1:8080/writers/",
            "http://localhost:8080/writers/",
        ):
            env = self._enabled_env()
            env["WRITERS_SUBMISSION_PUBLIC_URL"] = allowed
            with self.subTest(allowed=allowed), patch.dict(os.environ, env, clear=True):
                config = WritersSubmissionConfig.from_env(
                    bot_token="token",
                    database_url="postgresql://user:pass@db/name",
                    writers_chat_id=-1002619489118,
                )
                self.assertEqual(config.public_url, allowed)

        for rejected in (
            "http://example.test/writers/",
            "ftp://example.test/writers/",
            "javascript:alert(1)",
        ):
            env = self._enabled_env()
            env["WRITERS_SUBMISSION_PUBLIC_URL"] = rejected
            with self.subTest(rejected=rejected), patch.dict(os.environ, env, clear=True):
                with self.assertRaisesRegex(ValueError, "WRITERS_SUBMISSION_PUBLIC_URL"):
                    WritersSubmissionConfig.from_env(
                        bot_token="token",
                        database_url="postgresql://user:pass@db/name",
                        writers_chat_id=-1002619489118,
                    )

    def test_non_positive_numeric_limits_and_malformed_ids_fail(self):
        cases = (
            ("WRITERS_SUBMISSION_INIT_DATA_MAX_AGE_SECONDS", "0"),
            ("WRITERS_SUBMISSION_SESSION_TTL_SECONDS", "-1"),
            ("WRITERS_SUBMISSION_MAX_FILE_BYTES", "0"),
            ("WRITERS_SUBMISSION_MAX_FILES", "0"),
            ("WRITERS_SUBMISSION_RATE_LIMIT_WINDOW_SECONDS", "0"),
            ("WRITERS_SUBMISSION_PORT", "0"),
            ("WRITERS_SUBMISSION_MOD_CHAT_ID", "not-an-id"),
            ("WRITERS_SUBMISSION_FILE_CHAT_ID", "not-an-id"),
            ("WRITERS_SUBMISSION_MODERATOR_IDS", "123,not-an-id"),
        )
        for key, value in cases:
            env = self._enabled_env()
            env[key] = value
            with self.subTest(key=key), patch.dict(os.environ, env, clear=True):
                with self.assertRaises(ValueError):
                    WritersSubmissionConfig.from_env(
                        bot_token="token",
                        database_url="postgresql://user:pass@db/name",
                        writers_chat_id=-1002619489118,
                    )


if __name__ == "__main__":
    unittest.main()
