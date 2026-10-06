# Writers Submission v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a restart-safe Telegram Writers Submission Mini App where verified writers can create drafts, attach PDF/DOCX/TXT files, submit immutable revisions, track moderation, and receive Telegram decisions while PostgreSQL remains the source of truth.

**Architecture:** Keep the feature inside the existing asyncio process. Add a focused `writers_submission` package with PostgreSQL persistence, Telegram initData/session security, an aiohttp Mini App/API, private-chat entry/moderation handlers, and a transactional outbox worker; `main.py` owns lifecycle and the feature stays disabled unless explicitly configured.

**Tech Stack:** Python 3.12, aiogram >=3.24, aiohttp, asyncpg >=0.30, unittest/IsolatedAsyncioTestCase, PostgreSQL 17 in CI, plain HTML/CSS/JavaScript.

**Spec:** `docs/superpowers/specs/2026-10-06-writers-submission-v1-design.md`

## Global Constraints

- `WRITERS_SUBMISSION_ENABLED=0` by default; when disabled, existing bot behavior and startup remain unchanged.
- PostgreSQL is mandatory when Writers Submission is enabled; never fall back to SQLite for this subsystem.
- Mini App entry is through a private bot chat after a `start=writers_submit` deep link; direct group WebApp launch is not the supported flow.
- Telegram bootstrap `initData` freshness default is 900 seconds; application session TTL default is 43200 seconds.
- Session identity comes only from verified Telegram data; author/reviewer/chat identity is never trusted from frontend JSON or callback payload data.
- Session cookie is Secure, HttpOnly and SameSite=Strict; session signing uses stable domain-separated server secret material derived from the bot token.
- Current writers-community eligibility is required for create and submit; submit always performs a fresh `getChatMember` check.
- Default maximum attachment size is 20971520 bytes; default maximum is three attachments per revision.
- Supported file types are PDF, DOCX and TXT only; links must be HTTPS and are never fetched by backend code.
- Every ready file is uploaded to `WRITERS_SUBMISSION_FILE_CHAT_ID` and persisted as Telegram `file_id` before it can participate in submit; local temp files are not a durability dependency.
- Submitted revisions are immutable. `CHANGES_REQUESTED` creates revision N+1 instead of mutating N.
- Submission and moderation transitions are transactional and idempotent; concurrent claim has exactly one winner.
- Moderation and critical notification delivery use durable PostgreSQL outbox work; Telegram is not the source of truth.
- No React/Vite/Node build pipeline, S3, OCR, PDF rendering, DOCX parsing, plagiarism detection or AI review in v1.
- Do not alter Zero Trust state semantics, writers profanity moderation semantics, Entertainment behavior or unrelated games.
- Never log/persist BOT_TOKEN, DATABASE_URL, raw Telegram initData, uploaded bytes, full work text, or full moderation comments by default.
- Development is RED -> GREEN with a focused verification command and commit after each task.

## Review Focus

- **Telegram signed data with duplicate query keys or malformed percent-encoding:** bootstrap must reject ambiguous/malformed payloads instead of verifying a different canonical string than Telegram sent; covered in Task 2 security tests.
- **Two browser tabs autosaving the same draft:** stale expected version must return conflict and must not silently overwrite the newer draft; covered in Task 5 service tests and Task 10 HTTP tests.
- **DOCX-looking ZIP bombs / huge central directories:** validation must inspect only bounded container metadata and reject abnormal archives without expanding entries; covered in Task 3 upload tests.
- **Telegram file staging succeeds but PostgreSQL metadata insert fails:** temporary local file is deleted and the staging message may be cleaned up, while no ready attachment is exposed; covered in Task 6 handler/service tests.
- **Worker restart while an outbox row is IN_FLIGHT:** stale leased work must become claimable again after its lease timeout and must not duplicate an already recorded delivery; covered in Task 8 PostgreSQL/worker tests.

---

## File Structure

Create:

