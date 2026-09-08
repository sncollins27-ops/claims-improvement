#!/usr/bin/env bash
# Local miner (staged runtime by default). Reads .env. Does not print secrets.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "missing venv: $ROOT/.venv  (create with python3.12 -m venv .venv && pip install -r requirements.txt)" >&2
  exit 1
fi
if [[ ! -f "$ROOT/.env" ]]; then
  echo "missing $ROOT/.env" >&2
  exit 1
fi

# Load .env into this process only. python -m miner.agent_v1 also load_dotenv's it.
set -a
# shellcheck disable=SC1091
source "$ROOT/.env"
set +a

if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
  echo "OPENROUTER_API_KEY is empty in .env" >&2
  exit 1
fi

PDF="${1:-$ROOT/papers/openalex_w4415340077.pdf}"
OUT="${2:-$ROOT/runs/local/$(basename "${PDF%.*}")}"

if [[ ! -f "$PDF" ]]; then
  echo "missing PDF: $PDF" >&2
  exit 1
fi

mkdir -p "$OUT"
CACHE_DIR="${DSPY_CACHEDIR:-/tmp/dspy_cache_claims_gpt41mini}"
rm -rf "$CACHE_DIR"
mkdir -p "$CACHE_DIR"

export PYTHONUNBUFFERED=1
export SUBNET_CLAIMS_AGENT_RUNTIME="${SUBNET_CLAIMS_AGENT_RUNTIME:-staged}"

echo "miner start $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "runtime=$SUBNET_CLAIMS_AGENT_RUNTIME"
echo "model=${SUBNET_CLAIMS_AGENT_MODEL:-$OPENROUTER_MODEL}"
echo "pdf=$PDF"
echo "output=$OUT"

exec "$ROOT/.venv/bin/python" -m miner.agent_v1 \
  --pdf "$PDF" \
  --runtime "$SUBNET_CLAIMS_AGENT_RUNTIME" \
  --output-dir "$OUT"
