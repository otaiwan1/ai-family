#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-8000}"

cd "$ROOT_DIR/client"
npm run build

if [[ -x "$ROOT_DIR/server/venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/server/venv/bin/python"
elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
else
  PYTHON_BIN="python"
fi

cd "$ROOT_DIR/server"
PORT="$PORT" "$PYTHON_BIN" app.py
