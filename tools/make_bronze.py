"""Reconstruct the validator's Bronze reference extraction for a set of papers.

Bronze is what the subnet's own reference miner produces, and every Bronze
claim becomes a *required* Silver unit (see `silver_builder.build_silver_record`).
So reconstructing Bronze gives us the backbone of the Silver record that
already existed for a batch, independent of anything we would contribute.

Fidelity notes, because this is an approximation and should be read as one:

- It runs the public `claims-reference-miner` package against the untouched
  Claims checkout, so the pipeline, the stock compiler skill and the prompts
  are the real ones.
- Each paper runs in a subprocess with `cwd` set to that checkout and a minimal
  environment, so this repo's staged runtime and coverage skill cannot leak in.
- The validator picks its own reference model privately. We use a named model
  here, so unit counts will differ somewhat from the real run.

    python -m tools.make_bronze --papers-from runs/v5-50paper/comparison.json --workers 5
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLAIMS_REPO = Path(os.getenv("CLAIMS_REFERENCE_MINER_CLAIMS_REPO", str(ROOT.parent / "Claims")))

CHILD = """
import json, os, sys
from pathlib import Path
from claims_reference_miner.config import ReferenceMinerConfig
from claims_reference_miner.runner import run_reference_miner

config = ReferenceMinerConfig(
    claims_repo=Path(sys.argv[1]),
    output_dir=Path(sys.argv[2]),
    paper_id=sys.argv[3],
    runtime="dspy-react",
    model=sys.argv[4],
    pdf_reader="pdf-inspector",
    api_key=os.environ["OPENROUTER_API_KEY"],
    api_base=os.environ.get("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1"),
    temperature=1.0,          # reasoning models reject 0.0
    max_tokens=16384,         # and require >= 16000
    max_agent_iters=6,
    max_repair_attempts=2,
)
manifest = run_reference_miner(
    input_path=Path(sys.argv[5]),
    input_kind="pdf",
    output_dir=Path(sys.argv[2]) / sys.argv[3],
    config=config,
)
print("BRONZE_OK", manifest.bronze_record_id if hasattr(manifest, "bronze_record_id") else "")
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--papers-from", type=Path, help="comparison.json; uses the papers the validator scored")
    parser.add_argument("--paper-id", action="append", default=[])
    parser.add_argument("--out", type=Path, default=ROOT / "runs" / "bronze")
    parser.add_argument("--model", default=os.getenv("CLAIMS_BRONZE_MODEL", "openrouter/openai/gpt-5-mini"))
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    # The child runs with cwd set to the Claims checkout, so a relative output path
    # would write into that repo. Always resolve against this repo instead.
    args.out = args.out if args.out.is_absolute() else (ROOT / args.out).resolve()

    paper_ids = list(args.paper_id)
    if args.papers_from:
        data = json.loads(args.papers_from.read_text(encoding="utf-8"))
        paper_ids += [row["paper_id"] for row in data.get("papers", [])]
    paper_ids = [pid for pid in dict.fromkeys(paper_ids) if (ROOT / "papers" / f"{pid}.pdf").exists()]
    if not paper_ids:
        print("no papers with local PDFs", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)

    key = os.getenv("OPENROUTER_API_KEY") or _key_from_env_file()
    if not key:
        print("OPENROUTER_API_KEY not found", file=sys.stderr)
        return 1
    env = {
        "OPENROUTER_API_KEY": key,
        "OPENROUTER_API_BASE": os.getenv("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1"),
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "DSPY_CACHEDIR": "/tmp/dspy_cache_bronze",
    }

    def work(paper_id: str) -> tuple[str, str, float]:
        target = args.out / paper_id / "agent_output.json"
        if target.exists() and not args.force:
            return paper_id, "cached", 0.0
        started = time.perf_counter()
        result = subprocess.run(
            [str(ROOT / ".venv" / "bin" / "python"), "-c", CHILD, str(CLAIMS_REPO), str(args.out), paper_id, args.model, str(ROOT / "papers" / f"{paper_id}.pdf")],
            cwd=str(CLAIMS_REPO), env=env, capture_output=True, text=True, timeout=2400,
        )
        elapsed = time.perf_counter() - started
        if result.returncode != 0 or not target.exists():
            (args.out / paper_id).mkdir(parents=True, exist_ok=True)
            (args.out / paper_id / "error.txt").write_text((result.stdout + "\n" + result.stderr)[-4000:], encoding="utf-8")
            return paper_id, f"failed: {result.stderr.strip().splitlines()[-1][:120] if result.stderr.strip() else 'no output'}", elapsed
        claims = len(((json.loads(target.read_text(encoding='utf-8')).get("logic") or {}).get("claims")) or [])
        return paper_id, f"ok ({claims} bronze claims)", elapsed

    print(f"reconstructing Bronze for {len(paper_ids)} papers with {args.model} ({args.workers} workers)")
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        for paper_id, status, elapsed in executor.map(work, paper_ids):
            print(f"  {paper_id:28} {status:34} {elapsed:6.0f}s")
    return 0


def _key_from_env_file() -> str:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("OPENROUTER_API_KEY="):
            return line.split("=", 1)[1].strip()
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
