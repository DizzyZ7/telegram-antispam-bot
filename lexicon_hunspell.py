"""Optional Hunspell dictionary support for Lexicon.

The bot stays fully operational without Hunspell files. If ru_RU.aff/ru_RU.dic
are available in the image or mounted data directory, this module loads them and
uses the spell checker as an additional broad dictionary source.

If the dictionary is missing, the bot tries to download LibreOffice's Russian
Hunspell files once into DATA_DIR/hunspell. DATA_DIR is persistent on BotHost,
so the files survive redeploys when the volume is preserved.

If spylls cannot parse the downloaded dictionary, this module falls back to a
plain word set parsed from ru_RU.dic. That is less morphologically powerful, but
safe and enough for many base-word checks.
"""

from __future__ import annotations

import logging
import os
import re
import urllib.request
from functools import lru_cache
from pathlib import Path

LOGGER = logging.getLogger(__name__)
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
HUNSPELL_DATA_DIR = DATA_DIR / "hunspell"
DOWNLOAD_TIMEOUT_SECONDS = 20
RU_AFF_URL = "https://raw.githubusercontent.com/LibreOffice/dictionaries/master/ru_RU/ru_RU.aff"
RU_DIC_URL = "https://raw.githubusercontent.com/LibreOffice/dictionaries/master/ru_RU/ru_RU.dic"
WORD_RE = re.compile(r"^[а-яе]{4,32}$")

try:
    from spylls.hunspell import Dictionary
except Exception:  # pragma: no cover - optional dependency until installed
    Dictionary = None

HUNSPELL_CANDIDATES = (
    HUNSPELL_DATA_DIR / "ru_RU",
    DATA_DIR / "ru_RU",
    Path("/usr/share/hunspell/ru_RU"),
    Path("/usr/share/myspell/dicts/ru_RU"),
    Path("/usr/share/myspell/ru_RU"),
    Path("/usr/local/share/hunspell/ru_RU"),
)


def _has_dictionary_files(base_path: Path) -> bool:
    return base_path.with_suffix(".aff").is_file() and base_path.with_suffix(".dic").is_file()


def _download_file(url: str, target_path: Path) -> None:
    temporary_path = target_path.with_suffix(target_path.suffix + ".tmp")
    with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
        data = response.read()
    if len(data) < 1024:
        raise RuntimeError(f"Downloaded Hunspell file looks too small: {url} bytes={len(data)}")
    temporary_path.write_bytes(data)
    temporary_path.replace(target_path)


def _ensure_downloaded_dictionary() -> None:
    base_path = HUNSPELL_DATA_DIR / "ru_RU"
    if _has_dictionary_files(base_path):
        return
    try:
        HUNSPELL_DATA_DIR.mkdir(parents=True, exist_ok=True)
        _download_file(RU_AFF_URL, base_path.with_suffix(".aff"))
        _download_file(RU_DIC_URL, base_path.with_suffix(".dic"))
        print(f"LEXICON_HUNSPELL_DOWNLOADED path={base_path}", flush=True)
    except Exception as exc:
        LOGGER.warning("Could not download Russian Hunspell dictionary: %s", exc, exc_info=True)
        for path in (base_path.with_suffix(".aff"), base_path.with_suffix(".dic")):
            try:
                if path.is_file() and path.stat().st_size < 1024:
                    path.unlink()
            except Exception:
                LOGGER.info("Could not clean partial Hunspell file %s", path, exc_info=True)


def _normalize_word(raw_word: str) -> str:
    return raw_word.strip().split("/", 1)[0].lower().replace("ё", "е")


def _load_plain_dic_words(dic_path: Path) -> set[str]:
    words: set[str] = set()
    try:
        lines = dic_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        LOGGER.exception("Could not read plain Hunspell dictionary %s", dic_path)
        return words

    for idx, line in enumerate(lines):
        if idx == 0 and line.strip().isdigit():
            continue
        word = _normalize_word(line)
        if WORD_RE.fullmatch(word):
            words.add(word)
    return words


def _load_plain_word_set() -> set[str]:
    for base_path in HUNSPELL_CANDIDATES:
        dic_path = base_path.with_suffix(".dic")
        if not dic_path.is_file():
            continue
        words = _load_plain_dic_words(dic_path)
        if words:
            print(f"LEXICON_HUNSPELL_WORDSET_READY enabled=true path={dic_path} words={len(words)}", flush=True)
            return words
    print("LEXICON_HUNSPELL_WORDSET_READY enabled=false reason=dic_not_found", flush=True)
    return set()


def _load_hunspell_dictionary():
    _ensure_downloaded_dictionary()

    if Dictionary is None:
        print("LEXICON_HUNSPELL_READY enabled=false reason=spylls_missing", flush=True)
        return None

    for base_path in HUNSPELL_CANDIDATES:
        if not _has_dictionary_files(base_path):
            continue
        try:
            dictionary = Dictionary.from_files(str(base_path))
            print(f"LEXICON_HUNSPELL_READY enabled=true path={base_path}", flush=True)
            return dictionary
        except Exception as exc:
            LOGGER.warning("Could not load Hunspell dictionary from %s: %s", base_path, exc, exc_info=True)

    print("LEXICON_HUNSPELL_READY enabled=false reason=spylls_load_failed", flush=True)
    return None


HUNSPELL_DICTIONARY = _load_hunspell_dictionary()
HUNSPELL_WORD_SET = _load_plain_word_set() if HUNSPELL_DICTIONARY is None else set()


@lru_cache(maxsize=150_000)
def hunspell_knows(word: str) -> bool:
    normalized = word.lower().replace("ё", "е")
    try:
        if HUNSPELL_DICTIONARY is not None:
            return bool(HUNSPELL_DICTIONARY.lookup(normalized))
        return normalized in HUNSPELL_WORD_SET
    except Exception:
        LOGGER.exception("Hunspell lookup failed for word=%s", word)
        return normalized in HUNSPELL_WORD_SET
