# Zero Trust v2 — per-chat PostgreSQL verification

Date: 2026-10-06

## Goal

Replace the current process-local captcha state with a restart-safe Zero Trust subsystem backed by PostgreSQL.

The critical bug is cross-chat trust leakage: today `pending_users`, `passed_users` and `failed_users` are keyed only by `user_id`, so passing a captcha in one allowed chat can affect another allowed chat. The trader/xer chat `-1003237014529` must be independently protected even when the same user already passed verification elsewhere.

Old successful verifications from before Zero Trust v2 are **not recoverable** because the legacy state lived only in RAM and the bot has already been rebuilt/restarted. We do not invent or backfill fake trust. From the first Zero Trust v2 deployment onward, every verification result is persisted.

## Required behavior

1. Every `IS_NOT_MEMBER -> IS_MEMBER` event in a protected chat creates a fresh verification session for the exact `(chat_id, user_id)`.
2. Passing in one chat never grants trust in another chat.
3. Rejoining the same chat requires a new captcha.
4. One user joining two protected chats simultaneously gets two independent challenges.
5. Active challenges and verification history survive bot restarts.
6. Pending users remain restricted until their current session passes.
7. A wrong answer updates only the matching `(chat_id, user_id)` challenge.
8. The writers chat keeps its custom welcome/success copy and rules link, but uses the same Zero Trust service and storage as legacy chats.
9. The trader/xer chat `-1003237014529` remains independently protected.
10. Bots are not challenged.

## Architecture

Create a focused `zero_trust/` package instead of continuing to grow globals in `legacy_main.py`.

Suggested units:

- `zero_trust/models.py` — challenge/status dataclasses and enums.
- `zero_trust/storage.py` — PostgreSQL persistence contract and implementation.
- `zero_trust/service.py` — create challenge, validate answer, expire/cancel, record history.
- `zero_trust/handlers.py` — generic join/callback routing for protected chats.
- `zero_trust/presentation.py` — generic captcha text and keyboard formatting.

`writers_moderation.py` keeps profanity/rules-specific behavior only. Its join flow delegates challenge creation/validation to Zero Trust v2 and supplies writers-specific presentation hooks.

The old `pending_users`, `passed_users`, `failed_users` globals stop being sources of truth and are removed from verification decisions.

## Protected-chat configuration

The current `ALLOWED_CHATS` setting mixes general legacy feature access with captcha protection. Zero Trust v2 introduces a dedicated setting:

```env
ZERO_TRUST_CHAT_IDS=-1002619489118,-1003237014529,-1003643412493,-1003687304800
```

Default value contains the four chats that are currently protected by the legacy join handler, including `-1003237014529`.

`ALLOWED_CHATS` remains for legacy summaries/statistics/other legacy handlers and no longer determines whether Zero Trust runs.

At startup log:

- effective `ZERO_TRUST_CHAT_IDS`;
- effective `ALLOWED_CHATS`;
- a warning if the trader/xer chat `-1003237014529` is missing from `ZERO_TRUST_CHAT_IDS`.

This separates security scope from unrelated feature scope and prevents an `ALLOWED_CHATS` override from silently disabling entry verification.

## PostgreSQL schema

### `zero_trust_challenges`

Current and historical challenge sessions.

- `id BIGSERIAL PRIMARY KEY`
- `chat_id BIGINT NOT NULL`
- `user_id BIGINT NOT NULL`
- `expected_answer INTEGER NOT NULL`
- `attempts INTEGER NOT NULL DEFAULT 0`
- `status TEXT NOT NULL` — `pending | passed | failed | expired | cancelled`
- `created_at BIGINT NOT NULL`
- `expires_at BIGINT NOT NULL`
- `completed_at BIGINT NULL`
- `telegram_message_id BIGINT NULL`
- `username TEXT NULL`
- `display_name TEXT NULL`

A partial unique index permits at most one `pending` challenge for `(chat_id, user_id)`.

### `zero_trust_verification_history`

Immutable audit/history row for completed outcomes.

