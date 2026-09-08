#!/usr/bin/env bash
# Watch a local miner.agent_v1 process and its output directory.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/outputs/my_run_validation}"
PID_FILE="$OUT/miner.pid"
LOG="$OUT/miner.log"
INTERVAL="${MONITOR_INTERVAL_SECONDS:-10}"

mkdir -p "$OUT"

resolve_pid() {
  local pid=""
  if [[ -f "$PID_FILE" ]]; then
    pid="$(tr -d '[:space:]' < "$PID_FILE" || true)"
  fi
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "$pid"
    return 0
  fi
  pgrep -n -f 'python -m miner.agent_v1' 2>/dev/null || true
}

print_snapshot() {
  local pid="$1"
  echo "======== $(date -u +%Y-%m-%dT%H:%M:%SZ) ========"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    ps -p "$pid" -o pid,etime,pcpu,pmem,rss,stat --no-headers || true
    ss -tpn 2>/dev/null | grep "pid=$pid" || echo "no sockets"
  else
    echo "miner process not running"
  fi
  echo "--- output files ---"
  if [[ -d "$OUT" ]]; then
    find "$OUT" -type f -printf '%T+ %s %p\n' 2>/dev/null | sort | tail -n 20
  fi
  if [[ -f "$OUT/agent_output.json" ]]; then
    "$ROOT/.venv/bin/python" - "$OUT/agent_output.json" <<'PY' 2>/dev/null || echo "agent_output.json not valid JSON yet"
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
raw = p.read_text(encoding="utf-8")
print(f"agent_output.json bytes={len(raw)}")
try:
    d = json.loads(raw)
except Exception as exc:
    print(f"INCOMPLETE_JSON {type(exc).__name__}")
    raise SystemExit(0)
claims = (d.get("logic") or {}).get("claims") or []
evidence = ((d.get("logic") or {}).get("evidence") or {}).get("records") or d.get("evidence") or {}
if isinstance(evidence, dict):
    records = evidence.get("records") or []
else:
    records = evidence if isinstance(evidence, list) else []
print(f"VALID_JSON claims={len(claims)} evidence={len(records)}")
PY
  fi
  if [[ -f "$OUT/agent_validation_report.json" ]]; then
    echo "--- validation ---"
    "$ROOT/.venv/bin/python" -c "import json; p='$OUT/agent_validation_report.json'; d=json.load(open(p)); print(d)" 2>/dev/null || true
  fi
  if [[ -f "$LOG" ]]; then
    echo "--- log tail ---"
    tail -n 8 "$LOG"
  fi
  echo
}

echo "monitoring $OUT (interval ${INTERVAL}s)"
idle=0
while true; do
  pid="$(resolve_pid)"
  print_snapshot "$pid"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    idle=0
  else
    idle=$((idle + 1))
    # Wait a bit after spawn so a slow start is not treated as finished.
    if [[ "$idle" -ge 3 ]]; then
      echo "miner stopped. final snapshot above."
      if [[ -f "$OUT/manifest.json" ]]; then
        echo "--- manifest ---"
        cat "$OUT/manifest.json"
      fi
      exit 0
    fi
  fi
  sleep "$INTERVAL"
done
