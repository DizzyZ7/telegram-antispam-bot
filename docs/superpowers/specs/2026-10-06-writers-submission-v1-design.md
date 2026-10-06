# Writers Submission v1 — Design

Date: 2026-10-06
Status: approved conversational design, written spec pending final review
Branch: `design/writers-submission-v1`

## 1. Purpose

Writers Submission v1 adds a restart-safe Telegram Mini App flow for authors in the writers community to prepare, submit, revise and track works without turning the existing writers moderation module into a monolith.

The subsystem must let an author:

- open a Mini App from the bot;
- see their own drafts and submitted works;
- create and autosave a draft;
- provide a title, work type, genre, short description and main text;
- optionally attach a link and up to three supported files;
- submit an immutable revision for moderation;
- see review state and moderation feedback;
- create a new revision when changes are requested;
- withdraw a non-final submission;
- receive Telegram notifications for meaningful state changes.

Moderators must be able to claim, review and decide submissions from a private Telegram moderation chat. PostgreSQL is the source of truth for submission state, ownership, revisions, moderation actions and delivery state.

This subsystem is deliberately separate from `writers_moderation.py`. The existing writers moderation/rules layer and Zero Trust captcha ownership remain unchanged.

## 2. Existing constraints

The current application is a single asyncio Python process. It already uses:

- aiogram for Telegram;
- asyncpg/PostgreSQL for restart-safe Zero Trust and Entertainment persistence;
- `main.py` as the production lifecycle owner;
- explicit startup/shutdown handling for storage and background services.

Writers Submission v1 should fit that runtime rather than introducing a second deployment or a Node build pipeline.

The web layer therefore uses a small `aiohttp` application started and stopped by `main.py`. `aiohttp` is declared explicitly in `requirements.txt`; the subsystem must not rely on it being present only as a transitive dependency.

The Mini App frontend is plain HTML/CSS/JavaScript. React/Vite, a separate SPA build step and a separate web service are intentionally out of scope for v1.

## 3. High-level architecture

New package:

```text
writers_submission/
├── __init__.py
├── config.py
├── models.py
├── security.py
├── storage.py
├── service.py
├── handlers.py
├── web.py
├── delivery.py
└── static/
    ├── index.html
    ├── app.js
    └── app.css
```

Responsibilities:

- `config.py`: parse and validate Writers Submission environment configuration.
- `models.py`: enums and immutable service DTOs.
- `security.py`: Telegram Mini App `initData` verification, request authentication helpers and upload validation.
- `storage.py`: PostgreSQL schema/bootstrap and atomic persistence operations.
- `service.py`: ownership, state-machine, revision and moderation business rules.
- `handlers.py`: Telegram entry button, moderator callbacks and author notifications.
- `web.py`: aiohttp static routes and JSON API.
- `delivery.py`: restart-safe moderation-card/file outbox worker.
- `static/*`: mobile-first Mini App.

`main.py` owns construction and lifecycle of the storage, service, web server and delivery worker.

## 4. Deployment and configuration

Writers Submission is explicitly enabled. A partially configured subsystem must fail closed rather than silently expose an unauthenticated form.

Proposed environment variables:

```text
WRITERS_SUBMISSION_ENABLED=0
WRITERS_SUBMISSION_PUBLIC_URL=https://example.invalid/writers/
WRITERS_SUBMISSION_MOD_CHAT_ID=-100...
WRITERS_SUBMISSION_MODERATOR_IDS=12345,67890
WRITERS_SUBMISSION_BIND_HOST=0.0.0.0
WRITERS_SUBMISSION_PORT=8080
WRITERS_SUBMISSION_INIT_DATA_MAX_AGE_SECONDS=900
WRITERS_SUBMISSION_MAX_FILE_BYTES=20971520
WRITERS_SUBMISSION_MAX_FILES=3
WRITERS_SUBMISSION_RATE_LIMIT_WINDOW_SECONDS=60
```

`WRITERS_CHAT_ID` remains the writers-community scope and is reused instead of defining a second source-chat setting.

When `WRITERS_SUBMISSION_ENABLED=1`, startup requires:

