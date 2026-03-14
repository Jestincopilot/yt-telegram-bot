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

# ── URL detectors ─────────────────────────────────────────────────────────────
YT_RE = re.compile(
    r"https?://(www\.)?(youtube\.com/watch\?[^\s]*v=|youtu\.be/|youtube\.com/shorts/)[\w\-]{11}[^\s]*"
)

INSTAGRAM_RE = re.compile(
    r"https?://(www\.)?instagram\.com/(reel|p|reels)/[\w\-]+[^\s]*"
)

FACEBOOK_RE = re.compile(
    r"https?://(www\.|m\.|web\.)?"
    r"(facebook\.com/(watch|reel|reels|video|videos|share/v|share/r)|fb\.watch)"
    r"[^\s]*"
)

def extract_yt_url(text):
    m = YT_RE.search(text)
    return m.group(0) if m else None

def extract_ig_url(text):
    m = INSTAGRAM_RE.search(text)
    return m.group(0) if m else None

def extract_fb_url(text):
    m = FACEBOOK_RE.search(text)
    return m.group(0) if m else None

# ── Core downloader — tries multiple strategies until one works ───────────────
def smart_download(url: str, want_audio_only: bool = False) -> str:
    """
    Try multiple yt-dlp strategies in order.
    Returns path to downloaded file.
    Raises RuntimeError with a helpful message if all strategies fail.
    """
    tmpdir = tempfile.mkdtemp()

    # Build cookie part
    cookie_opts = get_cookie_opts()

    # Common headers to look like a real browser
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
    }

    # ── Strategy list — tried in order until one succeeds ─────────────────────
    # Each entry: (description, extra_opts_dict)
    strategies = []

    if want_audio_only:
        strategies = [
            ("audio/bestaudio+mp3", {
                "format": "bestaudio/best",
                "postprocessors": [{"key": "FFmpegExtractAudio",
                                    "preferredcodec": "mp3",
                                    "preferredquality": "192"}],
            }),
            ("audio/140", {
                "format": "140/bestaudio/best",
                "postprocessors": [{"key": "FFmpegExtractAudio",
                                    "preferredcodec": "mp3",
                                    "preferredquality": "192"}],
            }),
        ]
    else:
        strategies = [
            # Strategy 1: android_embedded — no PO token, no SABR, pre-merged mp4
            ("video/android_embedded", {
                "format": "b[ext=mp4]/b",
                "extractor_args": {"youtube": {"player_client": ["android_embedded"]}},
            }),
            # Strategy 2: ios client — gives direct mp4 URLs
            ("video/ios", {
                "format": "b[ext=mp4]/b",
                "extractor_args": {"youtube": {"player_client": ["ios"]}},
            }),
            # Strategy 3: tv_embedded — no SABR
            ("video/tv_embedded", {
                "format": "b[ext=mp4]/b",
                "extractor_args": {"youtube": {"player_client": ["tv_embedded"]}},
            }),
            # Strategy 4: mweb — mobile web client
            ("video/mweb", {
                "format": "b[ext=mp4]/b",
                "extractor_args": {"youtube": {"player_client": ["mweb"]}},
            }),
            # Strategy 5: default client, just grab absolute best single file
            ("video/default_best", {
                "format": "b",
            }),
            # Strategy 6: format_sort fallback — let yt-dlp decide everything
            ("video/format_sort", {
                "format": "bv*+ba/b",
                "format_sort": ["res:720", "ext:mp4:m4a"],
                "merge_output_format": "mp4",
            }),
        ]

    last_error = None
    for name, extra in strategies:
        attempt_dir = tempfile.mkdtemp()
        opts = {
            "quiet": True,
            "noplaylist": True,
            "outtmpl": os.path.join(attempt_dir, "%(title)s.%(ext)s"),
            "http_headers": headers,
            **cookie_opts,
            **extra,
        }
        try:
            logger.info("Trying strategy: %s for %s", name, url)
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            files = os.listdir(attempt_dir)
            if files:
                logger.info("Success with strategy: %s", name)
                return os.path.join(attempt_dir, files[0])
        except Exception as e:
            last_error = e
            logger.warning("Strategy %s failed: %s", name, e)
            continue

    raise RuntimeError(
        f"All download strategies failed.\nLast error: {last_error}\n\n"
        "This video may be age-restricted, private, or region-blocked."
    )

# ── Instagram downloader ──────────────────────────────────────────────────────
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
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    files = os.listdir(tmpdir)
    if not files:
        raise FileNotFoundError("No file downloaded.")
    return os.path.join(tmpdir, files[0])

