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

def register_admin_queue(app, storage, config):
    admins = set(map(int, config.moderator_ids)) | {int(config.owner_user_id)}

    @app.dp.message(Command("writers_admin"))
    async def menu(message):
        if not message.from_user or message.from_user.id not in admins or message.chat.type != "private":
            return
        await message.answer("📚 Управление заявками ИКФ", reply_markup=keyboard())

    @app.dp.callback_query(lambda c: bool(c.data and c.data.startswith("wadmin:")))
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
