from __future__ import annotations

import codecs
import hashlib
import re
from uuid import UUID
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .models import ValidationError

TITLE_MAX = 160
WORK_TYPE_MAX = 80
GENRE_MAX = 120
DESCRIPTION_MAX = 2000
LONG_DESCRIPTION_WORDS_MAX = 110
SKETCH_DESCRIPTION_WORDS_MAX = 35
SKETCH_DESCRIPTION_CHARS_MAX = 230
MAIN_GENRES_LIMIT = 3
WARNINGS_LIMIT = 10
BODY_MAX = 200_000
EXTERNAL_URL_MAX = 2048

_DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
_ALLOWED_MIME = {
    "pdf": {"application/pdf"},
    "docx": {_DOCX_MIME},
    "txt": {"text/plain"},
    "png": {"image/png"},
    "jpeg": {"image/jpeg"},
}
_MAX_DOCX_ENTRIES = 256
_MAX_DOCX_NAME_BYTES = 1024 * 1024
_CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True, slots=True)
class NormalizedSubmissionFields:
    title: str
    work_type: str
    genre: str
    description: str
    body_text: str
    external_url: str | None
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    safe_filename: str
    file_class: str
    declared_mime: str
    byte_size: int
    sha256: str


def _compact_text(value: object, *, field: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be text")
    normalized = " ".join(value.split())
    if not normalized:
        raise ValidationError(f"{field} is required")
    if len(normalized) > max_length:
        raise ValidationError(f"{field} exceeds maximum length")
    return normalized


def _body_text(value: object) -> str:
    if not isinstance(value, str):
        raise ValidationError("body_text must be text")
    normalized = value.strip()
    if len(normalized) > BODY_MAX:
        raise ValidationError("body_text exceeds maximum length")
    return normalized


def _draft_external_url(value: object) -> str | None:
    """Store unfinished URL text in a draft, never treat it as trusted link."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("external_url must be text")
    normalized = value.strip()
    if len(normalized) > EXTERNAL_URL_MAX:
        raise ValidationError("external_url exceeds maximum length")
    return normalized or None


def _external_url(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("external_url must be text")
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > EXTERNAL_URL_MAX:
        raise ValidationError("external_url exceeds maximum length")
    parsed = urlparse(normalized)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValidationError("external_url must be an HTTPS URL without credentials")
    return normalized



_TELEGRAM_HOSTS = frozenset({
    "t.me", "www.t.me", "telegram.me", "www.telegram.me",
})
_TELEGRAM_BARE_LINK = re.compile(
    r"^(?:(?:www\.)?(?:t\.me|telegram\.me))/",
    re.IGNORECASE,
)
_TELEGRAM_USERNAME = re.compile(r"@[A-Za-z0-9_]{5,32}\Z")
_TELEGRAM_PATH = re.compile(r"/[A-Za-z0-9_+/-]+\Z")


def normalize_extra_link(value: object) -> str | None:
    """Support Telegram-native channel shorthand in optional link fields.

    Only Telegram addresses may omit HTTPS. Other websites retain the
    existing strict HTTPS-only rule. Return canonical clickable URLs.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("extra_links must contain text")
    raw = value.strip()
    if not raw:
        return None
    if len(raw) > EXTERNAL_URL_MAX:
        raise ValidationError("extra_links URL exceeds maximum length")
    if _TELEGRAM_USERNAME.fullmatch(raw):
        return "https://t.me/" + raw[1:]
    if raw.startswith("@"):
        raise ValidationError("Telegram username must be @ followed by 5–32 letters, digits or underscores")

    candidate = raw
    if _TELEGRAM_BARE_LINK.match(raw):
        candidate = "https://" + raw
    elif raw.lower().startswith("http://"):
        parsed_http = urlparse(raw)
        if (parsed_http.hostname or "").lower() in _TELEGRAM_HOSTS:
            candidate = "https://" + raw[len("http://"):]

    url = _external_url(candidate)
    if url is None:
        return None
    parsed = urlparse(url)
    if (parsed.hostname or "").lower() in _TELEGRAM_HOSTS:
        if (not _TELEGRAM_PATH.fullmatch(parsed.path) or parsed.netloc.lower() not in _TELEGRAM_HOSTS
                or parsed.fragment or any(ch.isspace() for ch in url)
                or "\\" in url):
            raise ValidationError("Telegram link must point to a valid channel, invite or message")
    if len(url) > EXTERNAL_URL_MAX:
        raise ValidationError("extra_links URL exceeds maximum length")
    return url


_DETAILS_ALLOWED = frozenset({
    "form_version", "fandom", "size_category", "rating", "completion",
    "size_words", "pages", "parts", "extra_links", "visual_mode",
    "palette_colors", "characters", "notes", "cover_template", "cover_file_id",
    "main_genres", "warnings_present", "warnings", "sketch_origin",
    "sketch_profile_url", "sketch_article_url",
})


def normalize_submission_details(value: object, *, complete: bool = False,
                                 work_type: str = "") -> dict[str, object]:
    """Validate and normalize the Ficbook form; old work revisions are untouched."""
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - _DETAILS_ALLOWED:
        raise ValidationError("details must be an object with supported fields")
    version = value.get("form_version")
    if version not in (2, 3):
        raise ValidationError("unsupported submission form version")

    # Ignore obsolete layout choices stored by older clients. Existing
    # revisions remain readable but newly saved drafts have only two modes.
    legacy_template = value.get("cover_template")
    if legacy_template not in (None, "", "classic", "ribbon", "contrast", "minimal"):
        raise ValidationError("cover_template is no longer supported")
    details: dict[str, object] = {"form_version": version}
    for name, limit in (("fandom", 160), ("rating", 40), ("characters", 600), ("notes", 1500)):
        raw = value.get(name, "")
        if not isinstance(raw, str) or len(raw) > limit:
            raise ValidationError(f"{name} is invalid")
        details[name] = " ".join(raw.split())

    for name, allowed in (
        ("size_category", {"мини", "миди", "макси"}),
        ("completion", {"завершен", "в процессе"}),
        ("visual_mode", {"palette", "image", "editorial"}),
    ):
        raw = value.get(name, "")
        if raw and (not isinstance(raw, str) or raw not in allowed):
            raise ValidationError(f"{name} is invalid")
        details[name] = raw

    for name in ("size_words", "pages", "parts"):
        raw = value.get(name)
        if raw in (None, ""):
            details[name] = None
        elif type(raw) is not int or raw <= 0 or raw > 10_000_000:
            raise ValidationError(f"{name} must be a positive integer")
        else:
            details[name] = raw

    raw_links = value.get("extra_links", [])
    if not isinstance(raw_links, list) or len(raw_links) > 5:
        raise ValidationError("extra_links must contain at most five links")
    links = []
    for raw in raw_links:
        if complete:
            url = normalize_extra_link(raw)
        else:
            # Autosave must work while someone is halfway through typing
            # "https://ficbook.net/..." or an optional external link.
            # Validate strictly when the author actually submits.
            if not isinstance(raw, str):
                raise ValidationError("extra_links must contain text")
            url = raw.strip()
            if len(url) > EXTERNAL_URL_MAX:
                raise ValidationError("extra_links URL exceeds maximum length")
        if url and url not in links:
            links.append(url)
    details["extra_links"] = links

    raw_colors = value.get("palette_colors", [])
    if not isinstance(raw_colors, list) or len(raw_colors) > 4:
        raise ValidationError("palette_colors must contain up to four colors")
    colors = []
    for color in raw_colors:
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise ValidationError("palette_colors must use #RRGGBB format")
        colors.append(color.upper())
    details["palette_colors"] = colors
    raw_cover_file_id = value.get("cover_file_id")
    if raw_cover_file_id in (None, ""):
        details["cover_file_id"] = None
    elif not isinstance(raw_cover_file_id, str):
        raise ValidationError("cover_file_id is invalid")
    else:
        try:
            details["cover_file_id"] = str(UUID(raw_cover_file_id))
        except (ValueError, AttributeError) as exc:
            raise ValidationError("cover_file_id is invalid") from exc

    if version == 3:
        genres = value.get("main_genres", [])
        if not isinstance(genres, list) or len(genres) > MAIN_GENRES_LIMIT:
            raise ValidationError("main_genres must have 1–3 entries")
        genre_items: list[str] = []
        for raw in genres:
            if not isinstance(raw, str) or len(raw) > 70:
                raise ValidationError("main_genres entry is invalid")
            item = " ".join(raw.split())
            if item and item.casefold() not in {x.casefold() for x in genre_items}:
                genre_items.append(item)
            elif item:
                raise ValidationError("main_genres must not repeat")
        details["main_genres"] = genre_items

        warnings_present = value.get("warnings_present", "")
        if warnings_present not in ("", "yes", "no"):
            raise ValidationError("warnings_present must be yes or no")
        details["warnings_present"] = warnings_present
        warnings = value.get("warnings", [])
        if not isinstance(warnings, list) or len(warnings) > WARNINGS_LIMIT:
            raise ValidationError("warnings must contain up to ten entries")
        warning_items: list[str] = []
        for raw in warnings:
            if not isinstance(raw, str) or len(raw) > 85:
                raise ValidationError("warning entry is invalid")
            item = " ".join(raw.split())
            if item and item.casefold() not in {x.casefold() for x in warning_items}:
                warning_items.append(item)
            elif item:
                raise ValidationError("warnings must not repeat")
        if warnings_present != "yes" and warning_items:
            raise ValidationError("warnings require yes selection")
        details["warnings"] = warning_items

        sketch_origin = value.get("sketch_origin", "")
        if sketch_origin not in ("", "Фандом", "Ориджинал"):
            raise ValidationError("sketch_origin is invalid")
        details["sketch_origin"] = sketch_origin
        for key in ("sketch_profile_url", "sketch_article_url"):
            raw = value.get(key, "")
            if not isinstance(raw, str) or len(raw) > EXTERNAL_URL_MAX:
                raise ValidationError(f"{key} is invalid")
            details[key] = raw.strip()
        if work_type == "Зарисовка" and complete:
            if not details["sketch_origin"]:
                raise ValidationError("sketch_origin is required")
            if details["sketch_origin"] == "Фандом" and not details["fandom"]:
                raise ValidationError("fandom is required for fandom sketches")
            for key, hostname in (
                ("sketch_profile_url", "ficbook.net"),
                ("sketch_article_url", "telegra.ph"),
            ):
                url = _external_url(details[key])
                if not url or (urlparse(url).hostname or "").lower() not in (hostname, "www." + hostname) or not urlparse(url).path.strip("/"):
                    raise ValidationError(f"{key} must be an HTTPS {hostname} link")
        if complete:
            if not genre_items:
                raise ValidationError("at least one main genre is required")
            if warnings_present not in ("yes", "no"):
                raise ValidationError("select yes or no for warnings")
            if warnings_present == "yes" and not warning_items:
                raise ValidationError("provide at least one content warning")

    if details["completion"] != "завершен":
        for name in ("size_words", "pages", "parts"):
            details[name] = None
    if details["visual_mode"] != "palette":
        details["palette_colors"] = []
    if details["visual_mode"] != "image":
        details["cover_file_id"] = None

    if complete:
        required = (
            ("rating", "visual_mode", "sketch_origin")
            if version == 3 and work_type == "Зарисовка"
            else ("size_category", "rating", "completion", "visual_mode")
        )
        for name in required:
            if not details[name]:
                raise ValidationError(f"{name} is required")
        if version == 3 and work_type == "Зарисовка" and details["visual_mode"] != "editorial":
            raise ValidationError("sketches use the editorial rubric cover")
        if version == 3 and work_type != "Зарисовка" and details["visual_mode"] == "editorial":
            raise ValidationError("editorial cover is only for sketches")
        if details["visual_mode"] == "palette" and len(details["palette_colors"]) != 4:
            raise ValidationError("four palette colors are required")
        if not (version == 3 and work_type == "Зарисовка") and details["completion"] == "завершен":
            if any(details[name] is None for name in ("size_words", "pages", "parts")):
                raise ValidationError("finished work requires size, pages and parts")
    return details


def validate_description_for_format(description: str, work_type: str) -> None:
    """Short sketches fit roughly three Telegram lines; long work is balanced."""
    text = " ".join(str(description or "").split()).strip()
    words = re.findall(r"[^\W_]+(?:[-’'][^\W_]+)*", text, re.UNICODE)
    if work_type == "Зарисовка":
        if len(words) > SKETCH_DESCRIPTION_WORDS_MAX or len(text) > SKETCH_DESCRIPTION_CHARS_MAX:
            raise ValidationError("sketch description: max 35 words and 230 characters")
    else:
        if len(words) > LONG_DESCRIPTION_WORDS_MAX:
            raise ValidationError("work description: max 110 words")
        # A sentence is a piece ending with terminal punctuation, not an
        # arbitrary three semicolons or line breaks.
        sentences = [s.strip() for s in re.findall(r"[^.!?]+[.!?]+", text) if s.strip()]
        if len(sentences) < 3 or not re.search(r"[.!?]$", text):
            raise ValidationError("work description needs at least three sentences")


def validate_submission_fields(
    *,
    title: object,
    work_type: object,
    genre: object,
    description: object,
    body_text: object,
    external_url: object,
    has_ready_file: bool,
    details: object = None,
    require_work_content: bool = True,
) -> NormalizedSubmissionFields:
    normalized_details = normalize_submission_details(details, work_type=str(work_type or ""))
    # v2 accepts partial drafts so authors can leave the Mini App and resume
    # even before completing the application. Submit enforces required fields.
    partial_draft = bool(normalized_details) and not require_work_content

    def text_field(value: object, name: str, max_length: int) -> str:
        if partial_draft and value == "":
            return ""
        return _compact_text(value, field=name, max_length=max_length)

    normalized = NormalizedSubmissionFields(
        title=text_field(title, "title", TITLE_MAX),
        work_type=text_field(work_type, "work_type", WORK_TYPE_MAX),
        genre=text_field(genre, "genre", GENRE_MAX),
        description=text_field(description, "description", DESCRIPTION_MAX),
        body_text=_body_text(body_text),
        external_url=(
            _draft_external_url(external_url)
            if partial_draft else _external_url(external_url)
        ),
        details=normalized_details,
    )
    if normalized_details.get("form_version") == 3 and bool(require_work_content):
        validate_description_for_format(normalized.description, normalized.work_type)
        normalize_submission_details(normalized_details, complete=True, work_type=normalized.work_type)
        if normalized.work_type not in {"ФФ", "Оридж", "Зарисовка"}:
            raise ValidationError("invalid work_type")
        if normalized.work_type == "Зарисовка":
            article_url = normalized.details.get("sketch_article_url")
            if normalized.external_url != article_url:
                raise ValidationError("sketch reading URL must match Telegraph article")

    if (
        bool(require_work_content)
        and not normalized.body_text
        and not bool(has_ready_file)
        and not normalized.external_url
    ):
        raise ValidationError(
            "submission payload requires an HTTPS link, body text or a ready file"
        )
    return normalized


def normalize_display_filename(name: str) -> str:
    if not isinstance(name, str):
        raise ValidationError("filename must be text")
    if "\x00" in name:
        raise ValidationError("filename contains NUL")
    normalized = unicodedata.normalize("NFKC", name).replace("\\", "/")
    basename = normalized.rsplit("/", 1)[-1].strip()
    if basename in {"", ".", ".."}:
        raise ValidationError("filename is invalid")
    basename = "".join(
        character if ord(character) >= 32 else "_"
        for character in basename
    ).strip()
    if not basename:
        raise ValidationError("filename is invalid")
    if len(basename) > 255:
        stem = Path(basename).stem[:200]
        suffix = Path(basename).suffix[:20]
        basename = (stem + suffix) or "file"
    return basename


def _normalized_mime(value: str) -> str:
    if not isinstance(value, str):
        raise ValidationError("declared MIME type is invalid")
    return value.split(";", 1)[0].strip().casefold()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_pdf(path: Path) -> None:
    with path.open("rb") as source:
        if source.read(5) != b"%PDF-":
            raise ValidationError("PDF signature is invalid")


def _validate_txt(path: Path) -> None:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    control_count = 0
    character_count = 0
    try:
        with path.open("rb") as source:
            while chunk := source.read(_CHUNK_SIZE):
                if b"\x00" in chunk:
                    raise ValidationError("TXT contains binary NUL bytes")
                text = decoder.decode(chunk)
                character_count += len(text)
                control_count += sum(
                    1
                    for character in text
                    if ord(character) < 32 and character not in "\n\r\t\f"
                )
            tail = decoder.decode(b"", final=True)
            character_count += len(tail)
            control_count += sum(
                1
                for character in tail
                if ord(character) < 32 and character not in "\n\r\t\f"
            )
    except UnicodeDecodeError as exc:
        raise ValidationError("TXT must be valid UTF-8 text") from exc
    if character_count and control_count / character_count > 0.01:
        raise ValidationError("TXT contains too many control characters")


def _validate_docx(path: Path) -> None:
    try:
        with zipfile.ZipFile(path, "r") as archive:
            entries = archive.infolist()
            if len(entries) > _MAX_DOCX_ENTRIES:
                raise ValidationError("DOCX contains too many entries")
            name_bytes = 0
            names: set[str] = set()
            for info in entries:
                name_bytes += len(info.filename.encode("utf-8", errors="replace"))
                if name_bytes > _MAX_DOCX_NAME_BYTES:
                    raise ValidationError("DOCX metadata is too large")
                name = info.filename.replace("\\", "/")
                if name.startswith("/") or ".." in name.split("/"):
                    raise ValidationError("DOCX contains unsafe entry paths")
                names.add(name)
            if "[Content_Types].xml" not in names:
                raise ValidationError("DOCX structure is invalid")
            if not any(name.startswith("word/") for name in names):
                raise ValidationError("DOCX structure is invalid")
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValidationError("DOCX container is invalid") from exc


def validate_staged_file(
    path: Path,
    *,
    filename: str,
    declared_mime: str,
    max_bytes: int,
) -> ValidatedUpload:
    path = Path(path)
    if int(max_bytes) <= 0:
        raise ValueError("max_bytes must be positive")
    if not path.is_file():
        raise ValidationError("staged file does not exist")

    byte_size = path.stat().st_size
    if byte_size <= 0:
        raise ValidationError("file is empty")
    if byte_size > int(max_bytes):
        raise ValidationError("file size exceeds maximum")

    safe_filename = normalize_display_filename(filename)
    suffix = Path(safe_filename).suffix.casefold()
    file_class = {
        ".pdf": "pdf",
        ".docx": "docx",
        ".txt": "txt",
        ".png": "png",
        ".jpg": "jpeg",
        ".jpeg": "jpeg",
    }.get(suffix)
    if file_class is None:
        raise ValidationError("unsupported file extension")

    mime = _normalized_mime(declared_mime)
    if mime not in _ALLOWED_MIME[file_class]:
        raise ValidationError("file extension and MIME type do not match")

    with path.open("rb") as source:
        signature = source.read(8)

    if signature.startswith(b"%PDF-"):
        detected_class = "pdf"
        _validate_pdf(path)
    elif signature.startswith(bytes.fromhex("89504e470d0a1a0a")):
        # Strict PNG magic and IHDR sanity check (no decoding untrusted image data).
        with path.open("rb") as image:
            header = image.read(24)
        if len(header) < 24 or header[12:16] != b"IHDR" or int.from_bytes(header[16:20], "big") <= 0 or int.from_bytes(header[20:24], "big") <= 0:
            raise ValidationError("PNG header is invalid")
        detected_class = "png"
    elif signature.startswith(bytes.fromhex("ffd8ff")):
        with path.open("rb") as image:
            image.seek(-2, 2)
            if image.read(2) != bytes.fromhex("ffd9"):
                raise ValidationError("JPEG ending is invalid")
        detected_class = "jpeg"
    elif signature.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        _validate_docx(path)
        detected_class = "docx"
    else:
        _validate_txt(path)
        detected_class = "txt"

    if detected_class != file_class:
        raise ValidationError(
            "file extension, MIME type and detected file class do not match"
        )

    return ValidatedUpload(
        safe_filename=safe_filename,
        file_class=detected_class,
        declared_mime=mime,
        byte_size=byte_size,
        sha256=_sha256(path),
    )
