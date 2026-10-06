# Zero Trust v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the process-local captcha trust state with per-chat, restart-safe PostgreSQL verification so every new human join is challenged independently and `-1003237014529` can never inherit trust from another chat.

**Architecture:** Add a focused `zero_trust/` package with immutable models/config, PostgreSQL persistence, a state-machine service, Telegram handlers, and presentation helpers. `writers_moderation.py` keeps profanity/rules-specific UX only; `legacy_main.py` stops owning captcha state. `main.py` initializes Zero Trust before polling and fails closed if its PostgreSQL storage cannot initialize.

**Tech Stack:** Python 3.12, aiogram 3.24+, asyncpg 0.30+, unittest, PostgreSQL 17 in CI.

**Spec:** `docs/superpowers/specs/2026-10-06-zero-trust-v2-design.md`

## Global Constraints

- Default `ZERO_TRUST_CHAT_IDS=-1002619489118,-1003237014529,-1003643412493,-1003687304800`.
- Default `ZERO_TRUST_CHALLENGE_TTL_SECONDS=300`.
- Every join creates a fresh session; historical `passed` never bypasses a later join.
- Trust and callbacks are scoped by exact `challenge_id + chat_id + user_id`.
- Callback payload is `zt:<challenge_id>:<user_id>:<answer>`.
- States are exactly `pending | verified | passed | expired | cancelled`.
- PostgreSQL failures must never grant access.
- Existing writers profanity moderation and writers welcome/success copy remain unchanged.
- `ALLOWED_CHATS` continues to control legacy features only and cannot disable Zero Trust.
- No new dependency or secret is added to the repository.
- Old RAM-only verification history is unrecoverable and must not be fabricated.

## Review Focus

- **Duplicate join events / races:** only one active (`pending` or `verified`) session may exist for one `(chat_id, user_id)`; Task 2 pins this transactionally.
- **Stale Telegram buttons:** an old challenge must never complete a newer session; Task 3/4 test exact `challenge_id` rejection.
- **Telegram permission restoration failure:** DB must remain `verified`, never falsely `passed`; Task 4 tests retry/finalization.
- **Environment misconfiguration:** overriding `ALLOWED_CHATS` must not alter Zero Trust scope, and missing `DATABASE_URL` must fail startup when security scope is enabled; Task 5 tests both.
- **Restart during verification:** `pending`, `verified`, and historical `passed` rows must remain queryable after storage/service recreation; Task 2/3 integration tests cover this.

---

### Task 1: Zero Trust domain, configuration and callback/presentation primitives

**Files:**
- Create: `zero_trust/__init__.py`
- Create: `zero_trust/config.py`
- Create: `zero_trust/models.py`
- Create: `zero_trust/presentation.py`
- Create: `tests/test_zero_trust_config.py`
- Create: `tests/test_zero_trust_presentation.py`

**Interfaces:**
- Produces: `TRADER_XER_CHAT_ID = -1003237014529`.
- Produces: `DEFAULT_ZERO_TRUST_CHAT_IDS: frozenset[int]` containing all four protected chats from the spec.
- Produces: `ZeroTrustConfig(chat_ids: frozenset[int], challenge_ttl_seconds: int)` and `ZeroTrustConfig.from_env() -> ZeroTrustConfig`.
- Produces: `ChallengeStatus(str, Enum)` with `PENDING`, `VERIFIED`, `PASSED`, `EXPIRED`, `CANCELLED`.
- Produces: `ChallengeRecord` dataclass matching the PostgreSQL columns in the spec.
- Produces: `CaptchaPrompt(question: str, answer: int, options: tuple[int, ...])`.
- Produces: `generate_prompt(rng: random.Random) -> CaptchaPrompt` using operands 1..9 and four unique answer options.
- Produces: `encode_callback(challenge_id: int, user_id: int, answer: int) -> str` and `decode_callback(value: str) -> tuple[int, int, int] | None`.
- Produces: `build_challenge_keyboard(challenge_id: int, user_id: int, prompt: CaptchaPrompt) -> InlineKeyboardMarkup`.

- [ ] **Step 1: Write failing config/model tests**

