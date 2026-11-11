"""Persistent sticker setting; PostgreSQL is authoritative when configured."""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any

from lexicon_raccoon_sticker import is_valid_sticker_file_id


class MemeStickerStore:
    def __init__(self, data_dir: Path, *, postgres: Any = None) -> None:
        self.data_dir = Path(data_dir)
        self.postgres = postgres
        self._cache: dict[int, str | None] = {}
        self._lock = asyncio.Lock()

    def _path(self, chat_id: int) -> Path:
        return self.data_dir / f'writers_meme_sticker_{int(chat_id)}.txt'

    async def get(self, chat_id: int) -> str | None:
        chat_id = int(chat_id)
        async with self._lock:
            if chat_id not in self._cache:
                if self.postgres is not None:
                    value = await self.postgres.get_meme_sticker(chat_id)
                else:
                    try:
                        value = await asyncio.to_thread(self._path(chat_id).read_text, encoding='utf-8')
                    except FileNotFoundError:
                        value = None
                value = value.strip() if isinstance(value, str) else None
                self._cache[chat_id] = value if value and is_valid_sticker_file_id(value) else None
            return self._cache[chat_id]

    def _write_file(self, chat_id: int, file_id: str) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                             dir=self.data_dir, delete=False) as output:
                temporary = Path(output.name)
                output.write(file_id + '\n')
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self._path(chat_id))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    async def set(self, chat_id: int, file_id: str) -> None:
        if not isinstance(file_id, str) or not is_valid_sticker_file_id(file_id):
            raise ValueError('Invalid sticker file_id')
        chat_id, file_id = int(chat_id), file_id.strip()
        async with self._lock:
            if self.postgres is not None:
                await self.postgres.set_meme_sticker(chat_id, file_id)
            else:
                await asyncio.to_thread(self._write_file, chat_id, file_id)
            self._cache[chat_id] = file_id
