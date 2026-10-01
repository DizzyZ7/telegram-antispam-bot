# Entertainment Foundation & Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the existing entertainment v1 into a maintainable package and add topic-aware PostgreSQL-first persistence for Bothost without changing the current public entertainment commands or breaking moderation/Lexicon.

**Architecture:** Keep one aiogram process. Move the current monolithic module behind stable package exports, define an async storage protocol, implement SQLite and PostgreSQL backends with identical semantics, and normalize all forum memory by `(chat_id, topic_id)`. Production selects PostgreSQL from `DATABASE_URL`; local development uses SQLite; PostgreSQL failures only fall back when an explicit flag allows it.

**Tech Stack:** Python 3.12, aiogram 3.24+, aiosqlite 0.20+, asyncpg 0.30+, unittest, GitHub Actions PostgreSQL service.

**Spec:** `docs/superpowers/specs/2026-10-01-entertainment-autonomy-v2-design.md`

## Global Constraints

- Primary deployment is Bothost; runtime remains one Python/aiogram process with no Redis, Celery, RQ, or separate worker requirement.
- Initial enabled entertainment chat remains `-1002619489118`; entertainment stays hard-scoped to explicit chat IDs.
- Production database is PostgreSQL when `DATABASE_URL` is configured; SQLite remains the local-development fallback.
- A PostgreSQL connection/configuration failure must not silently create a second SQLite data universe unless `ENTERTAINMENT_DB_FALLBACK_SQLITE=1` is explicitly set.
- Forum context is keyed by `(chat_id, topic_id)`; non-forum/general context normalizes to topic `0`.
- Existing v1 entertainment SQLite memory/settings must be preserved and migration must be idempotent.
- Entertainment must not disable or bypass writers moderation, Lexicon, or unrelated handlers.
- No product wording, setting names, or UX should copy Sglypa; this phase intentionally preserves the current public commands until the later admin-UX plan replaces them with our own controls.
- AI providers, meme rendering, media memory, autonomous phase scoring, polls/events, and the new `/fun` UX are out of scope for this foundation plan and get separate plans after this one lands.

## Review Focus

- **Bad/unsupported `DATABASE_URL`:** startup must fail with a clear configuration error instead of silently selecting SQLite. Covered in Task 3 storage-factory tests.
- **PostgreSQL unavailable at startup:** default behavior is fail-fast; explicit fallback flag may open SQLite. Covered in Task 3 opener tests.
- **Forum topic leakage:** two topics in the same chat must never share `recent_messages()` results by default. Covered in Task 2 storage-contract tests and Task 5 service tests.
- **Repeated v1 migration:** running import twice must not duplicate learned messages or settings. Covered in Task 4 migration tests against SQLite and PostgreSQL.
- **Shutdown during partial startup:** every successfully opened storage resource must be closed exactly once even when a later initialization step fails. Covered in Task 5 lifecycle tests.

---

## File Structure Locked by This Plan

### Package created

- `entertainment/__init__.py` — compatibility exports used by `main.py` and current tests.
- `entertainment/config.py` — allowlist parsing and database configuration from environment.
- `entertainment/models.py` — shared dataclasses and topic normalization.
- `entertainment/generation.py` — existing local text generator, unchanged in behavior.
- `entertainment/service.py` — message eligibility, learning, spontaneous reply logic, admin operations.
- `entertainment/router.py` — aiogram middleware/handlers registration.
- `entertainment/storage/base.py` — `EntertainmentStorage` protocol and shared errors.
- `entertainment/storage/sqlite.py` — SQLite implementation and in-place schema migration.
- `entertainment/storage/postgres.py` — asyncpg implementation.
- `entertainment/storage/factory.py` — backend selection/opening/fallback policy.
- `entertainment/storage/migrations.py` — v1 SQLite -> selected v2 backend import.

### Existing files modified

- `main.py` — imports package exports and opens/closes storage through the factory.
- `requirements.txt` — adds asyncpg.
- `.github/workflows/ci.yml` — adds a focused PostgreSQL integration job without changing the existing full-suite job.
- `README.md` — documents Bothost `DATABASE_URL`, fallback behavior, and migration/runbook.
- `tests/test_entertainment.py` — compatibility + service behavior tests updated for topic-aware APIs.

### New tests

