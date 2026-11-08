# Chat Vocabulary Implementation Plan

> For agentic workers: use superpowers:executing-plans; test each new behavior RED→GREEN, one independent branch review at finish.

Goal: use ordinary accumulated conversation as vocabulary and prevent mechanical replay.

Architecture: keep existing age-scoped sources and constructor; normalize Russian word identity, extract bounded safe noun phrases from object positions, then combine with existing learned predicates.

Tech Stack: Python 3.12, pymorphy3, unittest, existing SQLite/PostgreSQL.

Spec: docs/superpowers/specs/2026-11-08-chat-vocabulary-design.md

## Constraints and review focus

- 86400-second incubation, source provenance, scope and forget behavior remain unchanged.
- No changes to dependency/runtime configuration or persistent schema.
- Do not turn people, name homonyms, verb homonyms or short predicatives into object characters.
- Noun phrases must retain number and agree after case conversion; ordinary grammatical input must not create a broken modifier.
- Normalizing е/ё must apply to all copy gates and distinct source counts.
- Repeated-predicate filtering must preserve negation and genuinely different objects.

## Task 1: Canonical novelty and repeated predicates

Files: entertainment/generation_v3.py, entertainment/novelty.py, entertainment/coherence.py; tests/test_entertainment_fragment_composer.py and tests/test_entertainment_coherence.py.

Interfaces: existing normalize_source_text, longest_contiguous_word_overlap and is_novel_generated_text gain е/ё equality. generated_text_defect rejects reliable duplicated coordinated predicates.

- [x] Add failing source/recent replay and normalized source identity tests, including repeated predicate morphology and allowed contrasts.
- [x] Observe RED; implement shared normalization semantics and a conservative duplicate check.
- [x] Run relevant novelty/v3/coherence/composer tests; commit.

## Task 2: Learn subjects from ordinary noun phrases

Files: entertainment/fragment_composer.py; tests/test_entertainment_chat_vocabulary.py.

Interfaces: private _vocabulary_subjects(source) -> list[_Subject], bounded to 12; a shared safe-clause iterator used by noun/predicate extraction. Existing compose_memory_text signature and result remain unchanged.

- [x] Add failing complete-output tests on ordinary/first-person/short-message corpora, literal case/number agreement, and unsafe/ambiguous exclusions.
- [x] Observe RED; implement nominal conversion without adding seed objects or weakening copy checks. Avoid equivalent predicate pairs.
- [x] Run vocabulary and complete existing composer/language/safety tests; commit.

## Task 3: Working-service proof and release

Files: tests/test_entertainment_accumulation.py, docs/entertainment-generation-v3.md.

- [x] Add failing exported-service test proving month-old ordinary messages still form a new result after reopening SQLite; current messages remain excluded.
- [x] Verify the behavior, document the bounded vocabulary and normalization.
- [x] Run full Python suite, client tests, compilation and diff check; request one independent read-only review.
- [ ] Fix Important findings with RED→GREEN tests, publish a PR, inspect every CI check, then integrate the verified head in main.
