"""Lightweight birthday and congratulation recognition and playful greetings."""
from __future__ import annotations

import random
import re
import hashlib
from datetime import datetime

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
    "С днем рождения! Если жизнь — фанфик, пусть у тебя всегда будет любимый автор.",
    "СРОЧНЫЕ НОВОСТИ! У тебя сегодня день рождения, а значит, все остальные новости отменяются.",
    "Поздравляю! Пусть твоя жизнь будет как хороший сериал: новый сезон лучше предыдущего.",
    "Сегодня твой персональный праздник. Даже енот достал праздничный галстук.",
    "С днем рождения! Пусть счастье появляется без кулдауна, а грусть уходит в бан.",
    "Поздравляю! Пусть каждый день приносит хотя бы одну историю, которую захочется пересказать.",
    "Праздничный комитет мемов постановил: сегодня ты невероятен. С днем рождения!",
    "С днем рождения! Пусть твои мечты получают зеленый свет без очереди.",
    "Пока все поздравляют, я просто напомню: ты главный герой сегодняшнего эпизода.",
    "С днем рождения! Пусть удача пишет тебе первой, а проблемы остаются в архиве.",
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
    "Поздравляю! Желаю, чтобы приятные неожиданности стали привычкой.",
    "Поздравляю! Даже енот отложил свои важные дела ради такого события.",
    "ОБЪЯВЛЕНИЕ: в чате зафиксирован редкий уровень радости. Поздравляю!",
    "Пусть этот повод станет началом целой серии счастливых событий!",
    "Поздравляю! Желаю столько поводов для гордости, чтобы не хватило закрепов.",
    "Сегодня можно немного погордиться собой. Поздравляю!",
    "Официальный вердикт праздничной комиссии: это заслуживает аплодисментов!",
)


def celebration_kind(text: str | None) -> str | None:
    if not text or len(text) > 500 or text.lstrip().startswith("/") or _NEGATION.search(text):
        return None
    if _BIRTHDAY.search(text):
        return "birthday"
    if _WISH.search(text):
        return "congratulation"
    return None



_SEASONAL = {
    "winter": ("Пусть удача сияет ярче гирлянд.", "Желаю теплых людей даже в холодные дни.", "Пусть снежинки приносят хорошие новости.", "Пусть мороз оставит только приятные мурашки."),
    "spring": ("Пусть идеи расцветают быстрее подснежников.", "Пусть весна добавит вдохновения.", "Желаю новых глав и легкого настроения.", "Пусть счастье просыпается вместе с природой."),
    "summer": ("Пусть лето будет как отпуск у главного героя.", "Пусть солнце заряжает лучше кофе.", "Желаю теплых встреч и незабываемых историй.", "Пусть удача будет ярче июльского неба."),
    "autumn": ("Пусть уют и хорошие новости идут комплектом.", "Желаю золотых идей и теплых разговоров.", "Пусть дождь приносит только вдохновение.", "Пусть осенние главы будут самыми красивыми."),
}
_DAYPART = {
    "morning": ("Утро официально объявлено праздничным.", "Сегодня даже будильник должен поздравлять."),
    "day": ("Дневной эфир захвачен хорошими новостями.", "Середина дня, а повод для счастья уже найден."),
    "evening": ("Вечер заслуживает праздничного саундтрека.", "Пусть сегодняшний вечер запомнится надолго."),
    "night": ("Даже ночные совы сегодня празднуют.", "Пусть ночные звезды подмигивают тебе."),
}


def celebration_context(moment: datetime) -> tuple[str, str]:
    month, hour = moment.month, moment.hour
    season = ("winter" if month in (12, 1, 2) else
              "spring" if month in (3, 4, 5) else
              "summer" if month in (6, 7, 8) else "autumn")
    daypart = "morning" if 5 <= hour < 12 else "day" if 12 <= hour < 18 else "evening" if 18 <= hour < 23 else "night"
    return season, daypart


def festive_reply(kind: str, *, rng: random, public_work_title: str | None = None, recent_replies: tuple[str, ...] = (), moment: datetime | None = None, occasion_key: str | None = None) -> str:
    """Shuffle emoji across words, sometimes within a word; cap total size."""
    lines = _BIRTHDAY_LINES if kind == "birthday" else _WISH_LINES
    # Avoid repeating the same base phrase even if emojis differ.
    used = tuple(item.casefold() for item in recent_replies[-20:])
    available = [line for line in lines if not any(line.casefold() in old for old in used)]
    # Deterministic rotation gives 365 distinct day slots without a new DB table.
    # The same occasion gets the same base line, regardless of worker restart.
    if occasion_key:
        digest = hashlib.sha256(occasion_key.encode('utf-8')).digest()
        message = lines[int.from_bytes(digest[:4], 'big') % len(lines)]
    else:
        message = rng.choice(available or lines)
    if moment is not None:
        season, daypart = celebration_context(moment)
        if occasion_key:
            digest = hashlib.sha256(occasion_key.encode('utf-8')).digest()
            seasonal = _SEASONAL[season][digest[4] % len(_SEASONAL[season])]
            daypart_line = _DAYPART[daypart][digest[5] % len(_DAYPART[daypart])]
        else:
            seasonal, daypart_line = rng.choice(_SEASONAL[season]), rng.choice(_DAYPART[daypart])
        message += ' ' + seasonal + ' ' + daypart_line
    # Only a previously verified, published and author-opted-in title is allowed.
    # Never use draft text, descriptions, submission statuses or unpublished titles.
    if kind == "birthday" and public_work_title:
        title = " ".join(public_work_title.split())
        if 1 <= len(title) <= 80 and not any(mark in title for mark in ("<", ">", "\\n", "\\r")):
            message += f" Пусть вдохновение для «{title}» никогда не заканчивается!"
    words = message.split(" ")
    # Emoji bursts are deliberately noisy *within one reply*, never extra chat messages.
    emojis = [rng.choice(_EMOJI) for _ in range(rng.randint(9, 24))]
    for emoji in emojis:
        position = rng.randrange(len(words) + 1)
        if position < len(words) and len(words[position]) >= 6 and rng.random() < 0.28:
            word = words[position]
            split = rng.randint(2, len(word) - 2)
            words[position] = word[:split] + emoji + word[split:]
        else:
            words.insert(position, emoji)
    return " ".join(words)[:500]


__all__ = ["celebration_kind", "festive_reply", "celebration_context"]
