import asyncio
import logging
import sys
from os import getenv
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, html
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message as MessageType
from aiogram.types import BusinessMessagesDeleted
import db
from db.models.message import Message
from db.models.file import File
from sqlmodel import Session as SQLSession
from sqlmodel import select
from pathlib import Path
from uuid import uuid4
from aiogram.utils.media_group import MediaGroupBuilder
from aiogram.types import FSInputFile

load_dotenv()

TOKEN = getenv("BOT_TOKEN")

dp = Dispatcher()

# ТЕПЕРЬ РАБОТАЕТ: Изменен тип на MessageType, чтобы не конфликтовать с моделью БД
@dp.message(CommandStart())
async def command_start_handler(message: MessageType) -> None:
    await message.answer(f"Hello, {html.bold(message.from_user.full_name)}! Бизнес-бот запущен и готов к работе.")


@dp.deleted_business_messages()
async def handle_business_message_deleted(deleted_messages: BusinessMessagesDeleted):
    print(f"Messages deleted in business connection {deleted_messages.business_connection_id}")
    
    # Используем 'with', чтобы сессия базы данных гарантированно закрывалась
    with SQLSession(db.engine) as session:
        business_connection = await deleted_messages.bot.get_business_connection(deleted_messages.business_connection_id)
        user_chat_id = business_connection.user_chat_id

        for message_id in deleted_messages.message_ids:
            msg = session.exec(
                select(Message)
                .where(Message.chat_id == deleted_messages.chat.id)
                .where(Message.id == message_id)
            ).first()

            if not msg:
                continue

            if msg.type == "photos":
                files = session.exec(select(File).where(File.message_id == msg.id)).fetchall()
                
                text = [
                    "<b>Удаленные фото",
                    f"Удалил @{msg.from_username}",
                    "",
                    "Описание:</b>",
                    msg.content
                ]
                text = '\n'.join(text)

                media_group = MediaGroupBuilder(caption=text)

                for file_name in files:
                    file_path = Path('.').joinpath("media").joinpath(file_name.file_name)
                    file = FSInputFile(file_path)
                    media_group.add(type="photo", media=file)

                await deleted_messages.bot.send_media_group(chat_id=user_chat_id, media=media_group.build())
                    
            elif msg.type == "video":
                fileDb = session.exec(select(File).where(File.message_id == msg.id)).first()
                
                text = [
                    "<b>Удаленное видео",
                    f"Удалил @{msg.from_username}",
                    "",
                    "Описание:</b>",
                    msg.content
                ]
                text = '\n'.join(text)

                file_path = Path('.').joinpath("media").joinpath(fileDb.file_name)
                file = FSInputFile(file_path)
                await deleted_messages.bot.send_video(chat_id=user_chat_id, video=file, caption=text)
            
            elif msg.type == "video_note":
                fileDb = session.exec(select(File).where(File.message_id == msg.id)).first()
                
                text = [
                    "<b>Удаленный кружочек ⬆️</b>",
                    f"<b>Удалил @{msg.from_username}</b>",
                ]
                text = '\n'.join(text)

                file_path = Path('.').joinpath("media").joinpath(fileDb.file_name)
                file = FSInputFile(file_path)
                
                await deleted_messages.bot.send_video_note(chat_id=user_chat_id, video_note=file)
                await deleted_messages.bot.send_message(chat_id=user_chat_id, text=text)

            elif msg.type == "audio":
                fileDb = session.exec(select(File).where(File.message_id == msg.id)).first()
                
                text = [
                    "<b>Удаленное гс</b>",
                    f"<b>Удалил @{msg.from_username}</b>",
                ]
                text = '\n'.join(text)

                file_path = Path('.').joinpath("media").joinpath(fileDb.file_name)
                file = FSInputFile(file_path)
                await deleted_messages.bot.send_audio(chat_id=user_chat_id, audio=file, caption=text)
            
            elif msg.type == "document":
                fileDb = session.exec(select(File).where(File.message_id == msg.id)).first()
                
                text = [
                    "<b>Удаленный файл",
                    f"Удалил @{msg.from_username}",
                    "",
                    "Описание:</b>",
                    msg.content
                ]
                text = '\n'.join(text)

                file_path = Path('.').joinpath("media").joinpath(fileDb.file_name)
                file = FSInputFile(file_path)
                await deleted_messages.bot.send_document(chat_id=user_chat_id, document=file, caption=text)
            
            elif msg.type == "text":
                text = [
                    "<b>Удаленное сообщение",
                    f"Удалил @{msg.from_username}",
                    "",
                    "Сообщение:</b>",
                    msg.content
                ]
                text = '\n'.join(text)

                await deleted_messages.bot.send_message(chat_id=user_chat_id, text=text)


