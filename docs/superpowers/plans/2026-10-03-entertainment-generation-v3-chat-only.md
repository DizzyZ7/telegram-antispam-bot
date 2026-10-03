# Entertainment Generation v3 — Chat-Only Language Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current v2 phrase remix path with a bounded, chat-only v3 language engine that synthesizes new topic-relevant phrases without replaying source messages, while preserving existing Culture Memory Phase C, autonomy, media, privacy and blocked-topic behavior.

**Architecture:** Keep `entertainment/generation.py` as the compatibility facade and rollback selector. Add `entertainment/language.py` for bounded token/morphology analysis and TopicAnchor construction, `entertainment/generation_v3.py` for candidate synthesis/scoring/anti-copy, and `entertainment/evaluation.py` for deterministic offline v2↔v3 evaluation. `service.py` consumes only the bounded Culture snapshot and passes explicit autonomous/direct requests; `scoped_service.py` remains the hard denylist boundary.

**Tech Stack:** Python 3, `random.Random`, `pymorphy3`, aiogram service layer, unittest, SQLite/PostgreSQL existing storage.

**Spec:** `docs/superpowers/specs/2026-10-03-entertainment-generation-v3-chat-only-design.md`

## Global Constraints

- Generation learns only from allowed same-topic Telegram Culture Memory and current same-topic context.
- Never read stories, fairy tales, books, Ficbook/text corpora, Lexicon dictionaries, internet, neighboring topics or other chats.
- Hard-denied topic scopes `292358`, `14637`, `42817` must never reach Culture snapshot generation or v3.
- No external AI API, model download, GPU, vector DB, worker or schema migration.
- Use only the existing bounded Phase C snapshot; never scan the full ~100k topic history.
- `pymorphy3` is a soft reranker signal only; slang/English/usernames/commands remain valid.
- Bounded morphology cache target: 4096 token forms.
- Candidate target: 48 per request, accepted internal range 32–64.
- Longest contiguous overlap with a single source: max 6 words and max 70% of candidate words, whichever is stricter.
- Candidates >=6 words require support from >=2 distinct normalized source-text identities or a genuine supported bridge.
- Weighted duplicate source strings never count as independent sources.
- Preserve emoji styling after text validation; preserve media/autonomy/privacy behavior.
- Keep v2 as env-selectable rollback for one release; v3 becomes default only after acceptance.
- Do not log source text, trigger text, captions, file IDs, DSNs or credentials.

## Review Focus

- Tiny corpora and short meme exchanges must fail closed or synthesize safely rather than weaken anti-copy rules.
- Repeated weighted copies of one message must not satisfy multi-source composition.
- Direct-reply trigger duplicated in legacy `context_messages` must be counted once in TopicAnchor weighting.
- Mixed Russian/slang/English/commands must remain usable when pymorphy3 cannot confidently parse them.
- Blocked topics must short-circuit before snapshot reads and before any v3 invocation.

---

### Task 1: Linguistic analysis and TopicAnchor

**Files:**
- Create: `entertainment/language.py`
- Create: `tests/test_entertainment_generation_v3_language.py`

**Interfaces:**
- Produces `AnalyzedToken`, `TopicAnchor`, `analyze_token(text: str)`, `build_topic_anchor(context_messages: list[str], trigger_text: str | None, direct_reply: bool) -> TopicAnchor`.
- `TopicAnchor` exposes weighted lemma/surface terms and `relevance(tokens) -> float` without hard-rejecting unknown chat tokens.

- [ ] Write tests for Russian lemmas, unknown/slang fallback, cache bound, recent-context terms, direct-trigger highest weight and trigger de-duplication.
- [ ] Run the focused test file and verify RED because `entertainment.language` does not exist.
- [ ] Implement bounded token analysis using installed `pymorphy3` and an LRU cache capped at 4096 forms.
- [ ] Implement TopicAnchor construction from only the latest 40 context messages, de-duplicating exact trigger copies from legacy context when direct mode is active.
- [ ] Run focused tests and full unittest discovery.
- [ ] Commit `feat: add generation v3 language analysis`.

### Task 2: V3 synthesis core and anti-copy invariants

**Files:**
- Create: `entertainment/generation_v3.py`
- Create: `tests/test_entertainment_generation_v3_core.py`

**Interfaces:**
- Produces `GenerationMode`, `GenerationRequest`, `GenerationResult`, `GenerationV3`, `normalize_source_text(text: str)`, `longest_contiguous_word_overlap(candidate: str, source: str) -> int`.
- `GenerationV3.generate(request: GenerationRequest, *, rng: random.Random) -> GenerationResult | None`.

