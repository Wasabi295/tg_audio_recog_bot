from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackContext, CallbackQueryHandler, filters
from pydub import AudioSegment
import logging
import os
import requests
from collections import defaultdict
import tempfile

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
AUDD_API_KEY = "your key from AUDD"
TELEGRAM_TOKEN = "your telegram token for bot"
FFMPEG_PATH = r"your path to ffmpeg"
SEGMENT_DURATION = 30  # Длительность сегмента для анализа (сек)
MAX_SEGMENTS = 10  # Максимальное количество сегментов

AudioSegment.converter = FFMPEG_PATH
user_data = defaultdict(dict)


async def start(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "🎵 Отправьте аудиофайл, и я найду все треки в нём!\n"
        "Анализирую файл по частям для лучшего результата."
    )


def split_audio(input_path: str) -> list:
    """Разбивает аудио на сегменты и возвращает пути к временным файлам"""
    try:
        audio = AudioSegment.from_file(input_path)
        duration = len(audio)
        segments = []

        # Создаем временную папку
        temp_dir = tempfile.mkdtemp()

        # Разбиваем на сегменты с перекрытием
        for i in range(0, min(MAX_SEGMENTS * SEGMENT_DURATION * 1000, duration), SEGMENT_DURATION * 1000):
            start_time = i
            end_time = min(i + SEGMENT_DURATION * 1000, duration)
            segment = audio[start_time:end_time]

            segment_path = os.path.join(temp_dir, f"segment_{i // 1000}.mp3")
            segment.export(segment_path, format="mp3")
            segments.append(segment_path)

        return segments

    except Exception as e:
        logger.error(f"Ошибка разделения аудио: {str(e)}")
        raise


async def recognize_audio_segments(segment_paths: list) -> list:
    """Анализирует все сегменты и возвращает уникальные треки"""
    all_results = []

    for segment_path in segment_paths:
        try:
            with open(segment_path, "rb") as f:
                response = requests.post(
                    "https://api.audd.io/",
                    data={
                        "api_token": AUDD_API_KEY,
                        "return": "apple_music,spotify",
                        "include_alternatives": "true"
                    },
                    files={"file": f}
                )
            data = response.json()

            if data.get("status") == "success":
                if data.get("result"):
                    all_results.append(data["result"])
                if data.get("alternatives"):
                    all_results.extend(data["alternatives"])

        except Exception as e:
            logger.error(f"Ошибка анализа сегмента: {str(e)}")

    # Удаляем дубликаты (по artist + title)
    unique_results = []
    seen = set()

    for track in all_results:
        if not track.get("title") or not track.get("artist"):
            continue

        key = f"{track['artist'].lower()}_{track['title'].lower()}"
        if key not in seen:
            seen.add(key)
            unique_results.append(track)

    return unique_results


async def handle_audio(update: Update, context: CallbackContext) -> None:
    """Обработка аудиофайлов"""
    try:
        # Скачиваем файл
        file = await update.message.audio.get_file()
        original_path = "original_audio"
        await file.download_to_drive(original_path)

        # Разбиваем на сегменты
        segment_paths = split_audio(original_path)
        logger.info(f"Создано {len(segment_paths)} сегментов для анализа")

        # Анализируем каждый сегмент
        results = await recognize_audio_segments(segment_paths)
        logger.info(f"Найдено уникальных треков: {len(results)}")

        if not results:
            await update.message.reply_text("❌ Совпадений не найдено")
            return

        # Сохраняем результаты
        user_data[update.effective_chat.id] = {
            'tracks': results,
            'current_index': 0
        }

        # Показываем первый результат
        await show_track_result(update, context)

    except Exception as e:
        logger.error(f"Ошибка обработки аудио: {str(e)}")
        await update.message.reply_text("⚠️ Ошибка обработки файла")

    finally:
        # Очистка временных файлов
        if 'original_path' in locals() and os.path.exists(original_path):
            try:
                os.remove(original_path)
            except Exception as e:
                logger.warning(f"Ошибка удаления файла: {str(e)}")


async def show_track_result(update: Update, context: CallbackContext, is_callback: bool = False):
    """Показывает текущий трек с кнопками навигации"""
    chat_id = update.effective_chat.id
    data = user_data.get(chat_id, {})

    if not data or not data.get('tracks'):
        if not is_callback:
            await update.message.reply_text("❌ Результаты не найдены")
        return

    tracks = data['tracks']
    current_index = data['current_index']
    total = len(tracks)

    if current_index >= total:
        await context.bot.send_message(chat_id, "🎉 Это все найденные треки!")
        return

    track = tracks[current_index]
    message = format_track_info(track, current_index + 1, total)
    keyboard = create_navigation_keyboard(current_index, total)

    if is_callback:
        await update.callback_query.edit_message_text(
            text=message,
            reply_markup=keyboard,
            disable_web_page_preview=True
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=message,
            reply_markup=keyboard,
            disable_web_page_preview=True
        )


def format_track_info(track: dict, position: int, total: int) -> str:
    """Форматирует информацию о треке"""
    info = [f"🔍 Трек {position} из {total}:"]

    if track.get('title'):
        info.append(f"🎵 Название: {track['title']}")
    if track.get('artist'):
        info.append(f"🎤 Исполнитель: {track['artist']}")
    if track.get('album'):
        info.append(f"💿 Альбом: {track['album']}")
    if track.get('release_date'):
        info.append(f"📅 Дата выхода: {track['release_date']}")
    if track.get('score'):
        info.append(f"🔢 Точность: {track['score']:.0f}%")

    # Ссылки
    spotify_url = track.get('spotify', {}).get('external_urls', {}).get('spotify')
    if spotify_url:
        info.append(f"🔗 Spotify: {spotify_url}")

    apple_music_url = track.get('apple_music', {}).get('url')
    if apple_music_url:
        info.append(f"🔗 Apple Music: {apple_music_url}")

    return "\n".join(info)


def create_navigation_keyboard(current_index: int, total: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру навигации"""
    buttons = []

    if current_index > 0:
        buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data="prev_track"))

    buttons.append(InlineKeyboardButton(f"{current_index + 1}/{total}", callback_data="position"))

    if current_index < total - 1:
        buttons.append(InlineKeyboardButton("Вперёд ➡️", callback_data="next_track"))

    return InlineKeyboardMarkup([buttons]) if buttons else None


async def button_callback(update: Update, context: CallbackContext) -> None:
    """Обработка нажатий кнопок"""
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    data = user_data.get(chat_id, {})

    if not data:
        return

    current_index = data['current_index']

    if query.data == "prev_track" and current_index > 0:
        user_data[chat_id]['current_index'] -= 1
    elif query.data == "next_track" and current_index < len(data['tracks']) - 1:
        user_data[chat_id]['current_index'] += 1

    await show_track_result(update, context, is_callback=True)


def main() -> None:
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    application.add_handler(CallbackQueryHandler(button_callback))

    logger.info("Бот запущен")
    application.run_polling()


if __name__ == "__main__":
    main()