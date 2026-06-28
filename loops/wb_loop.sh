#!/bin/bash
# Фоновый WB-проход: запускает wb_pass.py отдельным контейнером wb_worker.
# Анти-двойной-запуск: если воркер уже жив — выходим. Сам завершается, когда
# обработает свою пачку. По расписанию добивает остаток за несколько заходов.
REPO=/opt/tenderview/tender-pipeline
LOG=/opt/tenderview/wb_loop.log
PG=Tv7xK9pQm2wLrB
cd /opt/tenderview || exit 0

# уже идёт? выходим
if [ "$(docker inspect -f '{{.State.Running}}' wb_worker 2>/dev/null)" = "true" ]; then
  exit 0
fi
docker rm wb_worker >/dev/null 2>&1

# есть ли что обрабатывать (живые WB-EXACT без цены, попыток < 2)?
NEED=$(docker compose exec -T -e PGPASSWORD=$PG db psql -U tender -d tender -tAc \
  "SELECT count(*) FROM tenders WHERE match_status='FOUND_EXACT' AND COALESCE(found_url, match_result->>'source_url') ILIKE '%wildberries.ru/catalog/%' AND is_closed=false AND (match_result->>'price') IS NULL AND COALESCE((match_result->>'wb_tries')::int,0) < 2;" \
  2>/dev/null | tr -d '[:space:]')
[ -z "$NEED" ] && exit 0
[ "$NEED" = "0" ] && { echo "===== $(date) нечего обрабатывать (простой) =====" >> "$LOG"; exit 0; }

echo "===== $(date) WB-проход: $NEED ждут =====" >> "$LOG"
docker run -d --name wb_worker --network tenderview_default \
  -v "$REPO":/app -v wbcache:/root/.cache/ms-playwright -w /app \
  -e DATABASE_URL=postgresql://tender:$PG@db:5432/tender \
  -e WB_LIMIT=40 -e WB_ATTEMPTS=2 -e WB_MAX_TRIES=2 -e PYTHONUNBUFFERED=1 \
  python:3.12-slim bash -c \
  "pip install -q playwright psycopg2-binary >/dev/null 2>&1 && playwright install chromium >/dev/null 2>&1 && playwright install-deps chromium >/dev/null 2>&1 && python src/wb_pass.py" \
  >> "$LOG" 2>&1
