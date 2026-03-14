FROM python:3.12-slim

# Install system dependencies including Node.js 20 (required by bgutil)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install bgutil POT provider plugin for yt-dlp
# This fixes "Sign in to confirm you're not a bot" and format errors on cloud IPs
RUN pip install --no-cache-dir bgutil-ytdlp-pot-provider

# Install and build the bgutil Node.js server
RUN npm install -g @ybd-project/bgutil-ytdlp-pot-provider

# Build the bgutil server scripts
RUN bgutil-ytdlp-pot-provider || true

COPY bot.py .
COPY start.sh .
RUN chmod +x start.sh

CMD ["./start.sh"]
