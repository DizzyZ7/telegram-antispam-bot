"""Optional Hunspell dictionary support for Lexicon.

The bot stays fully operational without Hunspell files. If ru_RU.aff/ru_RU.dic
are available in the image or mounted data directory, this module loads them and
uses the spell checker as an additional broad dictionary source.

If the dictionary is missing, the bot tries to download LibreOffice's Russian
Hunspell files once into DATA_DIR/hunspell. DATA_DIR is persistent on BotHost,
so the files survive redeploys when the volume is preserved.
"""

from __future__ import annotations

import logging
import os
import urllib.request
from functools import lru_cache
from pathlib import Path

LOGGER = logging.getLogger(__name__)
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
HUNSPELL_DATA_DIR = DATA_DIR / "hunspell"
DOWNLOAD_TIMEOUT_SECONDS = 20
RU_AFF_URL = "https://raw.githubusercontent.com/LibreOffice/dictionaries/master/ru_RU/ru_RU.aff"
RU_DIC_URL = "https://raw.githubusercontent.com/LibreOffice/dictionaries/master/ru_RU/ru_RU.dic"

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
        # Keep partial/corrupted downloads from being used on the next startup.
        for path in (base_path.with_suffix(".aff"), base_path.with_suffix(".dic")):
            try:
                if path.is_file() and path.stat().st_size < 1024:
                    path.unlink()
            except Exception:
                LOGGER.info("Could not clean partial Hunspell file %s", path, exc_info=True)


def _load_hunspell_dictionary():
    if Dictionary is None:
        print("LEXICON_HUNSPELL_READY enabled=false reason=spylls_missing", flush=True)
        return None

    _ensure_downloaded_dictionary()

    for base_path in HUNSPELL_CANDIDATES:
        if not _has_dictionary_files(base_path):
            continue
        try:
            dictionary = Dictionary.from_files(str(base_path))
            print(f"LEXICON_HUNSPELL_READY enabled=true path={base_path}", flush=True)
            return dictionary
        except Exception:
            LOGGER.exception("Could not load Hunspell dictionary from %s", base_path)

    print("LEXICON_HUNSPELL_READY enabled=false reason=ru_RU_files_not_found", flush=True)
    return None


HUNSPELL_DICTIONARY = _load_hunspell_dictionary()


@lru_cache(maxsize=150_000)
def hunspell_knows(word: str) -> bool:
    if HUNSPELL_DICTIONARY is None:
        return False
    try:
        return bool(HUNSPELL_DICTIONARY.lookup(word))
    except Exception:
        LOGGER.exception("Hunspell lookup failed for word=%s", word)
        return False
