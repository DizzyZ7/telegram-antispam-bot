# Writers Submission v1 — Design

Date: 2026-10-06
Status: conversational design approved; written spec pending final user review
Branch: `design/writers-submission-v1`

## 1. Purpose

Writers Submission v1 adds a restart-safe Telegram Mini App flow for authors in the writers community to prepare, submit, revise and track works without turning the existing writers moderation module into a monolith.

The subsystem must let an author:

- enter through the bot and open a Mini App in private chat;
- see only their own drafts and submitted works;
- create and autosave a draft;
- provide a title, work type, genre and short description;
- provide the work as main text and/or supported files;
- optionally attach an HTTPS link;
- submit an immutable revision for moderation;
- see review state and moderation feedback;
- create a new revision when changes are requested;
- withdraw a non-final submission;
- receive Telegram notifications for meaningful state changes.

Moderators must be able to claim, review and decide submissions from a private Telegram moderation chat. PostgreSQL is the source of truth for ownership, revisions, workflow state, moderation actions, audit history and delivery state.

This subsystem is deliberately separate from `writers_moderation.py`. Existing writers moderation/rules behavior and Zero Trust captcha ownership remain unchanged.

## 2. Existing constraints and selected approach

The current application is a single asyncio Python process. It already uses:

- aiogram for Telegram;
- asyncpg/PostgreSQL for restart-safe Zero Trust and Entertainment persistence;
- `main.py` as the production lifecycle owner;
- explicit startup/shutdown handling for storage and background services.

Writers Submission v1 therefore stays in that process rather than introducing a second deployment.

The web layer uses a small `aiohttp` application started and stopped by `main.py`. `aiohttp` is declared explicitly in `requirements.txt`; the subsystem must not rely on a transitive dependency.

The Mini App frontend is plain HTML/CSS/JavaScript. React/Vite, a separate SPA build step and a separate Node service are intentionally out of scope for v1.

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
- `security.py`: Telegram Mini App `initData` verification, application-session signing, request authentication and upload validation.
- `storage.py`: PostgreSQL schema/bootstrap and atomic persistence operations.
- `service.py`: ownership, state-machine, revision, file and moderation business rules.
- `handlers.py`: private-chat entry flow, moderator callbacks and author notifications.
- `web.py`: aiohttp static routes and JSON API.
- `delivery.py`: restart-safe moderation-card/notification outbox worker.
- `static/*`: mobile-first Mini App.

`main.py` owns construction and lifecycle of storage, service, web server and delivery worker.

## 4. Entry flow

The writers group must not depend on a direct group `WebAppInfo` launch.

The supported flow is:

1. a writers-chat command/button/link points to a Telegram deep link such as `https://t.me/<bot>?start=writers_submit`;
2. the user opens the bot in private chat;
3. the bot handles the start parameter and sends the private-chat button `✒️ Отправить работу` with `WebAppInfo(WRITERS_SUBMISSION_PUBLIC_URL)`;
4. opening that button gives the Mini App Telegram `initData`;
5. the Mini App exchanges fresh verified `initData` for an application session and loads the author's workspace.

The bot may also expose the same private-chat entry from its normal command/menu UX. The group deep link is a convenience, not an authorization boundary.

Because the author must open the bot privately before using the Mini App, later private notifications normally have a valid bot-user conversation. Notification failure is still handled safely.

## 5. Deployment and configuration

Writers Submission is explicitly enabled. A partially configured subsystem must fail closed rather than silently expose an unauthenticated form.

Proposed environment variables:

