"""Score our artifacts against the REAL Silver units of a mainnet run.

`tools/fetch_silver_units.py` lifts the actual units (statement, importance,
scoring mode) out of the public dashboard. This tool asks, unit by unit,
whether one of our claims expresses it, then runs the validator's own
`score_miner_against_silver` over the result.

This supersedes the Bronze reconstruction in `tools/estimate_coverage.py`:
no proxy, no simulated competitors, the real denominator including every
accepted-improvement unit other miners contributed.

The one thing it still cannot model is the units our own accepted claims would
have added had we actually competed. Those raise our coverage and lower every
other miner's, so the number here is conservative in that respect.

    python -m tools.score_against_real_silver --run-name v5-50paper --run-id run_20260907_063159_471c07
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from miner.agent_v1.staged.grounding import jaccard  # noqa: E402
from miner.agent_v1.staged.llm import LLMClient  # noqa: E402
from validator.agent_v1.comparison_models import SilverRecord, SilverUnit  # noqa: E402
from validator.agent_v1.record_projection import project_agent_artifact  # noqa: E402
from validator.agent_v1.silver_scoring import score_miner_against_silver  # noqa: E402

API = "https://api.claims111.ai"
SAME_UNIT = {"semantic_equivalent", "compatible_refinement", "compatible_split_merge"}

JUDGE_SYSTEM = """You decide, for each Silver unit, whether the submission would be pooled into that unit by the validator's canonicalizer.

A Silver unit is a CANONICAL MERGED statement. The validator groups every claim that expresses the unit's proposition, or one of the specific findings the unit summarises, into that single unit: real units routinely merge 20 to 140 separate claims. A unit such as "knockdown inhibits proliferation, colony formation, migration and invasion, induces arrest and apoptosis" is the merge of many individual assay results.

So the question is NOT whether one submission claim restates the whole composite. It is whether at least one submission claim would be merged into this unit, meaning it asserts:
- the unit's core proposition, or
- one of the specific findings, assays, cell lines, markers, cohorts or endpoints the unit summarises, in the same direction.

Relations:
- semantic_equivalent: a submission states the unit's proposition.
- compatible_refinement: a submission states the proposition with added supported specificity.
- compatible_split_merge: submissions state the unit's content divided across several claims, or one component of the merged unit.
- partial_overlap: shares a material proposition but asserts a different direction, population or outcome.
- none: no submission asserts any part of the unit's scientific content.

