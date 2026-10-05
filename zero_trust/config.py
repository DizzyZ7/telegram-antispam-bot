from __future__ import annotations

import os
from dataclasses import dataclass

TRADER_XER_CHAT_ID = -1003237014529
DEFAULT_ZERO_TRUST_CHAT_IDS: frozenset[int] = frozenset(
    {
        -1002619489118,
        TRADER_XER_CHAT_ID,
        -1003643412493,
        -1003687304800,
    }
)
DEFAULT_CHALLENGE_TTL_SECONDS = 300


def _parse_chat_ids(raw: str) -> frozenset[int]:
    values: set[int] = set()
    for chunk in raw.replace(";", ",").split(","):
        item = chunk.strip()
        if not item:
            continue
        values.add(int(item))
    return frozenset(values)


@dataclass(frozen=True, slots=True)
class ZeroTrustConfig:
    chat_ids: frozenset[int]
    challenge_ttl_seconds: int

    @classmethod
    def from_env(cls) -> "ZeroTrustConfig":
        raw_chat_ids = os.getenv("ZERO_TRUST_CHAT_IDS")
        chat_ids = (
            DEFAULT_ZERO_TRUST_CHAT_IDS
            if raw_chat_ids is None
            else _parse_chat_ids(raw_chat_ids)
        )

        ttl = int(os.getenv("ZERO_TRUST_CHALLENGE_TTL_SECONDS", str(DEFAULT_CHALLENGE_TTL_SECONDS)))
        if ttl <= 0:
            raise ValueError("ZERO_TRUST_CHALLENGE_TTL_SECONDS must be positive")
        return cls(chat_ids=chat_ids, challenge_ttl_seconds=ttl)
