import asyncio
import random
import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import ChatPermissions, ChatMemberUpdated
from aiogram.filters import ChatMemberUpdatedFilter, IS_NOT_MEMBER, IS_MEMBER
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# ==============================
# Токен из Environment Variable
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in environment variables")

# Белый список групп — бот будет работать только здесь
ALLOWED_CHATS = [-1002619489118]  # <- твоя группа
# ==============================

# Создаём бота
bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# Словарь для хранения ожидающих капчу пользователей
pending_users = {}  # user_id -> correct_answer
CAPTCHA_TIMEOUT = 60  # секунд на прохождение капчи

# ==============================
# Функция генерации капчи
def build_captcha():
    a = random.randint(1, 9)
    b = random.randint(1, 9)
    answer = a + b

    # Генерируем варианты
    options = list({answer, answer + 1, answer - 1, answer + 2})
    random.shuffle(options)

    # Inline кнопки
    kb = InlineKeyboardBuilder()
    for opt in options:
        kb.button(text=str(opt), callback_data=f"captcha:{opt}")
    kb.adjust(len(options))
    return f"{a} + {b} = ?", answer, kb.as_markup()

# ==============================
# Новый пользователь вступил в группу
@dp.chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
async def on_user_join(event: ChatMemberUpdated):
    chat_id = event.chat.id
    if chat_id not in ALLOWED_CHATS:
        return  # Игнорируем другие чаты

    user = event.new_chat_member.user

    question, answer, keyboard = build_captcha()
    pending_users[user.id] = answer

    # Ограничиваем пользователя до прохождения капчи
    await bot.restrict_chat_member(
        chat_id,
        user.id,
        ChatPermissions(can_send_messages=False)
    )

    # Отправляем капчу
    msg = await bot.send_message(
        chat_id,
        f"👋 <b>{user.full_name}</b>, чтобы получить доступ к чату, реши капчу:\n\n<b>{question}</b>",
        reply_markup=keyboard
    )

    # Таймер на капчу
    async def timeout():
        await asyncio.sleep(CAPTCHA_TIMEOUT)
        if user.id in pending_users:
            pending_users.pop(user.id, None)

            # Логируем в консоль
            print(f"[BANNED] user_id={user.id} | username=@{user.username} | name={user.full_name} | chat_id={chat_id}")

            # Бан + анбан
            await bot.ban_chat_member(chat_id, user.id)
            await bot.unban_chat_member(chat_id, user.id)
            await msg.delete()

    asyncio.create_task(timeout())

# ==============================
# Обработка нажатий кнопок капчи
@dp.callback_query(F.data.startswith("captcha:"))
async def captcha_handler(callback):
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

        # Разрешаем пользователю писать
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

        # Удаляем капчу
        await callback.message.delete()
        await callback.answer("✅ Проверка пройдена")
    else:
        # Логируем неверный ответ
        print(f"[WRONG CAPTCHA] user_id={user_id} | username=@{callback.from_user.username} | name={callback.from_user.full_name} | chat_id={chat_id} | pressed={value} | correct={pending_users[user_id]}")
        await callback.answer("❌ Неверно", show_alert=True)

# ==============================
# Удаляем сообщения от пользователей, которые ещё не прошли капчу
@dp.message()
async def delete_messages_from_pending(message):
    chat_id = message.chat.id
    if chat_id not in ALLOWED_CHATS:
        return

    if message.from_user.id in pending_users:
        await message.delete()

# ==============================
# Старт polling
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
