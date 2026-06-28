#!/bin/bash
# ════════════════════════════════════════════════════════════════
#  TenderView — деплой репозитория в боевую папку /opt/tenderview
#  Раскладывает web/, loops/, deploy/ по местам, где их ждут
#  Docker и cron, затем пересобирает веб-приложение.
#
#  .env НЕ ТРОГАЕТСЯ (секреты остаются на сервере).
#  Запускать из корня репозитория: bash deploy/sync.sh
# ════════════════════════════════════════════════════════════════
set -e

RT=/opt/tenderview                       # боевая папка (runtime): логи, .env, сборка
REPO="$(cd "$(dirname "$0")/.." && pwd)" # корень репозитория (где лежит этот скрипт)

echo "Репозиторий: $REPO"
echo "Боевая папка: $RT"
echo ""

if [ ! -f "$RT/.env" ]; then
  echo "⚠️  ВНИМАНИЕ: $RT/.env не найден. Секреты должны лежать там. Прерываю."
  exit 1
fi

echo "→ Веб-приложение (app.py, load_results.py, static)..."
cp "$REPO/web/app.py"          "$RT/app.py"
cp "$REPO/web/load_results.py" "$RT/load_results.py"
mkdir -p "$RT/static"
cp "$REPO/web/static/index.html" "$RT/static/index.html"
cp "$REPO/web/static/admin.html" "$RT/static/admin.html"

echo "→ Конфигурация деплоя (Dockerfile, compose, requirements, Caddyfile, schema)..."
cp "$REPO/deploy/Dockerfile"                   "$RT/Dockerfile"
cp "$REPO/deploy/docker-compose.yml"           "$RT/docker-compose.yml"
cp "$REPO/deploy/docker-compose.override.yml"  "$RT/docker-compose.override.yml"
cp "$REPO/deploy/requirements.txt"             "$RT/requirements.txt"
cp "$REPO/deploy/Caddyfile"                    "$RT/Caddyfile"
cp "$REPO/deploy/schema.sql"                   "$RT/schema.sql"

echo "→ Cron-обёртки (loops)..."
cp "$REPO"/loops/*.sh "$RT"/
chmod +x "$RT"/*.sh

echo "→ Пересборка веб-приложения..."
cd "$RT"
docker compose up -d --build app

echo ""
echo "✅ Деплой завершён."
echo "   Проверь: витрина https://\$DOMAIN/ , панель https://\$DOMAIN/admin"
echo "   Воркеры пайплайна (src/) запускаются из $REPO через cron — git pull обновляет их без пересборки."