Assert `ZeroTrustConfig.from_env()` defaults to the four exact chat IDs, includes `-1003237014529`, defaults TTL to `300`, accepts comma/semicolon overrides, and rejects non-positive TTL.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_zero_trust_config -v`
Expected: FAIL because `zero_trust` domain/config does not exist.

- [ ] **Step 3: Implement `config.py`, `models.py`, `__init__.py`**

Keep parsing independent from `legacy_main.ALLOWED_CHATS`; no import of legacy state.

- [ ] **Step 4: Write failing presentation/callback tests**

Assert callback encoding is exactly `zt:41:777:12`, malformed/overflow-shaped values return `None`, prompt options are unique and contain the correct answer, and every button includes the exact challenge id and user id.

- [ ] **Step 5: Run RED then implement `presentation.py`**

Run: `python -m unittest tests.test_zero_trust_presentation -v`
Expected before implementation: FAIL. Expected after implementation: PASS.

- [ ] **Step 6: Run focused tests and commit**

Run: `python -m unittest tests.test_zero_trust_config tests.test_zero_trust_presentation -v`
Expected: PASS.

Commit: `feat: add Zero Trust domain primitives`

---

### Task 2: PostgreSQL persistence and transactional challenge state

**Files:**
- Create: `zero_trust/storage.py`
- Create: `tests/test_zero_trust_postgres.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `ChallengeRecord`, `ChallengeStatus` from Task 1.
- Produces: `PostgresZeroTrustStorage(database_url: str, *, min_pool_size: int = 1, max_pool_size: int = 2)`.
- Produces async methods:
  - `initialize() -> None`
  - `close() -> None`
  - `create_challenge(*, chat_id: int, user_id: int, expected_answer: int, created_at: int, expires_at: int, username: str | None, display_name: str | None) -> ChallengeRecord`
  - `attach_message_id(challenge_id: int, message_id: int) -> None`
  - `get_challenge(challenge_id: int) -> ChallengeRecord | None`
  - `apply_answer(*, challenge_id: int, chat_id: int, user_id: int, answer: int, now: int) -> ChallengeRecord`
  - `mark_passed(*, challenge_id: int, chat_id: int, user_id: int, now: int) -> ChallengeRecord`
  - `cancel_active(*, chat_id: int, user_id: int, now: int) -> int`
  - `expire_stale(*, now: int) -> int`
  - `history_for(chat_id: int, user_id: int, *, limit: int = 20) -> list[ChallengeRecord]`

`apply_answer` rules: wrong answer increments `attempts` and keeps `pending`; correct `pending` becomes `verified`; expired `pending` becomes `expired`; a matching `verified` row is returned unchanged so Telegram permission restoration can be retried; `passed/expired/cancelled` or identity mismatch cannot transition back to active.

- [ ] **Step 1: Write PostgreSQL RED tests**

Cover table/index creation, two different chats for the same user, one-active-session uniqueness per `(chat_id,user_id)`, rejoin cancelling the previous active row, wrong-answer isolation, expiry, `verified -> passed`, history persistence after closing/reopening storage, and exact same numeric challenge/user values in another chat remaining isolated.

- [ ] **Step 2: Run RED against CI PostgreSQL contract**

Run locally/CI: `TEST_DATABASE_URL=... python -m unittest tests.test_zero_trust_postgres -v`
Expected: FAIL because storage does not exist.

- [ ] **Step 3: Implement idempotent schema + transactional methods**

Use one `zero_trust_challenges` table. `create_challenge` must cancel active rows for the exact chat/user and insert the new row in one transaction. `apply_answer` must use row locking (`FOR UPDATE`) before checking identity/status/TTL and updating attempts/status.

- [ ] **Step 4: Add Zero Trust PostgreSQL suite to CI**

Append `tests.test_zero_trust_postgres` to the existing PostgreSQL job without removing Entertainment integration tests.

- [ ] **Step 5: Run PostgreSQL tests and commit**

Run: `python -m unittest tests.test_zero_trust_postgres -v`
Expected: PASS with `TEST_DATABASE_URL`; skipped without it.

Commit: `feat: persist Zero Trust challenges in PostgreSQL`

---

### Task 3: Restart-safe Zero Trust state-machine service

**Files:**
- Create: `zero_trust/service.py`
- Create: `tests/test_zero_trust_service.py`

