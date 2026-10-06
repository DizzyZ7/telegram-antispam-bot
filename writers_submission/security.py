from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from urllib.parse import unquote_plus

from .models import AuthorizationError

SESSION_COOKIE_NAME = "writers_session"
_PERCENT_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")
_HEX_DIGEST = re.compile(r"^[0-9A-Fa-f]{64}$")
_SESSION_DOMAIN = b"writers-submission-session-v1"


@dataclass(frozen=True, slots=True)
class TelegramIdentity:
    user_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    auth_date: int


@dataclass(frozen=True, slots=True)
class SessionClaims:
    user_id: int
    issued_at: int
    expires_at: int


def _validate_percent_encoding(value: str) -> None:
    index = 0
    while True:
        index = value.find("%", index)
        if index < 0:
            return
        if not _PERCENT_ESCAPE.match(value, index):
            raise AuthorizationError("Malformed Telegram initData encoding")
        index += 3


def _strict_decode(value: str) -> str:
    _validate_percent_encoding(value)
    try:
        return unquote_plus(value, encoding="utf-8", errors="strict")
    except (UnicodeDecodeError, ValueError) as exc:
        raise AuthorizationError("Malformed Telegram initData encoding") from exc


def _parse_init_data(raw: str) -> dict[str, str]:
    if not isinstance(raw, str) or not raw:
        raise AuthorizationError("Telegram initData is required")

    result: dict[str, str] = {}
    for segment in raw.split("&"):
        if not segment or "=" not in segment:
            raise AuthorizationError("Malformed Telegram initData")
        raw_key, raw_value = segment.split("=", 1)
        key = _strict_decode(raw_key)
        value = _strict_decode(raw_value)
        if not key:
            raise AuthorizationError("Malformed Telegram initData")
        if key in result:
            raise AuthorizationError(f"Telegram initData contains duplicate key: {key}")
        result[key] = value
    return result


def verify_telegram_init_data(
    init_data: str,
    *,
    bot_token: str,
    now: int,
    max_age_seconds: int,
) -> TelegramIdentity:
    if not bot_token:
        raise AuthorizationError("Telegram authentication is unavailable")
    if int(max_age_seconds) <= 0:
        raise AuthorizationError("Telegram authentication window is invalid")

    values = _parse_init_data(init_data)
    supplied_hash = values.get("hash")
    if supplied_hash is None or not _HEX_DIGEST.fullmatch(supplied_hash):
        raise AuthorizationError("Telegram initData signature is invalid")

    signed_values = {key: value for key, value in values.items() if key != "hash"}
    data_check_string = "\n".join(
        f"{key}={signed_values[key]}" for key in sorted(signed_values)
    )
    secret = hmac.new(
        b"WebAppData",
        bot_token.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    expected_hash = hmac.new(
        secret,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_hash, supplied_hash.casefold()):
        raise AuthorizationError("Telegram initData signature is invalid")

    try:
        auth_date = int(values["auth_date"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthorizationError("Telegram initData auth_date is invalid") from exc

    now = int(now)
    max_age_seconds = int(max_age_seconds)
    if auth_date > now + 60:
        raise AuthorizationError("Telegram initData auth_date is in the future")
    if now - auth_date >= max_age_seconds:
        raise AuthorizationError("Telegram initData has expired")

    try:
        user = json.loads(values["user"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AuthorizationError("Telegram initData user is invalid") from exc
    if not isinstance(user, dict):
        raise AuthorizationError("Telegram initData user is invalid")

    user_id = user.get("id")
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise AuthorizationError("Telegram initData user id is invalid")

    def optional_text(name: str) -> str | None:
        value = user.get(name)
        return value if isinstance(value, str) and value else None

    return TelegramIdentity(
        user_id=user_id,
        username=optional_text("username"),
        first_name=optional_text("first_name"),
        last_name=optional_text("last_name"),
        auth_date=auth_date,
    )


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    if not value:
        raise AuthorizationError("Session is malformed")
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode((value + padding).encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise AuthorizationError("Session is malformed") from exc


class SessionSigner:
    def __init__(self, bot_token: str, ttl_seconds: int) -> None:
        if not bot_token:
            raise ValueError("bot_token is required")
        if int(ttl_seconds) <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.ttl_seconds = int(ttl_seconds)
        self._key = hmac.new(
            bot_token.encode("utf-8"),
            _SESSION_DOMAIN,
            hashlib.sha256,
        ).digest()

    def issue(self, *, user_id: int, now: int) -> str:
        user_id = int(user_id)
        now = int(now)
        if user_id <= 0:
            raise ValueError("user_id must be positive")
        payload = json.dumps(
            {
                "exp": now + self.ttl_seconds,
                "iat": now,
                "uid": user_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        encoded = _b64encode(payload)
        signature = hmac.new(
            self._key,
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest()
        return f"{encoded}.{_b64encode(signature)}"

    def verify(self, token: str, *, now: int) -> SessionClaims:
        if not isinstance(token, str):
            raise AuthorizationError("Session is malformed")
        parts = token.split(".")
        if len(parts) != 2:
            raise AuthorizationError("Session is malformed")
        encoded, encoded_signature = parts

        signature = _b64decode(encoded_signature)
        expected = hmac.new(
            self._key,
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(expected, signature):
            raise AuthorizationError("Session signature is invalid")

        try:
            payload = json.loads(_b64decode(encoded).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AuthorizationError("Session payload is invalid") from exc
        if not isinstance(payload, dict):
            raise AuthorizationError("Session payload is invalid")

        user_id = payload.get("uid")
        issued_at = payload.get("iat")
        expires_at = payload.get("exp")
        if (
            isinstance(user_id, bool)
            or not isinstance(user_id, int)
            or user_id <= 0
            or isinstance(issued_at, bool)
            or not isinstance(issued_at, int)
            or isinstance(expires_at, bool)
            or not isinstance(expires_at, int)
            or expires_at <= issued_at
        ):
            raise AuthorizationError("Session payload is invalid")

        now = int(now)
        if issued_at > now + 60:
            raise AuthorizationError("Session issue time is invalid")
        if now >= expires_at:
            raise AuthorizationError("Session has expired")

        return SessionClaims(
            user_id=user_id,
            issued_at=issued_at,
            expires_at=expires_at,
        )
