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

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Cookies ───────────────────────────────────────────────────────────────────
_tmp_cookie_written = False

def get_cookie_opts() -> dict:
    global _tmp_cookie_written
    if os.path.exists("/app/cookies.txt"):
        return {"cookiefile": "/app/cookies.txt"}
    cookie_str = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if cookie_str:
        tmp = "/tmp/yt_cookies.txt"
        if not _tmp_cookie_written:
            with open(tmp, "w") as f:
                f.write(cookie_str)
            _tmp_cookie_written = True
        return {"cookiefile": tmp}
    return {}

def base_opts() -> dict:
    opts = {
        "quiet": True,
        "noplaylist": True,
        # Use android_vr client — not affected by SABR restrictions, no JS needed
        "extractor_args": {
            "youtube": {
                "player_client": ["android_vr"],
            }
        },
    }
    opts.update(get_cookie_opts())
    return opts

# ── YouTube URL detector ──────────────────────────────────────────────────────
YT_RE = re.compile(
    r"(https?://)?(www\.)?"
    r"(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)"
    r"[\w\-]{11}"
)

def extract_url(text: str):
    m = YT_RE.search(text)
    return m.group(0) if m else None

def get_title(url: str) -> str:
    try:
        opts = {**base_opts(), "skip_download": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get("title", "Video")[:60]
    except Exception as e:
        logger.warning("Could not fetch title: %s", e)
        return "Video"

# ── Download ──────────────────────────────────────────────────────────────────
def download_media(url: str, fmt: str) -> str:
    tmpdir = tempfile.mkdtemp()
    opts = base_opts()
    opts["outtmpl"] = os.path.join(tmpdir, "%(title)s.%(ext)s")

    if fmt == "video":
        opts["format"] = "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best[height<=720]/best"
        opts["merge_output_format"] = "mp4"
    else:
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]

    logger.info("Downloading %s as %s", url, fmt)
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    files = os.listdir(tmpdir)
    if not files:
        raise FileNotFoundError("No file was downloaded.")
    return os.path.join(tmpdir, files[0])

# ── Telegram handlers ─────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Welcome to YT Downloader Bot!*\n\n"
        "Send me any YouTube link and choose:\n"
        "🎬 *Video (MP4)* or 🎵 *Audio (MP3)*",
        parse_mode="Markdown",
    )

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = extract_url(update.message.text)
    if not url:
        await update.message.reply_text("⚠️ Please send a valid YouTube link.")
        return

    context.user_data["yt_url"] = url
    title = await asyncio.to_thread(get_title, url)

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🎬 Video (MP4)", callback_data="dl:video"),
        InlineKeyboardButton("🎵 Audio (MP3)", callback_data="dl:audio"),
    ]])
    await update.message.reply_text(
        f"🎯 *{title}*\n\nChoose format:",
        reply_markup=kb,
        parse_mode="Markdown",
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, fmt = query.data.split(":")
    url = context.user_data.get("yt_url")

    if not url:
        await query.edit_message_text("⚠️ Session expired. Send the link again.")
        return

    await query.edit_message_text(
        f"⏳ Downloading {'video 🎬' if fmt == 'video' else 'audio 🎵'}… please wait."
    )

    try:
        file_path = await asyncio.to_thread(download_media, url, fmt)
    except Exception as exc:
        logger.exception("Download error")
        await query.edit_message_text(f"❌ Download failed:\n`{exc}`", parse_mode="Markdown")
        return

    await query.edit_message_text("📤 Uploading to Telegram…")
    try:
        chat_id = query.message.chat_id
        if fmt == "video":
            with open(file_path, "rb") as f:
                await context.bot.send_video(
                    chat_id=chat_id, video=f,
                    supports_streaming=True,
                    caption="🎬 Here's your video!",
                    read_timeout=300, write_timeout=300,
                )
        else:
            with open(file_path, "rb") as f:
                await context.bot.send_audio(
                    chat_id=chat_id, audio=f,
                    caption="🎵 Here's your audio!",
                    read_timeout=300, write_timeout=300,
                )
        await query.edit_message_text("✅ Done! Enjoy 🎉")
    except Exception as exc:
        logger.exception("Upload error")
        await query.edit_message_text(f"❌ Upload failed:\n`{exc}`", parse_mode="Markdown")
    finally:
        try:
            os.remove(file_path)
        except OSError:
            pass

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not set!")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(CallbackQueryHandler(handle_callback, pattern=r"^dl:"))

    logger.info("Bot is running…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
