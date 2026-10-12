# Fosgen Alive v4 — Stage 3: reliable morning/night greetings

## Root cause

An explicit human message recognized as morning/night greeting previously passed
through four separate gates:
- independent 40% chance to respond;
- 20-minute all-greeting cooldown in a topic;
- shared adaptive spontaneous-presence budget (could suppress an explicit reply
  because of a recent unrelated bot message);
- optional media substitution (could send a sticker instead of the customary
  text).

This made an ordinary "доброе утро" / "спокойной ночи" unreliable.

## Updated behavior

- Explicit HUMAN morning/night wishes in allowed topics always select a safe,
  curated TEXT reply. There is no response-probability roll and no sticker/GIF
  substitution. Randomness influences only the wording/style.
- Existing topic-local reply history prevents duplicates from Telegram retries.
- 2-minute cooldown applies only to the SAME greeting kind in the SAME topic;
  morning and night are independent. This is a conservative anti-flood limit,
  not a chance to stay silent.
- A greeting is a direct prompted social reply, so it is exempt from the
  spontaneous activity budget; other autonomous messages still use the old
  Presence Engine with no new frequency increase.
- The bot remains OFF when admins disabled the entertainment feature or text
  autonomy, configured quiet hours are active, or the topic is forbidden.
  Forwarded messages, other bots and commands do not trigger a reply.
- For consented messages, the existing Culture Memory observation path
  evaluates the greeting once. One-token and opted-out greetings also receive
  replies, without writing private/opted-out content to Culture Memory.
- A final strict safety check guards all selected greeting text against
  profanity and personal attacks.
- Additional colloquial variants are recognized (e.g. "доброго утра",
  "доброе утречко", "спокойной ночки", "спокушки", "споки ноки",
  "сладких снов всем"). Casual sentences *about* morning/night remain
  ordinary text, not greeting triggers.
- More original night/morning lines were added to the neutral/absurd catalogs.
  Without relevant literary/technical/art context, default style selection
  favors neutral, absurd or literary banter instead of random specialist voices.

## Compatibility and rollout

No database migration, environment variable, external AI/LLM, or unrelated
command change. Action records still use the established ent_actions table
with source=greeting, greeting_kind, greeting_style and output_kind=text.

Tests cover guaranteed output regardless of random values, recent unrelated
bot actions, independent greeting kinds, anti-flood, duplicate Telegram
updates, quiet hours, off mode, bot/forwarded rejection, opt-out memory,
single-token greetings, colloquial aliases, false positives, and strict
profanity/insult checks across the full greeting catalog.

Full CI (Python, PostgreSQL, Docker) should pass before PR merge. Rebuild
Bothost after merging the pull request to MAIN with the existing PostgreSQL
and environment. A rebuild from MAIN before merging does NOT include this fix.
