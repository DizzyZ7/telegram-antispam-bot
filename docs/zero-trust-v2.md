# Zero Trust v2

Zero Trust v2 replaces the old process-local captcha trust with per-chat PostgreSQL sessions.

## Security scope

Entry verification uses its own allowlist and no longer depends on `ALLOWED_CHATS`:

```env
ZERO_TRUST_CHAT_IDS=-1002619489118,-1003237014529,-1003643412493,-1003687304800
ZERO_TRUST_CHALLENGE_TTL_SECONDS=300
DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/DATABASE
```

The trader/xer chat `-1003237014529` is in the default security scope.

If at least one Zero Trust chat is configured, `DATABASE_URL` must be PostgreSQL. Startup fails closed instead of polling without protection when the database configuration is missing or invalid.

## Verification lifecycle

- every human `join` creates a fresh challenge for the exact `chat_id + user_id`;
- passing a challenge in one chat never grants trust in another chat;
- rejoining the same chat requires a new challenge;
- stale buttons include a historical `challenge_id` and cannot complete a later challenge;
- wrong attempts and final results are durable in PostgreSQL;
- correct answers first become `verified`; only after Telegram restores permissions does the row become `passed`;
- if Telegram permission restoration fails, `verified` remains retryable;
- leaving cancels only the unfinished challenge from that exact chat;
- expired pending sessions cannot grant permissions.

The durable `zero_trust_challenges` table is both current state and history. Historical `passed` rows are audit information only and never suppress a later join challenge.

## Old verification history

Before v2, `pending_users`, `passed_users` and `failed_users` existed only in process RAM. The bot was rebuilt/restarted before this migration, so that old state was already lost and cannot be reconstructed safely. Zero Trust v2 deliberately does not invent a legacy whitelist.

Every verification session from the first v2 deployment onward is persisted.

## Startup diagnostics

Startup logs the effective Zero Trust chat IDs, legacy `ALLOWED_CHATS`, challenge TTL and warns when the trader/xer chat is omitted from an explicit Zero Trust override.

The old legacy captcha handlers are detached before polling; writers-specific welcome/success copy remains, but the writers moderation module itself owns only profanity/rules behavior.
