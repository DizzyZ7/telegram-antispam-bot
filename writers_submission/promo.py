"""Build publication-ready ICФ preview posts for the owner's private inbox.

The publisher is deliberately NOT a channel publisher: the delivery worker
routes each approved revision to one configured Telegram user.
"""
from __future__ import annotations

import html
import re
import struct
import zlib
from urllib.parse import urlsplit

from .models import ModerationDeliveryContext


def _preview(value: object, limit: int) -> str:
    value = str(value or "").strip()
    if len(value) > limit:
        value = value[:limit - 1].rstrip() + "…"
    return html.escape(value, quote=True)


def _tag(value: object, fallback: str = "") -> str:
    raw = str(value or fallback).strip().replace("NC-", "NC")
    # Telegram hashtag may contain letters, digits, and underscore.
    normalized = "".join(ch for ch in raw if ch.isalnum() or ch == "_")[:65]
    return f"#{normalized}" if normalized else ""


def _link(url: str, label: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'


def render_owner_promo(context: ModerationDeliveryContext) -> str:
    """HTML suitable for an ICФ-style copyable Telegram post."""
    details = context.details or {}
    is_ficbook = details.get("form_version") == 2
    tags = [
        _tag("Ориджиналы" if str(context.work_type) == "Оридж" else details.get("fandom"), "Фанфики"),
        _tag(str(details.get("size_category", "")).capitalize()) if is_ficbook else "",
        _tag(context.genre),
        _tag(details.get("rating")) if is_ficbook else "",
        _tag("Завершен" if details.get("completion") == "завершен" else "ВПроцессе") if details.get("completion") else "",
    ]
    lines = [
        f"🎩 <b>{_preview(context.title, 250)}</b> 🎩",
        "",
        _preview(context.description, 1650),
    ]
    if details.get("notes"):
        lines += ["", f"<b>Примечания:</b>\n{_preview(details['notes'], 850)}"]
    hashtags = [tag for tag in tags if tag]
    if hashtags:
        lines += ["", " | ".join(hashtags)]
    if details.get("completion") == "завершен" and details.get("pages") and details.get("parts"):
        lines += ["", f"Объем: {int(details.get('size_words') or 0)} слов, {int(details['pages'])} страниц, {int(details['parts'])} частей"]
    if details.get("characters"):
        lines += ["", f"Основные персонажи / пейринг: {_preview(details['characters'], 350)}"]
    if context.external_url:
        lines += ["", "📖 " + _link(context.external_url, "Читать на Фикбуке")]
    for i, url in enumerate((details.get("extra_links") or [])[:5]):
        if not isinstance(url, str) or urlsplit(url).scheme != "https":
            continue
        domain = (urlsplit(url).hostname or "").lower()
        label = "ТГ-канал" if domain in {"t.me", "telegram.me"} else f"Дополнительная ссылка {i + 1}"
        lines.append(("💬 " if domain in {"t.me", "telegram.me"} else "🔗 ") + _link(url, label))
    result = "\n".join(lines)
    # Telegram text is capped at 4096 chars. Use a conservative limit even
    # when HTML escaped entities and astral codepoints are counted.
    if len(result.encode("utf-16-le")) // 2 > 3900:
        raise ValueError("ICФ post exceeds safe Telegram message size")
    return result


def _png_chunk(tag: bytes, content: bytes) -> bytes:
    body = tag + content
    return struct.pack(">I", len(content)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def render_palette_png(colors: list[str] | tuple[str, ...]) -> bytes:
    """Render the author's four RGB selections as a genuine PNG, no PIL needed."""
    if len(colors) != 4 or any(
        not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color)
        for color in colors
    ):
        raise ValueError("four RGB colors are required")
    width, height = 800, 320
    rgb = [bytes.fromhex(color[1:]) for color in colors]
    scanline = b"\x00" + b"".join(color * (width // 4) for color in rgb)
    pixels = scanline * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(pixels, 9))
        + _png_chunk(b"IEND", b"")
    )
