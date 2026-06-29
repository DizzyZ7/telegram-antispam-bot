"""Application entry point with scoped writers-chat moderation."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("WRITERS_CHAT_ID", "-1002619489118")

APP_DIR = Path(__file__).resolve().parent
BUNDLED_LEXICON_PATH = APP_DIR / "bundled_moderation_lexicon.json"
RUNTIME_DATA_DIR = Path(os.getenv("DATA_DIR", APP_DIR / "data"))
RUNTIME_LEXICON_PATH = RUNTIME_DATA_DIR / "moderation_lexicon.json"
IGNORED_WRITERS_TOPIC_IDS = frozenset({14637, 42817, 292358})
ASCII_LATIN_PATTERN = re.compile(r"[A-Za-z]")
CYRILLIC_PATTERN = re.compile(r"[А-Яа-яЁё]")

RUNTIME_RULE_OVERLAY: dict[str, dict[str, tuple[str, ...]]] = {
    "exact": {
        "obscene": ("\u0431\u0434\u044c",),
        "english_obscene": ("xj",),
    },
    "prefix": {
        "obscene": ("\u0445\u0439", "\u043f\u0437\u0434"),
        "latin_translit": ("pzd",),
    },
}

UNSAFE_MIXED_PREFIXES = frozenset({"\u043d\u0443", "\u043e\u0431\u043e\u0441", "\u043f\u0430\u0434\u043b"})


def sync_moderation_lexicon() -> None:
    if not BUNDLED_LEXICON_PATH.is_file():
        raise RuntimeError(f"Bundled moderation lexicon is missing: {BUNDLED_LEXICON_PATH}")

    RUNTIME_DATA_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BUNDLED_LEXICON_PATH, RUNTIME_LEXICON_PATH)
    print(
        "WRITERS_LEXICON_SYNCED "
        f"source={BUNDLED_LEXICON_PATH.name} target={RUNTIME_LEXICON_PATH} "
        f"bytes={RUNTIME_LEXICON_PATH.stat().st_size}",
        flush=True,
    )


def apply_runtime_rule_overlay() -> int:
    payload = json.loads(RUNTIME_LEXICON_PATH.read_text(encoding="utf-8"))
    rules = payload.setdefault("rules", {})
    added = 0

    for match_mode, categories in RUNTIME_RULE_OVERLAY.items():
        target_categories = rules.setdefault(match_mode, {})
        for category, terms in categories.items():
            target_terms = target_categories.setdefault(category, [])
            known_terms = set(target_terms)
            for term in terms:
                if term not in known_terms:
                    target_terms.append(term)
                    known_terms.add(term)
                    added += 1

    RUNTIME_LEXICON_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"WRITERS_LEXICON_OVERLAY_READY added={added}", flush=True)
    return added


def _is_pure_latin_rule(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(ASCII_LATIN_PATTERN.search(value))
        and not CYRILLIC_PATTERN.search(value)
    )


def sanitize_compiled_lexicon() -> tuple[int, int]:
    """Remove unsafe Cyrillic copies of Latin rules and ambiguous short prefixes."""
    import writers_moderation as moderation

    payload = json.loads(RUNTIME_LEXICON_PATH.read_text(encoding="utf-8"))
    latin_exact_collisions: set[str] = set()
    latin_prefix_collisions: set[str] = set()

    for match_mode, categories in payload.get("rules", {}).items():
        if match_mode not in {"exact", "prefix"} or not isinstance(categories, dict):
            continue
        for terms in categories.values():
            if not isinstance(terms, list):
                continue
            for raw_term in terms:
                if not _is_pure_latin_rule(raw_term):
                    continue
                mixed_term = moderation._normalize_mixed_token(raw_term)
                if not mixed_term:
                    continue
                if match_mode == "exact":
                    latin_exact_collisions.add(mixed_term)
                else:
                    latin_prefix_collisions.add(mixed_term)

    exact_mixed = {
        term: category
        for term, category in moderation.MODERATION_LEXICON.exact_mixed.items()
        if term not in latin_exact_collisions
    }
    prefixes_without_latin_collisions = tuple(
        item
        for item in moderation.MODERATION_LEXICON.prefix_mixed
        if item[0] not in latin_prefix_collisions
    )
    prefix_mixed = tuple(
        item
        for item in prefixes_without_latin_collisions
        if item[0] not in UNSAFE_MIXED_PREFIXES
    )
    removed_latin = (
        len(moderation.MODERATION_LEXICON.exact_mixed) - len(exact_mixed)
        + len(moderation.MODERATION_LEXICON.prefix_mixed) - len(prefixes_without_latin_collisions)
    )
    removed_ambiguous = len(prefixes_without_latin_collisions) - len(prefix_mixed)

    moderation.MODERATION_LEXICON = replace(
        moderation.MODERATION_LEXICON,
        exact_mixed=exact_mixed,
        prefix_mixed=prefix_mixed,
        rule_count=max(0, moderation.MODERATION_LEXICON.rule_count - removed_latin - removed_ambiguous),
    )
    print(
        "WRITERS_LEXICON_SAFETY_PATCH "
        f"removed_latin_collisions={removed_latin} "
        f"removed_ambiguous_prefixes={removed_ambiguous}",
        flush=True,
    )
    return removed_latin, removed_ambiguous


def install_ignored_topic_filter() -> None:
    import writers_moderation as moderation

    original_call = moderation.ProhibitedLanguageFilter.__call__

    async def scoped_call(instance: object, message: object) -> bool:
        if getattr(message, "message_thread_id", None) in IGNORED_WRITERS_TOPIC_IDS:
            return False
        return await original_call(instance, message)

    moderation.ProhibitedLanguageFilter.__call__ = scoped_call
    print(
        "WRITERS_TOPIC_EXCLUSIONS_READY "
        f"ids={','.join(str(value) for value in sorted(IGNORED_WRITERS_TOPIC_IDS))}",
        flush=True,
    )


sync_moderation_lexicon()
apply_runtime_rule_overlay()

import writers_moderation

sanitize_compiled_lexicon()
install_ignored_topic_filter()

import legacy_main as app
from accurate_stats import AccurateStatsService, AccurateStatsStorage, register_accurate_stats_handlers
from writers_moderation import MODERATION_LEXICON, register_writers_chat_handlers


async def main() -> None:
    accurate_storage = AccurateStatsStorage(RUNTIME_DATA_DIR / "accurate_stats.db")
    await accurate_storage.initialize()
    accurate_stats = AccurateStatsService(app, accurate_storage, app.SUMMARY_TIMEZONE)

    try:
        scope = register_writers_chat_handlers(app)
        register_accurate_stats_handlers(app, accurate_stats)
        await scope.resolve(app.bot)

        if scope.chat_id is not None and scope.chat_id not in app.ALLOWED_CHATS:
            app.ALLOWED_CHATS.append(scope.chat_id)

        print(
            "WRITERS_MODERATION_READY "
            f"chat_id={scope.chat_id} rules={MODERATION_LEXICON.rule_count}",
            flush=True,
        )
        await app.main()
    finally:
        await accurate_storage.close()


if __name__ == "__main__":
    asyncio.run(main())
