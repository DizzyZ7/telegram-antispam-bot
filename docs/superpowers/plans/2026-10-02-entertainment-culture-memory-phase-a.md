# Entertainment Culture Memory Phase A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a canonical chronological Culture Memory event stream for text, emoji, stickers, photos and animations, migrate existing Entertainment text history into it, add per-user memory controls, and switch learning/runtime reads to the new memory without changing current autonomy frequency.

**Architecture:** Add a typed `MemoryEvent` model and storage APIs backed by new `ent_memory_events` and `ent_memory_preferences` tables in SQLite/PostgreSQL. During Phase A, current autonomy activity counters remain text-based; ingestion writes every supported culture event to the canonical stream and continues compatibility text writes where the old generator/activity path needs them. Current generation receives a text projection from canonical events only after parity tests pass.

**Tech Stack:** Python 3.12, aiogram 3.x, asyncpg, aiosqlite, unittest, PostgreSQL 17 CI.

**Spec:** `docs/superpowers/specs/2026-10-02-entertainment-culture-memory-v1-design.md`

## Global Constraints

- Memory stays isolated by `(chat_id, topic_id)`.
- No cross-chat or cross-topic retrieval.
- No mandatory external AI API, GPU, vector DB, OCR or vision model.
- No persistent storage of media binaries; store Telegram `file_id`/`file_unique_id` only.
- New canonical retention target remains approximately 100,000 events per topic with buffered/batch pruning rather than expensive full pruning on every insert.
- Phase A must not increase autonomous action frequency; existing text-based activity/autonomy semantics stay in place until a later verified cutover.
- Migration is idempotent and non-destructive; `entertainment_messages` remains rollback data for one release cycle.
- `/fun_delete_me` deletes the requesting user's stored Culture Memory in the current chat across all topics and must also remove their compatibility text rows during the transition period.
- Privacy checks happen before persistence.
- No copied raw neighborhood text is stored in media metadata.

## Review Focus

- Duplicate Telegram delivery / restart replay: the same `(chat_id, message_id)` must not create duplicate canonical events.
- General-topic versus forum-topic isolation: topic `0` and real topic ids must never bleed into each other.
- Users opting out before sending media: neither canonical event nor compatibility text row may be written.
- Deleting a user during the compatibility period: both canonical events and old `entertainment_messages` rows for that user must disappear.
- Corrupt/partial media objects or missing optional Telegram fields: ingestion must ignore unsupported/incomplete input safely rather than crash middleware.

---

## File Structure

### Create
- `entertainment/memory.py` — Telegram message classification and conversion into typed Culture Memory drafts/events.
- `tests/test_entertainment_memory_models.py` — event model and normalization tests.
- `tests/test_entertainment_memory_classifier.py` — text/emoji/sticker/photo/animation classification tests.
- `tests/test_entertainment_culture_memory_sqlite.py` — SQLite schema, retention, privacy and migration tests.
- `tests/test_entertainment_culture_memory_postgres.py` — PostgreSQL parity tests.
- `tests/test_entertainment_culture_memory_service.py` — service ingestion/privacy/compatibility behavior.

### Modify
- `entertainment/models.py` — add `MemoryEventType`, `MemoryEvent`, `MemoryCounts`.
- `entertainment/storage/base.py` — extend storage protocol with canonical memory and privacy methods.
- `entertainment/storage/sqlite.py` — schema + canonical event CRUD + privacy + backfill.
- `entertainment/storage/postgres.py` — PostgreSQL parity implementation.
- `entertainment/service.py` — event ingestion, privacy commands, current-topic memory counters, compatibility text projection.
- `entertainment/router.py` — register `/fun_ignore_me`, `/fun_remember_me`, `/fun_delete_me`.
- `.github/workflows/ci.yml` — include Culture Memory PostgreSQL tests in the dedicated PostgreSQL job.

---

### Task 1: Typed canonical memory model and classifier

**Files:**
- Modify: `entertainment/models.py`
- Create: `entertainment/memory.py`
- Create: `tests/test_entertainment_memory_models.py`
- Create: `tests/test_entertainment_memory_classifier.py`

