# Writers Submission v1 — operator runbook

Writers Submission v1 is the Telegram Mini App used by the writers community to create restart-safe drafts, attach PDF/DOCX/TXT files, submit immutable revisions and receive moderation decisions.

The subsystem is **disabled by default**. Existing bot behavior is unchanged while `WRITERS_SUBMISSION_ENABLED=0`.

## 1. Runtime topology

Writers Submission runs inside the existing Python asyncio process:

- aiogram continues to own Telegram polling;
- aiohttp serves the Mini App and JSON API;
- PostgreSQL is the only durable source of truth for submissions, revisions, audit history, idempotency and the delivery outbox;
- uploaded files are validated locally and immediately staged into a private Telegram file-storage chat; PostgreSQL stores Telegram `file_id` metadata, not long-lived local binaries;
- a durable outbox worker sends moderation cards and author notifications.

The feature never falls back to SQLite.

## 2. Required environment

Keep real credentials and private chat/user IDs in the deployment environment or an uncommitted local `.env`. Do not put them in Git.

```env
BOT_TOKEN=...
DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/DATABASE
WRITERS_CHAT_ID=-1002619489118

WRITERS_SUBMISSION_ENABLED=1
WRITERS_SUBMISSION_PUBLIC_URL=https://your-public-host.example/writers/
WRITERS_SUBMISSION_MOD_CHAT_ID=-1000000000001
WRITERS_SUBMISSION_FILE_CHAT_ID=-1000000000002
WRITERS_SUBMISSION_MODERATOR_IDS=123456789,987654321

WRITERS_SUBMISSION_BIND_HOST=0.0.0.0
WRITERS_SUBMISSION_PORT=8080
WRITERS_SUBMISSION_INIT_DATA_MAX_AGE_SECONDS=900
WRITERS_SUBMISSION_SESSION_TTL_SECONDS=43200
WRITERS_SUBMISSION_MAX_FILE_BYTES=20971520
WRITERS_SUBMISSION_MAX_FILES=3
WRITERS_SUBMISSION_RATE_LIMIT_WINDOW_SECONDS=60
```

`WRITERS_SUBMISSION_PUBLIC_URL` must be the externally reachable HTTPS URL of the Mini App. Plain HTTP is accepted only for `localhost` / `127.0.0.1` development.

The hosting/reverse-proxy layer must forward that public HTTPS endpoint to `WRITERS_SUBMISSION_BIND_HOST:WRITERS_SUBMISSION_PORT`.

## 3. Telegram setup

Create two private operator chats:

1. **Moderation chat** — receives one moderation card per submitted revision plus its staged attachments.
2. **File-storage chat** — stores validated file messages whose Telegram `file_id` is referenced from PostgreSQL.

Separate chats are recommended so file retention and moderation discussion are independent.

Add the bot to both chats. The bot must be able to:

- send messages;
- send documents;
- edit its own moderation cards;
- use inline callback buttons;
- preferably delete its own staging messages when a database write fails after Telegram accepted a file.

Set `WRITERS_SUBMISSION_MOD_CHAT_ID` and `WRITERS_SUBMISSION_FILE_CHAT_ID` to the exact private chat IDs.

Set `WRITERS_SUBMISSION_MODERATOR_IDS` to Telegram **user IDs**, not usernames. Only those signed Telegram actors can claim/approve/request changes/reject. Callback payloads do not carry a trusted reviewer identity.

## 4. Author entry flow

The supported entry point is the bot's private chat.

A writers-community link may point to:

```text
https://t.me/<BOT_USERNAME>?start=writers_submit
```

The bot answers in private with **✒️ Отправить работу**, a Telegram WebApp button for `WRITERS_SUBMISSION_PUBLIC_URL`.

The server verifies fresh Telegram `initData` and then issues a Secure + HttpOnly + SameSite=Strict application session cookie. Frontend JSON never supplies the trusted author ID.

Creating a new submission and submitting a draft require current membership in `WRITERS_CHAT_ID`. Existing owned drafts/history remain readable/editable after membership loss, but submit performs a fresh membership check.

## 5. File policy

v1 accepts:

- PDF;
- DOCX;
- UTF-8 TXT.

