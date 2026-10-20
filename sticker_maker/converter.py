"""Resource-bounded GIF/Telegram animation -> Telegram video sticker conversion."""

from __future__ import annotations

import asyncio
import logging
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError

LOGGER = logging.getLogger(__name__)
MAX_SOURCE_BYTES = 12 * 1024 * 1024
MAX_STICKER_BYTES = 256 * 1024
MAX_STATIC_STICKER_BYTES = 512 * 1024


class StickerConversionError(Exception):
    """An unsupported input or a conversion that does not fit Telegram limits."""


async def convert_to_sticker(source: Path, output: Path, *, ffmpeg: str = "ffmpeg") -> bytes:
    """Encode at most three seconds; never launch a shell or read arbitrary URLs."""
    if not source.is_file() or source.stat().st_size > MAX_SOURCE_BYTES:
        raise StickerConversionError("GIF слишком большой. Отправь файл до 12 МБ.")

    loop = asyncio.get_running_loop()
    deadline = loop.time() + 45
    for fps, crf in ((24, 40), (18, 49), (12, 58)):
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-threads", "1", "-filter_threads", "1", "-i", str(source),
            "-t", "3", "-an", "-sn", "-dn",
            "-vf", (
                f"fps={fps},scale=512:512:force_original_aspect_ratio=decrease:flags=lanczos,"
                "pad=512:512:(ow-iw)/2:(oh-ih)/2:color=black@0,format=yuva420p"
            ),
            "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", str(crf),
            "-deadline", "realtime", "-cpu-used", "6", "-threads:v", "1",
            "-auto-alt-ref", "0", str(output),
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise StickerConversionError("На сервере не установлен FFmpeg.") from exc
        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(), timeout=max(1, min(20, deadline - loop.time()))
            )
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise StickerConversionError("Конвертация заняла слишком много времени.") from exc
        except asyncio.CancelledError:
            process.kill()
            await process.communicate()
            raise

        if process.returncode != 0:
            LOGGER.warning("STICKER_FFMPEG_FAILED details=%s", stderr[-400:].decode(errors="replace"))
            raise StickerConversionError("Не удалось прочитать GIF. Попробуй другую анимацию.")
        if output.is_file() and 0 < output.stat().st_size <= MAX_STICKER_BYTES:
            return output.read_bytes()
        if loop.time() >= deadline:
            break

    raise StickerConversionError(
        "Анимация слишком сложная для стикера 256 КБ. Выбери GIF покороче или попроще."
    )


async def convert_to_static_sticker(
    source: Path,
    output: Path,
    *,
    ffmpeg: str = "ffmpeg",
) -> tuple[str, bytes]:
    """Extract a static frame into PNG/WEBP with RGBA 512x512 and a transparent border.

    Telegram's /addsticker flow can request a static image, not a WebM file.
    Animated GIF/Telegram-MP4 sources share the same FFmpeg decoder.
    Does not attempt semantic background removal from opaque source videos.
    """
    if not source.is_file() or not 0 < source.stat().st_size <= MAX_SOURCE_BYTES:
        raise StickerConversionError("GIF слишком большая или недоступна для конвертации.")

    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-threads", "1", "-filter_threads", "1",
        "-i", str(source), "-frames:v", "1",
        "-vf", (
            "scale=480:480:force_original_aspect_ratio=decrease:flags=lanczos,"
            "format=rgba,pad=512:512:(ow-iw)/2:(oh-ih)/2:color=black@0,"
            "format=rgba"
        ),
        "-an", "-sn", "-dn", str(output),
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise StickerConversionError("На сервере не установлен FFmpeg.") from exc

    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise StickerConversionError("Не удалось извлечь кадр из GIF вовремя.") from exc
    except asyncio.CancelledError:
        process.kill()
        await process.communicate()
        raise

    if process.returncode != 0 or not output.is_file():
        LOGGER.warning(
            "STICKER_STATIC_FFMPEG_FAILED details=%s",
            stderr[-400:].decode(errors="replace"),
        )
        raise StickerConversionError("Не удалось извлечь изображение из GIF.")

    try:
        with Image.open(output) as image:
            rgba = image.convert("RGBA")
            if rgba.size != (512, 512):
                raise StickerConversionError("Некорректный размер кадра стикера.")
            png_buffer = BytesIO()
            rgba.save(png_buffer, format="PNG", optimize=True)
            png = png_buffer.getvalue()
            if len(png) <= MAX_STATIC_STICKER_BYTES:
                return "fosgen_sticker.png", png

            # Detailed GIF frames may exceed the 512 KiB static sticker limit.
            for quality in (90, 80, 65, 50):
                webp_buffer = BytesIO()
                rgba.save(webp_buffer, format="WEBP", quality=quality, method=4)
                webp = webp_buffer.getvalue()
                if len(webp) <= MAX_STATIC_STICKER_BYTES:
                    return "fosgen_sticker.webp", webp
    except (OSError, UnidentifiedImageError) as exc:
        raise StickerConversionError("Не удалось сформировать статичный стикер.") from exc

    raise StickerConversionError("Изображение слишком сложное для лимита 512 КБ.")
