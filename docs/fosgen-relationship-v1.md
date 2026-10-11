# Fosgen Relationship Engine v1

Extends **Fosgen Personality Engine v1** without replacing
Culture Memory, Generation v3, the popular local meme remixes, media
callbacks, greetings, autonomy or safety checks.

## Voluntary personal preferences

In the **allowed** Writers community (never the Lexicon-only chat):

- `/fun_style мягкий|ирония|абсурд|off`: existing personal **tone**.
- `/fun_topic главы|чтение|герои|обложки|off`: new personal **literary interest**.
- `/fun_profile`: show **only the caller's explicitly selected** options.
- `/fun_ignore_me`: disable memory and delete this opt-in profile (both tone
  and topic). `/fun_delete_me`: delete source memory and personal profile.

A topic requires an existing voluntary `/fun_style` and memory consent.
The only persisted data is `chat_id, user_id, style, preferred_topic,
last_personalized_at, updated_at`. No inferred age, gender, emotions,
health, psychological profile, private messages or cross-chat links. A
participant can switch topic, remove the topic without changing tone, or
remove the entire profile.

The selected interest NEVER overrides a different real conversation topic.
It matters only when the participant **directly replies to the bot with an
explicit request to joke**, e.g. "Фосген, пошути!" and the message itself
does not name another literary topic. A message about plot takes priority
over an earlier interest in cover art.

## More variety, unchanged activity budgets

There are now **48 original, bounded, pre-reviewed literary lines**
(4 topics × 3 tonal styles × 4 lines), up from 36. The existing source-based
Culture Memory + Generation v3 is still the default; none of the new lines
quotes a chat member or makes claims about their personality.

- **Direct opt-in reply**: ~28% chance of a curated line within an
  *already-approved* contextual reply action. A SQL-backed, atomic
  **6-hour per-user** personalized cooldown is enforced across topics
  and bot workers. It is only reserved after deciding against a media reply.
  Failed send attempts can consume a slot; this conservative behavior is
  preferable to accidental floods.
- **Public literary interjection**: ~8% of already-approved autonomous
  **text** actions may use a contextual, authored line instead of the normal
  remix. Selected tone is randomized, no person is addressed, no stored
  per-user preferences are consulted. No public line is generated during
  supervisor runs and no extra messages are added.
- **Anti-repetition**: compare selected lines against the last 100 recorded
  actions within 24h in the same chat/topic, rejecting identical and
  near-identical lines (Unicode/case/punctuation normalized). If no fresh,
  safe, relevant line exists, use the normal Generation v3 result.
- All existing cooldowns, activity phases, quiet hours, on/off switches,
  chat/topic allowlists and profanity/excluded-topic rules apply BEFORE
  personality selection. One autonomy slot yields at most one message.

## Infrastructure and release

Uses built-in Python, aiogram, PostgreSQL and the existing storage layer.
No LLM, new service, Redis or ENV changes. Existing
`ent_personality_preferences` gains optional columns
`preferred_topic TEXT` and `last_personalized_at BIGINT DEFAULT 0`
via idempotent `ALTER TABLE IF NOT EXISTS` on startup; existing style rows
are preserved. SQLite fallback/test backend also adds columns non-
destructively. PostgreSQL `UPDATE ... WHERE last_personalized_at <= cutoff`
atomically reserves a personal reply, even with concurrent workers.

No change to the Writers Mini App, private file storage, owner-only
moderation, the three Writers exempt topics `292358`, `14637`,
`42817`, or isolated Lexicon chat `-1002659916114`.

After green CI, deploy latest GitHub main and **rebuild** the Bothost
custom Python image without resetting the PostgreSQL data. Check
`/fun_style ирония`, `/fun_topic обложки`, `/fun_profile`,
`/fun_topic off`, then `/fun_ignore_me` and `/fun_delete_me`.
Personality output is intentionally rare; lack of an instant joke is not
a malfunction. Verify the normal Culture Memory jokes still appear.