- `writers_submission/__init__.py` — stable public exports.
- `writers_submission/config.py` — env parsing and fail-closed enablement validation.
- `writers_submission/models.py` — enums, DTOs, service results and typed errors.
- `writers_submission/security.py` — Telegram initData verification, signed application session, same-origin/CSRF helpers.
- `writers_submission/uploads.py` — filename normalization, streamed size checks and PDF/DOCX/TXT classification.
- `writers_submission/storage.py` — PostgreSQL schema v1 and all transactional persistence primitives.
- `writers_submission/service.py` — ownership, eligibility, draft/revision and moderation state-machine logic.
- `writers_submission/files.py` — Telegram file-staging orchestration around validated temp files.
- `writers_submission/delivery.py` — moderation/notification outbox worker and Telegram rendering.
- `writers_submission/web.py` — aiohttp runner, middleware, JSON API and multipart endpoints.
- `writers_submission/handlers.py` — private start/WebApp button and moderator callbacks/comment flow.
- `writers_submission/static/index.html` — Mini App shell.
- `writers_submission/static/app.js` — session bootstrap, workspace/editor/timeline/autosave.
- `writers_submission/static/app.css` — mobile-first layout.
- `tests/test_writers_submission_config.py`
- `tests/test_writers_submission_security.py`
- `tests/test_writers_submission_uploads.py`
- `tests/test_writers_submission_storage.py`
- `tests/test_writers_submission_service.py`
- `tests/test_writers_submission_files.py`
- `tests/test_writers_submission_delivery.py`
- `tests/test_writers_submission_web.py`
- `tests/test_writers_submission_handlers.py`
- `tests/test_writers_submission_postgres.py`
- `docs/writers-submission-v1.md`

Modify:

- `requirements.txt` — add explicit `aiohttp>=3.10`.
- `.env.example` — document all Writers Submission variables.
- `main.py` — construct/start/stop Writers Submission resources without changing other subsystem semantics.
- `.github/workflows/ci.yml` — include Writers Submission PostgreSQL integration tests.
- `README.md` — operator-facing feature summary and link to runbook.

---

### Task 1: Configuration, model vocabulary and dependency contract

**Files:**
- Create: `writers_submission/__init__.py`
- Create: `writers_submission/config.py`
- Create: `writers_submission/models.py`
- Create: `tests/test_writers_submission_config.py`
- Modify: `requirements.txt`
- Modify: `.env.example`

**Interfaces:**
- Produces: `WritersSubmissionConfig.from_env(*, bot_token: str | None, database_url: str | None, writers_chat_id: int | None) -> WritersSubmissionConfig`
- Produces enums: `SubmissionStatus`, `RevisionState`, `ReviewAction`, `OutboxState`, `OutboxEventType`
- Produces typed exceptions: `WritersSubmissionError`, `AuthorizationError`, `ConflictError`, `ValidationError`, `NotFoundError`

- [ ] **Step 1: Write failing config tests**

Add tests that assert:
- disabled config accepts missing Writers Submission URL/mod-chat/file-chat/moderator values;
- enabled config rejects missing database URL, bot token, writers chat id, mod chat id, file chat id or moderator list;
- enabled config rejects non-HTTPS public URL except explicit local `http://127.0.0.1` / `http://localhost` development URLs;
- defaults are exactly: initData 900, session 43200, max bytes 20971520, max files 3, rate window 60, bind `0.0.0.0`, port 8080;
- non-positive TTL/limits and malformed integer ids fail.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_writers_submission_config -v`  
Expected: FAIL because `writers_submission.config` does not exist.

- [ ] **Step 3: Implement config and model vocabulary**

Implement `@dataclass(frozen=True, slots=True) WritersSubmissionConfig` with the exact defaults above and normalized `frozenset[int]` moderator ids. Add the enum values from the approved spec and typed domain errors in `models.py`.

- [ ] **Step 4: Declare explicit aiohttp and example env**

Add `aiohttp>=3.10` to `requirements.txt`. Add the spec's Writers Submission environment block to `.env.example` with safe placeholders and `WRITERS_SUBMISSION_ENABLED=0`.

- [ ] **Step 5: Run GREEN**

Run: `python -m unittest tests.test_writers_submission_config -v && python -m compileall -q writers_submission`  
Expected: PASS.

- [ ] **Step 6: Commit**

`git add writers_submission requirements.txt .env.example tests/test_writers_submission_config.py && git commit -m "feat: add Writers Submission configuration"`

---

### Task 2: Telegram initData verification and signed application sessions

**Files:**
- Create: `writers_submission/security.py`
- Create: `tests/test_writers_submission_security.py`

**Interfaces:**
- Consumes: `WritersSubmissionConfig`, `AuthorizationError`
- Produces: `TelegramIdentity(user_id: int, username: str | None, first_name: str | None, last_name: str | None, auth_date: int)`
- Produces: `verify_telegram_init_data(init_data: str, *, bot_token: str, now: int, max_age_seconds: int) -> TelegramIdentity`
- Produces: `SessionClaims(user_id: int, issued_at: int, expires_at: int)`
- Produces: `SessionSigner(bot_token: str, ttl_seconds: int)` with `issue(user_id: int, now: int) -> str` and `verify(token: str, now: int) -> SessionClaims`
- Produces: `SESSION_COOKIE_NAME = "writers_session"`

- [ ] **Step 1: Write failing initData/session tests**

Use deterministic test vectors generated inside the test with the Telegram Web App HMAC algorithm. Assert valid signature, wrong signature, expired `auth_date`, missing user, malformed user JSON, duplicate query key rejection, malformed percent-encoding rejection, and that the returned user id comes only from signed user JSON. Assert session tamper/expiry rejection and restart-stable verification from a second `SessionSigner` created with the same bot token.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_writers_submission_security -v`  
Expected: FAIL because security interfaces are missing.

