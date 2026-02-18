import asyncio
import random
import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import ChatPermissions, ChatMemberUpdated
from aiogram.filters import ChatMemberUpdatedFilter, IS_NOT_MEMBER, IS_MEMBER, Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in environment variables")

ALLOWED_CHATS = [-1002619489118]

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

pending_users = {}
CAPTCHA_TIMEOUT = 60

passed_count = 0
failed_count = 0


def user_tag(user):
    if user.username:
        return f"@{user.username}"
    return f"<a href='tg://user?id={user.id}'>{user.full_name}</a>"


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
    chat_id = event.chat.id
    if chat_id not in ALLOWED_CHATS:
        return

    user = event.new_chat_member.user

    question, answer, keyboard = build_captcha()
    pending_users[user.id] = answer

    await bot.restrict_chat_member(
        chat_id,
        user.id,
        ChatPermissions(can_send_messages=False)
    )

    msg = await bot.send_message(
        chat_id,
        f"👋 <b>{user.full_name}</b>, чтобы получить доступ к чату, реши капчу:\n\n<b>{question}</b>",
        reply_markup=keyboard
    )

    async def timeout():
        global failed_count
        await asyncio.sleep(CAPTCHA_TIMEOUT)
        if user.id in pending_users:
            pending_users.pop(user.id, None)
            failed_count += 1

            print(f"[BANNED] user_id={user.id} | username=@{user.username}")

            await bot.ban_chat_member(chat_id, user.id)
            await bot.unban_chat_member(chat_id, user.id)

            try:
                await msg.delete()
            except:
                pass

            await bot.send_message(
                chat_id,
                f"❌ {user_tag(user)} провалил испытание"
            )

    asyncio.create_task(timeout())


@dp.callback_query(F.data.startswith("captcha:"))
async def captcha_handler(callback):
    global passed_count

    chat_id = callback.message.chat.id
    if chat_id not in ALLOWED_CHATS:
        return

    user_id = callback.from_user.id
    value = int(callback.data.split(":")[1])

    if user_id not in pending_users:
        await callback.answer("Проверка уже завершена")
        return

    if value == pending_users[user_id]:
        pending_users.pop(user_id, None)
        passed_count += 1

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

        try:
            await callback.message.delete()
        except:
            pass

        await bot.send_message(
            chat_id,
            f"✅ {user_tag(callback.from_user)} справился с испытанием"
        )

        print(f"[PASSED] user_id={user_id}")
        await callback.answer("Проверка пройдена")

    else:
        print(f"[WRONG CAPTCHA] user_id={user_id}")
        await callback.answer("❌ Неверно", show_alert=True)


@dp.message(Command("stats"))
async def stats_cmd(message):
    if message.chat.id not in ALLOWED_CHATS:
        return

    await message.reply(
        f"📊 Статистика бота:\n"
        f"✅ Справились с испытанием: <b>{passed_count}</b>\n"
        f"❌ Провалили испытание: <b>{failed_count}</b>"
    )


@dp.message()
async def delete_messages_from_pending(message):
    chat_id = message.chat.id
    if chat_id not in ALLOWED_CHATS:
        return

    if message.from_user.id in pending_users:
        try:
            await message.delete()
        except:
            pass


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
