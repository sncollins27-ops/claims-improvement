#!/usr/bin/env bash
# Local 50-paper test of the deployed v5 miner, on a real mainnet batch
# (batch_20260907_d7b6080b73fb, the 06:31 UTC run of 2026-09-07 - a different
# batch from the 5 papers used during development).
#
#   ./scripts/run_50_paper_test.sh              # 8 workers, no judge  (~40 min, ~$11)
#   ./scripts/run_50_paper_test.sh 8 --judge    # 8 workers + LLM self-judge (~50 min, ~$14)
#   ./scripts/run_50_paper_test.sh 4            # gentler on the provider and on the live miner
#   RUN_NAME=v6-50paper ./scripts/run_50_paper_test.sh 8 --judge   # a named experiment
#
# Results land in runs/v5-50paper/<paper_id>/ and show up in the dashboard
# (Runs -> v5-50paper). Re-running skips papers that already have an
# agent_output.json; pass --force to redo them.
set -euo pipefail
cd "$(dirname "$0")/.."

WORKERS="${1:-8}"
shift || true
# Never overwrite a previous experiment: the run name is a parameter, not a constant.
RUN_NAME="${RUN_NAME:-v5-50paper}"

MANIFEST="papers/batch_20260907_d7b6080b73fb.json"
if [ ! -f "$MANIFEST" ]; then
  echo ">> fetching the 50 paper PDFs first (about 240 MB)"
  .venv/bin/python -m tools.fetch_batch_papers --batch-id batch_20260907_d7b6080b73fb --limit 50
fi

echo "50-paper local test"
echo "  manifest : $MANIFEST"
echo "  workers  : $WORKERS   (in-flight provider calls = workers x SUBNET_CLAIMS_STAGED_EXTRACT_CONCURRENCY)"
echo "  runtime  : ${SUBNET_CLAIMS_AGENT_RUNTIME:-staged}   model: ${SUBNET_CLAIMS_AGENT_MODEL:-from .env}"
echo "  output   : runs/$RUN_NAME/"
echo

exec .venv/bin/python -m tools.run_batch \
  --manifest "$MANIFEST" \
  --run-name "$RUN_NAME" \
  --workers "$WORKERS" \
  "$@"
