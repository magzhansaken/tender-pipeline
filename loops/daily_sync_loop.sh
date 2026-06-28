#!/bin/bash
# Единый проход сверки с goszakup: новые лоты + синхронизация статусов.
# Заменяет старый ненадёжный CSV-сбор. Защита от неполной выдачи — внутри
# daily_sync.py. Анти-двойной-запуск через docker inspect.
REPO=/opt/tenderview/tender-pipeline
LOG=/opt/tenderview/daily_sync_loop.log
PG=Tv7xK9pQm2wLrB
cd /opt/tenderview || exit 0

if [ "$(docker inspect -f '{{.State.Running}}' daily_sync_worker 2>/dev/null)" = "true" ]; then
  exit 0
fi
docker rm daily_sync_worker >/dev/null 2>&1

echo "===== $(date) daily_sync старт =====" >> "$LOG"
docker run -d --name daily_sync_worker --network tenderview_default \
  -e DATABASE_URL=postgresql://tender:$PG@db:5432/tender \
  -v "$REPO":/app -w /app \
  python:3.12-slim bash -c \
  "pip install -q requests beautifulsoup4 pdfplumber python-docx asyncpg >/dev/null 2>&1 && python src/daily_sync.py" \
  >> "$LOG" 2>&1
