"""Local evaluator for agent_v1 artifacts.

Runs the validator's deterministic structural and grounding passes (copied
verbatim from the Claims repo), then a stricter local grounding audit that
mirrors what the validator's rigor/diagnostic agent and Silver judges look
for, and finally (optionally) an LLM self-judge that estimates how many claims
an anonymous adjudicator would reject.

Usage:
    python -m tools.evaluate RUN_DIR [--judge] [--judge-model MODEL]

Writes RUN_DIR/evaluation.json and prints a summary.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from miner.agent_v1.staged.grounding import jaccard, normalize_text, numbers_in, quote_in_span, span_index, ungrounded_numbers  # noqa: E402
from miner.agent_v1.staged.llm import LLMClient  # noqa: E402
from validator.agent_v1.grounding import run_grounding_checks  # noqa: E402
from validator.agent_v1.models import AgentV1ValidationFinding  # noqa: E402
from validator.agent_v1.scoring import PENALTIES, score_findings  # noqa: E402
from validator.agent_v1.structural import run_structural_checks  # noqa: E402


GENERIC_FALSIFICATION = re.compile(r"^(if|when)?\s*(the )?(claim|statement|result)s? (is|are|were|would be) (false|not|wrong)", re.IGNORECASE)


def evaluate_run(run_dir: Path, *, judge: bool = False, judge_model: str = "", judge_batch: int = 12) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    artifact_path = run_dir / "agent_output.json"
    payload_path = run_dir / "source_payload.json"
    if not payload_path.exists():
        payload_path = run_dir / "data" / "agent_v1_source_payload.json"
    started = time.perf_counter()
    report: dict[str, Any] = {
        "run_dir": str(run_dir),
        "artifact_path": str(artifact_path),
        "exists": artifact_path.exists(),
        "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if not artifact_path.exists():
        report.update({"score_estimate": 0.0, "findings": [], "summary": {"error": "agent_output.json missing"}})
        _write(run_dir, report)
        return report
    source_payload = json.loads(payload_path.read_text(encoding="utf-8")) if payload_path.exists() else {}
    raw, artifact, structural = run_structural_checks(artifact_path)
    grounding = run_grounding_checks(artifact, source_payload) if artifact is not None else []
    local = _local_audit(raw, source_payload) if isinstance(raw, dict) else []
    findings = [*structural, *grounding, *local]
    score, passed, summary = score_findings(findings)
    stats = _stats(raw, source_payload) if isinstance(raw, dict) else {}
    report.update(
        {
            "deterministic": {
                "structural_count": len(structural),
                "grounding_count": len(grounding),
                "local_count": len(local),
                "diagnostic_quality_estimate": score,
                "passed": passed,
                "severity_summary": summary,
            },
            "findings": [_finding_dict(finding) for finding in findings],
            "stats": stats,
        }
    )
    adjudication_quality = 1.0
    if judge and isinstance(raw, dict):
        judge_report = _llm_judge(raw, source_payload, model=judge_model, batch_size=judge_batch)
        report["judge"] = judge_report
        rejected = judge_report.get("rejected_count", 0)
        adjudication_quality = max(0.0, round(1.0 - PENALTIES["critical"] * rejected, 4))
    report["adjudication_quality_estimate"] = adjudication_quality
    report["quality_estimate"] = round(score * adjudication_quality, 4)
    report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    _write(run_dir, report)
    return report


# ------------------------------------------------------------------ local audit
def _local_audit(raw: dict[str, Any], source_payload: dict[str, Any]) -> list[AgentV1ValidationFinding]:
    spans = span_index(source_payload)
    findings: list[AgentV1ValidationFinding] = []
    logic = raw.get("logic") if isinstance(raw.get("logic"), dict) else {}
    claims = [claim for claim in (logic.get("claims") or []) if isinstance(claim, dict)]
    evidence = [record for record in ((raw.get("evidence") or {}).get("records") or []) if isinstance(record, dict)]
    evidence_by_id = {str(record.get("evidence_id")): record for record in evidence}

    def add(severity: str, dimension: str, target_type: str, target_id: str, message: str, code: str, **metadata: Any) -> None:
        findings.append(
            AgentV1ValidationFinding(
                pass_name="rigor",
                dimension=dimension,
                severity=severity,  # type: ignore[arg-type]
                target_type=target_type,
                target_id=target_id,
                message=message,
                metadata={"code": code, "local": True, **metadata},
            )
        )

    def quote_texts(refs: list[Any]) -> list[str]:
        out: list[str] = []
        for ref in refs or []:
            if isinstance(ref, dict) and isinstance(ref.get("quote"), str) and ref["quote"].strip():
                out.append(ref["quote"])
        return out

    def check_refs(refs: list[Any], target_type: str, target_id: str) -> None:
        for ref in refs or []:
            if not isinstance(ref, dict):
                continue
            quote = ref.get("quote")
            span_ids = [str(item) for item in (ref.get("span_ids") or [])]
            if not isinstance(quote, str) or not quote.strip():
                continue
            if len(span_ids) != 1:
                add("minor", "grounding_adjudication", target_type, target_id, f"Quote cites {len(span_ids)} spans; one quote should cite exactly one span.", "quote_multi_span")
            found = any(span_id in spans and quote_in_span(str(spans[span_id].get("text") or ""), quote) for span_id in span_ids)
            if not found:
                add("critical", "grounding_adjudication", target_type, target_id, "Source quote does not appear in the referenced source span text.", "quote_not_in_source", quote=quote[:160])
            elif len(quote.strip()) < 25:
                add("warning", "grounding_adjudication", target_type, target_id, "Very short quote; judges may consider it insufficient support.", "short_quote", quote=quote)

    seen_statements: list[tuple[str, str]] = []
    for claim in claims:
        claim_id = str(claim.get("claim_id") or "?")
        statement = str(claim.get("statement") or "")
        conditions = str(claim.get("conditions") or "")
        falsification = str(claim.get("falsification_criteria") or "")
        refs = claim.get("sources") if isinstance(claim.get("sources"), list) else []
        check_refs(refs, "claim", claim_id)
        connected_quotes = quote_texts(refs)
        for evidence_id in claim.get("evidence_ids") or []:
            record = evidence_by_id.get(str(evidence_id))
            if record:
                connected_quotes.extend(quote_texts(record.get("source_refs") or []))
        missing = ungrounded_numbers(f"{statement} {conditions}", connected_quotes)
        if missing:
            add("major", "grounding_adjudication", "claim", claim_id, f"Load-bearing numbers not present in connected quotes: {', '.join(missing)}.", "number_not_grounded", numbers=missing)
        if not connected_quotes:
            add("major", "grounding_adjudication", "claim", claim_id, "Claim has no verbatim quote in its sources or linked evidence.", "no_quote")
        if len(conditions.strip()) < 30 or conditions.strip().lower().startswith("not available"):
            add("warning", "scope_calibration", "claim", claim_id, "Conditions are very short; scope may be judged uncalibrated.", "vague_conditions")
        if len(falsification.strip()) < 40 or GENERIC_FALSIFICATION.match(falsification.strip()):
            add("warning", "falsifiability_quality", "claim", claim_id, "Falsification criteria look generic or too short.", "weak_falsification")
        if len(statement.split()) > 60:
            add("minor", "scope_calibration", "claim", claim_id, "Statement is very long; split compound propositions.", "long_statement")
        for other_id, other_statement in seen_statements:
            if jaccard(statement, other_statement) >= 0.75:
                add("major", "argument_coherence", "claim", claim_id, f"Statement is a near-duplicate of {other_id}; Silver would merge them and judges may reject one.", "near_duplicate", duplicate_of=other_id)
                break
        seen_statements.append((claim_id, statement))
        importance = (claim.get("metadata") or {}).get("importance") if isinstance(claim.get("metadata"), dict) else None
        if importance not in {"central", "supporting", "minor"}:
            add("suggestion", "argument_coherence", "claim", claim_id, "No importance tag in metadata (informational only; validator assigns its own).", "no_importance")

    for record in evidence:
        evidence_id = str(record.get("evidence_id") or "?")
        refs = record.get("source_refs") if isinstance(record.get("source_refs"), list) else []
        check_refs(refs, "evidence", evidence_id)
        missing = ungrounded_numbers(str(record.get("summary") or ""), quote_texts(refs))
        if missing:
            add("minor", "grounding_adjudication", "evidence", evidence_id, f"Evidence summary numbers not in its quotes: {', '.join(missing)}.", "evidence_number_not_grounded", numbers=missing)
        if not record.get("linked_claim_ids"):
            add("minor", "argument_coherence", "evidence", evidence_id, "Evidence record links no claim.", "orphan_evidence")

    for experiment in logic.get("experiments") or []:
        if not isinstance(experiment, dict):
            continue
        experiment_id = str(experiment.get("experiment_id") or "?")
        check_refs(experiment.get("source_refs") or [], "experiment", experiment_id)
        if numbers_in(str(experiment.get("expected_outcome") or "")):
            add("minor", "methodological_rigor", "experiment", experiment_id, "expected_outcome contains exact numbers; experiments must be directional only.", "experiment_numbers")
        setup_numbers = ungrounded_numbers(f"{experiment.get('setup') or ''} {experiment.get('procedure') or ''}", quote_texts(experiment.get("source_refs") or []))
        if setup_numbers:
            add("warning", "grounding_adjudication", "experiment", experiment_id, f"Method constants not grounded in the experiment's own quotes: {', '.join(setup_numbers[:6])}.", "experiment_constants_ungrounded")

    for concept in logic.get("concepts") or []:
        if isinstance(concept, dict):
            check_refs(concept.get("source_refs") or [], "concept", str(concept.get("concept_id") or "?"))

    _walk_trace(raw.get("trace"), check_refs)
    for index, finding in enumerate(findings, start=1):
        finding.finding_id = f"L{index:03d}"
    return findings


def _walk_trace(node: Any, check_refs) -> None:
    if not isinstance(node, dict):
        return
    check_refs(node.get("source_refs") or [], "trace_node", str(node.get("node_id") or "?"))
    for child in node.get("children") or []:
        _walk_trace(child, check_refs)


# ----------------------------------------------------------------------- stats
def _stats(raw: dict[str, Any], source_payload: dict[str, Any]) -> dict[str, Any]:
    logic = raw.get("logic") if isinstance(raw.get("logic"), dict) else {}
    claims = [claim for claim in (logic.get("claims") or []) if isinstance(claim, dict)]
    evidence = [record for record in ((raw.get("evidence") or {}).get("records") or []) if isinstance(record, dict)]
    spans = span_index(source_payload)
    cited_spans: set[str] = set()
    quote_lengths: list[int] = []
    quotes_per_claim: list[int] = []
    for claim in claims:
        count = 0
        for ref in claim.get("sources") or []:
            if isinstance(ref, dict):
                cited_spans.update(str(item) for item in (ref.get("span_ids") or []))
                if ref.get("quote"):
                    quote_lengths.append(len(str(ref["quote"])))
                    count += 1
        quotes_per_claim.append(count)
    importance = Counter(str((claim.get("metadata") or {}).get("importance") or "unknown") for claim in claims)
    claim_types = Counter(str((claim.get("metadata") or {}).get("claim_type") or "unknown") for claim in claims)
    statuses = Counter(str(claim.get("status") or "unknown") for claim in claims)
    with_numbers = sum(1 for claim in claims if numbers_in(str(claim.get("statement") or "")))
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    metrics = metadata.get("runtime_metrics") if isinstance(metadata.get("runtime_metrics"), dict) else {}
    return {
        "claims": len(claims),
        "evidence_records": len(evidence),
        "experiments": len(logic.get("experiments") or []),
        "concepts": len(logic.get("concepts") or []),
        "spans_total": len(spans),
        "spans_cited": len(cited_spans & set(spans)),
        "span_coverage": round(len(cited_spans & set(spans)) / len(spans), 4) if spans else None,
        "claims_with_numbers": with_numbers,
        "avg_quotes_per_claim": round(sum(quotes_per_claim) / len(quotes_per_claim), 2) if quotes_per_claim else 0,
        "avg_quote_chars": round(sum(quote_lengths) / len(quote_lengths), 1) if quote_lengths else 0,
        "avg_statement_words": round(sum(len(str(claim.get("statement") or "").split()) for claim in claims) / len(claims), 1) if claims else 0,
        "importance": dict(importance),
        "claim_types": dict(claim_types),
        "status": dict(statuses),
        "runtime": metadata.get("runtime"),
        "models": metrics.get("models"),
        "elapsed_seconds": metrics.get("elapsed_seconds"),
        "cost_usd": metrics.get("cost_usd"),
        "total_tokens": (metrics.get("token_usage") or {}).get("total_tokens") if isinstance(metrics.get("token_usage"), dict) else None,
        "attempt_count": metrics.get("attempt_count"),
    }


# ----------------------------------------------------------------------- judge
JUDGE_SYSTEM = """You are an anonymous Silver adjudicator and rigor reviewer for scientific claim extraction.
For EACH claim you receive: statement, conditions, falsification criteria, the miner's verbatim quotes, and the full text of the cited source spans.
Decide, using ONLY the supplied spans:
- verdict: "accept" (the spans support the statement as scoped by conditions), "weak" (supported but over-generalised, imprecise, or judges could disagree), or "reject" (unsupported, contradicted, numbers/entities not in spans, trivial/bibliographic/not a scientific proposition of this paper, or a near-duplicate of another listed claim).
- evidence_status: supported | partially_supported | unsupported
- importance: central | supporting | minor (role in the paper)
- issues: short list of concrete problems (empty if none)
- fix: one sentence on how to make it acceptable (empty if accept)
Return STRICT JSON: {"results": {"<claim_id>": {"verdict": "...", "evidence_status": "...", "importance": "...", "issues": ["..."], "fix": "..."}}}
Every input claim id must appear exactly once."""


def _llm_judge(raw: dict[str, Any], source_payload: dict[str, Any], *, model: str, batch_size: int) -> dict[str, Any]:
    load_dotenv(ROOT / ".env")
    spans = span_index(source_payload)
    provider = (os.getenv("SUBNET_CLAIMS_AGENT_PROVIDER") or "openrouter").strip().lower()
    api_base = os.getenv("SUBNET_CLAIMS_AGENT_API_BASE") or (os.getenv("CHUTES_API_BASE", "https://llm.chutes.ai/v1") if provider == "chutes" else os.getenv("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1"))
    api_key = os.getenv("CHUTES_API_KEY" if provider == "chutes" else "OPENROUTER_API_KEY", "")
    model = model or os.getenv("SUBNET_CLAIMS_JUDGE_MODEL") or os.getenv("SUBNET_CLAIMS_AGENT_MODEL") or "openrouter/openai/gpt-5-mini"
    client = LLMClient(model=model, api_key=api_key, api_base=api_base, provider=provider, reasoning_effort=os.getenv("SUBNET_CLAIMS_JUDGE_REASONING_EFFORT", "medium"))
    claims = [claim for claim in ((raw.get("logic") or {}).get("claims") or []) if isinstance(claim, dict)]
    evidence_by_id = {str(record.get("evidence_id")): record for record in ((raw.get("evidence") or {}).get("records") or []) if isinstance(record, dict)}
    paper = raw.get("paper") if isinstance(raw.get("paper"), dict) else {}
    results: dict[str, Any] = {}
    errors: list[str] = []
    for start in range(0, len(claims), max(1, batch_size)):
        batch = claims[start : start + batch_size]
        cases = []
        span_ids_needed: set[str] = set()
        for claim in batch:
            refs = [ref for ref in (claim.get("sources") or []) if isinstance(ref, dict)]
            quotes = [ref.get("quote") for ref in refs if ref.get("quote")]
            ids = {str(item) for ref in refs for item in (ref.get("span_ids") or [])}
            for evidence_id in claim.get("evidence_ids") or []:
                record = evidence_by_id.get(str(evidence_id))
                if record:
                    for ref in record.get("source_refs") or []:
                        if isinstance(ref, dict):
                            ids.update(str(item) for item in (ref.get("span_ids") or []))
            span_ids_needed.update(ids)
            cases.append(
                {
                    "claim_id": claim.get("claim_id"),
                    "statement": claim.get("statement"),
                    "conditions": claim.get("conditions"),
                    "falsification_criteria": claim.get("falsification_criteria"),
                    "quotes": quotes,
                    "span_ids": sorted(ids),
                }
            )
        span_texts = {span_id: str(spans[span_id].get("text") or "") for span_id in sorted(span_ids_needed) if span_id in spans}
        user = json.dumps({"paper": {"title": paper.get("title"), "abstract": (paper.get("abstract") or "")[:1500]}, "claims": cases, "source_spans": span_texts}, ensure_ascii=False)
        try:
            payload, _usage = client.complete_json(system=JUDGE_SYSTEM, user=user, stage="judge", max_tokens=16000, label=f"batch{start // batch_size}")
        except Exception as exc:
            errors.append(str(exc)[:300])
            continue
        rows = payload.get("results") if isinstance(payload.get("results"), dict) else {}
        for claim in batch:
            claim_id = str(claim.get("claim_id"))
            row = rows.get(claim_id)
            if isinstance(row, dict):
                results[claim_id] = {
                    "verdict": str(row.get("verdict") or "weak").lower(),
                    "evidence_status": str(row.get("evidence_status") or ""),
                    "importance": str(row.get("importance") or ""),
                    "issues": [str(item) for item in (row.get("issues") or [])][:6],
                    "fix": str(row.get("fix") or ""),
                }
            else:
                results[claim_id] = {"verdict": "unknown", "issues": ["judge omitted this claim"], "fix": ""}
    verdicts = Counter(row["verdict"] for row in results.values())
    return {
        "model": model,
        "results": results,
        "verdicts": dict(verdicts),
        "rejected_count": verdicts.get("reject", 0),
        "weak_count": verdicts.get("weak", 0),
        "accepted_count": verdicts.get("accept", 0),
        "judge_importance": dict(Counter(row.get("importance") for row in results.values())),
        "usage": client.usage_summary(),
        "errors": errors,
    }


# --------------------------------------------------------------------- output
def _finding_dict(finding: AgentV1ValidationFinding) -> dict[str, Any]:
    data = finding.model_dump(mode="json")
    return {key: data.get(key) for key in ("finding_id", "pass_name", "dimension", "severity", "target_type", "target_id", "message", "metadata")}


def _write(run_dir: Path, report: dict[str, Any]) -> None:
    (run_dir / "evaluation.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


def print_summary(report: dict[str, Any]) -> None:
    stats = report.get("stats") or {}
    det = report.get("deterministic") or {}
    print(f"run: {report.get('run_dir')}")
    print(f"claims={stats.get('claims')} evidence={stats.get('evidence_records')} experiments={stats.get('experiments')} concepts={stats.get('concepts')} span_coverage={stats.get('span_coverage')}")
    print(f"findings: structural={det.get('structural_count')} grounding={det.get('grounding_count')} local={det.get('local_count')} severity={det.get('severity_summary')}")
    print(f"diagnostic_quality_estimate={det.get('diagnostic_quality_estimate')} adjudication_quality_estimate={report.get('adjudication_quality_estimate')} quality_estimate={report.get('quality_estimate')}")
    if report.get("judge"):
        judge = report["judge"]
        print(f"judge[{judge.get('model')}]: {judge.get('verdicts')} importance={judge.get('judge_importance')}")
    print(f"cost_usd={stats.get('cost_usd')} elapsed={stats.get('elapsed_seconds')} tokens={stats.get('total_tokens')}")
    codes = Counter(str((finding.get('metadata') or {}).get('code')) for finding in report.get("findings") or [])
    if codes:
        print("finding codes:", dict(codes.most_common(12)))


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate an agent_v1 run directory locally.")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--judge", action="store_true", help="Run the LLM self-judge (costs tokens).")
    parser.add_argument("--judge-model", default="")
    parser.add_argument("--judge-batch", type=int, default=12)
    args = parser.parse_args()
    report = evaluate_run(args.run_dir, judge=args.judge, judge_model=args.judge_model, judge_batch=args.judge_batch)
    print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
