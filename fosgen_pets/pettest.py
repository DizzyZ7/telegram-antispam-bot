"""Admin-only private laboratory with a separate temporary-pet database.

/pettest [SPECIES] creates a pet; ID, ID dna, list and ID delete inspect it.
Bulk delete supports ranges, comma-separated IDs and explicitly requested all.
/pettest series SPECIES COUNT [PATTERN_SEED] creates one atomic gallery.
With a seed, only consecutive Pattern Seeds vary; other DNA fields match.
Stored PNG/SVG bytes freeze the animal seen by the administrator.
"""
from __future__ import annotations

import asyncio
from contextlib import closing
from dataclasses import dataclass, replace
from html import escape
import json
import logging
from pathlib import Path
import re
import secrets
import sqlite3
from typing import Any

from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, InputMediaPhoto, Message

from .core import Appearance, MAX_PATTERN_SEED, SPECIES, build_appearance
from .portrait import render_png, render_portrait

MAX_SQL_ID = 2**63 - 1
MAX_COMMAND_LENGTH = 256
MAX_PETS_PER_OWNER = 30
_COMMAND = re.compile(r"/pettest(?:@[A-Za-z0-9_]{1,32})?(?:[ \t]+[A-Za-z0-9_,-]+)*[ \t]*", re.ASCII)
_USAGE = (
    "Команды: /pettest, /pettest kitten, /pettest ID, /pettest ID dna, "
    "/pettest list, /pettest ID delete, /pettest id 1-27 delete, "
    "/pettest 1,3,5-8 delete, /pettest all delete, "
    "/pettest series kitten 6 [PATTERN_SEED]. "
    "Серия: 2–8 питомцев; Pattern Seed: 0–999999. "
    "С заданным зерном все признаки одинаковы, меняется только Pattern Seed: SEED, SEED+1… "
    "(после 999999 идет 0). Без зерна — случайные внешности. "
    "Виды: " + ", ".join(SPECIES)
)


def _positive_id(value: int) -> int:
    if type(value) is not int or not 1 <= value <= MAX_SQL_ID:
        raise ValueError("ID must be a positive signed 64-bit integer")
    return value


def _number(value: str, *, minimum: int = 1, maximum: int = MAX_SQL_ID) -> int:
    if not 1 <= len(value) <= 19 or not re.fullmatch(r"[0-9]+", value, re.ASCII):
        raise ValueError("Expected an ASCII integer in the allowed range")
    result = int(value)
    if not minimum <= result <= maximum:
        raise ValueError("Integer is outside the allowed range")
    return result


def _pattern_seed(value: int | None) -> None:
    if value is not None and (type(value) is not int or not 0 <= value <= MAX_PATTERN_SEED):
        raise ValueError("Pattern seed must be an integer in 0..999999")


def _parse_delete_ids(spec: str) -> tuple[int, ...]:
    """Accept a bounded comma list of IDs and inclusive ascending ranges."""
    selected: set[int] = set()
    for part in spec.split(","):
        bounds = part.split("-")
        if len(bounds) not in (1, 2):
            raise ValueError("Invalid test ID range; use 1-27 or 1,3,5-8")
        first = _number(bounds[0])
        last = _number(bounds[1]) if len(bounds) == 2 else first
        if last < first or last - first + 1 > MAX_PETS_PER_OWNER:
            raise ValueError("An ID range must be ascending and contain at most 30 IDs")
        selected.update(range(first, last + 1))
        if len(selected) > MAX_PETS_PER_OWNER:
            raise ValueError("Choose at most 30 test IDs per command, or use /pettest all delete")
    return tuple(sorted(selected))


def parse_admin_ids(raw: str) -> frozenset[int]:
    if not isinstance(raw, str) or len(raw) > 8192:
        raise ValueError("FOSGEN_PETTEST_ADMIN_IDS must contain positive numeric Telegram IDs")
    try:
        return frozenset(_number(part.strip()) for part in raw.split(",") if part.strip())
    except ValueError as exc:
        raise ValueError("FOSGEN_PETTEST_ADMIN_IDS must contain positive numeric Telegram IDs") from exc


@dataclass(frozen=True)
class PettestRequest:
    operation: str
    ident: int | None = None
    species: str | None = None
    count: int = 1
    pattern_seed: int | None = None
    identifiers: tuple[int, ...] = ()


