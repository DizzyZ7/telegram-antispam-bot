# Entertainment Culture Memory Phase A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a canonical chronological Culture Memory stream for text, emoji, stickers, photos and animations; backfill existing Entertainment text history; add user memory controls; and cut current text generation over to the canonical projection without changing autonomy frequency.

**Architecture:** Add typed `MemoryEvent` records and new `ent_memory_events` / `ent_memory_preferences` tables in SQLite and PostgreSQL. Phase A dual-writes only qualifying legacy text for compatibility/activity parity while all supported culture events go to the canonical stream. Generation source text switches to canonical `recent_texts(...)` only after parity tests; existing action budgets, activity windows and supervisor cadence remain unchanged. Canonical-event retention extends the existing `storage/retention.py` wrapper so the current configurable buffered-prune architecture stays intact.

**Tech Stack:** Python 3.12, aiogram 3.x, aiosqlite, asyncpg, unittest, PostgreSQL 17 CI.

**Spec:** `docs/superpowers/specs/2026-10-02-entertainment-culture-memory-v1-design.md`

## Global Constraints

- Isolate all memory by chat + topic.
- Store Telegram media ids/metadata only; no persistent media binaries.
- Retain approximately 100,000 canonical events per topic with the existing buffered/batch pruning wrapper.
- Keep current autonomy frequency/activity semantics unchanged in Phase A.
- Backfill is idempotent and non-destructive; keep `entertainment_messages` for rollback.
- Privacy checks happen before any canonical or compatibility write.
- `/fun_delete_me` removes canonical events and compatibility legacy text for that user in the current chat, across all topics.
- No duplicated raw neighborhood text in media metadata.
- No external AI, GPU, vector DB, OCR or vision dependency.

## Review Focus

- Duplicate `(chat_id, message_id)` delivery must stay idempotent. Covered in Task 2 duplicate-delivery test.
- Topic `0` and real forum topics must never bleed together. Covered in Task 7 topic-isolation regression.
- Opted-out users must create neither canonical nor legacy rows. Covered in Task 5 opt-out test.
- User deletion must remove both canonical and compatibility text rows while leaving other users/chats untouched. Covered in Tasks 2 and 6 deletion tests.
- Missing optional Telegram media fields must be ignored safely, never crash middleware. Covered in Tasks 1 and 5 incomplete-media tests.

---

## File Map

**Create**
- `entertainment/memory.py` — classify aiogram messages into Culture Memory events.
- `tests/test_entertainment_memory_models.py`
- `tests/test_entertainment_memory_classifier.py`
- `tests/test_entertainment_culture_memory_sqlite.py`
- `tests/test_entertainment_culture_memory_postgres.py`
- `tests/test_entertainment_culture_memory_service.py`
- `tests/test_entertainment_culture_memory_router.py`

**Modify**
- `entertainment/models.py`
- `entertainment/storage/base.py`
- `entertainment/storage/sqlite.py`
- `entertainment/storage/postgres.py`
- `entertainment/storage/retention.py`
- `entertainment/service.py`
- `entertainment/router.py`
- `entertainment/runtime.py`
- `.github/workflows/ci.yml`
- `README.md`

---

### Task 1: Canonical memory types and Telegram classifier

**Files:**
- Modify: `entertainment/models.py`
- Create: `entertainment/memory.py`
- Test: `tests/test_entertainment_memory_models.py`
- Test: `tests/test_entertainment_memory_classifier.py`

**Interfaces:**
- Produces `MemoryEventType(str, Enum)`: `TEXT`, `EMOJI`, `STICKER`, `PHOTO`, `ANIMATION`.
- Produces immutable `MemoryEvent(id, chat_id, topic_id, message_id, user_id, event_type, text, caption, reply_to_message_id, file_id, file_unique_id, sticker_emoji, sticker_set_name, media_width, media_height, media_duration, is_forwarded, legacy_source_id, metadata, created_at)`.
- Produces immutable `MemoryCounts(total, text, emoji, sticker, photo, animation)`.
- Produces `classify_memory_event(message: Message, *, chat_id: int, topic_id: int, created_at: int) -> MemoryEvent | None`.