- a valid `DATABASE_URL`;
- an HTTPS `WRITERS_SUBMISSION_PUBLIC_URL` outside explicitly allowed local development mode;
- a valid moderation chat id;
- at least one numeric moderator id;
- a bot token already available to the main application;
- valid positive limits.

If these invariants are not met, startup raises an actionable configuration error. With the subsystem disabled, existing bot behavior remains unchanged and no web listener is started.

## 5. Authentication and authorization

### 5.1 Telegram identity

The browser is never trusted to supply an author id.

Every authenticated API request carries the original Telegram Web App `initData` in a dedicated request header. The backend verifies it using Telegram's documented HMAC procedure derived from the bot token, verifies `auth_date`, rejects stale payloads, parses the signed user object and obtains `user_id` only from that verified payload.

The server never accepts `user_id`, reviewer id or chat id from frontend JSON as an authority boundary.

Raw `initData`, its hash material and the bot token must never be logged or persisted.

### 5.2 Writers-community membership

Viewing already-owned submissions requires only valid Telegram identity. This preserves access to the author's own history if they later leave the writers chat.

Creating a new work or submitting a revision additionally requires current eligibility for the writers community. V1 performs a fail-closed `getChatMember(WRITERS_CHAT_ID, user_id)` check at the eligibility boundary and accepts only normal member/administrator/creator states. The result may be cached in memory for a short bounded interval to avoid a Bot API call on every autosave, but submit always performs a fresh eligibility check.

Zero Trust remains responsible for entry verification into the writers chat. Writers Submission does not create a second captcha or mutate Zero Trust state.

### 5.3 Moderator authorization

Moderator callbacks are accepted only when the Telegram actor id is present in the explicit `WRITERS_SUBMISSION_MODERATOR_IDS` allowlist. Merely being able to see or forward a moderation message does not grant review authority.

Every moderator action re-checks authorization server-side before touching storage.

### 5.4 Same-origin web security

The web API is same-origin with the Mini App. V1 does not enable permissive CORS.

Responses set at least:

- `Content-Security-Policy` allowing the local application and the official Telegram Web App script origin only as required;
- `X-Content-Type-Options: nosniff`;
- `Referrer-Policy: no-referrer`;
- a restrictive `Permissions-Policy` for unused browser capabilities.

No bot token, DSN, moderator id list or other server secret is emitted to frontend assets.

## 6. Data model

Public/API identities use UUIDs or equivalent unguessable identifiers. Internal database surrogate keys may exist but are never used as the frontend authorization boundary.

### 6.1 `writers_submissions`

One logical work thread per author.

Core fields:

- `id` UUID primary key;
- `writers_chat_id` bigint;
- `author_user_id` bigint;
- `status` enum/text constrained to the state machine;
- `current_draft_revision_id` nullable;
- `current_submitted_revision_id` nullable;
- `claimed_by_user_id` nullable;
- `claimed_at` nullable;
- `created_at`, `updated_at`;
- `version` integer for optimistic/concurrent transition protection.

Indexes include `(author_user_id, updated_at)` and moderation queue status indexes.

### 6.2 `writers_submission_revisions`

A submission can have multiple revisions.

Core fields:

- `id` UUID primary key;
- `submission_id` FK;
- `revision_number` positive integer, unique within one submission;
- `state` (`DRAFT` or `SEALED`);
- `title`;
- `work_type`;
- `genre`;
- `description`;
- `body_text`;
- `external_url` nullable;
- `created_at`, `updated_at`, `sealed_at` nullable.

A `DRAFT` revision may be edited by its owner. `submit` atomically seals it. A `SEALED` revision is immutable at the storage/service boundary.

When moderation requests changes, the author creates a new draft revision derived from the previous sealed revision. The prior revision remains unchanged.

### 6.3 `writers_submission_files`

Core fields:

- `id` UUID primary key;
- `submission_id` FK;
- `revision_id` FK;
- original display filename after safe normalization;
- declared MIME;
- detected file class;
- byte size;
- checksum (SHA-256);
- `telegram_file_id` nullable until delivered;
- `telegram_file_unique_id` nullable;
- upload/delivery timestamps.

A file belongs to one revision. Files attached to a sealed revision cannot be replaced or deleted.

