import asyncio
import logging
import sys
from os import getenv
from dotenv import load_dotenv
from datetime import datetime, timedelta
from typing import Optional
from pathlib import Path
from uuid import uuid4

from aiogram import Bot, Dispatcher, html, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.types import (
    Message as MessageType,
    BusinessMessagesDeleted,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    FSInputFile
)
from aiogram.utils.media_group import MediaGroupBuilder

import db
from db.models.message import Message
from db.models.file import File
from sqlmodel import Session as SQLSession, select, SQLModel, Field

load_dotenv()

TOKEN = getenv("BOT_TOKEN")

# ================= НАСТРОЙКИ БОТА =================
ADMIN_ID = 123456789  # ЗАМЕНИТЕ НА СВОЙ TELEGRAM ID
CHANNEL_ID = "@your_channel_username" # ЗАМЕНИТЕ НА USERNAME КАНАЛА
CHANNEL_URL = "https://t.me/your_channel_username" # ССЫЛКА НА КАНАЛ
# ==================================================

# --- НАСТРОЙКА ДИРЕКТОРИЙ (ДЛЯ RAILWAY И ПК) ---
DATA_DIR = Path(getenv("DATA_DIR", "."))
MEDIA_DIR = DATA_DIR / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

dp = Dispatcher()

# --- НОВЫЕ МОДЕЛИ БАЗЫ ДАННЫХ ---
class User(SQLModel, table=True):
    id: int = Field(primary_key=True)
    is_premium: bool = Field(default=False)
    premium_until: Optional[datetime] = Field(default=None)
    invited_by: Optional[int] = Field(default=None)
    referral_count: int = Field(default=0)

class BotSettings(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    is_paid_mode: bool = Field(default=False)
    price_stars: int = Field(default=100)
    payment_card: str = Field(default="💳 4149 1234 5678 9000 (Monobank)")
    payment_method: str = Field(default="stars")

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_subscription_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться на канал", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="🔄 Проверить", callback_data="check_subscription")]
    ])

def check_premium_access(session: SQLSession, user_id: int, settings: BotSettings) -> bool:
    if not settings.is_paid_mode:
        return True
    
    user = session.get(User, user_id)
    if user and user.is_premium and user.premium_until:
        if user.premium_until > datetime.now():
            return True
        else:
            user.is_premium = False
            session.commit()
    return False

# --- ОБРАБОТЧИКИ КОМАНД ---
@dp.message(CommandStart())
async def command_start_handler(message: MessageType, command: CommandObject) -> None:
    user_id = message.from_user.id
    args = command.args
    
    with SQLSession(db.engine) as session:
        user = session.get(User, user_id)
        if not user:
            inviter_id = None
            if args and args.isdigit():
                inviter_id = int(args)
            
            user = User(id=user_id, invited_by=inviter_id)
            session.add(user)
            
            # Логика реферальной системы
            if inviter_id and inviter_id != user_id:
                inviter = session.get(User, inviter_id)
                if inviter:
                    inviter.referral_count += 1
                    if inviter.referral_count % 3 == 0:
                        extra_time = timedelta(days=14)
                        if inviter.premium_until and inviter.premium_until > datetime.now():
                            inviter.premium_until += extra_time
                        else:
                            inviter.is_premium = True
                            inviter.premium_until = datetime.now() + extra_time
                        
                        await message.bot.send_message(
                            inviter_id, 
                            "🎉 Поздравляем! За 3 приглашенных друзей вы получили 2 недели Premium! 🎁"
                        )
                    session.add(inviter)
            session.commit()

    text = (
        f"Привет, {html.bold(message.from_user.full_name)}!\n\n"
        f"Для использования бота и всех его функций, пожалуйста, подпишитесь на наш официальный канал."
    )
    await message.answer(text, reply_markup=get_subscription_keyboard())

@dp.callback_query(F.data == "check_subscription")
async def check_subscription_handler(callback: CallbackQuery, bot: Bot):
    user_id = callback.from_user.id
    try:
        chat_member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        valid_statuses = [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]
        
        if chat_member.status in valid_statuses:
            bot_info = await bot.get_me()
            bot_link = f"https://t.me/{bot_info.username}?start={user_id}"
            
            main_menu_text = (
                "🎉 <b>Спасибо за подписку!</b>\n\n"
                "🤖 <b>Главная страница Savemod Bot</b>\n\n"
                "Я — ваш надежный бизнес-помощник для сохранения данных. Вот что я умею:\n\n"
                "🗑 <b>Восстановление удаленного:</b> Сохраняю сообщения, которые удалил собеседник.\n"
                "✏️ <b>История изменений:</b> Показываю старую и новую версию измененных сообщений.\n"
                "🔥 <b>Самоуничтожающиеся фото:</b> Перехватываю фото с таймером (просто сделайте reply).\n"
                "📁 <b>Все форматы:</b> Поддерживаю текст, фото, видео, кружочки, ГС и документы.\n\n"
                f"🎁 Ваша реферальная ссылка: <code>{bot_link}</code>\n"
                f"<i>Пригласите 3 друзей и получите 2 недели Premium бесплатно!</i>"
            )
            await callback.message.edit_text(main_menu_text)
        else:
            await callback.answer("❌ Вы еще не подписались на канал!", show_alert=True)
    except Exception as e:
        logging.error(f"Ошибка проверки подписки: {e}")
        await callback.answer("⚠️ Ошибка при проверке. Убедитесь, что бот является администратором канала.", show_alert=True)

