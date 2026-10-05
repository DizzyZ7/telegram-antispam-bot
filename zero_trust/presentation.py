from __future__ import annotations

import random

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .models import CaptchaPrompt

_CALLBACK_PREFIX = "zt"
_MAX_CALLBACK_BYTES = 64
_MAX_SIGNED_BIGINT = 2**63 - 1


def generate_prompt(rng: random.Random) -> CaptchaPrompt:
    left = rng.randint(1, 9)
    right = rng.randint(1, 9)
    answer = left + right

    options = {answer}
    while len(options) < 4:
        options.add(rng.randint(2, 18))
    ordered = list(options)
    rng.shuffle(ordered)
    return CaptchaPrompt(
        question=f"{left} + {right} = ?",
        answer=answer,
        options=tuple(ordered),
    )


def encode_callback(challenge_id: int, user_id: int, answer: int) -> str:
    if challenge_id <= 0 or user_id <= 0:
        raise ValueError("challenge_id and user_id must be positive")
    payload = f"{_CALLBACK_PREFIX}:{int(challenge_id)}:{int(user_id)}:{int(answer)}"
    if len(payload.encode("utf-8")) > _MAX_CALLBACK_BYTES:
        raise ValueError("callback payload exceeds Telegram limit")
    return payload


def decode_callback(value: str) -> tuple[int, int, int] | None:
    if not isinstance(value, str) or len(value.encode("utf-8")) > _MAX_CALLBACK_BYTES:
        return None
    parts = value.split(":")
    if len(parts) != 4 or parts[0] != _CALLBACK_PREFIX:
        return None
    try:
        challenge_id, user_id, answer = (int(part) for part in parts[1:])
    except ValueError:
        return None
    if challenge_id <= 0 or user_id <= 0:
        return None
    if challenge_id > _MAX_SIGNED_BIGINT or user_id > _MAX_SIGNED_BIGINT:
        return None
    return challenge_id, user_id, answer


def build_challenge_keyboard(
    challenge_id: int,
    user_id: int,
    prompt: CaptchaPrompt,
) -> InlineKeyboardMarkup:
    row = [
        InlineKeyboardButton(
            text=str(option),
            callback_data=encode_callback(challenge_id, user_id, option),
        )
        for option in prompt.options
    ]
    return InlineKeyboardMarkup(inline_keyboard=[row])