```text
WRITERS_SUBMISSION_ENABLED=0
WRITERS_SUBMISSION_PUBLIC_URL=https://example.invalid/writers/
WRITERS_SUBMISSION_MOD_CHAT_ID=-100...
WRITERS_SUBMISSION_FILE_CHAT_ID=-100...
WRITERS_SUBMISSION_MODERATOR_IDS=12345,67890
WRITERS_SUBMISSION_BIND_HOST=0.0.0.0
WRITERS_SUBMISSION_PORT=8080
WRITERS_SUBMISSION_INIT_DATA_MAX_AGE_SECONDS=900
WRITERS_SUBMISSION_SESSION_TTL_SECONDS=43200
WRITERS_SUBMISSION_MAX_FILE_BYTES=20971520
WRITERS_SUBMISSION_MAX_FILES=3
WRITERS_SUBMISSION_RATE_LIMIT_WINDOW_SECONDS=60
```

`WRITERS_CHAT_ID` remains the writers-community scope and is reused instead of defining a second source-chat setting.

`WRITERS_SUBMISSION_FILE_CHAT_ID` is a private Telegram staging/storage chat used only to turn validated uploads into reusable Telegram `file_id` values. It may equal the moderation chat, but a separate private chat is recommended to avoid clutter.

When `WRITERS_SUBMISSION_ENABLED=1`, startup requires:

- a valid `DATABASE_URL`;
- an HTTPS `WRITERS_SUBMISSION_PUBLIC_URL` outside explicitly allowed local development mode;
- valid moderation and file-storage chat ids;
- at least one numeric moderator id;
- a bot token already available to the application;
- valid positive TTL/file/rate limits.

If these invariants are not met, startup raises an actionable configuration error. With the subsystem disabled, existing bot behavior remains unchanged and no Writers web listener is started.

## 6. Authentication and authorization

### 6.1 Telegram bootstrap identity

The browser is never trusted to supply an author id.

On session bootstrap, the frontend sends the original Telegram Web App `initData`. The backend:

1. verifies it using Telegram's documented HMAC procedure derived from the bot token;
2. verifies `auth_date` against `WRITERS_SUBMISSION_INIT_DATA_MAX_AGE_SECONDS`;
3. parses the signed Telegram user object;
4. obtains `user_id` only from that verified payload.

The server never accepts `user_id`, reviewer id or writers chat id from frontend JSON as an authority boundary.

Raw `initData`, its hash material and the bot token must never be logged or persisted.

### 6.2 Application session

Requiring the same original `initData` on every autosave would make long editing sessions fail once the bootstrap freshness window expires. Therefore fresh verified `initData` is exchanged for a bounded application session.

V1 uses a Secure, HttpOnly, SameSite cookie containing or referencing an authenticated session signed by the server with a domain-separated key derived from server secret material. The session contains only the minimum claims needed for Writers Submission, including Telegram user id, issuance time and expiry.

Requirements:

- session lifetime is bounded by `WRITERS_SUBMISSION_SESSION_TTL_SECONDS`;
- signature is verified on every authenticated request;
- expiry is enforced server-side;
- cookie is not readable from JavaScript;
- raw `initData` is discarded after bootstrap;
- changing/restarting the process must not weaken authentication; a stable server signing source is used so restart does not magically authenticate arbitrary users.

The session proves Telegram identity only. Ownership and workflow authorization are still checked in service/storage on every operation.

### 6.3 Writers-community eligibility

Viewing and editing already-owned drafts/submissions requires valid Telegram identity and ownership. This preserves access to an author's own history even if they later leave the writers chat.

Creating a new logical submission or submitting a revision additionally requires current writers-community eligibility. V1 performs a fail-closed `getChatMember(WRITERS_CHAT_ID, user_id)` check and accepts only normal member/administrator/creator states. Submit always performs a fresh eligibility check. A short in-memory cache may be used for the create boundary but is not an authorization substitute at submit.

Zero Trust remains responsible for entry verification into the writers chat. Writers Submission does not create another captcha or mutate Zero Trust state.

### 6.4 Moderator authorization

Moderator callbacks are accepted only when the Telegram actor id is present in `WRITERS_SUBMISSION_MODERATOR_IDS`. Seeing or forwarding a moderation message does not grant review authority.

Every moderator action re-checks authorization server-side before touching storage.

### 6.5 Same-origin web security

