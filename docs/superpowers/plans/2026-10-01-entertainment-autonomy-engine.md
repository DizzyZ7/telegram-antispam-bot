# Entertainment Autonomy Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the V1 visible random-frequency model with our own state-driven autonomous behavior engine that understands forum-topic activity, applies `Спокойный / Живой / Активный` behavior modes, persists action history, and participates without spamming or copying source messages.

**Architecture:** Keep the existing single aiogram process and storage abstraction. Add pure activity/phase/scoring modules that are deterministic under tests, persist only the minimum action/state data needed for restart-safe budgets, and let `EntertainmentService` ask the engine for at most one eligible text action per evaluation. A single bounded supervisor task handles quiet/cooldown opportunities; message-triggered evaluation remains cheap and topic-local.

**Tech Stack:** Python 3.12, aiogram 3.24+, aiosqlite 0.20+, asyncpg 0.30+, unittest, asyncio, existing PostgreSQL/SQLite storage contract.

**Spec:** `docs/superpowers/specs/2026-10-01-entertainment-autonomy-v2-design.md`

## Global Constraints

- Product language is ours: no user-facing `лень`, copied labels, or copied UX.
- Behavior modes are exactly `calm`, `alive`, `active`, displayed as `Спокойный`, `Живой`, `Активный`.
- Entertainment remains hard-scoped to configured chat IDs and topic-local by `(chat_id, topic_id)`.
- One aiogram process only; no Redis, Celery, RQ, worker service, or unbounded task-per-message behavior.
- PostgreSQL and SQLite must expose identical autonomy semantics through the shared storage contract.
- Autonomous sends are conservative: at most one selected action per evaluation cycle and never two autonomous messages without intervening human activity.
- `Живой` default: at most 2 autonomous sends per rolling 30 minutes per topic.
- Existing moderation/Lexicon/other bot features remain independent.
- Generated text must pass novelty checks and cannot silently replay a remembered message as original speech.
- Media memory, Pillow memes/cards, polls/events, AI providers, and image generation are out of scope for this plan and follow after the decision engine is stable.

## Review Focus

- **Burst traffic / PEAK:** high chat activity should reduce autonomous chatter rather than amplify it. Covered in Task 2 phase tests and Task 4 selection tests.
- **Restart inside a budget window:** persisted action history must still block an otherwise eligible action after process restart. Covered in Task 3 storage tests and Task 5 service tests.
- **Forum topic isolation:** activity/action history from topic A must not influence topic B. Covered in Tasks 2–5.
- **No human activity after bot action:** supervisor must never emit a second autonomous message until at least one eligible human message appears. Covered in Task 4 budget tests and Task 6 supervisor tests.
- **Old V1 numeric settings:** existing databases must upgrade safely; old `laziness` values must not remain user-facing or control v2 decisions. Covered in Task 1 migration tests and Task 7 router/UI tests.

---

## File Structure Locked by This Plan

### New files

- `entertainment/autonomy.py` — pure phase derivation, policy lookup, candidate scoring, decision selection.
- `entertainment/context.py` — activity snapshot dataclasses and topic-context assembly.
- `entertainment/novelty.py` — exact/n-gram/recent-output rejection helpers.
- `entertainment/scheduler.py` — one bounded supervisor task owned by service lifecycle.
- `tests/test_entertainment_autonomy.py`
- `tests/test_entertainment_action_storage.py`
- `tests/test_entertainment_novelty.py`
- `tests/test_entertainment_scheduler.py`
- `tests/test_entertainment_behavior_ui.py`

### Existing files modified

- `entertainment/models.py` — behavior mode/settings/action dataclasses.
- `entertainment/storage/base.py` — activity/action-history contract.
- `entertainment/storage/sqlite.py` — settings schema upgrade + `ent_actions` + activity queries.
- `entertainment/storage/postgres.py` — same semantics using PostgreSQL.
- `entertainment/service.py` — replace per-message percentage logic with engine decisions.
- `entertainment/router.py` — new behavior panel callbacks and removal of visible numeric-frequency controls.
- `entertainment/runtime.py` — supervisor lifecycle wiring if needed.
- `entertainment/__init__.py` — stable exports.
- `main.py` — start/stop supervisor with bot lifecycle.
- `README.md` — document behavior modes and operational limits.
- `.github/workflows/ci.yml` — extend focused PostgreSQL tests to action-history contract.

---