**Interfaces:**
- Consumes: `ZeroTrustConfig`, `PostgresZeroTrustStorage`, `CaptchaPrompt`, `generate_prompt`.
- Produces: `AnswerKind(str, Enum)` with `WRONG`, `VERIFIED`, `ALREADY_VERIFIED`, `EXPIRED`, `STALE`.
- Produces: `AnswerResult(kind: AnswerKind, challenge: ChallengeRecord | None)`.
- Produces: `ZeroTrustService(storage, config, *, rng: random.Random | None = None, now_fn: Callable[[], float] = time.time)`.
- Produces async methods:
  - `start() -> int` (expire stale pending rows and return count)
  - `is_protected_chat(chat_id: int) -> bool`
  - `begin_join(*, chat_id: int, user_id: int, username: str | None, display_name: str | None) -> tuple[ChallengeRecord, CaptchaPrompt]`
  - `attach_message_id(challenge_id: int, message_id: int) -> None`
  - `answer(*, challenge_id: int, chat_id: int, user_id: int, answer: int) -> AnswerResult`
  - `finalize_pass(*, challenge_id: int, chat_id: int, user_id: int) -> ChallengeRecord`
  - `cancel_leave(*, chat_id: int, user_id: int) -> int`

- [ ] **Step 1: Write service RED tests**

Using a fake storage, prove: same user can begin independent joins in writers and trader/xer chats; historical pass never suppresses a new join; stale challenge ids return `STALE`; startup expiry runs; `verified` is retryable; bots are not a service concern (handlers own bot filtering).

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_zero_trust_service -v`
Expected: FAIL because service does not exist.

- [ ] **Step 3: Implement the service as a thin state-machine coordinator**

Do not duplicate transaction logic from storage; translate storage status into `AnswerKind` and create prompts/TTLs deterministically from injected `rng`/`now_fn`.

- [ ] **Step 4: Run service + PostgreSQL persistence tests**

Run: `python -m unittest tests.test_zero_trust_service -v`
Expected: PASS.

Run with DB: `python -m unittest tests.test_zero_trust_postgres -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit: `feat: add Zero Trust verification service`

---

### Task 4: Telegram join/leave/callback handlers with writers-specific presentation

**Files:**
- Create: `zero_trust/handlers.py`
- Create: `tests/test_zero_trust_handlers.py`
- Modify: `writers_moderation.py`
- Modify: `tests/test_writers_moderation.py`

**Interfaces:**
- Consumes: `ZeroTrustService`, callback helpers, Telegram bot/dispatcher, `WritersChatScope`, `build_welcome_text`, `build_captcha_success_text`, `RULES_LINK_PREVIEW_OPTIONS`.
- Produces: `register_zero_trust_handlers(app: Any, service: ZeroTrustService, *, writers_scope: WritersChatScope | None = None) -> None`.
- Changes `register_writers_chat_handlers(...)` so it registers profanity/rules handlers only; its join/captcha verification decisions are removed and it no longer reads/writes `module.pending_users`, `module.passed_users`, or `module.failed_users`.

Handler contract:
- join: ignore bots/unprotected chats, restrict first, call `begin_join`, send generic or writers welcome, attach challenge-message id;
- leave: cancel exact chat/user active challenge;
- callback: parse exact `zt:` payload, require callback actor == encoded user, require callback message chat matches stored challenge chat, handle wrong/expired/stale, restore permissions only after `VERIFIED/ALREADY_VERIFIED`, call `finalize_pass` only after Telegram confirms permissions;
- Telegram permission error leaves DB `verified` and callback can retry later.

- [ ] **Step 1: Write handler RED tests**

