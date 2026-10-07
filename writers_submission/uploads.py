from __future__ import annotations

import codecs
import hashlib
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .models import ValidationError

TITLE_MAX = 160
WORK_TYPE_MAX = 80
GENRE_MAX = 120
DESCRIPTION_MAX = 2000
BODY_MAX = 200_000
EXTERNAL_URL_MAX = 2048

_DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
_ALLOWED_MIME = {
    "pdf": {"application/pdf"},
    "docx": {_DOCX_MIME},
    "txt": {"text/plain"},
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


def validate_submission_fields(
    *,
    title: object,
    work_type: object,
    genre: object,
    description: object,
    body_text: object,
    external_url: object,
    has_ready_file: bool,
    require_work_content: bool = True,
) -> NormalizedSubmissionFields:
    normalized = NormalizedSubmissionFields(
        title=_compact_text(title, field="title", max_length=TITLE_MAX),
        work_type=_compact_text(
            work_type,
            field="work_type",
            max_length=WORK_TYPE_MAX,
        ),
        genre=_compact_text(genre, field="genre", max_length=GENRE_MAX),
        description=_compact_text(
            description,
            field="description",
            max_length=DESCRIPTION_MAX,
        ),
        body_text=_body_text(body_text),
        external_url=_external_url(external_url),
    )
    if (
        bool(require_work_content)
        and not normalized.body_text
        and not bool(has_ready_file)
    ):
        raise ValidationError(
            "submission payload requires body_text or at least one ready file"
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
