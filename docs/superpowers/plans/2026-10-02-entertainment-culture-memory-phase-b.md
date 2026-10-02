# Entertainment Culture Memory Phase B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Culture Memory influence text generation through real chronological conversation runs, bounded historical windows, reply-weighted turn relations and chat-specific emoji style without adding autonomous media sending yet.

**Architecture:** Add a focused `culture.py` layer that turns canonical `MemoryEvent` sequences into bounded generation inputs and emoji-style hints. Extend the storage contract with deterministic contiguous historical-window sampling implemented equivalently in SQLite/PostgreSQL. Keep Generation v2 as the lexical renderer, but feed it weighted recent/historical sequence material plus current-context messages and apply emoji styling after novelty checks.

**Tech Stack:** Python 3.12, aiogram 3.x, SQLite/aiosqlite, PostgreSQL/asyncpg, existing local generator and Culture Memory models.

**Spec:** `docs/superpowers/specs/2026-10-02-entertainment-culture-memory-v1-design.md`

## Global Constraints

- Strict isolation by `chat_id + topic_id`.
- No full scan of the retained ~100k events during generation.
- Historical sampling is bounded, contiguous and deterministic for a supplied seed.
- Recent context remains stronger than historical culture.
- Same-user adjacent text can act as continuation; cross-user adjacency influences turn selection but must never raw-concatenate arbitrary tokens across authors.
- Explicit reply links outrank accidental adjacency.
- Exact/near source replay remains rejected by the existing novelty layer.
- Emoji style is learned only from the current chat/topic and adds at most 0–2 contextual emoji to text output.
- No new background worker, no GPU, no external AI, no autonomous sticker/photo/animation sending in Phase B.
- Existing autonomy budgets/frequency remain unchanged.

## Review Focus

1. Sparse/old topics: historical sampling must return fewer windows safely, never fail. Covered in Task 1 storage tests.
2. Same timestamp/message-id gaps: chronology must remain deterministic and preserve contiguous window order. Covered in Task 1.
3. Cross-user adjacency: turn association may rank a later phrase but must not merge users' raw token streams. Covered in Task 2.
4. Reply links to missing/pruned targets: gracefully fall back to adjacency without exception. Covered in Task 2.
5. Emoji-heavy chats: styling must avoid mandatory decoration and repeated emoji signatures. Covered in Task 3.

---

### Task 1: Bounded Historical Window Storage

