#!/bin/bash
REPO=/opt/tenderview/tender-pipeline
cd /opt/tenderview || exit 0
docker compose cp "$REPO/src/publish.py" app:/tmp/publish.py >/dev/null 2>&1
docker compose cp "$REPO/src/fx_rate.py" app:/tmp/fx_rate.py >/dev/null 2>&1
docker compose exec -T app python /tmp/publish.py >> /opt/tenderview/publish.log 2>&1
