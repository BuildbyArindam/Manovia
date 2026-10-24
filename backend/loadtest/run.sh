#!/usr/bin/env bash
# Day 26: start the API on a throwaway SQLite file with the Fake LLM, wait until
# it is ready, run Locust headless, stop the API. Usage:
#   loadtest/run.sh [users] [spawn-rate] [duration]   (defaults: 100 20 2m)
# Needs the backend installed (pip install -e ".[dev]") and `pip install -r
# loadtest/requirements.txt`. Nothing here touches a real database or provider.
set -euo pipefail
cd "$(dirname "$0")/.."
USERS="${1:-100}"; RATE="${2:-20}"; DURATION="${3:-2m}"
WORK="$(mktemp -d)"; mkdir -p loadtest/out; trap 'kill "${SERVER_PID:-0}" 2>/dev/null || true; cp "$WORK/server.log" loadtest/out/server.log 2>/dev/null || true; rm -rf "$WORK"' EXIT
export APP_ENV=development LLM_PROVIDER=fake RATE_LIMIT_ENABLED=false
export SECRET_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')"
export FIELD_ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')"
export DATABASE_URL="sqlite+aiosqlite:///$WORK/load.db"
alembic upgrade head >/dev/null
uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log >"$WORK/server.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 60); do
  curl -fs http://127.0.0.1:8000/api/v1/ready >/dev/null && break
  sleep 1
done
curl -fs http://127.0.0.1:8000/api/v1/ready >/dev/null || { echo "API did not become ready"; tail -20 "$WORK/server.log"; exit 1; }
mkdir -p loadtest/out
locust -f loadtest/locustfile.py --headless -u "$USERS" -r "$RATE" -t "$DURATION" \
  --host http://127.0.0.1:8000 --csv loadtest/out/run