**Interfaces:**
- Produces: `MemoryEventType(str, Enum)` with `TEXT`, `EMOJI`, `STICKER`, `PHOTO`, `ANIMATION`.
- Produces: immutable `MemoryEvent` with fields `id`, `chat_id`, `topic_id`, `message_id`, `user_id`, `event_type`, `text`, `caption`, `reply_to_message_id`, `file_id`, `file_unique_id`, `sticker_emoji`, `sticker_set_name`, `media_width`, `media_height`, `media_duration`, `is_forwarded`, `legacy_source_id`, `metadata`, `created_at`.
- Produces: immutable `MemoryCounts(total, text, emoji, sticker, photo, animation)`.
- Produces: `classify_memory_event(message: Message, *, chat_id: int, topic_id: int, created_at: int) -> MemoryEvent | None`.

- [ ] **Step 1: Write failing model tests**

Add tests asserting enum values are stable lowercase strings, `MemoryEvent` preserves topic/message/reply ids exactly, and `MemoryCounts` exposes all six counters.

- [ ] **Step 2: Run the model tests and verify RED**

Run: `python -m unittest tests.test_entertainment_memory_models -v`

Expected: FAIL because the new model types do not exist.

- [ ] **Step 3: Implement the model types in `entertainment/models.py`**

Keep models storage/backend-neutral. `metadata` defaults to an empty dict via `field(default_factory=dict)`.

- [ ] **Step 4: Write failing classifier tests**

Cover:
- normal text -> `TEXT` and retains emoji embedded in text;
- emoji-only string -> `EMOJI`;
- sticker -> largest available Telegram sticker identifiers, sticker emoji and set name;
- photo -> largest photo size `file_id`/`file_unique_id` and dimensions;
- animation -> ids/caption/duration;
- reply relationship -> `reply_to_message_id` from `reply_to_message.message_id`;
- forwarded marker -> `is_forwarded=True` when Telegram forward metadata is present;
- unsupported voice/document/video -> `None`;
- missing sender/media identifiers -> safe `None`, not exception.

- [ ] **Step 5: Run classifier tests and verify RED**

Run: `python -m unittest tests.test_entertainment_memory_classifier -v`

Expected: FAIL because `classify_memory_event` does not exist.

- [ ] **Step 6: Implement `classify_memory_event` in `entertainment/memory.py`**

Classification precedence: sticker -> photo -> animation -> text/emoji. Do not download media or build persistent context hints.

- [ ] **Step 7: Run Task 1 tests**

Run: `python -m unittest tests.test_entertainment_memory_models tests.test_entertainment_memory_classifier -v`

Expected: PASS.

- [ ] **Step 8: Commit**

Commit message: `feat: add Culture Memory event model and classifier`

---

### Task 2: Extend the storage contract and implement SQLite canonical memory

**Files:**
- Modify: `entertainment/storage/base.py`
- Modify: `entertainment/storage/sqlite.py`
- Create: `tests/test_entertainment_culture_memory_sqlite.py`

**Interfaces:**
- Consumes: `MemoryEvent`, `MemoryCounts` from Task 1.
- Produces storage methods:
  - `add_event(event: MemoryEvent) -> int`
  - `recent_events(chat_id: int, topic_id: int, limit: int) -> list[MemoryEvent]`
  - `recent_texts(chat_id: int, topic_id: int, limit: int = GENERATION_SAMPLE_LIMIT) -> list[str]`
  - `memory_counts(chat_id: int, topic_id: int) -> MemoryCounts`
  - `get_remember_enabled(chat_id: int, user_id: int) -> bool`
  - `set_remember_enabled(chat_id: int, user_id: int, enabled: bool) -> None`
  - `delete_user_memory(chat_id: int, user_id: int) -> int`
  - `clear_memory_scope(chat_id: int, topic_id: int | None = None) -> int`
  - `backfill_legacy_memory(migration_key: str) -> int`

- [ ] **Step 1: Write failing SQLite schema and CRUD tests**

Assert initialization creates canonical storage behavior; insert TEXT/STICKER/EMOJI events; `recent_events` returns deterministic chronological order; `recent_texts` returns text and non-empty media captions only, never sticker ids; topic isolation is strict.