- `id BIGSERIAL PRIMARY KEY`
- `chat_id BIGINT NOT NULL`
- `user_id BIGINT NOT NULL`
- `challenge_id BIGINT NULL`
- `result TEXT NOT NULL` — `passed | failed | expired | cancelled`
- `username TEXT NULL`
- `display_name TEXT NULL`
- `joined_at BIGINT NOT NULL`
- `completed_at BIGINT NOT NULL`
- `attempts INTEGER NOT NULL`

Indexes:

- `(chat_id, user_id, completed_at DESC)`
- `(user_id, completed_at DESC)`

History is informational/audit data only. A historical `passed` row does **not** bypass the next join challenge.

## Challenge lifecycle

### Join

1. Ignore Telegram bots.
2. Check exact `chat_id` against `ZERO_TRUST_CHAT_IDS`.
3. Restrict the joining user immediately.
4. Cancel/expire any previous pending challenge for the same `(chat_id, user_id)`.
5. Create a fresh challenge in PostgreSQL.
6. Send the challenge message and save its Telegram `message_id` when available.

### Callback

1. Derive `chat_id` from the callback message and `user_id` from callback payload/actor.
2. Load the active pending challenge for exact `(chat_id, user_id)`.
3. Reject another person pressing the buttons.
4. If expired, mark `expired`; do not grant permissions.
5. Wrong answer increments attempts and records the failure state according to the existing failure policy.
6. Correct answer atomically marks `passed`, appends history, then restores Telegram permissions.
7. Delete the challenge message if possible and send the appropriate generic/writers success message.

The database transition must happen transactionally. Telegram API failures are logged explicitly; no cross-chat fallback is allowed.

## Restart behavior

A restart does not lose active challenges. On startup, the service can expire stale `pending` rows whose `expires_at` is in the past. Users with still-valid pending challenges remain restricted; a new join event always creates a new session.

No attempt is made to restore legacy in-memory `passed_users`: that information was already lost on previous restarts.

## Security invariants

- Trust is always scoped by `chat_id + user_id`.
- Historical success never grants automatic future access.
- Callback validation never checks by `user_id` alone.
- `ALLOWED_CHATS` cannot silently disable Zero Trust.
- No plaintext secrets are added to the repository.
- PostgreSQL is fail-closed for new join verification: if the Zero Trust storage is unavailable, the bot must not silently mark a new user as trusted.

## Compatibility

- Existing writers profanity moderation remains unchanged.
- Existing writers welcome/success wording and rules URL remain unchanged.
- Existing summaries/statistics/Entertainment are out of scope.
- The planned writers submission Mini App is a separate subsystem/spec and is not bundled into this security migration.

## Tests

TDD coverage must include at least:

1. pass in writers chat does not bypass `-1003237014529`;
2. pass in trader/xer chat does not bypass writers chat;
3. same user can hold independent pending challenges in two chats;
4. rejoin in same chat creates a new challenge after previous success;
5. restart-safe pending challenge persistence;
6. restart-safe verification history;
7. wrong answer mutates only exact `(chat_id, user_id)`;
8. callback from another user cannot complete a challenge;
9. expired challenge cannot restore permissions;
10. writers presentation still includes rules/success copy;
11. trader/xer `-1003237014529` is in default Zero Trust scope;
12. overriding `ALLOWED_CHATS` does not disable Zero Trust;
13. PostgreSQL integration tests for transactionality and uniqueness;
14. startup diagnostics show effective security scope.

## Rollout

1. Add schema creation/migration in an idempotent startup path.
2. Deploy Zero Trust v2 with PostgreSQL available.
3. Confirm startup log includes `-1003237014529` in `ZERO_TRUST_CHAT_IDS`.
4. Test a fresh join in writers chat and trader/xer chat with separate users/accounts.
5. Remove legacy in-memory verification decisions only after v2 tests and production startup checks pass.

## Success criteria

- `-1003237014529` challenges every new human join independently.
- No verification in one chat affects another chat.
- Every verification from Zero Trust v2 onward is stored in PostgreSQL.
- Bot restart does not erase verification state/history.
- Writers-specific UX is preserved.
