# Entertainment Foundation & Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor entertainment v1 into a maintainable package and add topic-aware PostgreSQL-first persistence for Bothost without breaking current commands, moderation, Lexicon, or stored memory.

**Architecture:** Keep one aiogram process. First perform a behavior-preserving package split, then replace the legacy concrete SQLite class with a shared async storage protocol implemented by SQLite and PostgreSQL. All forum memory is keyed by `(chat_id, topic_id)`; PostgreSQL is selected by `DATABASE_URL`, while SQLite remains local fallback only.

**Tech Stack:** Python 3.12, aiogram 3.24+, aiosqlite 0.20+, asyncpg 0.30+, unittest, GitHub Actions PostgreSQL service.

**Spec:** `docs/superpowers/specs/2026-10-01-entertainment-autonomy-v2-design.md`

## Global Constraints

- Primary deployment: Bothost, one Python/aiogram process, no Redis/Celery/RQ/worker requirement.
- Initial enabled chat remains `-1002619489118`; entertainment stays hard-scoped to explicit chat IDs.
- `DATABASE_URL` configured with PostgreSQL => PostgreSQL is authoritative.
- PostgreSQL startup failure must fail clearly unless `ENTERTAINMENT_DB_FALLBACK_SQLITE=1` is explicitly set.
- Forum memory scope is `(chat_id, topic_id)`; non-forum/general topic normalizes to `0`.
- Existing v1 SQLite settings/messages must survive migration; import must be idempotent.
- Existing moderation/Lexicon registration and behavior remain independent.
- This phase does not add autonomy scoring, media memory, memes, polls/events, AI providers, or the new admin UX; those get separate plans after the foundation lands.

## Review Focus

- Unsupported `DATABASE_URL` must raise a configuration error, never silently select SQLite. Covered in Task 3.
- PostgreSQL unavailable at startup must fail-fast unless explicit fallback is enabled. Covered in Task 3.
- Two forum topics in one chat must not share learned text. Covered in Tasks 2 and 5.
- Re-running v1 import must not duplicate settings/messages. Covered in Task 4.
- Partial startup/shutdown must close every opened storage resource exactly once. Covered in Task 5.

---

## Locked File Structure

- `entertainment/__init__.py` — stable compatibility exports.
- `entertainment/config.py` — chat allowlist + DB configuration.
- `entertainment/models.py` — settings dataclass + topic normalization.
- `entertainment/generation.py` — existing local text generator.
- `entertainment/service.py` — message learning/generation/admin behavior.
- `entertainment/router.py` — aiogram middleware/handlers.
- `entertainment/storage_legacy.py` — temporary behavior-preserving home of the current concrete SQLite class; removed in Task 2.
- `entertainment/storage/base.py` — storage protocol/errors.
- `entertainment/storage/sqlite.py` — SQLite backend.
- `entertainment/storage/postgres.py` — asyncpg backend.
- `entertainment/storage/factory.py` — backend selection/open/fallback policy.
- `entertainment/storage/migrations.py` — v1 SQLite import.

Existing files modified: `main.py`, `requirements.txt`, `.github/workflows/ci.yml`, `README.md`, `tests/test_entertainment.py`.

New tests: `test_entertainment_storage_sqlite.py`, `test_entertainment_storage_postgres.py`, `test_entertainment_storage_factory.py`, `test_entertainment_migration.py`, `test_entertainment_topics.py`, `test_entertainment_lifecycle.py`.

---

### Task 1: Behavior-preserving package split

**Files:**
- Create: `entertainment/__init__.py`
- Create: `entertainment/config.py`
- Create: `entertainment/models.py`
- Create: `entertainment/generation.py`
- Create: `entertainment/service.py`
- Create: `entertainment/router.py`
- Create: `entertainment/storage_legacy.py`
- Modify: `tests/test_entertainment.py`
- Delete after verification: `entertainment.py`

**Interfaces:**
- `parse_chat_ids(raw_value: str | None) -> frozenset[int]`
- `normalize_topic_id(message_thread_id: int | None) -> int`
- `EntertainmentSettings`
- legacy concrete `EntertainmentStorage(Path)` remains exported unchanged for this task only.
- `EntertainmentService(app, storage, chat_ids, *, rng=None)`
- `generate_chat_text(messages: list[str], *, rng=None, max_tokens: int = 30) -> str | None`
- `register_entertainment_handlers(app, service) -> None`

