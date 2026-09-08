#!/usr/bin/env bash
# SN111 mainnet miner. Reads .env. Does not print secrets. Intended for PM2.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "missing venv: $ROOT/.venv" >&2
  exit 1
fi
if [[ ! -f "$ROOT/.env" ]]; then
  echo "missing $ROOT/.env" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source "$ROOT/.env"
set +a

if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
  echo "OPENROUTER_API_KEY is empty in .env" >&2
  exit 1
fi

wallet_name="${BT_WALLET_NAME:-}"
wallet_hotkey="${BT_WALLET_HOTKEY:-}"
case "${wallet_name}" in
  ""|"TYPE_WALLET_NAME"|"MINER_WALLET"|"miner_wallet"|"claims-test-miner")
    echo "Set BT_WALLET_NAME in .env to your registered SN111 wallet name." >&2
    exit 2
    ;;
esac
case "${wallet_hotkey}" in
  ""|"TYPE_HOTKEY"|"HOTKEY"|"default")
    echo "Set BT_WALLET_HOTKEY in .env to your registered SN111 hotkey name." >&2
    exit 2
    ;;
esac

external_ip="${BT_AXON_EXTERNAL_IP:-}"
if [[ -z "${external_ip}" ]]; then
  echo "BT_AXON_EXTERNAL_IP is empty in .env" >&2
  exit 1
fi

netuid="${BT_NETUID:-111}"
network="${BT_SUBTENSOR_NETWORK:-finney}"
axon_ip="${BT_AXON_IP:-0.0.0.0}"
axon_port="${BT_AXON_PORT:-8091}"
axon_external_port="${BT_AXON_EXTERNAL_PORT:-${axon_port}}"
workers="${CLAIMS_MINER_BATCH_MAX_WORKERS:-8}"
model="${SUBNET_CLAIMS_AGENT_MODEL:-openrouter/openai/gpt-5-mini}"
harness="${SUBNET_CLAIMS_AGENT_HARNESS:-staged}"
out_dir="${CLAIMS_MINER_OUTPUT_DIR:-runs/neuron/mainnet}"
backend_url="${CLAIMS_BACKEND_URL:-https://artifacts.claims111.ai}"

mkdir -p "$out_dir"
mkdir -p "${DSPY_CACHEDIR:-/tmp/dspy_cache_claims_gpt5}"

export PYTHONUNBUFFERED=1

extra=()
if [[ "${1:-}" == "--dry-run" ]]; then
  extra+=(--claims.dry-run)
fi

echo "mainnet miner start $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "netuid=$netuid network=$network"
echo "axon.bind=$axon_ip:$axon_port external=${external_ip}:${axon_external_port}"
echo "harness=$harness model=$model workers=$workers"
echo "wallet.name=$wallet_name wallet.hotkey=$wallet_hotkey"
echo "output=$out_dir backend=$backend_url"

exec "$ROOT/.venv/bin/python" -m dotenv -f "$ROOT/.env" run --override -- \
  "$ROOT/.venv/bin/python" -m neurons.miner \
  --netuid "$netuid" \
  --wallet.name "$wallet_name" \
  --wallet.hotkey "$wallet_hotkey" \
  --subtensor.network "$network" \
  --axon.ip "$axon_ip" \
  --axon.external_ip "$external_ip" \
  --axon.port "$axon_port" \
  --axon.external_port "$axon_external_port" \
  --claims.pipeline agent_v1 \
  --claims.agent-harness "$harness" \
  --claims.agent-model "$model" \
  --claims.pdf-extraction-method "${SUBNET_CLAIMS_PDF_READER:-pdf-inspector}" \
  --claims.batch-max-workers "$workers" \
  --claims.backend-url "$backend_url" \
  --claims.output-dir "$out_dir" \
  --logging.info \
  "${extra[@]}"
