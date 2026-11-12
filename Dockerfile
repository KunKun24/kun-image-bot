# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai \
    DB_PATH=/data/image_bot.db \
    SAVE_DIR=/data/generated_images

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --uid 10001 app \
 && mkdir -p /data/generated_images \
 && chown -R app:app /app /data

USER app
VOLUME ["/data"]
EXPOSE 5003

CMD ["python", "main.py"]