- [ ] **Step 3: Implement Telegram canonical verification**

Parse raw query pairs without collapsing duplicates. Reject duplicate keys and malformed encoding before HMAC verification. Remove `hash`, sort remaining `key=value` pairs byte-for-byte by decoded key, build the Telegram data-check string, derive the documented WebAppData secret and compare with `hmac.compare_digest`.

- [ ] **Step 4: Implement compact signed sessions**

Use canonical compact JSON payload `{"uid":...,"iat":...,"exp":...}`, URL-safe base64 without padding, and HMAC-SHA256 with a domain-separated signing key derived from the bot token and label `writers-submission-session-v1`. Reject malformed/tampered/expired tokens.

- [ ] **Step 5: Run GREEN**

Run: `python -m unittest tests.test_writers_submission_security -v`  
Expected: PASS.

- [ ] **Step 6: Commit**

`git add writers_submission/security.py tests/test_writers_submission_security.py && git commit -m "feat: secure Writers Mini App sessions"`

---

### Task 3: Bounded text/link/file validation

**Files:**
- Create: `writers_submission/uploads.py`
- Create: `tests/test_writers_submission_uploads.py`

**Interfaces:**
- Produces constants: `TITLE_MAX=160`, `WORK_TYPE_MAX=80`, `GENRE_MAX=120`, `DESCRIPTION_MAX=2000`, `BODY_MAX=200000`, `EXTERNAL_URL_MAX=2048`
- Produces: `validate_submission_fields(...) -> NormalizedSubmissionFields`
- Produces: `normalize_display_filename(name: str) -> str`
- Produces: `validate_staged_file(path: Path, *, filename: str, declared_mime: str, max_bytes: int) -> ValidatedUpload`
- `ValidatedUpload` includes safe filename, file class `pdf|docx|txt`, size and SHA-256.

- [ ] **Step 1: Write failing validation tests**

