from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackContext, CallbackQueryHandler, filters
from pydub import AudioSegment
import logging
import os
import aiohttp
import time
import base64
import hmac
import hashlib
import asyncio
import json
from aiohttp import FormData

# THAT'S THE VERSION FOR ACRCloud but i don't know if it's working properly, also in this version u will never get multiple results, cause i didn't implement it, it's just an example of how u can use ACRCloud API, also here is a bug
# if u try to put different MAX_DURATION u will get different results, in the version for AUDd i made a segmentation of the audio file so u can get multiple results.

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация ACRCloud
ACR_ACCESS_KEY = "your access key"
ACR_SECRET_KEY = "your secret key"
ACR_HOST = "your host"
TELEGRAM_TOKEN = "your token"
FFMPEG_PATH = r"your ffmpeg path"
MAX_DURATION = 30  # Оптимальное время для анализа

AudioSegment.converter = FFMPEG_PATH
user_data = {}


def get_acrcloud_signature():
    """Генерация подписи для ACRCloud API"""
    timestamp = str(int(time.time()))
    string_to_sign = f"POST\n/v1/identify\n{ACR_ACCESS_KEY}\n{timestamp}"
    signature = base64.b64encode(
        hmac.new(ACR_SECRET_KEY.encode(), string_to_sign.encode(), hashlib.sha1).digest()
    ).decode()
    return timestamp, signature


async def process_audio(input_path: str) -> str:
    """Обработка аудиофайлов с минимальной конвертацией"""
    try:
        ext = os.path.splitext(input_path)[1].lower()
        if ext in ['.mp3', '.ogg', '.wav']:
            logger.info(f"Используется оригинальный формат: {ext}")
            return input_path

        output_path = f"temp_{int(time.time())}.mp3"
        audio = AudioSegment.from_file(input_path)
        audio = audio[:MAX_DURATION * 3000]
        audio.export(output_path, format="mp3")
        return output_path
    except Exception as e:
        logger.error(f"Ошибка обработки аудио: {str(e)}")
        raise


async def recognize_audio(file_path: str) -> list:
    """Запрос к ACRCloud API"""
    try:
        timestamp, signature = get_acrcloud_signature()
        url = f"https://{ACR_HOST}/v1/identify"

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            data = FormData()
            content_type = "audio/mpeg" if file_path.endswith('.mp3') else "audio/ogg"

            with open(file_path, 'rb') as f:
                data.add_field(
                    'sample',
                    f,
                    filename=os.path.basename(file_path),
                    content_type=content_type
                )
                data.add_field('multi', '5')

                response = await session.post(
                    url,
                    headers={
                        "Authorization": f"ACR {ACR_ACCESS_KEY}:{signature}",
                        "Date": timestamp
                    },
                    data=data
                )

                logger.info(f"Статус ответа: {response.status}")
                raw_response = await response.text()
                logger.debug(f"Сырой ответ: {raw_response}")

                try:
                    result = json.loads(raw_response)
                except Exception as e:
                    logger.error(f"Ошибка парсинга JSON: {str(e)}")
                    return []

        if result.get("status", {}).get("code") != 0:
            logger.error(f"Ошибка API: {result.get('status', {}).get('msg', 'Неизвестная ошибка')}")
            return []

        return result.get("metadata", {}).get("music", [])
    except Exception as e:
        logger.error(f"Ошибка запроса: {str(e)}", exc_info=True)
        return []
    finally:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as e:
                logger.warning(f"Ошибка удаления файла: {str(e)}")


async def start(update: Update, context: CallbackContext) -> None:
    """Обработчик команды /start"""
    await update.message.reply_text(
        "🎵 Отправьте аудиофайл или голосовое сообщение для распознавания музыки!\n"
        "📌 Поддерживаемые форматы: MP3, OGG, WAV\n"
        "🔍 После обработки вы получите информацию о треке и кнопки управления"
    )


async def handle_audio(update: Update, context: CallbackContext) -> None:
    """Обработка входящих аудио сообщений"""
    try:
        logger.info(f"Получен аудиофайл от пользователя {update.effective_user.id}")

        if update.message.audio:
            file = await update.message.audio.get_file()
        elif update.message.voice:
            file = await update.message.voice.get_file()
        else:
            await update.message.reply_text("⚠️ Пожалуйста, отправьте аудиофайл или голосовое сообщение")
            return

        original_path = f"temp_{int(time.time())}_{update.update_id}"
        await file.download_to_drive(custom_path=original_path)

        processed_path = await process_audio(original_path)
        results = await recognize_audio(processed_path)

        if not results:
            await update.message.reply_text("❌ Совпадений не найдено")
            return

        user_data[update.effective_chat.id] = {
            'tracks': results,
            'current_index': 0
        }
        await show_next_result(update, context)

    except Exception as e:
        logger.error(f"Критическая ошибка: {str(e)}", exc_info=True)
        await update.message.reply_text("⚠️ Произошла ошибка при обработке файла")
    finally:
        if os.path.exists(original_path):
            try:
                os.remove(original_path)
            except Exception as e:
                logger.warning(f"Ошибка очистки: {str(e)}")


