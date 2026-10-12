# Lexicon Source Bank v2 — more and better starting words

## Why

Previous Lexicon startup read up to 7,500 frequent Russian source samples,
admitted at most 1,500 early sources, appended a 230-word curated long-source
bank, and then kept the top **900** playable rounds strictly by number of
available answers. This over-favored repetitive very-rich long words and
sometimes excluded interesting intermediate-difficulty rounds.

Per-chat/topic SQLite source rotation already avoids identical source words
until the whole live source set has been used, so its persistence is retained.

## Upgrade

1. A third manually selected, themed starter pack contributes **159**
   candidate Russian words, **156 new unique** against the first two curated
   packs. Themes include everyday settings, literature, science, nature and
   storytelling. The pack is an input, NOT a guarantee of playability.
2. Broaden wordfreq source exploration from 7,500 to 13,000 and the initial
   frequency-source admission cap from 1,500 to 2,400.
3. Expand the final playable bank cap from 900 to 1,600. The final actual
   total depends on how many candidates pass dictionary checks.
4. Validate that starting words are canonical Russian noun lemmas using the
   existing pymorphy3-backed dictionary gate. Reject profanity sources and
   words that do not yield at least 38 eligible solutions. Answer-bank
   profanity is also filtered.
5. Rank on more than raw answer count: short, medium (6–8 letters), long
   (9+), unique source letters and total valid answers, with caps to prevent
   giant short-word farms from dominating.
6. Preserve a mix of classic (38–74 answers), rich (75–149) and legendary
   (150+) pages when enough candidates exist. Fill unused quotas with the
   best other candidates; don't artificially shrink the pool.
7. Drop duplicate letter multisets: two differently ordered source words
   with identical bags of letters cannot produce genuinely different pages.
8. Optimize startup enumeration with per-length answer indexing and a fast
   source-letter bitset before the exact repeated-letter signature test.
   This is essential when expanding the pool; it does not weaken validation.

## Invariants

- No extra external APIs, downloads or database schema changes.
- Existing per-topic SQLite cycle, early-repeat protection and retained
  history survive a Bothost container restart **if DATA_DIR is persistent**.
- No changes to scoring, payouts, achievements, raccoons, guessing rules,
  approved-word moderation, hints or the strict Lexicon-only chat isolation.
- All dynamic words remain validated as canonical noun lemmas, not arbitrary
  endings or forms.
- The game still presents one source word and the number of admitted answer
  words. The new curated words are only visible when selected for a round.

## Validation / rollout

Tests check the expanded curated bank is unique and clean, that variety
beats a farm of short answers, that all difficulty tiers survive under a
bounded capacity, that gaps are filled, that anagrams of source words are
deduplicated, and that fast letter screening never rejects valid combinations.

Run full Python/SQLite/PostgreSQL/Docker CI. After merge, rebuild Bothost
from main with the existing DATA_DIR. Monitor
LEXICON_SOURCE_POOL_READY for real compiled playable-source totals; up to
1,600 is an upper bound, not a fabricated guarantee of exactly 1,600.