Assert required title/type/genre/description; body may be blank only when service indicates at least one ready file; whitespace normalization does not destroy body formatting; only HTTPS links pass; every exact max length passes and max+1 fails. File cases: real PDF signature, UTF-8 TXT, DOCX-shaped ZIP with `[Content_Types].xml` and `word/` entry, extension/MIME/signature mismatch, binary TXT, oversize file, path-like filename, NUL filename, and a ZIP with excessive entry count / suspicious central directory metadata rejected without extraction.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_writers_submission_uploads -v`  
Expected: FAIL.

- [ ] **Step 3: Implement field and filename validation**

Keep title/type/genre/description normalized and bounded; preserve body newlines while trimming outer whitespace. Reject credentials/control characters in filenames and return a basename-only safe display name.

- [ ] **Step 4: Implement bounded file classifier**

Never extract DOCX entries. Inspect only central-directory names with hard caps: maximum 256 entries and maximum 1 MiB aggregate filename metadata read. Require DOCX markers; compute SHA-256 streaming in chunks.

- [ ] **Step 5: Run GREEN**

Run: `python -m unittest tests.test_writers_submission_uploads -v`  
Expected: PASS.

- [ ] **Step 6: Commit**

`git add writers_submission/uploads.py tests/test_writers_submission_uploads.py && git commit -m "feat: validate Writers submission content"`

---

### Task 4: PostgreSQL schema v1 and basic author persistence

**Files:**
- Create: `writers_submission/storage.py`
- Create: `tests/test_writers_submission_storage.py`
- Create: `tests/test_writers_submission_postgres.py`

**Interfaces:**
- Produces: `PostgresWritersSubmissionStorage(database_url: str, min_pool_size: int = 1, max_pool_size: int = 3)`
- Methods: `initialize() -> None`, `close() -> None`
- Methods: `create_submission(author_user_id: int, writers_chat_id: int, fields: NormalizedSubmissionFields, now: int, idempotency_key: str | None) -> SubmissionBundle`
- Methods: `list_for_author(author_user_id: int, limit: int = 100) -> list[SubmissionSummary]`
- Methods: `get_for_author(submission_id: UUID, author_user_id: int) -> SubmissionBundle | None`
- Methods: `update_draft(... expected_version: int, fields: NormalizedSubmissionFields, now: int) -> SubmissionBundle`
- Produces schema version 1 tables named exactly as in the spec plus `writers_submission_schema_meta`.

- [ ] **Step 1: Write RED storage contract tests with a fake/in-memory stub boundary**

Pin row-to-model mapping, exact ownership argument requirement, version conflict behavior, and that no public storage method accepts an author id from persisted row as an authorization substitute.

- [ ] **Step 2: Write RED PostgreSQL initialization/basic CRUD tests**

Under `@unittest.skipUnless(TEST_DATABASE_URL,...)`, initialize storage, truncate only Writers Submission tables in dependency-safe order, verify all tables/indexes/schema version, create/list/get/update a draft, verify UUID ownership isolation, verify restart persistence, and verify stale `expected_version` raises `ConflictError`.

- [ ] **Step 3: Run RED**

Run: `python -m unittest tests.test_writers_submission_storage -v`  
Expected: FAIL.  
With PostgreSQL available: `python -m unittest tests.test_writers_submission_postgres -v`  
Expected: FAIL.

- [ ] **Step 4: Implement schema/bootstrap and CRUD**

Use asyncpg transactions, PostgreSQL UUID columns and CHECK constraints for statuses. Make initialization idempotent and never modify unrelated tables. Create author/update indexes and unique `(submission_id, revision_number)`.

- [ ] **Step 5: Run GREEN**

Run local non-DB tests and PostgreSQL tests when `TEST_DATABASE_URL` is available. Expected: PASS.

- [ ] **Step 6: Commit**

`git add writers_submission/storage.py tests/test_writers_submission_storage.py tests/test_writers_submission_postgres.py && git commit -m "feat: persist Writers submission drafts"`

---

### Task 5: Author service, eligibility, autosave conflicts and revisions

**Files:**
- Create: `writers_submission/service.py`
- Create: `tests/test_writers_submission_service.py`
- Extend: `tests/test_writers_submission_postgres.py`

**Interfaces:**
- Consumes storage from Task 4 and validators from Task 3.
- Produces: `WritersEligibilityChecker = Callable[[int], Awaitable[bool]]`
- Produces: `WritersSubmissionService(storage, config, eligibility_checker)`
- Methods: `create(author_user_id: int, fields: ..., idempotency_key: str, now: int) -> SubmissionBundle`
- `list_mine(author_user_id: int) -> list[SubmissionSummary]`
- `get_mine(author_user_id: int, submission_id: UUID) -> SubmissionBundle`
- `autosave(author_user_id: int, submission_id: UUID, expected_version: int, fields: ..., now: int) -> SubmissionBundle`
- `withdraw(author_user_id: int, submission_id: UUID, idempotency_key: str, now: int) -> SubmissionBundle`
- `create_revision(author_user_id: int, submission_id: UUID, idempotency_key: str, now: int) -> SubmissionBundle`

- [ ] **Step 1: Write failing service tests**

Assert cross-user list/detail/update/withdraw/revision denial; create requires eligible writer; already-owned history remains readable/editable after membership loss; stale two-tab autosave conflicts; duplicate create with the same idempotency key returns the same logical result; withdrawal allowed only from non-terminal allowed states; `CHANGES_REQUESTED` creates prefilled N+1 and preserves N.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_writers_submission_service -v`  
Expected: FAIL.

- [ ] **Step 3: Implement service ownership/eligibility layer**

Service receives actor ids from trusted callers and passes them explicitly to storage ownership predicates. Never return existence details for another user's UUID: map to `NotFoundError`/generic 404 semantics.

- [ ] **Step 4: Add PostgreSQL idempotency and revision tests**

Verify idempotency survives storage/service reconstruction and `(actor, operation, key)` uniqueness prevents duplicate logical work.

- [ ] **Step 5: Run GREEN**

Run: `python -m unittest tests.test_writers_submission_service -v` plus Writers PostgreSQL tests. Expected: PASS.

- [ ] **Step 6: Commit**

`git add writers_submission/service.py writers_submission/storage.py tests/test_writers_submission_service.py tests/test_writers_submission_postgres.py && git commit -m "feat: add Writers author workflow"`

---

### Task 6: Restart-safe attachment staging to Telegram

**Files:**
- Create: `writers_submission/files.py`
- Create: `tests/test_writers_submission_files.py`
- Extend: `writers_submission/storage.py`
- Extend: `writers_submission/service.py`
- Extend: `tests/test_writers_submission_postgres.py`

