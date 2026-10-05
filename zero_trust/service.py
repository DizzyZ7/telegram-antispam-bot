from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .config import ZeroTrustConfig
from .models import CaptchaPrompt, ChallengeRecord, ChallengeStatus
from .presentation import generate_prompt


class ZeroTrustStorage(Protocol):
    async def create_challenge(self, **kwargs: object) -> ChallengeRecord: ...

    async def get_challenge(self, challenge_id: int) -> ChallengeRecord | None: ...

    async def attach_message_id(self, challenge_id: int, message_id: int) -> None: ...

    async def apply_answer(self, **kwargs: object) -> ChallengeRecord: ...

    async def mark_passed(self, **kwargs: object) -> ChallengeRecord: ...

    async def cancel_active(self, **kwargs: object) -> int: ...

    async def expire_stale(self, **kwargs: object) -> int: ...


class AnswerKind(str, Enum):
    WRONG = "wrong"
    VERIFIED = "verified"
    ALREADY_VERIFIED = "already_verified"
    EXPIRED = "expired"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class AnswerResult:
    kind: AnswerKind
    challenge: ChallengeRecord | None


class ZeroTrustService:
    def __init__(
        self,
        storage: ZeroTrustStorage,
        config: ZeroTrustConfig,
        *,
        rng: random.Random | None = None,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self.storage = storage
        self.config = config
        self.rng = rng if rng is not None else random.SystemRandom()
        self.now_fn = now_fn

    def _now(self) -> int:
        return int(self.now_fn())

    async def start(self) -> int:
        return await self.storage.expire_stale(now=self._now())

    def is_protected_chat(self, chat_id: int) -> bool:
        return int(chat_id) in self.config.chat_ids

    async def begin_join(
        self,
        *,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
    ) -> tuple[ChallengeRecord, CaptchaPrompt]:
        chat_id = int(chat_id)
        user_id = int(user_id)
        if not self.is_protected_chat(chat_id):
            raise ValueError(f"Chat {chat_id} is outside Zero Trust scope")

        now = self._now()
        prompt = generate_prompt(self.rng)
        challenge = await self.storage.create_challenge(
            chat_id=chat_id,
            user_id=user_id,
            expected_answer=prompt.answer,
            created_at=now,
            expires_at=now + self.config.challenge_ttl_seconds,
            username=username,
            display_name=display_name,
        )
        return challenge, prompt

    async def attach_message_id(self, challenge_id: int, message_id: int) -> None:
        await self.storage.attach_message_id(int(challenge_id), int(message_id))

    async def answer(
        self,
        *,
        challenge_id: int,
        chat_id: int,
        user_id: int,
        answer: int,
    ) -> AnswerResult:
        challenge_id = int(challenge_id)
        chat_id = int(chat_id)
        user_id = int(user_id)

        current = await self.storage.get_challenge(challenge_id)
        if current is None:
            return AnswerResult(AnswerKind.STALE, None)
        if current.chat_id != chat_id or current.user_id != user_id:
            return AnswerResult(AnswerKind.STALE, current)
        if current.status is ChallengeStatus.EXPIRED:
            return AnswerResult(AnswerKind.EXPIRED, current)
        if current.status is ChallengeStatus.VERIFIED:
            return AnswerResult(AnswerKind.ALREADY_VERIFIED, current)
        if current.status is not ChallengeStatus.PENDING:
            return AnswerResult(AnswerKind.STALE, current)

        try:
            updated = await self.storage.apply_answer(
                challenge_id=challenge_id,
                chat_id=chat_id,
                user_id=user_id,
                answer=int(answer),
                now=self._now(),
            )
        except LookupError:
            return AnswerResult(AnswerKind.STALE, None)

        if updated.chat_id != chat_id or updated.user_id != user_id:
            return AnswerResult(AnswerKind.STALE, updated)
        if updated.status is ChallengeStatus.EXPIRED:
            return AnswerResult(AnswerKind.EXPIRED, updated)
        if updated.status is ChallengeStatus.VERIFIED:
            return AnswerResult(AnswerKind.VERIFIED, updated)
        if updated.status is ChallengeStatus.PENDING:
            return AnswerResult(AnswerKind.WRONG, updated)
        return AnswerResult(AnswerKind.STALE, updated)

    async def finalize_pass(
        self,
        *,
        challenge_id: int,
        chat_id: int,
        user_id: int,
    ) -> ChallengeRecord:
        chat_id = int(chat_id)
        user_id = int(user_id)
        result = await self.storage.mark_passed(
            challenge_id=int(challenge_id),
            chat_id=chat_id,
            user_id=user_id,
            now=self._now(),
        )
        if (
            result.chat_id != chat_id
            or result.user_id != user_id
            or result.status is not ChallengeStatus.PASSED
        ):
            raise RuntimeError("Zero Trust challenge could not be finalized as passed")
        return result

    async def cancel_leave(self, *, chat_id: int, user_id: int) -> int:
        return await self.storage.cancel_active(
            chat_id=int(chat_id),
            user_id=int(user_id),
            now=self._now(),
        )
