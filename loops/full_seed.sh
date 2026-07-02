#!/bin/bash
# full_seed.sh — «ПОЛНЫЙ ЗАСЕВ»: разово залить в базу ВСЕ активные лоты goszakup за один
# проход (для холодного старта после обнуления, или чтобы быстро догнать backlog).
#
# Это НЕ новая логика — это тот же src/daily_sync.py, запущенный один раз с большим MAX_NEW.
# Штатный cron (daily_sync_loop.sh, MAX_NEW=400 каждые 4 часа) НЕ трогается.
#
# Что делает:
#   • тянет всю активную выдачу (статус 240), принимает при полноте ≥ SYNC_MIN_RATIO;
#   • добавляет ВСЕ новые лоты (до MAX_NEW=5000) с их ТЗ из вложений;
#   • дальше воркеры сами нормализуют (MATCHING_MODE) и ищут (VERIFY_MODE).
#
# Запуск на сервере:
#   bash /opt/tenderview/full_seed.sh            # засев с MAX_NEW=5000
#   MAX_NEW=8000 bash /opt/tenderview/full_seed.sh   # если лотов больше
#
# Прогресс:
#   docker logs -f full_seed_worker
#   docker compose exec -T -e PGPASSWORD=$PG db psql -U tender -d tender -tAc "SELECT count(*) FROM tenders;"

set -euo pipefail
REPO=/opt/tenderview/tender-pipeline
PG=Tv7xK9pQm2wLrB
MAX_NEW="${MAX_NEW:-5000}"          # разово снимаем ограничение порции
NAME=full_seed_worker

cd /opt/tenderview || exit 1

# не запускать второй засев поверх идущего
if [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" = "true" ]; then
  echo "⚠️ Полный засев уже идёт (контейнер $NAME). Смотри: docker logs -f $NAME"
  exit 0
fi
docker rm -f "$NAME" >/dev/null 2>&1 || true

echo "🌱 Полный засев запущен: MAX_NEW=$MAX_NEW (тянет все активные лоты за один проход)."
echo "   Это медленно (~1-2 сек/лот из-за ТЗ во вложениях): ~3500 лотов ≈ 30-60 мин."
docker run -d --name "$NAME" --network tenderview_default \
  -e DATABASE_URL="postgresql://tender:${PG}@db:5432/tender" \
  -e MAX_NEW="$MAX_NEW" \
  -v "$REPO":/app -w /app \
  python:3.12-slim bash -c \
  "pip install -q requests beautifulsoup4 pdfplumber python-docx asyncpg >/dev/null 2>&1 && python src/daily_sync.py"

echo "✅ Контейнер $NAME запущен в фоне."
echo "   Прогресс:  docker logs -f $NAME"
echo "   Счётчик:   docker compose exec -T -e PGPASSWORD=$PG db psql -U tender -d tender -tAc \"SELECT count(*) FROM tenders;\""