**Interfaces:**
- Produces: `WritersFileService(bot, storage, config)`
- Method: `attach_from_temp(*, author_user_id: int, submission_id: UUID, temp_path: Path, original_filename: str, declared_mime: str, now: int) -> SubmissionFile`
- Method: `delete_attachment(author_user_id: int, submission_id: UUID, file_id: UUID, now: int) -> None`
- Storage methods persist/delete files only for the actor-owned current DRAFT revision and reject sealed revisions.
- File staging uses aiogram `FSInputFile` and `bot.send_document(chat_id=config.file_chat_id, ...)`.

- [ ] **Step 1: Write failing file-service tests**

Mock Telegram bot. Assert validation happens before upload; file-count limit is enforced; successful Telegram response persists `file_id`/unique id then deletes local temp; Telegram failure deletes temp and persists nothing; DB persistence failure after Telegram success deletes temp, exposes no ready attachment, and attempts best-effort staging-message cleanup; deleting another user's file is denied; deleting a sealed-revision file is denied.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_writers_submission_files -v`  
Expected: FAIL.

- [ ] **Step 3: Implement storage attachment primitives and service**

Use a single persisted ready state: no row becomes visible as an attachment until Telegram identifiers exist. Persist storage-chat message id for optional cleanup.

- [ ] **Step 4: Add PostgreSQL file isolation/count tests**

Verify exact revision ownership, max-count service invariant, restart persistence and sealed-file immutability.

- [ ] **Step 5: Run GREEN**

Run file tests plus Writers PostgreSQL tests. Expected: PASS.

- [ ] **Step 6: Commit**

`git add writers_submission/files.py writers_submission/storage.py writers_submission/service.py tests/test_writers_submission_files.py tests/test_writers_submission_postgres.py && git commit -m "feat: stage Writers files in Telegram"`

---

### Task 7: Atomic submit, immutable sealing and transactional outbox

**Files:**
- Extend: `writers_submission/storage.py`
- Extend: `writers_submission/service.py`
- Extend: `tests/test_writers_submission_service.py`
- Extend: `tests/test_writers_submission_postgres.py`

**Interfaces:**
- Service method: `submit(author_user_id: int, submission_id: UUID, expected_version: int, idempotency_key: str, now: int) -> SubmissionBundle`
- Storage transaction: `seal_and_submit(...)->SubmissionBundle`
- Outbox API: `claim_due_outbox(worker_id: str, now: int, lease_seconds: int, limit: int) -> list[OutboxRecord]`, `mark_outbox_delivered(...)`, `mark_outbox_retryable(...)`, `mark_outbox_permanent_failure(...)`

- [ ] **Step 1: Write failing submit tests**

Assert submit performs a fresh eligibility check, rejects missing work payload, rejects invalid/incomplete file state, seals exactly one revision, clears draft pointer, creates exactly one `MODERATION_CARD` outbox row, and duplicate idempotency key returns the original result.

- [ ] **Step 2: Write failing immutability/race PostgreSQL tests**

After submit, direct public storage update/delete methods cannot modify sealed revision/files. Two simultaneous submits on the same expected version produce one success and one conflict/idempotent result without duplicate outbox rows.

- [ ] **Step 3: Run RED**

Run focused service and PostgreSQL tests. Expected: FAIL.

- [ ] **Step 4: Implement transactional submit/outbox**

Lock submission/current revision, validate actor/state/version/ready files, seal revision, transition state, append audit event, insert unique outbox row and idempotency record in one transaction.

- [ ] **Step 5: Run GREEN**

Run focused tests. Expected: PASS.

- [ ] **Step 6: Commit**

`git add writers_submission/storage.py writers_submission/service.py tests/test_writers_submission_service.py tests/test_writers_submission_postgres.py && git commit -m "feat: submit immutable Writers revisions"`

---

### Task 8: Moderation state machine, concurrency and outbox delivery worker

**Files:**
- Create: `writers_submission/delivery.py`
- Create: `tests/test_writers_submission_delivery.py`
- Extend: `writers_submission/storage.py`
- Extend: `writers_submission/service.py`
- Extend: `tests/test_writers_submission_postgres.py`

**Interfaces:**
- Service: `claim(reviewer_user_id: int, submission_id: UUID, revision_id: UUID, now: int) -> ModerationResult`
- Service: `decide(reviewer_user_id: int, submission_id: UUID, revision_id: UUID, action: ReviewAction, comment: str | None, now: int) -> ModerationResult`
- Reviewer authorization is `reviewer_user_id in config.moderator_ids`.
- Produces: `WritersDeliveryWorker(bot, storage, config, *, poll_seconds=2, lease_seconds=60, batch_size=10)` with `start()`, `stop()`, `run_once(now: int) -> int`.

