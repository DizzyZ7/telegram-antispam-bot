# Zero Trust v2 — per-chat PostgreSQL verification

Date: 2026-10-06

## Goal

Replace the current process-local captcha state with a restart-safe Zero Trust subsystem backed by PostgreSQL.

The critical bug is cross-chat trust leakage: today `pending_users`, `passed_users` and `failed_users` are keyed only by `user_id`, so passing a captcha in one allowed chat can affect another allowed chat. The trader/xer chat `-1003237014529` must be independently protected even when the same user already passed verification elsewhere.

Old successful verifications from before Zero Trust v2 are **not recoverable** because the legacy state lived only in RAM and the bot has already been rebuilt/restarted. We do not invent or backfill fake trust. From the first Zero Trust v2 deployment onward, every verification session and successful pass is persisted.

## Required behavior

1. Every `IS_NOT_MEMBER -> IS_MEMBER` event in a protected chat creates a fresh verification session for the exact `(chat_id, user_id)`.
2. Passing in one chat never grants trust in another chat.
3. Rejoining the same chat requires a new captcha.
4. One user joining two protected chats simultaneously gets two independent challenges.
5. Active challenges and completed verification history survive bot restarts.
6. Pending users remain restricted until their current session passes.
7. A wrong answer updates only the matching challenge and does not affect another chat/session.
8. The writers chat keeps its custom welcome/success copy and rules link, but uses the same Zero Trust service and storage as legacy chats.
9. The trader/xer chat `-1003237014529` remains independently protected.
10. Bots are not challenged.
11. Leaving a chat cancels an unfinished challenge; a later rejoin creates a new one.

## Architecture

Create a focused `zero_trust/` package instead of continuing to grow globals in `legacy_main.py`.

Suggested units:

- `zero_trust/models.py` — challenge/status dataclasses and enums.
- `zero_trust/storage.py` — PostgreSQL persistence contract and implementation.
- `zero_trust/service.py` — create challenge, validate answer, expire/cancel, finalize access.
- `zero_trust/handlers.py` — generic join/leave/callback routing for protected chats.
- `zero_trust/presentation.py` — generic captcha text and keyboard formatting.

`writers_moderation.py` keeps profanity/rules-specific behavior only. Its join flow delegates challenge creation/validation to Zero Trust v2 and supplies writers-specific presentation hooks.

The old `pending_users`, `passed_users`, `failed_users` globals stop being sources of truth and are removed from verification decisions.

## Protected-chat configuration

The current `ALLOWED_CHATS` setting mixes general legacy feature access with captcha protection. Zero Trust v2 introduces a dedicated setting:

```env
ZERO_TRUST_CHAT_IDS=-1002619489118,-1003237014529,-1003643412493,-1003687304800
ZERO_TRUST_CHALLENGE_TTL_SECONDS=300
```

The default security scope contains the four chats that are currently protected by the legacy join handler, including `-1003237014529`.

`ALLOWED_CHATS` remains for legacy summaries/statistics/other legacy handlers and no longer determines whether Zero Trust runs.

At startup log:

- effective `ZERO_TRUST_CHAT_IDS`;
- effective `ALLOWED_CHATS`;
- configured challenge TTL;
- a warning if the trader/xer chat `-1003237014529` is missing from `ZERO_TRUST_CHAT_IDS`.

This separates security scope from unrelated feature scope and prevents an `ALLOWED_CHATS` override from silently disabling entry verification.

## PostgreSQL model

Use one durable table as both current state and verification history. Do not duplicate final results into a second history table.

### `zero_trust_challenges`

Each join creates a new row which is retained after completion.

- `id BIGSERIAL PRIMARY KEY`
- `chat_id BIGINT NOT NULL`
- `user_id BIGINT NOT NULL`
- `expected_answer INTEGER NOT NULL`
- `attempts INTEGER NOT NULL DEFAULT 0`
- `status TEXT NOT NULL` — `pending | verified | passed | expired | cancelled`
- `created_at BIGINT NOT NULL`
- `expires_at BIGINT NOT NULL`
- `verified_at BIGINT NULL`
- `completed_at BIGINT NULL`
- `telegram_message_id BIGINT NULL`
- `username TEXT NULL`
- `display_name TEXT NULL`

Indexes:

- `(chat_id, user_id, created_at DESC)` for per-chat history;
- `(user_id, created_at DESC)` for global audit lookup;
- partial unique index allowing at most one active (`pending` or `verified`) challenge for `(chat_id, user_id)`.

Every historical success is therefore a durable row with `status='passed'`. A historical pass is audit data only and never bypasses a later join challenge.

## Callback identity

Captcha buttons must identify the exact session, not merely the user:

```text
zt:<challenge_id>:<user_id>:<answer>
```

