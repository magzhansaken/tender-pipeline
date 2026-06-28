#!/bin/bash
# Фоновый Alibaba-проход: запускает alibaba_pass.py отдельным контейнером
# alibaba_worker. Анти-двойной-запуск: если воркер уже жив — выходим (важно:
# проход может идти долго из-за пауз 15-20с между попытками; cron просто
# пропустит запуск, пока предыдущий не закончит). По расписанию ловит «окна»
# Alibaba по вероятности и капает оптовые ориентиры.
REPO=/opt/tenderview/tender-pipeline
LOG=/opt/tenderview/alibaba_loop.log
PG=Tv7xK9pQm2wLrB
cd /opt/tenderview || exit 0

# уже идёт? выходим
if [ "$(docker inspect -f '{{.State.Running}}' alibaba_worker 2>/dev/null)" = "true" ]; then
  exit 0
fi
docker rm alibaba_worker >/dev/null 2>&1

# есть ли что обрабатывать (живые подобранные без цены, ali-попыток < 5)?
NEED=$(docker compose exec -T -e PGPASSWORD=$PG db psql -U tender -d tender -tAc \
  "SELECT count(*) FROM tenders WHERE match_status IN ('FOUND_EXACT','FOUND_PARTIAL') AND is_closed=false AND (deadline IS NULL OR deadline >= now()) AND (match_result->>'price') IS NULL AND COALESCE((match_result->>'ali_tries')::int,0) < 5;" \
  2>/dev/null | tr -d '[:space:]')
[ -z "$NEED" ] && exit 0
[ "$NEED" = "0" ] && { echo "===== $(date) нечего обрабатывать (простой) =====" >> "$LOG"; exit 0; }

echo "===== $(date) Alibaba-проход: $NEED ждут =====" >> "$LOG"
docker run -d --name alibaba_worker --network tenderview_default \
  --env-file /opt/tenderview/.env \
  -v "$REPO":/app -w /app \
  -e DATABASE_URL=postgresql://tender:$PG@db:5432/tender \
  -e ALI_HARD_CAP=30 -e ALI_RETRIES=8 -e ALI_MAX_TRIES=5 -e PYTHONUNBUFFERED=1 \
  python:3.12-slim bash -c \
  "pip install -q curl_cffi requests psycopg2-binary ollama >/dev/null 2>&1 && python src/alibaba_pass.py" \
  >> "$LOG" 2>&1
