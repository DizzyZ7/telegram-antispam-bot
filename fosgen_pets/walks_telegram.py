"""Explicit group outings; moderation stays outside these narrowly scoped handlers."""
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
import logging
import re
import time
from typing import Any
from zoneinfo import ZoneInfo

from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from .stories import SPECIES_NAMES
from .walks import ROUTES, WALK_MINUTES, WALK_XP, walk_day
from .walks_postgres import PetWalkStore, WalkDiary

LOGGER = logging.getLogger(__name__)
_MOSCOW = ZoneInfo("Europe/Moscow")
_ROUTE_ALIASES = {"луг": "meadow", "лес": "forest", "река": "river", **{k: k for k in ROUTES}}
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
HELP = ("🌿 В подключенном чате: /pet_walk — лес, /pet_graze — луг, "
        "/pet_walk river — берег. Прогулка длится 30 минут, один зачетный выход в день. "
        "После нее +20 опыта дружбы. /pet_diary — дневник этого чата. "
        "Команда прогулки добавляет питомца в публичный дневник чата.")


class WalkInputError(ValueError):
    """Only invalid user arguments, never storage/Telegram failures."""


class ReplyGate:
    """Bound memory and bot replies; SQL remains the cross-worker reward gate."""
    def __init__(self):
        self._last: OrderedDict[tuple[int, str], float] = OrderedDict()

    def allow(self, owner: int, kind: str) -> bool:
        now = time.monotonic()
        key = (owner, kind)
        last = self._last.get(key)
        if last is not None and now - last < 30:
            return False
        self._last[key] = now
        self._last.move_to_end(key)
        if len(self._last) > 4096:
            self._last.popitem(last=False)
        return True


def diary_text(diary: WalkDiary, now: datetime) -> str:
    lines = [f"📖 Прогулки · {diary.day:%d.%m.%Y} · МСК", f"Участников: {diary.total}"]
    if not diary.walks:
        lines.append("На этой странице пока никого нет." if diary.total else "В этот день питомцы еще не выходили гулять.")
    for walk in diary.walks:
        stamp = walk.started_at.astimezone(_MOSCOW).strftime("%H:%M")
        name = SPECIES_NAMES.get(walk.species, "питомец").capitalize()
        state = walk.activity if walk.ends_at <= now else "Прогулка еще идет."
        lines.append(f"\n{stamp} · {name} #{walk.pet_id} · {ROUTES[walk.route]}\n{state}")
    lines.append(f"\nСтраница {diary.page}/{diary.pages}")
    if diary.page < diary.pages:
        lines.append(f"Далее: /pet_diary {diary.day.isoformat()} {diary.page + 1}")
    return "\n".join(lines)


