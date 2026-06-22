"""Loads the curated writers-chat moderation lexicon at process startup."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
DATASET_PATH = Path(__file__).parent / "data" / "writers_moderation_lexicon.json"
EXPECTED_SCHEMA_VERSION = 1


def _split_terms(raw_value: str) -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in re.split(r"[,;\n]", raw_value)
        if item.strip()
    )


def load_curated_terms(path: Path = DATASET_PATH) -> tuple[str, ...]:
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        LOGGER.warning("Moderation dataset is missing: %s", path)
        return ()
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.error("Could not load moderation dataset %s: %s", path, exc)
        return ()

    if not isinstance(payload, dict):
        LOGGER.error("Moderation dataset must be a JSON object: %s", path)
        return ()
    if payload.get("schema_version") != EXPECTED_SCHEMA_VERSION:
        LOGGER.error("Unsupported moderation dataset schema in %s", path)
        return ()

    raw_terms = payload.get("block_terms", [])
    if not isinstance(raw_terms, list):
        LOGGER.error("block_terms must be a JSON list in %s", path)
        return ()

    terms: list[str] = []
    seen: set[str] = set()
    for term in raw_terms:
        if not isinstance(term, str):
            continue
        normalized = term.strip()
        if len(normalized) < 3 or len(normalized) > 80:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        terms.append(normalized)

    return tuple(terms)


def install_curated_terms() -> int:
    manual_terms = _split_terms(os.getenv("WRITERS_EXTRA_BLOCKED_TERMS", ""))
    curated_terms = load_curated_terms()

    merged_terms: list[str] = []
    seen: set[str] = set()
    for term in (*curated_terms, *manual_terms):
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        merged_terms.append(term)

    if merged_terms:
        os.environ["WRITERS_EXTRA_BLOCKED_TERMS"] = ",".join(merged_terms)
    elif "WRITERS_EXTRA_BLOCKED_TERMS" in os.environ:
        os.environ.pop("WRITERS_EXTRA_BLOCKED_TERMS")

    LOGGER.info(
        "Writers moderation lexicon loaded: curated=%s, manual=%s, effective=%s",
        len(curated_terms),
        len(manual_terms),
        len(merged_terms),
    )
    return len(merged_terms)