def format_track_info(track: dict) -> str:
    """Форматирование информации о треке"""
    info = []
    title = track.get('title', 'Неизвестно')
    artists = ', '.join([a.get('name', '') for a in track.get('artists', [])]) or 'Неизвестен'

    info.append(f"🎵 <b>Название:</b> {title}")
    info.append(f"🎤 <b>Исполнитель:</b> {artists}")

    if track.get('album'):
        info.append(f"💿 <b>Альбом:</b> {track['album'].get('name', 'Неизвестен')}")

    if track.get('release_date'):
        info.append(f"📅 <b>Год выпуска:</b> {track['release_date']}")

    if track.get('external_metadata', {}).get('spotify', {}).get('track', {}).get('id'):
        spotify_id = track['external_metadata']['spotify']['track']['id']
        info.append(f"🔗 <a href='https://open.spotify.com/track/{spotify_id}'>Слушать в Spotify</a>")

    if track.get('external_metadata', {}).get('youtube', {}).get('vid'):
        youtube_id = track['external_metadata']['youtube']['vid']
        info.append(f"📺 <a href='https://youtu.be/{youtube_id}'>Смотреть на YouTube</a>")

    return "\n".join(info)


def create_keyboard(current_index: int, total: int) -> InlineKeyboardMarkup:
    """Создание интерактивной клавиатуры"""
    buttons = []
    if current_index < total - 1:
        buttons.append(InlineKeyboardButton("➡️ Следующий трек", callback_data="next_track"))

    buttons.extend([
        InlineKeyboardButton("🔄 Новый поиск", callback_data="new_search"),
        InlineKeyboardButton("📋 Все результаты", callback_data="show_all"),
        InlineKeyboardButton("❌ Закрыть", callback_data="close")
    ])

    return InlineKeyboardMarkup([
        buttons[0:1],
        buttons[1:3],
        buttons[3:]
    ])


async def show_next_result(update: Update, context: CallbackContext, is_callback: bool = False):
    """Отображение следующего результата"""
    chat_id = update.effective_chat.id
    data = user_data.get(chat_id)

    if not data or not data.get('tracks'):
        await context.bot.send_message(chat_id, "❌ Нет активных результатов")
        return

    current_index = data['current_index']
    tracks = data['tracks']

    if current_index >= len(tracks):
        await context.bot.send_message(chat_id, "🎉 Вы просмотрели все результаты!")
        return

    track = tracks[current_index]
    message = format_track_info(track)
    keyboard = create_keyboard(current_index, len(tracks))

    try:
        if is_callback:
            await update.callback_query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='HTML',
                disable_web_page_preview=True
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=message,
                reply_markup=keyboard,
                parse_mode='HTML',
                disable_web_page_preview=True
            )

        user_data[chat_id]['current_index'] = current_index + 1

    except Exception as e:
        logger.error(f"Ошибка отображения: {str(e)}")


async def show_all_results(update: Update, context: CallbackContext):
    """Отображение всех результатов"""
    chat_id = update.effective_chat.id
    data = user_data.get(chat_id)

    if not data or not data.get('tracks'):
        await context.bot.send_message(chat_id, "❌ Нет доступных результатов")
        return

    tracks = data['tracks']
    message = "<b>Все найденные треки:</b>\n\n" + "\n\n".join(
        [f"{i + 1}. {format_track_info(track)}" for i, track in enumerate(tracks)]
    )

    await context.bot.send_message(
        chat_id=chat_id,
        text=message,
        parse_mode='HTML',
        disable_web_page_preview=True
    )


async def button_callback(update: Update, context: CallbackContext) -> None:
    """Обработчик нажатий на кнопки"""
    query = update.callback_query
    await query.answer()

    try:
        if query.data == "next_track":
            await show_next_result(update, context, is_callback=True)
        elif query.data == "new_search":
            await context.bot.send_message(
                query.message.chat_id,
                "🎤 Отправьте новый аудиофайл для распознавания"
            )
        elif query.data == "show_all":
            await show_all_results(update, context)
        elif query.data == "close":
            await query.message.delete()
    except Exception as e:
        logger.error(f"Ошибка обработки кнопки: {str(e)}")
        await query.edit_message_text("⚠️ Произошла ошибка, попробуйте снова")


async def main():
    """Основная функция запуска бота"""
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.AUDIO | filters.VOICE, handle_audio))
    application.add_handler(CallbackQueryHandler(button_callback))

    logger.info("Бот успешно запущен")

    try:
        await application.initialize()
        await application.start()
        await application.updater.start_polling()

        # Бесконечный цикл обработки
        while True:
            await asyncio.sleep(3600)

    except (KeyboardInterrupt, SystemExit):
        logger.info("Остановка бота...")
    finally:
        await application.stop()
        await application.shutdown()


if __name__ == "__main__":
    # Явное создание и управление event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        pass
    finally:
        if not loop.is_closed():
            loop.close()
