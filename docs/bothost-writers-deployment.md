# Bothost — Python runtime, HTTPS Mini App and composer menu

This repository is a **Python/aiogram** bot with an embedded **aiohttp**
server for Writers Submission. `writers_submission/static/app.js` is browser
JavaScript; never start it with Node.js. The entry point is `main.py`.

## Deployment of an existing archive-based bot

1. In Bothost, open the bot's **Запуск** / deployment settings. Select the
   **Python (aiogram)** language/template rather than Node.js, and enter
   `main.py` as the main file. Re-upload an archive from the current `main`
   branch; a simple restart keeps the old image.
2. The root `bothost.json` specifies the Python runtime for archive-based
   deployments. If the existing bot remains stuck on `node:20-alpine`, enable
   **Использовать собственный Dockerfile** under the deployment's additional
   settings; the root `Dockerfile` explicitly builds Python 3.12 and runs
   `python main.py`. This checkbox is required for an existing bot to use
   the custom Dockerfile; the file's presence alone does not guarantee it.
3. Check build/runtime logs: the image should be based on `python:3.12-slim`
   (when using the custom Dockerfile), not `node:20-alpine`. The command
   must run Python; `ReferenceError: window is not defined` and
   `SyntaxError: Unexpected string` indicate the wrong runtime.
4. Keep the already issued bot token, database and persistent `/app/data`
   as they were. Do not store production credentials in the GitHub repository.

## Mini App configuration

Use the domain and port assigned in the Bothost panel:

```env
PORT=3000
WRITERS_SUBMISSION_ENABLED=1
WRITERS_SUBMISSION_PUBLIC_URL=https://bot-1783536415-4824-dizzy.bothost.tech/writers/
WRITERS_SUBMISSION_BIND_HOST=0.0.0.0
# Optional when PORT is already 3000:
WRITERS_SUBMISSION_PORT=3000
# Current owner-only moderation (without any admin group):
WRITERS_SUBMISSION_MODERATION_MODE=owner
WRITERS_SUBMISSION_OWNER_USER_ID=2039781854
# Separate private attachment-storage chat, required:
WRITERS_SUBMISSION_FILE_CHAT_ID=-100...
# ONLY after creating the future private admin review group:
# WRITERS_SUBMISSION_MODERATION_MODE=group
# WRITERS_SUBMISSION_MOD_CHAT_ID=-100...
# WRITERS_SUBMISSION_MODERATOR_IDS=...
```

`BOT_TOKEN`, `DATABASE_URL` and `WRITERS_CHAT_ID` are also required.
Never use the placeholder file-storage ID. In owner mode, the owner must
send `/start` to the bot in private before the bot can deliver application
cards or approved posts. The old group/moderator environment values are
ignored unless `WRITERS_SUBMISSION_MODERATION_MODE=group` is explicitly set.
If a required setting is missing, keep `WRITERS_SUBMISSION_ENABLED=0`
until it is set. The app uses `PORT`
when `WRITERS_SUBMISSION_PORT` is not explicitly set.

The public URL must route to the same internal port as the Writers aiohttp
listener; Bothost terminates HTTPS at Traefik.

## Persistent Telegram menu

With Writers Submission enabled and started successfully, the bot calls
`setChatMenuButton` with a `MenuButtonWebApp` labeled
**✒️ Отправить заявку**. Telegram shows the menu entry by the message
composer in the bot's **private chat**. Depending on the client, the user
may see a menu button rather than an always-visible full-width button.

This does **not** bypass WebApp signed identity validation, CSRF/origin
checks or membership rules. The old private `/start writers_submit`
and the inline **✒️ Отправить работу** button remain available as fallback.
A temporary Telegram API failure installing the menu is logged and does
not terminate the otherwise healthy bot process.

## Smoke check

- Confirm `WRITERS_SUBMISSION_READY` and (when Telegram accepts the call)
  `WRITERS_MENU_BUTTON_READY` in logs, followed by normal bot polling.
- Check `https://bot-1783536415-4824-dizzy.bothost.tech/writers/` in a
  browser; the UI should load (authentication requires Telegram WebApp).
- In the bot's private Telegram chat, open the composer menu and click
  **✒️ Отправить заявку**.
- Verify a writer can save a draft and the **owner's DM (2039781854)**
  receives the original application card and files, then the ready-to-post
  promo after approval. No message should land in the public writers group
  or the future admin group while moderation mode is `owner`.
- Confirm `WRITERS_MODERATION_ROUTE_READY mode=owner
  destination_chat_id=2039781854` in the runtime log.

If the bot is still built from `node:20-alpine`, its startup template is
not yet corrected. Resolve that in Bothost before modifying application code.
