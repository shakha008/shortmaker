FROM python:3.12-slim

# ffmpeg video kesish uchun
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# YouTube uchun yt-dlp ga JavaScript muhiti (deno) kerak
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV HOST=0.0.0.0
# Vazifalar holati xotirada saqlanadi, shuning uchun BITTA worker va ko'p thread
CMD gunicorn app:app --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 8 --timeout 300
