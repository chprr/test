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
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
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
ADMIN_ID = 8698190793  # ЗАМЕНИТЕ НА СВОЙ TELEGRAM ID
CHANNEL_ID = "@chprrshop" # ЗАМЕНИТЕ НА USERNAME КАНАЛА
CHANNEL_URL = "https://t.me/chprrshop" # ССЫЛКА НА КАНАЛ
# ==================================================

DATA_DIR = Path(getenv("DATA_DIR", "."))
MEDIA_DIR = DATA_DIR / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

dp = Dispatcher()

# --- СОСТОЯНИЯ ДЛЯ АДМИН-ПАНЕЛИ (FSM) ---
class AdminStates(StatesGroup):
    waiting_for_price = State()
    waiting_for_card = State()

# --- МОДЕЛИ БАЗЫ ДАННЫХ ---
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
    payment_method: str = Field(default="stars")  # 'stars' или 'card'

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_subscription_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться на канал", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="🔄 Проверить подписку", callback_data="check_subscription")]
    ])

def get_main_menu_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Реферальная система", callback_data="view_referrals")],
        [InlineKeyboardButton(text="💎 Купить Premium", callback_data="buy_premium")]
    ])

async def is_user_subscribed(bot: Bot, user_id: int) -> bool:
    try:
        chat_member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        valid_statuses = [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]
        return chat_member.status in valid_statuses
    except Exception as e:
        logging.error(f"Ошибка проверки подписки: {e}")
        return False

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

def get_main_menu_text(user_id: int):
    with SQLSession(db.engine) as session:
        user = session.get(User, user_id)
    
    if user and user.is_premium and user.premium_until and user.premium_until > datetime.now():
        status = f"👑 <b>Premium</b> (активен до {user.premium_until.strftime('%d.%m.%Y %H:%M')})"
    else:
        status = "❌ <b>Бесплатная (Ограниченная)</b>"

    return (
        "🤖 <b>Главная страница Savemod Bot</b>\n\n"
        f"📊 <b>Ваш статус подписки:</b> {status}\n\n"
        "Я — ваш надежный бизнес-помощник для сохранения данных. Вот что я умею:\n\n"
        "🗑 <b>Восстановление удаленного:</b> Сохраняю сообщения, которые удалил собеседник.\n"
        "✏️ <b>История изменений:</b> Показываю старую и новую версию измененных сообщений.\n"
        "🔥 <b>Самоуничтожающиеся фото:</b> Перехватываю фото с таймером (просто сделайте reply).\n"
        "📁 <b>Все форматы:</b> Поддерживаю текст, фото, видео, кружочки, ГС и документы."
    )

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

    if await is_user_subscribed(message.bot, user_id):
        await message.answer(get_main_menu_text(user_id), reply_markup=get_main_menu_keyboard())
    else:
        text = (
            f"Привет, {html.bold(message.from_user.full_name)}!\n\n"
            f"Для использования бота и всех его функций, пожалуйста, подпишитесь на наш официальный канал."
        )
        await message.answer(text, reply_markup=get_subscription_keyboard())

@dp.callback_query(F.data == "check_subscription")
async def check_subscription_handler(callback: CallbackQuery, bot: Bot):
    user_id = callback.from_user.id
    if await is_user_subscribed(bot, user_id):
        await callback.message.edit_text(get_main_menu_text(user_id), reply_markup=get_main_menu_keyboard())
    else:
        await callback.answer("❌ Вы еще не подписались на канал!", show_alert=True)

