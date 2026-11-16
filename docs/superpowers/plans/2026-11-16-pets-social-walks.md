# Pets social walks implementation plan

> For agentic workers: execute inline with test-first changes and one final independent review.

Goal: ship sex/species labels, timed group walks, friendship levels and a daily journal.
Architecture: immutable pure biography rules, a separate PostgreSQL walks store,
scoped aiogram commands, and optional integration into the existing private card.
Tech stack: Python, asyncpg/PostgreSQL, aiogram; no new dependencies.
Spec: docs/superpowers/specs/2026-11-16-pets-social-walks-design.md

## Global constraints

Preserve every old DNA, portrait, origin story and owner uniqueness constraint.
No Mini App, scheduler, production DB tests, global allowlist expansion or checkers edits.
Social feature defaults off; group commands require explicit selected Pets scope.

## Review focus

- Replayed or simultaneous requests across midnight cannot create extra walks/XP.
- Users in other groups cannot read a selected group's journal or start outings.
- Pictures, DNA and private origin data never leak into group responses.
- A storage failure or rejected message cannot skip existing moderation.
- Long diaries and invalid arguments cannot create oversized Telegram messages.

### Task 1: biography and progression rules

Files: create `fosgen_pets/biography.py`, `fosgen_pets/walks.py` (pure rules),
`tests/test_fosgen_pets_biography.py`, `tests/test_fosgen_pets_walks.py`.
- [x] Write/run RED tests for frozen sex vectors, balance, no DNA mutation,
  level thresholds, Moscow day boundaries and species-aware activities.
- [x] Implement `pet_sex(appearance)`, `sex_label(appearance)`,
  `level_for_xp(xp)`, `walk_day(now)`, `activity(appearance, day, route)`.
- [x] Run unit tests GREEN and commit.

### Task 2: durable walk store

Files: create `fosgen_pets/walks_postgres.py`, extend `schema.sql`,
create `tests/test_fosgen_pets_walks_postgres.py`.
Interfaces: `PetWalkStore(permanent_store)`, `start(owner, chat, message, route,
thread_id=None, now=None) -> WalkResult`, `growth(owner, now=None) -> Growth`,
`diary(chat, day, page=1, now=None) -> WalkDiary`.
- [x] Write/run RED real-PG tests: missing pet, 30-minute finish, global daily cap,
  exact replay/parallel starts, midnight active walk, restart and idempotent migration,
  group journal isolation/pagination, immutable old pet/art, invalid input.
- [x] Implement owner-row serialized starts and unique constraints; completion is
  derived from persisted end time, so no double rewards or timers.
- [x] Run task tests GREEN and commit.

### Task 3: scoped Telegram UI and release evidence

Files: create `fosgen_pets/walks_telegram.py`, extend `telegram.py`, `runtime.py`,
README and implementation docs; create dispatcher/runtime tests.
Interfaces: register social service using exactly the effective Pets chat IDs;
keep existing registered-handler promotion and storage failure containment.
- [x] Write/run RED tests for labels, config flag, group/topic commands,
  moderation ordering, outsiders, private guidance, stale/anonymous/edited senders,
  bounded diary output, cooldown/no duplicate response, and storage failure.
- [x] Implement commands, private XP display and privacy/help text.
- [x] Run focused and complete Python suite against disposable PostgreSQL.
- [x] Independent branch review; fix important findings with RED/GREEN tests.
- [ ] Publish PR and inspect all CI jobs; keep production feature off.


## Execution evidence

- 2026-11-16: rules, store and UI tests observed RED before implementation, then GREEN.
- Full Python suite: 1336 tests, no skips, OK on disposable PostgreSQL 16.2, 78.055s.
- Independent review: one important rejected-update replay issue, fixed with two
  RED/GREEN regression tests covering midnight, active rejection, restart and concurrency.
- Fix uses a monotonic owner/chat request watermark rather than an unbounded
  per-rejection ledger. Out-of-order older commands are deliberately ignored.
- Literal male/female fixtures pin sex for DNA v1/v2/v3; no renderer or asset changed.
- Documentation now correctly distinguishes card XP from the passport's identity fields.
- Full-suite-only failure in the new future-time test was a test-clock problem:
  dispatcher tests now freeze their clock instead of depending on suite duration.
- Collection/rare encounters and visual aging remain explicitly outside this increment;
  their proposed next-stage rules are recorded without changing owner uniqueness.