- [ ] Write RED tests for 5→1 backoff, deterministic fixed seed, exact replay rejection, overlap bound, repeated-3gram rejection, weighted duplicate source identity, genuine two-source composition and safe `None` fallback.
- [ ] Implement normalized distinct source identities and bounded 5/4/3/2/1 transition indexes plus 2–6-word phrase chunks.
- [ ] Implement TopicAnchor-weighted starts, longest-supported continuation, controlled punctuation/conjunction or surface/lemma bridges, sentence/token bounds and candidate target 48.
- [ ] Implement candidate rejection/scoring with topic relevance > historical popularity, direct trigger strongest, soft morphology penalties and recent-bot-output replay suppression.
- [ ] Run focused tests and full suite.
- [ ] Commit `feat: add chat-only generation v3 engine`.

### Task 3: Compatibility facade and rollback selector

**Files:**
- Modify: `entertainment/generation.py`
- Modify: `entertainment/config.py`
- Modify: `.env.example`
- Modify: `README.md`
- Create: `tests/test_entertainment_generation_v3_facade.py`

**Interfaces:**
- Preserve existing `generate_chat_text(...) -> str | None` unchanged for v2 callers/tests.
- Add `generate_text(request: GenerationRequest, *, rng: random.Random, engine: str | None = None) -> GenerationResult | None`.
- Add config `ENTERTAINMENT_GENERATION_ENGINE` accepting `v3` (default) or `v2`.

- [ ] Write RED tests proving default v3, explicit/env v2 rollback, invalid selector safe fallback and stable v2 compatibility.
- [ ] Implement selector without adding external dependencies or storage changes.
- [ ] Document engine rollback setting and chat-only source boundary.
- [ ] Run focused tests and full suite.
- [ ] Commit `feat: add generation engine selector`.

### Task 4: Culture service integration and blocked-topic proof

**Files:**
- Modify: `entertainment/service.py`
- Modify: `tests/test_entertainment_culture_generation_service.py`
- Modify/Create focused blocked-scope service tests as needed.

**Interfaces:**
- `_generate_culture_text(...)` builds `GenerationRequest` with explicit `AUTONOMOUS` or `DIRECT_REPLY`, passes recent outputs, then applies existing emoji style only after v3 accepts text.
- `evaluate_topic(...)` passes the exact direct trigger separately from context; `/fun_generate` uses autonomous mode.

- [ ] Write RED service tests for autonomous/direct mode, exact trigger propagation, trigger de-duplication, emoji-after-generation, diagnostics without raw text, and `/fun_generate` v3 autonomous mode.
- [ ] Add blocked-scope regression tests proving topics `292358`, `14637`, `42817` do not call Culture snapshot or generation facade.
- [ ] Implement service integration while preserving media selection, budgets, action cadence, privacy and topic isolation.
- [ ] Add safe metadata only: engine, mode, candidate_count, score bucket and aggregate rejection counts; never raw inputs.
- [ ] Run service-focused tests and full suite.
- [ ] Commit `feat: integrate generation v3 with culture service`.

### Task 5: Deterministic quality benchmark

**Files:**
- Create: `entertainment/evaluation.py`
- Create: `tests/test_entertainment_generation_v3_evaluation.py`

**Interfaces:**
- Produce deterministic metric helpers for replay rate, longest-source-overlap rate, topic-anchor overlap, direct-trigger relevance, supported n-gram ratio, diversity, no-output rate and latency summary.
- No private chat dumps in fixtures.

- [ ] Write RED regression fixtures covering travel/transport, `/spawn` culture, short memes, direct replies, `Капец, вот: 3.`-style fragments and unsupported transition analogues.
- [ ] Implement offline v2/v3 evaluator over synthetic/anonymized in-repo corpora and fixed seeds.
- [ ] Assert v3 is >= v2 on topical relevance/phrase support and does not increase exact/near replay for the fixture set.
- [ ] Report latency but only hard-fail on a severe regression threshold, not the ~250 ms target itself.
- [ ] Run evaluation tests and full suite.
- [ ] Commit `test: add generation v3 quality benchmark`.

### Task 6: Final verification and CI

**Files:**
- Modify docs only if verification exposes a documented mismatch.

**Interfaces:**
- No new product interfaces.

- [ ] Run `python -m compileall -q .`.
- [ ] Run full unittest discovery and record exact pass/skip/fail counts.
- [ ] Verify PostgreSQL 17 integration job remains green.
- [ ] Review diff for forbidden corpora, raw text logging, schema changes and scope leakage.
- [ ] Confirm v2 rollback path still works and v3 is default only after benchmark acceptance.
- [ ] Prepare PR summary with verification evidence and no credential material.