**Files:**
- Modify: `entertainment/storage/base.py`
- Modify: `entertainment/storage/retention.py`
- Test: `tests/test_entertainment_culture_windows_sqlite.py`
- Test: `tests/test_entertainment_culture_windows_postgres.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: canonical `MemoryEvent` rows ordered by `(created_at, message_id, id)`.
- Produces: `sample_event_windows(chat_id: int, topic_id: int, *, window_count: int, window_size: int, seed: int) -> list[list[MemoryEvent]]`.

- [ ] **Step 1: Write failing SQLite tests** for deterministic sampling, contiguous ordering, topic isolation, and sparse history.
- [ ] **Step 2: Run SQLite tests; expect missing-method failures.**
- [ ] **Step 3: Add the storage protocol method and minimal SQLite implementation.** Select bounded anchor offsets without loading all rows; fetch each contiguous window with indexed scope/order queries; return chronological events.
- [ ] **Step 4: Run SQLite tests; expect PASS.**
- [ ] **Step 5: Write failing PostgreSQL parity tests.**
- [ ] **Step 6: Run PostgreSQL tests; expect missing-method failures.**
- [ ] **Step 7: Implement equivalent PostgreSQL sampling and include the new test module in the PostgreSQL CI job.**
- [ ] **Step 8: Run targeted PostgreSQL job/tests; expect PASS.**
- [ ] **Step 9: Commit `feat: add bounded Culture Memory historical windows`.**

### Task 2: Sequence/Culture Engine

**Files:**
- Create: `entertainment/culture.py`
- Test: `tests/test_entertainment_culture_engine.py`

**Interfaces:**
- Consumes: recent canonical events, sampled historical windows, optional current trigger text.
- Produces: `CultureGenerationContext` with `source_messages`, `context_messages`, `emoji_candidates`, and diagnostics; `build_culture_context(...)` must preserve author/turn boundaries.

- [ ] **Step 1: Write failing tests** for 8-minute conversation-run boundaries, same-user split-message continuation weighting, cross-user turn association without token fusion, explicit reply weighting, missing reply fallback, and recent-over-historical priority.
- [ ] **Step 2: Run tests; expect import/missing-symbol failures.**
- [ ] **Step 3: Implement typed culture context models and run builder.** Same-user adjacent text may be represented as an additional joined phrase source; cross-user turns remain separate entries with association weighting by duplication/selection weight, never direct token concatenation.
- [ ] **Step 4: Implement reply weighting and bounded historical integration.** Recent sources must dominate by deterministic weighting; historical windows only enrich the corpus.
- [ ] **Step 5: Run culture-engine tests; expect PASS.**
- [ ] **Step 6: Commit `feat: add chronological Culture sequence engine`.**

### Task 3: Chat-Specific Emoji Style

**Files:**
- Modify: `entertainment/culture.py`
- Test: `tests/test_entertainment_emoji_culture.py`

**Interfaces:**
- Consumes: emoji from text/emoji/sticker events plus current context terms and recent bot action metadata.
- Produces: `apply_emoji_style(text: str, context: CultureGenerationContext, *, recent_signatures: set[str], rng: random.Random) -> tuple[str, str | None]`.

- [ ] **Step 1: Write failing tests** for local contextual emoji preference, 0–2 emoji cap, no mandatory emoji, no cross-topic source, and recent-signature suppression.
- [ ] **Step 2: Run tests; expect missing-function failures.**
- [ ] **Step 3: Implement bounded emoji extraction/scoring from the current culture context.** Avoid a global dictionary; sticker emoji may contribute weakly.
- [ ] **Step 4: Implement probabilistic styling and signature output.** Keep deterministic behavior under seeded RNG; never alter an empty generation result.
- [ ] **Step 5: Run emoji tests; expect PASS.**
- [ ] **Step 6: Commit `feat: add chat-specific emoji culture styling`.**

### Task 4: Runtime Integration With Existing Generator

**Files:**
- Modify: `entertainment/service.py`
- Test: `tests/test_entertainment_culture_generation_service.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `recent_events`, `sample_event_windows`, `build_culture_context`, existing `generate_chat_text` and novelty guard.
- Produces: autonomous/manual text generation that uses Culture sequence context while preserving current action budgets and text action types.

- [ ] **Step 1: Write failing service tests** proving recent + historical Culture inputs are requested only after action selection, direct trigger text enters `context_messages`, no full-memory API is called, and autonomy decision/budget calls are unchanged.
- [ ] **Step 2: Run tests; expect old flat-text behavior to fail assertions.**
- [ ] **Step 3: Add `_culture_generation_context(...)` and route `evaluate_topic()` / `generate_now()` through it.** Use recent canonical events (bounded by generation sample) plus 4 historical windows × 16 events as initial internal defaults.
- [ ] **Step 4: Pass culture `source_messages` to `generate_chat_text(..., context_messages=...)`, retain novelty/no-copy, then apply emoji style.** Store optional `emoji_signature` and source-size diagnostics in action metadata without raw corpus text.
- [ ] **Step 5: Run targeted service/culture tests; expect PASS.**
- [ ] **Step 6: Update README with Phase B behavior and explicit statement that media is remembered but not autonomously resent until Phase C.**
- [ ] **Step 7: Run full suite and PostgreSQL integration; compare failures to the known baseline.**
- [ ] **Step 8: Commit `feat: integrate Culture Memory sequence generation`.**

### Task 5: Final Verification and PR

**Files:**
- No product changes unless verification exposes a Phase B regression.

**Interfaces:**
- Consumes: all Phase B tasks.
- Produces: reviewable feature branch/PR with evidence.

- [ ] **Step 1: Run `python -m compileall -q .`. Expected: PASS.**
- [ ] **Step 2: Run `python -m unittest discover -s tests -p "test_*.py"`. Expected: no new Phase B/Entertainment failures; report any pre-existing baseline failures explicitly.**
- [ ] **Step 3: Run PostgreSQL 17 CI/storage tests. Expected: SUCCESS.**
- [ ] **Step 4: Compare branch to `main`; verify no unrelated Lexicon/moderation product files changed.**
- [ ] **Step 5: Open a draft PR against `main` with exact verification evidence. Do not merge without explicit user authorization.**