The web API is same-origin with the Mini App. V1 does not enable permissive CORS.

Responses set at least:

- `Content-Security-Policy` allowing local assets and the official Telegram Web App script origin only as required;
- `X-Content-Type-Options: nosniff`;
- `Referrer-Policy: no-referrer`;
- a restrictive `Permissions-Policy` for unused browser capabilities.

No bot token, DSN, moderator id list or other server secret is emitted to frontend assets.

## 7. Data model

Public/API identities use UUIDs or equivalent unguessable identifiers. Internal surrogate keys may exist but are never used as the frontend authorization boundary.

### 7.1 `writers_submissions`

One logical work thread per author.

Core fields:

- `id` UUID primary key;
- `writers_chat_id` bigint;
- `author_user_id` bigint;
- `status` constrained to the state machine;
- `current_draft_revision_id` nullable;
- `current_submitted_revision_id` nullable;
- `claimed_by_user_id` nullable;
- `claimed_at` nullable;
- `created_at`, `updated_at`;
- `version` integer for optimistic/concurrent transition protection.

Indexes include `(author_user_id, updated_at)` and moderation queue status indexes.

Ownership never changes.

### 7.2 `writers_submission_revisions`

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

A `DRAFT` revision may be edited by its owner. `submit` atomically seals it. A `SEALED` revision is immutable at the service/storage boundary.

When moderation requests changes, the author creates revision `N+1`, normally prefilled from the prior sealed revision. Revision `N` remains unchanged.

### 7.3 `writers_submission_files`

Core fields:

- `id` UUID primary key;
- `submission_id` FK;
- `revision_id` FK;
- safe display filename;
- declared MIME;
- detected file class;
- byte size;
- SHA-256 checksum;
- `telegram_file_id` required for a ready attachment;
- `telegram_file_unique_id` when returned by Telegram;
- file-storage chat/message metadata needed for operator cleanup where applicable;
- created timestamp.

A file belongs to one revision. Files attached to a sealed revision cannot be replaced or deleted.

Binary file contents are not stored permanently in PostgreSQL.

### 7.4 `writers_submission_reviews`

Records moderation actions without overwriting history.

Fields include:

- `id` UUID;
- `submission_id`;
- `revision_id`;
- reviewer Telegram id;
- action (`CLAIM`, `APPROVE`, `REQUEST_CHANGES`, `REJECT`);
- optional moderation comment;
- timestamp.

### 7.5 `writers_submission_events`

Append-only audit timeline.

Events include draft creation/update, file attach/delete, submit, delivery, claim, decision, withdrawal and revision creation. Metadata is bounded and sanitized. It never contains raw `initData`, credentials or uploaded bytes.

### 7.6 `writers_submission_outbox`

A transactional outbox makes Telegram moderation delivery and critical author notifications restart-safe.

Fields include:

- opaque event id;
- submission/revision id;
- event type;
- state (`PENDING`, `IN_FLIGHT`, `DELIVERED`, `RETRYABLE_FAILED`, `PERMANENT_FAILED`);
- attempt count;
- next-attempt timestamp;
- bounded safe error class/code;
- created/updated timestamps.

Creating a submitted revision and its moderation-delivery outbox row occur in one PostgreSQL transaction.

### 7.7 `writers_submission_idempotency`

State-changing HTTP operations that may be retried use a bounded persistent idempotency record.

Fields include:

- verified actor user id;
- operation name;
- client idempotency key;
- resulting entity/reference or stable response summary;
- created/expiry timestamps.

A unique constraint on actor + operation + key prevents duplicate create/submit/withdraw/revision operations after browser/network retries or process restart.

## 8. Submission state machine

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

A moderator claim is atomic. Two moderators clicking `claim` concurrently cannot both become the reviewer. Storage uses a conditional update/row lock and returns exactly one winner.

Decision writes are conditional on expected current state and reviewer ownership. Duplicate callbacks are idempotent: they return the already-applied result and do not produce duplicate transitions or notifications.

