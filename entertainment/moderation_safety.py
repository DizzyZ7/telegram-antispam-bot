"""Stable moderation matcher for Entertainment input/output safety.

The application startup sanitizes mixed-script copies of pure-Latin moderation
rules. Entertainment is also imported directly by tests and tools, so it must
not depend on that startup side effect to avoid false positives such as the
Latin transliteration ``huy`` becoming the Cyrillic prefix ``ну``.
"""

from __future__ import annotations

import json
import re
from typing import Iterable

import writers_moderation as moderation

_UNSAFE_AMBIGUOUS_MIXED_PREFIXES = frozenset({"ну", "обос", "падл"})
_ASCII_LATIN_RE = re.compile(r"[A-Za-z]")
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")


def _pure_latin_rule(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(_ASCII_LATIN_RE.search(value))
        and not _CYRILLIC_RE.search(value)
    )


def _latin_mixed_collisions() -> tuple[set[str], set[str]]:
    exact: set[str] = set()
    prefix: set[str] = set()
    try:
        payload = json.loads(moderation.LEXICON_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return exact, prefix

    rules = payload.get("rules", {})
    if not isinstance(rules, dict):
        return exact, prefix
    for mode, categories in rules.items():
        if mode not in {"exact", "prefix"} or not isinstance(categories, dict):
            continue
        target = exact if mode == "exact" else prefix
        for terms in categories.values():
            if not isinstance(terms, list):
                continue
            for raw_term in terms:
                if not _pure_latin_rule(raw_term):
                    continue
                normalized = moderation._normalize_mixed_token(raw_term)
                if normalized:
                    target.add(normalized)
    return exact, prefix


_LATIN_EXACT_COLLISIONS, _LATIN_PREFIX_COLLISIONS = _latin_mixed_collisions()


def _sanitized_mixed_exact() -> dict[str, str]:
    return {
        token: category
        for token, category in moderation.MODERATION_LEXICON.exact_mixed.items()
        if token not in _LATIN_EXACT_COLLISIONS
    }


def _sanitized_mixed_prefixes() -> tuple[tuple[str, str], ...]:
    return tuple(
        (prefix, category)
        for prefix, category in moderation.MODERATION_LEXICON.prefix_mixed
        if prefix not in _LATIN_PREFIX_COLLISIONS
        and prefix not in _UNSAFE_AMBIGUOUS_MIXED_PREFIXES
    )


def _match(
    value: str,
    exact: dict[str, str],
    prefixes: Iterable[tuple[str, str]],
) -> str | None:
    category = exact.get(value)
    if category is not None:
        return category
    for prefix, category in prefixes:
        if value.startswith(prefix):
            return category
    return None


def _detect_token(raw_token: str) -> str | None:
    lexicon = moderation.MODERATION_LEXICON

    latin = moderation._normalize_latin_token(raw_token)
    if latin in moderation.EXTRA_BLOCKED_TOKENS:
        return "extra"
    if latin and latin not in lexicon.allow_latin:
        category = _match(latin, lexicon.exact_latin, lexicon.prefix_latin)
        if category is not None:
            return category

    mixed = moderation._normalize_mixed_token(raw_token)
    if mixed in moderation.EXTRA_BLOCKED_TOKENS:
        return "extra"
    if not mixed or mixed in lexicon.allow_mixed:
        return None
    return _match(mixed, _sanitized_mixed_exact(), _sanitized_mixed_prefixes())


def _detect_candidates(matches: Iterable[str]) -> str | None:
    for match in matches:
        compact = moderation._compact_candidate(match)
        if not compact:
            continue
        category = _detect_token(compact)
        if category is not None:
            return category
    return None


def detect_unsafe_entertainment_language(text: str) -> str | None:
    """Match the writers-chat moderation policy without startup-only collisions."""
    for raw_token in moderation.WORD_TOKEN_PATTERN.findall(str(text or "")):
        category = _detect_token(raw_token)
        if category is not None:
            return category

    category = _detect_candidates(
        moderation.SPACED_TOKEN_PATTERN.findall(str(text or ""))
    )
    if category is not None:
        return category
    return _detect_candidates(
        moderation.SYMBOL_OBFUSCATION_PATTERN.findall(str(text or ""))
    )


def is_safe_entertainment_text(text: str | None) -> bool:
    if not isinstance(text, str) or not text.strip():
        return True
    return detect_unsafe_entertainment_language(text) is None


__all__ = ["detect_unsafe_entertainment_language", "is_safe_entertainment_text"]
