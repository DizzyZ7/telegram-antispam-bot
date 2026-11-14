# Fosgen Pets — design and safe rollout (draft)

Status: foundation only, disabled. No handlers are registered; no chat activity is currently collected for Pets.

## Product rules

- One permanent animal per Telegram user ID, shared across opted-in groups.
- A gift is unlocked after **100 eligible, deduplicated messages** sent **after consent** in participating groups.
- Species selected from a playful, transparent interpretation of opt-in activity; no psychological claims.
- No evolution, death, replacement, mandatory daily chores or punitive decay.
- Cute storybook art, species-correct anatomy, immutable markings and colors.
- Everything in Telegram: private chat, inline keyboards and occasional opt-in group announcements; **no Mini App**.
- Individual origin story and event log. Future writers' collaborations require explicit permission and author attribution.
- Never expose private messages or source text to writers or public story generation.

## Assets and uniqueness

Build **per-species** master silhouettes and pose templates (idle, sleeping, happy).
Each template contains named anatomical masks (face, body, legs, tail) and anchored features.
Apply compatible palette/pattern/marking layers under masks; draw eyes and outlines last.
Keep one immutable asset_version per adopted pet and cache the resulting PNG and Telegram file_id.
Appearance DNA is canonical JSON; fingerprint is SHA-256 and UNIQUE in PostgreSQL.
Creation must occur in a transaction: lock owner, verify 100 eligible messages, INSERT pet and fingerprint, retry collision with next attempt. No check-then-insert races.
A distinct DNA fingerprint alone **does not guarantee visually distinct portraits**: art masks and marking variants must be rendered perceptibly different. Do not claim full visual uniqueness until a renderer and visual-difference tests exist.
Do not generate images on every /pet request.

## Counting and privacy

- Opt-in per user plus enablement per group; only new eligible events count.
- Count after moderation, not before; do not count deleted/spam/command/bot messages.
- Deduplicate by (chat_id, message_id), enforce per-user rate limits and anti-farm heuristics.
- Group message text should not be stored for this feature. Store minimal metadata and derived traits; support /pet_privacy and deletion.
- Telegram message access depends on bot permissions and privacy mode; do not silently assume full visibility.
- Before rollout, define clear consent and data retention policies and make them visible to participants.

## Next milestones

1. SVG prototype for kitten/foal/calf implemented with fixed viewBox, body/head clip mask, stable marking variants and XML tests. Next: replace proof-of-concept art with polished illustrated layered assets, export PNG and add golden image/visual-difference tests.
2. Build PostgreSQL repository, transactional message accounting and uniqueness retries; add concurrency tests.
3. Register handlers behind FOSGEN_PETS_ENABLED=0; consent, /pet_progress, /pet, naming and callbacks.
4. Add care actions, idempotent events, origin story templates and privacy controls.
5. Pilot in one opted-in chat, monitor latency and spam; only then enable other chats.

## Operational boundaries

No changes to Zero Trust, entertainment, writers submissions or moderation routing in this foundation PR.


## Temporary visual laboratory: /pettest

The laboratory is disabled by default and has no access to production pet records.
On Bothost, set:

- `FOSGEN_PETTEST_ENABLED=1`
- `FOSGEN_PETTEST_ADMIN_IDS=<your numeric Telegram user ID>`
- `DATA_DIR=/app/data` (existing durable data directory)

Restart the bot after deploying this PR. Only the listed users can run these commands, and only in the bot's private chat:

- `/pettest`: create a random kitten, foal or calf with immutable DNA, unique test ID, inline PNG and complete DNA JSON.
- `/pettest 42`: show test animal #42 again with the same appearance.
- `/pettest 42 delete`: delete test record #42. No undo. Telegram messages already sent remain visible.

The temporary SQLite file is `DATA_DIR/fosgen_pettest.sqlite3`, separate from the permanent PostgreSQL schema. Do not manually delete the file unless you intentionally want to reset all temporary records and ID sequence. Deletion removes the record and DNA reservation from this test store; no real pet, owner progress or story is touched. IDs are never reused during the life of the database. Disabling the flag removes the command but retains test records until deleted.

**Current limitation:** only three prototype SVG species are renderable; final art is not ready. A DNA fingerprint does not yet prove that two PNGs are perceptually different. No pet is granted automatically and no messages are counted.

**Deployment note:** `CairoSVG` rasterizes SVG to PNG. Confirm its system Cairo dependency is present on the Bothost image and CI before enabling. If it is missing, /pettest returns an error and logs `FOSGEN_PETTEST_FAILED`.
