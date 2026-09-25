#!/usr/bin/env bash
# Starts everything needed to use the dashboard: Colima (if needed),
# Postgres + Grafana via docker-compose, then the telemetry API in the
# foreground. Ctrl+C stops the API; Postgres/Grafana keep running
# (docker-compose down to stop those too).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

colima status >/dev/null 2>&1 || colima start

docker-compose up -d

echo "Waiting for Postgres..."
until docker-compose exec -T postgres pg_isready -U telemetry >/dev/null 2>&1; do
  sleep 1
done
echo "Postgres is up. Grafana: http://localhost:3000 (admin/admin on a fresh volume)"

cd server
DATABASE_URL="postgresql://telemetry:telemetry@localhost:5433/telemetry" \
  .venv/bin/uvicorn app.main:app --port 8000