def parse_request(text: str) -> PettestRequest:
    if not isinstance(text, str) or not 1 <= len(text) <= MAX_COMMAND_LENGTH:
        raise ValueError(_USAGE)
    text = text.strip(" \t")
    if not _COMMAND.fullmatch(text):
        raise ValueError(_USAGE)
    args = text.split()[1:]
    if not args:
        return PettestRequest("create")
    if len(args) == 1 and args[0].lower() == "list":
        return PettestRequest("list")
    if len(args) == 1 and args[0] in SPECIES:
        return PettestRequest("create", species=args[0])
    if len(args) in (3, 4) and args[0] == "series" and args[1] in SPECIES:
        return PettestRequest(
            "series", species=args[1], count=_number(args[2], minimum=2, maximum=8),
            pattern_seed=_number(args[3], minimum=0, maximum=MAX_PATTERN_SEED) if len(args) == 4 else None,
        )
    if len(args) == 2 and args[0] == "dna":
        return PettestRequest("dna", ident=_number(args[1]))
    if len(args) == 2 and args[0].lower() == "all" and args[1].lower() == "delete":
        return PettestRequest("delete_all")
    if len(args) == 3 and args[0].lower() == "id" and args[2].lower() == "delete":
        return PettestRequest("delete_many", identifiers=_parse_delete_ids(args[1]))
    if len(args) == 2 and args[1].lower() == "delete" and ("," in args[0] or "-" in args[0]):
        return PettestRequest("delete_many", identifiers=_parse_delete_ids(args[0]))
    if len(args) in (1, 2):
        ident = _number(args[0])
        if len(args) == 1:
            return PettestRequest("show", ident=ident)
        if args[1].lower() in ("delete", "dna"):
            return PettestRequest(args[1].lower(), ident=ident)
    raise ValueError(_USAGE)


def parse_command(text: str) -> tuple[str, int | None]:
    """Keep the original two-item contract for existing integrations."""
    request = parse_request(text)
    return request.operation, request.ident


@dataclass(frozen=True)
class TestPet:
    id: int
    owner_id: int
    appearance: Appearance
    png: bytes
    svg: str
    pixel_hash: str


class TestPetStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout=10000")
            # Serialize schema migration across store instances/processes too.
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("""CREATE TABLE IF NOT EXISTS fosgen_pettest (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id INTEGER NOT NULL,
                    dna TEXT NOT NULL,
                    fingerprint TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    png BLOB,
                    svg TEXT,
                    pixel_hash TEXT
                )""")
                columns = {row[1] for row in conn.execute("PRAGMA table_info(fosgen_pettest)")}
                for name, kind in (("png", "BLOB"), ("svg", "TEXT"), ("pixel_hash", "TEXT")):
                    if name not in columns:
                        conn.execute(f"ALTER TABLE fosgen_pettest ADD COLUMN {name} {kind}")
                conn.execute("CREATE INDEX IF NOT EXISTS fosgen_pettest_owner_idx ON fosgen_pettest(owner_id)")
                # Old records may share a raster. Cache those faithfully; new
                # allocations reserve hashes under BEGIN IMMEDIATE below.
                conn.execute("CREATE INDEX IF NOT EXISTS fosgen_pettest_pixel_idx ON fosgen_pettest(pixel_hash)")
            return conn
        except BaseException:
            conn.close()
            raise

    @staticmethod
    def _appearance(dna: str) -> Appearance:
        fields = json.loads(dna)
        fields.setdefault("asset_version", 1)
        return Appearance(**fields)

    @classmethod
    def _decode(cls, row: tuple[Any, ...]) -> TestPet:
        ident, owner_id, dna, png, svg, pixel_hash = row
        return TestPet(int(ident), int(owner_id), cls._appearance(dna), bytes(png), svg, pixel_hash)

    @staticmethod
    def _check_capacity(conn: sqlite3.Connection, owner_id: int, count: int) -> None:
        current = conn.execute("SELECT COUNT(*) FROM fosgen_pettest WHERE owner_id=?", (owner_id,)).fetchone()[0]
        if current + count > MAX_PETS_PER_OWNER:
            raise ValueError("Limit of 30 temporary pets reached; delete old test IDs first")

    def _create_many_sync(
        self, owner_id: int, species: str | None, count: int, pattern_seed: int | None,
    ) -> list[TestPet]:
        with closing(self._connect()) as conn:
            self._check_capacity(conn, owner_id, count)
            candidates: list[tuple[Appearance, Any]] = []
            fingerprints: set[str] = set()
            raster_hashes: set[str] = set()
            comparison = count > 1 and pattern_seed is not None
            base: Appearance | None = None
            # Bounded retries cover raster-equivalent DNA (e.g. solid coats).
            for _ in range(100):
                selected = species if species is not None else secrets.choice(SPECIES)
                if comparison:
                    if base is None:
                        base = build_appearance(selected, secrets.token_hex(24), pattern_seed=pattern_seed)
                    appearance = replace(base, pattern_seed=(pattern_seed + len(candidates)) % (MAX_PATTERN_SEED + 1))
                else:
                    appearance = build_appearance(selected, secrets.token_hex(24), pattern_seed=pattern_seed)
                portrait = render_portrait(appearance)
                fingerprint = appearance.fingerprint()
                if fingerprint in fingerprints or portrait.pixel_hash in raster_hashes:
                    if comparison:
                        base = None
                        candidates.clear()
                        fingerprints.clear()
                        raster_hashes.clear()
                    continue
                if conn.execute(
                    "SELECT 1 FROM fosgen_pettest WHERE fingerprint=? OR pixel_hash=? LIMIT 1",
                    (fingerprint, portrait.pixel_hash),
                ).fetchone():
                    if comparison:
                        base = None
                        candidates.clear()
                        fingerprints.clear()
                        raster_hashes.clear()
                    continue
                candidates.append((appearance, portrait))
                fingerprints.add(fingerprint)
                raster_hashes.add(portrait.pixel_hash)
                if len(candidates) != count:
                    continue
                # Render all candidates before starting a write transaction.
                # Recheck capacity and both uniqueness keys under the DB lock.
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    self._check_capacity(conn, owner_id, count)
                    collision = any(conn.execute(
                        "SELECT 1 FROM fosgen_pettest WHERE fingerprint=? OR pixel_hash=? LIMIT 1",
                        (candidate.fingerprint(), image.pixel_hash),
                    ).fetchone() for candidate, image in candidates)
                    if collision:
                        conn.rollback()
                        base = None
                        candidates.clear()
                        fingerprints.clear()
                        raster_hashes.clear()
                        continue
                    pets = []
                    for candidate, image in candidates:
                        cur = conn.execute(
                            "INSERT INTO fosgen_pettest(owner_id,dna,fingerprint,png,svg,pixel_hash) VALUES (?,?,?,?,?,?)",
                            (owner_id, candidate.canonical_dna(), candidate.fingerprint(), image.png, image.svg, image.pixel_hash),
                        )
                        pets.append(TestPet(int(cur.lastrowid), owner_id, candidate, image.png, image.svg, image.pixel_hash))
                    return pets
        raise RuntimeError("Could not allocate a distinct test portrait; try again")

    async def create(
        self, owner_id: int, species: str | None = None, pattern_seed: int | None = None,
    ) -> TestPet:
        _positive_id(owner_id)
        if species is not None and species not in SPECIES:
            raise ValueError("Unknown pet species")
        _pattern_seed(pattern_seed)
        async with self._lock:
            return (await asyncio.to_thread(self._create_many_sync, owner_id, species, 1, pattern_seed))[0]

    async def create_series(
        self, owner_id: int, species: str, count: int, pattern_seed: int | None = None,
    ) -> list[TestPet]:
        """Seeded galleries vary only Pattern Seed; unseeded pets vary freely."""
        _positive_id(owner_id)
        if species not in SPECIES or type(count) is not int or not 2 <= count <= 8:
            raise ValueError("Series requires a known species and a count in 2..8")
        _pattern_seed(pattern_seed)
        async with self._lock:
            return await asyncio.to_thread(self._create_many_sync, owner_id, species, count, pattern_seed)

    def _get_sync(self, ident: int, owner_id: int) -> TestPet | None:
        with closing(self._connect()) as conn:
            query = "SELECT id,owner_id,dna,png,svg,pixel_hash FROM fosgen_pettest WHERE id=? AND owner_id=?"
            row = conn.execute(query, (ident, owner_id)).fetchone()
            if not row:
                return None
            if any(value is None for value in row[3:]):
                portrait = render_portrait(self._appearance(row[2]))
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute(
                        "UPDATE fosgen_pettest SET png=?,svg=?,pixel_hash=? WHERE id=? AND owner_id=? "
                        "AND (png IS NULL OR svg IS NULL OR pixel_hash IS NULL)",
                        (portrait.png, portrait.svg, portrait.pixel_hash, ident, owner_id),
                    )
                    row = conn.execute(query, (ident, owner_id)).fetchone()
            return self._decode(row) if row else None

    async def get(self, ident: int, owner_id: int) -> TestPet | None:
        _positive_id(ident)
        _positive_id(owner_id)
        async with self._lock:
            return await asyncio.to_thread(self._get_sync, ident, owner_id)

    def _delete_sync(self, ident: int, owner_id: int) -> bool:
        with closing(self._connect()) as conn:
            cur = conn.execute("DELETE FROM fosgen_pettest WHERE id=? AND owner_id=?", (ident, owner_id))
            return cur.rowcount == 1

    async def delete(self, ident: int, owner_id: int) -> bool:
        _positive_id(ident)
        _positive_id(owner_id)
        async with self._lock:
            return await asyncio.to_thread(self._delete_sync, ident, owner_id)

    def _delete_many_sync(self, owner_id: int, identifiers: tuple[int, ...] | None) -> list[int]:
        # One SQLite write transaction: no partial delete if a statement fails.
        with closing(self._connect()) as conn:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                if identifiers is None:
                    rows = conn.execute(
                        "SELECT id FROM fosgen_pettest WHERE owner_id=? ORDER BY id", (owner_id,),
                    ).fetchall()
                    conn.execute("DELETE FROM fosgen_pettest WHERE owner_id=?", (owner_id,))
                else:
                    placeholders = ",".join("?" for _ in identifiers)
                    params = (owner_id, *identifiers)
                    rows = conn.execute(
                        f"SELECT id FROM fosgen_pettest WHERE owner_id=? AND id IN ({placeholders}) ORDER BY id",
                        params,
                    ).fetchall()
                    conn.execute(
                        f"DELETE FROM fosgen_pettest WHERE owner_id=? AND id IN ({placeholders})",
                        params,
                    )
                return [int(row[0]) for row in rows]

    async def delete_many(self, owner_id: int, identifiers: tuple[int, ...] | None) -> list[int]:
        """Delete only this admin's laboratory records. None means explicitly all."""
        _positive_id(owner_id)
        if identifiers is not None:
            if not isinstance(identifiers, tuple) or not 1 <= len(identifiers) <= MAX_PETS_PER_OWNER:
                raise ValueError("Choose 1..30 test IDs")
            identifiers = tuple(sorted(set(_positive_id(ident) for ident in identifiers)))
        async with self._lock:
            return await asyncio.to_thread(self._delete_many_sync, owner_id, identifiers)

    def _list_sync(self, owner_id: int) -> list[tuple[int, str]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT id,dna FROM fosgen_pettest WHERE owner_id=? ORDER BY id DESC LIMIT 30", (owner_id,),
            ).fetchall()
        return [(int(ident), json.loads(dna)["species"]) for ident, dna in rows]

    async def list(self, owner_id: int) -> list[tuple[int, str]]:
        _positive_id(owner_id)
        async with self._lock:
            return await asyncio.to_thread(self._list_sync, owner_id)