### Task 1: Behavior settings model and schema upgrade

**Files:**
- Modify: `entertainment/models.py`
- Modify: `entertainment/storage/base.py`
- Modify: `entertainment/storage/sqlite.py`
- Modify: `entertainment/storage/postgres.py`
- Modify: `tests/test_entertainment_storage_sqlite.py`
- Modify: `tests/test_entertainment_storage_postgres.py`

**Interfaces:**
- Produce enum `BehaviorMode(str, Enum)` values: `CALM="calm"`, `ALIVE="alive"`, `ACTIVE="active"`.
- Replace v2 decision input settings with `EntertainmentSettings(enabled: bool = True, behavior_mode: BehaviorMode = BehaviorMode.ALIVE, quiet_hours_start: int | None = None, quiet_hours_end: int | None = None, timezone: str = "Europe/Moscow", autonomous_text_enabled: bool = True)`.
- Storage continues `get_settings(chat_id)` / `save_settings(chat_id, settings)` with identical semantics on SQLite/PostgreSQL.
- Existing V1 columns may remain physically for compatibility during this release, but v2 decision code must not read `laziness` or `cooldown_seconds`.

- [ ] **Step 1: Write failing settings/schema tests**

Assert default mode is `ALIVE`; SQLite/PostgreSQL round-trip all new fields; reopening an existing V1-shaped settings table upgrades without losing `enabled`; legacy numeric columns do not determine returned v2 mode.

- [ ] **Step 2: Run focused storage tests and verify RED**

Run: `python -m unittest tests.test_entertainment_storage_sqlite tests.test_entertainment_storage_postgres -v`

Expected without PostgreSQL env: new SQLite assertions FAIL and PostgreSQL tests SKIP; in PostgreSQL CI both backends must exercise the new contract.

- [ ] **Step 3: Implement models and idempotent schema upgrades**

SQLite adds missing columns with defaults. PostgreSQL uses `ADD COLUMN IF NOT EXISTS`. Invalid stored mode values normalize to `BehaviorMode.ALIVE` instead of crashing startup.

- [ ] **Step 4: Run storage tests GREEN**

Run: `python -m unittest tests.test_entertainment_storage_sqlite -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add entertainment/models.py entertainment/storage tests/test_entertainment_storage_*.py
git commit -m "feat(entertainment): add behavior modes"
```

---

### Task 2: Topic activity snapshots and conversation phases

**Files:**
- Create: `entertainment/context.py`
- Create: `entertainment/autonomy.py`
- Modify: `entertainment/storage/base.py`
- Modify: `entertainment/storage/sqlite.py`
- Modify: `entertainment/storage/postgres.py`
- Create: `tests/test_entertainment_autonomy.py`
- Extend: `tests/test_entertainment_storage_sqlite.py`
- Extend: `tests/test_entertainment_storage_postgres.py`

**Interfaces:**
- Produce `ActivitySnapshot(chat_id: int, topic_id: int, messages_1m: int, messages_5m: int, messages_previous_5m: int, messages_15m: int, active_users_5m: int, seconds_since_human: float | None)`.
- Storage produces `activity_snapshot(chat_id: int, topic_id: int, *, now: int) -> ActivitySnapshot` using `entertainment_messages.created_at`.
- Produce enum `ConversationPhase`: `QUIET`, `WARMING_UP`, `ACTIVE`, `PEAK`, `COOLDOWN`.
- Produce `derive_phase(snapshot: ActivitySnapshot) -> ConversationPhase` with exact precedence:
  1. `QUIET` if `messages_5m == 0` and `messages_15m <= 1`.
  2. `COOLDOWN` if `1 <= messages_5m <= 4` and `messages_previous_5m >= 8`.
  3. `PEAK` if `messages_5m >= 12` and `active_users_5m >= 3`.
  4. `ACTIVE` if `messages_5m >= 5`.
  5. otherwise `WARMING_UP`.

- [ ] **Step 1: Write failing pure phase tests for every boundary**

Include exact boundaries at 0/1/4/5/11/12 messages and the COOLDOWN precedence case.

- [ ] **Step 2: Write failing backend activity-query tests**

Seed messages with controlled timestamps/users in two topics and assert every rolling counter plus topic isolation.

- [ ] **Step 3: Run RED**

Run: `python -m unittest tests.test_entertainment_autonomy tests.test_entertainment_storage_sqlite -v`

Expected: FAIL because snapshot/phase APIs do not exist.