Report the single best-matching submission id. Return STRICT JSON:
{"units": [{"unit_id": "u0", "best_submission_id": "s12" or null, "relation": "semantic_equivalent|compatible_refinement|compatible_split_merge|partial_overlap|none"}]}
One entry per Silver unit, no omissions."""


def _get(path: str, **params: Any) -> Any:
    response = requests.get(f"{API}{path}", params=params, timeout=90)
    response.raise_for_status()
    return response.json()


def judge_units(
    client: LLMClient,
    paper_id: str,
    units: list[dict[str, Any]],
    ours: list[Any],
    *,
    passes: int,
    chunk_size: int,
) -> dict[str, dict[str, Any]]:
    """Majority vote over `passes` independent judgements, batching units into chunks."""
    submission = [{"submission_id": f"s{i}", "statement": c.statement} for i, c in enumerate(ours)]
    votes: dict[str, list[str]] = {}
    best: dict[str, str] = {}
    for start in range(0, len(units), chunk_size):
        chunk = units[start : start + chunk_size]
        payload_units = [{"unit_id": f"u{start + i}", "statement": unit["statement"]} for i, unit in enumerate(chunk)]
        for attempt in range(passes):
            try:
                result, _usage = client.complete_json(
                    system=JUDGE_SYSTEM,
                    user=json.dumps({"silver_units": payload_units, "submission_claims": submission}, ensure_ascii=False),
                    stage="real_silver_judge",
                    max_tokens=16000,
                    label=f"{paper_id}:{start}:p{attempt}",
                )
            except Exception:
                continue
            for row in result.get("units", []) or []:
                if not isinstance(row, dict):
                    continue
                unit_id = str(row.get("unit_id") or "")
                if not unit_id:
                    continue
                votes.setdefault(unit_id, []).append(str(row.get("relation") or "none"))
                submission_id = str(row.get("best_submission_id") or "")
                if submission_id and unit_id not in best:
                    best[unit_id] = submission_id
    decided: dict[str, dict[str, Any]] = {}
    for unit_id, relations in votes.items():
        same = sum(1 for relation in relations if relation in SAME_UNIT)
        decided[unit_id] = {
            "matched": same * 2 > len(relations),
            "votes": f"{same}/{len(relations)}",
            "best_submission_id": best.get(unit_id, ""),
        }
    return decided


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--silver-dir", type=Path, default=ROOT / "runs" / "silver_real")
    parser.add_argument("--network", default="mainnet")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--passes", type=int, default=3)
    parser.add_argument("--chunk-size", type=int, default=25)
    parser.add_argument("--max-units", type=int, default=0, help="cap units per paper (0 = all); the tail is minor-tier")
    parser.add_argument("--model", default="")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    import os

    client = LLMClient(
        model=args.model or os.getenv("SUBNET_CLAIMS_JUDGE_MODEL") or "openrouter/openai/gpt-5-mini",
        api_key=os.getenv("OPENROUTER_API_KEY", ""),
        api_base=os.getenv("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1"),
        provider="openrouter",
        reasoning_effort="low",
    )

    ranking = sorted(_get(f"/public/runs/{args.run_id}/batch-scores", network=args.network), key=lambda b: b.get("rank") or 99)
    real_rows: dict[str, list[dict[str, Any]]] = {}
    for row in _get(f"/public/runs/{args.run_id}/silver-scores", network=args.network):
        real_rows.setdefault(row["paper_id"], []).append(row)

    silver_dir = args.silver_dir / args.run_id
    jobs = []
    for path in sorted(silver_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        paper_id = data["paper_id"]
        ours_path = ROOT / "runs" / args.run_name / paper_id / "agent_output.json"
        if data.get("units") and ours_path.exists():
            jobs.append((paper_id, data, ours_path))
    if not jobs:
        print("no papers with both real Silver units and our artifact", file=sys.stderr)
        return 1

    def work(job: tuple[str, dict[str, Any], Path]) -> dict[str, Any]:
        paper_id, data, ours_path = job
        units = data["units"]
        if args.max_units and len(units) > args.max_units:
            order = {"central": 0, "supporting": 1, "minor": 2}
            units = sorted(units, key=lambda u: order.get(str(u.get("importance")), 3))[: args.max_units]
        ours = project_agent_artifact(json.loads(ours_path.read_text(encoding="utf-8")), origin="miner", miner_id="uid_192")
        decided = judge_units(client, paper_id, units, ours, passes=args.passes, chunk_size=args.chunk_size)

        silver_units: list[SilverUnit] = []
        matched = 0
        for index, unit in enumerate(units):
            verdict = decided.get(f"u{index}") or {}
            equivalent = [f"silver_anchor_{index}"]
            if verdict.get("matched"):
                submission_id = str(verdict.get("best_submission_id") or "")
                position = int(submission_id[1:]) if submission_id.startswith("s") and submission_id[1:].isdigit() else -1
                if not 0 <= position < len(ours):
                    scored = [(jaccard(unit["statement"] or "", other.statement), i) for i, other in enumerate(ours)]
                    position = max(scored)[1] if scored else -1
                if 0 <= position < len(ours):
                    equivalent.append(ours[position].candidate_id)
                    matched += 1
            silver_units.append(
                SilverUnit(
                    silver_unit_id=unit.get("silver_unit_id") or f"unit_{index}",
                    paper_id=paper_id,
                    statement=unit.get("statement") or "",
                    importance=str(unit.get("importance") or "supporting"),  # type: ignore[arg-type]
                    required_for_completeness=bool(unit.get("required_for_completeness", True)),
                    equivalent_candidate_ids=equivalent,
                    scoring_mode=str(unit.get("scoring_mode") or "required"),  # type: ignore[arg-type]
                )
            )
        record = SilverRecord(silver_record_id=data.get("silver_record_id") or paper_id, paper_id=paper_id, silver_units=silver_units)
        breakdown = score_miner_against_silver(miner_id="uid_192", miner_candidates=ours, silver_record=record, normal_findings=[])
        evaluation_path = ours_path.parent / "evaluation.json"
        quality = json.loads(evaluation_path.read_text(encoding="utf-8")).get("quality_estimate") if evaluation_path.exists() else None
        rows = real_rows.get(paper_id, [])
        scoring = [r["coverage"] for r in rows if r["score"] > 0]
        return {
            "paper_id": paper_id,
            "units": len(units),
            "units_total": len(data["units"]),
            "matched": matched,
            "coverage": breakdown.coverage,
            "quality": quality,
            "score": round(breakdown.coverage * quality, 4) if isinstance(quality, int | float) else None,
            "importance_mix": dict(Counter(str(u.get("importance")) for u in units)),
            "real_coverage_median_scoring": round(statistics.median(scoring), 4) if scoring else None,
            "real_coverage_max": round(max(r["coverage"] for r in rows), 4) if rows else None,
            "real_score_max": round(max(r["score"] for r in rows), 4) if rows else None,
        }

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        results = list(executor.map(work, jobs))

    scores = [r["score"] for r in results if r["score"] is not None]
    coverages = [r["coverage"] for r in results]
    estimated = round(statistics.mean(scores), 4) if scores else 0.0
    beat = [r for r in results if r["real_coverage_median_scoring"] is not None and r["coverage"] > r["real_coverage_median_scoring"]]
    summary = {
        "run_id": args.run_id,
        "run_name": args.run_name,
        "papers": len(results),
        "silver_units_total": sum(r["units_total"] for r in results),
        "silver_units_judged": sum(r["units"] for r in results),
        "units_matched": sum(r["matched"] for r in results),
        "mean_coverage": round(statistics.mean(coverages), 4) if coverages else 0.0,
        "estimated_batch_score": estimated,
        "papers_beating_median_miner": len(beat),
        "real_ranking": [{k: b.get(k) for k in ("rank", "uid", "batch_score", "winner")} for b in ranking],
        "judge_passes": args.passes,
        "judge_usage": client.usage_summary(),
        "papers_detail": results,
        "caveats": [
            "Silver units are the real ones for this run, read from the public dashboard.",
            "Units our own accepted claims would have added are not modelled; they would raise our coverage and lower other miners'.",
            "Matching is judged by an LLM over N passes, not by the validator's own comparator models.",
        ],
    }
    out = ROOT / "runs" / args.run_name / "real_silver_score.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"papers                 : {summary['papers']}")
    print(f"real Silver units      : {summary['silver_units_total']} total, {summary['silver_units_judged']} judged, {summary['units_matched']} matched")
    print(f"mean coverage          : {summary['mean_coverage']}")
    print(f"estimated batch score  : {estimated}")
    winner = next((b for b in ranking if b.get("winner")), None)
    if winner:
        placing = sum(1 for b in ranking if (b.get("batch_score") or 0) > estimated) + 1
        print(f"real winner            : uid {winner['uid']} at {winner['batch_score']:.4f}  ->  we would place {placing} of {len(ranking) + 1}")
    print(f"beats median miner on  : {len(beat)} of {len(results)} papers")
    print()
    print(f"{'paper':26} {'units':>6} {'match':>6} {'our cov':>8} {'real med':>8} {'qual':>5} {'score':>6}  mix")
    for row in sorted(results, key=lambda r: r["coverage"]):
        median = f"{row['real_coverage_median_scoring']:.3f}" if row["real_coverage_median_scoring"] is not None else "-"
        print(f"{row['paper_id'][:26]:26} {row['units']:>6} {row['matched']:>6} {row['coverage']:>8.3f} {median:>8} "
              f"{str(row['quality'])[:5]:>5} {str(row['score'])[:6]:>6}  {row['importance_mix']}")
    print(f"\njudge cost ${client.usage_summary().get('cost_usd', 0):.3f}; wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