# --- АДМИН ПАНЕЛЬ ---
@dp.message(Command("give_premium"))
async def give_premium(message: MessageType, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    try:
        args = command.args.split()
        target_id = int(args[0])
        days = int(args[1]) if len(args) > 1 else 30
        
        with SQLSession(db.engine) as session:
            user = session.get(User, target_id)
            if not user:
                user = User(id=target_id)
            user.is_premium = True
            user.premium_until = datetime.now() + timedelta(days=days)
            session.add(user)
            session.commit()
            
        await message.answer(f"✅ Пользователю {target_id} выдан Premium на {days} дней.")
        await message.bot.send_message(target_id, f"🎉 Администратор выдал вам Premium доступ на {days} дней!")
    except Exception:
        await message.answer("❌ Использование: /give_premium <ID пользователя> <Кол-во дней>")

@dp.message(Command("admin"))
async def admin_panel(message: MessageType):
    if message.from_user.id != ADMIN_ID: return
    
    with SQLSession(db.engine) as session:
        settings = session.get(BotSettings, 1)
        if not settings:
            settings = BotSettings(id=1)
            session.add(settings)
            session.commit()
            
    mode_text = "🔴 Платный" if settings.is_paid_mode else "🟢 Бесплатный"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Режим бота: {mode_text}", callback_data="toggle_mode")],
        [InlineKeyboardButton(text="Настроить оплату", callback_data="setup_payments")]
    ])
    await message.answer("🛠 <b>Админ Панель</b>", reply_markup=keyboard)

@dp.callback_query(F.data == "toggle_mode")
async def toggle_bot_mode(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    
    with SQLSession(db.engine) as session:
        settings = session.get(BotSettings, 1)
        settings.is_paid_mode = not settings.is_paid_mode
        session.add(settings)
        session.commit()
        
        mode_text = "🔴 Платный" if settings.is_paid_mode else "🟢 Бесплатный"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"Режим бота: {mode_text}", callback_data="toggle_mode")],
            [InlineKeyboardButton(text="Настроить оплату", callback_data="setup_payments")]
        ])
        
    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer(f"Режим изменен на {mode_text}")

@dp.callback_query(F.data == "buy_premium")
async def buy_premium_handler(callback: CallbackQuery):
    await callback.answer("Метод оплаты находится в разработке.", show_alert=True)

# --- БИЗНЕС ЛОГИКА ---
@dp.edited_business_message()
async def handle_edited_business_message(message: MessageType):
    with SQLSession(db.engine) as session:
        business_connection = await message.bot.get_business_connection(message.business_connection_id)
        user_chat_id = business_connection.user_chat_id

        settings = session.get(BotSettings, 1) or BotSettings(id=1)
        
        # Проверка премиума
        if not check_premium_access(session, user_chat_id, settings):
            buy_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💎 Приобрести Premium", callback_data="buy_premium")]
            ])
            await message.bot.send_message(
                chat_id=user_chat_id, 
                text="🔒 <b>Собеседник изменил сообщение!</b>\n\n"
                     "Бот сейчас работает в платном режиме. Чтобы увидеть старую версию сообщения, "
                     "приобретите Premium подписку или пригласите 3 друзей.",
                reply_markup=buy_keyboard
            )
            return

        old_msg = session.exec(
            select(Message)
            .where(Message.chat_id == message.chat.id)
            .where(Message.id == message.message_id)
        ).first()

        if not old_msg: return

        new_content = message.text or message.caption or ""
        old_content = old_msg.content or ""

        if old_content != new_content:
            sender_link = f"<a href='tg://user?id={message.chat.id}'>{old_msg.from_username}</a>"
            
            alert_text = (
                f"✏️ <b>{sender_link} изменил сообщение</b>\n\n"
                f"🔴 <b>Было:</b>\n"
                f"<blockquote>{old_content if old_content else '<i>Без текста</i>'}</blockquote>\n"
                f"🟢 <b>Стало:</b>\n"
                f"<blockquote>{new_content if new_content else '<i>Текст удален</i>'}</blockquote>"
            )
            
            await message.bot.send_message(chat_id=user_chat_id, text=alert_text)
            
            old_msg.content = new_content
            session.add(old_msg)
            session.commit()

