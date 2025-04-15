from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackContext, filters
from pydub import AudioSegment
import os
import requests
import logging
import subprocess

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Пути к FFmpeg (ОБЯЗАТЕЛЬНО УКАЖИТЕ СВОИ!)
FFMPEG_PATH = r"C:\ffmpeg\bin\ffmpeg.exe"
FFPROBE_PATH = r"C:\ffmpeg\bin\ffprobe.exe"

# Настройки ACRCloud (ЗАМЕНИТЕ НА СВОИ!)
ACR_HOST = "https://identify-eu-west-1.acrcloud.com"
ACCESS_KEY = "fb07d5200c9ea10d5897e449525767fe"
ACCESS_SECRET = "jUSs8yLcqOF2tw79qCp1iloB8uPptJWi0ZzmRKft"

# Настройка Telegram бота (ЗАМЕНИТЕ НА СВОЙ ТОКЕН!)
TELEGRAM_TOKEN = "7892537711:AAGweAfJrXDymHwDJPo-xj-Vp_8FsT1l1sg"

# Инициализация аудио модуля
AudioSegment.ffmpeg = FFMPEG_PATH
AudioSegment.ffprobe = FFPROBE_PATH

async def start(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "🎧 Привет! Отправь мне голосовое сообщение или аудиофайл, "
        "и я попробую определить название трека!\n\n"
        "⚠️ Лучше всего работают фрагменты длиной 10-30 секунд"
    )

def recognize_audio(file_path: str) -> str:
    try:
        with open(file_path, "rb") as f:
            response = requests.post(
                f"{ACR_HOST}/v1/identify",
                headers={
                    "access-key": ACCESS_KEY,
                    "access-secret": ACCESS_SECRET
                },
                files={"sample": f},
                timeout=15
            )

        if response.status_code != 200:
            logger.error(f"ACRCloud error: {response.status_code}")
            return "❌ Ошибка соединения с сервисом распознавания"

        data = response.json()
        music_data = data.get("metadata", {}).get("music", [])

        if not music_data:
            return "❌ Не удалось распознать трек"

        track = music_data[0]
        title = track.get("title", "Неизвестный трек")
        artist = track.get("artists", [{}])[0].get("name", "Неизвестный исполнитель")
        spotify_url = track.get("external_metadata", {}).get("spotify", {}).get("track", {}).get("url", "Нет ссылки")

        return (
            f"🎵 Результат:\n"
            f"▫️ Название: {title}\n"
            f"▫️ Исполнитель: {artist}\n"
            f"🔗 Spotify: {spotify_url}"
        )

    except Exception as e:
        logger.error(f"Recognition error: {str(e)}")
        return "❌ Ошибка при обработке аудио"

async def handle_voice(update: Update, context: CallbackContext) -> None:
    try:
        await update.message.reply_text("🔍 Анализирую голосовое сообщение...")
        voice_file = await update.message.voice.get_file()
        ogg_path = "temp_voice.ogg"
        await voice_file.download_to_drive(ogg_path)

        # Конвертация в MP3
        mp3_path = "voice.mp3"
        AudioSegment.from_ogg(ogg_path).export(mp3_path, format="mp3")

        result = recognize_audio(mp3_path)
        await update.message.reply_text(result)

    except Exception as e:
        logger.error(f"Voice error: {str(e)}")
        await update.message.reply_text("❌ Ошибка обработки голосового сообщения")

    finally:
        for path in [ogg_path, mp3_path]:
            if os.path.exists(path):
                os.remove(path)

async def handle_audio(update: Update, context: CallbackContext) -> None:
    try:
        await update.message.reply_text("🔍 Анализирую аудиофайл...")
        audio_file = await update.message.audio.get_file()
        mp3_path = "audio.mp3"
        await audio_file.download_to_drive(mp3_path)

        result = recognize_audio(mp3_path)
        await update.message.reply_text(result)

    except Exception as e:
        logger.error(f"Audio error: {str(e)}")
        await update.message.reply_text("❌ Ошибка обработки аудиофайла")

    finally:
        if os.path.exists(mp3_path):
            os.remove(mp3_path)

def main() -> None:
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(MessageHandler(filters.AUDIO, handle_audio))

    application.run_polling()

if __name__ == "__main__":
    main()