`WITHDRAWN` is allowed only before a terminal decision. A decision and withdrawal racing each other are serialized transactionally; one transition wins and the other sees the resulting state instead of overwriting it.

## 9. Draft and revision semantics

Draft autosave updates only the current author's current `DRAFT` revision and requires the expected submission/revision version.

Submission performs one transaction that:

1. locks the submission/current draft;
2. validates ownership and current state;
3. validates required fields and ready attachment metadata;
4. seals the current revision;
5. changes top-level status to `SUBMITTED`;
6. sets `current_submitted_revision_id`;
7. clears `current_draft_revision_id`;
8. appends audit events;
9. inserts moderation-delivery outbox work;
10. records idempotency result where supplied.

No API exists to modify a sealed revision.

`CHANGES_REQUESTED -> DRAFT` creates revision `N+1`, sets it as `current_draft_revision_id`, and normally prefills it from revision `N`. It never mutates `N`.

## 10. Form and validation

V1 fields:

- title — required;
- work type — required;
- genre — required, represented as free text or a small UI preset plus custom value; storage remains text rather than a hard product taxonomy;
- short description — required;
- main text — optional only when at least one ready supported file contains the work;
- external link — optional HTTPS URL;
- files — optional, up to the configured maximum (default three).

At least one actual work payload is required: non-empty main text or at least one ready file.

Server-side validation is authoritative. Frontend validation exists only for UX.

All user text has explicit bounded lengths defined as implementation constants/config defaults and covered by tests. The API rejects oversized request bodies before persistence.

HTML is never trusted. The Mini App renders user content as text, not raw HTML. Telegram messages escape dynamic content before HTML/Markdown formatting.

## 11. File handling and restart safety

Default v1 file rules:

- maximum 20 MiB per file;
- maximum three files per revision;
- supported extensions: `.pdf`, `.docx`, `.txt`;
- supported MIME classes are mapped explicitly;
- extension, declared MIME and detected file class must be compatible;
- PDF requires a PDF signature;
- TXT must pass bounded text/binary sanity checks;
- DOCX requires ZIP/container signature plus bounded central-directory name checks for DOCX structure; no document extraction, rendering or macro execution occurs.

V1 does not execute documents, render PDFs, run OCR or automatically fetch links.

### 11.1 Attachment upload flow

To avoid losing a binary between submit and asynchronous moderation delivery, an attachment becomes `ready` before it is eligible for a submission.

Upload flow:

1. aiohttp streams the multipart body with a hard byte limit into a server-generated temporary path;
2. extension/MIME/signature and bounded DOCX container metadata are validated;
3. checksum is calculated while streaming/validating;
4. the bot uploads the validated file to `WRITERS_SUBMISSION_FILE_CHAT_ID` with notifications disabled where possible;
5. Telegram returns reusable `file_id` / `file_unique_id`;
6. PostgreSQL persists attachment metadata and Telegram identifiers;
7. the temporary local file is removed immediately after successful persistence;
8. optional staging Telegram message cleanup may run only after identifiers are persisted.

If Telegram staging upload or DB persistence fails, the attachment operation fails and the temporary file is cleaned up. The draft itself remains intact.

The moderation outbox therefore depends only on durable PostgreSQL metadata plus Telegram `file_id`, never on a restart-fragile local temporary file.

Filenames are normalized for display; local paths are generated by the server and never taken from the supplied filename.

External links must be HTTPS and are stored/displayed only. The backend never requests them, preventing SSRF through the URL field.

## 12. Telegram moderation delivery

Submitting a revision creates a private moderation-card outbox job.

The delivery worker sends to `WRITERS_SUBMISSION_MOD_CHAT_ID`:

- author display identity and numeric id;
- opaque submission/revision reference;
- title, type, genre and description;
- bounded text preview;
- external link when present, clearly treated as user-supplied;
- attached documents by existing Telegram `file_id`;
- inline moderator controls.

Controls:

