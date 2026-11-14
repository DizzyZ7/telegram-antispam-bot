# Fosgen Pets: implementation and evidence

Goal: one permanent Telegram pet per consenting user after 100 eligible messages,
with frozen DNA and stored portrait bytes; isolated disposable admin tests.

Architecture: retain v1 rendering unchanged for existing DNA. New v2 DNA adds
Pattern Seed 0–999999 and uses species-specific procedural masks. PostgreSQL
owns consent, deduplicated progress, adoption, frozen portraits and care events.
Telegram handlers are optional and isolated from existing moderation.

Constraints: no Mini App, death, replacement, reset, main merge or production DB
experiments. Work remains in feat/fosgen-pets-foundation / PR #127.

## Tasks

- [x] Freeze v1 fixtures; add validated v2 DNA and version dispatch, bounded PNG rendering.
- [x] Render all 12 species; verify seed pixel differences, anatomy and parallel determinism.
- [x] Expand isolated /pettest with species, DNA, lists, atomic bounded series and frozen art.
- [x] Add additive PostgreSQL migration, consent, message dedup/rate limit, single adoption,
  immutable stored portraits and idempotent care; test against a disposable PostgreSQL.
- [x] Connect private /pet, consent/progress/privacy/passport/history and care callbacks;
  count only messages accepted by existing moderation, behind explicit enablement.
- [x] Update Docker, CI and operational documentation; complete full Python/Node
  verification and independent review before pushing the existing branch.

## Review focus

- Old v1 canonical DNA/SVG must not drift when v2 is released.
- Concurrent workers must not issue a second pet or exceed laboratory caps.
- Revocation, replay, commands, moderated/forwarded messages must not advance progress.
- Renderer failure must leave no half-created pet; DB errors must not interrupt moderation.
- Foreign care callbacks and laboratory IDs must not cross ownership/storage boundaries.

## Baseline (2026-11-14)

- HEAD 1cfa3db; PR #127 open draft, feat/fosgen-pets-foundation -> main.
- Existing Pets core/render tests: 14 passed. Pettest import initially blocked by missing
  local aiogram; install repository requirements before complete baseline.
- Audit: v1 ignores variant when marking=none; tabby seed-independent; calf muzzle drawn
  under head; sock coordinates not anchored to feet. No permanent repository/handlers yet.
- Decision: persist canonical SVG and PNG bytes at creation; never re-render permanent pets
  after graphics upgrades. Retain frozen v1 implementation for historical temporary DNA.
- Decision: isolated fresh clone on requested branch; no extra branch/worktree.

## Verification evidence

| Requirement | Change | Executed check | Result | Limitation |
| --- | --- | --- | --- | --- |
| Existing bot compatibility | Optional handlers and post-moderation counting | Original baseline `python -m unittest discover -s tests -p 'test_*.py'` | 1086 tests OK, 81 PG skips | Baseline before edits |
| Permanent identity and concurrency | Owner locks, consent, unique DNA/pixel hashes, frozen SVG/PNG and DB triggers | `python /tmp/fosgen_run_pgtests.py tests.test_fosgen_pets_postgres -v` | 18 passed on disposable PostgreSQL 16.2 | CI additionally uses PG17 |
| Whole application with real persistence | All Python tests, including other bot subsystems | `python /tmp/fosgen_run_pgtests.py discover -s tests -p 'test_*.py'` | 1189 passed with no skips on disposable PostgreSQL 16.2 | No live Telegram API |
| Existing web clients | No modifications to their flows | `node --check writers_submission/static/app.js`; `node --test tests/*.cjs` | 65 passed | Browser QA not needed for unchanged clients |
| Procedural portraits | 12 species; independent seed streams, anatomical clips, canonical variants | `python -m unittest tests.test_fosgen_pets_graphics_v2 -v` | 13 passed | Sampled perceptual differences, not exhaustive million-seed guarantee |
| Recipe upgrades | Frozen v1, explicit v2, reviewed golden hashes | Version, golden and security modules | Passed; independent review compared 3472 v1 recipes to original exactly | Keep frozen version files unchanged in future |
| Isolated laboratory | Atomic multi-instance cap, cached art, bounded series | `python -m unittest tests.test_fosgen_pettest tests.test_fosgen_pettest_security -v` | 23 passed | Sent Telegram messages remain after test deletion |
| Telegram journey and resilience | Own private UI, persisted stories/care, optional startup | `python -m unittest tests.test_fosgen_pets_runtime tests.test_fosgen_pets_telegram -v` | 38 passed | Bot session mocked at Telegram network boundary |
| Offline review | Seed comparisons holding other DNA fixed | `python -m fosgen_pets.gallery --output /tmp/fosgen-gallery --png` | 102 SVG and PNG portraits created; contact sheet visually reviewed | Idle pose only |
| Build configuration | Explicit Cairo system dependency and all-species Docker smoke | `python -m compileall -q .`; `git diff --check` | Passed | Actual Docker build delegated to GitHub CI |

## Review and decisions

- Independent read-only review found no remaining blocking security/concurrency issue
  after malformed startup configuration and shutdown-isolation fixes.
- Fixed its metadata-growth finding: retain at most 256 rejected message events per owner,
  preserve counted HMACs and source-timestamp replay protection. Regression test first failed
  with 300 rejected rows, then passed with 256.
- Runtime DB outages leave moderation working but do not replay missed pet progress.
  This operational limitation is disclosed in the user-facing design/runbook.
- Adopt is triggered privately by `/pet` after 100, preventing group reply spam and
  coupling neither portrait rendering nor notification delivery to group moderation.
- No production DB, Telegram credentials, merge or public rollout was used.
- User authorization covers committing/pushing this existing branch; main merge remains
  pending the user's later confirmation, independently of CI success.