Binary file contents are not stored permanently in PostgreSQL.

### 6.4 `writers_submission_reviews`

Records moderation decisions without overwriting history.

Fields include:

- `id` UUID;
- `submission_id`;
- `revision_id`;
- reviewer Telegram id;
- action (`CLAIM`, `APPROVE`, `REQUEST_CHANGES`, `REJECT`);
- optional moderation comment;
- timestamp.

### 6.5 `writers_submission_events`

Append-only audit timeline.

Events include draft creation, update, file attach/delete, submit, delivery, claim, decision, withdrawal and revision creation. Event metadata is bounded and sanitized. It must not duplicate raw `initData`, bot credentials or full uploaded binary content.

### 6.6 `writers_submission_outbox`

A transactional outbox makes Telegram moderation delivery restart-safe.

Fields include:

- opaque event id;
- submission/revision id;
- event type;
- state (`PENDING`, `IN_FLIGHT`, `DELIVERED`, `RETRYABLE_FAILED`, `PERMANENT_FAILED`);
- attempt count;
- next-attempt timestamp;
- last bounded error class/code without secret payloads;
- created/updated timestamps.

Creating a submitted revision and creating its moderation-delivery outbox row occur in one PostgreSQL transaction.

## 7. Submission state machine

Top-level statuses:

```text
DRAFT
  -> SUBMITTED

SUBMITTED
  -> IN_REVIEW
  -> WITHDRAWN

IN_REVIEW
  -> APPROVED
  -> CHANGES_REQUESTED
  -> REJECTED
  -> WITHDRAWN

CHANGES_REQUESTED
  -> DRAFT        (new revision)
  -> WITHDRAWN

APPROVED          terminal
REJECTED          terminal
WITHDRAWN         terminal
```

A moderator claim is atomic. Two moderators clicking `claim` concurrently cannot both become the reviewer. Storage uses a conditional update / row lock and returns the winner.

Decision writes are conditional on the expected current state and reviewer ownership. Duplicate callbacks are idempotent: they return the already-applied result and do not produce duplicate state transitions or duplicate author notifications.

`WITHDRAWN` is allowed only before a terminal moderation decision. A decision and withdrawal racing each other are serialized transactionally; one transition wins and the other receives the resulting state rather than overwriting it.

## 8. Draft and revision semantics

Draft autosave updates only the current author's current `DRAFT` revision.

Submission performs one transaction that:

1. locks the submission/current draft;
2. validates ownership and current state;
3. validates required fields and attachments;
4. seals the current revision;
5. changes top-level status to `SUBMITTED`;
6. sets `current_submitted_revision_id`;
7. appends audit events;
8. inserts moderation-delivery outbox work.

No API exists to modify a sealed revision.

`CHANGES_REQUESTED -> DRAFT` creates revision `N+1`, normally prefilled from revision `N`. It does not mutate revision `N`.

## 9. Form and validation

V1 fields:

- title — required;
- work type — required;
- genre — required but represented as free text or a small UI preset plus custom value; the server stores text, not a hard product taxonomy;
- short description — required;
- main text — required unless at least one supported file contains the work; the service still requires enough metadata to understand the submission;
- external link — optional HTTPS URL;
- files — optional, up to configured maximum (default three).

Server-side validation is authoritative. Frontend validation exists only for UX.

All user text has bounded lengths. Exact bounds are constants/configured defaults chosen in implementation and covered by tests; the API rejects oversized payloads before persistence.

HTML is never trusted. The Mini App renders user content as text, not raw HTML. Telegram moderation messages escape dynamic content before HTML/Markdown formatting.

## 10. File handling

Default v1 file rules:

- maximum 20 MiB per file;
- maximum three files per revision;
- supported extensions: `.pdf`, `.docx`, `.txt`;
- supported MIME classes are mapped explicitly;
- extension, declared MIME and file signature/header must be mutually compatible;
- PDF requires a PDF signature;
- TXT must pass bounded text/binary sanity checks;
- DOCX requires the ZIP/container signature but is not executed or rendered server-side in v1.

V1 does not parse macros, execute documents, render PDFs, run OCR or automatically fetch links.