@dp.deleted_business_messages()
async def handle_business_message_deleted(deleted_messages: BusinessMessagesDeleted):
    with SQLSession(db.engine) as session:
        business_connection = await deleted_messages.bot.get_business_connection(deleted_messages.business_connection_id)
        user_chat_id = business_connection.user_chat_id

        settings = session.get(BotSettings, 1) or BotSettings(id=1)
        
        # Проверка премиума
        if not check_premium_access(session, user_chat_id, settings):
            buy_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💎 Приобрести Premium", callback_data="buy_premium")]
            ])
            await deleted_messages.bot.send_message(
                chat_id=user_chat_id, 
                text="🔒 <b>Собеседник удалил сообщение!</b>\n\n"
                     "Бот сейчас работает в платном режиме. Чтобы увидеть содержимое, "
                     "приобретите Premium подписку или пригласите 3 друзей.",
                reply_markup=buy_keyboard
            )
            return

        for message_id in deleted_messages.message_ids:
            msg = session.exec(
                select(Message)
                .where(Message.chat_id == deleted_messages.chat.id)
                .where(Message.id == message_id)
            ).first()

            if not msg: continue

            sender_link = f"<a href='tg://user?id={deleted_messages.chat.id}'>{msg.from_username}</a>"

            if msg.type == "photos":
                files = session.exec(select(File).where(File.message_id == msg.id)).fetchall()
                text = (
                    f"🖼 <b>{sender_link} удалил фото</b>\n\n"
                    f"<b>Описание:</b>\n<blockquote>{msg.content if msg.content else '<i>Без описания</i>'}</blockquote>"
                )
                media_group = MediaGroupBuilder(caption=text)
                for file_name in files:
                    file_path = MEDIA_DIR.joinpath(file_name.file_name)
                    media_group.add(type="photo", media=FSInputFile(file_path))
                await deleted_messages.bot.send_media_group(chat_id=user_chat_id, media=media_group.build())
                    
            elif msg.type == "video":
                fileDb = session.exec(select(File).where(File.message_id == msg.id)).first()
                text = (
                    f"🎥 <b>{sender_link} удалил видео</b>\n\n"
                    f"<b>Описание:</b>\n<blockquote>{msg.content if msg.content else '<i>Без описания</i>'}</blockquote>"
                )
                file_path = MEDIA_DIR.joinpath(fileDb.file_name)
                await deleted_messages.bot.send_video(chat_id=user_chat_id, video=FSInputFile(file_path), caption=text)
            
            elif msg.type == "video_note":
                fileDb = session.exec(select(File).where(File.message_id == msg.id)).first()
                text = f"📹 <b>{sender_link} удалил кружочек ⬆️</b>"
                file_path = MEDIA_DIR.joinpath(fileDb.file_name)
                await deleted_messages.bot.send_video_note(chat_id=user_chat_id, video_note=FSInputFile(file_path))
                await deleted_messages.bot.send_message(chat_id=user_chat_id, text=text)

            elif msg.type == "audio":
                fileDb = session.exec(select(File).where(File.message_id == msg.id)).first()
                text = f"🎤 <b>{sender_link} удалил голосовое сообщение</b>"
                file_path = MEDIA_DIR.joinpath(fileDb.file_name)
                await deleted_messages.bot.send_audio(chat_id=user_chat_id, audio=FSInputFile(file_path), caption=text)
            
            elif msg.type == "document":
                fileDb = session.exec(select(File).where(File.message_id == msg.id)).first()
                text = (
                    f"📁 <b>{sender_link} удалил файл</b>\n\n"
                    f"<b>Описание:</b>\n<blockquote>{msg.content if msg.content else '<i>Без описания</i>'}</blockquote>"
                )
                file_path = MEDIA_DIR.joinpath(fileDb.file_name)
                await deleted_messages.bot.send_document(chat_id=user_chat_id, document=FSInputFile(file_path), caption=text)
            
            elif msg.type == "text":
                text = (
                    f"🗑 <b>{sender_link} удалил сообщение</b>\n\n"
                    f"📝 <b>Текст:</b>\n<blockquote>{msg.content}</blockquote>"
                )
                await deleted_messages.bot.send_message(chat_id=user_chat_id, text=text)

