import asyncio
import random
import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import ChatPermissions, ChatMemberUpdated
from aiogram.filters import ChatMemberUpdatedFilter, IS_NOT_MEMBER, IS_MEMBER
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode

TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

bot = Bot(TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

pending_users = {}  # user_id -> correct_answer
CAPTCHA_TIMEOUT = 60  # секунд


def build_captcha():
    a = random.randint(1, 9)
    b = random.randint(1, 9)
    answer = a + b

    options = list({answer, answer + 1, answer - 1, answer + 2})
    random.shuffle(options)

    kb = InlineKeyboardBuilder()
    for opt in options:
        kb.button(text=str(opt), callback_data=f"captcha:{opt}")

    kb.adjust(len(options))
    return f"{a} + {b} = ?", answer, kb.as_markup()


@dp.chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
async def on_user_join(event: ChatMemberUpdated):
    user = event.new_chat_member.user
    chat_id = event.chat.id

    question, answer, keyboard = build_captcha()
    pending_users[user.id] = answer

    await bot.restrict_chat_member(
        chat_id,
        user.id,
        ChatPermissions(can_send_messages=False)
    )

    msg = await bot.send_message(
        chat_id,
        f"👋 <b>{user.full_name}</b>, реши капчу:\n\n<b>{question}</b>",
        reply_markup=keyboard
    )

    async def timeout():
        await asyncio.sleep(CAPTCHA_TIMEOUT)
        if user.id in pending_users:
            pending_users.pop(user.id, None)
            await bot.ban_chat_member(chat_id, user.id)
            await bot.unban_chat_member(chat_id, user.id)
            await msg.delete()

    asyncio.create_task(timeout())


@dp.callback_query(F.data.startswith("captcha:"))
async def captcha_handler(callback):
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    value = int(callback.data.split(":")[1])

    if user_id not in pending_users:
        await callback.answer("Проверка уже завершена")
        return

    if value == pending_users[user_id]:
        pending_users.pop(user_id, None)

        await bot.restrict_chat_member(
            chat_id,
            user_id,
            ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True
            )
        )

        await callback.message.delete()
        await callback.answer("Готово ✅")
    else:
        await callback.answer("❌ Неверно", show_alert=True)


@dp.message()
async def delete_messages_from_pending(message):
    if message.from_user.id in pending_users:
        await message.delete()


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