Uploads are streamed/bounded to temporary storage rather than read without limit. Filenames are normalized to safe display names; filesystem paths are generated by the server and never taken from the supplied filename.

Temporary file lifetime is strictly bounded. After successful Telegram document delivery and persistence of Telegram `file_id`/`file_unique_id`, the temporary copy is removed. On retryable delivery failure it may remain only within a bounded retry window; terminal cleanup removes it.

A revision cannot be submitted while an attached file is in an invalid or incomplete upload state.

External links must be HTTPS and are stored/displayed only. The backend never requests them, eliminating SSRF through the submission URL field.

## 11. Telegram moderation delivery

Submitting a revision creates a private moderation-card delivery job.

The delivery worker sends to `WRITERS_SUBMISSION_MOD_CHAT_ID`:

- author display identity and numeric id;
- submission/revision reference;
- title, type, genre and description;
- bounded text preview;
- external link when present;
- attached Telegram documents;
- inline moderator controls.

Controls:

- `Взять на проверку`;
- `Одобрить`;
- `Нужны правки`;
- `Отклонить`.

Callback payloads use a compact opaque moderation token that maps server-side to the target submission/revision. The payload does not trust a frontend-supplied reviewer identity and stays within Telegram callback-size limits.

For actions requiring a free-form comment (`REQUEST_CHANGES`, optionally `REJECT`), v1 uses a small moderator conversation state keyed by moderator + moderation token with a bounded expiry. The final decision is persisted only after the comment is received or an explicit no-comment path is chosen where allowed.

The moderation card is updated after claim/decision when possible, but PostgreSQL remains authoritative if message editing fails.

## 12. Delivery failure semantics

A submission is durably accepted when PostgreSQL commits the sealed revision and outbox event. Telegram delivery is asynchronous and retryable.

Therefore:

- a transient Telegram failure does not lose the submission;
- the outbox retries with bounded exponential backoff and jitter;
- restart resumes pending/retryable jobs;
- successful delivery records Telegram ids and marks the outbox delivered;
- permanent failure is surfaced in operator logs/diagnostics and remains visible as a technical delivery failure rather than pretending moderators received the work.

Author UX distinguishes `submission accepted` from `moderation delivery pending` only when delivery is materially delayed; normal fast delivery should not expose implementation details.

A file selected as part of the sealed revision must be durably uploaded and eligible for Telegram delivery before submit commits. If local staging/upload validation failed, submit is rejected and the revision remains draft.

## 13. Author notifications

Significant transitions enqueue/send private Telegram notifications:

- submission accepted;
- reviewer claimed the work (optional UX notification, configurable later; v1 may omit to reduce noise);
- approved;
- changes requested with moderator comment;
- rejected with permitted comment;
- withdrawal confirmation;
- serious moderation-delivery problem when action from the author is required.

Notification failure never rewrites the durable moderation decision. It is logged safely and may use the same outbox pattern where required for restart-safe delivery.

## 14. Mini App UX

### 14.1 Home — `Мои работы`

Shows the current author's submissions sorted by most recently updated, grouped or filtered by:

- drafts;
- under review;
- needs changes;
- completed/closed.

Each card shows title, revision, human-readable status and last update time.

### 14.2 Editor

Mobile-first editor with:

- title;
- type;
- genre;
- description;
- body text;
- optional HTTPS link;
- attachment list;
- autosave indicator;
- text counters;
- `Сохранить черновик`;
- `Отправить`.

Autosave is debounced and uses an expected revision/version so stale browser tabs cannot silently overwrite newer edits. Conflict responses prompt the client to reload instead of last-write-wins data loss.

### 14.3 Submission timeline

The detail screen renders a bounded audit-derived timeline such as:

```text
Черновик создан
Работа отправлена
Взята на проверку
Нужны правки: <comment>
Редакция #2 создана
Работа отправлена
Одобрено
```

Internal delivery retries and sensitive operator metadata are not exposed as author timeline events unless they affect the author's required action.

## 15. HTTP/API surface

All API routes are under `/api/writers` and require verified Telegram `initData` except static health/public assets explicitly designed otherwise.

Proposed author routes:

```text
GET    /writers/
GET    /api/writers/submissions
POST   /api/writers/submissions
GET    /api/writers/submissions/{submission_id}
PATCH  /api/writers/submissions/{submission_id}
POST   /api/writers/submissions/{submission_id}/submit
POST   /api/writers/submissions/{submission_id}/withdraw
POST   /api/writers/submissions/{submission_id}/revisions
POST   /api/writers/submissions/{submission_id}/files
DELETE /api/writers/submissions/{submission_id}/files/{file_id}
GET    /api/writers/submissions/{submission_id}/history
```

No public moderator HTTP API is required in v1; moderation uses Telegram handlers.

Error responses use stable machine-readable codes plus safe human messages. Storage/stack traces are never sent to the browser.

State-changing endpoints support idempotency where duplicate browser retries are plausible, especially create/submit/withdraw/revision creation. Idempotency keys are scoped to verified user id + endpoint semantics and have bounded retention.

## 16. Rate limiting

V1 uses an in-process bounded rate limiter for cheap abuse control plus hard server-side payload/upload limits. Because rate-limit state is not an authorization invariant, restart reset is acceptable.

Separate buckets exist for:

- list/read;
- autosave/update;
- create/revision creation;
- upload;
- submit/withdraw.

Persistent PostgreSQL constraints and idempotency are still required, so bypassing/resetting the convenience rate limiter cannot create duplicate durable transitions.

## 17. Lifecycle integration

`main.py` should follow the existing explicit lifecycle style.

When enabled:

1. validate Writers Submission config;
2. reuse the validated PostgreSQL `DATABASE_URL`;
3. initialize `PostgresWritersSubmissionStorage` and idempotent schema;
4. create service;
5. register bot entry/moderator handlers;
6. start aiohttp runner/site;
7. start bounded delivery worker;
8. start normal bot polling;
9. on shutdown stop worker and web runner, then close storage/pools in deterministic order.

No unbounded orphan tasks may survive shutdown.

If the web listener cannot bind while the subsystem is enabled, startup fails rather than running a button that points to a dead Mini App.

## 18. Database/bootstrap policy

Schema creation/upgrades are idempotent and versioned in the same practical style as the existing project. The implementation must not destructively rewrite unrelated Entertainment or Zero Trust tables.

All multi-row state transitions use transactions.

Important database invariants include:

- submission ownership cannot change;
- `(submission_id, revision_number)` unique;
- one active current draft pointer at most;
- sealed revision content cannot be updated through storage methods;
- file count cannot exceed service/config rules;
- moderation claim/decision is conditional on expected state;
- outbox delivery event for a submitted revision is unique/idempotent;
- audit events are append-only through the public storage interface.

## 19. Observability and privacy

Startup diagnostics may log:

- feature enabled/disabled;
- bind host/port;
- writers chat id;
- moderation chat id;
- moderator count;
- upload limits;
- outbox worker readiness;
- schema version.

Runtime logs may contain opaque submission ids, actor numeric ids when operationally necessary, state names and safe error classes.

Runtime logs must not contain:

- bot token;
- `DATABASE_URL`;
- raw Telegram `initData`;
- full work text;
- full moderation comments by default;
- uploaded file bytes;
- arbitrary external-link response data (links are never fetched).

## 20. Testing strategy

Development follows RED -> GREEN for each behavior slice.

### 20.1 Pure/unit tests

- config parsing/fail-closed validation;
- Telegram `initData` valid signature, wrong signature and expired auth;
- user id is derived only from signed data;
- text/link validation;
- upload extension/MIME/signature compatibility;
- state-machine allowed/forbidden transitions;
- sealed revision immutability;
- safe rendering/escaping;
- callback-token parsing without trusting actor identity;
- rate-limit bucket behavior.

### 20.2 Service tests

- author can access only their own submissions;
- cross-user GET/PATCH/file/history access is denied;
- current writers eligibility required for create/submit;
- autosave conflict detection;
- idempotent create/submit/withdraw/revision creation;
- submit seals revision atomically;
- changes requested creates a new editable revision without mutating old content;
- final states reject later mutation;
- moderator allowlist enforced;
- duplicate moderator callbacks do not duplicate decisions/notifications.

### 20.3 PostgreSQL integration tests

Run in the existing PostgreSQL CI job or a clearly scoped extension of it.

