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
WRITERS_SUBMISSION_MODERATION_MODE=owner
WRITERS_SUBMISSION_OWNER_USER_ID=2039781854
WRITERS_SUBMISSION_FILE_CHAT_ID=-1000000000002

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

**Current safe default: only the owner's personal chat handles moderation.**
The ICФ owner (Telegram user ID `2039781854`) opens a private dialog with
`@Fosgen_AntiSpam_bot` and sends `/start` before the first application.

Submitted application cards, previewable attachments, moderator action buttons,
and reply-to-comment prompts all arrive in that personal dialog. **No
submission card is sent to the writers/reader group, the channel or the future
admin group.** The separate private **file-storage chat** is still needed to
persist uploaded documents by Telegram `file_id`; it is not a moderation inbox.

Add the bot to the private file-storage chat. The bot must be able to:

- send messages;
- send documents;
- edit its own moderation cards;
- use inline callback buttons;
- preferably delete its own staging messages when a database write fails after Telegram accepted a file.

Set `WRITERS_SUBMISSION_FILE_CHAT_ID` to the private storage chat ID.

With `WRITERS_SUBMISSION_MODERATION_MODE=owner` (or omitted), the server
forces the moderation recipient and sole allowed reviewer to
`WRITERS_SUBMISSION_OWNER_USER_ID=2039781854`, even if outdated
`WRITERS_SUBMISSION_MOD_CHAT_ID` / `WRITERS_SUBMISSION_MODERATOR_IDS`
remain in Bothost ENV. This prevents accidental rerouting to a group.

**Later, after the closed admin group exists**, explicitly set:

```env
WRITERS_SUBMISSION_MODERATION_MODE=group
WRITERS_SUBMISSION_MOD_CHAT_ID=-100xxxxxxxxxx
WRITERS_SUBMISSION_MODERATOR_IDS=123456789,987654321
```

Use actual members' **numeric Telegram user IDs** for the allowlist.
Only negative Telegram group IDs are accepted in group mode; the bot must
be added to the private group with message/document permissions.
The approved promotional preview still goes personally to the owner.
Any mode change requires a restart/redeploy.

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
2. Have owner `2039781854` open the bot privately and press Start; create the separate private file-storage chat and add the bot.
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
7. In the owner's personal dialog, claim the work. For future group mode, concurrent claims still have one winner.
8. Test **Нужны правки** by replying directly to the bot's comment prompt (Telegram reply, not a new standalone message). Verify unrelated moderator chat text is ignored, a second prompt invalidates the earlier reply target, and the author can create revision N+1 while revision N stays unchanged.
9. Submit the new revision and approve or reject it. Confirm the author notification and timeline update.
10. Restart the worker/process with a pending delivery and verify PostgreSQL outbox retry resumes without duplicate durable state.


### Moderation comment safety

After the owner presses **Нужны правки**, the bot posts a prompt in the owner's DM (or the configured closed group in group mode). The moderator must **reply to that exact bot message** within 10 minutes. Normal chat messages, replies to the moderation card, and replies to older prompts are intentionally not treated as review comments. If the bot cannot post the prompt, the request is not armed; press **Нужны правки** again after Telegram recovers.

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

## 11. Ficbook application form v2 (owner-requested)

The Telegram Mini App editor now collects a structured, revisioned application:

1. Work title.
2. **ФФ / Оридж** as exclusive but reversible checkboxes; fanfiction shows a
   **Фандом** field and requires it at submission.
3. **Мини / Миди / Макси** as exclusive, reversible checkboxes.
4. **Направление** (editable suggestions such as Джен/Гет/Слэш).
5. **Рейтинг** (editable suggestions including G/PG-13/R/NC-17/NC-21).
6. **Завершен / В процессе**; completed work requires word count, pages and
   parts. Switching to in-progress clears these submitted counts.
7. A required **HTTPS Ficbook URL**, with up to five optional HTTPS links
   (Telegram, portfolio and so on).
8. Description.
9. One of **four RGB colors** (both color picker and individual R/G/B channels
   with live preview) or **image** (PNG/JPEG staged as a Telegram document to
   the configured private storage chat). Upload size/count follow the normal
   Writers file policy.

A Ficbook HTTPS link now suffices as publication content: uploading manuscript
text or a PDF/DOCX/TXT file is **optional**. The old manuscript field remains
under an expandable "Дополнительно" section.

