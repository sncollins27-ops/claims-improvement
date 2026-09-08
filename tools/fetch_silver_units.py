"""Fetch the real Silver units for a run's papers from the public dashboard.

The Claims API publishes per-miner coverage numbers but never the Silver unit
text, and `/admin/runs/{run_id}/silver-records` needs an operator token. The
public dashboard at dashboard.claims111.ai does render the units, and its
Next.js page server-renders them into the response when the run and paper are
named as query parameters:

    https://dashboard.claims111.ai/runs?run_id=<run_id>&paper_id=<paper_id>

This reads that page and lifts the `silver_records` payload out of the React
Server Component stream. No login, no clicking, no private endpoint: the same
bytes the browser already receives.

    python -m tools.fetch_silver_units --run-id run_20260907_063159_471c07

Writes runs/silver_real/<paper_id>.json (unit statements, importance, scoring
mode and the candidate ids merged into each unit).
"""

from __future__ import annotations

import argparse
import codecs
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = "https://dashboard.claims111.ai/runs"
API = "https://api.claims111.ai"
CHUNK = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', re.DOTALL)


def _rsc_payload(html: str) -> str:
    return "".join(codecs.decode(chunk, "unicode_escape", "replace") for chunk in CHUNK.findall(html))


def _balanced(text: str, start: int) -> str:
    """Return the JSON array/object starting at `start`, respecting strings and escapes."""
    opener = text[start]
    closer = {"[": "]", "{": "}"}[opener]
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise ValueError("unbalanced JSON in RSC payload")


def fetch_units(run_id: str, paper_id: str, *, timeout: float = 120.0) -> dict[str, Any]:
    response = requests.get(DASHBOARD, params={"run_id": run_id, "paper_id": paper_id}, timeout=timeout)
    response.raise_for_status()
    payload = _rsc_payload(response.text)
    marker = payload.find('"silver_records":[')
    if marker < 0:
        return {"paper_id": paper_id, "run_id": run_id, "units": [], "error": "no silver_records in page"}
    records = json.loads(_balanced(payload, payload.index("[", marker)))
    matching = [r for r in records if r.get("paper_id") == paper_id and r.get("run_id") == run_id]
    if not matching:
        got = sorted({f"{r.get('run_id')}/{r.get('paper_id')}" for r in records})
        return {"paper_id": paper_id, "run_id": run_id, "units": [], "error": f"page returned other records: {got[:3]}"}
    record = matching[0]
    units = [
        {
            "silver_unit_id": unit.get("silver_unit_id"),
            "statement": unit.get("statement"),
            "importance": unit.get("importance"),
            "required_for_completeness": unit.get("required_for_completeness"),
            "scoring_mode": unit.get("scoring_mode"),
            "candidate_count": len(unit.get("equivalent_candidate_ids") or []),
            "source_span_ids": unit.get("source_span_ids") or [],
            "source_quotes": (unit.get("source_quotes") or [])[:5],
        }
        for unit in record.get("silver_units") or []
    ]
    return {
        "paper_id": paper_id,
        "run_id": run_id,
        "silver_record_id": record.get("silver_record_id"),
        "bronze_record_id": record.get("bronze_record_id"),
        "units": units,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--paper-id", action="append", default=[])
    parser.add_argument("--network", default="mainnet")
    parser.add_argument("--out", type=Path, default=ROOT / "runs" / "silver_real")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    paper_ids = list(args.paper_id)
    if not paper_ids:
        rows = requests.get(f"{API}/public/runs/{args.run_id}/silver-scores", params={"network": args.network}, timeout=90).json()
        paper_ids = sorted({row["paper_id"] for row in rows})
    out = args.out / args.run_id
    out.mkdir(parents=True, exist_ok=True)

    def work(paper_id: str) -> tuple[str, str]:
        target = out / f"{paper_id}.json"
        if target.exists() and not args.force:
            data = json.loads(target.read_text(encoding="utf-8"))
            return paper_id, f"cached ({len(data.get('units') or [])} units)"
        try:
            data = fetch_units(args.run_id, paper_id)
        except Exception as exc:
            return paper_id, f"failed: {type(exc).__name__}: {exc}"
        target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        if data.get("error"):
            return paper_id, f"no units: {data['error']}"
        mix: dict[str, int] = {}
        for unit in data["units"]:
            mix[str(unit.get("importance"))] = mix.get(str(unit.get("importance")), 0) + 1
        return paper_id, f"{len(data['units'])} units {mix}"

    print(f"fetching real Silver units for {len(paper_ids)} papers of {args.run_id}")
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        for paper_id, status in executor.map(work, paper_ids):
            print(f"  {paper_id:28} {status}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
