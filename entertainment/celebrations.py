"""Lightweight birthday and congratulation recognition and playful greetings."""
from __future__ import annotations

import random
import re

_BIRTHDAY = re.compile(r"(?iu)\b(?:с\s+дн(?:ем|ём|юхой)\s+рождени[яе]|с\s+др\b|happy\s+birthday)\b")
_WISH = re.compile(r"(?iu)\b(?:поздравляю|поздравляем|поздравляем\s+тебя|с\s+праздником|с\s+юбилеем|поздравухи)\b")
_NEGATION = re.compile(r"(?iu)\b(?:не\s+поздравля|не\s+день\s+рождения|вчера\s+был\s+день\s+рождения)")

_EMOJI = ("🫦", "🤗", "🥳", "🎉", "🎊", "🤣", "💃", "🪩", "✨", "🎂")
_BIRTHDAY_LINES = (
    "С ДНЕМ РОЖДЕНИЯ! Желаю столько счастья, чтобы даже енот не смог утащить.",
    "С днем рождения! Пусть сюжет жизни будет с неожиданными поворотами, но без багов.",
    "С днем рождения! Сегодня официальный повод быть легендой чата.",
)
_WISH_LINES = (
    "Поздравляю! Пусть удача пристает сильнее, чем Фосген к мемам.",
    "УРА! Поздравления приняты, праздничный хаос запущен.",
    "Это повод устроить маленький праздник прямо в чате!",
)


def celebration_kind(text: str | None) -> str | None:
    if not text or len(text) > 500 or text.lstrip().startswith("/") or _NEGATION.search(text):
        return None
    if _BIRTHDAY.search(text):
        return "birthday"
    if _WISH.search(text):
        return "congratulation"
    return None


def festive_reply(kind: str, *, rng: random.Random) -> str:
    """Shuffle emoji across words, sometimes within a word; cap total size."""
    lines = _BIRTHDAY_LINES if kind == "birthday" else _WISH_LINES
    words = rng.choice(lines).split(" ")
    emojis = [rng.choice(_EMOJI) for _ in range(rng.randint(5, 9))]
    for emoji in emojis:
        position = rng.randrange(len(words) + 1)
        if position < len(words) and len(words[position]) >= 6 and rng.random() < 0.24:
            word = words[position]
            split = rng.randint(2, len(word) - 2)
            words[position] = word[:split] + emoji + word[split:]
        else:
            words.insert(position, emoji)
    return " ".join(words)[:500]


__all__ = ["celebration_kind", "festive_reply"]