- [ ] **Step 1: Write import-compatibility tests**

Assert existing imports from `entertainment` still resolve and current `parse_chat_ids`, generation, settings defaults, and storage constructor behavior remain unchanged.

- [ ] **Step 2: Run current entertainment tests**

Run: `python -m unittest discover -s tests -p "test_entertainment.py" -v`

Expected before refactor: PASS.

- [ ] **Step 3: Split code into package files without changing behavior**

Move the current concrete storage class into `storage_legacy.py`; do not redesign its schema yet. Re-export it from `entertainment.__init__` so `main.py` and tests keep working.

- [ ] **Step 4: Remove `entertainment.py` and rerun tests**

Run: `python -m unittest discover -s tests -p "test_entertainment.py" -v`

Expected: PASS.

- [ ] **Step 5: Compile**

Run: `python -m compileall -q .`

Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add entertainment tests/test_entertainment.py
git rm entertainment.py
git commit -m "refactor(entertainment): split module into package"
```

---

### Task 2: Storage protocol + topic-aware SQLite

**Files:**
- Create: `entertainment/storage/__init__.py`
- Create: `entertainment/storage/base.py`
- Create: `entertainment/storage/sqlite.py`
- Modify: `entertainment/models.py`
- Modify: `entertainment/service.py`
- Modify: `entertainment/__init__.py`
- Delete: `entertainment/storage_legacy.py`
- Create: `tests/test_entertainment_storage_sqlite.py`
- Create: `tests/test_entertainment_topics.py`
- Modify: `tests/test_entertainment.py`

**Interfaces:**
- Protocol `EntertainmentStorage`:
  - `initialize() -> None`
  - `close() -> None`
  - `get_settings(chat_id: int) -> EntertainmentSettings`
  - `save_settings(chat_id: int, settings: EntertainmentSettings) -> None`
  - `add_message(chat_id: int, topic_id: int, user_id: int, text: str, *, message_id: int | None = None) -> None`
  - `recent_messages(chat_id: int, topic_id: int, limit: int = 900) -> list[str]`
  - `message_count(chat_id: int, topic_id: int | None = None) -> int`
  - `clear_scope(chat_id: int, topic_id: int | None = None) -> int`
- `SQLiteEntertainmentStorage(database_path: Path)` implements the protocol.
- `topic_id=None` means whole-chat aggregation/destruction; integer means one topic.

- [ ] **Step 1: Write failing SQLite contract tests**

Assert chat isolation, topic isolation, whole-chat count, topic-only clear, whole-chat clear preserving other chats, and in-place upgrade of a legacy table without `topic_id`.

- [ ] **Step 2: Run and confirm failure**

Run: `python -m unittest discover -s tests -p "test_entertainment_storage_sqlite.py" -v`

Expected: FAIL because v2 storage does not exist.

- [ ] **Step 3: Implement protocol + SQLite backend**

`entertainment_messages` keeps existing columns and adds `topic_id INTEGER NOT NULL DEFAULT 0`, nullable `message_id`, and index `(chat_id, topic_id, id DESC)`. Upgrade uses schema inspection before `ALTER TABLE` so restart is idempotent.

- [ ] **Step 4: Write failing service topic-isolation tests**

Use the same `chat.id` with two `message_thread_id` values; after `observe_message()`, assert writes/reads stay in the originating topic.

- [ ] **Step 5: Update service to normalize topic IDs and use v2 storage**

Generation/status/learning operate on current topic. Whole-chat destructive operations require an explicit whole-chat call path.

- [ ] **Step 6: Run entertainment suite**

Run: `python -m unittest discover -s tests -p "test_entertainment*.py" -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add entertainment tests/test_entertainment*.py
git commit -m "feat(entertainment): add topic-aware storage contract"
```

---

### Task 3: PostgreSQL backend + strict backend selection

**Files:**
- Create: `entertainment/storage/postgres.py`
- Create: `entertainment/storage/factory.py`
- Modify: `entertainment/storage/__init__.py`
- Modify: `entertainment/config.py`
- Modify: `requirements.txt`
- Create: `tests/test_entertainment_storage_factory.py`
- Create: `tests/test_entertainment_storage_postgres.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- `EntertainmentDatabaseConfig.from_env(data_dir: Path) -> EntertainmentDatabaseConfig`
- fields: `database_url: str | None`, `sqlite_path: Path`, `allow_sqlite_fallback: bool`
- `PostgresEntertainmentStorage(database_url: str, *, min_pool_size: int = 1, max_pool_size: int = 4)`
- `async open_entertainment_storage(config: EntertainmentDatabaseConfig) -> EntertainmentStorage`
- errors: `EntertainmentStorageConfigurationError`, `EntertainmentStorageUnavailableError`