This prevents an old/stale captcha message from solving a later challenge for the same user. The callback actor must equal the encoded `user_id`, and the challenge row must belong to the callback message's exact `chat_id`.

## Challenge lifecycle

### Join

1. Ignore Telegram bots.
2. Check exact `chat_id` against `ZERO_TRUST_CHAT_IDS`.
3. Restrict the joining user immediately.
4. Cancel any previous unfinished challenge for the same `(chat_id, user_id)`.
5. Create a fresh `pending` challenge in PostgreSQL.
6. Send the challenge message and save its Telegram `message_id` when available.

If PostgreSQL creation fails after restriction, the user remains restricted and the error is logged; the bot never silently grants access.

### Wrong answer

1. Validate exact `challenge_id + chat_id + user_id`.
2. Increment `attempts` atomically.
3. Keep the challenge `pending` while it is within TTL.
4. Return the existing `❌ Неверно` alert.

This preserves current retry behavior while making attempts persistent.

### Correct answer

1. Validate exact `challenge_id + chat_id + user_id` and TTL.
2. Atomically transition `pending -> verified` and set `verified_at`.
3. Restore Telegram send permissions.
4. After Telegram confirms success, atomically transition `verified -> passed` and set `completed_at`.
5. Delete the challenge message if possible and send the appropriate generic/writers success message.

If Telegram permission restoration fails, the challenge remains `verified`, the user remains restricted, and pressing the same valid challenge again may retry only the permission-restoration/finalization step. The database must never claim `passed` before Telegram access is actually restored.

### Expiry

A `pending` challenge past `expires_at` becomes `expired` and cannot restore permissions. The user stays restricted until a legitimate new join/session or explicit administrative intervention.

### Leave

An unfinished `pending`/`verified` challenge for that exact `(chat_id, user_id)` becomes `cancelled`. A later join always creates a new row.

## Restart behavior

A restart does not lose challenge state or passed history. On startup the service expires stale `pending` rows whose TTL is over. `verified` rows remain retryable because they represent a correct answer whose Telegram permission restoration/finalization did not complete.

No attempt is made to restore legacy in-memory `passed_users`: that information was already lost on previous restarts.

## Security invariants

- Trust is always scoped by exact challenge plus `chat_id + user_id`.
- Historical success never grants automatic future access.
- Callback validation never checks by `user_id` alone.
- Stale captcha buttons cannot solve a new challenge.
- `ALLOWED_CHATS` cannot silently disable Zero Trust.
- No plaintext secrets are added to the repository.
- PostgreSQL failures never cause fail-open access.

## Compatibility

- Existing writers profanity moderation remains unchanged.
- Existing writers welcome/success wording and rules URL remain unchanged.
- Existing summaries/statistics/Entertainment are out of scope.
- The planned writers submission Mini App is a separate subsystem/spec and is not bundled into this security migration.

## Tests

TDD coverage must include at least:

1. pass in writers chat does not bypass `-1003237014529`;
2. pass in trader/xer chat does not bypass writers chat;
3. same user can hold independent challenges in two chats;
4. rejoin in same chat creates a new challenge after previous success;
5. restart-safe pending/verified challenge persistence;
6. restart-safe passed-history persistence;
7. wrong answer increments only the exact challenge;
8. callback from another user cannot complete a challenge;
9. stale challenge callback cannot complete a newer challenge;
10. expired challenge cannot restore permissions;
11. Telegram permission failure leaves state `verified`, not `passed`;
12. retry from `verified` can finalize after Telegram succeeds;
13. leave cancels only the exact chat/user challenge;
14. writers presentation still includes rules/success copy;
15. trader/xer `-1003237014529` is in default Zero Trust scope;
16. overriding `ALLOWED_CHATS` does not disable Zero Trust;
17. PostgreSQL integration tests for transactionality and active-session uniqueness;
18. startup diagnostics show effective security scope.

## Rollout

1. Add idempotent PostgreSQL schema initialization.
2. Add `ZERO_TRUST_CHAT_IDS` and TTL to `.env.example`/README.
3. Deploy Zero Trust v2 with PostgreSQL available.
4. Confirm startup log includes `-1003237014529` in `ZERO_TRUST_CHAT_IDS`.
5. Test fresh joins in writers chat and trader/xer chat with separate accounts.
6. Remove legacy in-memory verification decisions after the v2 handler path is verified.

## Success criteria

- `-1003237014529` challenges every new human join independently.
- No verification in one chat affects another chat.
- Every verification session from Zero Trust v2 onward is durable in PostgreSQL.
- Every successful pass from Zero Trust v2 onward remains queryable after restart.
- Bot restart does not erase active challenge state/history.
- Writers-specific UX is preserved.
