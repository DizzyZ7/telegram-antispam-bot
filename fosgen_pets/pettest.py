"""Admin-only, isolated /pettest laboratory. No production pets are created.

Usage (private chat only):
  /pettest             -> creates a temporary random animal, PNG, ID and DNA
  /pettest <id>        -> displays an existing temporary animal
  /pettest <id> delete -> permanently removes the test record
The test database is deliberately separate from production pet tables.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import io
import json
import os
from pathlib import Path
import secrets
import sqlite3
from typing import Any

from aiogram import F
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message

from .core import Appearance, build_appearance
from .render_svg import SUPPORTED, render_svg


def parse_admin_ids(raw: str) -> frozenset[int]:
    values = [part.strip() for part in raw.split(",") if part.strip()]
    if any(not value.isdecimal() or int(value) <= 0 for value in values):
        raise ValueError("FOSGEN_PETTEST_ADMIN_IDS must contain positive numeric Telegram IDs")
    return frozenset(map(int, values))


def parse_command(text: str) -> tuple[str, int | None]:
    parts = text.strip().split()
    if len(parts) == 1:
        return "create", None
    if len(parts) in (2, 3) and parts[1].isdecimal():
        ident = int(parts[1])
        if ident <= 0:
            raise ValueError("ID must be positive")
        if len(parts) == 2:
            return "show", ident
        if parts[2].lower() == "delete":
            return "delete", ident
    raise ValueError("Usage: /pettest | /pettest ID | /pettest ID delete")


@dataclass(frozen=True)
class TestPet:
    id: int
    owner_id: int
    appearance: Appearance


class TestPetStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("""CREATE TABLE IF NOT EXISTS fosgen_pettest (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            dna TEXT NOT NULL,
            fingerprint TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        return conn

    @staticmethod
    def _decode(row: tuple[Any, ...]) -> TestPet:
        ident, owner_id, dna = row
        return TestPet(int(ident), int(owner_id), Appearance(**json.loads(dna)))

    def _create_sync(self, owner_id: int) -> TestPet:
        with self._connect() as conn:
            for _ in range(100):
                species = secrets.choice(tuple(sorted(SUPPORTED)))
                appearance = build_appearance(species, secrets.token_hex(24))
                try:
                    cur = conn.execute(
                        "INSERT INTO fosgen_pettest(owner_id,dna,fingerprint) VALUES (?,?,?)",
                        (owner_id, appearance.canonical_dna(), appearance.fingerprint()),
                    )
                    return TestPet(int(cur.lastrowid), owner_id, appearance)
                except sqlite3.IntegrityError:
                    continue
        raise RuntimeError("Could not allocate unique test DNA")

    async def create(self, owner_id: int) -> TestPet:
        async with self._lock:
            return await asyncio.to_thread(self._create_sync, owner_id)

    def _get_sync(self, ident: int, owner_id: int) -> TestPet | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id,owner_id,dna FROM fosgen_pettest WHERE id=? AND owner_id=?",
                (ident, owner_id),
            ).fetchone()
        return self._decode(row) if row else None

    async def get(self, ident: int, owner_id: int) -> TestPet | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_sync, ident, owner_id)

    def _delete_sync(self, ident: int, owner_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM fosgen_pettest WHERE id=? AND owner_id=?",
                (ident, owner_id),
            )
            return cur.rowcount == 1

    async def delete(self, ident: int, owner_id: int) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._delete_sync, ident, owner_id)


def render_png(appearance: Appearance) -> bytes:
    """Rasterize the exact versioned SVG so Telegram shows a normal photo."""
    import cairosvg

    return cairosvg.svg2png(bytestring=render_svg(appearance).encode("utf-8"), output_width=512, output_height=512)


def register_pettest_handlers(app: Any, *, admin_ids: frozenset[int], data_dir: Path) -> None:
    if not admin_ids:
        return
    store = TestPetStore(data_dir / "fosgen_pettest.sqlite3")

    async def pettest(message: Message) -> None:
        if not message.from_user or message.from_user.id not in admin_ids:
            return
        if message.chat.type != "private":
            await message.answer("🐾 /pettest доступен только в личном чате с Фосгеном.")
            return
        try:
            operation, ident = parse_command(message.text or "")
        except ValueError:
            await message.answer("Команды: /pettest, /pettest ID, /pettest ID delete")
            return

        owner_id = message.from_user.id
        try:
            if operation == "delete":
                deleted = await store.delete(ident, owner_id)
                await message.answer(
                    f"🗑 Тестовый питомец #{ident} удален из базы." if deleted
                    else "Такого тестового питомца нет."
                )
                return
            pet = await store.create(owner_id) if operation == "create" else await store.get(ident, owner_id)
            if pet is None:
                await message.answer("Такого тестового питомца нет.")
                return
            png = await asyncio.to_thread(render_png, pet.appearance)
            dna = pet.appearance.canonical_dna()
            await message.answer_photo(
                BufferedInputFile(png, filename=f"pettest_{pet.id}.png"),
                caption=(
                    f"🐾 Тестовый питомец #{pet.id}\\n"
                    f"Вид: {pet.appearance.species}\\n"
                    f"Окрас: {pet.appearance.palette} · {pet.appearance.pattern}\\n"
                    f"Глаза: {pet.appearance.eyes}\\n"
                    f"Отметина: {pet.appearance.marking} ({pet.appearance.marking_variant})\\n"
                    f"DNA SHA256: {pet.appearance.fingerprint()}\\n"
                    f"\\nУдалить: /pettest {pet.id} delete"
                ).replace("\\n", "\n"),
            )
            # Full DNA is separate to keep the image caption readable.
            await message.answer(f"🧬 DNA #{pet.id}:\n<code>{dna}</code>", parse_mode="HTML")
        except Exception:
            import logging
            logging.exception("FOSGEN_PETTEST_FAILED")
            await message.answer("Не удалось создать или показать тестового питомца. Проверь логи.")

    # Register ahead of generic catch-all handlers (see main.py).
    app.dp.message.register(pettest, Command("pettest"))
