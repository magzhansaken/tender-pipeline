#!/bin/bash
# Синхронизация статусов лотов с активной выдачей goszakup.
# Держит is_closed в точном соответствии с goszakup (лот в активном списке ->
# живой; пропал -> закрыт). Защита от сбоя — внутри sync_status.py (не трогает
# базу при неполной выдаче). Анти-двойной-запуск через docker inspect.
REPO=/opt/tenderview/tender-pipeline
LOG=/opt/tenderview/sync_loop.log
PG=Tv7xK9pQm2wLrB
cd /opt/tenderview || exit 0

if [ "$(docker inspect -f '{{.State.Running}}' sync_worker 2>/dev/null)" = "true" ]; then
  exit 0
fi
docker rm sync_worker >/dev/null 2>&1

echo "===== $(date) sync_status старт =====" >> "$LOG"
docker run -d --name sync_worker --network tenderview_default \
  -e DATABASE_URL=postgresql://tender:$PG@db:5432/tender \
  -v "$REPO":/app -w /app \
  python:3.12-slim bash -c \
  "pip install -q requests beautifulsoup4 psycopg2-binary >/dev/null 2>&1 && python sync_status.py" \
  >> "$LOG" 2>&1
