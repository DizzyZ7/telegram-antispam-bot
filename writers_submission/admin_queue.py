"""Telegram moderator menu for publication queues."""
from __future__ import annotations
import html
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
            [InlineKeyboardButton(text="🏠 Меню", callback_data="wadmin:review:0")]
        ])
        await callback.message.edit_text("\n\n".join(lines), parse_mode="HTML", reply_markup=markup)
        await callback.answer()

    @app.dp.callback_query(lambda c: bool(c.data and c.data.startswith("wadmin:date:")))
    async def choose_day(callback):
        if not callback.from_user or callback.from_user.id not in admins:
            await callback.answer("Нет доступа", show_alert=True)
            return
        try:
            _, _, work_id, offset_raw = callback.data.split(":")
            work_id = str(UUID(work_id))
            offset = int(offset_raw)
            if not 0 <= offset <= 90:
                raise ValueError
        except (ValueError, TypeError):
            await callback.answer("Неверная дата", show_alert=True)
            return
        day = datetime.now(ZoneInfo("Europe/Moscow")).date() + timedelta(days=offset)
        buttons = []
        for hour in (10, 12, 15, 18, 20):
            buttons.append([InlineKeyboardButton(
                text=f"{hour:02d}:00 МСК",
                callback_data=f"wadmin:slot:{work_id}:{offset}:{hour}",
            )])
        navigation = []
        if offset:
            navigation.append(InlineKeyboardButton(text="◀️ День", callback_data=f"wadmin:date:{work_id}:{offset-1}"))
        if offset < 90:
            navigation.append(InlineKeyboardButton(text="День ▶️", callback_data=f"wadmin:date:{work_id}:{offset+1}"))
        if navigation:
            buttons.append(navigation)
        await callback.message.edit_text(
            f"📅 Выберите время публикации на {day:%d.%m.%Y} (Москва):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
        await callback.answer()

    @app.dp.callback_query(lambda c: bool(c.data and c.data.startswith("wadmin:slot:")))
    async def confirm_slot(callback):
        if not callback.from_user or callback.from_user.id not in admins:
            await callback.answer("Нет доступа", show_alert=True)
            return
        try:
            _, _, work_id, offset_raw, hour_raw = callback.data.split(":")
            work_id = UUID(work_id)
            offset, hour = int(offset_raw), int(hour_raw)
            if not 0 <= offset <= 90 or hour not in (10, 12, 15, 18, 20):
                raise ValueError
        except (ValueError, TypeError):
            await callback.answer("Неверное время", show_alert=True)
            return
        local = datetime.now(ZoneInfo("Europe/Moscow")).date() + timedelta(days=offset)
        slot = datetime(local.year, local.month, local.day, hour, tzinfo=ZoneInfo("Europe/Moscow"))
        if slot.timestamp() <= datetime.now(timezone.utc).timestamp():
            await callback.answer("Время уже прошло. Выберите другое.", show_alert=True)
            return
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"wadmin:save:{work_id}:{int(slot.timestamp())}")],
            [InlineKeyboardButton(text="◀️ Изменить", callback_data=f"wadmin:date:{work_id}:{offset}")],
        ])
        await callback.message.edit_text(f"📚 Назначить публикацию на {slot:%d.%m.%Y %H:%M} МСК?", reply_markup=markup)
        await callback.answer()

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
