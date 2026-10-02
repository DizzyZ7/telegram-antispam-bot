# Entertainment Culture Memory Phase C Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add bootstrap learning for commands/other-bot culture and context-aware autonomous reuse of remembered stickers, photos and animations without increasing Entertainment action frequency.

**Architecture:** Extend the canonical `MemoryEvent` provenance model, keep activity/autonomy strictly human-text-driven, reuse Phase B bounded recent+historical snapshots, and add a pure `media_culture.py` scorer. `EntertainmentService` remains orchestration-only: it decides whether an action is allowed, builds one bounded snapshot, optionally realizes that slot as `MEMORY_CALLBACK`, sends one Telegram media item by `file_id`, and falls back to Phase B text without creating a second action.

**Tech Stack:** Python 3.12, aiogram 3.x, SQLite/aiosqlite, PostgreSQL/asyncpg, unittest, existing Culture Memory/Autonomy v2 modules.

**Spec:** `docs/superpowers/specs/2026-10-02-entertainment-culture-memory-phase-c-design.md`

## Global Constraints

- Bootstrap threshold: `ENTERTAINMENT_BOOTSTRAP_TEXT_EVENT_THRESHOLD=10000` per `chat_id + topic_id`, counted from canonical `TEXT + EMOJI` events.
- Media repeat cooldown: `ENTERTAINMENT_MEDIA_REPEAT_COOLDOWN_SECONDS=21600` (6 hours) per `chat_id + topic_id + file_unique_id`.
- Post-bootstrap command/other-bot generation multiplier: `0.40` of normal source weight.
- Never learn from the Entertainment bot itself.
- Other-bot events and commands enrich memory but never increment human activity/autonomy cadence.
- Forwarded media is never eligible for autonomous reuse.
- No external LLM/vision/GPU dependency and no permanent media binary downloads.
- Media reuse consumes the same existing BehaviorMode action budget as text; no separate media budget.
- Photos have the strictest contextual threshold; no random photo resurfacing.
- Preserve strict `chat_id + topic_id` isolation and existing privacy commands.
- Store only safe media diagnostics in action metadata; never store neighboring raw corpus text there.

## Review Focus

1. **Self-bot loop prevention:** a message authored by the running Entertainment bot must not enter canonical Culture Memory even when it looks like `/spawn` or media. Task 2 pins this.
2. **Existing rows after schema migration:** Phase A/B rows without provenance columns must deserialize as `sender_is_bot=False`, `is_command=False` on both SQLite and PostgreSQL. Task 1 pins this.
3. **Bot flood isolation:** hundreds of commands/messages from another bot must not increase human activity counters or make autonomous actions eligible earlier. Task 2 pins this.
4. **Media send failure:** Telegram rejection/expired `file_id` must not record a successful `MEMORY_CALLBACK`, and text fallback may produce at most one visible successful action. Task 6 pins this.
5. **Photo safety under weak context:** a photo with only generic neighboring words must stay below the photo threshold even if it is recent/frequent. Task 4 pins this.

---

### Task 1: Provenance Fields + Backward-Compatible Storage Migration

**Files:**
- Modify: `entertainment/models.py`
- Modify: `entertainment/memory.py`
- Modify: `entertainment/storage/retention.py`
- Modify: `entertainment/storage/base.py`
- Test: `tests/test_entertainment_memory.py`
- Test: `tests/test_entertainment_culture_memory_sqlite.py`
- Test: `tests/test_entertainment_culture_memory_postgres.py`

**Interfaces:**
- Produces: `MemoryEvent.sender_is_bot: bool`, `MemoryEvent.is_command: bool`.
- Produces: command classification for `/spawn`, `/spawn args`, `/spawn@OtherBot args`.
- Storage readers/writers must round-trip both fields and default existing rows to `False`.

- [ ] **Step 1: Write failing model/classifier tests**

Add tests asserting:
- `/spawn`, `/spawn boss`, `/spawn@RaidBot boss` => `event_type=TEXT`, `is_command=True`;
- normal text => `is_command=False`;
- another bot sender => `sender_is_bot=True`;
- human sender => `sender_is_bot=False`.

- [ ] **Step 2: Run focused classifier tests and confirm RED**

Run: `python -m unittest tests.test_entertainment_memory -v`
Expected: FAIL because provenance fields/classification do not exist yet.

- [ ] **Step 3: Add provenance fields and classifier support**

Implement exact model fields:

```python
sender_is_bot: bool = False
is_command: bool = False
```

