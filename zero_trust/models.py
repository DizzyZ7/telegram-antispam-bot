from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ChallengeStatus(str, Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    PASSED = "passed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ChallengeRecord:
    id: int
    chat_id: int
    user_id: int
    expected_answer: int
    attempts: int
    status: ChallengeStatus
    created_at: int
    expires_at: int
    verified_at: int | None = None
    completed_at: int | None = None
    telegram_message_id: int | None = None
    username: str | None = None
    display_name: str | None = None


@dataclass(frozen=True, slots=True)
class CaptchaPrompt:
    question: str
    answer: int
    options: tuple[int, ...]
