"""Lightweight birthday and congratulation recognition and playful greetings."""
from __future__ import annotations

import random
import re

_BIRTHDAY = re.compile(r"(?iu)\b(?:с\s+дн(?:ем|ём|юхой)\s+рождени[яе]|с\s+др\b|happy\s+birthday)\b")
_WISH = re.compile(r"(?iu)\b(?:поздравляю|поздравляем|поздравляем\s+тебя|с\s+праздником|с\s+юбилеем|поздравухи)\b")
_NEGATION = re.compile(r"(?iu)\b(?:не\s+поздравля|не\s+день\s+рождения|вчера\s+был\s+день\s+рождения)")

_EMOJI = ("🫦", "🤗", "🥳", "🎉", "🎊", "🤣", "💃", "🪩", "✨", "🎂", "😭", "💀", "🔥", "💖", "❤️", "😍", "😂", "🥹", "🫶", "😎", "💅", "🗣️", "👑", "🍰", "🎁", "🎈", "💐", "⭐", "😼", "🦝")
_BIRTHDAY_LINES = (
    "С ДНЕМ РОЖДЕНИЯ! Желаю столько счастья, чтобы даже енот не смог утащить.",
    "С днем рождения! Пусть сюжет жизни будет с неожиданными поворотами, но без багов.",
    "С днем рождения! Сегодня официальный повод быть легендой чата.",
    "ВНИМАНИЕ, ЧАТ! Сегодня у одного из главных героев новый сезон жизни. С днем рождения!",
    "С днем рождения! Пусть твой запас удачи не кончается даже после финальных титров.",
    "УРААА! Праздничный енот одобрил этот день. С днем рождения!",
    "С днем рождения! Желаю вдохновения, смешных историй и людей, с которыми хорошо даже молчать.",
    "Сегодня вселенная официально разрешает тебе быть счастливее обычного. С днем рождения!",
    "С днем рождения! Если жизнь — фанфик, пусть у тебя всегда будет любимый автор.",\n    "СРОЧНЫЕ НОВОСТИ! У тебя сегодня день рождения, а значит, все остальные новости отменяются.",\n    "Поздравляю! Пусть твоя жизнь будет как хороший сериал: новый сезон лучше предыдущего.",\n    "Сегодня твой персональный праздник. Даже енот достал праздничный галстук.",\n    "С днем рождения! Пусть счастье появляется без кулдауна, а грусть уходит в бан.",\n    "Поздравляю! Пусть каждый день приносит хотя бы одну историю, которую захочется пересказать.",\n    "Праздничный комитет мемов постановил: сегодня ты невероятен. С днем рождения!",\n    "С днем рождения! Пусть твои мечты получают зеленый свет без очереди.",\n    "Пока все поздравляют, я просто напомню: ты главный герой сегодняшнего эпизода.",\n    "С днем рождения! Пусть удача пишет тебе первой, а проблемы остаются в архиве.",
    "Поздравляю с днем рождения! Сюжетный поворот дня: все хорошие пожелания сбываются.",
    "С днем рождения! Пусть хорошие новости приходят чаще, чем уведомления из чата.",
    "В этот день даже мемы встали по стойке смирно. С днем рождения!",
)
_WISH_LINES = (
    "Поздравляю! Пусть удача пристает сильнее, чем Фосген к мемам.",
    "УРА! Поздравления приняты, праздничный хаос запущен.",
    "Это повод устроить маленький праздник прямо в чате!",
    "Официально объявляю: сегодня есть повод для радости. Поздравляю!",
    "Поздравляю! Пусть эта победа станет только первой главой.",
    "Тревога! Обнаружен повод для счастья. Срочно поздравляю!",
    "Поздравляю! Пусть дальше будет еще больше поводов улыбаться.",
    "Сегодня удача явно решила зайти в этот чат. Поздравляю!",
    "Поздравляю! Желаю, чтобы приятные неожиданности стали привычкой.",\n    "Поздравляю! Даже енот отложил свои важные дела ради такого события.",\n    "ОБЪЯВЛЕНИЕ: в чате зафиксирован редкий уровень радости. Поздравляю!",\n    "Пусть этот повод станет началом целой серии счастливых событий!",\n    "Поздравляю! Желаю столько поводов для гордости, чтобы не хватило закрепов.",\n    "Сегодня можно немного погордиться собой. Поздравляю!",\n    "Официальный вердикт праздничной комиссии: это заслуживает аплодисментов!",
)


def celebration_kind(text: str | None) -> str | None:
    if not text or len(text) > 500 or text.lstrip().startswith("/") or _NEGATION.search(text):
        return None
    if _BIRTHDAY.search(text):
        return "birthday"
    if _WISH.search(text):
        return "congratulation"
    return None


def festive_reply(kind: str, *, rng: random, public_work_title: str | None = None, recent_replies: tuple[str, ...] = ()) -> str:
    """Shuffle emoji across words, sometimes within a word; cap total size."""
    lines = _BIRTHDAY_LINES if kind == "birthday" else _WISH_LINES
    # Avoid repeating the same base phrase even if emojis differ.\n    used = tuple(item.casefold() for item in recent_replies[-20:])\n    available = [line for line in lines if not any(line.casefold() in old for old in used)]\n    message = rng.choice(available or lines)
    # Only a previously verified, published and author-opted-in title is allowed.
    # Never use draft text, descriptions, submission statuses or unpublished titles.
    if kind == "birthday" and public_work_title:
        title = " ".join(public_work_title.split())
        if 1 <= len(title) <= 80 and not any(mark in title for mark in ("<", ">", "\\n", "\\r")):
            message += f" Пусть вдохновение для «{title}» никогда не заканчивается!"
    words = message.split(" ")
    # Emoji bursts are deliberately noisy *within one reply*, never extra chat messages.\n    emojis = [rng.choice(_EMOJI) for _ in range(rng.randint(9, 24))]
    for emoji in emojis:
        position = rng.randrange(len(words) + 1)
        if position < len(words) and len(words[position]) >= 6 and rng.random() < 0.28:
            word = words[position]
            split = rng.randint(2, len(word) - 2)
            words[position] = word[:split] + emoji + word[split:]
        else:
            words.insert(position, emoji)
    return " ".join(words)[:500]


__all__ = ["celebration_kind", "festive_reply"]