@dp.business_message()
async def handle_business_message(message: MessageType):
    with SQLSession(db.engine) as session:
        business_connection = await message.bot.get_business_connection(message.business_connection_id)
        user_chat_id = business_connection.user_chat_id

        # Обработка реплаев на медиафайлы
        if message.reply_to_message:
            reply_to = message.reply_to_message

            if reply_to.photo:
                file_name = f"{uuid4()}.jpg"
                file_path = Path('.').joinpath("media").joinpath(file_name)
                photo = reply_to.photo[-1] # Берем только максимальное качество
                fl = await message.bot.get_file(photo.file_id)

                await message.bot.download_file(fl.file_path, file_path)
                await message.bot.send_photo(chat_id=user_chat_id, photo=FSInputFile(file_path))
                Path.unlink(file_path)
            
            elif reply_to.video:
                file_name = f"{uuid4()}.mp4"
                file_path = Path('.').joinpath("media").joinpath(file_name)
                fl = await message.bot.get_file(reply_to.video.file_id)

                await message.bot.download_file(fl.file_path, file_path)
                await message.bot.send_video(chat_id=user_chat_id, video=FSInputFile(file_path))
                Path.unlink(file_path)
            
            elif reply_to.video_note:
                file_name = f"{uuid4()}.mp4"
                file_path = Path('.').joinpath("media").joinpath(file_name)
                fl = await message.bot.get_file(reply_to.video_note.file_id)

                await message.bot.download_file(fl.file_path, file_path)
                await message.bot.send_video_note(chat_id=user_chat_id, video_note=FSInputFile(file_path))
                Path.unlink(file_path)
            
            elif reply_to.voice:
                file_name = f"{uuid4()}.ogg"
                file_path = Path('.').joinpath("media").joinpath(file_name)
                fl = await message.bot.get_file(reply_to.voice.file_id)

                await message.bot.download_file(fl.file_path, file_path)
                await message.bot.send_audio(chat_id=user_chat_id, audio=FSInputFile(file_path))
                Path.unlink(file_path)

        # Логирование входящих сообщений в БД на случай их удаления
        elif message.photo:
            msg = Message(chat_id=message.chat.id, id=message.message_id, type="photos", content=message.caption if message.caption else "", from_username=message.from_user.username if message.from_user.username else "Нету")
            session.add(msg)

            # ИСПРАВЛЕНО: Берем строго ОДНУ фотку лучшего качества (последнюю в списке), а не все размеры
            photo = message.photo[-1]
            file_name = f"{uuid4()}.jpg"
            fl = await message.bot.get_file(photo.file_id)
            await message.bot.download_file(fl.file_path, Path('.').joinpath("media").joinpath(file_name))

            file = File(file_name=file_name, message_id=message.message_id)
            session.add(file)
            session.commit()

        elif message.video:
            msg = Message(chat_id=message.chat.id, id=message.message_id, type="video", content=message.caption if message.caption else "", from_username=message.from_user.username if message.from_user.username else "Нету")
            session.add(msg)

            file_name = f"{uuid4()}.mp4"
            fl = await message.bot.get_file(message.video.file_id)
            await message.bot.download_file(fl.file_path, Path('.').joinpath("media").joinpath(file_name))

            file = File(file_name=file_name, message_id=message.message_id)
            session.add(file)
            session.commit()

        elif message.video_note:
            msg = Message(chat_id=message.chat.id, id=message.message_id, type="video_note", content=message.caption if message.caption else "", from_username=message.from_user.username if message.from_user.username else "Нету")
            session.add(msg)

            file_name = f"{uuid4()}.mp4"
            fl = await message.bot.get_file(message.video_note.file_id)
            await message.bot.download_file(fl.file_path, Path('.').joinpath("media").joinpath(file_name))

            file = File(file_name=file_name, message_id=message.message_id)
            session.add(file)
            session.commit()

        elif message.voice:
            msg = Message(chat_id=message.chat.id, id=message.message_id, type="audio", content=message.caption if message.caption else "", from_username=message.from_user.username if message.from_user.username else "Нету")
            session.add(msg)

            file_name = f"{uuid4()}.ogg"
            fl = await message.bot.get_file(message.voice.file_id)
            await message.bot.download_file(fl.file_path, Path('.').joinpath("media").joinpath(file_name))

            file = File(file_name=file_name, message_id=message.message_id)
            session.add(file)
            session.commit()

        elif message.document:
            msg = Message(chat_id=message.chat.id, id=message.message_id, type="document", content=message.caption if message.caption else "", from_username=message.from_user.username if message.from_user.username else "Нету")
            session.add(msg)

            ext = message.document.mime_type.split('/')[1] if message.document.mime_type else "bin"
            file_name = f"{uuid4()}.{ext}"
            fl = await message.bot.get_file(message.document.file_id)
            await message.bot.download_file(fl.file_path, Path('.').joinpath("media").joinpath(file_name))

            file = File(file_name=file_name, message_id=message.message_id)
            session.add(file)
            session.commit()

        else:
            msg = Message(chat_id=message.chat.id, id=message.message_id, type="text", content=message.text, from_username=message.from_user.username if message.from_user.username else "Нету")
            session.add(msg)
            session.commit()


async def main() -> None:
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    db.init()
    asyncio.run(main())
