from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

DEFAULT_BIND_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
DEFAULT_INIT_DATA_MAX_AGE_SECONDS = 900
DEFAULT_SESSION_TTL_SECONDS = 43200
DEFAULT_MAX_FILE_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_FILES = 3
DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 60
DEFAULT_OWNER_USER_ID = 2039781854


def _enabled(raw: str | None) -> bool:
    return (raw or "").strip().casefold() in {"1", "true", "yes", "on"}


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _required_int(name: str) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        raise ValueError(f"{name} is required when Writers Submission is enabled")
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _moderator_ids() -> frozenset[int]:
    raw = os.getenv("WRITERS_SUBMISSION_MODERATOR_IDS", "").strip()
    if not raw:
        raise ValueError(
            "WRITERS_SUBMISSION_MODERATOR_IDS is required when Writers Submission is enabled"
        )
    values: set[int] = set()
    try:
        for chunk in raw.replace(";", ",").split(","):
            item = chunk.strip()
            if not item:
                continue
            value = int(item)
            if value <= 0:
                raise ValueError
            values.add(value)
    except ValueError as exc:
        raise ValueError(
            "WRITERS_SUBMISSION_MODERATOR_IDS must contain positive integers"
        ) from exc
    if not values:
        raise ValueError("WRITERS_SUBMISSION_MODERATOR_IDS must contain at least one id")
    return frozenset(values)


def _validated_public_url() -> str:
    raw = os.getenv("WRITERS_SUBMISSION_PUBLIC_URL", "").strip()
    if not raw:
        raise ValueError(
            "WRITERS_SUBMISSION_PUBLIC_URL is required when Writers Submission is enabled"
        )
    parsed = urlparse(raw)
    if parsed.scheme == "https" and parsed.netloc:
        return raw
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}:
        return raw
    raise ValueError(
        "WRITERS_SUBMISSION_PUBLIC_URL must use HTTPS "
        "(HTTP is allowed only on loopback)"
    )


@dataclass(frozen=True, slots=True)
class WritersSubmissionConfig:
    enabled: bool
    public_url: str | None
    moderation_chat_id: int | None
    file_chat_id: int | None
    moderator_ids: frozenset[int]
    writers_chat_id: int | None
    bind_host: str
    port: int
    init_data_max_age_seconds: int
    session_ttl_seconds: int
    max_file_bytes: int
    max_files: int
    rate_limit_window_seconds: int
    owner_user_id: int = DEFAULT_OWNER_USER_ID

    @classmethod
    def from_env(
        cls,
        *,
        bot_token: str | None,
        database_url: str | None,
        writers_chat_id: int | None,
    ) -> "WritersSubmissionConfig":
        enabled = _enabled(os.getenv("WRITERS_SUBMISSION_ENABLED"))
        if not enabled:
            return cls(
                enabled=False,
                public_url=None,
                moderation_chat_id=None,
                file_chat_id=None,
                moderator_ids=frozenset(),
                writers_chat_id=writers_chat_id,
                bind_host=(
                    os.getenv("WRITERS_SUBMISSION_BIND_HOST", DEFAULT_BIND_HOST).strip()
                    or DEFAULT_BIND_HOST
                ),
                port=DEFAULT_PORT,
                init_data_max_age_seconds=DEFAULT_INIT_DATA_MAX_AGE_SECONDS,
                session_ttl_seconds=DEFAULT_SESSION_TTL_SECONDS,
                max_file_bytes=DEFAULT_MAX_FILE_BYTES,
                max_files=DEFAULT_MAX_FILES,
                rate_limit_window_seconds=DEFAULT_RATE_LIMIT_WINDOW_SECONDS,
            )

        if not (database_url or "").strip():
            raise ValueError("DATABASE_URL is required when Writers Submission is enabled")
        if not (bot_token or "").strip():
            raise ValueError("BOT_TOKEN is required when Writers Submission is enabled")
        if writers_chat_id is None:
            raise ValueError("WRITERS_CHAT_ID is required when Writers Submission is enabled")

        # Bothost publishes the internal web port through PORT. An explicit
        # Writers-specific override still takes precedence for other hosts.
        port_env = (
            "WRITERS_SUBMISSION_PORT"
            if os.getenv("WRITERS_SUBMISSION_PORT", "").strip()
            else "PORT"
        )
        port = _positive_int(port_env, DEFAULT_PORT)
        if port > 65535:
            raise ValueError("WRITERS_SUBMISSION_PORT must be between 1 and 65535")

        return cls(
            enabled=True,
            public_url=_validated_public_url(),
            moderation_chat_id=_required_int("WRITERS_SUBMISSION_MOD_CHAT_ID"),
            file_chat_id=_required_int("WRITERS_SUBMISSION_FILE_CHAT_ID"),
            moderator_ids=_moderator_ids(),
            writers_chat_id=int(writers_chat_id),
            bind_host=(
                os.getenv("WRITERS_SUBMISSION_BIND_HOST", DEFAULT_BIND_HOST).strip()
                or DEFAULT_BIND_HOST
            ),
            port=port,
            init_data_max_age_seconds=_positive_int(
                "WRITERS_SUBMISSION_INIT_DATA_MAX_AGE_SECONDS",
                DEFAULT_INIT_DATA_MAX_AGE_SECONDS,
            ),
            session_ttl_seconds=_positive_int(
                "WRITERS_SUBMISSION_SESSION_TTL_SECONDS",
                DEFAULT_SESSION_TTL_SECONDS,
            ),
            max_file_bytes=_positive_int(
                "WRITERS_SUBMISSION_MAX_FILE_BYTES",
                DEFAULT_MAX_FILE_BYTES,
            ),
            max_files=_positive_int(
                "WRITERS_SUBMISSION_MAX_FILES",
                DEFAULT_MAX_FILES,
            ),
            rate_limit_window_seconds=_positive_int(
                "WRITERS_SUBMISSION_RATE_LIMIT_WINDOW_SECONDS",
                DEFAULT_RATE_LIMIT_WINDOW_SECONDS,
            ),
            owner_user_id=_positive_int("WRITERS_SUBMISSION_OWNER_USER_ID", DEFAULT_OWNER_USER_ID),
        )