- [ ] **Step 4: Implement bounded activity queries and pure phase derivation**

No query scans older than 15 minutes for snapshot calculation. Indexes must support `(chat_id, topic_id, created_at)`.

- [ ] **Step 5: Run GREEN**

Run: `python -m unittest tests.test_entertainment_autonomy tests.test_entertainment_storage_sqlite -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add entertainment/context.py entertainment/autonomy.py entertainment/storage tests/test_entertainment_autonomy.py tests/test_entertainment_storage_*.py
git commit -m "feat(entertainment): derive topic conversation phases"
```

---

### Task 3: Persisted action history and rolling budgets

**Files:**
- Modify: `entertainment/models.py`
- Modify: `entertainment/storage/base.py`
- Modify: `entertainment/storage/sqlite.py`
- Modify: `entertainment/storage/postgres.py`
- Create: `tests/test_entertainment_action_storage.py`
- Extend: `tests/test_entertainment_storage_postgres.py`

**Interfaces:**
- Produce enum `EntertainmentActionType`: `CONTEXTUAL_REPLY`, `REMIXED_PHRASE`, `MEMORY_CALLBACK`.
- Produce dataclass `EntertainmentActionRecord(id: int | None, chat_id: int, topic_id: int, action_type: EntertainmentActionType, trigger_message_id: int | None, created_at: int, metadata: dict[str, object])`.
- Storage produces:
  - `record_action(record: EntertainmentActionRecord) -> int`
  - `recent_actions(chat_id: int, topic_id: int, *, since: int, limit: int = 20) -> list[EntertainmentActionRecord]`
  - `human_messages_since(chat_id: int, topic_id: int, since: int) -> int`
- New table `ent_actions` is indexed by `(chat_id, topic_id, created_at DESC)`.

- [ ] **Step 1: Write failing storage contract tests**

Assert topic isolation, chronological ordering, restart persistence, JSON metadata round-trip, and human-message count since last action.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_entertainment_action_storage -v`

Expected: FAIL because action APIs/table do not exist.

- [ ] **Step 3: Implement both backends**

SQLite stores metadata as compact JSON text; PostgreSQL uses JSONB. Unknown action types in corrupt historical rows are skipped/logged instead of crashing the bot.

- [ ] **Step 4: Run SQLite GREEN and PostgreSQL CI contract**

Run: `python -m unittest tests.test_entertainment_action_storage -v`

Expected locally: PASS for SQLite-focused cases; PostgreSQL equivalents run in the service job.

- [ ] **Step 5: Commit**

```bash
git add entertainment/models.py entertainment/storage tests/test_entertainment_action_storage.py tests/test_entertainment_storage_postgres.py
git commit -m "feat(entertainment): persist autonomous action history"
```

---

### Task 4: Policies, eligibility, scoring and anti-spam decision engine

**Files:**
- Modify: `entertainment/autonomy.py`
- Create: `entertainment/novelty.py`
- Create: `tests/test_entertainment_novelty.py`
- Extend: `tests/test_entertainment_autonomy.py`

**Interfaces:**
- Produce `BehaviorPolicy` with exact defaults:
  - `CALM`: `max_actions_30m=1`, `min_gap_seconds=900`, `min_human_messages_between=8`.
  - `ALIVE`: `max_actions_30m=2`, `min_gap_seconds=360`, `min_human_messages_between=4`.
  - `ACTIVE`: `max_actions_30m=3`, `min_gap_seconds=180`, `min_human_messages_between=2`.
- Produce `ActionCandidate(action_type, relevance: float, novelty: float, annoyance_cost: float, trigger_message_id: int | None)`.
- Produce `DecisionContext(settings, phase, activity, recent_actions, human_messages_since_last_action, memory_count, quiet_hours_active)`.
- Produce `select_action(context: DecisionContext, candidates: list[ActionCandidate], *, rng: random.Random) -> ActionCandidate | None`.
- Eligibility invariants:
  - disabled or autonomous text disabled -> None;
  - quiet hours -> None;
  - 30-minute budget exhausted -> None;
  - min gap not reached -> None;
  - zero human messages since last autonomous action -> None;
  - `PEAK` rejects non-direct autonomous candidates;
  - `ALIVE` never exceeds 2/30m.
- Candidate score is `0.55*relevance + 0.35*novelty - 0.45*annoyance_cost + jitter`, where `jitter` is bounded to `[0, 0.10]`; candidate must score at least `0.45`.
- Produce `is_novel_generated_text(candidate: str, sources: list[str], recent_outputs: list[str]) -> bool`; reject exact normalized replay, recent bot replay, and any candidate whose contiguous 4-gram overlap covers >= 70% of its word 4-grams from one source.

- [ ] **Step 1: Write novelty RED tests**

Cover exact replay, punctuation/case-normalized replay, near-copy 4-gram case, recent bot replay, and a genuinely recombined sentence.

- [ ] **Step 2: Write decision RED tests for every anti-spam invariant and policy boundary**

Use seeded `random.Random` so tests are deterministic.

- [ ] **Step 3: Run RED**

Run: `python -m unittest tests.test_entertainment_novelty tests.test_entertainment_autonomy -v`

Expected: FAIL on missing APIs.

- [ ] **Step 4: Implement pure novelty/policy/scoring logic**

No database or Telegram imports in these pure modules.

- [ ] **Step 5: Run GREEN**

Run: `python -m unittest tests.test_entertainment_novelty tests.test_entertainment_autonomy -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add entertainment/autonomy.py entertainment/novelty.py tests/test_entertainment_autonomy.py tests/test_entertainment_novelty.py
git commit -m "feat(entertainment): add autonomous decision engine"
```

---

### Task 5: Integrate engine into message-driven service flow

**Files:**
- Modify: `entertainment/service.py`
- Modify: `entertainment/generation.py`
- Extend: `tests/test_entertainment.py`
- Extend: `tests/test_entertainment_topics.py`
- Create: `tests/test_entertainment_service_autonomy.py`

**Interfaces:**
- `EntertainmentService.observe_message(message)` remains the learning entrypoint.
- After storing an eligible human message, service assembles `DecisionContext` for the same topic and evaluates message-triggered candidates.
- Direct/contextual reply candidates may be considered during `ACTIVE`; ordinary spontaneous remix candidates are preferred during `COOLDOWN` and `QUIET`; `PEAK` mostly observes.
- Service records `ent_actions` only after Telegram send succeeds.
- Generated text must pass `is_novel_generated_text`; failed novelty retries at most 3 times, then emits nothing.
- No old `rng.randrange(100) < laziness` path remains.

- [ ] **Step 1: Write failing service tests**

Assert: topic A context never reads topic B; PEAK suppresses ordinary spontaneous text; successful send persists action; failed Telegram send does not persist action; restart action history blocks a second send; novelty rejection produces no copy-paste.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_entertainment_service_autonomy -v`

