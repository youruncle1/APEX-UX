#!/usr/bin/env bash
# Dumps current DB data into scripts/initdb/seed.sql so the supervisor
# gets a pre-seeded database on first `docker compose up`.
#
# Run this once while your postgres container is running:
#   bash scripts/export_seed.sh

set -e

CONTAINER="${DB_CONTAINER:-apex-postgres}"
DB_USER="${DB_USER:-apex}"
DB_NAME="${DB_NAME:-apex_ux}"
OUT="$(dirname "$0")/initdb/seed.sql"

echo "Dumping $DB_NAME from container $CONTAINER..."

docker exec "$CONTAINER" pg_dump \
  -U "$DB_USER" \
  "$DB_NAME" \
  --no-owner \
  --no-privileges \
  --no-comments \
  > "$OUT"

echo "Done. Seed written to $OUT"
echo "Commit scripts/initdb/seed.sql and the supervisor can just run: docker compose up"