- `tests/test_entertainment_storage_sqlite.py`
- `tests/test_entertainment_storage_postgres.py`
- `tests/test_entertainment_storage_factory.py`
- `tests/test_entertainment_migration.py`
- `tests/test_entertainment_topics.py`

---

### Task 1: Package split with compatibility exports

**Files:**
- Create: `entertainment/__init__.py`
- Create: `entertainment/config.py`
- Create: `entertainment/models.py`
- Create: `entertainment/generation.py`
- Create: `entertainment/service.py`
- Create: `entertainment/router.py`
- Modify: `tests/test_entertainment.py`
- Delete after verification: `entertainment.py`

**Interfaces:**
- Produces: `parse_chat_ids(raw_value: str | None) -> frozenset[int]`
- Produces: `normalize_topic_id(message_thread_id: int | None) -> int`
- Produces: `EntertainmentSettings`
- Produces: `EntertainmentService`
- Produces: `generate_chat_text(messages: list[str], *, rng: random.Random | None = None, max_tokens: int = 30) -> str | None`
- Produces: `register_entertainment_handlers(app: Any, service: EntertainmentService) -> None`
- Compatibility exports from `entertainment.__init__`: `ENTERTAINMENT_CHAT_IDS`, `EntertainmentService`, `EntertainmentSettings`, `EntertainmentStorage`, `generate_chat_text`, `parse_chat_ids`, `register_entertainment_handlers`, `MEMORY_LIMIT`.

- [ ] **Step 1: Add compatibility/import tests before moving code**

Add tests asserting that the current imports from `entertainment` still resolve and that `parse_chat_ids`, `generate_chat_text`, and existing settings defaults keep their current behavior.

- [ ] **Step 2: Run targeted tests and verify the package does not exist yet**

Run: `python -m unittest discover -s tests -p "test_entertainment.py" -v`

Expected before refactor: current tests pass against `entertainment.py`; add a temporary assertion that `entertainment` exposes `__path__` and verify it fails, proving the package split has not happened yet.

- [ ] **Step 3: Move code into focused package files and re-export the current public API**

Keep logic behavior-identical. `config.py` owns allowlist parsing/constants; `models.py` owns dataclasses/topic normalization; `generation.py` owns tokenization/generation; `service.py` owns `EntertainmentService`; `router.py` owns filters/middleware/handler registration. Do not add PostgreSQL or autonomy logic in this task.

- [ ] **Step 4: Remove the temporary package-shape assertion and run compatibility tests**

Run: `python -m unittest discover -s tests -p "test_entertainment.py" -v`

Expected: PASS with the same public imports and current v1 behavior.

- [ ] **Step 5: Compile the application**

Run: `python -m compileall -q .`

Expected: exit code 0.

- [ ] **Step 6: Commit**

```bash
git add entertainment tests/test_entertainment.py
git rm entertainment.py
git commit -m "refactor(entertainment): split module into package"
```

---

### Task 2: Storage protocol and topic-aware SQLite contract

**Files:**
- Create: `entertainment/storage/__init__.py`
- Create: `entertainment/storage/base.py`
- Create: `entertainment/storage/sqlite.py`
- Modify: `entertainment/models.py`
- Modify: `entertainment/service.py`
- Modify: `entertainment/__init__.py`
- Create: `tests/test_entertainment_storage_sqlite.py`
- Create: `tests/test_entertainment_topics.py`
- Modify: `tests/test_entertainment.py`

**Interfaces:**
- Produces protocol `EntertainmentStorage` with async methods:
  - `initialize() -> None`
  - `close() -> None`
  - `get_settings(chat_id: int) -> EntertainmentSettings`
  - `save_settings(chat_id: int, settings: EntertainmentSettings) -> None`
  - `add_message(chat_id: int, topic_id: int, user_id: int, text: str, *, message_id: int | None = None) -> None`
  - `recent_messages(chat_id: int, topic_id: int, limit: int = 900) -> list[str]`
  - `message_count(chat_id: int, topic_id: int | None = None) -> int`
  - `clear_scope(chat_id: int, topic_id: int | None = None) -> int`
- Produces `SQLiteEntertainmentStorage(database_path: Path)` implementing that protocol.
- `EntertainmentService.observe_message()` consumes `normalize_topic_id(message.message_thread_id)` and writes/reads only that topic.
- `topic_id=None` on count/clear means the whole chat; a concrete integer means only that topic.

