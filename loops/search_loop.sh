#!/bin/bash
REPO=/opt/tenderview/tender-pipeline
LOG=/opt/tenderview/search_loop.log
PG=Tv7xK9pQm2wLrB
cd /opt/tenderview || exit 0
if [ "$(docker inspect -f '{{.State.Running}}' search_worker 2>/dev/null)" = "true" ]; then
  exit 0
fi
docker rm search_worker >/dev/null 2>&1
NEED=$(docker compose exec -T -e PGPASSWORD=$PG db psql -U tender -d tender -tAc "SELECT count(*) FROM tenders WHERE stage='parsed';" 2>/dev/null | tr -d '[:space:]')
[ -z "$NEED" ] && exit 0
[ "$NEED" = "0" ] && { echo "===== $(date) нечего обрабатывать (простой) =====" >> "$LOG"; exit 0; }
echo "===== $(date) старт поиска: $NEED ждут =====" >> "$LOG"
docker run -d --name search_worker --network tenderview_default \
  -v "$REPO":/app -w /app --env-file /opt/tenderview/.env \
  -e DATABASE_URL=postgresql://tender:$PG@db:5432/tender -e PYTHONUNBUFFERED=1 \
  python:3.12-slim bash -c "pip install -q ddgs ollama asyncpg && python src/search_verify.py" >> "$LOG" 2>&1