Expected: FAIL because service still uses V1 random logic.

- [ ] **Step 3: Refactor service to use snapshot -> candidates -> engine -> send -> record**

Keep normal message-handler latency bounded: no sleeps and no image work in `observe_message`.

- [ ] **Step 4: Run all entertainment tests**

Run: `python -m unittest discover -s tests -p "test_entertainment*.py" -v`

Expected: PASS locally except PostgreSQL-only tests that skip without `TEST_DATABASE_URL`.

- [ ] **Step 5: Commit**

```bash
git add entertainment/service.py entertainment/generation.py tests/test_entertainment*.py
git commit -m "feat(entertainment): drive replies from chat state"
```

---

### Task 6: One bounded supervisor for quiet/cooldown opportunities

**Files:**
- Create: `entertainment/scheduler.py`
- Modify: `entertainment/service.py`
- Modify: `entertainment/runtime.py`
- Modify: `main.py`
- Create: `tests/test_entertainment_scheduler.py`
- Extend: `tests/test_entertainment_runtime.py`

**Interfaces:**
- Produce `EntertainmentSupervisor(service, *, interval_seconds: float = 60.0)` with `start() -> None` and `stop() -> Awaitable[None]`.
- Exactly one supervisor task per service instance.
- Each tick asks storage/service only for enabled scopes with human activity in the previous 30 minutes; no full historical scan.
- Supervisor never sends if no human message occurred after the last autonomous action.
- `stop()` is idempotent and waits for task cancellation/exit cleanly.

- [ ] **Step 1: Write RED lifecycle tests**

