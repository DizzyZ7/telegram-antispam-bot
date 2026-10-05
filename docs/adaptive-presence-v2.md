# Entertainment — Adaptive Presence v2

Adaptive Presence v2 controls **when** Entertainment participates in a forum topic. Culture Memory and Generation v3 continue to control **what** it says or which remembered media item fits the current context. The amount of learned history never increases posting frequency by itself.

## Topic-local activity horizon

Presence decisions are isolated by exact `(chat_id, topic_id)` and use bounded human-activity windows:

- messages in the last 1, 5 and 15 minutes;
- messages in the last 60 and 120 minutes;
- active users in the last 5 and 60 minutes;
- time since the latest human message;
- persisted Entertainment actions from the same topic for the last 120 minutes;
- number of human messages since the latest Entertainment action.

The supervisor keeps a recently active topic eligible for evaluation for up to 120 minutes. A topic with no human activity inside that horizon is removed from the in-process supervisor set, so Entertainment never wakes up a dead conversation on a fixed hourly timer.

## Shared presence budget

Normal generated text, remembered stickers/photos/animations and morning/night greetings all consume the same presence budget. Greeting-specific probability/cooldown rules remain an additional restriction; they no longer allow a greeting to bypass a recent normal text or media action.

The default `ALIVE` profile adapts to the current conversation phase:

| Phase | Max actions / 30m | Minimum gap | Human messages required after last bot action |
| --- | ---: | ---: | ---: |
| Quiet | 1 | 60 min | 2 |
| Recently busy / cooldown | 1 | 40 min | 4 |
| Warming up | 1 | 30 min | 6 |
| Active | 2 | 15 min | 10 |
| Peak | 1 | 45 min | 16 |

A quiet topic that still had substantial activity in the previous hour is treated as recently busy rather than fully quiet. At a true peak, non-direct autonomous actions remain suppressed by the existing autonomy rule, so the bot gets quieter when humans are already carrying the conversation.

`CALM` uses longer gaps and larger human-message budgets; `ACTIVE` uses shorter gaps but still requires human activity. These modes remain relative behavior profiles rather than fixed cron schedules.

## Persistence and compatibility

The shared budget reuses the existing persisted `ent_actions` history. No new database table, column or migration is introduced. SQLite and PostgreSQL only extend the existing activity aggregation query to calculate 60/120-minute human windows.

Minimal/legacy storage implementations that do not expose activity/action-history methods fail safely back to the previous service flow. Production SQLite/PostgreSQL backends expose the full adaptive signals.

## Culture Memory boundary

Adaptive Presence does not read another chat or forum topic. Once the presence budget permits an action, the existing Culture Memory / Generation v3 / media-selection pipeline chooses content from the same topic only. Learned words, phrases, emoji style and remembered media improve contextual fit and variety, but do not grant extra action budget.

The existing hard Entertainment denylist remains outside the presence layer, so blocked writer-chat topics stop before presence reads, generation or greetings.

## Regression coverage

Tests cover:

- quiet vs active adaptive policies;
- recently busy topics transitioning through cooldown;
- 60/120-minute SQLite and PostgreSQL activity boundaries;
- a bot action older than the old 30-minute history window still consuming the quiet-topic gap;
- human-message budget requirements in active topics;
- dead topics failing closed instead of self-starting;
- supervisor retention inside 120 minutes and eviction after the horizon;
- normal bot actions suppressing a greeting through the shared budget;
- legacy storage compatibility and the existing hard-scope policy.