- [ ] **Step 2: Add duplicate-delivery regression test**

Insert two events with the same non-null `(chat_id, message_id)` and assert only one canonical row exists and the second write returns/reuses the existing id rather than duplicating it.

- [ ] **Step 3: Add preference/deletion tests**

Assert unknown users default to `True`; toggling is per chat; `delete_user_memory(chat_id, user_id)` removes that user's events from every topic in that chat and leaves other users/chats untouched.

- [ ] **Step 4: Add retention test**

Construct storage with the existing memory policy, insert beyond the configured cap/buffer, trigger buffered prune, and assert oldest canonical events are removed while newest ordering remains valid. Do not require pruning on every insert.

- [ ] **Step 5: Run SQLite Culture Memory tests and verify RED**

Run: `python -m unittest tests.test_entertainment_culture_memory_sqlite -v`

Expected: FAIL because schema and protocol methods do not exist.

- [ ] **Step 6: Extend `EntertainmentStorage` protocol**

Add the exact signatures above in `storage/base.py`.

- [ ] **Step 7: Implement SQLite schema**

Create `ent_memory_events` with a unique partial index on `(chat_id, message_id)` where `message_id IS NOT NULL`, indexes for `(chat_id, topic_id, created_at, message_id, id)` and `user_id`, plus `ent_memory_preferences(chat_id, user_id, remember_enabled, updated_at)` with composite primary key.

- [ ] **Step 8: Implement SQLite CRUD/privacy/retention methods**

Serialize `metadata` as JSON text. Reads must convert unknown event types defensively by skipping/logging invalid rows rather than crashing all retrieval.

- [ ] **Step 9: Run SQLite tests**

Run: `python -m unittest tests.test_entertainment_culture_memory_sqlite -v`

Expected: PASS.

- [ ] **Step 10: Commit**

Commit message: `feat: add SQLite Culture Memory storage`

---

### Task 3: Idempotent legacy text backfill

**Files:**
- Modify: `entertainment/storage/sqlite.py`
- Modify: `entertainment/storage/postgres.py`
- Modify: `tests/test_entertainment_culture_memory_sqlite.py`
- Create/modify: `tests/test_entertainment_culture_memory_postgres.py`

**Interfaces:**
- Consumes: `backfill_legacy_memory(migration_key: str) -> int` from Task 2.
- Produces: backend parity for migration key `culture_memory_v1_text_backfill`.

- [ ] **Step 1: Add failing SQLite backfill tests**

Seed legacy `entertainment_messages`, run `backfill_legacy_memory("culture_memory_v1_text_backfill")`, and assert chat/topic/user/text/message_id/created_at are preserved, event type is TEXT, and `legacy_source_id` equals the old row id.

- [ ] **Step 2: Add idempotency test**

Run the same backfill twice and assert the second call imports `0` rows and canonical count does not change.

- [ ] **Step 3: Add bounded-batch behavior test**

Seed enough rows to require more than one internal batch and assert every source row is imported exactly once without deleting legacy rows.

- [ ] **Step 4: Implement SQLite backfill**

Use `ent_schema_migrations`; insert by legacy row id; keep old table unchanged; perform bounded batches inside transactions.

- [ ] **Step 5: Add equivalent PostgreSQL tests before implementation**

Use `TEST_DATABASE_URL`; assert field preservation, idempotency, non-destructive legacy rows and unique `legacy_source_id` behavior.

- [ ] **Step 6: Implement PostgreSQL canonical schema + backfill parity**

Add `ent_memory_events`, `ent_memory_preferences`, indexes, JSONB metadata, unique partial indexes, and all protocol methods required by Task 2 before making the PostgreSQL test green.

- [ ] **Step 7: Run backend-specific tests**

Run: `python -m unittest tests.test_entertainment_culture_memory_sqlite tests.test_entertainment_culture_memory_postgres -v`

Expected: SQLite PASS locally; PostgreSQL tests PASS in PostgreSQL-capable CI/runtime.

- [ ] **Step 8: Commit**

