FROM python:3.12-slim

# Install system deps + Node.js 20
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl gnupg git \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python deps ───────────────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── bgutil POT provider (Python plugin) ──────────────────────────────────────
RUN pip install --no-cache-dir bgutil-ytdlp-pot-provider

# ── bgutil POT server (Node.js HTTP server on port 4416) ─────────────────────
# Clone the repo at the matching version tag, build the TypeScript server
RUN git clone --depth 1 --branch 1.3.1 \
    https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git \
    /bgutil

WORKDIR /bgutil/server
RUN npm ci && npx tsc

# Back to app dir
WORKDIR /app

COPY bot.py .
COPY start.sh .
RUN chmod +x start.sh

CMD ["./start.sh"]
