import os
import re
import logging
import asyncio
import tempfile
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
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

def extract_yt(text): m = YT_RE.search(text); return m.group(0) if m else None
def extract_ig(text): m = INSTAGRAM_RE.search(text); return m.group(0) if m else None
def extract_fb(text): m = FACEBOOK_RE.search(text); return m.group(0) if m else None

# ── YouTube downloader ────────────────────────────────────────────────────────
def yt_download(url: str, want_audio: bool) -> str:
    """
    Download using web client + bgutil POT provider (running on port 4416).
    bgutil generates PO tokens automatically — this is the only reliable
    method for cloud server IPs as of 2025.
    """
    tmpdir = tempfile.mkdtemp()
    opts = {
        "quiet": True,
        "noplaylist": True,
        "outtmpl": os.path.join(tmpdir, "%(title)s.%(ext)s"),
        # Use web client — bgutil POT plugin handles the token automatically
        "extractor_args": {
            "youtube": {
                "player_client": ["web"],
            }
        },
        **get_cookie_opts(),
    }

    if want_audio:
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    else:
        opts["format"] = "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best[height<=720]/best"
        opts["merge_output_format"] = "mp4"

    logger.info("Downloading YT [%s]: %s", "audio" if want_audio else "video", url)
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    files = os.listdir(tmpdir)
    if not files:
        raise FileNotFoundError("No file downloaded.")
    return os.path.join(tmpdir, files[0])

def yt_title(url: str) -> str:
    try:
        opts = {
            "quiet": True, "skip_download": True,
            "extractor_args": {"youtube": {"player_client": ["web"]}},
            **get_cookie_opts(),
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get("title", "Video")[:60]
    except Exception:
        return "Video"

# ── Instagram downloader ──────────────────────────────────────────────────────
def ig_download(url: str) -> str:
    tmpdir = tempfile.mkdtemp()
    opts = {"quiet": True, "outtmpl": os.path.join(tmpdir, "%(title)s.%(ext)s"), "format": "best[ext=mp4]/best"}
    cookie_str = os.environ.get("INSTAGRAM_COOKIES", "").strip()
    if cookie_str:
        tmp = "/tmp/ig_cookies.txt"
        with open(tmp, "w") as f: f.write(cookie_str)
        opts["cookiefile"] = tmp
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    files = os.listdir(tmpdir)
    if not files: raise FileNotFoundError("No file downloaded.")
    return os.path.join(tmpdir, files[0])

# ── Facebook downloader ───────────────────────────────────────────────────────
def fb_download(url: str) -> str:
    bad = ["?_fb_noscript", "facebook.com/?", "facebook.com/#"]
    for b in bad:
        if b in url:
            raise ValueError(
                "Facebook redirected to homepage. Please share the direct video link "
                "(URL should contain /watch, /reel, or /videos)."
            )
    tmpdir = tempfile.mkdtemp()
    opts = {
        "quiet": True,
        "outtmpl": os.path.join(tmpdir, "%(title)s.%(ext)s"),
        "format": "best[ext=mp4]/best",
        "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36"},
    }
    cookie_str = os.environ.get("FACEBOOK_COOKIES", "").strip()
    if cookie_str:
        tmp = "/tmp/fb_cookies.txt"
        with open(tmp, "w") as f: f.write(cookie_str)
        opts["cookiefile"] = tmp
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    files = os.listdir(tmpdir)
    if not files: raise FileNotFoundError("No file downloaded.")
    return os.path.join(tmpdir, files[0])

# ── Upload helpers ────────────────────────────────────────────────────────────
async def send_video(ctx, chat_id, path, caption):
    with open(path, "rb") as f:
        await ctx.bot.send_video(chat_id=chat_id, video=f, supports_streaming=True,
                                  caption=caption, read_timeout=300, write_timeout=300)

async def send_audio(ctx, chat_id, path, caption):
    with open(path, "rb") as f:
        await ctx.bot.send_audio(chat_id=chat_id, audio=f,
                                  caption=caption, read_timeout=300, write_timeout=300)

def cleanup(p):
    try: os.remove(p)
    except: pass

# ── Handlers ──────────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Welcome to Downloader Bot!*\n\n"
        "📺 *YouTube* — Video (MP4) or Audio (MP3)\n"
        "📸 *Instagram* — Reels & Posts\n"
        "📘 *Facebook* — Videos & Reels\n\n"
        "_Paste any link to get started!_",
        parse_mode="Markdown",
    )

async def handle_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    chat_id = update.message.chat_id

    # Instagram
    ig_url = extract_ig(text)
    if ig_url:
        msg = await update.message.reply_text("⏳ Downloading Instagram video…")
        fp = None
        try:
            fp = await asyncio.to_thread(ig_download, ig_url)
            await send_video(ctx, chat_id, fp, "📸 Here's your Instagram video!")
            await msg.edit_text("✅ Done! Enjoy 🎉")
        except Exception as e:
            await msg.edit_text(f"❌ Failed:\n`{e}`", parse_mode="Markdown")
        finally:
            if fp: cleanup(fp)
        return

    # Facebook
    fb_url = extract_fb(text)
    if fb_url:
        msg = await update.message.reply_text("⏳ Downloading Facebook video…")
        fp = None
        try:
            fp = await asyncio.to_thread(fb_download, fb_url)
            await send_video(ctx, chat_id, fp, "📘 Here's your Facebook video!")
            await msg.edit_text("✅ Done! Enjoy 🎉")
        except Exception as e:
            await msg.edit_text(f"❌ Failed:\n`{e}`", parse_mode="Markdown")
        finally:
            if fp: cleanup(fp)
        return

    # YouTube
    yt_url = extract_yt(text)
    if yt_url:
        ctx.user_data["yt_url"] = yt_url
        title = await asyncio.to_thread(yt_title, yt_url)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🎬 Video (MP4)", callback_data="dl:video"),
            InlineKeyboardButton("🎵 Audio (MP3)", callback_data="dl:audio"),
        ]])
        await update.message.reply_text(f"🎯 *{title}*\n\nChoose format:", reply_markup=kb, parse_mode="Markdown")
        return

    await update.message.reply_text("⚠️ Please send a valid YouTube, Instagram, or Facebook link.")

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, fmt = query.data.split(":")
    url = ctx.user_data.get("yt_url")
    if not url:
        await query.edit_message_text("⚠️ Session expired. Send the link again.")
        return

    want_audio = fmt == "audio"
    await query.edit_message_text(f"⏳ Downloading {'audio 🎵' if want_audio else 'video 🎬'}… please wait.")

    fp = None
    try:
        fp = await asyncio.to_thread(yt_download, url, want_audio)
    except Exception as e:
        await query.edit_message_text(f"❌ Download failed:\n`{e}`", parse_mode="Markdown")
        return

    await query.edit_message_text("📤 Uploading to Telegram…")
    try:
        chat_id = query.message.chat_id
        if want_audio:
            await send_audio(ctx, chat_id, fp, "🎵 Here's your audio!")
        else:
            await send_video(ctx, chat_id, fp, "🎬 Here's your video!")
        await query.edit_message_text("✅ Done! Enjoy 🎉")
    except Exception as e:
        await query.edit_message_text(f"❌ Upload failed:\n`{e}`", parse_mode="Markdown")
    finally:
        if fp: cleanup(fp)

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