Command recognition must inspect the first non-space token only and accept Telegram command usernames after `@`.

- [ ] **Step 4: Write failing SQLite/PostgreSQL migration parity tests**

Assert new rows round-trip both fields; simulate/read pre-Phase-C rows and assert both values default `False`.

- [ ] **Step 5: Run storage tests and confirm RED**

Run: `python -m unittest tests.test_entertainment_culture_memory_sqlite tests.test_entertainment_culture_memory_postgres -v`
Expected: FAIL until schema/read-write paths include provenance fields.

- [ ] **Step 6: Implement idempotent schema migration and row mapping**

SQLite/PostgreSQL initialize paths must add boolean columns safely to existing `ent_memory_events`; update `_EVENT_FIELDS` and serialization/deserialization consistently.

- [ ] **Step 7: Run focused tests and confirm GREEN**

Run the commands from Steps 2 and 5. Expected: PASS for all Phase C provenance tests.

- [ ] **Step 8: Commit**

Commit message: `feat: add Culture Memory provenance fields`

---

### Task 2: Bootstrap Ingestion Without Activity Inflation

**Files:**
- Modify: `entertainment/config.py`
- Modify: `entertainment/service.py`
- Modify: `entertainment/memory.py`
- Test: `tests/test_entertainment_bootstrap_learning.py`
- Test: `tests/test_entertainment_culture_memory_service.py`
- Test: `tests/test_entertainment_autonomy.py`

**Interfaces:**
- Produces config constant `BOOTSTRAP_TEXT_EVENT_THRESHOLD: int` from `ENTERTAINMENT_BOOTSTRAP_TEXT_EVENT_THRESHOLD`, default `10000`.
- `observe_message()` stores supported human events plus supported other-bot events, but excludes the running Entertainment bot id.
- Human privacy preference applies only to human senders; other-bot culture bypasses human opt-out rows.
- Legacy human activity path remains unchanged and must not count commands, media-only events, or other-bot events.

- [ ] **Step 1: Write failing ingestion/activity tests**

Pin these behaviors:
- another bot `/spawn` is stored canonically;
- another bot sticker/photo/animation is stored canonically;
- own bot message is rejected before `add_event`;
- human `/spawn` is stored even though it does not enter legacy activity;
- opted-out human `/spawn`/media is not stored;
- bot events do not call `add_message`, do not increment `human_messages_since`, and do not make an otherwise-ineligible autonomy decision eligible.

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `python -m unittest tests.test_entertainment_bootstrap_learning tests.test_entertainment_culture_memory_service tests.test_entertainment_autonomy -v`
Expected: FAIL on current human-only sender/command gates.

- [ ] **Step 3: Implement bootstrap ingestion gates**

Refactor sender eligibility so canonical Culture Memory can ingest humans + other bots while activity remains on the existing human text path. Explicitly reject `from_user.id == app.bot.id`.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run Step 2 command. Expected: PASS for new Phase C cases and existing autonomy tests.

- [ ] **Step 5: Commit**

Commit message: `feat: learn commands and other bot culture`

---

### Task 3: Bootstrap-Aware Text/Emoji Weighting

**Files:**
- Modify: `entertainment/culture.py`
- Modify: `entertainment/service.py`
- Test: `tests/test_entertainment_bootstrap_weighting.py`
- Test: `tests/test_entertainment_culture_generation_service.py`

**Interfaces:**
- Extend `build_culture_context(...)` with:

```python
textual_event_count: int = 0
bootstrap_threshold: int = 10_000
```

- Add pure helper behavior: ordinary human text keeps existing weights; before threshold human commands and other-bot text use normal base weight; at/after threshold they use exactly `0.40 * base_weight`, rounded to a minimum effective weight of 1 when material is represented by integer duplication.
- Service obtains `MemoryCounts` once for the current topic and passes `counts.text + counts.emoji` into Culture context construction.

- [ ] **Step 1: Write failing weighting tests**

Assert:
- at 9,999 textual events, `/spawn` and other-bot text receive normal bootstrap representation;
- at 10,000, the same sources remain present but are reduced to 40% relative weight;
- ordinary human text is unchanged;
- threshold is topic-local;
- recent sources still outweigh historical windows.

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `python -m unittest tests.test_entertainment_bootstrap_weighting tests.test_entertainment_culture_generation_service -v`
Expected: FAIL because Phase B has no provenance-aware weighting.

- [ ] **Step 3: Implement exact weighting policy**