- [ ] **Step 1: Add `asyncpg>=0.30` and failing factory tests**

Cases: no URL -> SQLite; `postgres://`/`postgresql://` -> PostgreSQL; unsupported scheme -> configuration error; PostgreSQL init failure -> unavailable error; explicit fallback flag -> SQLite.

- [ ] **Step 2: Run factory tests and confirm failure**

Run: `python -m unittest discover -s tests -p "test_entertainment_storage_factory.py" -v`

Expected: FAIL.

- [ ] **Step 3: Implement config/factory**

Read `DATABASE_URL` and `ENTERTAINMENT_DB_FALLBACK_SQLITE`. Never log raw DB credentials.

- [ ] **Step 4: Write PostgreSQL contract tests using `TEST_DATABASE_URL`**

Mirror SQLite semantics: settings isolation, topic isolation, count/clear behavior, clean close.

- [ ] **Step 5: Implement asyncpg backend**

Pool size 1..4, explicit timeouts, transactional schema initialization, equivalent indexes.

- [ ] **Step 6: Add focused PostgreSQL CI job**

GitHub Actions starts PostgreSQL service and runs factory + PostgreSQL storage tests with `TEST_DATABASE_URL`.

- [ ] **Step 7: Verify locally**

Run: `python -m unittest discover -s tests -p "test_entertainment_storage_*.py" -v`

Expected: SQLite/factory PASS; PostgreSQL tests SKIP locally when no test URL exists.

- [ ] **Step 8: Commit**

```bash
git add entertainment requirements.txt tests/test_entertainment_storage_*.py .github/workflows/ci.yml
git commit -m "feat(entertainment): add postgres storage backend"
```

---

### Task 4: Idempotent v1 SQLite migration

**Files:**
- Create: `entertainment/storage/migrations.py`
- Modify: `entertainment/storage/base.py`
- Modify: `entertainment/storage/sqlite.py`
- Modify: `entertainment/storage/postgres.py`
- Create: `tests/test_entertainment_migration.py`

**Interfaces:**
- `async migrate_v1_sqlite_if_needed(source_path: Path, target: EntertainmentStorage) -> MigrationReport`
- `MigrationReport(settings_imported: int, messages_imported: int, skipped: int, already_applied: bool)`
- Imported messages carry original v1 row ID as nullable `legacy_source_id` for conflict-safe re-import.

- [ ] **Step 1: Build a real v1-shaped SQLite fixture and failing migration tests**

Assert settings/text survive, topic becomes `0`, source file remains, second run duplicates nothing, and newer target settings are not overwritten by stale source settings.

- [ ] **Step 2: Run and confirm failure**

Run: `python -m unittest discover -s tests -p "test_entertainment_migration.py" -v`

Expected: FAIL.

- [ ] **Step 3: Implement migration metadata/import**

Add nullable `legacy_source_id` with conflict-safe uniqueness and `ent_schema_migrations`. Never delete/rename the source DB automatically.

- [ ] **Step 4: Verify SQLite target**

Run: `python -m unittest discover -s tests -p "test_entertainment_migration.py" -v`

Expected: PASS.

- [ ] **Step 5: Extend PostgreSQL integration test**

Run import twice against PostgreSQL and assert stable row counts.

- [ ] **Step 6: Commit**

```bash
git add entertainment/storage tests/test_entertainment_migration.py tests/test_entertainment_storage_postgres.py
git commit -m "feat(entertainment): migrate v1 memory idempotently"
```

---

### Task 5: Application lifecycle + coexistence

**Files:**
- Modify: `main.py`
- Modify: `entertainment/__init__.py`
- Create: `tests/test_entertainment_lifecycle.py`
- Modify: `tests/test_entertainment.py`

**Interfaces:**
- `main.py` uses `EntertainmentDatabaseConfig.from_env(RUNTIME_DATA_DIR)`.
- `main.py` calls `await open_entertainment_storage(config)`.
- If target backend is PostgreSQL, call `migrate_v1_sqlite_if_needed(RUNTIME_DATA_DIR / "entertainment.db", storage)` before handler registration.
- `EntertainmentService(app, storage, ENTERTAINMENT_CHAT_IDS, *, rng=None)` remains stable.

