# Birthday greetings: shared daily limit and addressed plans

The user additionally requires varied birthday greetings when several people
start congratulating in chat, with exactly one birthday greeting per chat per
Moscow calendar day across all topics, restarts, workers and sending paths.
The first human birthday phrase triggers the greeting; repeated phrases do not
trigger another message that day. Count source message timestamps, not old
backlog, and never count bots, forwarded messages or other
achievements. Use 2304 combinations of complete authored warm paragraphs, with
intact words and readable emoji placement. No chat-message copying or inferred
personalization. The daily database key is `(chat_id, local_date)`. Confirmed
failures can retry; uncertain/successful sends occupy the day's slot.

The user selected warm greeting variant 1 and identified the recipient by the
exact Telegram username `Buketsireni`, describing her as a storyteller. They
authorized a greeting in IKF on 2026-11-03 around 12–13 Moscow time, or earlier
when other people congratulate her. Use 12:30 Europe/Moscow as the fallback.

Add a bounded one-off plan in repository data, using only this supplied handle
and storyteller description. No author submissions, unpublished titles or
inferred personal information. Deliver a coherent warm message with normal
paragraphs and intact words. The greeting contains an @mention of the supplied
handle; do not invent a first name or confuse the handle with a pet's name.

The existing supervisor checks the plan even when the chat is quiet. Human
birthday congratulations on the same local date can deliver early only when
they explicitly address the exact handle or reply to that user's message.
Unrelated birthdays, generic greetings, forwarded posts, bots, other chats,
other dates and future years cannot trigger this one-off plan. Ambiguous
multiple-recipient mentions do not trigger early sending.
The processing date and source message date must both match. Check all explicit
recipients before accepting a reply-to-target. Reserve an addressed plan's day
for that approved recipient so generic activity cannot consume its only slot.

Persist a daily delivery claim before calling Telegram, shared by scheduled,
targeted and generic paths. Successful sends retain their Telegram message ID across
restart and replicas. Definite Telegram API rejection releases the claim for
retry; uncertain network outcomes retain it to prevent blind duplicates.
Document that uncertain delivery needs operator inspection. Explicit scheduled
greetings bypass ordinary entertainment randomness/cooldown, but stay within
the configured chat allowlist. A handled targeted greeting must not also emit
the generic celebration reply. Late same-day startup catches up; never send
after that calendar date.

Approach: extend the bot's own existing supervisor and persistence. A generic
random celebration cannot guarantee an addressed message or time; a ChatGPT
reminder alone cannot send as the deployed Fosgen bot.