@dp.callback_query(F.data == "view_referrals")
async def view_referrals_handler(callback: CallbackQuery, bot: Bot):
    user_id = callback.from_user.id
    bot_info = await bot.get_me()
    bot_link = f"https://t.me/{bot_info.username}?start={user_id}"
    
    with SQLSession(db.engine) as session:
        user = session.get(User, user_id)
        ref_count = user.referral_count if user else 0
        
    ref_text = (
        "👥 <b>Реферальная система Savemod Bot</b>\n\n"
        f"🎁 Ваша реферальная ссылка: <code>{bot_link}</code>\n\n"
        f"📊 Всего приглашено друзей: <b>{ref_count}</b>\n"
        "<i>Пригласите 3 друзей и получите 2 недели Premium бесплатно!</i>"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_menu")]
    ])
    await callback.message.edit_text(ref_text, reply_markup=keyboard)

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu_handler(callback: CallbackQuery):
    await callback.message.edit_text(get_main_menu_text(callback.from_user.id), reply_markup=get_main_menu_keyboard())

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
        [InlineKeyboardButton(text="⚙️ Настроить оплату", callback_data="setup_payments")]
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
            [InlineKeyboardButton(text="⚙️ Настроить оплату", callback_data="setup_payments")]
        ])
        
    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer(f"Режим изменен на {mode_text}")

@dp.callback_query(F.data == "setup_payments")
async def setup_payments_handler(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    
    with SQLSession(db.engine) as session:
        settings = session.get(BotSettings, 1) or BotSettings(id=1)
    
    current_method = "⭐ Звезды Telegram" if settings.payment_method == "stars" else "💳 Банковская карта"
    
    text = (
        "⚙️ <b>Настройка платежных методов</b>\n\n"
        f"Текущий способ оплаты: <b>{current_method}</b>\n"
        f"Цена в звездах: <code>{settings.price_stars} Stars</code>\n"
        f"Реквизиты карты: <code>{settings.payment_card}</code>\n\n"
        "Нажмите кнопки ниже, чтобы изменить параметры прямо в чате."
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Метод: Звезды", callback_data="set_method_stars"),
         InlineKeyboardButton(text="💳 Метод: Карта", callback_data="set_method_card")],
        [InlineKeyboardButton(text="💰 Изменить цену", callback_data="edit_price"),
         InlineKeyboardButton(text="📝 Изменить реквизиты", callback_data="edit_card")],
        [InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="back_to_admin")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard)

# --- ИЗМЕНЕНИЕ ПАРАМЕТРОВ ОПЛАТЫ ЧЕРЕЗ БОТА (FSM) ---
@dp.callback_query(F.data == "edit_price")
async def edit_price_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await callback.message.answer("💰 <b>Введите новую цену подписки в Звездах (только число):</b>")
    await state.set_state(AdminStates.waiting_for_price)
    await callback.answer()