- [ ] **Step 1: Write failing SQLite contract tests**

Tests must assert: settings are isolated by chat; messages are isolated by both chat and topic; `message_count(chat, None)` aggregates all topics; `clear_scope(chat, topic)` preserves sibling topics; `clear_scope(chat, None)` preserves other chats; a legacy DB missing `topic_id` upgrades in place with existing rows normalized to `0`.

- [ ] **Step 2: Run the new storage tests and verify they fail**

Run: `python -m unittest discover -s tests -p "test_entertainment_storage_sqlite.py" -v`

Expected: FAIL because the storage protocol/package and topic-aware schema are not implemented.

- [ ] **Step 3: Implement the storage protocol and SQLite backend**

Schema requirements for `entertainment_messages`: keep existing `id/chat_id/user_id/text/created_at`, add `topic_id INTEGER NOT NULL DEFAULT 0`, add nullable `message_id`, and index `(chat_id, topic_id, id DESC)`. In-place upgrade must inspect the current schema before `ALTER TABLE` so restart is idempotent.

- [ ] **Step 4: Write failing service-level topic isolation tests**

Use fake `Message` objects with the same `chat.id` but different `message_thread_id`; after `observe_message()`, assert the storage received normalized topic IDs and generation for topic A cannot read topic B.

- [ ] **Step 5: Update `EntertainmentService` to use topic-aware storage**

All learning/generation/forget/status counts use the current topic unless the admin explicitly requests a whole-chat destructive operation. Preserve current command names in this phase.

- [ ] **Step 6: Run SQLite/topic tests**

Run: `python -m unittest discover -s tests -p "test_entertainment*.py" -v`

Expected: PASS for entertainment tests.

- [ ] **Step 7: Commit**

```bash
git add entertainment tests/test_entertainment*.py
git commit -m "feat(entertainment): add topic-aware storage contract"
```

---

### Task 3: PostgreSQL backend and explicit backend selection

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
- Produces `EntertainmentDatabaseConfig.from_env(data_dir: Path) -> EntertainmentDatabaseConfig` with fields `database_url: str | None`, `sqlite_path: Path`, `allow_sqlite_fallback: bool`.
- Produces `PostgresEntertainmentStorage(database_url: str, *, min_pool_size: int = 1, max_pool_size: int = 4)` implementing `EntertainmentStorage`.
- Produces `async open_entertainment_storage(config: EntertainmentDatabaseConfig) -> EntertainmentStorage`.
- Raises `EntertainmentStorageConfigurationError` for unsupported URLs.
- Raises `EntertainmentStorageUnavailableError` when PostgreSQL cannot initialize and fallback is disabled.
- With `DATABASE_URL` absent, `open_entertainment_storage()` opens SQLite.
- With PostgreSQL URL + `ENTERTAINMENT_DB_FALLBACK_SQLITE=1`, a PostgreSQL initialization failure may open SQLite and must log a prominent warning.

- [ ] **Step 1: Add `asyncpg>=0.30` and write failing factory tests**

Cover: no URL -> SQLite; `postgres://` and `postgresql://` -> PostgreSQL; unsupported scheme -> configuration error; PostgreSQL open failure -> unavailable error by default; explicit fallback flag -> SQLite.

- [ ] **Step 2: Run factory tests and verify failure**

Run: `python -m unittest discover -s tests -p "test_entertainment_storage_factory.py" -v`

Expected: FAIL because factory/PostgreSQL types do not exist.

- [ ] **Step 3: Implement config parsing and storage opener**

Environment contract: `DATABASE_URL`, `ENTERTAINMENT_DB_FALLBACK_SQLITE`; default SQLite path is `DATA_DIR / "entertainment.db"`. Never log credentials or the raw PostgreSQL URL.

- [ ] **Step 4: Write PostgreSQL contract tests using `TEST_DATABASE_URL`**

Run the same semantic assertions as SQLite: settings isolation, chat/topic isolation, count/clear semantics, and clean close. Tests skip only when `TEST_DATABASE_URL` is absent.

- [ ] **Step 5: Implement `PostgresEntertainmentStorage` with asyncpg pool**

Use pool size 1..4, command timeout, transactions for schema initialization, and indexes equivalent to SQLite. SQL may use JSONB later, but this phase stores only the v1-compatible settings/messages fields needed by the protocol.