Assert one task only, repeated `start()` does not duplicate it, repeated `stop()` is safe, cancellation does not leak, and a quiet scope with no post-action human message produces no send.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_entertainment_scheduler tests.test_entertainment_runtime -v`

Expected: FAIL because supervisor does not exist.

- [ ] **Step 3: Implement bounded supervisor and lifecycle wiring**

Start after storage/service initialization; stop before storage close in `main.py`.

- [ ] **Step 4: Run GREEN**

Run: `python -m unittest tests.test_entertainment_scheduler tests.test_entertainment_runtime -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add entertainment/scheduler.py entertainment/service.py entertainment/runtime.py main.py tests/test_entertainment_scheduler.py tests/test_entertainment_runtime.py
git commit -m "feat(entertainment): add autonomous supervisor"
```

---

### Task 7: Own-product `/fun` behavior UX and remove visible numeric-frequency controls

**Files:**
- Modify: `entertainment/router.py`
- Modify: `entertainment/service.py`
- Modify: `entertainment/__init__.py`
- Create: `tests/test_entertainment_behavior_ui.py`
- Modify: `README.md`

**Interfaces:**
- `/fun` panel shows current mode and buttons: `🌙 Спокойный`, `✨ Живой`, `⚡ Активный`, plus `🧠 Память`, `⏸ Выключить/▶️ Включить`.
- Callback prefix for modes: `fun:mode:calm|alive|active`.
- Admin-only mode mutations.
- Do not register `/fun_laziness` as an active numeric configuration command.
- If the old command is retained for one compatibility release, it may only answer with a migration hint to `/fun`; it must not change numeric behavior.
- `/fun_cooldown` is likewise no longer part of normal product UX; engine policies own rate limits.
- Existing `/fun_generate` remains as a manual debug/entertainment action but still applies novelty filtering.

- [ ] **Step 1: Write failing UI/router tests**

Assert mode buttons/callbacks, admin permissions, persistence, and absence of user-facing `лень` text in panel output.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_entertainment_behavior_ui -v`

Expected: FAIL against V1 panel/router.

- [ ] **Step 3: Implement new panel and compatibility hints**

All displayed terminology follows our product vocabulary.

- [ ] **Step 4: Update README with modes, budgets and quiet behavior**

Do not document copied/numeric controls as active features.

- [ ] **Step 5: Run all entertainment tests and compileall**

Run: `python -m compileall -q . && python -m unittest discover -s tests -p "test_entertainment*.py" -v`

Expected: compile PASS; entertainment tests PASS except service-backed PostgreSQL tests skipped locally when `TEST_DATABASE_URL` is absent.

- [ ] **Step 6: Commit**

```bash
git add entertainment README.md tests/test_entertainment_behavior_ui.py
git commit -m "feat(entertainment): add own behavior-mode UX"
```

---

### Task 8: Final cross-backend verification and rollout guard

**Files:**
- Modify if required by findings: `.github/workflows/ci.yml`
- Modify if required by findings: `README.md`
- No product code changes unless verification exposes a defect.

**Interfaces:**
- PostgreSQL job must run the behavior settings, action history, activity snapshots, migration, and factory contract.
- Existing full suite remains the comparison baseline; this branch must add no new failures beyond already documented unrelated baseline failures.

- [ ] **Step 1: Run focused PostgreSQL GitHub Actions job on the final head**

Expected: all Entertainment PostgreSQL tests PASS.

- [ ] **Step 2: Run full unittest discovery on the final head**

Run: `python -m unittest discover -s tests -p "test_*.py"`

Expected: no new failures relative to the documented six pre-existing baseline failures unless those baseline failures have independently been fixed in `main` meanwhile.

- [ ] **Step 3: Inspect branch diff against `main`**

Confirm only Autonomy Engine scope, tests and documentation changed; no accidental Lexicon/moderation edits.

- [ ] **Step 4: Update PR verification note and squash-merge after evidence is fresh**

Use the exact verified head SHA in the PR note and merge expectation.

---

## Plan Self-Review

- Spec coverage for this subproject: behavior modes, per-topic activity, phases, scoring, anti-spam budgets, persisted restart-safe action history, novelty protection, message-driven decisions, bounded supervisor, own-product UX, PostgreSQL/SQLite parity.
- Explicitly deferred to the next plan: media capture, Telegram `file_id` reuse, stickers/GIF callbacks, Pillow rendering, memes/cards, polls/events, optional AI providers.
- Type consistency checked across tasks: settings -> snapshot -> phase -> decision context -> candidate -> action record.
- Review Focus cases each have an owning test task.
- No task requires Redis, extra worker processes, or a live PostgreSQL service for the ordinary local suite.