Commit message: `feat: migrate legacy Entertainment text into Culture Memory`

---

### Task 4: Service ingestion with privacy and compatibility writes

**Files:**
- Modify: `entertainment/service.py`
- Modify: `entertainment/memory.py`
- Create: `tests/test_entertainment_culture_memory_service.py`

**Interfaces:**
- Consumes: classifier from Task 1 and storage methods from Tasks 2–3.
- Produces service methods:
  - `observe_message(message: Message) -> None` learns all supported events.
  - `set_remember_me(message: Message, enabled: bool) -> None`.
  - `delete_my_memory(message: Message) -> None`.

- [ ] **Step 1: Write failing ingestion tests**

Assert allowed human TEXT/EMOJI/STICKER/PHOTO/ANIMATION messages call `add_event`; bots, unsupported chats and unsupported media do not.

- [ ] **Step 2: Add compatibility/autonomy regression tests**

Assert only messages that satisfy the existing text-learning quality gate are also written to legacy `add_message`; sticker/photo/emoji-only events do not enter legacy text activity. Assert a media-only event does not itself increase current generation readiness or cause a new autonomous text action solely because it exists.

- [ ] **Step 3: Add opt-out test**

When `get_remember_enabled` returns false, assert neither `add_event` nor legacy `add_message` is called and no raw event data reaches storage.

- [ ] **Step 4: Add duplicate/incomplete-media middleware safety test**

A classifier/storage duplicate or incomplete media should not cause `observe_message` to crash the outer learning middleware; unsupported input is ignored.

- [ ] **Step 5: Run service tests and verify RED**

Run: `python -m unittest tests.test_entertainment_culture_memory_service -v`

Expected: FAIL on current text-only `observe_message`.

- [ ] **Step 6: Refactor `observe_message` into classify -> preference check -> canonical persist -> compatibility path**

Preserve `remember_active_topic` behavior. Evaluate autonomy only under current semantics; do not make all stored media events autonomous triggers in Phase A.

- [ ] **Step 7: Implement `set_remember_me` and `delete_my_memory`**

`delete_my_memory` deletes canonical memory and compatibility legacy text rows for that `(chat_id, user_id)` during the transition period. Add one focused storage helper if necessary rather than issuing SQL from service code.

- [ ] **Step 8: Run Task 4 tests**

Run: `python -m unittest tests.test_entertainment_culture_memory_service -v`

Expected: PASS.

- [ ] **Step 9: Commit**

Commit message: `feat: ingest chronological Culture Memory events`

---

### Task 5: Privacy commands, `/fun` counters and scope deletion

**Files:**
- Modify: `entertainment/router.py`
- Modify: `entertainment/service.py`
- Modify: storage contract/backends only if a compatibility deletion helper is still required
- Modify: `tests/test_entertainment_culture_memory_service.py`
- Modify/create: `tests/test_entertainment_router.py` if present; otherwise add focused router tests in the existing Entertainment router test module.

**Interfaces:**
- Consumes: `set_remember_me`, `delete_my_memory`, `memory_counts`.
- Produces commands `/fun_ignore_me`, `/fun_remember_me`, `/fun_delete_me`.

- [ ] **Step 1: Add failing command registration tests**

Assert all three commands are registered only in Entertainment-allowlisted chats and do not consume unrelated handlers.

- [ ] **Step 2: Add service response tests**

Assert ignore/remember commands acknowledge the current chat scope; delete command reports deleted event count without claiming Telegram messages were deleted.

- [ ] **Step 3: Add `/fun_forget` regression test**

Admin forget for the current topic clears both canonical Culture Memory and compatibility text projection for that topic, but not another topic.

- [ ] **Step 4: Add `/fun` memory counter test**

Panel/status reads `memory_counts` for the current topic and shows compact total/text/emoji/sticker/photo+animation counts without exposing raw stored content.

- [ ] **Step 5: Implement router and service UX**

Keep existing admin-only restrictions for admin controls; privacy commands act on the requesting user and do not require admin privileges.

- [ ] **Step 6: Run privacy/panel tests**

Run the focused service/router test modules.

Expected: PASS.

- [ ] **Step 7: Commit**