@dp.message(AdminStates.waiting_for_price)
async def process_new_price(message: MessageType, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    if not message.text.isdigit():
        await message.answer("❌ Ошибка! Пожалуйста, введите корректное число (например: 150).")
        return
    
    new_price = int(message.text)
    with SQLSession(db.engine) as session:
        settings = session.get(BotSettings, 1) or BotSettings(id=1)
        settings.price_stars = new_price
        session.add(settings)
        session.commit()
        
    await message.answer(f"✅ Цена успешно изменена на <b>{new_price} Stars</b>!")
    await state.clear()
    await admin_panel(message)

@dp.callback_query(F.data == "edit_card")
async def edit_card_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await callback.message.answer("💳 <b>Введите новые реквизиты карты (одним сообщением):</b>")
    await state.set_state(AdminStates.waiting_for_card)
    await callback.answer()

@dp.message(AdminStates.waiting_for_card)
async def process_new_card(message: MessageType, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    new_card = message.text
    with SQLSession(db.engine) as session:
        settings = session.get(BotSettings, 1) or BotSettings(id=1)
        settings.payment_card = new_card
        session.add(settings)
        session.commit()
        
    await message.answer("✅ Реквизиты карты успешно обновлены!")
    await state.clear()
    await admin_panel(message)

@dp.callback_query(F.data.startswith("set_method_"))
async def set_payment_method(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    method = callback.data.split("_")[2]
    
    with SQLSession(db.engine) as session:
        settings = session.get(BotSettings, 1)
        settings.payment_method = method
        session.add(settings)
        session.commit()
        
    await callback.answer(f"Способ оплаты изменен на: {method}")
    await setup_payments_handler(callback)

@dp.callback_query(F.data == "back_to_admin")
async def back_to_admin_handler(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    await callback.message.delete()
    await admin_panel(callback.message)

@dp.callback_query(F.data == "buy_premium")
async def buy_premium_handler(callback: CallbackQuery):
    with SQLSession(db.engine) as session:
        settings = session.get(BotSettings, 1) or BotSettings(id=1)
        
    if settings.payment_method == "stars":
        await callback.message.answer(f"🤖 <b>Оплата Premium (Stars)</b>\n\nДля оплаты переведите {settings.price_stars} Stars (XTR-платежи в интеграции).")
    else:
        await callback.message.answer(f"🤖 <b>Оплата Premium (Карта)</b>\n\nПереведите сумму на указанные реквизиты:\n<code>{settings.payment_card}</code>\n\nПосле оплаты отправьте чек администратору.")
    await callback.answer()

# --- БИЗНЕС ЛОГИКА ---
@dp.edited_business_message()
async def handle_edited_business_message(message: MessageType):
    with SQLSession(db.engine) as session:
        business_connection = await message.bot.get_business_connection(message.business_connection_id)
        user_chat_id = business_connection.user_chat_id
        settings = session.get(BotSettings, 1) or BotSettings(id=1)
        
        old_msg = session.exec(
            select(Message).where(Message.chat_id == message.chat.id).where(Message.id == message.message_id)
        ).first()
        username = old_msg.from_username if old_msg else (message.chat.username or message.chat.first_name or "Пользователь")
        sender_link = f"<a href='tg://user?id={message.chat.id}'>{username}</a>"

        # Изменение: Если подписка бесплатная, уведомляем КТО изменил, но скрываем ЧТО именно
        if not check_premium_access(session, user_chat_id, settings):
            buy_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💎 Приобрести Premium", callback_data="buy_premium")]
            ])
            await message.bot.send_message(
                chat_id=user_chat_id, 
                text=f"✏️ <b>{sender_link} изменил сообщение!</b>\n\n"
                     f"🔒 <b>Содержимое скрыто.</b> Для просмотра старой и новой версии истории изменений, "
                     f"приобретите Premium подписку или пригласите 3 друзей.",
                reply_markup=buy_keyboard
            )
            return

        if not old_msg: return
        new_content = message.text or message.caption or ""
        old_content = old_msg.content or ""

        if old_content != new_content:
            # Кружочки убраны
            alert_text = (
                f"✏️ <b>{sender_link} изменил сообщение</b>\n\n"
                f"<b>Было:</b>\n"
                f"<blockquote>{old_content if old_content else '<i>Без текста</i>'}</blockquote>\n"
                f"<b>Стало:</b>\n"
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
        
        # Изменение: Если подписка бесплатная, уведомляем КТО удалил, скрывая контент
        if not check_premium_access(session, user_chat_id, settings):
            buy_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💎 Приобрести Premium", callback_data="buy_premium")]
            ])
            for message_id in deleted_messages.message_ids:
                msg = session.exec(
                    select(Message).where(Message.chat_id == deleted_messages.chat.id).where(Message.id == message_id)
                ).first()
                username = msg.from_username if msg else "Пользователь"
                sender_link = f"<a href='tg://user?id={deleted_messages.chat.id}'>{username}</a>"
                
                await deleted_messages.bot.send_message(
                    chat_id=user_chat_id, 
                    text=f"🗑 <b>{sender_link} удалил сообщение!</b>\n\n"
                         f"🔒 <b>Содержимое скрыто.</b> Чтобы восстановить удаленный текст или медиафайл, "
                         f"приобретите Premium подписку.",
                    reply_markup=buy_keyboard
                )
            return

        for message_id in deleted_messages.message_ids:
            msg = session.exec(
                select(Message).where(Message.chat_id == deleted_messages.chat.id).where(Message.id == message_id)
            ).first()

            if not msg: continue
            sender_link = f"<a href='tg://user?id={deleted_messages.chat.id}'>{msg.from_username}</a>"

            if msg.type == "photos":
                files = session.exec(select(File).where(File.message_id == msg.id)).fetchall()
                text = f"🖼 <b>{sender_link} удалил фото</b>\n\n<b>Описание:</b>\n<blockquote>{msg.content if msg.content else '<i>Без описания</i>'}</blockquote>"
                media_group = MediaGroupBuilder(caption=text)
                for file_name in files:
                    media_group.add(type="photo", media=FSInputFile(MEDIA_DIR / file_name.file_name))
                await deleted_messages.bot.send_media_group(chat_id=user_chat_id, media=media_group.build())
            elif msg.type == "video":
                fileDb = session.exec(select(File).where(File.message_id == msg.id)).first()
                text = f"🎥 <b>{sender_link} удалил видео</b>\n\n<b>Описание:</b>\n<blockquote>{msg.content if msg.content else '<i>Без описания</i>'}</blockquote>"
                await deleted_messages.bot.send_video(chat_id=user_chat_id, video=FSInputFile(MEDIA_DIR / fileDb.file_name), caption=text)
            elif msg.type == "video_note":
                fileDb = session.exec(select(File).where(File.message_id == msg.id)).first()
                await deleted_messages.bot.send_video_note(chat_id=user_chat_id, video_note=FSInputFile(MEDIA_DIR / fileDb.file_name))
            elif msg.type == "audio":
                fileDb = session.exec(select(File).where(File.message_id == msg.id)).first()
                await deleted_messages.bot.send_audio(chat_id=user_chat_id, audio=FSInputFile(MEDIA_DIR / fileDb.file_name))
            elif msg.type == "document":
                fileDb = session.exec(select(File).where(File.message_id == msg.id)).first()
                await deleted_messages.bot.send_document(chat_id=user_chat_id, document=FSInputFile(MEDIA_DIR / fileDb.file_name))
            elif msg.type == "text":
                text = f"🗑 <b>{sender_link} удалил сообщение</b>\n\n📝 <b>Текст:</b>\n<blockquote>{msg.content}</blockquote>"
                await deleted_messages.bot.send_message(chat_id=user_chat_id, text=text)
@dp.business_message()
async def handle_business_message(message: MessageType):
    with SQLSession(db.engine) as session:
        business_connection = await message.bot.get_business_connection(message.business_connection_id)
        user_chat_id = business_connection.user_chat_id

        # --- МОДУЛЬ СОХРАНЕНИЯ МЕДИА ПО РЕПЛАЮ (ВОССТАНОВЛЕН И ИСПРАВЛЕН) ---
        # Бот сохраняет всё медиа по реплаю, так как API Telegram не отличает 
        # обычные фото от сгорающих.
        if message.reply_to_message:
            reply = message.reply_to_message
            
            try:
                if reply.photo:
                    file_name = f"{uuid4()}.jpg"
                    file_path = MEDIA_DIR / file_name
                    fl = await message.bot.get_file(reply.photo[-1].file_id)
                    await message.bot.download_file(fl.file_path, file_path)
                    await message.bot.send_photo(chat_id=user_chat_id, photo=FSInputFile(file_path), caption="🔥 Сохраненное фото")
                    Path.unlink(file_path)
                
                elif reply.video:
                    file_name = f"{uuid4()}.mp4"
                    file_path = MEDIA_DIR / file_name
                    fl = await message.bot.get_file(reply.video.file_id)
                    await message.bot.download_file(fl.file_path, file_path)
                    await message.bot.send_video(chat_id=user_chat_id, video=FSInputFile(file_path), caption="🔥 Сохраненное видео")
                    Path.unlink(file_path)
                
                elif reply.video_note:
                    file_name = f"{uuid4()}.mp4"
                    file_path = MEDIA_DIR / file_name
                    fl = await message.bot.get_file(reply.video_note.file_id)
                    await message.bot.download_file(fl.file_path, file_path)
                    await message.bot.send_video_note(chat_id=user_chat_id, video_note=FSInputFile(file_path))
                    Path.unlink(file_path)
                
                elif reply.voice:
                    file_name = f"{uuid4()}.ogg"
                    file_path = MEDIA_DIR / file_name
                    fl = await message.bot.get_file(reply.voice.file_id)
                    await message.bot.download_file(fl.file_path, file_path)
                    await message.bot.send_audio(chat_id=user_chat_id, audio=FSInputFile(file_path), caption="🔥 Сохраненное ГС")
                    Path.unlink(file_path)
            except Exception as e:
                logging.error(f"Ошибка при сохранении медиа через реплай: {e}")

        # --- ФОНОВОЕ СОХРАНЕНИЕ ДЛЯ УДАЛЕННЫХ/ИЗМЕНЕННЫХ СООБЩЕНИЙ ---
        elif message.photo:
            msg = Message(chat_id=message.chat.id, id=message.message_id, type="photos", content=message.caption or "", from_username=message.from_user.username or "Скрыт")
            session.add(msg)
            file_name = f"{uuid4()}.jpg"
            fl = await message.bot.get_file(message.photo[-1].file_id)
            await message.bot.download_file(fl.file_path, MEDIA_DIR / file_name)
            session.add(File(file_name=file_name, message_id=message.message_id))
            session.commit()
            
        elif message.video:
            msg = Message(chat_id=message.chat.id, id=message.message_id, type="video", content=message.caption or "", from_username=message.from_user.username or "Скрыт")
            session.add(msg)
            file_name = f"{uuid4()}.mp4"
            fl = await message.bot.get_file(message.video.file_id)
            await message.bot.download_file(fl.file_path, MEDIA_DIR / file_name)
            session.add(File(file_name=file_name, message_id=message.message_id))
            session.commit()
            
        elif message.video_note:
            msg = Message(chat_id=message.chat.id, id=message.message_id, type="video_note", content="", from_username=message.from_user.username or "Скрыт")
            session.add(msg)
            file_name = f"{uuid4()}.mp4"
            fl = await message.bot.get_file(message.video_note.file_id)
            await message.bot.download_file(fl.file_path, MEDIA_DIR / file_name)
            session.add(File(file_name=file_name, message_id=message.message_id))
            session.commit()
            
        elif message.voice:
            msg = Message(chat_id=message.chat.id, id=message.message_id, type="audio", content="", from_username=message.from_user.username or "Скрыт")
            session.add(msg)
            file_name = f"{uuid4()}.ogg"
            fl = await message.bot.get_file(message.voice.file_id)
            await message.bot.download_file(fl.file_path, MEDIA_DIR / file_name)
            session.add(File(file_name=file_name, message_id=message.message_id))
            session.commit()
            
        elif message.document:
            msg = Message(chat_id=message.chat.id, id=message.message_id, type="document", content=message.caption or "", from_username=message.from_user.username or "Скрыт")
            session.add(msg)
            ext = message.document.mime_type.split('/')[1] if message.document.mime_type else "bin"
            file_name = f"{uuid4()}.{ext}"
            fl = await message.bot.get_file(message.document.file_id)
            await message.bot.download_file(fl.file_path, MEDIA_DIR / file_name)
            session.add(File(file_name=file_name, message_id=message.message_id))
            session.commit()
            
        else:
            msg = Message(chat_id=message.chat.id, id=message.message_id, type="text", content=message.text, from_username=message.from_user.username or "Скрыт")
            session.add(msg)
            session.commit()

async def main() -> None:
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    db.init()
    asyncio.run(main())