- [ ] Write failing model tests for stable enum values, immutable event fields and all six counters.
- [ ] Run `python -m unittest tests.test_entertainment_memory_models -v`; expect RED because types do not exist.
- [ ] Implement the three model types in `entertainment/models.py`; `metadata` uses `field(default_factory=dict)`.
- [ ] Write failing classifier tests: normal text -> TEXT, emoji-only -> EMOJI, sticker metadata, largest photo size, animation metadata, reply id, forwarded marker, unsupported voice/document/video -> `None`, incomplete media -> `None` without exception.
- [ ] Run `python -m unittest tests.test_entertainment_memory_classifier -v`; expect RED.
- [ ] Implement `classify_memory_event` with precedence sticker -> photo -> animation -> text/emoji. Never download media or persist neighborhood text.
- [ ] Run both Task 1 modules; expect PASS.
- [ ] Commit: `feat: add Culture Memory event model and classifier`.

---

### Task 2: Storage contract and SQLite canonical memory

**Files:**
- Modify: `entertainment/storage/base.py`
- Modify: `entertainment/storage/sqlite.py`
- Test: `tests/test_entertainment_culture_memory_sqlite.py`

**Interfaces:**
- Consumes Task 1 models.
- Adds these exact storage methods:
  - `add_event(event: MemoryEvent) -> int`
  - `recent_events(chat_id: int, topic_id: int, limit: int) -> list[MemoryEvent]`
  - `recent_texts(chat_id: int, topic_id: int, limit: int = GENERATION_SAMPLE_LIMIT) -> list[str]`
  - `memory_counts(chat_id: int, topic_id: int) -> MemoryCounts`
  - `get_remember_enabled(chat_id: int, user_id: int) -> bool`
  - `set_remember_enabled(chat_id: int, user_id: int, enabled: bool) -> None`
  - `delete_user_memory(chat_id: int, user_id: int) -> int`
  - `delete_legacy_user_messages(chat_id: int, user_id: int) -> int`
  - `clear_memory_scope(chat_id: int, topic_id: int | None = None) -> int`
  - `backfill_legacy_memory(migration_key: str) -> int`

- [ ] Write failing SQLite CRUD tests: TEXT/STICKER/EMOJI insert, chronological `recent_events`, `recent_texts` includes TEXT and non-empty media captions only, strict topic isolation.
- [ ] Add duplicate-delivery test: inserting same non-null `(chat_id, message_id)` twice yields one row and reuses/returns existing id.
- [ ] Add preference tests: unknown defaults `True`; toggle is per chat.
- [ ] Add deletion tests for `delete_user_memory` and `delete_legacy_user_messages`, proving other users/chats remain.
- [ ] Run `python -m unittest tests.test_entertainment_culture_memory_sqlite -v`; expect RED.
- [ ] Extend `EntertainmentStorage` protocol with the signatures above.
- [ ] Create SQLite `ent_memory_events` with unique partial `(chat_id, message_id)` index for non-null message ids, scope/order indexes, user index, and `legacy_source_id` unique partial index.
- [ ] Create SQLite `ent_memory_preferences(chat_id, user_id, remember_enabled, updated_at)` with composite primary key.
- [ ] Implement SQLite CRUD/privacy methods without retention policy in the core backend. Metadata is JSON text; unknown event types are skipped/logged during reads rather than crashing retrieval.
- [ ] Run SQLite Culture Memory tests; expect PASS.
- [ ] Commit: `feat: add SQLite Culture Memory storage`.

---

### Task 3: PostgreSQL parity and legacy text backfill

**Files:**
- Modify: `entertainment/storage/postgres.py`
- Modify: `entertainment/storage/sqlite.py`
- Test: `tests/test_entertainment_culture_memory_sqlite.py`
- Test: `tests/test_entertainment_culture_memory_postgres.py`

**Interfaces:**
- Implements every Task 2 method on PostgreSQL.
- Uses migration key `culture_memory_v1_text_backfill`.

- [ ] Add SQLite backfill tests: seed `entertainment_messages`, backfill, assert chat/topic/user/text/message_id/created_at preserved and `legacy_source_id` equals old row id.
- [ ] Add SQLite idempotency test: second backfill returns `0`, canonical count unchanged, old table untouched.
- [ ] Add multi-batch SQLite test proving all source rows import once.
- [ ] Implement bounded SQLite backfill using `ent_schema_migrations` and transactions.
- [ ] Write PostgreSQL parity tests for CRUD, privacy, duplicate delivery, deletion, text projection and backfill idempotency against `TEST_DATABASE_URL`.
- [ ] Run PostgreSQL tests before implementation; expect RED.
- [ ] Implement PostgreSQL `ent_memory_events`/`ent_memory_preferences`, JSONB metadata, indexes, protocol methods and bounded backfill. Keep retention out of the core backend.
- [ ] Run `python -m unittest tests.test_entertainment_culture_memory_sqlite tests.test_entertainment_culture_memory_postgres -v` in a PostgreSQL-capable environment; expect PASS.
- [ ] Commit: `feat: add PostgreSQL Culture Memory and backfill`.

