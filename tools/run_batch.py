"""Run several papers concurrently, the way the neuron does, and evaluate each.

    python -m tools.run_batch --manifest papers/batch_20260907_71b95ea13aa3.json --run-name staged-v3-batch --workers 4 --judge
    python -m tools.run_batch --pdf papers/a.pdf papers/b.pdf --run-name quick --workers 2

Reports per-paper claims/quality/cost/time and the wall-clock for the whole
batch, which is what matters for the validator's one-hour deadline
(50 papers / CLAIMS_MINER_BATCH_MAX_WORKERS workers).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from miner.agent_v1.config import AgentV1Config  # noqa: E402
from miner.agent_v1.runner import AgentV1Runner  # noqa: E402
from tools.evaluate import evaluate_run  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, help="papers/<batch>.json from tools.fetch_batch_papers")
    parser.add_argument("--pdf", nargs="*", type=Path, default=[])
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--runtime", default="")
    parser.add_argument("--judge", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    config = AgentV1Config.from_env(ROOT)
    if args.runtime:
        config.runtime = args.runtime

    items: list[dict] = []
    if args.manifest:
        for row in json.loads(args.manifest.read_text(encoding="utf-8")).get("papers", []):
            items.append({"paper_id": row["paper_id"], "title": row.get("title") or "", "pdf": Path(row["pdf"]), "url": row.get("url") or "", "sha256": row.get("sha256") or ""})
    for pdf in args.pdf:
        items.append({"paper_id": pdf.stem, "title": "", "pdf": pdf, "url": "", "sha256": ""})
    if args.limit:
        items = items[: args.limit]
    if not items:
        print("nothing to run", file=sys.stderr)
        return 1

    runner = AgentV1Runner(config)
    batch_started = time.perf_counter()

    def work(item: dict) -> dict:
        run_dir = ROOT / "runs" / args.run_name / item["paper_id"]
        run_dir.mkdir(parents=True, exist_ok=True)
        meta = {**{k: (str(v) if isinstance(v, Path) else v) for k, v in item.items()}, "run_name": args.run_name, "runtime": config.runtime, "model": config.model, "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        started = time.perf_counter()
        if args.force or not (run_dir / "agent_output.json").exists():
            try:
                runner.run_from_pdf(item["pdf"], output_dir=run_dir, paper_override={"paper_id": item["paper_id"], "title": item["title"], "source_url": item["url"], "source_sha256": item["sha256"]})
                meta["status"] = "completed"
            except Exception as exc:
                meta["status"] = "failed"
                meta["error"] = f"{type(exc).__name__}: {exc}"
                logging.exception("paper failed: %s", item["paper_id"])
        else:
            meta["status"] = "completed"
        meta["wall_seconds"] = round(time.perf_counter() - started, 3)
        meta["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        (run_dir / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        report = evaluate_run(run_dir, judge=args.judge)
        stats = report.get("stats") or {}
        return {"paper_id": item["paper_id"], "status": meta["status"], "claims": stats.get("claims"), "quality": report.get("quality_estimate"), "cost": stats.get("cost_usd"), "seconds": meta["wall_seconds"], "judge": (report.get("judge") or {}).get("verdicts")}

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        rows = list(executor.map(work, items))
    wall = time.perf_counter() - batch_started
    print(f"\n{'paper':28} {'status':10} {'claims':>6} {'quality':>8} {'cost':>7} {'sec':>6}  judge")
    for row in rows:
        print(f"{row['paper_id']:28} {row['status']:10} {str(row['claims']):>6} {str(row['quality']):>8} {str(row['cost'])[:7]:>7} {row['seconds']:>6.0f}  {row['judge']}")
    completed = [r for r in rows if r["status"] == "completed"]
    extraction_seconds = [r["seconds"] for r in rows if r["status"] == "completed"]
    avg = sum(extraction_seconds) / max(1, len(extraction_seconds))
    slowest = max(extraction_seconds, default=0.0)
    print(f"\nbatch: {len(completed)}/{len(rows)} completed; wall {wall:.0f}s including judging with {args.workers} workers")
    print(f"extraction only: avg {avg:.0f}s/paper, slowest {slowest:.0f}s -> projected 50 papers at 8 workers: ~{avg * 50 / 8 / 60:.0f} min (validator deadline 60 min)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
