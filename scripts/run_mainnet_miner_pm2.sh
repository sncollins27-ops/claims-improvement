#!/usr/bin/env bash
# PM2 wrapper: keep one miner process, back off on crash so Finney is not 429'd.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
delay=20
max_delay=180

while true; do
  "$ROOT/scripts/run_mainnet_miner.sh" "$@"
  code=$?
  echo "miner exited code=$code at $(date -u +%Y-%m-%dT%H:%M:%SZ); retry in ${delay}s"
  sleep "$delay"
  delay=$(( delay * 2 ))
  if (( delay > max_delay )); then
    delay=$max_delay
  fi
done
