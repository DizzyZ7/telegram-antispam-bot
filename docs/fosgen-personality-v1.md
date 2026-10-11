# Fosgen Personality Engine v1 (Bothost + PostgreSQL)

Fosgen's existing Culture Memory, Generation v3, topic isolation, media,
greetings and Autonomy Engine remain the default behavior. Personality is
an **explicitly opt-in**, infrequent literary joke when a participant
**replies directly to Fosgen** in the allowed Writers community. This
feature does not invite the bot to join general conversations more often:
the existing rate/quiet-hours/phase budgets still decide whether it may
speak at all.

## Commands (in the Writers chat, outside excluded topics)

- `/fun_style` — show the current selected writing tone.
- `/fun_style мягкий` — gentle, bookish, encouraging literary humor.
- `/fun_style ирония` — playful fictitious administrative literary sarcasm.
- `/fun_style абсурд` — absurd creative-world micro-scenes.
- `/fun_style off` — immediately delete the stored style preference.
- `/fun_ignore_me` — disable learning and delete the personality preference.
- `/fun_delete_me` — delete past Culture/legacy memory **and** the preference.

No implicit profiling of individual users, no nicknames, demographic/health
or other sensitive inference, no cross-chat preference sharing. Only
`(chat_id, user_id, explicit style, updated_at)` is stored. The bot never
uses its topic inference to label a person as an author/reader or to make
personal claims.

Personality reactions require **all** of the following: existing full
Entertainment scope permission, existing autonomy action approval, a
direct reply to the bot, explicit personal opt-in, user's memory consent,
a clear literary trigger in that reply, a random 28% chance, a safe
catalog response, and a line not sent in this topic in the previous 24h
(bounded up to 100 recent bot actions). If any fails, the current v3
generation stays in place and the bot does not increase its output rate.

No external AI or API, no paid service, no blocking I/O or model load;
the catalog is bounded and uses simple Russian keyword checks only.
The 36 curated micro-scenes span chapters, reading, characters and cover
art, in three tones. They add new material not copied from participants.
Existing Generation v3, Culture Memory and novelty/morality gates remain.

## Storage and rollout

Production backend: `entertainment/storage/windows.py` inherited through
`retention.py`. Idempotent `CREATE TABLE IF NOT EXISTS
ent_personality_preferences` in SQLite and PostgreSQL on startup,
primary key `(chat_id,user_id)`. No new ENV values or manual migration.
No changes to existing memory rows, draft submissions or moderation data.

Protected scopes in the Writers chat remain excluded: `292358`,
`14637`, `42817`. The isolated Lexicon-only chat `-1002659916114`
**does not** get Entertainment or personality; its game and fairy
profanity notice remain unchanged.

Regression tests cover style validation, literary context, consent,
profile isolation and deletion, deduplication, no generic unsolicited
jokes, SQLite persistence and actual PostgreSQL reinitialization.
After all CI jobs pass, deploy by downloading fresh main and doing a
full custom Dockerfile rebuild in Bothost. PostgreSQL data must persist.

**Limit:** This is v1 opt-in writing tone, not a full psychological
profile or generative LLM. It is intentionally conservative. Next
iteration may add consent-based feedback and richer context scoring
after live community testing, without turning Fosgen into a spam bot.