Must cover:

- restart persistence;
- author isolation;
- writers-chat scope persistence;
- concurrent claim: exactly one winner;
- decision vs withdrawal race: exactly one legal transition wins;
- revision uniqueness/immutability;
- transactional submit + outbox creation;
- retryable outbox persistence across reconstructed service/worker;
- idempotency uniqueness;
- file metadata persistence;
- exact UUID ownership checks.

### 20.4 HTTP tests

Using aiohttp test utilities or equivalent:

- static Mini App route;
- unauthenticated API denied;
- invalid/expired `initData` denied;
- payload too large rejected before persistence;
- malformed JSON safe error;
- cross-user opaque id still denied;
- upload count/size/type limits;
- security headers present;
- no permissive CORS;
- stable API error codes.

### 20.5 Telegram handler tests

- WebApp button uses configured public URL;
- non-moderator callback denied;
- claim callback uses atomic service result;
- decision callback is idempotent;
- moderation card escapes user-controlled text;
- Telegram message-edit failure does not undo DB state;
- author notification failure does not undo DB state.

## 21. Acceptance criteria

Writers Submission v1 is ready to merge only when all of the following are true:

1. Existing bot behavior remains green with the feature disabled.
2. A valid writers user can create/autosave a draft and restart the process without losing it.
3. Another Telegram user cannot read, modify, withdraw or attach files to that draft even when given its UUID.
4. Submission creates one immutable sealed revision and one durable moderation-delivery job atomically.
5. A duplicate submit request cannot create a second sealed revision or second moderation job.
6. Uploaded file validation enforces count, byte and type limits and leaves no unbounded temporary files.
7. A submitted revision cannot be modified.
8. Two moderators claiming concurrently produce exactly one owner.
9. Unauthorized moderators cannot claim or decide.
10. Changes-requested workflow creates revision N+1 and preserves N byte-for-byte at the service/storage contract level.
11. Telegram delivery failure is restart-safe and does not lose accepted work.
12. PostgreSQL/HTTP/handler tests pass in CI alongside the full existing unit suite.
13. No credentials, real DSN, raw `initData` or uploaded content are committed to Git.
14. Documentation explains env, startup behavior, moderation workflow and rollback/disable path.

## 22. Explicit non-goals for v1

The following are intentionally deferred:

- separate moderator web dashboard;
- rich collaborative editor;
- line-level comments/annotations;
- S3/object-storage dependency;
- OCR/PDF rendering;
- DOCX document parsing;
- automatic external-link downloading;
- antivirus/malware scanning service integration;
- plagiarism detection;
- AI review/scoring;
- public author profiles;
- ratings/leaderboards;
- billing;
- external identity provider;
- React/Vite/Node build pipeline.

These can be added as later subsystems without weakening v1 ownership and revision invariants.

## 23. Rollout and rollback

Rollout is controlled by `WRITERS_SUBMISSION_ENABLED`.

Initial deployment sequence:

1. deploy schema/code with feature disabled and run the full test/CI suite;
2. configure public HTTPS URL, moderation chat and moderator allowlist;
3. enable the feature;
4. verify web health/startup diagnostics;
5. submit one operator test draft/revision and verify moderation delivery/decision;
6. expose the normal Writers entry button.

Emergency rollback sets `WRITERS_SUBMISSION_ENABLED=0` and restarts the process. Existing submissions remain in PostgreSQL untouched. Disabling Writers Submission must not disable writers moderation, Zero Trust, Entertainment, Lexicon or other bot features.

## 24. Implementation boundary

The implementation should add the subsystem without rewriting `writers_moderation.py`, `zero_trust/*` or unrelated Entertainment logic.

Necessary integration changes are expected in:

- `main.py` lifecycle wiring;
- `requirements.txt` (`aiohttp` explicit dependency);
- `.env.example`;
- `.github/workflows/ci.yml` for PostgreSQL/HTTP coverage if needed;
- README/operator documentation;
- new `writers_submission/*` package;
- new focused tests.

Any need to alter Zero Trust state semantics, writers profanity moderation semantics, Entertainment generation/memory semantics or unrelated game behavior is out of scope and requires a separate design decision.