Cover:
1. writers pass does not bypass `-1003237014529`;
2. trader/xer pass does not bypass writers;
3. bot joins do nothing;
4. DB failure after restriction never restores permissions;
5. another user pressing the button gets an alert and no state transition;
6. stale old callback cannot finish a newer challenge;
7. wrong answer does not grant permissions;
8. Telegram permission failure does not call `finalize_pass`;
9. retry from `verified` calls permission restoration again then finalizes;
10. leave cancels only exact chat/user;
11. writers welcome/success still include the rules URL.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_zero_trust_handlers tests.test_writers_moderation -v`
Expected: FAIL on missing generic Zero Trust handlers / old writers captcha ownership.

- [ ] **Step 3: Implement handlers and slim writers moderation ownership**

Keep profanity detection/deletion unchanged. Reuse existing writers copy builders rather than duplicating their strings.

- [ ] **Step 4: Run focused tests and commit**

Run: `python -m unittest tests.test_zero_trust_handlers tests.test_writers_moderation -v`
Expected: PASS.

Commit: `feat: route chat verification through Zero Trust v2`

---

### Task 5: Remove legacy global trust decisions and wire production startup fail-closed

**Files:**
- Modify: `legacy_main.py`
- Modify: `main.py`
- Modify: `.env.example`
- Modify: `README.md`
- Create: `tests/test_zero_trust_startup.py`

**Interfaces:**
- Consumes: `ZeroTrustConfig`, `PostgresZeroTrustStorage`, `ZeroTrustService`, `register_zero_trust_handlers`.
- Production uses `DATABASE_URL` for Zero Trust PostgreSQL. If `ZERO_TRUST_CHAT_IDS` is non-empty and `DATABASE_URL` is missing/invalid or initialization fails, startup raises before polling instead of silently running unprotected.
- `legacy_main.py` retains `DEFAULT_ALLOWED_CHATS` and legacy feature filtering but removes the old `pending_users`, `passed_users`, `failed_users`, `build_captcha`, join/callback handlers, and message-collection checks that depended on pending RAM state.

- [ ] **Step 1: Write startup/compatibility RED tests**

Assert:
- `ZeroTrustConfig` still includes `-1003237014529` when `ALLOWED_CHATS` is overridden to only the writers chat;
- startup rejects enabled Zero Trust without `DATABASE_URL`;
- startup diagnostics include effective `ZERO_TRUST_CHAT_IDS`, effective `ALLOWED_CHATS`, TTL, and warn if the trader/xer id is absent from an explicit Zero Trust override;
- legacy globals no longer exist as verification sources.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_zero_trust_startup -v`
Expected: FAIL before production wiring.

- [ ] **Step 3: Wire startup and shutdown in `main.py`**

Initialize Zero Trust storage/service before registering Zero Trust handlers; call `await service.start()` before polling; close Zero Trust storage in `finally`. Resolve writers scope before/while registering handlers so writers-specific presentation is deterministic.

- [ ] **Step 4: Remove legacy captcha ownership from `legacy_main.py`**

Do not change summary/statistics behavior. Do not remove `DEFAULT_ALLOWED_CHATS`; it remains the legacy-feature default.

- [ ] **Step 5: Update environment/docs**

Document `ZERO_TRUST_CHAT_IDS`, TTL, Postgres requirement, per-chat re-verification, durable history from v2 onward, and the fact that old RAM history was already lost and is not backfilled.

- [ ] **Step 6: Run focused + full suite and commit**

Run: `python -m unittest tests.test_zero_trust_config tests.test_zero_trust_presentation tests.test_zero_trust_service tests.test_zero_trust_handlers tests.test_zero_trust_startup tests.test_writers_moderation -v`
Expected: PASS.

Run: `python -m compileall -q . && python -m unittest discover -s tests -p "test_*.py"`
Expected: PASS.

Commit: `feat: enable PostgreSQL Zero Trust v2 in production`

---

### Task 6: Final PostgreSQL, regression and rollout verification

**Files:**
- Modify only if a failing regression reveals a scoped defect; otherwise no product-code change.
- Test: existing full suite + `tests/test_zero_trust_postgres.py`.

**Interfaces:**
- No new interface. This task verifies the shipped contract from Tasks 1–5.

- [ ] **Step 1: Run full unit suite**

Run: `python -m compileall -q . && python -m unittest discover -s tests -p "test_*.py"`
Expected: PASS.

- [ ] **Step 2: Run PostgreSQL integration suite**

Run with CI DB: `python -m unittest tests.test_entertainment_storage_postgres tests.test_entertainment_storage_factory tests.test_entertainment_memory_postgres tests.test_entertainment_culture_memory_postgres tests.test_entertainment_culture_windows_postgres tests.test_entertainment_message_purge tests.test_zero_trust_postgres -v`
Expected: PASS.

- [ ] **Step 3: Explicit security regression check**

Confirm automated tests prove: a user with historical `passed` in writers receives a fresh independent challenge on join to `-1003237014529`; a stale writers callback cannot complete that trader/xer challenge; an `ALLOWED_CHATS` override cannot change either result.

- [ ] **Step 4: Branch review and CI**

Compare implementation branch against the approved design branch/main, verify only Zero Trust/legacy captcha ownership/docs/CI changed, then require both GitHub Actions jobs green.

- [ ] **Step 5: PR, squash merge, post-merge main CI**

Open a PR summarizing the security root cause, state-machine invariants, Postgres durability, and old-history limitation. Merge only after PR CI passes, then require fresh `main` CI success before reporting completion.