All form metadata is validated server-side, stored in `details_json` on
`writers_submission_revisions`, and included in revision detail API responses.
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` migrates old installations without
removing earlier submissions or revisions. Existing v1 submissions without a
structured form remain readable and follow the legacy submission validation.

The moderation card includes structured dimensions, status, optional additional
links and the four selected RGB values. Image attachments are forwarded via the
existing file delivery worker. The app uses safe text rendering for user values.

**Deployment:** deploy the updated Python archive/real Dockerfile; restart the
existing bot process with the current PostgreSQL. The migration runs during
startup. Back up production PostgreSQL before a schema-changing rollout.

**Checks:** try switching FF to original and back; toggle already-selected
checkboxes off; edit each RGB channel and the color picker; save a partially
completed draft, close and reopen; send a link-only completed work, and check
all fields on the moderator's Telegram card. For image mode, the file must
finish uploading before submission.

## 12. Approved post: owner's personal inbox, not the public group/channel

On a successful `APPROVE` review, the same PostgreSQL transaction enqueues a
unique `OWNER_PREVIEW` outbox job (once per submitted revision). The delivery
worker renders a prepared ICФ-style promotional post with title, description,
hashtags for original/fandom, size, direction, rating and progress, characters,
notes, Ficbook URL and optional Telegram/other HTTPS links.

The recipient is exclusively the Telegram **private chat** belonging to the
ICФ owner **2039781854** (overridable by `WRITERS_SUBMISSION_OWNER_USER_ID`).
**Do not publish in `WRITERS_CHAT_ID`, a channel, or the moderation group.**
The original author still receives their private decision notification.
Reject/request-changes never generate an owner's promotional post.

When the author selected RGB colors, a four-color PNG is generated
deterministically and attached as a Telegram photo. When they uploaded a
PNG/JPEG illustration, the photo is downloaded from private Telegram storage
and sent to the owner as an image (or as a document if it exceeds Telegram's
photo limit). If the post fits the Telegram photo-caption limit, it is delivered
as one photo with the full caption. For longer posts, the owner receives the
visual first and then complete copyable HTML-formatted post text.

**Critical operator requirement:** the ICФ owner must open
`@Fosgen_AntiSpam_bot` in Telegram and press **Start** / send `/start`
**before** the bot can DM them (Telegram bots cannot initiate private chats).
Give the bot access to the private file-storage chat (and later to the
optional closed admin group, only when explicitly enabling group mode).

The PostgreSQL outbox is restart-safe and the owner-preview job has a unique
dedupe key. Telegram Bot API does not provide an atomic send+DB commit; a
process crash between sending and marking delivered can still cause a retry
and a duplicated Telegram message. Verify owner delivery logs and outbox
state if recovering from such a crash.

**Smoketest:** create a v2 application with notes, characters and a palette;
submit, claim and approve; verify the owner receives the preview in DM and
the main writers chat/channel receives NOTHING. Repeat with a PNG/JPEG
illustration and a long description, then reject a separate application and
verify no owner preview appears. Keep PostgreSQL backed up before deploying
the schema constraint migration.

## 13. Owner-only intake: safe-by-default routing (October 2026)

Until a real admin team and closed review group are ready, **ALL incoming
Writers Submission applications are delivered to owner ID `2039781854` in
their personal Telegram dialog**. The message looks like the existing
"✒️ Новая работа на модерацию" card and includes author ID, title, type,
direction, rating, size, status, color palette, description, Ficbook URL,
attachments and the normal moderation buttons.

Selecting "Одобрить" subsequently creates the separately queued, formatted
ICФ publication preview in the **same** owner's private dialog. Nothing
is automatically published into the public writers chat, channel, or the
not-yet-created admin group. Authors still receive private status updates.

`WRITERS_SUBMISSION_MODERATION_MODE=owner` is the default and does **not**
require `WRITERS_SUBMISSION_MOD_CHAT_ID` or a separate
`WRITERS_SUBMISSION_MODERATOR_IDS` value. The private
`WRITERS_SUBMISSION_FILE_CHAT_ID` remains required for persistent uploads.

Smoke test: send one new work from a writer, check its card and documents
arrive exclusively in owner's DM, click claim/approve, check the same DM
receives the formatted publication preview, then exercise "Нужны правки"
by replying to the exact prompt. Confirm no cards appear in the public
writers community or the previously configured review group.

## 14. Fixing photo attachments in production (October 2026)

**Root cause:** Writers form v2 already allowed users to select PNG/JPEG,
but the PostgreSQL `writers_submission_files` table still had the original
v1 `writers_submission_file_class_check` permitting only `pdf`, `docx`,
`txt`. The upload was accepted by browser/server validation and staged in
Telegram, then rejected by PostgreSQL. Staged copies were cleaned up but
authors saw a failed upload. Re-running `CREATE TABLE IF NOT EXISTS` did not
migrate the old check.

**Fix:** On startup, in a transaction, the storage layer updates the file
class constraint to include `png` and `jpeg`. Existing submissions,
revisions, text documents and file IDs remain intact. Repeated startup
does not recreate the constraint. An integration test simulates the legacy
constraint, reopens the storage and verifies migration, photo persistence
and original file retention.

**Mobile handling:** The Telegram Mini App's photo picker and general
attachment picker both accept JPG, PNG, WebP and HEIC/HEIF. Browsers that can
decode the latter formats convert them to a real JPEG via canvas before
upload; JPEG with missing picker MIME is normalized without transcoding.
If the current WebView cannot decode HEIC, the author sees a specific
message advising use of JPG / iPhone Camera > Formats > Most Compatible.
The server continues verifying extension, declared MIME and binary signature:
it never accepts arbitrary bytes as a photograph.

**UX:** Upload waits for any in-flight draft autosave to complete and flushes
the current draft before adding files (because adding a file increments
the submission version). Status and failures are shown in the editor;
the photo preview only appears *after* a successful upload. Telegram
WebView assets use updated URLs and `Cache-Control: no-store` so an older
cached script is not reused after deployment.

**Operator:** Back up PostgreSQL before deployment. No new Bothost ENV
variables are required. Upload current `main`, perform a full Bothost
Python/Dockerfile rebuild, restart. Check that `WRITERS_SUBMISSION_READY`
appears. Test photo selection via (a) `Картинка` and (b) `Файлы → Добавить`,
on both Android and iOS, verify the file appears in attachments after a page
reload, then submit and verify it reaches the owner DM / review card.
Images are subject to the existing limit of 3 files of up to 20 MB each.

## 15. ICФ branded covers and paid custom image option

The form has **exactly two cover modes**: (1) an ordinary **free**
four-color RGB palette, (2) an author's **paid** custom photo. There is no
cover-style/template selector. The free mode displays one approximate
1200 × 450 color-combination mockup with title on the lower right; this
is **not final cover artwork**. A human IKF artist can later alter the
composition, font and finishing independently. The colors remain stored
with the draft and submitted revision.

Older revisions may still contain `details_json.cover_template`, created
before this simplification. They remain readable. The backend accepts but
discards that legacy setting when editing the draft, and owner delivery uses
a single fixed rough reference regardless of the historical style.

When an application is approved, the owner `2039781854` receives the
rough four-color palette reference and prepared promo text in their private chat. Neither
the Writers community nor the channel gets an automatic post.

**An author's own artwork as a cover is a paid option.** Authors can still
attach photos/documents to an application for administrative reference;
uploading an attachment is not itself a purchase. The paid image selection
is labelled in the form and requires a JPEG/PNG attachment (WebP and HEIC are
converted to JPEG on supported browsers).

There is **no fabricated price, checkout or automatic charge**. Payment
terms are agreed with the ICФ administration outside the Mini App. After
external payment is actually verified:

1. Owner claims the submitted application with the regular moderation button.
2. Owner explicitly taps **💎 Подтвердить оплату своей обложки**.
3. The server checks the Telegram actor against
   `WRITERS_SUBMISSION_OWNER_USER_ID` (not just the moderator allowlist).
   Confirmation is persisted, per submission+revision, in a separate
   `writers_submission_cover_payments` table with actor/time audit.
4. Only then may the claimed reviewer approve that revision. PostgreSQL
   enforces this at the approval transaction; an author cannot forge payment
   by manipulating public form fields.
5. Owner receives the custom art center-cropped to ICФ format with a dark
   title band and the title **bottom right**.

For old approved jobs awaiting delivery, the owner preview worker checks
the payment record again before sending the custom artwork. If absent,
it sends only the post text with an explicit unpaid-cover note, not the photo.

The separate payment table is created idempotently. The repository now
installs Pillow and DejaVu Cyrillic fonts in its Python Dockerfile. **No new
ENV settings** are required. After backing up PostgreSQL, rebuild from
latest `main` with the custom Dockerfile. Test one free palette application
and one paid photo application, including rejection of premature approval,
owner payment confirmation and a successful approval. The form does not
perform transactions or guarantee an agreed fee.

**Note:** In the present stage a payment confirmation means the owner has
personally attested payment received by their own method. Do not tap the
confirmation button merely because a user claims they paid.

## 16. Approved but the owner did not receive a post — delivery diagnostics

**Approval and delivery are two different durable steps.** The author can
receive `✅ Работа одобрена` even while the independent `OWNER_PREVIEW`
job cannot be delivered to Telegram owner DM `2039781854`. A moderator
card keyboard warning `message is not modified` is harmless; it is NOT
the cause of an owner DM failing.

By design, **all finished promo text and any illustration go only to
the owner's private chat**. Telegram requires the *owner account itself*
to open `@Fosgen_AntiSpam_bot` and send `/start`. The author starting the
bot does not grant permission to message a different Telegram account.

### How to recover a missing post

1. Confirm the deployed build is current and `WRITERS_MODERATION_ROUTE_READY`
   indicates `destination_chat_id=2039781854`. Check the loaded Mini App
   asset version to avoid confusing a cached/older container with latest main.
2. From the **owner's own personal Telegram account** (ID `2039781854`),
   send `/start` to the bot and ensure it is not blocked.
3. Send `/writers_delivery`. It shows the last owner posts with
   `DELIVERED`, `PENDING`, `RETRYABLE_FAILED`, or `PERMANENT_FAILED`
   and the technical error code.
4. For a failed or missing approved post, send
   `/writers_retry <submission UUID>`, for example
   `/writers_retry 41e49749-1b2f-420d-9e47-a9b32d2f04da`.
   Without a UUID, the command chooses the latest failed/missing approved
   post. Recovery does **not** resend an already delivered item.
5. Send `/writers_delivery` again and verify `DELIVERED`. If delivery
   fails, search Bothost logs for `WRITERS_DELIVERY_FAILED
   event=OWNER_PREVIEW`. Errors contain submission/revision ID,
   retry/permanent classification and exception information.
6. After successful delivery, verify the generated post and image before
   publishing. A future *server-side outage* can cause duplicated Telegram
   messages if it occurs after sending and before recording delivery; check
   the owner's chat before manually retrying.

If the palette image cannot be generated, or Telegram rejects a photo, the
worker **falls back to sending the complete approved text** with an explanation,
instead of losing the whole post. An actual forbidden private chat remains a
failed job until the owner unblocks/starts the bot.

These commands are restricted to the configured owner ID and private chat.
They do **not** publish to the public IKF channel or writers group. No new
Bothost ENV values are required; changes take effect only after rebuilding
and restarting the new GitHub `main`. Existing PostgreSQL data/outbox jobs
are preserved.

## 17. Visible attachment upload progress in the Telegram Mini App

The general **Файлы → Добавить** picker and the paid **Своя фотография**
picker share a real, accessible native HTML `<progress>` indicator.
Unlike ordinary `fetch`, `XMLHttpRequest.upload` reports uploaded bytes
from the actual HTTP request, allowing a percentage during transfer.
Both request types preserve the existing same-origin authentication cookie,
strict server MIME/signature checks and PostgreSQL attachment version logic.

Stages: **Preparing the file** (including optional on-device HEIC/WebP JPEG
conversion, indeterminate) → **Saving draft** (indeterminate) →
**Transferring N of M files** (real percentage where the WebView exposes
`lengthComputable`) → **100% transmitted, server saving** (not success yet)
→ **verifying the persisted submission** → **file added** only after HTTP
201 and a successful GET of the updated submission.

The progress section stays visible with a success/failure label. Multiple
files upload sequentially with an individual N-of-M indication. If one
request fails, the UI reports how many were already persisted, to discourage
blind repeats. If the upload POST succeeds but the post-upload refresh
fails, it warns the author that the file is already saved and the form
must be reopened; it does not falsely report that the upload failed.

Upload failures do not silently disappear: server validation, HTTP
conflict (409), network failure, timeout and unexpected responses produce
explicit errors. Controls remain locked throughout preparation/upload to
avoid overlapping requests, while pending draft edits resume afterward.
The progress display does not bypass author eligibility, file limits,
payment confirmation or moderation rules.

No new environment variables or database migrations. After uploading
the new source, the existing Bothost custom Dockerfile must be rebuilt.
The Mini App asset query string changes to `upload-progress-v1` to avoid
stale Telegram WebView caching.