- [ ] **Step 6: Add focused PostgreSQL CI job**

Add a GitHub Actions job with a PostgreSQL service and `TEST_DATABASE_URL`, running only `test_entertainment_storage_postgres.py` plus factory tests. Do not alter the semantics of the existing full-suite job in this task.

- [ ] **Step 7: Run local non-Postgres tests**

Run: `python -m unittest discover -s tests -p "test_entertainment_storage_*.py" -v`

Expected locally without `TEST_DATABASE_URL`: SQLite/factory PASS, PostgreSQL integration tests SKIP cleanly.

- [ ] **Step 8: Commit**

```bash
git add entertainment requirements.txt tests/test_entertainment_storage_*.py .github/workflows/ci.yml
git commit -m "feat(entertainment): add postgres storage backend"
```

---

### Task 4: Idempotent v1 SQLite migration into selected backend

**Files:**
- Create: `entertainment/storage/migrations.py`
- Modify: `entertainment/storage/base.py`
- Modify: `entertainment/storage/sqlite.py`
- Modify: `entertainment/storage/postgres.py`
- Create: `tests/test_entertainment_migration.py`

**Interfaces:**
- Produces `async migrate_v1_sqlite_if_needed(source_path: Path, target: EntertainmentStorage) -> MigrationReport`.
- Produces `MigrationReport(settings_imported: int, messages_imported: int, skipped: int, already_applied: bool)`.
- Storage backends expose migration primitives needed by the migrator, including idempotent import keyed by original v1 row ID (`legacy_source_id`).
- Existing v1 rows receive `topic_id=0`; source SQLite file is never deleted automatically.

- [ ] **Step 1: Create a real v1-shaped SQLite fixture in tests and write failing migration tests**

Fixture uses the current two v1 tables and columns. Assertions: settings/text survive import; topic becomes `0`; second import inserts zero duplicates; source file remains; partial target data does not overwrite newer target settings incorrectly.

- [ ] **Step 2: Run migration tests and verify failure**

Run: `python -m unittest discover -s tests -p "test_entertainment_migration.py" -v`

Expected: FAIL because migrator/import primitives are missing.

- [ ] **Step 3: Implement migration metadata and idempotent import**

Add nullable `legacy_source_id` to v2 message schema and a unique partial/compatible constraint or equivalent conflict-safe logic. Track migration version in an `ent_schema_migrations` table. Never remove or rename the source file.

- [ ] **Step 4: Run migration tests against SQLite**

Run: `python -m unittest discover -s tests -p "test_entertainment_migration.py" -v`

Expected: PASS for SQLite target.

- [ ] **Step 5: Extend PostgreSQL integration test to exercise the same v1 import twice**

With `TEST_DATABASE_URL`, assert the second run returns `already_applied=True` or imports zero rows and total row count is unchanged.

- [ ] **Step 6: Commit**

```bash
git add entertainment/storage tests/test_entertainment_migration.py tests/test_entertainment_storage_postgres.py
git commit -m "feat(entertainment): migrate v1 memory idempotently"
```

---

### Task 5: Wire storage lifecycle into `main.py` and preserve coexistence

**Files:**
- Modify: `main.py`
- Modify: `entertainment/__init__.py`
- Modify: `entertainment/service.py`
- Modify: `tests/test_entertainment.py`
- Create: `tests/test_entertainment_lifecycle.py`

**Interfaces:**
- `main.py` consumes `EntertainmentDatabaseConfig.from_env(RUNTIME_DATA_DIR)` and `await open_entertainment_storage(config)`.
- After opening the selected backend, `main.py` runs `migrate_v1_sqlite_if_needed(RUNTIME_DATA_DIR / "entertainment.db", storage)` only when the selected target is not that exact in-place SQLite database.
- `EntertainmentService(app, storage, ENTERTAINMENT_CHAT_IDS, *, rng=None)` remains the construction API.
- Shutdown calls `await storage.close()` exactly once for every successfully opened storage.

- [ ] **Step 1: Write lifecycle tests with fake storage objects**

Assert: successful startup constructs service from opened storage; PostgreSQL migration call precedes handler registration; initialization failure does not start bot polling; finalizer closes opened storage; unrelated moderation registration remains present.

