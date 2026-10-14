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

from .models import ModerationDeliveryContext, ValidationError
from .uploads import normalize_extra_link


def quote_work_title(value: object) -> str:
    """One pair of Russian guillemets for the actual title.

    Users may omit quotes or type common straight/curly quotation marks.
    Strip only complete outer pairs; preserve punctuation inside the title.
    """
    title = " ".join(str(value or "").split()).strip()
    pairs = {("«", "»"), ('"', '"'), ("“", "”"), ("„", "“"), ("‘", "’"), ("'", "'")}
    while len(title) >= 2 and (title[0], title[-1]) in pairs:
        title = title[1:-1].strip()
    return f"«{title or 'Название'}»"


def _visible_warnings(raw: object) -> list[str]:
    """Deduplicate warnings before HTML escaping and spoiler rendering."""
    result: list[str] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, (list, tuple)) else ():
        value = " ".join(str(item or "").split())
        if not value or value.casefold() in seen:
            continue
        result.append(value)
        seen.add(value.casefold())
        if len(result) >= 10:
            break
    return result


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
    is_ficbook = details.get("form_version") in (2, 3)
    is_sketch = details.get("form_version") == 3 and context.work_type == "Зарисовка"
    tags = [
        _tag("Ориджиналы" if str(context.work_type) == "Оридж" else details.get("fandom"), "Фанфики"),
        _tag(str(details.get("size_category", "")).capitalize()) if is_ficbook else "",
        _tag(context.genre),
        _tag(details.get("rating")) if is_ficbook else "",
        _tag("Завершен" if details.get("completion") == "завершен" else "ВПроцессе") if details.get("completion") else "",
    ]
    if is_sketch:
        tags = [
            _tag("Ориджинал" if details.get("sketch_origin") == "Ориджинал" else "Фандом"),
            _tag(details.get("fandom")) if details.get("sketch_origin") == "Фандом" else "",
            _tag(details.get("rating")),
            _tag(context.genre),
        ]
    lines = [
        f"🎩 <b>{_preview(quote_work_title(context.title), 250)}</b> 🎩",
        "",
        _preview(context.description, 1650),
    ]
    if details.get("form_version") == 3:
        genres = details.get("main_genres") or []
        if genres:
            lines.extend(("", "• " + " • ".join(_preview(g, 70) for g in genres[:3]) + " •"))
        if details.get("warnings_present") == "yes":
            warnings = _visible_warnings(details.get("warnings"))
            if warnings:
                lines.extend((
                    "", "🚨 <b>ПРЕДУПРЕЖДЕНИЯ (18+)</b>",
                    "<tg-spoiler>" + " × ".join(_preview(w, 85) for w in warnings) + "</tg-spoiler>",
                ))
        if is_sketch:
            lines.extend(("", "🎨 Обложка для рубрики — от команды ИКФ."))
    if not is_sketch and details.get("notes"):
        lines += ["", f"<b>Примечания:</b>\n{_preview(details['notes'], 850)}"]
    hashtags = [tag for tag in tags if tag]
    if hashtags:
        lines += ["", " | ".join(hashtags)]
    if not is_sketch and details.get("completion") == "завершен" and details.get("pages") and details.get("parts"):
        lines += ["", f"Объем: {int(details.get('size_words') or 0)} слов, {int(details['pages'])} страниц, {int(details['parts'])} частей"]
    if not is_sketch and details.get("characters"):
        lines += ["", f"Основные персонажи / пейринг: {_preview(details['characters'], 350)}"]
    if is_sketch:
        lines += ["", "👤 " + _link(details["sketch_profile_url"], "Профиль автора на Фикбуке")]
        lines += ["📖 " + _link(details["sketch_article_url"], "Читать зарисовку в Telegraph")]
    elif context.external_url:
        lines += ["", "📖 " + _link(context.external_url, "Читать на Фикбуке")]
    for i, raw in enumerate((details.get("extra_links") or [])[:5] if not is_sketch else []):
        # The sealed revision may still store Telegram's shorthand as typed
        # because submit validation does not rewrite the author's draft JSON.
        try:
            url = normalize_extra_link(raw)
        except ValidationError:
            continue
        if not url:
            continue
        domain = (urlsplit(url).hostname or "").lower()
        is_telegram = domain in {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}
        label = "ТГ-канал" if is_telegram else f"Дополнительная ссылка {i + 1}"
        lines.append(("💬 " if is_telegram else "🔗 ") + _link(url, label))
    result = "\n".join(lines)
    # Telegram text is capped at 4096 chars. Use a conservative limit even
    # when HTML escaped entities and astral codepoints are counted.
    if len(result.encode("utf-16-le")) // 2 > 3900:
        raise ValueError("ICФ post exceeds safe Telegram message size")
    return result


# This is a SINGLE rough color mockup for the human artist, not a finished
# cover design. Previously selected templates are intentionally ignored.
COVER_SIZE = (1200, 450)