- [ ] **Step 1: Write failing moderation tests**

Assert non-allowlisted reviewer denied; claim changes `SUBMITTED -> IN_REVIEW`; exactly one winner under concurrent claim; only claimant can decide; approve/request-changes/reject legal transitions; duplicate decision callback returns existing result without duplicate review/event/outbox; decision-vs-withdraw race has one legal winner.

- [ ] **Step 2: Write failing delivery-worker tests**

Mock bot and storage. Assert moderation card escapes dynamic text, sends stored Telegram documents by `file_id`, marks delivered only after success, transient Telegram error schedules bounded retry, permanent Telegram error marks permanent failure, and stale `IN_FLIGHT` lease becomes reclaimable after lease timeout.

- [ ] **Step 3: Run RED**

Run: `python -m unittest tests.test_writers_submission_delivery -v` and focused service/PostgreSQL tests. Expected: FAIL.

- [ ] **Step 4: Implement moderation transactions and worker**

Use conditional SQL/row locks for claim/decision races. Backoff schedule: `min(300, 2 ** min(attempt_count, 8))` seconds plus deterministic small jitter derived from outbox id to avoid importing randomness into tests. Record only bounded error class/code.

- [ ] **Step 5: Add critical author-notification outbox events**

Approve, request changes, reject and withdrawal create `AUTHOR_NOTIFICATION` outbox rows in the same transaction as the durable state change. Notification delivery failure never rolls back the decision.

- [ ] **Step 6: Run GREEN**

Run delivery/service/PostgreSQL tests. Expected: PASS.

- [ ] **Step 7: Commit**

`git add writers_submission/delivery.py writers_submission/storage.py writers_submission/service.py tests/test_writers_submission_delivery.py tests/test_writers_submission_postgres.py && git commit -m "feat: add Writers moderation workflow"`

---

### Task 9: Private-chat Telegram entry and moderator callbacks

**Files:**
- Create: `writers_submission/handlers.py`
- Create: `tests/test_writers_submission_handlers.py`

**Interfaces:**
- Produces: `register_writers_submission_handlers(app, service, config) -> None`
- Handles private `/start writers_submit` and sends `InlineKeyboardButton(text="✒️ Отправить работу", web_app=WebAppInfo(url=config.public_url))`.
- Produces compact callback token codec with opaque submission/revision token lookup server-side.
- Maintains bounded in-memory pending-comment state keyed by `(moderator_user_id, moderation_token)` with 10-minute expiry.

- [ ] **Step 1: Write failing handler tests**

