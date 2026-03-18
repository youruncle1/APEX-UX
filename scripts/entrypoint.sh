#!/bin/sh
set -e

echo "[entrypoint] Running database initialization check..."
python scripts/ensure_db.py

echo "[entrypoint] Starting application..."
exec uvicorn src.api.main:app --host 0.0.0.0 --port "${APP_PORT:-8002}" --reload