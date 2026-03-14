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
    cookie_file = "/app/cookies.txt"
    if os.path.exists(cookie_file):
        return {"cookiefile": cookie_file}
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
    """
    Use tv_embedded client — most reliable for cloud IPs.
    tv_embedded gives pre-merged MP4 streams that don't need
    PO tokens or SABR negotiation, and work on server IPs.
    """
    opts = {
        "quiet": True,
        "noplaylist": True,
        "extractor_args": {
            "youtube": {
                # tv_embedded: stable client, no SABR, no PO token needed
                # android_vr: fallback, works for most videos
                "player_client": ["tv_embedded", "android_vr"],
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
        # format 18 = 360p MP4 pre-merged (always available via tv_embedded)
        # format 22 = 720p MP4 pre-merged (available on most videos)
        # We prefer 22 (720p) and fall back to 18 (360p), then best
        opts["format"] = "22/18/best[ext=mp4]/best"
        opts["merge_output_format"] = "mp4"
    else:
        # For audio: use format 140 (m4a 128k) which is always available
        # fallback to bestaudio then convert to mp3
        opts["format"] = "140/bestaudio[ext=m4a]/bestaudio/best"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]

    logger.info("Downloading %s [%s]", url, fmt)
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    files = os.listdir(tmpdir)
    if not files:
        raise FileNotFoundError("No file was downloaded.")
    return os.path.join(tmpdir, files[0])

# ── Instagram Reel downloader ─────────────────────────────────────────────────
INSTAGRAM_RE = re.compile(
    r"(https?://)?(www\.)?instagram\.com/(reel|p|reels)/[\w\-]+"
)

def extract_instagram_url(text: str):
    m = INSTAGRAM_RE.search(text)
    return m.group(0) if m else None

def download_instagram(url: str) -> str:
    tmpdir = tempfile.mkdtemp()
    opts = {
        "quiet": True,
        "outtmpl": os.path.join(tmpdir, "%(title)s.%(ext)s"),
        "format": "best[ext=mp4]/best",
    }
    ig_cookies = os.environ.get("INSTAGRAM_COOKIES", "").strip()
    if ig_cookies:
        tmp = "/tmp/ig_cookies.txt"
        with open(tmp, "w") as f:
            f.write(ig_cookies)
        opts["cookiefile"] = tmp

    logger.info("Downloading Instagram: %s", url)
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    files = os.listdir(tmpdir)
    if not files:
        raise FileNotFoundError("No file was downloaded.")
    return os.path.join(tmpdir, files[0])

# ── Telegram handlers ─────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Welcome to Downloader Bot!*\n\n"
        "📺 Send a *YouTube* link → choose Video or Audio\n"
        "📸 Send an *Instagram Reel* link → get the video\n\n"
        "_Paste any link to get started!_",
        parse_mode="Markdown",
    )

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""

    # ── Instagram Reel ──
    ig_url = extract_instagram_url(text)
    if ig_url:
        await update.message.reply_text("⏳ Downloading Instagram Reel…")
        try:
            file_path = await asyncio.to_thread(download_instagram, ig_url)
            with open(file_path, "rb") as f:
                await context.bot.send_video(
                    chat_id=update.message.chat_id,
                    video=f,
                    supports_streaming=True,
                    caption="📸 Here's your Reel!",
                    read_timeout=300, write_timeout=300,
                )
            await update.message.reply_text("✅ Done! Enjoy 🎉")
        except Exception as exc:
            logger.exception("Instagram download error")
            await update.message.reply_text(f"❌ Failed:\n`{exc}`", parse_mode="Markdown")
        finally:
            try:
                os.remove(file_path)
            except Exception:
                pass
        return

    # ── YouTube ──
    yt_url = extract_url(text)
    if yt_url:
        context.user_data["yt_url"] = yt_url
        title = await asyncio.to_thread(get_title, yt_url)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🎬 Video (MP4)", callback_data="dl:video"),
            InlineKeyboardButton("🎵 Audio (MP3)", callback_data="dl:audio"),
        ]])
        await update.message.reply_text(
            f"🎯 *{title}*\n\nChoose format:",
            reply_markup=kb,
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text(
        "⚠️ Please send a valid YouTube or Instagram Reel link."
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
