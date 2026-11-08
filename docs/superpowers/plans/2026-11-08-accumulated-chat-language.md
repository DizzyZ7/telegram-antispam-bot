# Accumulated Chat Language Implementation Plan

> For agentic workers: use superpowers:executing-plans task by task; RED/GREEN tests precede production edits.

Goal: delayed, original, coherent local chat remixes from persisted older conversation.

Architecture: select aged Culture Memory in the existing scoped stores, compose learned grammatical fragments, then validate all production output against original/fresh messages and bot history. Preserve existing autonomy and special social handlers.

Tech Stack: Python 3.12, unittest, SQLite/PostgreSQL, pymorphy3.

Spec: docs/superpowers/specs/2026-11-08-accumulated-chat-language.md

## Global Constraints

- Incubation is 86400 seconds. No external dependencies or extra corpus persistence.
- Maximum source overlap is six contiguous words / 70%; fresh-source overlap must stay below four contiguous words.
- Human-only safe sources; scope, forget, presence, greetings and birthday quotas remain authoritative.

## Review Focus

- Newer traffic can hide all old events from recent snapshots: historical SQL must filter by age before sampling.
- Retention/restart/forget must not leave a shadow copy of creative sources.
- Morphology is ambiguous for Russian names, person/number and predicate complements: reject uncertain constructions.
- Tiny or hostile corpora must not bypass age, social safety or no-copy rules to avoid silence.
- A layered service can silently bypass a working helper: verify the exported production class with persisted events.

## Task 1: Incubated source selection

Files: entertainment/storage/windows.py, entertainment/storage/base.py, entertainment/culture.py, entertainment/culture_service.py, entertainment/moderation_safety_service.py; tests/test_entertainment_culture_windows_sqlite.py, tests/test_entertainment_culture_windows_postgres.py, tests/test_entertainment_accumulation.py.

Interfaces: sample_event_windows(..., created_before: int | None = None); build_culture_context(..., generation_before: int | None = None). CultureGenerationContext gains protected_messages and fresh_messages lists, defaulting empty for legacy pure callers.

- [ ] Write tests proving cutoff boundaries, older events surviving newer traffic, persistence after reopening, and human-only sources.
- [ ] Run those tests and inspect the expected missing-cutoff/age-selection failure.
- [ ] Implement cutoff queries and source/context separation in both backends and the production safety rebuild.
- [ ] Run task tests plus existing culture window/context/scope tests; commit the verified selection.

## Task 2: Learned fragment composition and validation

Files: new entertainment/fragment_composer.py; entertainment/coherence.py, entertainment/moderation_safety.py; tests/test_entertainment_fragment_composer.py, tests/test_entertainment_coherence.py, tests/test_entertainment_moderation_safety.py.

Interfaces: compose_memory_text(GenerationRequest, *, rng) -> GenerationResult | None; reject_replayed_memory(text, sources, recent_outputs, fresh_messages) -> bool. Composed results use existing v3 diagnostics without changing the engine selector.

- [ ] Write tests for learned predicates, gender/number agreement, multiple sources, no fixed news vocabulary, no human targets and no fresh echo.
- [ ] Observe RED against missing composition/validation behavior.
- [ ] Implement bounded extraction, source-distinct combinations and reliable outgoing checks.
- [ ] Run task tests and generation/language/grammar regression tests; commit.

## Task 3: Production integration and evidence

Files: entertainment/culture_service.py, docs/entertainment-generation-v3.md, tests/test_entertainment_accumulation.py and affected existing service fixtures.

Consumes task 1 context fields and task 2 composer. Produces delayed text through the exported service and aggregate-only source-count metadata.

- [ ] Add failing integration tests for a new reply from old persisted material after restart, fresh anti-echo and preserving good generated text.
- [ ] Call the learned composer before bounded v3 fallback; apply the same validation to both; retain existing curated social fallbacks when no acceptable text exists.
- [ ] Document behavior, cold start and rollout without logging user text.
- [ ] Run Python full discovery, JS client tests, compilation and diff checks; request one independent whole-branch review.
- [ ] Fix important review findings with RED/GREEN tests, commit and open a reviewable PR; inspect CI before reporting readiness.
