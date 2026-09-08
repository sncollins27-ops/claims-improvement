"""Run the agent_v1 miner on one paper and evaluate the result locally.

Examples:
    python -m tools.run_paper --pdf papers/openalex_w4415340077.pdf --paper-id openalex_w4415340077 \
        --title "Single-cell atlas of ..." --run-name staged-v2 --judge
    python -m tools.run_paper --url https://.../paper.pdf --paper-id openalex_w123 --run-name baseline --runtime dspy-react

Outputs land in runs/<run-name>/<paper_id>/ (agent_output.json, staged_trace.json,
backend_manifest.json, evaluation.json, ...). The dashboard reads that tree.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from miner.agent_v1.config import AgentV1Config  # noqa: E402
from miner.agent_v1.runner import AgentV1Runner  # noqa: E402
from neurons.tasks import download_pdf  # noqa: E402
from tools.evaluate import evaluate_run, print_summary  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one paper through agent_v1 and evaluate it.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pdf", type=Path)
    source.add_argument("--url")
    parser.add_argument("--paper-id", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--run-name", required=True, help="Experiment name, e.g. staged-v2 or baseline-dspy")
    parser.add_argument("--runtime", default="", help="staged | dspy-react | langchain-agent | agent-cli (default from .env)")
    parser.add_argument("--model", default="", help="Override SUBNET_CLAIMS_AGENT_MODEL")
    parser.add_argument("--skill-dir", type=Path)
    parser.add_argument("--judge", action="store_true", help="Run the LLM self-judge after extraction")
    parser.add_argument("--judge-model", default="")
    parser.add_argument("--note", default="", help="Free-text note stored in run_meta.json")
    parser.add_argument("--force", action="store_true", help="Re-run even if agent_output.json exists")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    config = AgentV1Config.from_env(ROOT)
    if args.runtime:
        config.runtime = args.runtime
    if args.model:
        config.model = args.model
    if args.skill_dir:
        config.skill_dir = args.skill_dir

    if args.url:
        downloads = ROOT / "papers" / "downloads"
        downloaded = download_pdf(args.url, output_dir=downloads)
        pdf_path = downloaded.path
        source_sha = downloaded.sha256
    else:
        pdf_path = args.pdf.resolve()
        source_sha = ""
    paper_id = args.paper_id or pdf_path.stem
    run_dir = ROOT / "runs" / args.run_name / paper_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "run_name": args.run_name,
        "paper_id": paper_id,
        "title": args.title,
        "pdf": str(pdf_path),
        "url": args.url,
        "runtime": config.runtime,
        "model": config.model,
        "skill_dir": str(config.skill_dir),
        "note": args.note,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (run_dir / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    if args.force or not (run_dir / "agent_output.json").exists():
        started = time.perf_counter()
        runner = AgentV1Runner(config)
        try:
            runner.run_from_pdf(
                pdf_path,
                output_dir=run_dir,
                paper_override={"paper_id": paper_id, "title": args.title, "source_url": args.url or "", "source_sha256": source_sha},
            )
            meta["status"] = "completed"
        except Exception as exc:
            meta["status"] = "failed"
            meta["error"] = f"{type(exc).__name__}: {exc}"
            logging.exception("miner run failed")
        meta["wall_seconds"] = round(time.perf_counter() - started, 3)
        meta["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        (run_dir / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    else:
        print(f"agent_output.json exists in {run_dir}; evaluating only (use --force to re-run)")

    report = evaluate_run(run_dir, judge=args.judge, judge_model=args.judge_model)
    print_summary(report)
    return 0 if meta.get("status", "completed") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