Keep weighting pure in `culture.py`; service only supplies count/threshold. Do not persist derived weights.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run Step 2 command. Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat: add bootstrap-aware culture weighting`

---

### Task 4: Pure Contextual Media Ranking Engine

**Files:**
- Create: `entertainment/media_culture.py`
- Test: `tests/test_entertainment_media_culture.py`

**Interfaces:**
- Create:

```python
@dataclass(frozen=True, slots=True)
class MediaCandidate:
    event: MemoryEvent
    score: float
    source_class: str  # "human" | "other_bot"
    score_bucket: str


def select_media_candidate(
    recent_events: Sequence[MemoryEvent],
    historical_windows: Sequence[Sequence[MemoryEvent]],
    *,
    context_messages: Sequence[str],
    recent_actions: Sequence[EntertainmentActionRecord],
    now: int,
    textual_event_count: int,
    bootstrap_threshold: int,
    repeat_cooldown_seconds: int,
) -> MediaCandidate | None:
    ...
```

- Exact minimum normalized relevance thresholds:
  - sticker: `0.48`
  - animation: `0.62`
  - photo: `0.78`
- Exclude forwarded media, missing `file_id`/`file_unique_id`, unsupported types, wrong topic, and any `file_unique_id` used in a `MEMORY_CALLBACK` within 21,600 seconds.
- Recent material outranks otherwise-equal historical material.
- After bootstrap, other-bot media context/source influence is multiplied by `0.40`.

- [ ] **Step 1: Write failing pure-ranking tests**

Cover contextual sticker/GIF wins, unrelated candidate loses, exact photo threshold safety, forwarded exclusion, missing identifiers, topic isolation, recent-over-history preference, other-bot post-bootstrap reduction, and 6h anti-repeat.

- [ ] **Step 2: Run tests and confirm RED**

Run: `python -m unittest tests.test_entertainment_media_culture -v`
Expected: FAIL because `media_culture.py` does not exist.

- [ ] **Step 3: Implement pure candidate extraction/context reconstruction/scoring**

Reconstruct neighboring context only from bounded chronological inputs. Do not persist duplicated neighbor text. Keep all Telegram sending out of this module.

- [ ] **Step 4: Run tests and confirm GREEN**

Run Step 2 command. Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat: add contextual media ranking`

---

### Task 5: One Bounded Culture Snapshot for Text + Emoji + Media

**Files:**
- Modify: `entertainment/culture.py`
- Modify: `entertainment/service.py`
- Test: `tests/test_entertainment_culture_snapshot.py`
- Test: `tests/test_entertainment_culture_generation_service.py`

**Interfaces:**
- Add:

```python
@dataclass(frozen=True, slots=True)
class CultureMemorySnapshot:
    recent_events: list[MemoryEvent]
    historical_windows: list[list[MemoryEvent]]
    counts: MemoryCounts
    generation: CultureGenerationContext
```

- Service method:

```python
async def _culture_memory_snapshot(
    self,
    chat_id: int,
    topic_id: int,
    *,
    trigger_text: str | None = None,
    now: int | None = None,
) -> CultureMemorySnapshot:
    ...
```

- It performs one bounded recent read, one bounded historical-window read, and one `memory_counts` read; the same snapshot feeds text/emoji/media logic.
- Legacy/minimal storage fallback builds a snapshot with text-only `generation`, empty raw event windows, and synthetic counts sufficient for existing tests.

- [ ] **Step 1: Write failing snapshot tests**

Assert bounded calls occur once per generation attempt, no full-history scan method is called, topic isolation is preserved, and legacy fallback remains usable.

- [ ] **Step 2: Run tests and confirm RED**

Run: `python -m unittest tests.test_entertainment_culture_snapshot tests.test_entertainment_culture_generation_service -v`
Expected: FAIL because service currently builds only `CultureGenerationContext`.

- [ ] **Step 3: Implement snapshot type and service builder**

Move no ranking logic into service; service only reads and assembles.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run Step 2 command. Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `refactor: share bounded Culture snapshot`

---

### Task 6: Runtime Media Realization, Budget Safety + Failure Fallback

**Files:**
- Modify: `entertainment/service.py`
- Modify: `entertainment/models.py` only if a helper/property is required; reuse `EntertainmentActionType.MEMORY_CALLBACK` rather than adding a new enum value.
- Test: `tests/test_entertainment_media_service.py`
- Test: `tests/test_entertainment_scheduler.py`
- Test: `tests/test_entertainment_autonomy.py`

