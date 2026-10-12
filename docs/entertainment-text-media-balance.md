# Entertainment text/media balance hotfix

## Incident
Writers chat observed Fosgen mostly sending remembered stickers and photos
and almost no new sentences, although Generation v3 and Relationship v1
were enabled. Root cause: in `entertainment/culture_service.py`,
`select_media_candidate(...)` could succeed on practically every approved
action. Media was always sent **first**, even when `generated` contained
an already-approved relevant text. The media reuse cooldown in
`media_culture.py` was intentionally soft, permitting reuses after 300s.

## Fix
`output_balance.should_send_media` is a pure, bounded per-topic selector
used only after the existing global Autonomy + Presence checks. It reserves
text opportunities without adding messages:

- When **both safe text and eligible media** exist: prefer text on most
  approved slots; media gets a **32%** opportunity on spontaneous actions,
  **18%** on direct replies, never two media in a row, and never a projected
  greater-than-45% share over the latest 10 media/text actions.
- When **no safe text** exists: let suitable media through. The release does
  not disable stickers, photos or animations or turn a valid approved slot
  into silence just to satisfy a ratio.
- When Generation v3 fails: try a **bounded original literary text**
  from the existing carefully curated Personality catalog. This uses an
  actual human literary topic within the **last 20 minutes**, no bot or
  forwarded messages, no command, no stale conversation. Checks last
  24h bot outputs for duplicates, tries at most 3 styles, applies the
  existing entertainment-language filter; no personal profile is needed.
  If no good original sentence is available, retain media fallback.
- Store a safe `output_kind=text|media` diagnostic in the existing
  JSONB `ent_actions.metadata`. No content is added to logs or external
  services. Existing safe telemetry and per-personalization cooldown stay
  intact. No new data tables, ENV, PostgreSQL migrations or LLM.

Rate limits, inactive hours, game/permission boundaries, 3 excluded Writers
topics `292358 / 14637 / 42817`, and the Lexicon-only group
`-1002659916114` retain their existing behavior. Every approved action
still produces at most one bot message.

## Verification
Regression tests include pure share/streak rules, direct-reply preference,
fresh vs stale/bot/forwarded/command context, simulated previously-media
and previously-text action histories, v3-failure text recovery, and
media-only when no relevant text is possible.

Deploy fresh `main` with full Bothost Docker rebuild and keep PostgreSQL
data. In the Writers chat, allow normal messages to accumulate. The ratio
is a conditional bound **when text is available**, not a promise of a
fixed percentage if the context offers no safe text. Check bot status,
logs, and the group chat after release; live deployment is manual.