Assert group deep link does not itself grant access; private start sends exact WebApp button URL; non-moderator callback denied; callback actor id, not payload identity, is used; claim calls service once; duplicate decide is safe; request-changes enters comment state and final text calls service; expired comment state is rejected; Telegram card edit failure does not undo service result; user-controlled text is escaped.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_writers_submission_handlers -v`  
Expected: FAIL.

- [ ] **Step 3: Implement handlers**

Register only Writers Submission-specific commands/callback prefixes so existing `writers_moderation.py` and Zero Trust handlers remain untouched.

- [ ] **Step 4: Run GREEN**

Run handler tests. Expected: PASS.

- [ ] **Step 5: Commit**

`git add writers_submission/handlers.py tests/test_writers_submission_handlers.py && git commit -m "feat: add Writers Telegram submission controls"`

---

### Task 10: aiohttp session bootstrap, authenticated API and upload endpoint

**Files:**
- Create: `writers_submission/web.py`
- Create: `tests/test_writers_submission_web.py`

**Interfaces:**
- Produces: `create_writers_web_app(service, file_service, config, session_signer, bot_token: str) -> aiohttp.web.Application`
- Produces lifecycle wrapper: `WritersWebServer(app, host: str, port: int)` with `start()` and `stop()`.
- Routes:
  - `GET /writers/`
  - `POST /api/writers/session`
  - `DELETE /api/writers/session`
  - `GET /api/writers/submissions`
  - `POST /api/writers/submissions`
  - `GET /api/writers/submissions/{submission_id}`
  - `PATCH /api/writers/submissions/{submission_id}`
  - `POST /api/writers/submissions/{submission_id}/submit`
  - `POST /api/writers/submissions/{submission_id}/withdraw`
  - `POST /api/writers/submissions/{submission_id}/revisions`
  - `POST /api/writers/submissions/{submission_id}/files`
  - `DELETE /api/writers/submissions/{submission_id}/files/{file_id}`
  - `GET /api/writers/submissions/{submission_id}/history`

- [ ] **Step 1: Write failing HTTP security/session tests**

Using aiohttp test utilities, assert session bootstrap accepts valid initData and sets Secure/HttpOnly/SameSite=Strict cookie; invalid/expired initData denied; unauthenticated API denied; logout clears cookie; security headers exist; no wildcard CORS header; state-changing cookie-auth requests with wrong/missing same-origin `Origin` are denied.

- [ ] **Step 2: Write failing author API tests**

Assert stable JSON error codes, malformed JSON returns 400 without stack trace, oversized JSON rejected before service call, actor id comes from session only, cross-user opaque UUID maps to 404, stale version maps to 409, idempotency header is required on create/submit/withdraw/revision routes, and two-tab stale autosave cannot overwrite.

- [ ] **Step 3: Write failing multipart upload tests**

Assert stream limit applies while reading, max-file count is checked before staging, temp file always cleaned in request-finally paths, invalid file maps to safe 422, and successful upload returns metadata without server filesystem path.

- [ ] **Step 4: Run RED**

Run: `python -m unittest tests.test_writers_submission_web -v`  
Expected: FAIL.

- [ ] **Step 5: Implement middleware/routes/rate limiter**

Use per-actor in-process token/window buckets separated into read/update/create/upload/submit classes. Set request body limits; never call `request.post()` on unbounded multipart. Map domain errors to stable safe codes.

- [ ] **Step 6: Run GREEN**

Run HTTP tests. Expected: PASS.

- [ ] **Step 7: Commit**

`git add writers_submission/web.py tests/test_writers_submission_web.py && git commit -m "feat: add Writers Mini App API"`

---

### Task 11: Mini App frontend

**Files:**
- Create: `writers_submission/static/index.html`
- Create: `writers_submission/static/app.js`
- Create: `writers_submission/static/app.css`
- Extend: `tests/test_writers_submission_web.py`

**Interfaces:**
- Frontend bootstraps with `window.Telegram.WebApp.initData` to `POST /api/writers/session`, then relies on HttpOnly cookie.
- Debounced autosave sends `expected_version`; 409 conflict stops autosave and shows reload-required state.
- UI supports list/filter, editor, text counters, attachment list, submit, withdraw, revision creation and timeline.

- [ ] **Step 1: Add failing static-contract tests**

HTTP test reads served assets and asserts: Telegram Web App script is present, no embedded BOT_TOKEN/DSN/moderator ids, `textContent`/DOM-safe rendering helpers are used instead of assigning server content to `innerHTML`, autosave includes version, submit/revision/create generate idempotency keys, and 409 conflict has explicit UI handling.

- [ ] **Step 2: Run RED**

Run focused static/web tests. Expected: FAIL.

- [ ] **Step 3: Implement mobile-first Mini App**

Build four views in plain JS: workspace list, editor, submission detail/timeline, changes-requested revision action. Use Telegram theme CSS variables with sensible browser fallbacks. Disable submit while upload/autosave is pending.

- [ ] **Step 4: Run GREEN**

Run web tests and manually syntax-check via `python -m compileall -q .` for Python; static contract tests must PASS.

- [ ] **Step 5: Commit**

`git add writers_submission/static tests/test_writers_submission_web.py && git commit -m "feat: add Writers Mini App frontend"`

---

### Task 12: Runtime lifecycle integration and current-writer eligibility

**Files:**
- Modify: `main.py`
- Extend: `writers_submission/__init__.py`
- Extend: `tests/test_writers_submission_config.py`
- Create or extend: `tests/test_writers_submission_startup.py`

**Interfaces:**
- Add `TelegramWritersEligibilityChecker(bot, writers_chat_id)` accepting member/administrator/creator states and failing closed on Bot API errors.
- `main.py` constructs Writers Submission only when enabled, after env loading and before polling.
- Shutdown order: stop Writers delivery worker -> stop Writers web server -> close Writers storage; existing supervisor/storage shutdown order otherwise remains intact.

- [ ] **Step 1: Write failing startup tests**

Assert disabled mode constructs no web listener/storage/handlers; enabled mode requires DB and all config; startup order initializes storage before handler/web/worker registration; bind/start failure aborts before polling; shutdown calls all started Writers resources exactly once even after partial startup failure; eligibility rejects left/kicked/restricted-nonmember and Bot API exceptions.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_writers_submission_startup -v`  
Expected: FAIL.