**Interfaces:**
- Add service helper:

```python
async def _send_media_candidate(
    self,
    *,
    chat_id: int,
    topic_id: int,
    candidate: MediaCandidate,
) -> None:
    ...
```

- Pin Telegram calls:
  - sticker => `bot.send_sticker(chat_id=..., sticker=file_id, message_thread_id=topic_id or None)`
  - photo => `bot.send_photo(chat_id=..., photo=file_id, message_thread_id=topic_id or None)`
  - animation => `bot.send_animation(chat_id=..., animation=file_id, message_thread_id=topic_id or None)`
- Do **not** copy original photo/animation captions.
- Realization flow: existing autonomy decision first proves one action slot is allowed; build one snapshot; generate text fallback; rank media; if media qualifies and recent history does not end in non-direct `MEMORY_CALLBACK`, realize the same slot as `MEMORY_CALLBACK`. Otherwise send Phase B text.
- In `PEAK`, only a decision already permitted as direct may be realized as media; non-direct media cannot bypass the existing peak guard.
- On media send exception: record no media success; if a generated text fallback exists, send exactly one text response under the original decision and record that text action; if text send also fails, record nothing and let supervisor isolation handle/log it.

- [ ] **Step 1: Write failing service tests**

Cover:
- correct `send_sticker`, `send_photo`, `send_animation` args;
- no reused caption;
- same action budget count as text;
- no consecutive non-direct `MEMORY_CALLBACK`;
- no non-direct media in PEAK;
- no media candidate => Phase B text;
- expired/rejected media `file_id` => at most one successful text fallback and no successful media action record;
- safe metadata contains `media_type`, `media_file_unique_id`, `media_score_bucket`, `media_source_class`, never neighboring text.

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `python -m unittest tests.test_entertainment_media_service tests.test_entertainment_scheduler tests.test_entertainment_autonomy -v`
Expected: FAIL because runtime does not yet realize media.

- [ ] **Step 3: Implement media realization and fallback**

Keep `select_action()` budgets unchanged. Media is a realization of an already-allowed slot, never a second independent decision.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run Step 2 command. Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat: reuse contextual media safely`

---

### Task 7: Documentation, PostgreSQL CI Coverage + Whole-Branch Verification

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `.github/workflows/ci.yml` only if new PostgreSQL test modules are not already covered by the existing job command.
- Test: all Phase C tests plus full suite.

**Interfaces:**
- Document:
  - `ENTERTAINMENT_BOOTSTRAP_TEXT_EVENT_THRESHOLD=10000`
  - `ENTERTAINMENT_MEDIA_REPEAT_COOLDOWN_SECONDS=21600`
  - commands/other bots are remembered as culture but do not increase activity;
  - own bot is excluded;
  - media is reused by Telegram `file_id` without binary storage;
  - photos use stricter relevance rules;
  - `/fun_ignore_me`, `/fun_remember_me`, `/fun_delete_me` still govern human memory.

- [ ] **Step 1: Add/update PostgreSQL job coverage if needed**

Ensure Task 1 migration/provenance parity tests run against PostgreSQL 17 in CI.

- [ ] **Step 2: Update README and `.env.example`**

No credentials or real DSN values.

- [ ] **Step 3: Run compile verification**

Run: `python -m compileall -q .`
Expected: exit 0.

- [ ] **Step 4: Run all Phase C focused tests**

Run: `python -m unittest tests.test_entertainment_memory tests.test_entertainment_bootstrap_learning tests.test_entertainment_bootstrap_weighting tests.test_entertainment_media_culture tests.test_entertainment_culture_snapshot tests.test_entertainment_media_service -v`
Expected: PASS.

- [ ] **Step 5: Run full unittest discovery**

Run: `python -m unittest discover -s tests -p "test_*.py"`
Expected: no new Phase C/Entertainment failures; compare any remaining failures against the known repository baseline rather than hiding them.

- [ ] **Step 6: Verify PostgreSQL 17 job**

Expected: `entertainment-postgres` SUCCESS on the exact final HEAD.

- [ ] **Step 7: Review final diff against Phase B head**

Verify scope contains only Phase C spec/plan, Entertainment/Culture storage/service/media code, tests, README/env/CI as required. Confirm no credentials, media binaries or raw corpus fixtures were committed.

- [ ] **Step 8: Commit final docs/CI changes**

Commit message: `docs: document Culture Memory Phase C`
