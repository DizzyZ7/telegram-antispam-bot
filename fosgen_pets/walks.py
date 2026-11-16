"""Versioned, local walk stories and gentle friendship progression."""
from datetime import date, datetime
from hashlib import sha256
from math import isqrt
from zoneinfo import ZoneInfo

from .biography import pet_sex
from .core import Appearance

WALK_MINUTES = 30
WALK_XP = 20
DIARY_PAGE_SIZE = 10
ROUTES = {"meadow": "луг", "forest": "лесная тропинка", "river": "берег реки"}
_GRAZERS = frozenset({"foal", "calf", "fawn", "capybara", "alpaca", "bunny"})
_ACTIVITIES = (
    ("разглядывал", "разглядывала", "облака и грелся на солнышке", "облака и грелась на солнышке"),
    ("искал", "искала", "самый красивый листочек", "самый красивый листочек"),
    ("слушал", "слушала", "пение птиц и шорох травы", "пение птиц и шорох травы"),
    ("наблюдал", "наблюдала", "за бабочкой и отдыхал в тени", "за бабочкой и отдыхала в тени"),
    ("изучал", "изучала", "новую тропинку и знакомые запахи", "новую тропинку и знакомые запахи"),
    ("нашел", "нашла", "тихое место для отдыха", "тихое место для отдыха"),
)


def level_for_xp(xp: int) -> int:
    if type(xp) is not int or xp < 0:
        raise ValueError("Experience must be a nonnegative integer")
    return min(100, 1 + isqrt(xp // 25))


def walk_day(now: datetime) -> date:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Walk timestamps must be timezone-aware")
    return now.astimezone(ZoneInfo("Europe/Moscow")).date()


def activity(appearance: Appearance, day: date, route: str) -> str:
    if route not in ROUTES:
        raise ValueError("Unknown walk route")
    digest = sha256(f"pet-walk-story-v1:{appearance.fingerprint()}:{day.isoformat()}:{route}".encode()).digest()
    female = pet_sex(appearance) == "female"
    if route == "meadow" and appearance.species in _GRAZERS:
        return ("Щипала" if female else "Щипал") + " свежую травку и отдыхал" + ("а" if female else "") + " на мягком лугу."
    if route == "river" and appearance.species in {"otter", "capybara"}:
        return ("Плескалась" if female else "Плескался") + " у берега и разглядывал" + ("а" if female else "") + " круги на воде."
    row = _ACTIVITIES[digest[0] % len(_ACTIVITIES)]
    return f"{row[int(female)].capitalize()} {row[2 + int(female)]}."