def _foreground_color(hex_color: str) -> str:
    rgb = [int(hex_color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    luminance = sum(
        weight * (part / 12.92 if part <= 0.04045 else ((part + 0.055) / 1.055) ** 2.4)
        for weight, part in zip((0.2126, 0.7152, 0.0722), rgb)
    )
    return "#161923" if luminance > 0.24 else "#F5F3ED"


def _title_lines(draw: object, title: str, font_path: str | None, *, max_width: int) -> tuple[list[str], object]:
    from PIL import ImageFont

    def chosen_font(size: int):
        # Pillow bundles a Unicode-capable default font. Bothost deployments
        # that run Python directly may not have Debian's DejaVu font package.
        return ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default(size=size)

    title = quote_work_title(" ".join(str(title or "Название").split())[:200])
    words = title.split()
    for size in range(66, 23, -2):
        font = chosen_font(size)
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = (current + " " + word).strip()
            if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        if len(lines) <= 2 and all(
            draw.textbbox((0, 0), line, font=font)[2] <= max_width for line in lines
        ):
            return lines, font
    # Keep a long unbroken word contained, even if font must be small.
    font = chosen_font(23)
    content = title
    while content and draw.textbbox((0, 0), content, font=font)[2] > max_width:
        content = content[:-2] + "…"
    return [content], font


def render_palette_fallback_png(
    colors: list[str] | tuple[str, ...],
) -> bytes:
    """Minimal, guaranteed four-color preview using only Python stdlib.

    This is intentionally a color board, not a finished branded cover. It
    stays usable even if Pillow / font packages are missing in the host.
    """
    if len(colors) != 4 or any(
        not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color)
        for color in colors
    ):
        raise ValueError("four RGB colors are required")
    swatches = [bytes.fromhex(color[1:]) for color in colors]
    width, height = COVER_SIZE
    raster = bytearray()
    for y in range(height):
        skew = (y - height // 2) // 5
        cuts = (width // 4 + skew, width // 2 + skew,
                (width * 3) // 4 + skew)
        sizes = (cuts[0], cuts[1] - cuts[0], cuts[2] - cuts[1],
                 width - cuts[2])
        raster.append(0)  # PNG scanline: no filter.
        for color, count in zip(swatches, sizes):
            raster.extend(color * count)

    def chunk(kind: bytes, content: bytes) -> bytes:
        return (
            struct.pack(">I", len(content)) + kind + content
            + struct.pack(">I", zlib.crc32(kind + content) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raster, 6))
        + chunk(b"IEND", b"")
    )


def render_palette_png(
    colors: list[str] | tuple[str, ...],
    title: str = "",
) -> bytes:
    """Make a single approximate ICФ color mockup, never a final artist cover.

    The four selected RGB colors remain visible together; the lettering is
    only a placeholder in the lower-right, subject to later artist edits.
    """
    if len(colors) != 4 or any(
        not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color)
        for color in colors
    ):
        raise ValueError("four RGB colors are required")
    offset, ribbon, slope = 390, 0, 0

    from io import BytesIO
    from pathlib import Path
    from PIL import Image, ImageDraw

    first, second, third, fourth = colors
    width, height = COVER_SIZE
    image = Image.new("RGB", COVER_SIZE, first)
    draw = ImageDraw.Draw(image)
    # Large clean diagonals, color #1 background + three other colors.
    draw.polygon(
        [(0, 40), (offset + 30, 335 + slope), (0, height)], fill=second
    )
    draw.polygon(
        [(0, height), (0, 320 + slope), (offset + 20, 294 + slope),
         (width, 130 + ribbon), (width, height)], fill=third
    )
    draw.polygon(
        [(0, height), (0, height - 35 - ribbon // 4),
         (offset + 10, 335 + slope), (offset + 75, 347 + slope),
         (width, 237 + ribbon // 2), (width, height)], fill=fourth
    )
    line_color = _foreground_color(first)
    for idx in range(3):
        delta = idx * 7
        draw.line(
            [(offset + 5 + delta, 333 + slope + delta),
             (width, 88 + ribbon + delta)],
            fill=line_color, width=3,
        )

    font_path = next(
        (str(p) for p in (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSerifCondensed-Italic.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf"),
        ) if p.is_file()),
        None,
    )
    # Use Pillow's bundled font when system DejaVu is unavailable.

    max_width = 770
    lines, font = _title_lines(draw, title, font_path, max_width=max_width)
    text_color = _foreground_color(fourth)
    line_height = font.size + 9
    y = height - 20 - len(lines) * line_height
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_width = bbox[2] - bbox[0]
        draw.text(
            (width - 36 - text_width, y - bbox[1]),
            line,
            font=font,
            fill=text_color,
        )
        y += line_height

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def render_custom_cover_png(photo_bytes: bytes, title: str) -> bytes:
    """Publish a verified paid photo as a wide IKF banner.

    The user's art is center-cropped; we add only an unobtrusive dark title
    ribbon. The title stays at the same lower-right anchor as free templates.
    """
    from io import BytesIO
    from pathlib import Path
    from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

    try:
        with Image.open(BytesIO(photo_bytes)) as original:
            if original.format not in {"PNG", "JPEG"}:
                raise ValueError("Custom cover must be PNG or JPEG")
            if original.width * original.height > 48_000_000:
                raise ValueError("Custom cover resolution is too large")
            image = ImageOps.fit(original.convert("RGB"), COVER_SIZE)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Custom cover is not a supported image") from exc

    overlay = Image.new("RGBA", COVER_SIZE, (0, 0, 0, 0))
    paint = ImageDraw.Draw(overlay)
    paint.polygon(
        [(200, 450), (420, 323), (1200, 278), (1200, 450)],
        fill=(15, 21, 34, 195),
    )
    for index in range(3):
        off = index * 7
        paint.line([(418 + off, 329 + off), (1200, 215 + off)],
                   fill=(241, 237, 231, 185), width=3)
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(image)
    font_path = next(
        (str(p) for p in (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSerifCondensed-Italic.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf"),
        ) if p.is_file()),
        None,
    )
    if font_path is None:
        raise RuntimeError("ICФ Cyrillic serif font is missing from the Docker image")
    lines, font = _title_lines(draw, title, font_path, max_width=770)
    line_height = font.size + 9
    y = 450 - 20 - len(lines) * line_height
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        draw.text((1200 - 36 - (bbox[2] - bbox[0]), y - bbox[1]),
                  line, font=font, fill="#F5F3ED")
        y += line_height
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