Commit message: `feat: add Culture Memory privacy controls`

---

### Task 6: Compatibility text projection, runtime backfill and CI verification

**Files:**
- Modify: `entertainment/service.py`
- Modify: `entertainment/runtime.py`
- Modify: `.github/workflows/ci.yml`
- Modify: relevant storage/runtime tests
- Modify: `tests/test_entertainment_culture_memory_service.py`

**Interfaces:**
- Consumes: `recent_texts`, `backfill_legacy_memory`.
- Produces: runtime startup that ensures schema/backfill before Entertainment starts; current generator can read canonical text projection while activity/budget counters remain unchanged.

- [ ] **Step 1: Add failing runtime backfill test**

Assert opening initialized Entertainment storage applies `culture_memory_v1_text_backfill` once and surfaces/logs imported-row count without logging DSN or raw chat content.

- [ ] **Step 2: Add text projection parity test**

For canonical TEXT rows equivalent to legacy source rows, assert `recent_texts` returns the same chronological text sequence expected by the current generator, excluding emoji-only and media-without-caption events.

- [ ] **Step 3: Add topic isolation regression test**

Populate general topic `0` and two forum topics; assert each generation projection receives only its own topic's text.

- [ ] **Step 4: Switch current generation reads to `recent_texts`**

Replace only the source-text retrieval call; do not change action budgets, `activity_snapshot`, `human_messages_since`, behavior modes or supervisor frequency in this phase.

- [ ] **Step 5: Wire startup backfill**

Run canonical backfill after storage initialization and before service starts accepting updates. Failure should fail startup rather than silently run with a partially migrated canonical store.

- [ ] **Step 6: Extend PostgreSQL CI command**

Update `entertainment-postgres` to include `tests.test_entertainment_culture_memory_postgres` alongside existing PostgreSQL modules.

- [ ] **Step 7: Run focused Culture Memory suite**

Run: `python -m unittest tests.test_entertainment_memory_models tests.test_entertainment_memory_classifier tests.test_entertainment_culture_memory_sqlite tests.test_entertainment_culture_memory_service -v`

Expected: PASS.

- [ ] **Step 8: Run compile check**

Run: `python -m compileall -q .`

Expected: exit 0.

- [ ] **Step 9: Run full unittest discovery**

Run: `python -m unittest discover -s tests -p "test_*.py"`

Expected: no new failures outside the repository's known unrelated baseline. Record exact counts rather than claiming an all-green suite if baseline failures remain.

- [ ] **Step 10: Verify PostgreSQL 17 CI job**

Expected: dedicated `entertainment-postgres` job SUCCESS including the new Culture Memory PostgreSQL tests.

- [ ] **Step 11: Compare branch with base**

Verify only Phase A files/tests/docs/CI changed; no Lexicon or unrelated moderation behavior changed.

- [ ] **Step 12: Commit**

Commit message: `feat: cut Entertainment reads over to Culture Memory`

---

## Phase A Exit Criteria

Phase A is complete only when all of the following are evidenced by fresh verification:

- canonical event storage works on SQLite and PostgreSQL;
- old text history is backfilled once and remains available in the old table for rollback;
- TEXT/EMOJI/STICKER/PHOTO/ANIMATION ingestion is topic-isolated;
- no media binary is persisted;
- duplicate Telegram message delivery is idempotent;
- opt-out prevents future canonical and compatibility learning;
- `/fun_delete_me` removes the user's canonical + compatibility memory for the current chat;
- `/fun_forget` clears the full current Culture Memory topic scope;
- current generator reads canonical text projection;
- current autonomy frequency/budgets remain unchanged;
- PostgreSQL integration job is green;
- no new unrelated test failures are introduced.

## Follow-up Plans After Phase A

Do not implement these inside Phase A:

- **Phase B:** sequence/culture engine, bounded historical windows, emoji style behavior.
- **Phase C:** contextual sticker/photo/animation retrieval and autonomous media actions.
- **Phase D:** Pillow Meme Engine, temporary media download/render/send/cleanup.

Each phase gets its own implementation plan after Phase A is merged and verified in production-like CI.
