"""Optional Hunspell dictionary support for Lexicon.

The bot stays fully operational without Hunspell files. If ru_RU.aff/ru_RU.dic
are available in the image or mounted data directory, this module loads them and
uses the spell checker as an additional broad dictionary source.

Supported locations:
- /app/data/hunspell/ru_RU.aff + ru_RU.dic
- /app/data/ru_RU.aff + ru_RU.dic
- common Linux dictionary paths
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

LOGGER = logging.getLogger(__name__)
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))

try:
    from spylls.hunspell import Dictionary
except Exception:  # pragma: no cover - optional dependency until installed
    Dictionary = None

HUNSPELL_CANDIDATES = (
    DATA_DIR / "hunspell" / "ru_RU",
    DATA_DIR / "ru_RU",
    Path("/usr/share/hunspell/ru_RU"),
    Path("/usr/share/myspell/dicts/ru_RU"),
    Path("/usr/share/myspell/ru_RU"),
    Path("/usr/local/share/hunspell/ru_RU"),
)


def _load_hunspell_dictionary():
    if Dictionary is None:
        print("LEXICON_HUNSPELL_READY enabled=false reason=spylls_missing", flush=True)
        return None

    for base_path in HUNSPELL_CANDIDATES:
        aff_path = base_path.with_suffix(".aff")
        dic_path = base_path.with_suffix(".dic")
        if not aff_path.is_file() or not dic_path.is_file():
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
