import os
import unittest
from unittest.mock import patch


class ZeroTrustConfigTests(unittest.TestCase):
    def test_defaults_are_independent_from_legacy_allowlist(self):
        from zero_trust.config import (
            DEFAULT_ZERO_TRUST_CHAT_IDS,
            TRADER_XER_CHAT_ID,
            ZeroTrustConfig,
        )

        with patch.dict(os.environ, {}, clear=True):
            config = ZeroTrustConfig.from_env()

        self.assertEqual(
            DEFAULT_ZERO_TRUST_CHAT_IDS,
            frozenset(
                {
                    -1002619489118,
                    -1003237014529,
                    -1003643412493,
                    -1003687304800,
                }
            ),
        )
        self.assertEqual(TRADER_XER_CHAT_ID, -1003237014529)
        self.assertIn(TRADER_XER_CHAT_ID, config.chat_ids)
        self.assertEqual(config.chat_ids, DEFAULT_ZERO_TRUST_CHAT_IDS)
        self.assertEqual(config.challenge_ttl_seconds, 300)

    def test_env_override_accepts_commas_and_semicolons(self):
        from zero_trust.config import ZeroTrustConfig

        with patch.dict(
            os.environ,
            {
                "ZERO_TRUST_CHAT_IDS": "-1001; -1002, -1003",
                "ZERO_TRUST_CHALLENGE_TTL_SECONDS": "420",
                "ALLOWED_CHATS": "-9999",
            },
            clear=True,
        ):
            config = ZeroTrustConfig.from_env()

        self.assertEqual(config.chat_ids, frozenset({-1001, -1002, -1003}))
        self.assertEqual(config.challenge_ttl_seconds, 420)

    def test_non_positive_ttl_is_rejected(self):
        from zero_trust.config import ZeroTrustConfig

        for value in ("0", "-1"):
            with self.subTest(value=value):
                with patch.dict(
                    os.environ,
                    {"ZERO_TRUST_CHALLENGE_TTL_SECONDS": value},
                    clear=True,
                ):
                    with self.assertRaises(ValueError):
                        ZeroTrustConfig.from_env()

    def test_challenge_status_values_are_stable(self):
        from zero_trust.models import ChallengeStatus

        self.assertEqual(
            {status.value for status in ChallengeStatus},
            {"pending", "verified", "passed", "expired", "cancelled"},
        )


if __name__ == "__main__":
    unittest.main()
