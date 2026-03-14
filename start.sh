#!/bin/bash
set -e

echo "=== Starting bgutil POT HTTP server ==="
# Start bgutil HTTP server in background on port 4416
# This generates YouTube PO tokens automatically for yt-dlp
npx --yes @ybd-project/bgutil-ytdlp-pot-provider server &
BGU_PID=$!
echo "bgutil server started (PID $BGU_PID)"

# Give it 3 seconds to be ready
sleep 3

echo "=== Starting Telegram bot ==="
exec python bot.py
