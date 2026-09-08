"""Compare a local experiment against the real Silver scores of a mainnet run.

    python -m tools.compare_silver --run-name v5-50paper --run-id run_20260907_063159_471c07

Joins runs/<run-name>/<paper_id>/evaluation.json with the network's per-paper
Silver scores for the same batch and prints:

- which papers the validator actually scored (many are dropped as
  "validator failed" for everyone),
- what the real miners achieved per paper (coverage / quality / score),
- what our artifact looks like on the same paper (claims, findings, judge),
- a projected batch score under explicitly stated coverage assumptions, since
  true coverage can only be computed by the validator against its Silver units.

Writes runs/<run-name>/comparison.json for the dashboard's Benchmark view.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

API = "https://api.claims111.ai"


def _get(path: str, **params: Any) -> Any:
    response = requests.get(f"{API}{path}", params=params, timeout=90)
    response.raise_for_status()
    return response.json()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-name", required=True, help="local experiment under runs/")
    parser.add_argument("--run-id", required=True, help="mainnet run_id whose batch we replayed")
    parser.add_argument("--network", default="mainnet")
    args = parser.parse_args()

    run = _get(f"/public/runs/{args.run_id}")
    batch_scores = _get(f"/public/runs/{args.run_id}/batch-scores", network=args.network)
    silver = _get(f"/public/runs/{args.run_id}/silver-scores", network=args.network)
    batch = _get(f"/public/batches/{run['batch_id']}") if run.get("batch_id") else {"papers": []}
    titles = {p["paper_id"]: p.get("title") or "" for p in batch.get("papers", [])}

    by_paper: dict[str, list[dict[str, Any]]] = {}
    for row in silver:
        by_paper.setdefault(row["paper_id"], []).append(row)

    local_dir = ROOT / "runs" / args.run_name
    local: dict[str, dict[str, Any]] = {}
    for paper_dir in sorted(local_dir.iterdir()) if local_dir.is_dir() else []:
        report_path = paper_dir / "evaluation.json"
        if not report_path.is_dir() and report_path.exists():
            local[paper_dir.name] = json.loads(report_path.read_text(encoding="utf-8"))

    rows: list[dict[str, Any]] = []
    for paper_id, real_rows in by_paper.items():
        report = local.get(paper_id)
        stats = (report or {}).get("stats") or {}
        judge = (report or {}).get("judge") or {}
        coverage = [r["coverage"] for r in real_rows]
        scored = [r for r in real_rows if r["score"] > 0]
        rows.append(
            {
                "paper_id": paper_id,
                "title": titles.get(paper_id, ""),
                "real_miner_count": len(real_rows),
                "real_scoring_miner_count": len(scored),
                "real_coverage_median": round(statistics.median(coverage), 4),
                "real_coverage_max": round(max(coverage), 4),
                "real_coverage_median_scoring": round(statistics.median([r["coverage"] for r in scored]), 4) if scored else 0.0,
                "real_score_max": round(max(r["score"] for r in real_rows), 4),
                "real_score_median": round(statistics.median([r["score"] for r in real_rows]), 4),
                "local_claims": stats.get("claims"),
                "local_evidence": stats.get("evidence_records"),
                "local_diagnostic_quality": ((report or {}).get("deterministic") or {}).get("diagnostic_quality_estimate"),
                "local_adjudication_quality": (report or {}).get("adjudication_quality_estimate"),
                "local_quality": (report or {}).get("quality_estimate"),
                "local_judge": judge.get("verdicts"),
                "local_cost_usd": stats.get("cost_usd"),
                "local_seconds": stats.get("elapsed_seconds"),
                "local_span_coverage": stats.get("span_coverage"),
            }
        )
    rows.sort(key=lambda row: row["paper_id"])

    scored_rows = [row for row in rows if row["local_claims"]]
    quality = [row["local_quality"] for row in scored_rows if isinstance(row["local_quality"], int | float)]
    mean_quality = statistics.mean(quality) if quality else 0.0

    # Projections. Coverage is the validator's to compute; we state assumptions instead of inventing it.
    projections = {}
    for label, key in [("coverage = median of real miners", "real_coverage_median"), ("coverage = median of scoring miners", "real_coverage_median_scoring"), ("coverage = best real miner", "real_coverage_max")]:
        per_paper = [min(1.0, row[key]) * (row["local_quality"] if isinstance(row["local_quality"], int | float) else 0.0) for row in scored_rows]
        projections[label] = round(statistics.mean(per_paper), 4) if per_paper else 0.0
    projections["coverage = 1.0 (upper bound)"] = round(mean_quality, 4)

    ranking = sorted(batch_scores, key=lambda b: b.get("rank") or 99)
    summary = {
        "run_id": args.run_id,
        "batch_id": run.get("batch_id"),
        "run_name": args.run_name,
        "expected_papers": ranking[0]["expected_paper_count"] if ranking else None,
        "eligible_papers": ranking[0]["eligible_paper_count"] if ranking else None,
        "validator_failed_papers": len(ranking[0].get("validator_failed_paper_ids", [])) if ranking else None,
        "real_ranking": [
            {k: b.get(k) for k in ("rank", "uid", "batch_score", "mean_score", "median_score", "min_score", "selection_lane", "winner", "payout_weight")}
            for b in ranking
        ],
        "local_papers_run": len(local),
        "local_papers_overlapping_scored": len(scored_rows),
        "local_mean_quality": round(mean_quality, 4),
        "local_total_cost_usd": round(sum((report.get("stats") or {}).get("cost_usd") or 0 for report in local.values()), 4),
        "local_mean_seconds": round(statistics.mean([(report.get("stats") or {}).get("elapsed_seconds") or 0 for report in local.values()]), 1) if local else 0,
        "local_mean_claims": round(statistics.mean([(report.get("stats") or {}).get("claims") or 0 for report in local.values()]), 1) if local else 0,
        "local_total_judge": {
            verdict: sum(((report.get("judge") or {}).get("verdicts") or {}).get(verdict, 0) for report in local.values())
            for verdict in ("accept", "weak", "reject")
        },
        "projected_batch_score": projections,
        "papers": rows,
    }
    (local_dir / "comparison.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"batch {summary['batch_id']} — {summary['expected_papers']} papers requested, {summary['eligible_papers']} scored by the validator, {summary['validator_failed_papers']} dropped for everyone")
    print(f"\nreal ranking ({len(ranking)} miners):")
    for b in ranking:
        print(f"  rank {b['rank']:>2}  uid {b['uid']:>3}  batch {b['batch_score']:.4f}  median {b['median_score']:.3f}  lane {b['selection_lane']:<14} {'WINNER' if b['winner'] else ''}")
    print(f"\nlocal run '{args.run_name}': {summary['local_papers_run']} papers, mean {summary['local_mean_claims']} claims, mean quality {summary['local_mean_quality']}, ${summary['local_total_cost_usd']} total, {summary['local_mean_seconds']}s/paper")
    print(f"papers in both (validator-scored and run locally): {summary['local_papers_overlapping_scored']}")
    print("\nprojected batch score (our measured quality x an assumed coverage):")
    for label, value in projections.items():
        print(f"  {label:38} -> {value:.4f}")
    print(f"\n{'paper':26} {'ours':>5} {'q':>5} {'judge':>12}   {'real cov med':>12} {'real cov max':>12} {'real best':>9}")
    for row in rows:
        judge = row["local_judge"] or {}
        judge_text = f"{judge.get('accept', 0)}/{judge.get('weak', 0)}/{judge.get('reject', 0)}" if judge else "-"
        claims = row["local_claims"] if row["local_claims"] is not None else "-"
        quality_text = f"{row['local_quality']:.2f}" if isinstance(row["local_quality"], int | float) else "-"
        print(f"{row['paper_id'][:26]:26} {str(claims):>5} {quality_text:>5} {judge_text:>12}   {row['real_coverage_median']:>12.3f} {row['real_coverage_max']:>12.3f} {row['real_score_max']:>9.3f}")
    print(f"\nwrote {local_dir / 'comparison.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