def _caption(pet: TestPet) -> str:
    appearance = pet.appearance
    seed = str(appearance.pattern_seed) if appearance.pattern_seed is not None else "legacy (v1)"
    return (
        f"🐾 Тестовый питомец #{pet.id}\n"
        f"Вид: {escape(appearance.species)}\n"
        f"Окрас: {escape(appearance.palette)} · {escape(appearance.pattern)}\n"
        f"Pattern Seed: {seed}\n"
        f"Глаза: {escape(appearance.eyes)}\n"
        f"Отметина: {escape(appearance.marking)} ({appearance.marking_variant})\n"
        f"DNA SHA256: {appearance.fingerprint()}\n"
        f"DNA: /pettest {pet.id} dna\nУдалить: /pettest {pet.id} delete"
    )


async def _answer_dna(message: Message, pet: TestPet) -> None:
    await message.answer(
        f"🧬 DNA #{pet.id}:\n<code>{escape(pet.appearance.canonical_dna(), quote=False)}</code>",
        parse_mode="HTML",
    )


def register_pettest_handlers(app: Any, *, admin_ids: frozenset[int], data_dir: Path) -> None:
    if not admin_ids:
        return
    for admin_id in admin_ids:
        _positive_id(admin_id)
    store = TestPetStore(data_dir / "fosgen_pettest.sqlite3")
    render_slots = asyncio.Semaphore(2)

    async def pettest(message: Message) -> None:
        user = message.from_user
        if (
            not user or user.is_bot or type(user.id) is not int
            or not 1 <= user.id <= MAX_SQL_ID or user.id not in admin_ids
        ):
            # Preserve normal moderation for users outside the laboratory.
            raise SkipHandler()
        if message.chat.type != "private":
            await message.answer("🐾 /pettest доступен только в личном чате с Фосгеном.")
            return
        if type(message.chat.id) is not int or message.chat.id != user.id:
            raise SkipHandler()
        try:
            request = parse_request(message.text or "")
        except ValueError:
            await message.answer(_USAGE)
            return
        owner_id = user.id
        try:
            if request.operation == "list":
                items = await store.list(owner_id)
                await message.answer(
                    "🐾 Тестовые питомцы:\n" + "\n".join(f"#{i} · {escape(s)}" for i, s in items)
                    if items else "Тестовых питомцев пока нет.", parse_mode="HTML",
                )
                return
            if request.operation == "delete":
                deleted = await store.delete(request.ident, owner_id)
                await message.answer(
                    f"🗑 Тестовый питомец #{request.ident} удален из базы." if deleted
                    else "Такого тестового питомца нет."
                )
                return
            if request.operation in ("delete_many", "delete_all"):
                selected = request.identifiers if request.operation == "delete_many" else None
                removed = await store.delete_many(owner_id, selected)
                if not removed:
                    await message.answer("Нет подходящих тестовых питомцев для удаления.")
                else:
                    absent = len(selected) - len(removed) if selected is not None else 0
                    details = f" Не найдено среди твоих записей: {absent}." if absent else ""
                    await message.answer(
                        f"🗑 Удалено тестовых питомцев: {len(removed)}. "
                        + "ID: " + ", ".join(map(str, removed)) + "." + details
                    )
                return
            # Bound pending CPU-heavy requests too, without a growing owner map.
            if render_slots.locked():
                await message.answer("🐾 Лаборатория занята. Повтори команду после завершения текущей серии.")
                return
            async with render_slots:
                if request.operation == "series":
                    pets = await store.create_series(owner_id, request.species, request.count, request.pattern_seed)
                else:
                    pet = (
                        await store.create(owner_id, request.species, request.pattern_seed)
                        if request.operation == "create" else await store.get(request.ident, owner_id)
                    )
                    pets = [pet] if pet else []
            if not pets:
                await message.answer("Такого тестового питомца нет.")
                return
            if request.operation == "series":
                await message.answer_media_group(media=[
                    InputMediaPhoto(
                        media=BufferedInputFile(pet.png, filename=f"pettest_{pet.id}.png"),
                        caption=_caption(pet), parse_mode="HTML",
                    ) for pet in pets
                ])
                await message.answer(
                    "🐾 Серия: " + ", ".join(f"#{pet.id}" for pet in pets)
                    + (". Меняется только Pattern Seed" if request.pattern_seed is not None else ". Случайные внешности")
                    + ". Полная DNA: /pettest ID dna; удалить: /pettest ID delete."
                )
                return
            pet = pets[0]
            if request.operation != "dna":
                await message.answer_photo(
                    BufferedInputFile(pet.png, filename=f"pettest_{pet.id}.png"),
                    caption=_caption(pet), parse_mode="HTML",
                )
            await _answer_dna(message, pet)
        except ValueError as exc:
            await message.answer(escape(str(exc)), parse_mode="HTML")
        except Exception:
            logging.exception("FOSGEN_PETTEST_FAILED")
            await message.answer("Не удалось создать или показать тестового питомца. Проверь логи.")

    # Promote only this command before imported legacy catch-all handlers.
    # Dispatcher middleware remains in force and other registrations keep order.
    handlers = app.dp.message.handlers
    previous_count = len(handlers)
    app.dp.message.register(pettest, Command("pettest"))
    for handler in list(handlers[previous_count:]):
        if getattr(handler, "callback", None) is pettest:
            handlers.remove(handler)
            handlers.insert(0, handler)