- `Взять на проверку`;
- `Одобрить`;
- `Нужны правки`;
- `Отклонить`.

Callback payloads use a compact opaque moderation token that maps server-side to the target submission/revision and remains within Telegram callback-size limits. Reviewer identity comes only from the callback actor, never from payload data.

For actions requiring free-form feedback (`REQUEST_CHANGES`, and optionally `REJECT`), v1 uses a small bounded-expiry moderator conversation state keyed by moderator + moderation token. No durable decision occurs until the comment/no-comment choice is finalized, so losing this transient input state on restart cannot create a false moderation decision.

The moderation card is updated after claim/decision when possible, but PostgreSQL remains authoritative if Telegram message editing fails.

## 13. Delivery failure semantics

A submission is durably accepted when PostgreSQL commits the sealed revision and moderation outbox event.

Because every attachment already has a Telegram `file_id`, moderation delivery is restart-safe without persistent local binaries.

Therefore:

- a transient Telegram moderation-chat failure does not lose the submission;
- the outbox retries with bounded exponential backoff and jitter;
- restart resumes pending/retryable jobs;
- successful delivery records moderation message ids and marks the outbox delivered;
- permanent failure is visible in safe operator diagnostics rather than pretending moderators received the work.

Author UX distinguishes `submission accepted` from `moderation delivery pending` only when delivery is materially delayed.

An attachment that failed validation/staging never becomes ready and therefore cannot be part of a sealed revision.

## 14. Author notifications

Significant transitions use Telegram private notifications:

- submission accepted;
- approved;
- changes requested with moderator comment;
- rejected with permitted comment;
- withdrawal confirmation;
- serious delivery problem only when author action is required.

A `claimed` notification is omitted in v1 to reduce noise; claim remains visible in the Mini App timeline/status if desired.

Critical post-decision notifications may use the same durable outbox mechanism. Notification failure never rewrites a durable moderation decision.

## 15. Mini App UX

### 15.1 Home — `Мои работы`

Shows the current author's submissions sorted by most recently updated, grouped/filtered by:

- drafts;
- under review;
- needs changes;
- completed/closed.

Each card shows title, revision, human-readable status and last update time.

### 15.2 Editor

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

Autosave is debounced and carries an expected version so stale browser tabs cannot silently overwrite newer edits. A conflict prompts reload/merge rather than last-write-wins data loss.

### 15.3 Submission timeline

The detail screen renders a bounded audit-derived timeline, for example:

```text
Черновик создан
Работа отправлена
Взята на проверку
Нужны правки: <comment>
Редакция #2 создана
Работа отправлена
Одобрено
```

Internal delivery retries and sensitive operator metadata are not exposed unless they require author action.

## 16. HTTP/API surface

Static Mini App:

```text
GET /writers/
```

Session bootstrap:

```text
POST /api/writers/session
```

The bootstrap endpoint accepts fresh Telegram `initData` and returns/sets the authenticated application session. All remaining author API routes require the valid session cookie.

Author routes:

