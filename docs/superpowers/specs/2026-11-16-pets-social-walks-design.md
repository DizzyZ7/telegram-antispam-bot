# Pets: sex, walks and daily journal

The first live owners want to take their permanent friends into the group,
see what everyone did, and earn gentle progression. They also cannot tell the
species or sex from the current private card. This increment ships that playable
loop; collection expansion remains a separately migrated follow-up.

## Current contracts

- PostgreSQL has one permanent pet per owner, immutable DNA, source art and origin.
- `/pet` and care are private; regular messages count after moderation.
- The selected isolated Lexicon chat has a separate explicit Pets scope.
- No sex is currently encoded; masculine origin-story wording is not sex.

## Decisions for this increment

- Freeze logical sex v1 as the low bit of SHA256(namespace + canonical DNA
  fingerprint). It is independent of owner names, chat messages and species;
  near 50/50 over generated DNA. Publish fixed algorithm test vectors. This
  adds a permanent trait without rewriting a single existing DNA/portrait.
- Display species and boy/girl in card and passport. Preserve stored origin
  stories exactly, including legacy grammar. New stories use sex-aware wording
  and no species-inappropriate paw actions; no retrospective rewriting.
- `/pet_walk [meadow|forest|river]` and `/pet_graze` are explicit group actions.
  Only selected Pets groups, original personal senders, no pending captcha.
  All existing outer moderation still runs. Never widen the global allowlist.
- One 30-minute walk per owner per Moscow calendar day across all chats/topics.
  A walk across midnight must finish before another starts. Requests are also
  deduplicated by Telegram chat/message. Persist a per-owner/chat monotonic
  request watermark even for rejections, so replay cannot become a new next-day
  walk; conservatively ignore out-of-order older commands without retaining an
  unbounded rejection ledger. Telegram events older than 5 minutes
  or more than 1 minute ahead cannot start a new walk.
- Persist start/end/day/chat/topic, pet ID, route and a bounded original activity
  text. No human message text, display names, usernames or photos are retained.
  The explicit command publishes this pet's participation in this chat's journal.
- Activity text is species-appropriate and sex-aware. No calf paws, fox grazing
  or fabricated messages from humans. Select local templates deterministically
  from DNA, date and route; store the result so template changes preserve history.
- Completed walks grant 20 XP, inferred once from each unique persistent row
  whose end time has passed. No claim deadline, timers, workers, energy decay,
  streak reset or overnight failure. Level = 1 + isqrt(XP // 25), capped at 100.
  This is friendship progression; the immutable portrait does not age visually.
- `/pet_diary [YYYY-MM-DD] [page]` reads only the current selected group. Moscow
  dates, 10 entries/page, bounded Telegram output, explicit next-page hint.
  It works in all topics; original walk topic is recorded. No cross-chat lookup.
- Private `/pet` shows XP/level and walk help. Group commands never reveal DNA,
  passport, private origin/history, progress, or another owner's care controls.
- `FOSGEN_PETS_SOCIAL_ENABLED=1` explicitly enables group actions and progression;
  default off. Sex/species labels are safe for all existing pets. A failed social
  handler never disables moderation or existing Pets handlers.

## Alternatives considered

An instant daily button is simpler but gives no sense of walking together.
Timer workers plus automatic group reports add operational failure and spam.
Chosen design: persistent timed rows, lazy read-time completion and on-demand
journal, with no new dependencies or background scheduler.

## Later collection design (proposal, not enabled)

The owner's first pet always remains; extra pets get new immutable IDs/art.
Proposed slots: second after 7 walking days, third after 21, fourth after 45.
A rare early encounter may offer the same path before a milestone; check luck
once per completed walk with a persisted decision, never per command/message.
At a milestone or lucky encounter, a fixed wild friend returns on 3 separate walking days; claiming
is voluntary, private, and idempotent, with no expiration while the owner is away.
The numbers need live balancing before adoption. This requires replacing owner
uniqueness with `(owner, slot)` uniqueness, preserving unique slot 1, selecting
an active pet, and updating all ownership/care/checkers queries. None of that
migration is hidden in this increment. No promise of multi-pet adoption yet.

## Acceptance

Real disposable PostgreSQL tests cover parallel starts, replay, midnight,
restart, reward uniqueness, old pets and invalid inputs. Real dispatcher tests
cover isolated scope, forums, moderation, anonymous/forwarded/edited senders,
foreign chats, command privacy, output size and storage failure. Existing
v1/v2/v3 fixtures, all permanent identity constraints and test-lab isolation stay
unchanged. Publish a reviewable PR; do not auto-enable production configuration.
