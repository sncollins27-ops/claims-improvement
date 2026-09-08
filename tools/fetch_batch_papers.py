"""Download the PDFs of a mainnet batch/run so they can be replayed locally.

    python -m tools.fetch_batch_papers --run-id run_20260907_121647_21873c --limit 6
    python -m tools.fetch_batch_papers --batch-id batch_20260907_71b95ea13aa3 --positions 1,2,3

PDFs land in papers/<paper_id>.pdf and a manifest in papers/<batch_id>.json
(paper ids, titles, urls, sha256). Use with tools/run_batch.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.claims111.ai"


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run-id")
    group.add_argument("--batch-id")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--positions", default="", help="comma-separated 1-based positions to fetch (overrides --limit)")
    parser.add_argument("--out", type=Path, default=ROOT / "papers")
    args = parser.parse_args()

    batch_id = args.batch_id
    if args.run_id:
        run = requests.get(f"{API}/public/runs/{args.run_id}", timeout=60).json()
        batch_id = run.get("batch_id")
        if not batch_id:
            print("run has no batch_id", file=sys.stderr)
            return 1
    batch = requests.get(f"{API}/public/batches/{batch_id}", timeout=60).json()
    papers = sorted(batch.get("papers") or [], key=lambda p: p.get("position") or 0)
    if args.positions:
        wanted = {int(x) for x in args.positions.split(",") if x.strip()}
        papers = [p for p in papers if p.get("position") in wanted]
    else:
        papers = papers[: args.limit]
    args.out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for paper in papers:
        paper_id = paper["paper_id"]
        target = args.out / f"{paper_id}.pdf"
        url = paper.get("source_url")
        if not url:
            print(f"skip {paper_id}: no source_url")
            continue
        if not target.exists():
            response = requests.get(url, timeout=180, headers={"User-Agent": "claims-subnet/0.1"})
            response.raise_for_status()
            target.write_bytes(response.content)
        sha = hashlib.sha256(target.read_bytes()).hexdigest()
        manifest.append({"paper_id": paper_id, "title": paper.get("title"), "url": url, "pdf": str(target), "sha256": sha, "position": paper.get("position")})
        print(f"{paper.get('position'):>3} {paper_id} {target.stat().st_size // 1024} KB  {paper.get('title', '')[:80]}")
    manifest_path = args.out / f"{batch_id}.json"
    manifest_path.write_text(json.dumps({"batch_id": batch_id, "papers": manifest}, indent=2), encoding="utf-8")
    print(f"manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
