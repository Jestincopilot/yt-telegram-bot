import os
import re
import logging
import asyncio
import tempfile
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Cookie support ────────────────────────────────────────────────────────────
COOKIES_FILE = "/app/cookies.txt"
_tmp_cookie_written = False

def get_base_ydl_opts() -> dict:
    global _tmp_cookie_written
    opts = {
        "quiet": True,
        "noplaylist": True,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        },
    }
    if os.path.exists(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    else:
        cookie_str = os.environ.get("YOUTUBE_COOKIES", "").strip()
        if cookie_str:
            tmp_cookie = "/tmp/yt_cookies.txt"
            if not _tmp_cookie_written:
                with open(tmp_cookie, "w") as f:
                    f.write(cookie_str)
                _tmp_cookie_written = True
            opts["cookiefile"] = tmp_cookie
    return opts


# ── Helpers ───────────────────────────────────────────────────────────────────
YOUTUBE_REGEX = re.compile(
    r"(https?://)?(www\.)?"
    r"(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)"
    r"[\w\-]{11}"
)

def extract_youtube_url(text: str):
    match = YOUTUBE_REGEX.search(text)
    return match.group(0) if match else None


def get_video_info(url: str) -> dict:
    """Fetch full video info including available formats."""
    opts = get_base_ydl_opts()
    opts["skip_download"] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def pick_video_format(formats: list) -> str:
    """
    Dynamically pick the best available video format string.
    Tries to find a combined mp4, then best available.
    """
    # Collect available format ids
    ids = {f.get("format_id") for f in formats}
    exts = {f.get("ext") for f in formats}

    # Find best video-only stream (prefer mp4/webm) up to 720p
    video_formats = [
        f for f in formats
        if f.get("vcodec", "none") != "none"
        and f.get("acodec", "none") == "none"
        and f.get("height") is not None
        and f.get("height") <= 720
    ]
    audio_formats = [
        f for f in formats
        if f.get("acodec", "none") != "none"
        and f.get("vcodec", "none") == "none"
    ]

    if video_formats and audio_formats:
        # Sort video by height descending
        video_formats.sort(key=lambda x: x.get("height", 0), reverse=True)
        best_video = video_formats[0]["format_id"]

        # Prefer m4a audio
        m4a = [f for f in audio_formats if f.get("ext") == "m4a"]
        best_audio = (m4a or audio_formats)[0]["format_id"]

        return f"{best_video}+{best_audio}"

    # Fallback: best single combined format
    combined = [
        f for f in formats
        if f.get("vcodec", "none") != "none"
        and f.get("acodec", "none") != "none"
    ]
    if combined:
        combined.sort(key=lambda x: x.get("height") or 0, reverse=True)
        return combined[0]["format_id"]

    # Last resort
    return "best"


def pick_audio_format(formats: list) -> str:
    """Pick best available audio-only format."""
    audio_formats = [
        f for f in formats
        if f.get("acodec", "none") != "none"
        and f.get("vcodec", "none") == "none"
    ]
    if not audio_formats:
        # fallback to combined and strip video later
        return "best"

    # Prefer m4a, then webm, then anything
    for ext in ("m4a", "webm", "mp4"):
        match = [f for f in audio_formats if f.get("ext") == ext]
        if match:
            match.sort(key=lambda x: x.get("abr") or 0, reverse=True)
            return match[0]["format_id"]

    audio_formats.sort(key=lambda x: x.get("abr") or 0, reverse=True)
    return audio_formats[0]["format_id"]


def get_video_title(url: str) -> str:
    try:
        info = get_video_info(url)
        return info.get("title", "Video")[:60]
    except Exception:
        return "Video"


# ── Handlers ──────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *Welcome to YT Downloader Bot!*\n\n"
        "Just send me any YouTube link and I'll let you choose:\n"
        "🎬 *Video* (MP4) or 🎵 *Audio* (MP3)\n\n"
        "_Paste a link to get started!_",
        parse_mode="Markdown",
    )


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    url = extract_youtube_url(update.message.text)
    if not url:
        await update.message.reply_text("⚠️ Please send a valid YouTube link.")
        return

    context.user_data["yt_url"] = url
    title = await asyncio.to_thread(get_video_title, url)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎬 Video (MP4)", callback_data="download:video"),
            InlineKeyboardButton("🎵 Audio (MP3)", callback_data="download:audio"),
        ]
    ])

    await update.message.reply_text(
        f"🎯 *{title}*\n\nChoose a download format:",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    _, fmt = query.data.split(":")
    url = context.user_data.get("yt_url")

    if not url:
        await query.edit_message_text("⚠️ Session expired. Please send the link again.")
        return

    await query.edit_message_text(
        f"⏳ Downloading {'video 🎬' if fmt == 'video' else 'audio 🎵'} … please wait."
    )

    try:
        file_path = await asyncio.to_thread(download_media, url, fmt)
    except Exception as exc:
        logger.exception("Download failed")
        await query.edit_message_text(f"❌ Download failed:\n`{exc}`", parse_mode="Markdown")
        return

    await query.edit_message_text("📤 Uploading to Telegram…")

    try:
        chat_id = query.message.chat_id
        if fmt == "video":
            with open(file_path, "rb") as f:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=f,
                    supports_streaming=True,
                    caption="🎬 Here's your video!",
                    read_timeout=120,
                    write_timeout=120,
                )
        else:
            with open(file_path, "rb") as f:
                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=f,
                    caption="🎵 Here's your audio!",
                    read_timeout=120,
                    write_timeout=120,
                )
        await query.edit_message_text("✅ Done! Enjoy 🎉")
    except Exception as exc:
        logger.exception("Upload failed")
        await query.edit_message_text(f"❌ Upload failed:\n`{exc}`", parse_mode="Markdown")
    finally:
        try:
            os.remove(file_path)
        except OSError:
            pass


# ── Download logic ────────────────────────────────────────────────────────────
def download_media(url: str, fmt: str) -> str:
    tmpdir = tempfile.mkdtemp()
    opts = get_base_ydl_opts()

    # ── Fetch available formats first ─────────────────────────────────────────
    logger.info("Fetching available formats for: %s", url)
    info = get_video_info(url)
    formats = info.get("formats", [])
    logger.info("Total formats available: %d", len(formats))

    if fmt == "video":
        chosen_format = pick_video_format(formats)
        logger.info("Chosen video format: %s", chosen_format)
        opts.update({
            "format": chosen_format,
            "outtmpl": os.path.join(tmpdir, "%(title)s.%(ext)s"),
            "merge_output_format": "mp4",
        })
    else:
        chosen_format = pick_audio_format(formats)
        logger.info("Chosen audio format: %s", chosen_format)
        opts.update({
            "format": chosen_format,
            "outtmpl": os.path.join(tmpdir, "%(title)s.%(ext)s"),
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    files = os.listdir(tmpdir)
    if not files:
        raise FileNotFoundError("No file was downloaded.")
    return os.path.join(tmpdir, files[0])


# ── Entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable is not set!")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(CallbackQueryHandler(handle_callback, pattern=r"^download:"))

    logger.info("Bot is running…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
