# Writers profanity moderation — incident response

## Writers profanity moderation and three explicit forum exemptions

The Writers language filter is scoped to `WRITERS_CHAT_ID`. It blocks
prohibited words and severe insults, then asks Telegram to delete the
offending message and posts a rules notice (rate-limited).

**By explicit owner request, exactly three forum topic IDs are exempt from
profanity deletion: `14637`, `42817`, `292358`.** This is a language-filter
exception, not a shutdown of Zero Trust, Mini App, Lexicon or other handlers.
All other topics, including the main/general Writers topic (`1` or no
`message_thread_id`), are checked normally.

The lexicon explicitly includes `нихуя` and `нихуе` prefixes, so a
message such as `НИХУЯ СЕБЕ` must be classified as obscene in the general
topic and deleted, assuming the bot has Telegram deletion rights.

The handler is re-promoted to first position after all other handlers
(including Writers Submission Mini App, Lexicon and Entertainment) finish
registering. This prevents catch-all handlers from consuming a message
before the profanity checker.

Anonymous administrators posting as a sender chat are also filtered. Regular
bot-originated messages are excluded. Slash-prefixed profanity is not
exempted.

## Bothost smoke checklist

After deploying and restarting, look for:
- `WRITERS_TOPIC_EXCLUSIONS_READY ids=14637,42817,292358`
- `WRITERS_MODERATION_PRIORITY_READY first=remove_prohibited_language`
- `WRITERS_MODERATION_READY chat_id=-1002619489118` (or your actual Writers chat)
- `WRITERS_MODERATION_DELETE_PERMISSION_READY`

The bot must be an **administrator with Delete messages permission** in the
**writers chat**, not merely in a moderation or private-storage chat. When
permissions are absent, logs will contain
`WRITERS_MODERATION_DELETE_PERMISSION_MISSING`. If an attempted deletion
is rejected by Telegram, logs contain `WRITERS_PROFANITY_DELETE_FAILED`
with chat, topic and message ID (no message text). Successful deletions log
`WRITERS_PROFANITY_DELETED`.

A `WRITERS_MODERATION_SCOPE_MISSING` or unexpected `chat_id` indicates
incorrect `WRITERS_CHAT_ID` / chat access and should be fixed before
assuming a dictionary failure.

Check with a permitted test account in a temporary, designated non-exempt test topic
rather than sending profanity to the public community unnecessarily.
The filter protects only the configured Writers community, not every chat in
`ZERO_TRUST_CHAT_IDS`. The latter controls join verification.

If a message survives, collect its **chat ID**, **topic ID** (if any), the
precise word/obfuscation and `WRITERS_PROFANITY_DELETE_FAILED` lines,
redacting tokens/other secrets. Review the compiled lexicon's coverage and
Telegram delete permissions separately. GitHub tests cannot verify the
admin rights of a live Bothost deployment.