- [ ] **Step 1: Write lifecycle tests with fake storage**

Assert storage opens before service construction, migration runs before entertainment handlers, failed DB startup prevents polling, finalizer closes exactly once, and writers moderation/Lexicon registrations remain present.

- [ ] **Step 2: Run and confirm failure**

Run: `python -m unittest discover -s tests -p "test_entertainment_lifecycle.py" -v`

Expected: FAIL because `main.py` still directly constructs SQLite storage.

- [ ] **Step 3: Wire factory/migration into `main.py`**

Do not add scheduler/autonomy tasks yet. Preserve current handler ordering.

- [ ] **Step 4: Run all entertainment tests**

Run: `python -m unittest discover -s tests -p "test_entertainment*.py" -v`

Expected: PASS (PostgreSQL tests may SKIP without test URL).

- [ ] **Step 5: Compile**

Run: `python -m compileall -q .`

Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add main.py entertainment tests/test_entertainment*.py
git commit -m "refactor(entertainment): wire database lifecycle"
```

---

### Task 6: Bothost runbook + acceptance

**Files:**
- Modify: `README.md`
- Modify: `.github/workflows/ci.yml` only if healthcheck/connection details need correction after real CI execution.

**Interfaces:**
- Document `ENTERTAINMENT_CHAT_IDS`, `DATABASE_URL`, `ENTERTAINMENT_DB_FALLBACK_SQLITE`, `DATA_DIR`.
- Document safe SQLite -> PostgreSQL cutover and rollback.

- [ ] **Step 1: Write Bothost deployment runbook**

Flow: create PostgreSQL in Bothost -> set `DATABASE_URL` -> keep old SQLite file -> keep fallback disabled -> deploy -> verify PostgreSQL backend/migration logs -> only then treat PostgreSQL as source of truth. Rollback to SQLite must be intentional.

- [ ] **Step 2: Run focused suite**

Run: `python -m unittest discover -s tests -p "test_entertainment*.py" -v`

Expected: PASS except optional local PostgreSQL SKIPs.

- [ ] **Step 3: Compile**

Run: `python -m compileall -q .`

Expected: exit 0.

- [ ] **Step 4: Run full repository suite and compare to baseline**

Run: `python -m unittest discover -s tests -p "test_*.py"`

Acceptance: entertainment work introduces no new failures. Current `main` baseline has six unrelated failures: four Lexicon source-length assertions for `общежитие`/`декорация`, plus two moderation false positives for `обоснуй`/`обоснование`. Record them; do not hide/fix them in this feature plan.

- [ ] **Step 5: Verify focused PostgreSQL CI job**

Expected: PASS.

- [ ] **Step 6: Commit docs/CI fixes**

```bash
git add README.md .github/workflows/ci.yml
git commit -m "docs(entertainment): add bothost postgres runbook"
```

- [ ] **Step 7: Open implementation PR with evidence**

Include backend-selection behavior, migration test counts, topic-isolation proof, entertainment test result, PostgreSQL CI result, compile result, and exact comparison to the six pre-existing failures.

---

## Self-Review Result

- Spec coverage for this phase is complete: package split, storage abstraction, PostgreSQL-first Bothost support, SQLite fallback, topic isolation, v1 migration, lifecycle, CI and deployment runbook are all assigned to tasks.
- Interfaces are consistent across Tasks 2-5: `EntertainmentStorage` owns topic-aware methods; both backends implement the same contract; `main.py` only depends on the factory/protocol.
- The temporary `storage_legacy.py` explicitly prevents the Task 1 refactor from accidentally changing persistence behavior before Task 2 is ready.
- The five highest-risk conditions from the spec are pinned to concrete tests in the Review Focus section.
- Scope remains intentionally smaller than the full v2 spec; autonomy/media/UI/AI are separate follow-on plans rather than mixed into this persistence change.

## Follow-on Plans

After this foundation ships, implement the approved design through separate plans:

1. Autonomy Engine & Budgets.
2. Media Memory & Local Meme Rendering.
3. Quotes, Polls & Local Events.
4. Admin UX v2 with our own `Спокойный / Живой / Активный` modes.
5. Optional AI Provider Layer.

Every follow-on plan must reuse the chat/topic isolation and storage contracts established here.