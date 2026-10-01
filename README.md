# 🛡️ telegram-antispam-bot

**telegram-antispam-bot** — Telegram-бот с антиспам-защитой, модерацией, статистикой, Lexicon и отдельным развлекательным слоем для разрешенных чатов.

## 🧩 Механика защиты

Challenge-Response слой работает по принципу Zero Trust:

1. **Intercept** — перехват нового участника.
2. **Quarantine** — временное ограничение отправки контента.
3. **Challenge** — арифметическая проверка с TTL.
4. **Verification** — успешный пользователь получает права обратно, проваленная проверка завершается удалением/баном согласно текущей логике бота.

## ⚙️ Базовая конфигурация

Основные переменные окружения:

- `BOT_TOKEN` — Telegram Bot API token.
- `ALLOWED_CHATS` — список разрешенных ID чатов через запятую.
- `SUMMARY_STORAGE_PATH` — SQLite-файл legacy-сводки.
- `SUMMARY_TIMEZONE` — таймзона сводки, по умолчанию `Europe/Moscow`.
- `SUMMARY_MIN_MESSAGES` — минимальное количество сообщений для расширенной сводки.
- `DATA_DIR` — каталог постоянных runtime-данных. Для хостинга его нужно направлять в persistent storage.

## 🧠 Дневная сводка и статистика

- `/summary` или `/today` — ручная сводка дня.
- `/stats` — статистика сообщений, стикеров, эмодзи и реакций.

Автоматическая отправка итогов дня не используется: сводка вызывается вручную.

## 🎭 Entertainment

Entertainment включается только для явного allowlist. В текущем production-конфиге чат `-1002619489118` добавляется как default, при этом значение можно переопределить через environment:

```env
ENTERTAINMENT_CHAT_IDS=-1002619489118
```

Память разделяется по `chat_id + topic_id`: разные темы forum-чата не смешивают локальный контекст. Между разными чатами данные также не смешиваются.

Текущий фундамент поддерживает:

- локальное обучение на подходящих сообщениях;
- topic-aware генерацию текста;
- отдельную память и настройки чатов;
- `/fun` и `/fun_generate`;
- admin enable/disable и очистку памяти текущей темы;
- PostgreSQL и SQLite через единый storage contract;
- безопасную миграцию существующей V1 SQLite-памяти в PostgreSQL.

Старые V1 numeric controls пока сохранены только для обратной совместимости. В Autonomy v2 они будут заменены нашими собственными режимами поведения (`Спокойный`, `Живой`, `Активный`) и state-driven движком без пользовательского параметра «лень».

### PostgreSQL на Bothost

Если `DATABASE_URL` отсутствует, Entertainment использует:

```text
$DATA_DIR/entertainment.db
```

Если `DATABASE_URL` задан с `postgres://` или `postgresql://`, PostgreSQL становится production backend:

```env
DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/DATABASE
```

Не добавляйте реальную строку подключения в Git или исходный код — задавайте ее в переменных окружения проекта на Bothost.

По умолчанию ошибка подключения к настроенному PostgreSQL останавливает запуск Entertainment вместо молчаливого создания второй SQLite-базы. Это защищает от split-brain памяти.

Аварийный fallback можно включить только явно:

```env
ENTERTAINMENT_DB_FALLBACK_SQLITE=1
```

Для production рекомендуется оставить fallback выключенным.

### Миграция SQLite → PostgreSQL

При запуске с PostgreSQL бот проверяет старый `$DATA_DIR/entertainment.db` и, если он существует:

1. импортирует старые настройки, не перетирая более новые настройки в PostgreSQL;
2. переносит старые сообщения в topic `0`;
3. запоминает исходные legacy ID и marker миграции;
4. повторный запуск не создает дубли;
5. исходный SQLite-файл автоматически не удаляется.

В логах запуска появляется `ENTERTAINMENT_STORAGE_READY` с backend и количеством импортированных строк, но без credentials/DSN.

## 🛠 Установка и запуск

```bash
git clone https://github.com/DizzyZ7/telegram-antispam-bot.git
cd telegram-antispam-bot
pip install -r requirements.txt
python main.py
```

Для чтения обычных сообщений группой Telegram бот должен действительно получать эти сообщения: обычно это обеспечивается правами администратора либо соответствующей настройкой Group Privacy Mode.

## 🧪 Проверки

```bash
python -m compileall -q .
python -m unittest discover -s tests -p "test_*.py"
```

GitHub Actions дополнительно поднимает реальный PostgreSQL 17 и проверяет storage contract отдельно от основного тестового job.
