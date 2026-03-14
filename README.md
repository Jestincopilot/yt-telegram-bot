# 🎬 YouTube Downloader Telegram Bot

A Telegram bot that lets users download YouTube videos as **MP4 (video)** or **MP3 (audio)** with a single tap.

---

## ✨ Features

- Send any YouTube link → get two buttons: **Video (MP4)** and **Audio (MP3)**
- Downloads and delivers the file directly in Telegram
- Hosted 24/7 on Railway (free tier)

---

## 📁 Project Structure

```
yt-telegram-bot/
├── bot.py            # Main bot code
├── requirements.txt  # Python dependencies
├── Dockerfile        # Container setup (includes ffmpeg)
├── railway.toml      # Railway deployment config
└── README.md
```

---

## 🚀 Deployment Guide (Railway — Free, 24/7)

### STEP 1 — Create your Telegram Bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Enter a name (e.g. `My YT Downloader`)
4. Enter a username ending in `bot` (e.g. `myytdl_bot`)
5. BotFather gives you a **token** like:
   ```
   7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```
   **Save this token — you'll need it in Step 3.**

---

### STEP 2 — Upload code to GitHub

1. Go to [github.com](https://github.com) → **New repository**
2. Name it `yt-telegram-bot` → Create (Public or Private, both work)
3. Upload all 5 files from this folder:
   - `bot.py`
   - `requirements.txt`
   - `Dockerfile`
   - `railway.toml`
   - `.gitignore`

   *(Drag & drop them on the GitHub repo page → Commit changes)*

---

### STEP 3 — Deploy on Railway

1. Go to [railway.app](https://railway.app) and sign up with GitHub
2. Click **"New Project"** → **"Deploy from GitHub repo"**
3. Select your `yt-telegram-bot` repo
4. Railway will detect the Dockerfile automatically
5. Click on your service → go to **"Variables"** tab
6. Add this environment variable:
   ```
   TELEGRAM_BOT_TOKEN = 7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```
   *(Paste your token from Step 1)*
7. Click **Deploy** — Railway builds and starts the bot!

---

### STEP 4 — Verify it's working

1. Open Telegram → search for your bot's username
2. Send `/start`
3. Paste any YouTube URL (e.g. `https://youtu.be/dQw4w9WgXcQ`)
4. Two buttons appear → tap **Video** or **Audio**
5. Bot downloads and sends the file! 🎉

---

## ⚙️ Environment Variables

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Your bot token from BotFather (required) |

---

## 📝 Notes

- **File size limit**: Telegram bots can send files up to **50 MB**. Very long videos may exceed this.
- **Free tier**: Railway gives 500 hours/month free — enough for 24/7 for one project.
- The bot uses `yt-dlp` (best YouTube downloader) + `ffmpeg` for audio conversion.

---

## 🛠 Local Testing (optional)

```bash
# Install dependencies
pip install -r requirements.txt
# Install ffmpeg: https://ffmpeg.org/download.html

# Set your token
export TELEGRAM_BOT_TOKEN="your_token_here"

# Run
python bot.py
```
