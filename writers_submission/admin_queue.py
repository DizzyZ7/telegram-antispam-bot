"""Telegram moderator menu for publication queues."""
from __future__ import annotations
import html
import calendar
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from uuid import UUID
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

CATEGORIES = {"review": "На проверке", "approved": "Одобрены", "queue": "Очередь", "published": "Опубликованы"}

def keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"wadmin:{key}:0")]
        for key, label in CATEGORIES.items()
    ])

def is_queue_page_callback(data: str | None) -> bool:
    """Only list pages; never consume date, payment or priority callbacks."""
    if not data:
        return False
    parts = data.split(":")
    return (
        len(parts) == 3
        and parts[0] == "wadmin"
        and parts[1] in CATEGORIES
        and parts[2].isdigit()
        and 0 <= int(parts[2]) <= 10000
    )


def register_admin_queue(app, storage, config):
    admins = set(map(int, config.moderator_ids)) | {int(config.owner_user_id)}

    @app.dp.message(Command("writers_admin"))
    async def menu(message):
        if not message.from_user or message.from_user.id not in admins or message.chat.type != "private":
            return
        await message.answer("📚 Управление заявками ИКФ", reply_markup=keyboard())

    @app.dp.callback_query(lambda c: is_queue_page_callback(c.data))
    async def pages(callback):
        if not callback.from_user or callback.from_user.id not in admins:
            await callback.answer("Нет доступа", show_alert=True)
            return
        try:
            _, category, raw_page = callback.data.split(":")
            page = int(raw_page)
            if category not in CATEGORIES or not 0 <= page <= 10000:
                raise ValueError
        except (ValueError, TypeError):
            await callback.answer("Неверная страница", show_alert=True)
            return
        rows = await storage.list_publication_admin(category, page * 5)
        lines = [f"📚 <b>{CATEGORIES[category]}</b> · страница {page+1}"]
        entry_buttons = []
        for item in rows:
            lines.append("• <b>" + html.escape(str(item["title"])[:100]) + "</b>\n<code>" + str(item["id"]) + "</code>")
            if category == "queue":
                entry_buttons.append([InlineKeyboardButton(
                    text="⭐ Изменить приоритет · " + str(item["title"])[:24],
                    callback_data="wadmin:priority:" + str(item["id"]) + (":0" if item["priority"] else ":1"),
                )])
                entry_buttons.append([
                    InlineKeyboardButton(text="💳 Оплата", callback_data="wadmin:paymenu:" + str(item["id"])),
                    InlineKeyboardButton(text="📜 История", callback_data="wadmin:history:" + str(item["id"])),
                ])
                entry_buttons.append([InlineKeyboardButton(
                    text="⏸ Снять с расписания", callback_data="wadmin:unschedule:" + str(item["id"]),
                )])
            if category in ("approved", "queue"):
                entry_buttons.append([InlineKeyboardButton(text="📅 " + str(item["title"])[:30], callback_data="wadmin:date:" + str(item["id"]) + ":0")])
        if not rows:
            lines.append("Заявок пока нет.")
        nav = []
        if page:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"wadmin:{category}:{page-1}"))
        if len(rows) == 5:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"wadmin:{category}:{page+1}"))
        markup = InlineKeyboardMarkup(inline_keyboard=entry_buttons + ([nav] if nav else []) + [
            [InlineKeyboardButton(text="🏠 Меню", callback_data="wadmin:home")]
        ])
        await callback.message.edit_text("\n\n".join(lines), parse_mode="HTML", reply_markup=markup)
        await callback.answer()


    @app.dp.callback_query(lambda c: c.data == "wadmin:home")
    async def home(callback):
        if not callback.from_user or callback.from_user.id not in admins:
            await callback.answer("Нет доступа", show_alert=True)
            return
        await callback.message.edit_text("📚 Управление заявками ИКФ", reply_markup=keyboard())
        await callback.answer()

    @app.dp.callback_query(lambda c: c.data == "wadmin:noop")
    async def noop(callback):
        await callback.answer()

    @app.dp.callback_query(lambda c: bool(c.data and c.data.startswith(("wadmin:date:", "wadmin:month:"))))
    async def calendar_page(callback):
        if not callback.from_user or callback.from_user.id not in admins:
            await callback.answer("Нет доступа", show_alert=True)
            return
        try:
            _, kind, raw_id, value = callback.data.split(":")
            work_id = UUID(raw_id)
            today = datetime.now(ZoneInfo("Europe/Moscow")).date()
            day = today + timedelta(days=int(value)) if kind == "date" else datetime.strptime(value, "%Y%m").date()
            if day < today.replace(day=1) or day > today + timedelta(days=730):
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            await callback.answer("Неверная дата", show_alert=True)
            return
        first = day.replace(day=1)
        previous = (first - timedelta(days=1)).replace(day=1)
        following = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
        buttons = [[InlineKeyboardButton(text=f"📅 {first:%m.%Y}", callback_data="wadmin:noop")],
                   [InlineKeyboardButton(text=name, callback_data="wadmin:noop") for name in ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")]]
        for week in calendar.Calendar().monthdayscalendar(first.year, first.month):
            buttons.append([InlineKeyboardButton(
                text=str(d) if d and today <= first.replace(day=d) <= today + timedelta(days=730) else "·",
                callback_data=f"wadmin:day:{work_id}:{first.year:04d}{first.month:02d}{d:02d}" if d and today <= first.replace(day=d) <= today + timedelta(days=730) else "wadmin:noop",
            ) for d in week])
        buttons.append([
            InlineKeyboardButton(text="◀️", callback_data=f"wadmin:month:{work_id}:{previous:%Y%m}" if first > today.replace(day=1) else "wadmin:noop"),
            InlineKeyboardButton(text="▶️", callback_data=f"wadmin:month:{work_id}:{following:%Y%m}" if following <= today + timedelta(days=730) else "wadmin:noop"),
        ])
        buttons.append([InlineKeyboardButton(text="🏠 Меню", callback_data="wadmin:home")])
        await callback.message.edit_text("📅 Выберите дату публикации (МСК):", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        await callback.answer()

    @app.dp.callback_query(lambda c: bool(c.data and c.data.startswith(("wadmin:day:", "wadmin:hour:"))))
    async def choose_time(callback):
        if not callback.from_user or callback.from_user.id not in admins:
            await callback.answer("Нет доступа", show_alert=True)
            return
        try:
            parts = callback.data.split(":")
            work_id = UUID(parts[2])
            day = datetime.strptime(parts[3], "%Y%m%d").date()
            today = datetime.now(ZoneInfo("Europe/Moscow")).date()
            if not today <= day <= today + timedelta(days=730):
                raise ValueError
            hour = int(parts[4]) if parts[1] == "hour" else None
            if hour is not None and not 0 <= hour <= 23:
                raise ValueError
        except (ValueError, TypeError, IndexError):
            await callback.answer("Неверная дата", show_alert=True)
            return
        if hour is None:
            buttons = [[InlineKeyboardButton(text=f"{h:02d}:00", callback_data=f"wadmin:hour:{work_id}:{day:%Y%m%d}:{h}") for h in range(i, i+4)] for i in range(0, 24, 4)]
        else:
            buttons = [[InlineKeyboardButton(text=f"{hour:02d}:{m:02d}", callback_data=f"wadmin:pick:{work_id}:{day:%Y%m%d}:{hour:02d}{m:02d}") for m in range(i, i+15, 5)] for i in range(0, 60, 15)]
        buttons.append([InlineKeyboardButton(text="◀️ Календарь", callback_data=f"wadmin:month:{work_id}:{day:%Y%m}"),
                        InlineKeyboardButton(text="🏠 Меню", callback_data="wadmin:home")])
        await callback.message.edit_text(f"🕒 {day:%d.%m.%Y} МСК. Выберите {'час' if hour is None else 'минуты'}:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        await callback.answer()

    @app.dp.callback_query(lambda c: bool(c.data and c.data.startswith("wadmin:pick:")))
    async def confirm_slot(callback):
        if not callback.from_user or callback.from_user.id not in admins:
            await callback.answer("Нет доступа", show_alert=True)
            return
        try:
            _, _, raw_id, date_key, hhmm = callback.data.split(":")
            work_id = UUID(raw_id)
            if len(date_key) != 8 or len(hhmm) != 4:
                raise ValueError
            slot = datetime.strptime(date_key + hhmm, "%Y%m%d%H%M").replace(tzinfo=ZoneInfo("Europe/Moscow"))
            if slot <= datetime.now(ZoneInfo("Europe/Moscow")) or slot > datetime.now(ZoneInfo("Europe/Moscow")) + timedelta(days=731):
                raise ValueError
        except (ValueError, TypeError):
            await callback.answer("Время уже прошло или некорректно", show_alert=True)
            return
        await callback.message.edit_text(
            f"📚 Назначить публикацию на {slot:%d.%m.%Y %H:%M} МСК?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"wadmin:save:{work_id}:{int(slot.timestamp())}")],
                [InlineKeyboardButton(text="◀️ Изменить", callback_data=f"wadmin:day:{work_id}:{date_key}")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="wadmin:home")],
            ]),
        )
        await callback.answer()

    @app.dp.message(Command("time"))
    async def manual_time(message):
        if not message.from_user or message.from_user.id not in admins or message.chat.type != "private":
            return
        parts = (message.text or "").split()
        if len(parts) != 4:
            await message.answer("Формат: /time UUID ДД.ММ.ГГГГ ЧЧ:ММ\\nПример: /time 7507c432-d24f-477d-9d33-66a8bc5a4938 30.10.2026 18:30")
            return
        try:
            work_id = UUID(parts[1])
            local = datetime.strptime(parts[2] + " " + parts[3], "%d.%m.%Y %H:%M").replace(tzinfo=ZoneInfo("Europe/Moscow"))
            if local <= datetime.now(ZoneInfo("Europe/Moscow")) or local > datetime.now(ZoneInfo("Europe/Moscow")) + timedelta(days=731):
                raise ValueError
        except ValueError:
            await message.answer("Неверная дата или время. Укажите будущее время по МСК.")
            return
        saved = await storage.schedule_publication(work_id, int(local.timestamp()), message.from_user.id)
        if not saved:
            await message.answer("Работа не одобрена или уже опубликована.")
            return
        await message.answer(f"✅ Дата сохранена: {local:%d.%m.%Y %H:%M} МСК. Автор получит уведомление. Публикация остается ручной.")

    @app.dp.callback_query(lambda c: bool(c.data and c.data.startswith("wadmin:save:")))
    async def save_slot(callback):
        if not callback.from_user or callback.from_user.id not in admins:
            await callback.answer("Нет доступа", show_alert=True)
            return
        try:
            _, _, work_id, timestamp = callback.data.split(":")
            work_id, timestamp = UUID(work_id), int(timestamp)
            if timestamp <= datetime.now(timezone.utc).timestamp():
                raise ValueError
        except (ValueError, TypeError):
            await callback.answer("Дата устарела", show_alert=True)
            return
        saved = await storage.schedule_publication(work_id, timestamp, callback.from_user.id)
        if not saved:
            await callback.answer("Работа не одобрена или уже опубликована", show_alert=True)
            return
        local = datetime.fromtimestamp(timestamp, ZoneInfo("Europe/Moscow"))
        await callback.message.edit_text(f"✅ Дата сохранена: {local:%d.%m.%Y %H:%M} МСК. Публикация пока не автоматическая.")
        await callback.answer()

    @app.dp.callback_query(lambda c: bool(c.data and c.data.startswith("wadmin:priority:")))
    async def change_priority(callback):
        if not callback.from_user or callback.from_user.id not in admins:
            await callback.answer("Нет доступа", show_alert=True)
            return
        try:
            _, _, work_id, flag = callback.data.split(":")
            work_id = UUID(work_id)
            if flag not in ("0", "1"):
                raise ValueError
        except (ValueError, TypeError):
            await callback.answer("Неверная заявка", show_alert=True)
            return
        changed = await storage.update_publication_priority(work_id, flag == "1", callback.from_user.id)
        if not changed:
            await callback.answer("Изменить можно только запланированную публикацию", show_alert=True)
            return
        await callback.answer("Приоритет сохранен")
        await callback.message.edit_text("⭐ Приоритет обновлен. Вернитесь в /writers_admin для просмотра очереди.")

    @app.dp.callback_query(lambda c: bool(c.data and c.data.startswith("wadmin:paymenu:")))
    async def payment_menu(callback):
        if not callback.from_user or callback.from_user.id not in admins:
            await callback.answer("Нет доступа", show_alert=True)
            return
        try:
            work_id = UUID(callback.data.split(":")[2])
        except (ValueError, IndexError):
            await callback.answer("Неверная заявка", show_alert=True)
            return
        choices = (("Ожидает оплаты", "PENDING"), ("Оплата подтверждена", "CONFIRMED"),
                   ("Оплата не требуется", "NOT_REQUIRED"))
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"wadmin:pay:{work_id}:{state}")]
            for label, state in choices
        ])
        await callback.message.edit_text(
            "💳 Статус оплаты приоритетного размещения (200 ₽). Подтверждайте только после проверки платежа.",
            reply_markup=markup,
        )
        await callback.answer()

    @app.dp.callback_query(lambda c: bool(c.data and c.data.startswith("wadmin:pay:")))
    async def save_payment(callback):
        if not callback.from_user or callback.from_user.id not in admins:
            await callback.answer("Нет доступа", show_alert=True)
            return
        try:
            _, _, raw_id, state = callback.data.split(":")
            work_id = UUID(raw_id)
            if state not in ("NOT_REQUIRED", "PENDING", "CONFIRMED"):
                raise ValueError
        except (ValueError, TypeError):
            await callback.answer("Неверный статус", show_alert=True)
            return
        if not await storage.update_publication_payment(work_id, state, callback.from_user.id):
            await callback.answer("Изменение недоступно", show_alert=True)
            return
        await callback.message.edit_text("✅ Статус оплаты сохранен. /writers_admin")
        await callback.answer()

    @app.dp.callback_query(lambda c: bool(c.data and c.data.startswith("wadmin:unschedule:")))
    async def unschedule(callback):
        if not callback.from_user or callback.from_user.id not in admins:
            await callback.answer("Нет доступа", show_alert=True)
            return
        try:
            work_id = UUID(callback.data.split(":")[2])
        except (ValueError, IndexError):
            await callback.answer("Неверная заявка", show_alert=True)
            return
        if not await storage.unschedule_publication(work_id, callback.from_user.id):
            await callback.answer("Нельзя снять с расписания", show_alert=True)
            return
        await callback.message.edit_text(
            "⏸ Дата снята. Заявка одобрена, оплата и история сохранены. /writers_admin"
        )
        await callback.answer()

    @app.dp.callback_query(lambda c: bool(c.data and c.data.startswith("wadmin:history:")))
    async def history(callback):
        if not callback.from_user or callback.from_user.id not in admins:
            await callback.answer("Нет доступа", show_alert=True)
            return
        try:
            work_id = UUID(callback.data.split(":")[2])
        except (ValueError, IndexError):
            await callback.answer("Неверная заявка", show_alert=True)
            return
        rows = await storage.get_publication_audit(work_id)
        lines = ["📜 Последние изменения публикации:"]
        for row in rows:
            stamp = datetime.fromtimestamp(row["created_at"], ZoneInfo("Europe/Moscow"))
            lines.append(
                f"{stamp:%d.%m.%Y %H:%M} · {html.escape(str(row['action']))} · "
                f"{html.escape(str(row['old_value']))} → {html.escape(str(row['new_value']))} "
                f"(админ {row['admin_id']})"
            )
        await callback.message.edit_text(
            "\n".join(lines) if rows else "История пока пуста.", parse_mode="HTML"
        )
        await callback.answer()