---

### Task 4: Canonical event retention wrapper

**Files:**
- Modify: `entertainment/storage/retention.py`
- Modify: `tests/test_entertainment_memory_limits.py`
- Modify: `tests/test_entertainment_memory_postgres.py`

**Interfaces:**
- Consumes `add_event(event: MemoryEvent) -> int` from Tasks 2–3.
- Produces the same method on the existing retention wrappers, applying `MEMORY_LIMIT` and `MEMORY_PRUNE_BUFFER` per `chat_id + topic_id`.

- [ ] Add failing SQLite wrapper test with tiny configured values: inserts up to `memory_limit + prune_buffer` do not prune; the next insert prunes canonical rows back to exactly `memory_limit`.
- [ ] Add strict-isolation assertion: pruning one topic does not touch another topic/chat.
- [ ] Add regression assertion: canonical-event pruning never deletes rollback rows from `entertainment_messages`.
- [ ] Add equivalent PostgreSQL wrapper test to the existing PostgreSQL memory test module.
- [ ] Run `python -m unittest tests.test_entertainment_memory_limits -v`; expect RED on canonical-event retention assertions.
- [ ] Override `add_event()` in SQLite/PostgreSQL retention wrappers. Insert once through core canonical storage, count only `ent_memory_events` in that topic, and prune only after the buffer threshold is exceeded.
- [ ] Ensure wrapper pruning returns the original event id and does not perform a full prune delete on every insert.
- [ ] Run SQLite retention tests; expect PASS.
- [ ] Run PostgreSQL memory job including the new canonical retention test; expect PASS.
- [ ] Commit: `feat: retain Culture Memory events in buffered batches`.

---

### Task 5: Service ingestion, dual-write compatibility and privacy semantics

**Files:**
- Modify: `entertainment/service.py`
- Test: `tests/test_entertainment_culture_memory_service.py`

**Interfaces:**
- Consumes Task 1 classifier and Task 2–4 storage methods.
- Produces:
  - `observe_message(message: Message) -> None`
  - `set_remember_me(message: Message, enabled: bool) -> None`
  - `delete_my_memory(message: Message) -> None`

- [ ] Write failing ingestion tests: allowed human TEXT/EMOJI/STICKER/PHOTO/ANIMATION call `add_event`; bots, unsupported chats and unsupported media do not.
- [ ] Add compatibility regression: only text satisfying the old text-quality gate also calls legacy `add_message`; emoji-only/media do not enter legacy activity.
- [ ] Add autonomy regression: storing media alone does not increase current generation readiness or trigger a new autonomous text action in Phase A.
- [ ] Add opt-out test: when `get_remember_enabled` is false, neither `add_event` nor `add_message`, `remember_active_topic`, nor `evaluate_topic` is called.
- [ ] Add incomplete-media/duplicate safety test: `observe_message` returns cleanly and middleware continues.
- [ ] Run `python -m unittest tests.test_entertainment_culture_memory_service -v`; expect RED.
- [ ] Refactor `observe_message` to: allowlist/human check -> preference check -> classify -> canonical write -> compatibility text write -> active-topic update -> existing autonomy path.
- [ ] Implement `set_remember_me` using `set_remember_enabled`.
- [ ] Implement `delete_my_memory` using both `delete_user_memory` and `delete_legacy_user_messages`; never issue SQL from service.
- [ ] Run Task 5 tests; expect PASS.
- [ ] Commit: `feat: ingest chronological Culture Memory events`.

---

### Task 6: Privacy commands, `/fun` counters and admin scope deletion

**Files:**
- Modify: `entertainment/router.py`
- Modify: `entertainment/service.py`
- Test: `tests/test_entertainment_culture_memory_service.py`
- Test: `tests/test_entertainment_culture_memory_router.py`

**Interfaces:**
- Adds commands `/fun_ignore_me`, `/fun_remember_me`, `/fun_delete_me`.
- `/fun_forget` clears canonical current-topic memory and legacy current-topic compatibility text.

