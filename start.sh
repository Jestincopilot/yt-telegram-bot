#!/bin/bash
set -e

echo "=== Starting bgutil POT HTTP server on port 4416 ==="
cd /bgutil/server
node dist/server.js &
BGU_PID=$!
echo "bgutil server PID: $BGU_PID"

# Wait for server to be ready
sleep 4

echo "=== Starting Telegram bot ==="
cd /app
exec python bot.py