# ── Facebook downloader ───────────────────────────────────────────────────────
def download_facebook(url: str) -> str:
    bad = ["?_fb_noscript", "facebook.com/?", "facebook.com/#"]
    for b in bad:
        if b in url:
            raise ValueError(
                "Facebook redirected to homepage instead of the video.\n\n"
                "Please copy the direct link from your browser address bar.\n"
                "It should contain: /watch, /reel, or /videos"
            )
    tmpdir = tempfile.mkdtemp()
    opts = {
        "quiet": True,
        "outtmpl": os.path.join(tmpdir, "%(title)s.%(ext)s"),
        "format": "best[ext=mp4]/best",
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        },
    }
    fb_cookies = os.environ.get("FACEBOOK_COOKIES", "").strip()
    if fb_cookies:
        tmp = "/tmp/fb_cookies.txt"
        with open(tmp, "w") as f:
            f.write(fb_cookies)
        opts["cookiefile"] = tmp
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    files = os.listdir(tmpdir)
    if not files:
        raise FileNotFoundError("No file downloaded.")
    return os.path.join(tmpdir, files[0])

# ── get YouTube title ─────────────────────────────────────────────────────────
def get_yt_title(url: str) -> str:
    try:
        opts = {
            "quiet": True,
            "skip_download": True,
            "extractor_args": {"youtube": {"player_client": ["android_embedded"]}},
            **get_cookie_opts(),
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get("title", "Video")[:60]
    except Exception:
        return "Video"

# ── Generic upload helper ─────────────────────────────────────────────────────
async def upload_video(context, chat_id: int, file_path: str, caption: str):
    with open(file_path, "rb") as f:
        await context.bot.send_video(
            chat_id=chat_id, video=f,
            supports_streaming=True, caption=caption,
            read_timeout=300, write_timeout=300,
        )

async def upload_audio(context, chat_id: int, file_path: str, caption: str):
    with open(file_path, "rb") as f:
        await context.bot.send_audio(
            chat_id=chat_id, audio=f, caption=caption,
            read_timeout=300, write_timeout=300,
        )

def cleanup(path: str):
    try:
        os.remove(path)
    except Exception:
        pass

# ── Telegram handlers ─────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Welcome to Downloader Bot!*\n\n"
        "Supported platforms:\n"
        "📺 *YouTube* — Video (MP4) or Audio (MP3)\n"
        "📸 *Instagram* — Reels & Posts\n"
        "📘 *Facebook* — Videos & Reels\n\n"
        "_Just paste any link to get started!_",
        parse_mode="Markdown",
    )

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    chat_id = update.message.chat_id

    # ── Instagram ──────────────────────────────────────────────────────────────
    ig_url = extract_ig_url(text)
    if ig_url:
        msg = await update.message.reply_text("⏳ Downloading Instagram video…")
        file_path = None
        try:
            file_path = await asyncio.to_thread(download_instagram, ig_url)
            await upload_video(context, chat_id, file_path, "📸 Here's your Instagram video!")
            await msg.edit_text("✅ Done! Enjoy 🎉")
        except Exception as exc:
            logger.exception("Instagram error")
            await msg.edit_text(f"❌ Failed:\n`{exc}`", parse_mode="Markdown")
        finally:
            if file_path:
                cleanup(file_path)
        return

    # ── Facebook ───────────────────────────────────────────────────────────────
    fb_url = extract_fb_url(text)
    if fb_url:
        msg = await update.message.reply_text("⏳ Downloading Facebook video…")
        file_path = None
        try:
            file_path = await asyncio.to_thread(download_facebook, fb_url)
            await upload_video(context, chat_id, file_path, "📘 Here's your Facebook video!")
            await msg.edit_text("✅ Done! Enjoy 🎉")
        except Exception as exc:
            logger.exception("Facebook error")
            await msg.edit_text(f"❌ Failed:\n`{exc}`", parse_mode="Markdown")
        finally:
            if file_path:
                cleanup(file_path)
        return

    # ── YouTube ────────────────────────────────────────────────────────────────
    yt_url = extract_yt_url(text)
    if yt_url:
        context.user_data["yt_url"] = yt_url
        title = await asyncio.to_thread(get_yt_title, yt_url)
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
        "⚠️ Please send a valid YouTube, Instagram, or Facebook link."
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, fmt = query.data.split(":")
    url = context.user_data.get("yt_url")

    if not url:
        await query.edit_message_text("⚠️ Session expired. Send the link again.")
        return

    want_audio = (fmt == "audio")
    await query.edit_message_text(
        f"⏳ Downloading {'audio 🎵' if want_audio else 'video 🎬'}… please wait."
    )

    file_path = None
    try:
        file_path = await asyncio.to_thread(smart_download, url, want_audio)
    except Exception as exc:
        logger.exception("YT download error")
        await query.edit_message_text(f"❌ Download failed:\n`{exc}`", parse_mode="Markdown")
        return

    await query.edit_message_text("📤 Uploading to Telegram…")
    try:
        chat_id = query.message.chat_id
        if want_audio:
            await upload_audio(context, chat_id, file_path, "🎵 Here's your audio!")
        else:
            await upload_video(context, chat_id, file_path, "🎬 Here's your video!")
        await query.edit_message_text("✅ Done! Enjoy 🎉")
    except Exception as exc:
        logger.exception("Upload error")
        await query.edit_message_text(f"❌ Upload failed:\n`{exc}`", parse_mode="Markdown")
    finally:
        if file_path:
            cleanup(file_path)

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
