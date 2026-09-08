"""Measure how many of a batch's pre-existing Silver units our claims would cover.

The public API never publishes Silver unit text, so coverage cannot be read off
the network. This tool reconstructs the units that existed independently of us
and then scores against them with the validator's own code:

1. Bronze reconstruction (`tools.make_bronze`) gives the reference claims. Every
   Bronze claim becomes a required Silver unit in `build_silver_record`.
2. A judge applies the validator's own comparator wording to decide, for each
   Bronze unit, whether one of our claims expresses the same unit
   (semantic_equivalent / compatible_refinement / compatible_split_merge count
   as the same unit; partial_overlap and contradiction do not).
3. It also tags each unit central / supporting / minor, as the validator's
   canonicalizer does.
4. `score_miner_against_silver` and `score_batch` - the real validator
   functions - turn that into coverage, per-paper score and a batch score.

What this deliberately does NOT model: Silver units created by our own accepted
claims. Those would raise our coverage and lower everyone else's, and they
cannot be predicted before participating. The number here is therefore a
conservative floor on the existing-unit half of coverage.

    python -m tools.estimate_coverage --run-name v5-50paper --run-id run_20260907_063159_471c07
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
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
from validator.agent_v1.batch_scoring import score_batch  # noqa: E402
from validator.agent_v1.comparison_models import SilverRecord, SilverUnit  # noqa: E402
from validator.agent_v1.record_projection import project_agent_artifact  # noqa: E402
from validator.agent_v1.silver_scoring import score_miner_against_silver  # noqa: E402

API = "https://api.claims111.ai"
SAME_UNIT = {"semantic_equivalent", "compatible_refinement", "compatible_split_merge"}

JUDGE_SYSTEM = """You decide, for each reference claim, whether any submission claim expresses the SAME scientific claim unit, and how important the reference claim is to the paper.

Relation definitions (use these exactly):
- semantic_equivalent: each expresses the same scientific proposition; material qualifiers are compatible.
- compatible_refinement: one preserves the other's concrete proposition while adding supported specificity.
- compatible_split_merge: one states substantially the same content the other divides across claims.
- partial_overlap: they share a material proposition but each asserts important content the other does not entail.
- none: no submission claim shares a concrete scientific proposition with this reference claim.

A shared paper, pathway, entity, disease family or broad topic is NOT a match. Different wording, detail level or granularity does not by itself prevent a match: if both assert the same mechanism or result, they match.

Importance of the reference claim to the paper: central = a main finding or contribution; supporting = a material result needed to support a main finding; minor = valid but peripheral.

Return STRICT JSON:
{"units": [{"reference_id": "r0", "best_submission_id": "s12" or null, "relation": "semantic_equivalent|compatible_refinement|compatible_split_merge|partial_overlap|none", "importance": "central|supporting|minor", "rationale": "one short sentence"}]}
One entry per reference claim, no omissions."""


def _fmt(value: Any, *, width: int = 8) -> str:
    """Format an optional float for a fixed-width column."""
    if value is None:
        return "-"
    return f"{float(value):.3f}"[:width]


def _get(path: str, **params: Any) -> Any:
    response = requests.get(f"{API}{path}", params=params, timeout=90)
    response.raise_for_status()
    return response.json()


def judge_paper_voted(
    client: LLMClient,
    paper_id: str,
    bronze: list[Any],
    ours: list[Any],
    title: str,
    passes: int,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Judge each unit `passes` times and take a majority vote.

    A single pass is not a measurement: repeated runs of the same comparison
    disagreed on whole papers. Majority voting over independent passes turns a
    noisy call into a stable one and exposes how noisy it was.
    """
    votes: dict[str, list[dict[str, Any]]] = {}
    for index in range(passes):
        rows = judge_paper(client, f"{paper_id}#p{index}", bronze, ours, title)
        for key, row in rows.items():
            votes.setdefault(key, []).append(row)

    decided: dict[str, dict[str, Any]] = {}
    disagreements = 0
    for key, rows in votes.items():
        same = [row for row in rows if str(row.get("relation")) in SAME_UNIT]
        if same and len(same) != len(rows):
            disagreements += 1
        if len(same) * 2 > len(rows):  # strict majority say "same unit"
            decided[key] = same[0]
        else:
            loser = next((row for row in rows if str(row.get("relation")) not in SAME_UNIT), rows[0])
            decided[key] = {**loser, "relation": "none" if str(loser.get("relation")) in SAME_UNIT else loser.get("relation")}
    stats = {
        "passes": passes,
        "units_voted": len(votes),
        "units_with_disagreement": disagreements,
        "vote_counts": {key: sum(1 for row in rows if str(row.get("relation")) in SAME_UNIT) for key, rows in votes.items()},
    }
    return decided, stats