@dp.business_message()
async def handle_business_message(message: MessageType):
    with SQLSession(db.engine) as session:
        business_connection = await message.bot.get_business_connection(message.business_connection_id)
        user_chat_id = business_connection.user_chat_id

        if message.reply_to_message:
            reply_to = message.reply_to_message

            if reply_to.photo:
                file_name = f"{uuid4()}.jpg"
                file_path = MEDIA_DIR.joinpath(file_name)
                fl = await message.bot.get_file(reply_to.photo[-1].file_id)
                await message.bot.download_file(fl.file_path, file_path)
                await message.bot.send_photo(chat_id=user_chat_id, photo=FSInputFile(file_path))
                Path.unlink(file_path)
            
            elif reply_to.video:
                file_name = f"{uuid4()}.mp4"
                file_path = MEDIA_DIR.joinpath(file_name)
                fl = await message.bot.get_file(reply_to.video.file_id)
                await message.bot.download_file(fl.file_path, file_path)
                await message.bot.send_video(chat_id=user_chat_id, video=FSInputFile(file_path))
                Path.unlink(file_path)
            
            elif reply_to.video_note:
                file_name = f"{uuid4()}.mp4"
                file_path = MEDIA_DIR.joinpath(file_name)
                fl = await message.bot.get_file(reply_to.video_note.file_id)
                await message.bot.download_file(fl.file_path, file_path)
                await message.bot.send_video_note(chat_id=user_chat_id, video_note=FSInputFile(file_path))
                Path.unlink(file_path)
            
            elif reply_to.voice:
                file_name = f"{uuid4()}.ogg"
                file_path = MEDIA_DIR.joinpath(file_name)
                fl = await message.bot.get_file(reply_to.voice.file_id)
                await message.bot.download_file(fl.file_path, file_path)
                await message.bot.send_audio(chat_id=user_chat_id, audio=FSInputFile(file_path))
                Path.unlink(file_path)

        elif message.photo:
            msg = Message(chat_id=message.chat.id, id=message.message_id, type="photos", content=message.caption if message.caption else "", from_username=message.from_user.username if message.from_user.username else "Скрыт")
            session.add(msg)
            file_name = f"{uuid4()}.jpg"
            fl = await message.bot.get_file(message.photo[-1].file_id)
            await message.bot.download_file(fl.file_path, MEDIA_DIR.joinpath(file_name))
            session.add(File(file_name=file_name, message_id=message.message_id))
            session.commit()

        elif message.video:
            msg = Message(chat_id=message.chat.id, id=message.message_id, type="video", content=message.caption if message.caption else "", from_username=message.from_user.username if message.from_user.username else "Скрыт")
            session.add(msg)
            file_name = f"{uuid4()}.mp4"
            fl = await message.bot.get_file(message.video.file_id)
            await message.bot.download_file(fl.file_path, MEDIA_DIR.joinpath(file_name))
            session.add(File(file_name=file_name, message_id=message.message_id))
            session.commit()

        elif message.video_note:
            msg = Message(chat_id=message.chat.id, id=message.message_id, type="video_note", content=message.caption if message.caption else "", from_username=message.from_user.username if message.from_user.username else "Скрыт")
            session.add(msg)
            file_name = f"{uuid4()}.mp4"
            fl = await message.bot.get_file(message.video_note.file_id)
            await message.bot.download_file(fl.file_path, MEDIA_DIR.joinpath(file_name))
            session.add(File(file_name=file_name, message_id=message.message_id))
            session.commit()

        elif message.voice:
            msg = Message(chat_id=message.chat.id, id=message.message_id, type="audio", content=message.caption if message.caption else "", from_username=message.from_user.username if message.from_user.username else "Скрыт")
            session.add(msg)
            file_name = f"{uuid4()}.ogg"
            fl = await message.bot.get_file(message.voice.file_id)
            await message.bot.download_file(fl.file_path, MEDIA_DIR.joinpath(file_name))
            session.add(File(file_name=file_name, message_id=message.message_id))
            session.commit()

        elif message.document:
            msg = Message(chat_id=message.chat.id, id=message.message_id, type="document", content=message.caption if message.caption else "", from_username=message.from_user.username if message.from_user.username else "Скрыт")
            session.add(msg)
            ext = message.document.mime_type.split('/')[1] if message.document.mime_type else "bin"
            file_name = f"{uuid4()}.{ext}"
            fl = await message.bot.get_file(message.document.file_id)
            await message.bot.download_file(fl.file_path, MEDIA_DIR.joinpath(file_name))
            session.add(File(file_name=file_name, message_id=message.message_id))
            session.commit()

        else:
            msg = Message(chat_id=message.chat.id, id=message.message_id, type="text", content=message.text, from_username=message.from_user.username if message.from_user.username else "Скрыт")
            session.add(msg)
            session.commit()

async def main() -> None:
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    db.init()
    asyncio.run(main())