- [ ] Write failing router tests proving the three privacy commands register only in Entertainment chats and middleware/handlers remain non-consuming for unrelated features.
- [ ] Add service response tests: ignore/remember acknowledge current chat; delete reports deleted count and never claims Telegram messages were deleted.
- [ ] Add `/fun_forget` regression: current topic canonical + legacy projection clear; another topic remains.
- [ ] Add `/fun` panel test: display compact current-topic counts for total/text/emoji/sticker/photo+animation without raw content.
- [ ] Run service/router Culture Memory tests; expect RED.
- [ ] Register the three commands in `router.py`; privacy commands act on requester and do not require admin status.
- [ ] Update `show_panel`/status to use `memory_counts` while keeping existing behavior-mode controls intact.
- [ ] Update admin `forget_chat` to call both `clear_memory_scope(chat_id, topic_id)` and existing legacy `clear_scope(chat_id, topic_id)`.
- [ ] Run service/router tests; expect PASS.
- [ ] Commit: `feat: add Culture Memory privacy controls`.

---

### Task 7: Runtime backfill, canonical text projection, docs and CI gate

**Files:**
- Modify: `entertainment/runtime.py`
- Modify: `entertainment/service.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Modify: `tests/test_entertainment_runtime.py`
- Test: `tests/test_entertainment_culture_memory_service.py`

**Interfaces:**
- Startup calls `backfill_legacy_memory("culture_memory_v1_text_backfill")` after storage initialization and before Entertainment handlers run.
- Current generator source retrieval switches from legacy `recent_messages(...)` to canonical `recent_texts(...)`.

- [ ] Add failing runtime test: startup applies Culture Memory backfill once, exposes/logs imported count, and never logs DSN/raw chat text.
- [ ] Add canonical text-projection parity test against equivalent legacy text rows.
- [ ] Add topic-isolation regression for topic `0` plus two real forum topics.
- [ ] Run focused tests; expect RED.
- [ ] Wire backfill into runtime startup. Backfill failure fails startup instead of silently running partially migrated.
- [ ] Switch only generation source reads to `recent_texts`; do not change `activity_snapshot`, `human_messages_since`, action budgets, behavior modes or supervisor interval.
- [ ] Update PostgreSQL CI command to include `tests.test_entertainment_culture_memory_postgres`.
- [ ] Update README with remembered Phase A event types, privacy commands, `file_id`-only media persistence, rollback-table retention, and the explicit note that Phase A does **not** autonomously send remembered media yet.
- [ ] Run `python -m unittest tests.test_entertainment_memory_models tests.test_entertainment_memory_classifier tests.test_entertainment_culture_memory_sqlite tests.test_entertainment_culture_memory_service tests.test_entertainment_culture_memory_router -v`; expect PASS.
- [ ] Run `python -m compileall -q .`; expect exit 0.
- [ ] Run `python -m unittest discover -s tests -p "test_*.py"`; record exact pass/fail/skip counts and require no new failures outside known unrelated baseline.
- [ ] Verify PostgreSQL 17 `entertainment-postgres` CI job is SUCCESS with new Culture Memory module included.
- [ ] Compare branch against base and verify only Phase A files/tests/docs/CI changed; no Lexicon/moderation product behavior changed.
- [ ] Commit: `feat: cut Entertainment reads over to Culture Memory`.

---

## Phase A Exit Criteria

Phase A ships only with fresh evidence that:
- canonical SQLite/PostgreSQL event storage works;
- legacy history backfills once without deleting rollback rows;
- TEXT/EMOJI/STICKER/PHOTO/ANIMATION ingestion is topic-isolated;
- no media binaries are persisted;
- duplicate Telegram delivery is idempotent;
- opt-out blocks canonical + compatibility learning;
- `/fun_delete_me` removes canonical + legacy user memory in the current chat;
- `/fun_forget` clears current-topic canonical + legacy memory only;
- current generator reads canonical text projection;
- autonomy frequency/budgets remain unchanged;
- buffered 100k retention applies to canonical events through the existing retention wrappers;
- PostgreSQL integration is green;
- no new unrelated failures appear.

## Explicitly Deferred

- **Phase B:** sequence/culture engine, historical windows and emoji style behavior.
- **Phase C:** contextual sticker/photo/animation retrieval and autonomous media actions.
- **Phase D:** Pillow Meme Engine with temporary download/render/send/cleanup.

Each deferred phase gets its own implementation plan after Phase A is merged and verified.
