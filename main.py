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

STRICT_RULE_OVERLAY: dict[str, dict[str, tuple[str, ...]]] = {
    "exact": {
        "obscene": (
            "\u0431\u0434\u044c", "\u0434\u0435\u0440\u044c\u043c\u043e", "\u0433\u043e\u0432\u043d\u043e", "\u0433\u043e\u0432\u043d\u0438\u0449\u0435", "\u0433\u043e\u0432\u043d\u044e\u043a",
            "\u0445\u0435\u0440\u043d\u044f", "\u043f\u0437\u0434\u0446", "\u043e\u0431\u043e\u0441\u0441\u0430\u043b", "\u043e\u0431\u043e\u0441\u0441\u0430\u043b\u0438", "\u043e\u0431\u043e\u0441\u0441\u0430\u043d", "\u043e\u0431\u043e\u0441\u0441\u0430\u0442\u044c",
        ),
        "severe_insult": (
            "\u043f\u0430\u0441\u043a\u0443\u0434\u0430", "\u043f\u0430\u0434\u043b\u0430",
        ),
        "english_obscene": (
            "cunt", "motherfucker", "slut", "whore", "bastard", "xj", "oboss",
        ),
    },
    "prefix": {
        "obscene": (
            "\u0445\u0439", "\u043f\u0437\u0434", "\u0430\u0445\u0443", "\u0445\u0435\u0440\u043d", "\u043f\u043e\u0445\u0435\u0440", "\u043d\u0430\u0445\u0435\u0440", "\u0433\u043e\u0432\u043d", "\u0433\u0430\u0432\u043d",
            "\u0434\u0435\u0440\u044c\u043c", "\u0441\u0440\u0430\u043d", "\u0441\u0441\u044b\u043a", "\u0434\u0440\u043e\u0447", "\u0448\u043b\u044e\u0445", "\u0448\u0430\u043b\u0430\u0432",
            "\u0448\u043c\u0430\u0440", "\u043f\u0430\u0441\u043a\u0443\u0434", "\u043f\u0430\u0434\u043b", "\u0434\u0440\u0438\u0441\u0442", "\u0437\u0430\u0441\u0440\u0430\u043d",
        ),
        "toxic_insult": (
            "\u0438\u043c\u0431\u0435\u0446\u0438\u043b", "\u0432\u044b\u0440\u043e\u0434",
        ),
        "rare_insult": (
            "\u0438\u043c\u0431\u0435\u0446\u0438\u043b\u043a", "\u0443\u0431\u043b\u044e\u0434\u0438\u0449", "\u0433\u0430\u0432\u043d\u044e\u043a", "\u0441\u0441\u044b\u043a\u0443\u043d", "\u0434\u0440\u043e\u0447\u0435\u0440", "\u0434\u0440\u043e\u0447\u0438\u043b",
        ),
        "latin_translit": (
            "pzd", "ahu", "ohu", "oher", "naher", "poher", "hernya", "govn", "gavno", "derm", "sran", "ssyk", "droch",
            "shlyuh", "shalav", "shmar", "paskud", "padla", "drist", "pzdts", "pzdc", "blya", "ueb", "razeb", "eblan", "ebanut", "ebuch",
        ),
    },
}

# Transliterated roots must not become short Cyrillic prefixes that collide with normal words.
UNSAFE_MIXED_PREFIXES = frozenset({"\u043d\u0443", "\u043e\u0431\u043e\u0441"})


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


def apply_strict_rule_overlay() -> None:
    payload = json.loads(RUNTIME_LEXICON_PATH.read_text(encoding="utf-8"))
    rules = payload.setdefault("rules", {})
    added = 0

    for match_mode, categories in STRICT_RULE_OVERLAY.items():
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


def remove_unsafe_mixed_prefixes() -> int:
    import writers_moderation as moderation

    filtered_prefixes = tuple(
        item
        for item in moderation.MODERATION_LEXICON.prefix_mixed
        if item[0] not in UNSAFE_MIXED_PREFIXES
    )
    removed = len(moderation.MODERATION_LEXICON.prefix_mixed) - len(filtered_prefixes)
    if removed:
        moderation.MODERATION_LEXICON = replace(
            moderation.MODERATION_LEXICON,
            prefix_mixed=filtered_prefixes,
            rule_count=max(0, moderation.MODERATION_LEXICON.rule_count - removed),
        )
    print(f"WRITERS_LEXICON_SAFETY_PATCH removed_mixed_prefixes={removed}", flush=True)
    return removed


def _is_pure_latin_rule(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(ASCII_LATIN_PATTERN.search(value))
        and not CYRILLIC_PATTERN.search(value)
    )


def remove_cross_script_rule_collisions() -> int:
    """Remove Cyrillic-normalized copies of rules that originated in Latin only."""
    import writers_moderation as moderation

    payload = json.loads(RUNTIME_LEXICON_PATH.read_text(encoding="utf-8"))
    exact_collisions: set[str] = set()
    prefix_collisions: set[str] = set()

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
                    exact_collisions.add(mixed_term)
                else:
                    prefix_collisions.add(mixed_term)

    exact_mixed = {
        term: category
        for term, category in moderation.MODERATION_LEXICON.exact_mixed.items()
        if term not in exact_collisions
    }
    prefix_mixed = tuple(
        item
        for item in moderation.MODERATION_LEXICON.prefix_mixed
        if item[0] not in prefix_collisions
    )
    removed = (
        len(moderation.MODERATION_LEXICON.exact_mixed) - len(exact_mixed)
        + len(moderation.MODERATION_LEXICON.prefix_mixed) - len(prefix_mixed)
    )

    if removed:
        moderation.MODERATION_LEXICON = replace(
            moderation.MODERATION_LEXICON,
            exact_mixed=exact_mixed,
            prefix_mixed=prefix_mixed,
            rule_count=max(0, moderation.MODERATION_LEXICON.rule_count - removed),
        )

    print(f"WRITERS_CROSS_SCRIPT_SAFETY_PATCH removed_rules={removed}", flush=True)
    return removed


def install_ignored_topic_filter() -> None:
    import writers_moderation as moderation

    original_call = moderation.ProhibitedLanguageFilter.__call__

    async def scoped_call(instance: object, message: object) -> bool:
        message_thread_id = getattr(message, "message_thread_id", None)
        if message_thread_id in IGNORED_WRITERS_TOPIC_IDS:
            return False
        return await original_call(instance, message)

    moderation.ProhibitedLanguageFilter.__call__ = scoped_call
    print(
        "WRITERS_TOPIC_EXCLUSIONS_READY "
        f"ids={','.join(str(value) for value in sorted(IGNORED_WRITERS_TOPIC_IDS))}",
        flush=True,
    )


sync_moderation_lexicon()
apply_strict_rule_overlay()
remove_unsafe_mixed_prefixes()
remove_cross_script_rule_collisions()
install_ignored_topic_filter()

import legacy_main as app
from writers_moderation import MODERATION_LEXICON, register_writers_chat_handlers


async def main() -> None:
    scope = register_writers_chat_handlers(app)
    await scope.resolve(app.bot)

    if scope.chat_id is not None and scope.chat_id not in app.ALLOWED_CHATS:
        app.ALLOWED_CHATS.append(scope.chat_id)

    print(
        "WRITERS_MODERATION_READY "
        f"chat_id={scope.chat_id} rules={MODERATION_LEXICON.rule_count}",
        flush=True,
    )

    await app.main()


if __name__ == "__main__":
    asyncio.run(main())
