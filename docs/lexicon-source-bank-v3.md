# Lexicon Source Bank v3 — scientific words and permanent no-replay ledger

> **Обновление:** правило постоянного запрета повторов из этой версии
> заменено в Lexicon Source Rotation v4. Теперь источники не повторяются
> **внутри одного круга**, а после его завершения следующий круг запускается
> автоматически. Подробности: [Lexicon Source Rotation v4](lexicon-source-rotation-v4.md).

## Purpose

Expand Russian starter words into chemistry, physics, medicine, geology,
technology and humanities. The original game rules stay unchanged: only a
validated Russian noun in dictionary form with at least 38 playable subwords
can be a starting word. The curated list is a set of *proposals*, not a list
of guaranteed playable rounds.

## Vocabulary

- 229 new nonduplicate proposal entries in
  `lexicon_source_words_extra3.py`, spanning six topic groups.
- The established `pymorphy3` canonical-noun validation, profanity filter,
  minimum-source length and minimum number of available solutions continue
  to apply. Unknown/unrecognized terms are excluded, not forced into the game.
- The playable shortlist upper cap expands from 1,600 to 2,100 while keeping
  tier-balanced quality selection. The actual playable count depends on the
  grammar and subword checks and is shown at runtime through `/lexicon_pool`.

## Permanent no-replay policy (SQLite)

The legacy `wordgame_source_cycle` table rotated sources per topic and
discarded old cycles, enabling old sources to recur eventually. This release
adds `wordgame_source_claims` to the existing minigames SQLite file:

- `source_word TEXT PRIMARY KEY`;
- `letter_bag TEXT UNIQUE` prevents differently ordered words with exactly
  the same letters being presented as a "new" source;
- `chat_id`, `thread_id` and `used_at` for audit, without extra user data.
- The ledger is **global to this MiniGameStorage SQLite database**, across
  chats and forum topics in the same bot installation.
- Existing `wordgame_source_cycle` rows and all
  `wordgame_round_archive` rows are backfilled before the first new claim.
- Reservations are acquired inside the storage lock with SQLite
  `BEGIN IMMEDIATE` and uniqueness constraints, before the first game
  message is sent. Simultaneous games and process restarts cannot replay a
  reserved source.
- Active in-memory rounds are also considered when selecting a new word.
- **Never fall back to random selection** if SQLite is unavailable or all
  playable sources have been used: display a friendly explanatory message
  instead, without starting a repeat round.
- `/lexicon_pool` now shows how many **never-used** pages remain (across
  the whole installation), in addition to total/tier counts.
- No manual migration command or environment variable required.

## Historical limitation

Old versions retained only a bounded number of source cycles. Finished
rounds with participants also appear in `wordgame_round_archive`, and those
sources are recovered. However, very old empty rounds whose cycle entries
were already deleted **cannot** be reconstructed. From the moment this
release is installed, every claimed source is stored permanently as long
as the original SQLite file exists.

## Rollout and testing

Unit/SQLite tests verify:
- the curated bank is properly shaped and profanity-free,
- global non-repetition across chats, topics and concurrent claims,
- persistence after reopening the same SQLite file,
- recovery from preexisting source-cycle rows and historic archives,
- anagram avoidance,
- refusing to launch if exhausted or if history storage is unavailable.

Keep `DATA_DIR` persistent across Bothost rebuilds. **Do not delete,
reinitialize, or replace the existing `minigames.db` file** if old history
must be preserved. Other scores, rankings and the raccoon collection are
unchanged.

Merge only after full CI (Python, PostgreSQL, Docker) is green, then rebuild
Bothost from updated `main`.