```text
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

Error responses use stable machine-readable codes plus safe human messages. Storage errors and stack traces are never sent to the browser.

State-changing endpoints support persistent idempotency where browser/network retries are plausible, especially create/submit/withdraw/revision creation.

## 17. Rate limiting

V1 uses an in-process bounded rate limiter for cheap abuse control plus hard server-side payload/upload limits. Since rate-limit state is not an authorization invariant, restart reset is acceptable for this single-process deployment.

Separate buckets exist for:

- list/read;
- autosave/update;
- create/revision creation;
- upload;
- submit/withdraw;
- session bootstrap.

Persistent PostgreSQL constraints/idempotency remain authoritative, so rate-limit reset cannot create duplicate durable transitions.

## 18. Lifecycle integration

`main.py` follows the existing explicit lifecycle style.

When enabled:

1. validate Writers Submission config;
2. reuse the validated PostgreSQL `DATABASE_URL`;
3. initialize `PostgresWritersSubmissionStorage` and idempotent schema;
4. create service/security/session components;
5. register private entry and moderator handlers;
6. start aiohttp runner/site;
7. start bounded outbox worker;
8. start normal bot polling;
9. on shutdown stop worker and web runner, then close owned resources in deterministic order.

No unbounded orphan tasks may survive shutdown.

If the web listener cannot bind while enabled, startup fails rather than exposing a dead Mini App button.

Writers Submission storage may use its own small asyncpg pool or a deliberately shared pool only if ownership/lifecycle is explicit. It must never close a pool owned by Zero Trust or Entertainment.

## 19. Database/bootstrap policy

Schema creation/upgrades are idempotent and versioned in the same practical style as the existing project. The implementation must not destructively rewrite unrelated Entertainment or Zero Trust tables.

All multi-row state transitions use transactions.

Important invariants include:

- submission ownership cannot change;
- `(submission_id, revision_number)` is unique;
- at most one current draft pointer exists;
- submit clears the current draft pointer;
- sealed revision content cannot be updated through storage/service APIs;
- attachment rows usable by a sealed revision have a Telegram `file_id`;
- file count cannot exceed service/config rules;
- moderation claim/decision is conditional on expected state;
- outbox moderation event per submitted revision is unique/idempotent;
- idempotency actor + operation + key is unique while retained;
- audit events are append-only through the public storage interface.

## 20. Observability and privacy

Startup diagnostics may log:

- feature enabled/disabled;
- bind host/port;
- writers chat id;
- moderation chat id;
- file-storage chat id;
- moderator count;
- upload limits;
- outbox worker readiness;
- schema version.

Runtime logs may contain opaque submission ids, actor numeric ids when operationally necessary, state names and safe error classes.

Runtime logs must not contain:

- bot token;
- `DATABASE_URL`;
- raw Telegram `initData`;
- session cookie/token value;
- full work text;
- full moderation comments by default;
- uploaded bytes.

External links are never fetched by the backend.

## 21. Testing strategy

Development follows RED -> GREEN for each behavior slice.

### 21.1 Pure/unit tests

- config parsing/fail-closed validation;
- Telegram `initData` valid signature, wrong signature and expired auth;
- application-session signature/expiry/tampering;
- user id derived only from verified auth/session;
- text/link validation;
- upload extension/MIME/signature compatibility;
- bounded DOCX container metadata validation without extraction;
- state-machine allowed/forbidden transitions;
- sealed revision immutability;
- safe rendering/escaping;
- callback-token parsing without trusting actor identity;
- rate-limit bucket behavior.

### 21.2 Service tests

- author can access only their own submissions;
- cross-user GET/PATCH/file/history access denied;
- current writers eligibility required for create/submit;
- existing owner can view/edit own draft after leaving, but cannot submit until eligible again;
- autosave conflict detection;
- idempotent create/submit/withdraw/revision creation;
- submit seals revision atomically and clears draft pointer;
- changes requested creates a new editable revision without mutating old content;
- final states reject later mutation;
- moderator allowlist enforced;
- duplicate moderator callbacks do not duplicate decisions/notifications.

### 21.3 PostgreSQL integration tests

Run in the existing PostgreSQL CI job or a clearly scoped extension.

Must cover:

- restart persistence;
- author isolation;
- writers-chat scope persistence;
- concurrent claim: exactly one winner;
- decision vs withdrawal race: exactly one legal transition wins;
- revision uniqueness/immutability;
- transactional submit + outbox creation;
- retryable outbox persistence across reconstructed service/worker;
- persistent idempotency across reconstructed service;
- attachment metadata and Telegram file id persistence;
- exact UUID ownership checks.

### 21.4 HTTP tests

Using aiohttp test utilities or equivalent:

- static Mini App route;
- session bootstrap with valid/invalid/expired `initData`;
- authenticated cookie accepted;
- tampered/expired cookie denied;
- unauthenticated API denied;
- request body too large rejected before persistence;
- malformed JSON safe error;
- cross-user opaque id still denied;
- upload count/size/type limits;
- failed Telegram staging upload does not create ready attachment;
- temporary upload cleanup on success/failure;
- security headers present;
- no permissive CORS;
- stable API error codes.

### 21.5 Telegram handler/delivery tests

- writers group deep link targets private bot start flow;
- private start flow emits configured WebApp URL;
- non-moderator callback denied;
- claim callback uses atomic service result;
- decision callback is idempotent;
- moderation card escapes user-controlled text;
- moderation documents reuse stored Telegram `file_id`;
- Telegram card/message-edit failure does not undo DB state;
- author notification failure does not undo DB state.

## 22. Acceptance criteria

Writers Submission v1 is ready to merge only when all of the following are true:

1. Existing bot behavior remains green with the feature disabled.
2. A valid writers user can enter through private bot flow, create/autosave a draft and restart without losing it.
3. Another Telegram user cannot read, modify, withdraw or attach files to that draft even when given its UUID.
4. A stale/tampered Telegram bootstrap or application session is rejected.
5. Every ready attachment is validated, converted to Telegram `file_id`, persisted and detached from local temporary storage before it can be sealed into a submission.
6. Submission creates one immutable sealed revision and one durable moderation-delivery job atomically.
7. Duplicate submit cannot create a second sealed revision or second moderation job.
8. A submitted revision cannot be modified.
9. Two moderators claiming concurrently produce exactly one owner.
10. Unauthorized moderators cannot claim or decide.
11. Changes-requested creates revision N+1 and preserves N unchanged at the service/storage contract level.
12. Telegram moderation delivery failure is restart-safe and does not lose accepted work or attached documents.
13. PostgreSQL/HTTP/handler tests pass in CI alongside the full existing unit suite.
14. No credentials, real DSN, raw `initData`, session secrets or uploaded content are committed to Git.
15. Documentation explains env, private entry flow, upload staging, moderation workflow and rollback/disable path.

## 23. Explicit non-goals for v1

The following are intentionally deferred:

- separate moderator web dashboard;
- rich collaborative editor;
- line-level comments/annotations;
- S3/object-storage dependency;
- OCR/PDF rendering;
- semantic DOCX parsing/editing;
- automatic external-link downloading;
- antivirus/malware scanning service integration;
- plagiarism detection;
- AI review/scoring;
- public author profiles;
- ratings/leaderboards;
- billing;
- external identity provider;
- React/Vite/Node build pipeline.

These can be added later without weakening v1 ownership/revision invariants.

## 24. Rollout and rollback

Rollout is controlled by `WRITERS_SUBMISSION_ENABLED`.

Initial deployment sequence:

1. deploy schema/code with feature disabled and run the full test/CI suite;
2. configure public HTTPS URL, moderation chat, file-storage chat and moderator allowlist;
3. enable the feature;
4. verify web startup/health diagnostics;
5. run one operator attachment upload to confirm `file_id` staging and temp cleanup;
6. submit one operator test revision and verify moderation delivery/decision;
7. expose the normal Writers entry link/button.

Emergency rollback sets `WRITERS_SUBMISSION_ENABLED=0` and restarts the process. Existing submissions remain in PostgreSQL untouched. Disabling Writers Submission must not disable writers moderation, Zero Trust, Entertainment, Lexicon or other bot features.

## 25. Implementation boundary

The implementation adds the subsystem without rewriting `writers_moderation.py`, `zero_trust/*` or unrelated Entertainment logic.

Expected integration changes:

- `main.py` lifecycle wiring;
- `requirements.txt` (`aiohttp` explicit dependency);
- `.env.example`;
- `.github/workflows/ci.yml` for PostgreSQL/HTTP coverage if needed;
- README/operator documentation;
- new `writers_submission/*` package;
- new focused tests.

Any need to alter Zero Trust state semantics, writers profanity moderation semantics, Entertainment generation/memory semantics or unrelated game behavior is out of scope and requires a separate design decision.