- [ ] **Step 2: Run lifecycle tests and verify failure**

Run: `python -m unittest discover -s tests -p "test_entertainment_lifecycle.py" -v`

Expected: FAIL because `main.py` still constructs the old concrete SQLite storage directly.

- [ ] **Step 3: Update `main.py` to use the factory and migration flow**

Keep the existing writers moderation and Lexicon registration order intact. Do not add autonomous scheduler tasks in this plan.

- [ ] **Step 4: Verify chat/topic service behavior**

Run: `python -m unittest discover -s tests -p "test_entertainment*.py" -v`

Expected: all entertainment tests PASS (PostgreSQL integration may SKIP if no test DB URL).

- [ ] **Step 5: Compile application**

Run: `python -m compileall -q .`

Expected: exit code 0.

- [ ] **Step 6: Commit**

```bash
git add main.py entertainment tests/test_entertainment*.py
git commit -m "refactor(entertainment): wire database lifecycle"
```

---

### Task 6: Bothost deployment documentation and phase acceptance

**Files:**
- Modify: `README.md`
- Modify: `.github/workflows/ci.yml` only if verification reveals CI-specific connection/healthcheck issues

**Interfaces:**
- Documents exact production environment variables: `ENTERTAINMENT_CHAT_IDS`, `DATABASE_URL`, `ENTERTAINMENT_DB_FALLBACK_SQLITE`, `DATA_DIR`.
- Documents safe cutover from current SQLite to Bothost PostgreSQL and rollback path.

- [ ] **Step 1: Add Bothost runbook**

Document: create PostgreSQL in Bothost; copy its connection string into `DATABASE_URL`; keep the existing SQLite file during first deploy; leave fallback disabled in production; verify startup logs show PostgreSQL backend and migration counts; only after verification treat PostgreSQL as source of truth; rollback by removing `DATABASE_URL` only when intentionally returning to the untouched SQLite source.

- [ ] **Step 2: Run focused entertainment suite**

Run: `python -m unittest discover -s tests -p "test_entertainment*.py" -v`

Expected: PASS except PostgreSQL tests may SKIP locally when `TEST_DATABASE_URL` is absent.

- [ ] **Step 3: Run compilation**

Run: `python -m compileall -q .`

Expected: exit code 0.

- [ ] **Step 4: Run full repository suite and compare to baseline**

Run: `python -m unittest discover -s tests -p "test_*.py"`

Expected acceptance for this phase: no new failures introduced by entertainment work. Current `main` baseline has six known unrelated failures: four Lexicon static-source length assertions for `общежитие`/`декорация`, and two writers-moderation false positives around `обоснуй`/`обоснование`. Record exact output; do not suppress or rewrite those tests inside this feature branch.

- [ ] **Step 5: Verify PostgreSQL GitHub Actions job**

Expected: focused PostgreSQL job PASS. Existing full-suite job may still report the documented six baseline failures until they are fixed in a separate change.

- [ ] **Step 6: Commit documentation/CI adjustments**

```bash
git add README.md .github/workflows/ci.yml
git commit -m "docs(entertainment): add bothost postgres runbook"
```

- [ ] **Step 7: Open implementation PR with phase report**

PR report must include: backend-selection behavior, migration counts from test fixture, topic isolation proof, targeted test results, PostgreSQL CI result, compile result, and explicit comparison against the six pre-existing full-suite failures.

---

## Follow-on Plans After This Foundation

This plan deliberately stops after a production-ready persistence/topic foundation. The approved design then proceeds through separate executable plans in this order:

1. **Autonomy Engine & Budgets** — conversation phases, candidate scoring, quiet hours, per-topic/hourly budgets, scheduler lifecycle, action telemetry.
2. **Media Memory & Local Meme Rendering** — Telegram `file_id` memory, Pillow renderer, cache/TTL, quote cards, top/bottom memes, demotivator/two-panel formats.
3. **Quotes, Polls & Local Events** — callbacks, archive resurfacing, playful polls/events, novelty/repetition protection.
4. **Admin UX v2** — replace raw v1 controls with our own `Спокойный / Живой / Активный` presets, feature toggles, topic controls, memory/status UI.
5. **Optional AI Provider Layer** — provider interface and richer generation only after local mode is stable; no hard dependency.

Each follow-on plan must preserve the same chat/topic isolation and database contracts established here.