from __future__ import annotations

import hashlib
import hmac
import json
import time
import unittest
from urllib.parse import quote_plus

from writers_submission.models import AuthorizationError
from writers_submission.security import (
    SESSION_COOKIE_NAME,
    SessionSigner,
    verify_telegram_init_data,
)

BOT_TOKEN = "123456:TEST_TOKEN"


def signed_init_data(
    *,
    user: dict | None = None,
    auth_date: int = 1_700_000_000,
    extra: dict[str, str] | None = None,
) -> str:
    values: dict[str, str] = {
        "auth_date": str(auth_date),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps(
            user
            or {
                "id": 777,
                "first_name": "Test",
                "last_name": "Writer",
                "username": "writer",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    if extra:
        values.update(extra)
    data_check_string = "\n".join(
        f"{key}={values[key]}" for key in sorted(values)
    )
    secret = hmac.new(
        b"WebAppData",
        BOT_TOKEN.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    digest = hmac.new(
        secret,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    pairs = [
        f"{quote_plus(key)}={quote_plus(value)}"
        for key, value in values.items()
    ]
    pairs.append(f"hash={digest}")
    return "&".join(pairs)


class TelegramInitDataSecurityTests(unittest.TestCase):
    def test_valid_signature_returns_only_signed_identity(self):
        raw = signed_init_data(
            user={
                "id": 777,
                "first_name": "Тест",
                "last_name": "Автор",
                "username": "writer",
            }
        )

        identity = verify_telegram_init_data(
            raw,
            bot_token=BOT_TOKEN,
            now=1_700_000_100,
            max_age_seconds=900,
        )

        self.assertEqual(identity.user_id, 777)
        self.assertEqual(identity.username, "writer")
        self.assertEqual(identity.first_name, "Тест")
        self.assertEqual(identity.last_name, "Автор")
        self.assertEqual(identity.auth_date, 1_700_000_000)

    def test_wrong_signature_is_rejected(self):
        raw = signed_init_data().replace("hash=", "hash=00", 1)
        with self.assertRaises(AuthorizationError):
            verify_telegram_init_data(
                raw,
                bot_token=BOT_TOKEN,
                now=1_700_000_100,
                max_age_seconds=900,
            )

    def test_expired_auth_date_is_rejected(self):
        raw = signed_init_data(auth_date=1_700_000_000)
        with self.assertRaisesRegex(AuthorizationError, "expired"):
            verify_telegram_init_data(
                raw,
                bot_token=BOT_TOKEN,
                now=1_700_001_000,
                max_age_seconds=900,
            )

    def test_missing_or_malformed_user_is_rejected(self):
        raw_missing_user = signed_init_data(user={"id": 777})
        # Re-sign a payload that omits the user field entirely.
        values = {
            "auth_date": "1700000000",
            "query_id": "q",
        }
        check = "\n".join(f"{k}={values[k]}" for k in sorted(values))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        raw_without_user = (
            f"auth_date=1700000000&query_id=q&hash={digest}"
        )
        with self.assertRaises(AuthorizationError):
            verify_telegram_init_data(
                raw_without_user,
                bot_token=BOT_TOKEN,
                now=1_700_000_100,
                max_age_seconds=900,
            )

        malformed = signed_init_data(
            user={"id": 777},
            extra={"user": "{not-json"},
        )
        with self.assertRaises(AuthorizationError):
            verify_telegram_init_data(
                malformed,
                bot_token=BOT_TOKEN,
                now=1_700_000_100,
                max_age_seconds=900,
            )

        # A signed user object without an integer id is also invalid.
        no_id = signed_init_data(user={"first_name": "No id"})
        with self.assertRaises(AuthorizationError):
            verify_telegram_init_data(
                no_id,
                bot_token=BOT_TOKEN,
                now=1_700_000_100,
                max_age_seconds=900,
            )

        self.assertTrue(raw_missing_user)

    def test_duplicate_query_key_is_rejected_before_signature_verification(self):
        raw = signed_init_data()
        raw += "&auth_date=1700000000"
        with self.assertRaisesRegex(AuthorizationError, "duplicate"):
            verify_telegram_init_data(
                raw,
                bot_token=BOT_TOKEN,
                now=1_700_000_100,
                max_age_seconds=900,
            )

    def test_malformed_percent_encoding_is_rejected(self):
        raw = signed_init_data()
        raw += "&broken=%ZZ"
        with self.assertRaisesRegex(AuthorizationError, "encoding"):
            verify_telegram_init_data(
                raw,
                bot_token=BOT_TOKEN,
                now=1_700_000_100,
                max_age_seconds=900,
            )

    def test_unsigned_frontend_user_id_cannot_override_signed_user(self):
        raw = signed_init_data(extra={"frontend_user_id": "999"})
        identity = verify_telegram_init_data(
            raw,
            bot_token=BOT_TOKEN,
            now=1_700_000_100,
            max_age_seconds=900,
        )
        self.assertEqual(identity.user_id, 777)


class SessionSignerTests(unittest.TestCase):
    def test_cookie_name_is_stable(self):
        self.assertEqual(SESSION_COOKIE_NAME, "writers_session")

    def test_session_survives_signer_recreation_with_same_bot_token(self):
        first = SessionSigner(BOT_TOKEN, ttl_seconds=43_200)
        token = first.issue(user_id=777, now=1_700_000_000)

        second = SessionSigner(BOT_TOKEN, ttl_seconds=43_200)
        claims = second.verify(token, now=1_700_000_100)

        self.assertEqual(claims.user_id, 777)
        self.assertEqual(claims.issued_at, 1_700_000_000)
        self.assertEqual(claims.expires_at, 1_700_043_200)

    def test_tampered_session_is_rejected(self):
        signer = SessionSigner(BOT_TOKEN, ttl_seconds=43_200)
        token = signer.issue(user_id=777, now=1_700_000_000)
        payload, signature = token.split(".", 1)
        tampered = ("A" if payload[0] != "A" else "B") + payload[1:]
        with self.assertRaises(AuthorizationError):
            signer.verify(f"{tampered}.{signature}", now=1_700_000_100)

    def test_expired_session_is_rejected(self):
        signer = SessionSigner(BOT_TOKEN, ttl_seconds=100)
        token = signer.issue(user_id=777, now=1_700_000_000)
        with self.assertRaisesRegex(AuthorizationError, "expired"):
            signer.verify(token, now=1_700_000_100)


if __name__ == "__main__":
    unittest.main()