Defaults:

- maximum 20 MiB per file;
- maximum 3 attachments;
- HTTPS external links only.

The backend checks size, filename, declared MIME and detected content class. DOCX is inspected as bounded ZIP metadata and is never extracted. External links are stored as text and are never fetched server-side.

Temporary upload files are deleted after staging attempts. A file becomes a ready attachment only after Telegram returned durable file identifiers and PostgreSQL persisted them.

## 6. Rollout

Recommended production rollout:

1. Deploy the code with `WRITERS_SUBMISSION_ENABLED=0`. Confirm the existing bot starts normally.
2. Create/configure the moderation and file-storage chats and add the bot.
3. Set every Writers Submission variable except the enable flag.
4. Confirm the public HTTPS endpoint routes to the configured bind port.
5. Set `WRITERS_SUBMISSION_ENABLED=1` and restart the process.
6. Confirm startup contains `WRITERS_SUBMISSION_READY` and reaches normal bot polling. No DSN/token should appear in logs.
7. Run the smoke test below with one operator account before posting the entry link publicly.

If PostgreSQL, the HTTP bind, or required configuration is unavailable while the feature is enabled, startup aborts before Telegram polling rather than running a partially available submission system.

## 7. Smoke test

Use one allowlisted operator plus one normal writers-community account.

1. Open `/start writers_submit` in the bot's private chat and open the Mini App.
2. Create a draft with title/type/genre/description/text; close and reopen the Mini App and verify it is still present.
3. Edit the same draft from two browser/Mini App instances. Confirm a stale autosave shows the reload-required state instead of overwriting the newer version.
4. Attach a small TXT/PDF/DOCX file; close/reopen and verify the attachment is restored.
5. Restart the bot process before submission and verify the draft, attachment and timeline survive.
6. Submit once, then repeat the same logical request/callback where practical. Confirm there is only one sealed revision and one moderation job.
7. In the moderation chat, claim the work. If two moderators press claim concurrently, exactly one must win.
8. Test **Нужны правки** with a comment. Verify the author sees the decision and can create revision N+1 while revision N remains unchanged.
9. Submit the new revision and approve or reject it. Confirm the author notification and timeline update.
10. Restart the worker/process with a pending delivery and verify PostgreSQL outbox retry resumes without duplicate durable state.

## 8. Expected security properties

- Telegram `initData` HMAC and freshness are checked server-side.
- Duplicate/malformed initData query keys are rejected.
- Actor IDs come from signed Telegram/session data only.
- Every submission/file/history query is author scoped.
- UUID knowledge does not grant cross-user access.
- Cookie-authenticated state-changing HTTP requests require the exact configured Origin.
- Submitted revisions/files are immutable.
- State transitions and idempotency are enforced in PostgreSQL transactions.
- Moderator identity comes from the Telegram callback actor and an explicit allowlist.
- Dynamic Telegram/DOM content is escaped or rendered with safe DOM text APIs.
- Raw initData, bot token, PostgreSQL DSN, uploaded bytes and full work text are not intentionally logged.

## 9. Rollback

Emergency rollback:

```env
WRITERS_SUBMISSION_ENABLED=0
```

Restart the process after changing the flag.

This disables the Writers HTTP listener, submission handlers and delivery worker. It **does not delete** existing submissions, revisions, attachments, audit events, moderation decisions or outbox rows from PostgreSQL.

Re-enabling the feature reconnects to the same durable state. Do not drop Writers tables as part of a normal rollback.

## 10. Verification commands

Full non-environment suite:

```bash
python -m compileall -q .
python -m unittest discover -s tests -p "test_*.py"
```

PostgreSQL 17 integration suite (with `TEST_DATABASE_URL` configured):

```bash
python -m unittest \
  tests.test_entertainment_storage_postgres \
  tests.test_entertainment_storage_factory \
  tests.test_entertainment_memory_postgres \
  tests.test_entertainment_culture_memory_postgres \
  tests.test_entertainment_culture_windows_postgres \
  tests.test_entertainment_message_purge \
  tests.test_zero_trust_postgres \
  tests.test_writers_submission_postgres -v
```

GitHub Actions runs both of these paths automatically.