def judge_paper(client: LLMClient, paper_id: str, bronze: list[Any], ours: list[Any], title: str) -> dict[str, dict[str, Any]]:
    """Judge every reference unit, retrying the ones a first pass drops.

    A unit the judge never ruled on must not be scored as a miss: that silently
    biases coverage downward. Anything still unjudged after the retry is
    returned without a relation and excluded from scoring by the caller.
    """
    submission = [{"submission_id": f"s{i}", "statement": c.statement} for i, c in enumerate(ours)]
    wanted = {f"r{i}" for i in range(len(bronze))}
    rows: dict[str, dict[str, Any]] = {}

    for attempt in range(2):
        missing = sorted(wanted - set(rows), key=lambda key: int(key[1:]))
        if not missing:
            break
        reference = [
            {"reference_id": key, "statement": bronze[int(key[1:])].statement, "qualifier": (bronze[int(key[1:])].qualifier or "")[:200]}
            for key in missing
        ]
        payload = {"paper_title": title, "reference_claims": reference, "submission_claims": submission}
        result, _usage = client.complete_json(
            system=JUDGE_SYSTEM,
            user=json.dumps(payload, ensure_ascii=False),
            stage="coverage_judge",
            max_tokens=16000,
            label=f"{paper_id}" if attempt == 0 else f"{paper_id}:retry",
        )
        for row in result.get("units", []) or []:
            if isinstance(row, dict) and str(row.get("reference_id")) in wanted:
                rows[str(row["reference_id"])] = row
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--bronze-dir", type=Path, default=ROOT / "runs" / "bronze")
    parser.add_argument("--network", default="mainnet")
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--model", default="")
    parser.add_argument("--passes", type=int, default=3, help="independent judge passes per unit; majority wins")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    import os

    client = LLMClient(
        model=args.model or os.getenv("SUBNET_CLAIMS_JUDGE_MODEL") or "openrouter/openai/gpt-5-mini",
        api_key=os.getenv("OPENROUTER_API_KEY", ""),
        api_base=os.getenv("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1"),
        provider="openrouter",
        reasoning_effort="medium",
    )

    run = _get(f"/public/runs/{args.run_id}")
    batch_scores = _get(f"/public/runs/{args.run_id}/batch-scores", network=args.network)
    ranking = sorted(batch_scores, key=lambda b: b.get("rank") or 99)
    silver_rows = _get(f"/public/runs/{args.run_id}/silver-scores", network=args.network)
    scored_paper_ids = sorted({row["paper_id"] for row in silver_rows})
    real_by_paper: dict[str, list[dict[str, Any]]] = {}
    for row in silver_rows:
        real_by_paper.setdefault(row["paper_id"], []).append(row)

    jobs = []
    for paper_id in scored_paper_ids:
        bronze_path = args.bronze_dir / paper_id / "agent_output.json"
        ours_path = ROOT / "runs" / args.run_name / paper_id / "agent_output.json"
        if bronze_path.exists() and ours_path.exists():
            jobs.append((paper_id, bronze_path, ours_path))
    if not jobs:
        print("no papers with both a Bronze reconstruction and our artifact", file=sys.stderr)
        return 1

    def work(job: tuple[str, Path, Path]) -> dict[str, Any]:
        paper_id, bronze_path, ours_path = job
        bronze_artifact = json.loads(bronze_path.read_text(encoding="utf-8"))
        our_artifact = json.loads(ours_path.read_text(encoding="utf-8"))
        bronze = project_agent_artifact(bronze_artifact, origin="bronze")
        ours = project_agent_artifact(our_artifact, origin="miner", miner_id="uid_192")
        if not bronze:
            return {"paper_id": paper_id, "error": "bronze produced no claims"}
        title = (bronze_artifact.get("paper") or {}).get("title") or paper_id
        try:
            rows, vote_stats = judge_paper_voted(client, paper_id, bronze, ours, title, args.passes)
        except Exception as exc:
            return {"paper_id": paper_id, "error": f"{type(exc).__name__}: {exc}"}

        units: list[SilverUnit] = []
        matched = 0
        unjudged: list[str] = []
        for index, candidate in enumerate(bronze):
            row = rows.get(f"r{index}")
            if row is None:
                # Never score an unjudged unit as a miss; leave it out entirely.
                unjudged.append(candidate.statement[:110])
                continue
            relation = str(row.get("relation") or "none")
            importance = str(row.get("importance") or "supporting")
            if importance not in {"central", "supporting", "minor"}:
                importance = "supporting"
            equivalent = [candidate.candidate_id]
            if relation in SAME_UNIT:
                # The relation is the judge's decision; the id is only bookkeeping.
                # When the id is missing or malformed, name the closest of our claims
                # by wording rather than discarding a unit the judge said we cover.
                position = -1
                submission_id = str(row.get("best_submission_id") or "")
                if submission_id.startswith("s") and submission_id[1:].isdigit():
                    position = int(submission_id[1:])
                if not 0 <= position < len(ours):
                    scored = [(jaccard(candidate.statement, other.statement), index) for index, other in enumerate(ours)]
                    position = max(scored)[1] if scored else -1
                if 0 <= position < len(ours):
                    equivalent.append(ours[position].candidate_id)
                    matched += 1
            units.append(
                SilverUnit(
                    silver_unit_id=f"silver_{paper_id}_{index}",
                    paper_id=paper_id,
                    statement=candidate.statement,
                    importance=importance,  # type: ignore[arg-type]
                    required_for_completeness=True,
                    equivalent_candidate_ids=equivalent,
                    scoring_mode="required",
                )
            )
        record = SilverRecord(silver_record_id=f"proxy_{paper_id}", paper_id=paper_id, silver_units=units)
        breakdown = score_miner_against_silver(miner_id="uid_192", miner_candidates=ours, silver_record=record, normal_findings=[])
        local_eval = json.loads((ours_path.parent / "evaluation.json").read_text(encoding="utf-8")) if (ours_path.parent / "evaluation.json").exists() else {}
        measured_quality = local_eval.get("quality_estimate")
        real_rows = real_by_paper.get(paper_id, [])
        real_cov = sorted(r["coverage"] for r in real_rows)
        real_scoring = [r["coverage"] for r in real_rows if r["score"] > 0]
        return {
            "paper_id": paper_id,
            "bronze_units": len(units),
            "matched_units": matched,
            "our_claims": len(ours),
            "coverage": breakdown.coverage,
            "coverage_score_only": breakdown.score,
            "measured_quality": measured_quality,
            "score_with_measured_quality": round(breakdown.coverage * measured_quality, 4) if isinstance(measured_quality, int | float) else None,
            "importance_mix": {level: sum(1 for u in units if u.importance == level) for level in ("central", "supporting", "minor")},
            "real_coverage_median": round(statistics.median(real_cov), 4) if real_cov else None,
            "real_coverage_median_scoring": round(statistics.median(real_scoring), 4) if real_scoring else None,
            "real_coverage_max": round(max(real_cov), 4) if real_cov else None,
            "beats_real_median": (breakdown.coverage > statistics.median(real_scoring)) if real_scoring else None,
            "missing_units": [u.statement[:110] for u in units if u.silver_unit_id in breakdown.missing_required_silver_units][:5],
            "unjudged_units": unjudged,
            "vote_stats": vote_stats,
            "breakdown": breakdown,
        }

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        results = list(executor.map(work, jobs))

    ok = [r for r in results if "error" not in r]
    failed = [r for r in results if "error" in r]
    batch = score_batch(batch_id=run.get("batch_id") or "proxy", paper_scores=[r["breakdown"] for r in ok])
    # score_batch returns one MinerBatchScore per miner id present in the breakdowns.
    our_batch = next((m for m in batch.miners if m.miner_id == "uid_192"), None)
    scores = [r["score_with_measured_quality"] for r in ok if r["score_with_measured_quality"] is not None]
    estimated_batch = round(statistics.mean(scores), 4) if scores else 0.0

    summary = {
        "run_id": args.run_id,
        "batch_id": run.get("batch_id"),
        "papers_estimated": len(ok),
        "papers_failed": [r["paper_id"] for r in failed],
        "bronze_units_total": sum(r["bronze_units"] for r in ok),
        "unjudged_units_total": sum(len(r.get("unjudged_units") or []) for r in ok),
        "judge_passes": args.passes,
        "units_with_split_vote": sum((r.get("vote_stats") or {}).get("units_with_disagreement", 0) for r in ok),
        "matched_units_total": sum(r["matched_units"] for r in ok),
        "mean_coverage": round(statistics.mean([r["coverage"] for r in ok]), 4) if ok else 0.0,
        "coverage_only_batch_score": round(our_batch.batch_score, 4) if our_batch else 0.0,
        "coverage_only_mean": round(our_batch.mean_score, 4) if our_batch else 0.0,
        "coverage_only_median": round(our_batch.median_score, 4) if our_batch else 0.0,
        "estimated_batch_score": estimated_batch,
        "real_ranking": [{k: b.get(k) for k in ("rank", "uid", "batch_score", "winner")} for b in ranking],
        "papers": [{k: v for k, v in r.items() if k != "breakdown"} for r in ok],
        "judge_usage": client.usage_summary(),
        "caveats": [
            "Bronze is reconstructed with a named model; the validator uses its own private model, so unit sets differ.",
            "Silver units created by other miners' accepted claims are not modelled and are not observable.",
            "Silver units our own accepted claims would create are deliberately excluded, so this is a floor.",
        ],
    }
    out = ROOT / "runs" / args.run_name / "coverage_estimate.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"papers estimated      : {len(ok)}" + (f"  (failed: {[r['paper_id'] for r in failed]})" if failed else ""))
    print(f"reconstructed units   : {summary['bronze_units_total']} Bronze units, {summary['matched_units_total']} matched by our claims")
    print(f"mean coverage         : {summary['mean_coverage']}")
    print(f"estimated batch score : {estimated_batch}   (coverage x our measured quality)")
    winner = next((b for b in ranking if b.get("winner")), None)
    if winner:
        placing = sum(1 for b in ranking if (b.get("batch_score") or 0) > estimated_batch) + 1
        print(f"real winner           : uid {winner['uid']} at {winner['batch_score']:.4f}  ->  we would place {placing} of {len(ranking) + 1}")
    beat = [r for r in ok if r.get("beats_real_median")]
    print(f"our coverage beats the median scoring miner on {len(beat)} of {len(ok)} papers")
    print(f"judge stability: {summary['units_with_split_vote']} of {summary['bronze_units_total']} units had a split vote across {args.passes} passes")
    print()
    print(f"{'paper':26} {'units':>5} {'match':>5} {'our cov':>8} {'real med':>8} {'real max':>8} {'qual':>5} {'score':>6}")
    for row in sorted(ok, key=lambda r: r["coverage"]):
        real_median = _fmt(row["real_coverage_median_scoring"])
        real_max = _fmt(row["real_coverage_max"])
        quality = _fmt(row["measured_quality"], width=5)
        score = _fmt(row["score_with_measured_quality"], width=6)
        print(
            f"{row['paper_id'][:26]:26} {row['bronze_units']:>5} {row['matched_units']:>5} "
            f"{row['coverage']:>8.3f} {real_median:>8} {real_max:>8} {quality:>5} {score:>6}"
        )
    print(f"\njudge cost ${client.usage_summary().get('cost_usd', 0):.3f}; wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
