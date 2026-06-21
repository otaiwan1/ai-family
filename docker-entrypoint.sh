#!/bin/sh
set -eu

DB_PATH="${QUESTIONS_DB_PATH:-/data/questions_db.json}"
BACKUP_PATH="${QUESTIONS_BACKUP_DIR:-/data/backups}"

mkdir -p "$(dirname "$DB_PATH")" "$BACKUP_PATH"

if [ ! -s "$DB_PATH" ]; then
  cp /app/questions_db.seed.json "$DB_PATH"
  echo "[container] Seeded question database at $DB_PATH"
fi

export QUESTIONS_DB_PATH="$DB_PATH"
export QUESTIONS_BACKUP_DIR="$BACKUP_PATH"

exec "$@"
