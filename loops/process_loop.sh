#!/bin/bash
REPO=/opt/tenderview/tender-pipeline
LOG=/opt/tenderview/ollama_loop.log
PG=Tv7xK9pQm2wLrB
cd /opt/tenderview || exit 0
# защита от двойного запуска: если воркер уже жив — выходим
if [ "$(docker inspect -f '{{.State.Running}}' ollama_worker 2>/dev/null)" = "true" ]; then
  exit 0
fi
docker rm ollama_worker >/dev/null 2>&1
# есть ли что обрабатывать?
NEED=$(docker compose exec -T -e PGPASSWORD=$PG db psql -U tender -d tender -tAc "SELECT count(*) FROM tenders WHERE structured_spec IS NULL AND stage='collected';" 2>/dev/null | tr -d '[:space:]')
[ -z "$NEED" ] && exit 0
[ "$NEED" = "0" ] && exit 0
echo "===== $(date) старт: $NEED необработанных =====" >> "$LOG"
docker run -d --name ollama_worker --network tenderview_default \
  -v "$REPO":/app -w /app --env-file /opt/tenderview/.env \
  -e DATABASE_URL=postgresql://tender:$PG@db:5432/tender -e PYTHONUNBUFFERED=1 \
  python:3.12-slim bash -c "pip install -q ollama asyncpg && python src/process_specs.py" >> "$LOG" 2>&1
