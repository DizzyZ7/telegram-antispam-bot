"""Opt-in licensed music search and Telegram audio delivery.

Jamendo only: download links are accepted solely for tracks whose provider
explicitly marks audio downloads as allowed. No scraping or stream ripping.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import aiohttp
from aiogram import F
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

LOG = logging.getLogger(__name__)
API = "https://api.jamendo.com/v3.0/tracks/"
MAX_BYTES = 20 * 1024 * 1024
MAX_RESULTS = 8


@dataclass(frozen=True)
class Track:
    title: str
    artist: str
    download_url: str
    duration: int
    track_id: str


def _trusted_download_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        return (
            parsed.scheme == "https"
            and (hostname == "jamendo.com" or hostname.endswith(".jamendo.com"))
            and parsed.username is None and parsed.password is None
            and parsed.port in (None, 443)
        )
    except ValueError:
        return False


class MusicService:
    def __init__(self, client_id: str):
        self.client_id = client_id.strip()
        self._sessions: dict[str, tuple[float, int, int, Track]] = {}
        self._last_search: dict[int, float] = {}
        self._active_downloads: set[str] = set()
        self._semaphore = asyncio.Semaphore(2)

    async def search(self, query: str) -> list[Track]:
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
            async with session.get(API, params={
                "client_id": self.client_id, "format": "json", "limit": MAX_RESULTS,
                "search": query, "include": "licenses",
            }, allow_redirects=False) as response:
                response.raise_for_status()
                payload = await response.json(content_type=None)
        if not isinstance(payload, dict) or payload.get("headers", {}).get("status") != "success":
            raise ValueError("Jamendo search returned an invalid response")
        results = []
        for item in payload.get("results", []):
            if not isinstance(item, dict) or not item.get("audiodownload_allowed"):
                continue
            url = str(item.get("audiodownload") or "")
            if not _trusted_download_url(url):
                continue
            results.append(Track(
                title=str(item.get("name") or "Без названия")[:150],
                artist=str(item.get("artist_name") or "Неизвестный исполнитель")[:150],
                download_url=url,
                duration=max(0, int(item.get("duration") or 0)),
                track_id=str(item.get("id") or ""),
            ))
        return results

    def offer(self, user_id: int, chat_id: int, tracks: list[Track]) -> InlineKeyboardMarkup:
        now = time.monotonic()
        self._sessions = {k: v for k, v in self._sessions.items() if v[0] > now}
        if len(self._sessions) > 4000:
            self._sessions.clear()
        buttons = []
        for track in tracks:
            token = secrets.token_urlsafe(12)
            self._sessions[token] = (now + 600, user_id, chat_id, track)
            buttons.append([InlineKeyboardButton(
                text=f"🎵 {track.artist} — {track.title}"[:64],
                callback_data="fm:" + token,
            )])
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    def take(self, token: str, user_id: int, chat_id: int) -> Track | None:
        record = self._sessions.get(token)
        if not record:
            return None
        expires, owner, chat, track = record
        if expires < time.monotonic():
            self._sessions.pop(token, None)
            return None
        if owner != user_id or chat != chat_id:
            return None
        return track

    async def download(self, track: Track) -> bytes:
        if not _trusted_download_url(track.download_url):
            raise ValueError("Untrusted music download URL")
        timeout = aiohttp.ClientTimeout(total=40, sock_read=15)
        async with self._semaphore:
            async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
                async with session.get(track.download_url, allow_redirects=False) as response:
                    response.raise_for_status()
                    if response.content_length is not None and response.content_length > MAX_BYTES:
                        raise ValueError("Audio exceeds download limit")
                    chunks = bytearray()
                    async for part in response.content.iter_chunked(65536):
                        if len(chunks) + len(part) > MAX_BYTES:
                            raise ValueError("Audio exceeds download limit")
                        chunks.extend(part)
                    if not chunks:
                        raise ValueError("Empty audio")
                    return bytes(chunks)


def register_music_handlers(app, client_id: str) -> MusicService:
    service = MusicService(client_id)
    if not service.client_id:
        LOG.info("FOSGEN_MUSIC_DISABLED missing_jamendo_client_id")
        return service

    @app.dp.message(Command("music", "song"))
    async def music_search(message: Message):
        if message.from_user is None or message.chat.type != "private":
            await message.answer("Музыкальный поиск пока доступен в личном чате с Фосгеном.")
            return
        query = re.sub(r"^/\\w+(?:@\\w+)?", "", message.text or "", count=1).strip()
        if not (2 <= len(query) <= 120):
            await message.answer("Напиши /music исполнитель название")
            return
        now = time.monotonic()
        user_id = message.from_user.id
        if now - service._last_search.get(user_id, -100) < 5:
            await message.answer("Подожди несколько секунд перед следующим поиском.")
            return
        service._last_search[user_id] = now
        try:
            tracks = await service.search(query)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            LOG.warning("FOSGEN_MUSIC_SEARCH_FAILED type=%s", type(exc).__name__)
            await message.answer("Поиск временно недоступен. Попробуй позже.")
            return
        if not tracks:
            await message.answer("Среди треков с разрешенным скачиванием ничего не найдено.")
            return
        await message.answer("Выбери композицию для получения аудиофайла:",
                             reply_markup=service.offer(user_id, message.chat.id, tracks))

    @app.dp.callback_query(F.data.startswith("fm:"))
    async def music_download(callback: CallbackQuery):
        if callback.from_user is None or callback.message is None:
            await callback.answer("Не удалось определить запрос.", show_alert=True)
            return
        track = service.take((callback.data or "")[3:], callback.from_user.id,
                             callback.message.chat.id)
        if track is None:
            await callback.answer("Кнопка устарела или принадлежит другому пользователю.", show_alert=True)
            return
        token = (callback.data or '')[3:]
        if token in service._active_downloads:
            await callback.answer('Этот трек уже загружается.')
            return
        service._active_downloads.add(token)
        await callback.answer("Готовлю аудиофайл...")
        try:
            payload = await service.download(track)
            await callback.message.answer_audio(
                audio=BufferedInputFile(payload, filename=f"jamendo-{track.track_id}.mp3"),
                title=track.title, performer=track.artist, duration=track.duration,
                caption="Источник: Jamendo · скачивание разрешено правообладателем",
            )
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            LOG.warning("FOSGEN_MUSIC_DELIVERY_FAILED type=%s", type(exc).__name__)
            await callback.message.answer("Не удалось получить аудиофайл. Попробуй другую композицию.")
        finally:
            service._active_downloads.discard(token)

    # Ensure music handlers precede legacy catch-all handlers.
    for callback_name in ("music_search",):
        handlers = app.dp.message.handlers
        idx = next(i for i, h in enumerate(handlers) if h.callback.__name__ == callback_name)
        handlers.insert(0, handlers.pop(idx))
    print("FOSGEN_MUSIC_READY provider=jamendo scope=private", flush=True)
    return service
