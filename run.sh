#!/usr/bin/env bash
# One-command dashboard: builds the Vue app (if needed) and starts the sidecar
# which serves it at http://localhost:8795 together with /api.
set -euo pipefail
cd "$(dirname "$0")"
PY="${CIJ_PYTHON:-.venv/bin/python}"
[ -x "$PY" ] || { echo "python venv missing: $PY (python3.12 -m venv .venv && uv pip install --python .venv/bin/python -r requirements.txt fastapi uvicorn)" >&2; exit 1; }
if [ ! -d dashboard/node_modules ]; then (cd dashboard && npm install --no-audit --no-fund); fi
if [ ! -f dashboard/dist/index.html ] || [ "${1:-}" = "--build" ]; then (cd dashboard && npm run build:fast); fi
exec "$PY" server/app.py --host "${CIJ_HOST:-0.0.0.0}" --port "${CIJ_PORT:-8795}"