- [ ] **Step 3: Wire feature into `main.py`**

Reuse existing `DATABASE_URL`, bot instance and `WRITERS_CHAT_ID`. Do not modify Zero Trust/writers moderation ownership. Print safe readiness diagnostics only.

- [ ] **Step 4: Run GREEN**

Run startup tests plus existing Zero Trust startup tests:  
`python -m unittest tests.test_writers_submission_startup tests.test_zero_trust_startup -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

`git add main.py writers_submission tests/test_writers_submission_startup.py && git commit -m "feat: wire Writers Submission runtime"`

---

### Task 13: CI, runbook and full acceptance verification

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Create: `docs/writers-submission-v1.md`

**Interfaces:**
- Existing `test` job remains compileall + full unittest discovery.
- Existing PostgreSQL 17 job adds `tests.test_writers_submission_postgres`; rename the job only if necessary, not its behavior.
- Runbook documents exact env variables, private storage/mod chat setup, bot permissions, rollout, smoke test and rollback.

- [ ] **Step 1: Extend CI PostgreSQL command**

Append `tests.test_writers_submission_postgres` to the existing PostgreSQL command. Do not remove Entertainment or Zero Trust suites.

- [ ] **Step 2: Write operator runbook**

Document:
  - feature disabled by default;
  - HTTPS Mini App URL;
  - moderator ids;
  - private moderation and file-storage chat ids;
  - bot must be able to send/edit messages and documents in those chats;
  - no real credentials in Git;
  - rollout with one operator test submission;
  - rollback via `WRITERS_SUBMISSION_ENABLED=0`;
  - existing submissions stay in PostgreSQL after rollback.

- [ ] **Step 3: Run complete local verification**

Run:
`python -m compileall -q .`  
`python -m unittest discover -s tests -p "test_*.py"`

Expected: all non-environment tests PASS; PostgreSQL tests may be skipped locally only when `TEST_DATABASE_URL` is absent.

- [ ] **Step 4: Run PostgreSQL integration verification**

With PostgreSQL 17 and `TEST_DATABASE_URL`:
`python -m unittest tests.test_entertainment_storage_postgres tests.test_entertainment_storage_factory tests.test_entertainment_memory_postgres tests.test_entertainment_culture_memory_postgres tests.test_entertainment_culture_windows_postgres tests.test_entertainment_message_purge tests.test_zero_trust_postgres tests.test_writers_submission_postgres -v`

Expected: PASS.

- [ ] **Step 5: Acceptance checklist**

Confirm against the spec:
  - disabled mode leaves bot unchanged;
  - restart-safe draft and outbox;
  - cross-user UUID access denied;
  - one immutable revision + one moderation job on submit;
  - duplicate submit cannot duplicate durable work;
  - file limits/types/temp cleanup;
  - sealed revision immutable;
  - concurrent moderator claim exactly one winner;
  - unauthorized moderator blocked;
  - changes-requested creates N+1 without changing N;
  - Telegram delivery retry survives worker reconstruction;
  - no credentials/initData/uploaded binary in repository or logs;
  - docs cover enable/disable and operator smoke test.

- [ ] **Step 6: Commit**

`git add .github/workflows/ci.yml README.md docs/writers-submission-v1.md && git commit -m "docs: finish Writers Submission v1 rollout"`

---

## Plan Self-Review

**Spec coverage:** Tasks 1-13 cover explicit enablement/config, private Telegram entry, initData bootstrap/session security, eligibility, same-origin headers, all data entities, ownership/versioning, draft/revision semantics, Telegram-backed file durability, atomic submit/outbox, moderation races/idempotency, notifications, aiohttp API, Mini App UX, lifecycle, CI, observability constraints and rollback. No spec section is left without an owning task.

**Step scan:** Each task has a concrete RED test, verification command, implementation boundary and commit. Implementation steps name signatures/invariants but do not transcribe complete function bodies.

**Type consistency:** `UUID` is the public submission/revision/file identifier throughout; actor ids are Telegram `int`; versions are integer optimistic-lock values; service methods are the only HTTP/handler business boundary; storage owns transactions; outbox worker consumes persisted `OutboxRecord`.

**Review Focus coverage:** malformed/duplicate initData is Task 2; stale autosave is Tasks 5/10; hostile DOCX metadata is Task 3; Telegram-success/DB-failure attachment cleanup is Task 6; stale outbox lease recovery is Task 8.

**Proportion:** The plan fixes interfaces, tests and sequencing while leaving implementation bodies to the executing engineer. No new product subsystem beyond the approved Writers Submission v1 scope was added.