class PetWalkTelegramService:
    def __init__(self, app: Any, walks: PetWalkStore, chat_ids: frozenset[int],
                 isolated_chat_ids: frozenset[int]):
        if not isolated_chat_ids.issubset(chat_ids):
            raise ValueError("Isolated walks must be inside the explicit Pets scope")
        self.app, self.walks, self.chat_ids = app, walks, chat_ids
        self.isolated_chat_ids = isolated_chat_ids
        self.gate = ReplyGate()
        self.registrations: list[tuple[Any, Any]] = []

    async def command(self, message: Message, command: CommandObject) -> None:
        user = message.from_user
        if (user is None or user.is_bot or user.id <= 0 or message.sender_chat is not None
                or message.edit_date is not None or message.forward_origin is not None
                or message.via_bot is not None):
            raise SkipHandler
        if message.chat.type == "private" and message.chat.id == user.id:
            if self.gate.allow(user.id, "private_help"):
                await message.answer(HELP, parse_mode=None)
            return
        isolated = message.chat.id in self.isolated_chat_ids
        if (message.chat.type not in ("group", "supergroup") or message.chat.id not in self.chat_ids
                or (not isolated and message.chat.id not in getattr(self.app, "ALLOWED_CHATS", ()))
                or (not isolated and message.chat.id in getattr(self.app, "LEXICON_ONLY_CHAT_IDS", ()))
                or user.id in getattr(self.app, "pending_users", {})):
            raise SkipHandler
        now = datetime.now(timezone.utc)
        if not now - timedelta(minutes=5) <= message.date <= now + timedelta(minutes=1):
            return
        kind = "diary" if command.command == "pet_diary" else "walk"
        if not self.gate.allow(user.id, kind):
            return
        try:
            args = (command.args or "").strip()
            if len(args) > 64:
                raise WalkInputError("Too many arguments")
            if kind == "diary":
                parts = args.split()
                if len(parts) > 2 or (parts and not _DATE.fullmatch(parts[0])):
                    raise WalkInputError("Invalid diary date")
                try:
                    day = date.fromisoformat(parts[0]) if parts else walk_day(now)
                except ValueError:
                    raise WalkInputError("Invalid diary date") from None
                if len(parts) > 1 and (not parts[1].isascii() or not parts[1].isdigit() or len(parts[1]) > 6):
                    raise WalkInputError("Invalid diary page")
                page = int(parts[1]) if len(parts) > 1 else 1
                if not 1 <= page <= 100000:
                    raise WalkInputError("Invalid diary page")
                diary = await self.walks.diary(message.chat.id, day, page=page)
                await message.answer(diary_text(diary, now), parse_mode=None)
                return
            if command.command == "pet_graze":
                if args:
                    raise WalkInputError("Grazing takes no arguments")
                route = "meadow"
            else:
                route = _ROUTE_ALIASES.get(args.casefold() or "forest")
                if route is None:
                    raise WalkInputError("Unknown route")
            result = await self.walks.start(user.id, message.chat.id, message.message_id, route,
                                            thread_id=message.message_thread_id)
            walk = result.walk
            if result.reason == "started" and walk is not None:
                name = SPECIES_NAMES.get(walk.species, "питомец").capitalize()
                text = (f"🌿 {name} #{walk.pet_id} на прогулке! Место: {ROUTES[walk.route]}.\n"
                        f"Возвращение: {walk.ends_at.astimezone(_MOSCOW):%H:%M} МСК. "
                        f"После {WALK_MINUTES} минут +{WALK_XP} опыта дружбы.\n"
                        "В дневнике этого чата будет запись о прогулке: /pet_diary")
            elif result.reason == "pet_missing":
                text = "Сначала получи своего постоянного питомца: /pet_start и /pet в личке с Фосгеном."
            elif result.reason == "active":
                text = "Твой питомец уже гуляет. Еще одна команда не ускоряет прогулку и не дает дополнительный опыт."
            elif result.reason == "daily_limit":
                text = "Сегодня прогулка уже учтена. Следующий зачетный выход — в новый день по Москве. /pet_diary — дневник."
            elif result.reason == "replay":
                text = "Эта команда уже обработана. Повтор не начинает новую прогулку и не дает дополнительный опыт."
            else:
                text = "Эта команда уже использована. Открой /pet в личке."
            await message.answer(text, parse_mode=None)
        except WalkInputError:
            await message.answer("Маршруты: /pet_walk лес, /pet_walk луг, /pet_walk река. "
                                 "Дневник: /pet_diary или /pet_diary 2026-11-16 1.", parse_mode=None)
        except Exception as exc:
            LOGGER.warning("FOSGEN_PETS_WALK_FAILED reason=%s", type(exc).__name__)
            from .telegram import UNAVAILABLE
            await message.answer(UNAVAILABLE, parse_mode=None)


def register_walk_handlers(app: Any, walks: PetWalkStore, *, chat_ids: frozenset[int],
                           isolated_chat_ids: frozenset[int] = frozenset()) -> PetWalkTelegramService:
    service = PetWalkTelegramService(app, walks, chat_ids, isolated_chat_ids)
    command = service.command
    app.dp.message.register(command, Command("pet_walk", "pet_graze", "pet_diary"))
    handlers = app.dp.message.handlers
    record = next(item for item in reversed(handlers) if item.callback is command)
    handlers.remove(record)
    handlers.insert(0, record)
    service.registrations = [(app.dp.message, command)]
    return service
