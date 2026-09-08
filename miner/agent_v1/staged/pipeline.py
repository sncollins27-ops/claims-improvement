from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from .grounding import (
    is_non_claim,
    jaccard,
    locate_quote,
    numbers_in,
    ungrounded_measurements,
    ungrounded_numbers,
)
from .llm import LLMClient


logger = logging.getLogger(__name__)

VALID_ROLES = {"input", "result", "method", "interpretation", "metadata"}
IMPORTANCE = {"central", "supporting", "minor"}
STATUS = {"supported", "partially_supported", "hypothesis"}
CLAIM_TYPES = {
    "quantitative_result", "comparative_finding", "association", "mechanism", "method_contribution",
    "validation", "predictive_performance", "resource_description", "limitation", "implication",
}
BOILERPLATE = re.compile(
    r"\b(references?|acknowledg|funding|competing interests|conflict of interest|author contributions|"
    r"data availability|code availability|supplementary information|ethics approval|open access|"
    r"creative commons|copyright|received:|accepted:|published online|correspondence)\b",
    re.IGNORECASE,
)


@dataclass
class StagedSettings:
    extract_concurrency: int = 4
    max_span_chars: int = 9000
    min_quote_chars: int = 25
    max_quote_chars: int = 420
    max_candidates_per_consolidation: int = 70
    cross_group_duplicate_threshold: float = 0.55
    merge_min_overlap: float = 0.3
    merge_max_members: int = 6
    premerge_overlap: float = 0.5
    retention_floor: float = 0.55
    selfcheck: bool = True
    selfcheck_batch: int = 25
    max_claims: int = 150
    extract_max_tokens: int = 16000
    consolidate_max_tokens: int = 32000
    structure_max_tokens: int = 32000
    extract_reasoning_effort: str | None = "low"
    consolidate_reasoning_effort: str | None = "medium"
    structure_reasoning_effort: str | None = "low"
    repair_ungrounded: bool = True
    near_duplicate_threshold: float = 0.5


@dataclass
class Candidate:
    candidate_id: str
    span_id: str
    page: int | None
    section_name: str
    statement: str
    conditions: str
    claim_type: str
    status: str
    importance: str
    quotes: list[dict[str, str]]
    evidence_summary: str
    evidence_method: str
    presentation_type: str
    verification: dict[str, Any] = field(default_factory=dict)


@dataclass
class StageRecord:
    key: str
    label: str
    started_at: float
    ended_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def finish(self, **metadata: Any) -> "StageRecord":
        self.ended_at = time.time()
        self.metadata.update(metadata)
        return self

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "duration_seconds": round(max(0.0, (self.ended_at or time.time()) - self.started_at), 3),
            "metadata": self.metadata,
        }


class StagedPipeline:
    def __init__(
        self,
        *,
        clients: dict[str, LLMClient],
        prompts: dict[str, str],
        settings: StagedSettings,
        paper: dict[str, Any],
        source_payload: dict[str, Any],
    ) -> None:
        self.clients = clients
        self.prompts = prompts
        self.settings = settings
        self.paper = paper
        self.source_payload = source_payload
        self.spans = [span for span in source_payload.get("spans", []) if isinstance(span, dict) and span.get("text")]
        self.span_by_id = {str(span["span_id"]): span for span in self.spans}
        # Identifier/measurement checks run against the whole paper: a token defined on another
        # page is legitimate, while one absent from the paper entirely is a typo or a fabrication.
        self.full_text = "\n".join(str(span["text"]) for span in self.spans)
        self.trace: dict[str, Any] = {"stages": [], "spans": [], "consolidation": {}, "structure": {}, "warnings": []}
        self.stages: list[StageRecord] = []

    # ------------------------------------------------------------------ driver
    def run(self) -> dict[str, Any]:
        candidates = self._stage_extract()
        claims = self._stage_consolidate(candidates)
        if self.settings.selfcheck and claims:
            claims = self._stage_selfcheck(claims)
        structure = self._stage_structure(claims)
        artifact = self._stage_assemble(claims, structure)
        self.trace["stages"] = [stage.as_dict() for stage in self.stages]
        self.trace["usage"] = {name: client.usage_summary() for name, client in self._unique_clients().items()}
        return artifact

    def _unique_clients(self) -> dict[str, LLMClient]:
        unique: dict[int, LLMClient] = {}
        for client in self.clients.values():
            unique[id(client)] = client
        return {client.model + f"#{index}": client for index, client in enumerate(unique.values())}

    def _start(self, key: str, label: str) -> StageRecord:
        record = StageRecord(key=key, label=label, started_at=time.time())
        self.stages.append(record)
        return record

    # ------------------------------------------------------------- stage: extract
    def _stage_extract(self) -> list[Candidate]:
        stage = self._start("extract", "Per-span candidate extraction")
        context = self._paper_context()
        jobs: list[tuple[dict[str, Any], str, int]] = []
        for span in self.spans:
            text = str(span["text"])
            if len(text) <= self.settings.max_span_chars:
                jobs.append((span, text, 0))
            else:
                for part_index, part in enumerate(_split_text(text, self.settings.max_span_chars)):
                    jobs.append((span, part, part_index))
        results: list[list[Candidate]] = [[] for _ in jobs]
        span_records: dict[str, dict[str, Any]] = {}

        def work(index: int) -> None:
            span, text, part_index = jobs[index]
            span_id = str(span["span_id"])
            label = f"{span_id}" + (f"#part{part_index}" if part_index else "")
            if _looks_like_boilerplate_only(text):
                span_records.setdefault(span_id, {"span_id": span_id, "page": span.get("page"), "chars": len(str(span["text"])), "parts": []})
                span_records[span_id]["parts"].append({"part": part_index, "skipped": "boilerplate", "raw_candidates": 0, "kept": 0})
                return
            raw = self._extract_span(span, text, context, label)
            verified, report = self._verify_candidates(raw, span, text, label)
            results[index] = verified
            record = span_records.setdefault(span_id, {"span_id": span_id, "page": span.get("page"), "section_name": span.get("section_name"), "chars": len(str(span["text"])), "parts": []})
            record["parts"].append({"part": part_index, "raw_candidates": len(raw), "kept": len(verified), **report})

        with ThreadPoolExecutor(max_workers=max(1, self.settings.extract_concurrency)) as executor:
            list(executor.map(work, range(len(jobs))))

        candidates: list[Candidate] = []
        counter = 0
        for group in results:
            for candidate in group:
                counter += 1
                candidate.candidate_id = f"x{counter}"
                candidates.append(candidate)
        self.trace["spans"] = [span_records[str(span["span_id"])] for span in self.spans if str(span["span_id"]) in span_records]
        stage.finish(span_count=len(self.spans), job_count=len(jobs), candidate_count=len(candidates))
        logger.info("staged.extract spans=%s candidates=%s", len(self.spans), len(candidates))
        return candidates

    def _paper_context(self) -> str:
        title = str(self.paper.get("title") or "")
        first = str(self.spans[0]["text"])[:2500] if self.spans else ""
        section_map = "\n".join(
            f"- {span.get('span_id')} (page {span.get('page')}): {str(span.get('text'))[:110].replace(chr(10), ' ')}..."
            for span in self.spans
        )
        return f"PAPER TITLE: {title}\n\nFIRST PAGE EXCERPT:\n{first}\n\nSPAN MAP:\n{section_map}"

    def _extract_span(self, span: dict[str, Any], text: str, context: str, label: str) -> list[dict[str, Any]]:
        user = (
            f"{context}\n\n"
            f"=== SPAN TO EXTRACT FROM ===\nspan_id: {span['span_id']}\nsection: {span.get('section_name')}\npage: {span.get('page')}\n\n"
            f"SPAN TEXT (quotes must be exact substrings of this text):\n<<<\n{text}\n>>>\n\n"
            "Return {\"candidates\": [...]} now."
        )
        try:
            payload, _usage = self.clients["extract"].complete_json(
                system=self.prompts["extract"],
                user=user,
                stage="extract",
                max_tokens=self.settings.extract_max_tokens,
                reasoning_effort=self.settings.extract_reasoning_effort,
                label=label,
            )
        except Exception as exc:
            logger.warning("Extraction failed for %s: %s", label, exc)
            self.trace["warnings"].append({"stage": "extract", "span_id": span.get("span_id"), "error": str(exc)[:400]})
            return []
        rows = payload.get("candidates")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def _verify_candidates(
        self,
        rows: list[dict[str, Any]],
        span: dict[str, Any],
        text: str,
        label: str,
    ) -> tuple[list[Candidate], dict[str, Any]]:
        span_id = str(span["span_id"])
        span_text = str(span["text"])
        kept: list[Candidate] = []
        dropped: list[dict[str, Any]] = []
        needs_repair: list[tuple[dict[str, Any], list[str]]] = []
        quote_methods = {"exact": 0, "normalized": 0, "fuzzy": 0, "failed": 0}

        def build(row: dict[str, Any]) -> tuple[Candidate | None, list[str], str]:
            statement = _clean(row.get("statement"))
            conditions = _clean(row.get("conditions"))
            if len(statement) < 20:
                return None, [], "empty_statement"
            if BOILERPLATE.search(statement) and not numbers_in(statement):
                return None, [], "boilerplate_statement"
            quotes: list[dict[str, str]] = []
            seen: set[str] = set()
            for quote_row in row.get("quotes") or []:
                if not isinstance(quote_row, dict):
                    continue
                raw_quote = _clean(quote_row.get("quote"))
                role = str(quote_row.get("role") or "result").strip().lower()
                if role not in VALID_ROLES:
                    role = "result"
                match = locate_quote(span_text, raw_quote, min_chars=self.settings.min_quote_chars)
                if match is None:
                    quote_methods["failed"] += 1
                    continue
                quote_methods[match.method] += 1
                exact = match.quote[: self.settings.max_quote_chars]
                if exact not in span_text:
                    exact = match.quote
                if exact in seen:
                    continue
                seen.add(exact)
                quotes.append({"quote": exact, "role": role})
            if not quotes:
                return None, [], "no_grounded_quote"
            missing = ungrounded_numbers(f"{statement} {conditions}", [q["quote"] for q in quotes])
            candidate = Candidate(
                candidate_id="",
                span_id=span_id,
                page=span.get("page"),
                section_name=str(span.get("section_name") or ""),
                statement=statement,
                conditions=conditions or "Not available from provided input",
                claim_type=_pick(row.get("claim_type"), CLAIM_TYPES, "comparative_finding"),
                status=_pick(row.get("status"), STATUS, "supported"),
                importance=_pick(row.get("importance"), IMPORTANCE, "supporting"),
                quotes=quotes,
                evidence_summary=_clean(row.get("evidence_summary")) or statement,
                evidence_method=_clean(row.get("evidence_method")) or "Not available from provided input",
                presentation_type=_pick(row.get("presentation_type"), {"text", "table", "figure", "mixed"}, "text"),
            )
            return candidate, missing, ""

        for row in rows:
            candidate, missing, reason = build(row)
            if candidate is None:
                dropped.append({"statement": _clean(row.get("statement"))[:160], "reason": reason})
                continue
            if missing:
                needs_repair.append((row, missing))
                candidate.verification = {"ungrounded_numbers": missing}
                dropped.append({"statement": candidate.statement[:160], "reason": "ungrounded_numbers", "numbers": missing, "pending_repair": True})
                continue
            candidate.verification = {"quote_count": len(candidate.quotes)}
            kept.append(candidate)

        repaired = 0
        if needs_repair and self.settings.repair_ungrounded:
            repaired_rows = self._repair_numbers(needs_repair, span, text, label)
            for row in repaired_rows:
                candidate, missing, reason = build(row)
                if candidate is None or missing:
                    continue
                candidate.verification = {"quote_count": len(candidate.quotes), "repaired": True}
                kept.append(candidate)
                repaired += 1
        report = {
            "dropped": dropped,
            "quote_methods": quote_methods,
            "repair_requested": len(needs_repair),
            "repaired": repaired,
        }
        return kept, report

    def _repair_numbers(
        self,
        items: list[tuple[dict[str, Any], list[str]]],
        span: dict[str, Any],
        text: str,
        label: str,
    ) -> list[dict[str, Any]]:
        problems = [
            {
                "candidate": {key: row.get(key) for key in ("statement", "conditions", "claim_type", "status", "importance", "quotes", "evidence_summary", "evidence_method", "presentation_type")},
                "ungrounded_numbers": missing,
            }
            for row, missing in items
        ]
        user = (
            "The following candidates contain numbers in statement/conditions that do not appear in their quotes. "
            "For each candidate either (a) add an exact verbatim quote from the span text that contains the number, or "
            "(b) rewrite the statement/conditions without the ungrounded number. Keep everything else. "
            "Return {\"candidates\": [...]} with the same Candidate shape, one per input candidate, in order.\n\n"
            f"span_id: {span['span_id']}\nSPAN TEXT:\n<<<\n{text}\n>>>\n\nPROBLEMS:\n{json.dumps(problems, ensure_ascii=False)}"
        )
        try:
            payload, _usage = self.clients["extract"].complete_json(
                system=self.prompts["extract"],
                user=user,
                stage="extract_repair",
                max_tokens=self.settings.extract_max_tokens,
                reasoning_effort=self.settings.extract_reasoning_effort,
                label=f"{label}:repair",
            )
        except Exception as exc:
            logger.warning("Number repair failed for %s: %s", label, exc)
            return []
        rows = payload.get("candidates")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    # --------------------------------------------------------- stage: consolidate
    def _stage_consolidate(self, candidates: list[Candidate]) -> list[dict[str, Any]]:
        stage = self._start("consolidate", "Global consolidation")
        by_id = {candidate.candidate_id: candidate for candidate in candidates}
        if not candidates:
            stage.finish(claim_count=0)
            return []
        candidates = self._premerge_restatements(candidates, by_id)
        groups = _chunk(candidates, self.settings.max_candidates_per_consolidation)
        merged_claims: list[dict[str, Any]] = []
        dropped: list[dict[str, Any]] = []
        if len(groups) == 1:
            claims, group_dropped = self._consolidate_group(groups[0], by_id, label="group0")
            merged_claims.extend(claims)
            dropped.extend(group_dropped)
        else:
            with ThreadPoolExecutor(max_workers=min(4, len(groups))) as executor:
                results = list(executor.map(lambda item: self._consolidate_group(item[1], by_id, label=f"group{item[0]}"), enumerate(groups)))
            for claims, group_dropped in results:
                merged_claims.extend(claims)
                dropped.extend(group_dropped)
        merged_claims = self._validate_merges(merged_claims, by_id)
        merged_claims = self._enforce_retention(merged_claims, candidates, by_id)
        self.trace["candidates"] = [
            {
                "id": candidate.candidate_id,
                "span_id": candidate.span_id,
                "page": candidate.page,
                "statement": candidate.statement,
                "importance": candidate.importance,
                "claim_type": candidate.claim_type,
                "quotes": len(candidate.quotes),
            }
            for candidate in candidates
        ]
        final = self._finalize_claims(merged_claims, by_id)
        final = self._hygiene(final)
        final = self._merge_near_duplicates(final, threshold=self.settings.cross_group_duplicate_threshold if len(groups) > 1 else None)
        self.trace["consolidation"] = {
            "input_candidates": len(candidates),
            "groups": len(groups),
            "claims": len(final),
            "dropped": dropped,
        }
        stage.finish(claim_count=len(final), dropped_count=len(dropped))
        logger.info("staged.consolidate candidates=%s claims=%s dropped=%s", len(candidates), len(final), len(dropped))
        return final

    def _consolidate_group(
        self,
        group: list[Candidate],
        by_id: dict[str, Candidate],
        *,
        label: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        compact = [
            {
                "id": candidate.candidate_id,
                "page": candidate.page,
                "statement": candidate.statement,
                "conditions": candidate.conditions,
                "importance": candidate.importance,
                "claim_type": candidate.claim_type,
                "status": candidate.status,
                "quotes": [q["quote"][:220] for q in candidate.quotes[:3]],
            }
            for candidate in group
        ]
        user = (
            f"PAPER TITLE: {self.paper.get('title')}\n\nCANDIDATES ({len(compact)}):\n{json.dumps(compact, ensure_ascii=False)}\n\n"
            "Return the JSON object now."
        )
        try:
            payload, _usage = self.clients["consolidate"].complete_json(
                system=self.prompts["consolidate"],
                user=user,
                stage="consolidate",
                max_tokens=self.settings.consolidate_max_tokens,
                reasoning_effort=self.settings.consolidate_reasoning_effort,
                label=label,
            )
        except Exception as exc:
            logger.warning("Consolidation failed (%s); keeping candidates as singleton claims: %s", label, exc)
            self.trace["warnings"].append({"stage": "consolidate", "label": label, "error": str(exc)[:400]})
            return [self._singleton_claim(candidate) for candidate in group], []

        claims_out: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for row in payload.get("claims") or []:
            if not isinstance(row, dict):
                continue
            ids = [str(item) for item in (row.get("candidate_ids") or []) if str(item) in by_id and str(item) not in seen_ids]
            if not ids:
                continue
            seen_ids.update(ids)
            claims_out.append(
                {
                    "candidate_ids": ids,
                    "statement": _clean(row.get("statement")),
                    "conditions": _clean(row.get("conditions")),
                    "status": _pick(row.get("status"), STATUS, by_id[ids[0]].status),
                    "falsification_criteria": _clean(row.get("falsification_criteria")),
                    "importance": _pick(row.get("importance"), IMPORTANCE, by_id[ids[0]].importance),
                    "claim_type": _pick(row.get("claim_type"), CLAIM_TYPES, by_id[ids[0]].claim_type),
                    "topic": _clean(row.get("topic")) or by_id[ids[0]].section_name or "Results",
                }
            )
        dropped_out: list[dict[str, Any]] = []
        for row in payload.get("dropped") or []:
            if not isinstance(row, dict):
                continue
            candidate_id = str(row.get("candidate_id") or "")
            if candidate_id in by_id and candidate_id not in seen_ids:
                seen_ids.add(candidate_id)
                dropped_out.append({"candidate_id": candidate_id, "statement": by_id[candidate_id].statement[:160], "reason": _clean(row.get("reason"))[:240]})
        # Safety: never lose a candidate silently. Unmentioned candidates stay as singleton claims.
        for candidate in group:
            if candidate.candidate_id not in seen_ids:
                claims_out.append(self._singleton_claim(candidate))
        return claims_out, dropped_out

    def _singleton_claim(self, candidate: Candidate) -> dict[str, Any]:
        return {
            "candidate_ids": [candidate.candidate_id],
            "statement": candidate.statement,
            "conditions": candidate.conditions,
            "status": candidate.status,
            "falsification_criteria": "",
            "importance": candidate.importance,
            "claim_type": candidate.claim_type,
            "topic": candidate.section_name or "Results",
        }

    def _claim_as_candidate(self, claim: dict[str, Any], by_id: dict[str, Candidate]) -> Candidate:
        primary = by_id[claim["candidate_ids"][0]]
        quotes: list[dict[str, str]] = []
        for candidate_id in claim["candidate_ids"]:
            quotes.extend(by_id[candidate_id].quotes)
        return Candidate(
            candidate_id=f"m{abs(hash(tuple(claim['candidate_ids']))) % 10_000_000}",
            span_id=primary.span_id,
            page=primary.page,
            section_name=primary.section_name,
            statement=claim["statement"] or primary.statement,
            conditions=claim["conditions"] or primary.conditions,
            claim_type=claim["claim_type"],
            status=claim["status"],
            importance=claim["importance"],
            quotes=quotes,
            evidence_summary=primary.evidence_summary,
            evidence_method=primary.evidence_method,
            presentation_type=primary.presentation_type,
        )

    def _finalize_claims(self, merged: list[dict[str, Any]], by_id: dict[str, Candidate]) -> list[dict[str, Any]]:
        final: list[dict[str, Any]] = []
        for claim in merged:
            members = [by_id[candidate_id] for candidate_id in claim["candidate_ids"] if candidate_id in by_id]
            if not members:
                continue
            members.sort(key=lambda candidate: (_importance_rank(candidate.importance), candidate.page or 0))
            primary = members[0]
            pooled: list[dict[str, Any]] = []
            seen: set[tuple[str, str]] = set()
            for member in members:
                for quote in member.quotes:
                    quote_span = str(quote.get("span_id") or member.span_id)
                    key = (quote_span, quote["quote"])
                    if key in seen:
                        continue
                    seen.add(key)
                    pooled.append({"span_id": quote_span, "quote": quote["quote"], "role": quote["role"], "page": quote.get("page", member.page) if quote.get("span_id") else member.page})
            statement = claim["statement"] or primary.statement
            conditions = claim["conditions"] or primary.conditions
            quote_texts = [row["quote"] for row in pooled]
            grounded_fallback = False
            if ungrounded_numbers(f"{statement} {conditions}", quote_texts):
                statement = primary.statement
                conditions = primary.conditions
                grounded_fallback = True
                if ungrounded_numbers(f"{statement} {conditions}", quote_texts):
                    # Should not happen (primary passed verification) but never emit ungrounded numbers.
                    continue
            falsification = claim.get("falsification_criteria") or _default_falsification(statement, conditions)
            member_ids: list[str] = []
            for member in members:
                member_ids.append(member.candidate_id)
                member_ids.extend(member.verification.get("absorbed", []))
            final.append(
                {
                    "candidate_ids": member_ids,
                    "statement": statement,
                    "conditions": conditions or "Not available from provided input",
                    "status": claim["status"],
                    "falsification_criteria": falsification,
                    "importance": claim["importance"],
                    "claim_type": claim["claim_type"],
                    "topic": claim["topic"],
                    "sources": pooled,
                    "span_ids": sorted({row["span_id"] for row in pooled}),
                    "pages": sorted({row["page"] for row in pooled if row.get("page") is not None}),
                    "evidence": {
                        "summary": primary.evidence_summary,
                        "method": primary.evidence_method,
                        "presentation_type": primary.presentation_type,
                        "member_summaries": [member.evidence_summary for member in members[1:4]],
                    },
                    "statement_fallback": grounded_fallback,
                }
            )
        return final

    def _premerge_restatements(self, candidates: list[Candidate], by_id: dict[str, Candidate]) -> list[Candidate]:
        """Fold obvious restatements (high token overlap AND a shared number) into one candidate before consolidation.

        The surviving candidate keeps the union of quotes; the absorbed ids are
        recorded in ``verification['absorbed']`` so the claim metadata still lists
        every original candidate.
        """
        threshold = self.settings.premerge_overlap
        kept: list[Candidate] = []
        absorbed = 0
        for candidate in candidates:
            target = None
            numbers = set(numbers_in(candidate.statement))
            for existing in kept:
                overlap = jaccard(existing.statement, candidate.statement)
                shared_number = bool(numbers & set(numbers_in(existing.statement)))
                if overlap >= threshold or (shared_number and overlap >= threshold * 0.75):
                    target = existing
                    break
            if target is None:
                kept.append(candidate)
                continue
            absorbed += 1
            seen = {(q["quote"]) for q in target.quotes}
            for quote in candidate.quotes:
                if quote["quote"] not in seen:
                    target.quotes.append({**quote, "span_id": quote.get("span_id") or candidate.span_id, "page": quote.get("page", candidate.page)})
                    seen.add(quote["quote"])
            target.verification.setdefault("absorbed", []).append(candidate.candidate_id)
            if _importance_rank(candidate.importance) < _importance_rank(target.importance):
                target.importance = candidate.importance
        if absorbed:
            self.trace["warnings"].append({"stage": "consolidate", "premerged_restatements": absorbed})
        return kept

    def _validate_merges(self, merged: list[dict[str, Any]], by_id: dict[str, Candidate]) -> list[dict[str, Any]]:
        """Split members out of a merged claim when they do not restate the primary proposition.

        Over-merging silently forfeits Silver coverage; under-merging only costs a
        duplicate that Silver consolidation folds into one unit. So the guard is
        biased toward splitting: a member stays only when it shares enough
        wording, or a number, with the group's primary candidate.
        """
        out: list[dict[str, Any]] = []
        split_count = 0
        for claim in merged:
            ids = [candidate_id for candidate_id in claim["candidate_ids"] if candidate_id in by_id]
            if len(ids) <= 1:
                out.append(claim)
                continue
            members = sorted((by_id[candidate_id] for candidate_id in ids), key=lambda c: (_importance_rank(c.importance), c.page or 0))
            primary = members[0]
            primary_numbers = set(numbers_in(primary.statement))
            keep: list[str] = [primary.candidate_id]
            split: list[Candidate] = []
            for member in members[1:]:
                overlap = jaccard(member.statement, primary.statement)
                shares_number = bool(primary_numbers & set(numbers_in(member.statement)))
                if overlap >= self.settings.merge_min_overlap or (shares_number and overlap >= self.settings.merge_min_overlap / 2):
                    keep.append(member.candidate_id)
                else:
                    split.append(member)
            if len(keep) > self.settings.merge_max_members:
                extra = keep[self.settings.merge_max_members:]
                keep = keep[: self.settings.merge_max_members]
                split.extend(by_id[candidate_id] for candidate_id in extra)
            if split:
                split_count += len(split)
                # The merged statement may describe the whole group; fall back to the primary wording when members leave.
                claim = {**claim, "candidate_ids": keep, "statement": primary.statement if len(split) >= 2 else claim["statement"], "conditions": primary.conditions if len(split) >= 2 else claim["conditions"]}
                for member in split:
                    out.append(self._singleton_claim(member))
            out.append(claim)
        if split_count:
            self.trace["warnings"].append({"stage": "consolidate", "merge_guard_split_members": split_count})
        return out

    def _enforce_retention(
        self,
        merged: list[dict[str, Any]],
        candidates: list[Candidate],
        by_id: dict[str, Candidate],
    ) -> list[dict[str, Any]]:
        """Fall back to deterministic merging when the consolidator collapses too far.

        The consolidation model occasionally fuses most of a paper into a handful
        of claims (13 candidates to 4 on a short paper; 120 to 37 in an earlier
        version). Every collapse forfeits coverage permanently, so when retention
        drops below the floor we discard the model's grouping and keep the
        deterministic pre-merge result instead.
        """
        if not candidates:
            return merged
        retention = len(merged) / len(candidates)
        if retention >= self.settings.retention_floor:
            return merged
        self.trace["warnings"].append(
            {
                "stage": "consolidate",
                "retention_floor_applied": True,
                "claims_from_model": len(merged),
                "candidates": len(candidates),
                "retention": round(retention, 3),
            }
        )
        kept_ids = {candidate_id for claim in merged for candidate_id in claim["candidate_ids"]}
        rebuilt = [self._singleton_claim(candidate) for candidate in candidates if candidate.candidate_id in kept_ids]
        return rebuilt or merged

    def _stage_selfcheck(self, claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Two final passes that mirror what the validator's judges reject.

        Adjudicators reject a claim for two repeatable reasons: it restates
        another claim of ours, or it says something its own quotes do not
        support (a reversed direction, a mislabelled statistic, a specific the
        source never states). Each rejection costs 0.25 of a paper's quality,
        so both are worth one cheap pass here.
        """
        stage = self._start("selfcheck", "Duplicate and support self-check")
        before = len(claims)
        claims = self._merge_restatements(claims)
        deduped = len(claims)
        claims = self._drop_unsupported(claims)
        stage.finish(before=before, after_dedupe=deduped, after_support=len(claims))
        return claims

    def _merge_restatements(self, claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(claims) < 2:
            return claims
        listing = [{"id": index, "statement": claim["statement"]} for index, claim in enumerate(claims)]
        system = (
            "You find claims that restate another claim in the same list. Two claims restate each other when a "
            "reviewer would say they assert the same scientific proposition: the same finding, direction and "
            "entities, even in different words, or one is a less specific version of the other. Claims about "
            "different assays, cell lines, cohorts, markers, timepoints, directions or outcomes are NOT "
            "restatements and must be left alone.\n"
            'Return STRICT JSON: {"groups": [[3, 17], [42, 8, 9]]} listing only ids that restate each other. '
            "Return an empty list when there are none. Never put a claim in more than one group."
        )
        try:
            payload, _usage = self.clients["consolidate"].complete_json(
                system=system,
                user=json.dumps({"claims": listing}, ensure_ascii=False),
                stage="selfcheck_dedupe",
                max_tokens=self.settings.consolidate_max_tokens,
                reasoning_effort=self.settings.consolidate_reasoning_effort,
                label="dedupe",
            )
        except Exception as exc:
            self.trace["warnings"].append({"stage": "selfcheck", "dedupe_error": str(exc)[:200]})
            return claims
        removed: set[int] = set()
        merged_count = 0
        for group in payload.get("groups") or []:
            ids = [int(i) for i in group if isinstance(i, int | str) and str(i).isdigit() and int(i) < len(claims)]
            ids = [i for i in dict.fromkeys(ids) if i not in removed]
            if len(ids) < 2:
                continue
            # Keep the most specific survivor: most grounded quotes, then longest statement.
            keeper = max(ids, key=lambda i: (len(claims[i]["sources"]), len(claims[i]["statement"])))
            for other in ids:
                if other == keeper:
                    continue
                seen = {(row["span_id"], row["quote"]) for row in claims[keeper]["sources"]}
                for row in claims[other]["sources"]:
                    if (row["span_id"], row["quote"]) not in seen:
                        claims[keeper]["sources"].append(row)
                claims[keeper]["candidate_ids"] = list(dict.fromkeys(claims[keeper]["candidate_ids"] + claims[other]["candidate_ids"]))
                if _importance_rank(claims[other]["importance"]) < _importance_rank(claims[keeper]["importance"]):
                    claims[keeper]["importance"] = claims[other]["importance"]
                removed.add(other)
                merged_count += 1
        if merged_count:
            for claim in claims:
                claim["span_ids"] = sorted({row["span_id"] for row in claim["sources"]})
            self.trace["warnings"].append({"stage": "selfcheck", "restatements_merged": merged_count})
        return [claim for index, claim in enumerate(claims) if index not in removed]

    def _drop_unsupported(self, claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
        system = (
            "You check whether each claim is supported by its own quoted source text. Answer for each claim:\n"
            '- "ok": the quotes support the statement as written.\n'
            '- "contradicted": the quotes state the opposite direction, the opposite preference, or a different '
            "comparison operator or threshold than the statement.\n"
            '- "mislabelled": the statement describes what a number measures differently from the quotes '
            "(for example calling median values proportions, or a count a percentage).\n"
            '- "unsupported": the statement asserts a specific entity, cohort, experiment or result that does not '
            "appear in the quotes at all.\n"
            "Judge only against the quotes given. Wording differences and added context are fine; be strict only "
            "about direction, what a number measures, and invented specifics.\n"
            'Return STRICT JSON: {"results": {"<id>": {"verdict": "ok|contradicted|mislabelled|unsupported"}}} '
            "with one entry per claim."
        )
        flagged: set[int] = set()
        counts: dict[str, int] = {}
        size = max(5, self.settings.selfcheck_batch)
        for start in range(0, len(claims), size):
            chunk = claims[start : start + size]
            payload_claims = [
                {
                    "id": start + offset,
                    "statement": claim["statement"],
                    "quotes": [row["quote"] for row in claim["sources"][:6]],
                }
                for offset, claim in enumerate(chunk)
            ]
            try:
                payload, _usage = self.clients["consolidate"].complete_json(
                    system=system,
                    user=json.dumps({"claims": payload_claims}, ensure_ascii=False),
                    stage="selfcheck_support",
                    max_tokens=self.settings.consolidate_max_tokens,
                    reasoning_effort=self.settings.consolidate_reasoning_effort,
                    label=f"support:{start}",
                )
            except Exception as exc:
                self.trace["warnings"].append({"stage": "selfcheck", "support_error": str(exc)[:200]})
                continue
            for key, row in (payload.get("results") or {}).items():
                if not isinstance(row, dict) or not str(key).isdigit():
                    continue
                verdict = str(row.get("verdict") or "ok")
                counts[verdict] = counts.get(verdict, 0) + 1
                if verdict in {"contradicted", "mislabelled", "unsupported"}:
                    index = int(key)
                    if 0 <= index < len(claims):
                        flagged.add(index)
        if flagged:
            self.trace.setdefault("consolidation", {})["selfcheck_dropped"] = [
                {"statement": claims[i]["statement"][:180]} for i in sorted(flagged)
            ]
            self.trace["warnings"].append({"stage": "selfcheck", "unsupported_dropped": len(flagged), "verdicts": counts})
        return [claim for index, claim in enumerate(claims) if index not in flagged]

    def _hygiene(self, claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Drop claims an adjudicator would reject outright.

Two classes, both measured against the paper text and both validated
        against 5,497 real claims: statements that are never a scientific
        proposition (data-availability lines, ethics boilerplate, bare
        statistics sentences, placeholders), and concentration/mass/temperature
        values the paper never states (2 uM where the source says 2 mM).

        An entity-name check and a signature-based duplicate merge were tried
        and removed: they dropped 30 and merged 108 judge-accepted claims
        respectively while catching almost nothing.
        """
        kept: list[dict[str, Any]] = []
        dropped: list[dict[str, Any]] = []
        for claim in claims:
            statement = claim["statement"]
            reason = ""
            detail: list[str] = []
            if is_non_claim(statement):
                reason = "not_a_scientific_claim"
            else:
                missing_units = ungrounded_measurements(statement, self.full_text)
                if missing_units:
                    reason, detail = "measurement_not_in_paper", missing_units
            if reason:
                dropped.append({"statement": statement[:180], "reason": reason, "detail": detail})
                continue
            kept.append(claim)
        if dropped:
            self.trace.setdefault("consolidation", {})["hygiene_dropped"] = dropped
            self.trace["warnings"].append({"stage": "consolidate", "hygiene_dropped": len(dropped)})
        return kept

    def _merge_near_duplicates(self, claims: list[dict[str, Any]], *, threshold: float | None = None) -> list[dict[str, Any]]:
        limit = threshold if threshold is not None else self.settings.near_duplicate_threshold
        merged: list[dict[str, Any]] = []
        removed = 0
        for claim in claims:
            duplicate_of = None
            for existing in merged:
                if jaccard(existing["statement"], claim["statement"]) >= limit:
                    duplicate_of = existing
                    break

            if duplicate_of is None:
                merged.append(claim)
                continue
            removed += 1
            duplicate_of["candidate_ids"] = list(dict.fromkeys(duplicate_of["candidate_ids"] + claim["candidate_ids"]))
            seen = {(row["span_id"], row["quote"]) for row in duplicate_of["sources"]}
            for row in claim["sources"]:
                if (row["span_id"], row["quote"]) not in seen:
                    duplicate_of["sources"].append(row)
            duplicate_of["span_ids"] = sorted({row["span_id"] for row in duplicate_of["sources"]})
            if _importance_rank(claim["importance"]) < _importance_rank(duplicate_of["importance"]):
                duplicate_of["importance"] = claim["importance"]
        if removed:
            self.trace["warnings"].append({"stage": "consolidate", "near_duplicates_merged": removed})
        return merged

    # ------------------------------------------------------------ stage: structure
    def _stage_structure(self, claims: list[dict[str, Any]]) -> dict[str, Any]:
        stage = self._start("structure", "Structure synthesis")
        if not claims:
            stage.finish(skipped=True)
            return {}
        # Order by importance, then by page: the validator's per-paper adjudication
        # budget retains candidates round-robin by index, so central claims must
        # come first to survive any truncation.
        claims.sort(key=lambda claim: (_importance_rank(claim["importance"]), min(claim["pages"]) if claim["pages"] else 999))
        if self.settings.max_claims and len(claims) > self.settings.max_claims:
            # Minor-tier units carry at most 5% of Silver coverage weight; beyond the cap they only add
            # adjudication exposure and validator time, so keep the highest-importance claims.
            self.trace["warnings"].append({"stage": "structure", "claims_capped": len(claims) - self.settings.max_claims, "cap": self.settings.max_claims})
            del claims[self.settings.max_claims :]
        for index, claim in enumerate(claims, start=1):
            claim["claim_id"] = f"C{index:02d}" if index < 100 else f"C{index}"
        compact = [
            {
                "claim_id": claim["claim_id"],
                "statement": claim["statement"],
                "conditions": claim["conditions"][:220],
                "topic": claim["topic"],
                "importance": claim["importance"],
                "claim_type": claim["claim_type"],
                "span_ids": claim["span_ids"],
                "quote": claim["sources"][0]["quote"][:160] if claim["sources"] else "",
            }
            for claim in claims
        ]
        user = (
            f"{self._paper_context()}\n\nKNOWN PAPER METADATA: {json.dumps({k: v for k, v in self.paper.items() if k in ('paper_id', 'title', 'doi', 'year', 'venue')}, ensure_ascii=False)}\n\n"
            f"FINAL CLAIMS ({len(compact)}):\n{json.dumps(compact, ensure_ascii=False)}\n\nReturn the JSON object now."
        )
        try:
            payload, _usage = self.clients["structure"].complete_json(
                system=self.prompts["structure"],
                user=user,
                stage="structure",
                max_tokens=self.settings.structure_max_tokens,
                reasoning_effort=self.settings.structure_reasoning_effort,
                label="structure",
            )
        except Exception as exc:
            logger.warning("Structure stage failed; using deterministic fallback: %s", exc)
            self.trace["warnings"].append({"stage": "structure", "error": str(exc)[:400]})
            payload = {}
        stage.finish(
            experiments=len(payload.get("experiments") or []),
            concepts=len(payload.get("concepts") or []),
            trace_children=len(((payload.get("trace") or {}).get("children")) or []),
        )
        return payload

    # ------------------------------------------------------------- stage: assemble
    def _stage_assemble(self, claims: list[dict[str, Any]], structure: dict[str, Any]) -> dict[str, Any]:
        stage = self._start("assemble", "Artifact assembly")
        claim_ids = [claim["claim_id"] for claim in claims]
        claim_by_id = {claim["claim_id"]: claim for claim in claims}
        source_counter = [0]

        def source_ref(span_id: str, quote: str | None, role: str) -> dict[str, Any]:
            source_counter[0] += 1
            return {
                "source_id": f"S{source_counter[0]:03d}",
                "source_type": "span",
                "path": None,
                "span_ids": [span_id],
                "quote": quote,
                "role": role if role in VALID_ROLES else "result",
            }

        def verified_ref(span_ids: list[Any], quote: Any, role: str) -> list[dict[str, Any]]:
            refs: list[dict[str, Any]] = []
            valid = [str(span_id) for span_id in (span_ids or []) if str(span_id) in self.span_by_id]
            if not valid:
                return refs
            quote_text = _clean(quote)
            if quote_text:
                for span_id in valid:
                    match = locate_quote(str(self.span_by_id[span_id]["text"]), quote_text, min_chars=self.settings.min_quote_chars)
                    if match is not None:
                        refs.append(source_ref(span_id, match.quote[: self.settings.max_quote_chars] if match.quote[: self.settings.max_quote_chars] in str(self.span_by_id[span_id]["text"]) else match.quote, role))
                        return refs
            refs.append(source_ref(valid[0], None, role))
            return refs

        # Final deterministic guard: every pooled quote must be a substring of its span (whitespace-insensitive).
        dropped_quotes = 0
        for claim in claims:
            verified_sources = []
            for row in claim["sources"]:
                span = self.span_by_id.get(str(row["span_id"]))
                match = locate_quote(str(span["text"]), row["quote"], min_chars=1) if span else None
                if match is None or match.method == "fuzzy" and match.similarity < 0.999:
                    dropped_quotes += 1
                    continue
                verified_sources.append({**row, "quote": match.quote})
            if verified_sources:
                claim["sources"] = verified_sources
        if dropped_quotes:
            self.trace["warnings"].append({"stage": "assemble", "unverifiable_quotes_dropped": dropped_quotes})
        # Evidence records: one per claim (distinct support basis) with pooled quotes.
        evidence_records: list[dict[str, Any]] = []
        experiments: list[dict[str, Any]] = []
        artifact_claims: list[dict[str, Any]] = []
        evidence_id_by_claim: dict[str, str] = {}
        for index, claim in enumerate(claims, start=1):
            evidence_id = f"EV{index:02d}" if index < 100 else f"EV{index}"
            evidence_id_by_claim[claim["claim_id"]] = evidence_id
            refs = [source_ref(row["span_id"], row["quote"], row["role"]) for row in claim["sources"]]
            evidence_records.append(
                {
                    "evidence_id": evidence_id,
                    "title": _evidence_title(claim),
                    "role": "support",
                    "summary": _grounded_summary(claim),
                    "evidence_method": claim["evidence"]["method"],
                    "outcome_type": _outcome_type(claim),
                    "presentation_type": claim["evidence"]["presentation_type"],
                    "source_refs": refs,
                    "linked_claim_ids": [claim["claim_id"]],
                    "metadata": {"pages": claim["pages"], "candidate_ids": claim["candidate_ids"]},
                }
            )

        # Experiments from the structure stage, with fallback coverage.
        verified_claim_ids: set[str] = set()
        topic_of = {claim["claim_id"]: claim["topic"] for claim in claims}
        for row in structure.get("experiments") or []:
            if not isinstance(row, dict):
                continue
            verifies = [str(item) for item in (row.get("verifies") or []) if str(item) in claim_by_id]
            if not verifies:
                continue
            refs = verified_ref(row.get("span_ids") or [], row.get("quote"), "method")
            if not refs:
                first_claim = claim_by_id[verifies[0]]
                refs = [source_ref(first_claim["span_ids"][0], None, "method")]
            experiments.append(
                {
                    "experiment_id": "",
                    "title": _clean(row.get("title")) or f"Analysis: {topic_of.get(verifies[0], 'Results')}",
                    "verifies": verifies,
                    "setup": _strip_numbers_if_ungrounded(_clean(row.get("setup")), refs) or "Not available from provided input",
                    "procedure": _strip_numbers_if_ungrounded(_clean(row.get("procedure")), refs) or "Not available from provided input",
                    "expected_outcome": _strip_numbers(_clean(row.get("expected_outcome"))) or "Directional outcome consistent with the verified claims.",
                    "evidence_ids": [evidence_id_by_claim[claim_id] for claim_id in verifies],
                    "run": _clean(row.get("run")) or None,
                    "source_refs": refs,
                }
            )
            verified_claim_ids.update(verifies)
        leftover = [claim for claim in claims if claim["claim_id"] not in verified_claim_ids]
        by_topic: dict[str, list[dict[str, Any]]] = {}
        for claim in leftover:
            by_topic.setdefault(claim["topic"], []).append(claim)
        for topic, members in by_topic.items():
            first = members[0]
            experiments.append(
                {
                    "experiment_id": "",
                    "title": f"Analysis of {topic}",
                    "verifies": [claim["claim_id"] for claim in members],
                    "setup": _strip_numbers(first["conditions"]) or "Not available from provided input",
                    "procedure": _strip_numbers(first["evidence"]["method"]) or "Not available from provided input",
                    "expected_outcome": "The reported comparison shows the direction stated in the verified claims.",
                    "evidence_ids": [evidence_id_by_claim[claim["claim_id"]] for claim in members],
                    "run": None,
                    "source_refs": [source_ref(first["span_ids"][0], None, "method")],
                }
            )
        for index, experiment in enumerate(experiments, start=1):
            experiment["experiment_id"] = f"E{index:02d}" if index < 100 else f"E{index}"
        experiment_ids_by_claim: dict[str, list[str]] = {claim_id: [] for claim_id in claim_ids}
        for experiment in experiments:
            for claim_id in experiment["verifies"]:
                experiment_ids_by_claim[claim_id].append(experiment["experiment_id"])

        for claim in claims:
            artifact_claims.append(
                {
                    "claim_id": claim["claim_id"],
                    "statement": claim["statement"],
                    "conditions": claim["conditions"],
                    "status": claim["status"],
                    "falsification_criteria": claim["falsification_criteria"],
                    "proof": experiment_ids_by_claim[claim["claim_id"]],
                    "evidence_ids": [evidence_id_by_claim[claim["claim_id"]]],
                    "dependencies": [],
                    "sources": [source_ref(row["span_id"], row["quote"], row["role"]) for row in claim["sources"]],
                    "metadata": {
                        "importance": claim["importance"],
                        "claim_type": claim["claim_type"],
                        "topic": claim["topic"],
                        "pages": claim["pages"],
                        "candidate_ids": claim["candidate_ids"],
                    },
                }
            )

        concepts: list[dict[str, Any]] = []
        for index, row in enumerate(structure.get("concepts") or [], start=1):
            if not isinstance(row, dict):
                continue
            label = _clean(row.get("label"))
            definition = _clean(row.get("definition"))
            if not label or not definition:
                continue
            concepts.append(
                {
                    "concept_id": f"K{index:02d}",
                    "label": label,
                    "definition": definition,
                    "source_refs": verified_ref(row.get("span_ids") or [], row.get("quote"), "input"),
                }
            )

        trace = self._build_trace(structure.get("trace") or {}, claims, verified_ref, source_ref)
        paper_meta = self._paper_metadata(structure.get("paper") or {}, claims)

        artifact = {
            "ara_version": "1.0",
            "paper": paper_meta,
            "logic": {
                "problem_observations": _str_list(structure.get("problem_observations")),
                "gaps": _str_list(structure.get("gaps")),
                "key_insight": _clean(structure.get("key_insight")) or (claims[0]["statement"] if claims else "Not available from provided input"),
                "assumptions": _str_list(structure.get("assumptions")),
                "claims": artifact_claims,
                "concepts": concepts,
                "experiments": experiments,
                "related_work": _str_list(structure.get("related_work")),
                "constraints": _str_list(structure.get("constraints")) or [claim["statement"] for claim in claims if claim["claim_type"] == "limitation"][:10],
            },
            "evidence": {
                "records": evidence_records,
                "ledger_notes": _str_list(structure.get("ledger_notes")) or [
                    f"{len(evidence_records)} evidence records, one per distinct support basis; quotes are verbatim span substrings.",
                ],
            },
            "trace": trace,
            "src": {
                "environment": _str_list(structure.get("environment")),
                "artifacts": _str_list(structure.get("artifacts")),
            },
            "metadata": {
                "staged_runtime": {
                    "candidate_count": sum(len(claim["candidate_ids"]) for claim in claims),
                    "claim_count": len(claims),
                    "importance_counts": _count(claim["importance"] for claim in claims),
                    "claim_type_counts": _count(claim["claim_type"] for claim in claims),
                }
            },
        }
        stage.finish(claims=len(artifact_claims), evidence=len(evidence_records), experiments=len(experiments), concepts=len(concepts))
        return artifact

    def _build_trace(self, trace_payload: dict[str, Any], claims: list[dict[str, Any]], verified_ref, source_ref) -> dict[str, Any]:
        claim_ids = {claim["claim_id"] for claim in claims}
        covered: set[str] = set()
        node_counter = [0]

        def build(node: dict[str, Any], depth: int, parent_id: str) -> dict[str, Any] | None:
            if not isinstance(node, dict):
                return None
            node_counter[0] += 1
            node_id = f"{'Q' if node.get('node_type', 'question') == 'question' else 'R'}{node_counter[0]}"
            evidence = [str(item) for item in (node.get("claim_ids") or node.get("evidence") or []) if str(item) in claim_ids]
            covered.update(evidence)
            support = "explicit" if node.get("support_level") == "explicit" and (node.get("span_ids") or evidence) else "inferred"
            refs = verified_ref(node.get("span_ids") or [], node.get("quote"), "interpretation") if node.get("span_ids") else []
            if support == "explicit" and not refs and evidence:
                first = next(claim for claim in claims if claim["claim_id"] == evidence[0])
                refs = [source_ref(first["span_ids"][0], None, "result")]
            children = []
            if depth < 4:
                for child in node.get("children") or []:
                    built = build(child, depth + 1, node_id)
                    if built:
                        children.append(built)
            return {
                "node_id": node_id,
                "node_type": str(node.get("node_type") or "question"),
                "support_level": support,
                "summary": _clean(node.get("summary")) or "Not available from provided input",
                "source_refs": refs,
                "evidence": evidence,
                "children": children,
            }

        children = []
        for child in trace_payload.get("children") or []:
            built = build(child, 1, "Q0")
            if built:
                children.append(built)
        leftover = [claim for claim in claims if claim["claim_id"] not in covered]
        if leftover:
            by_topic: dict[str, list[dict[str, Any]]] = {}
            for claim in leftover:
                by_topic.setdefault(claim["topic"], []).append(claim)
            for topic, members in by_topic.items():
                node_counter[0] += 1
                first = members[0]
                children.append(
                    {
                        "node_id": f"R{node_counter[0]}",
                        "node_type": "result",
                        "support_level": "explicit",
                        "summary": f"Results on {topic}",
                        "source_refs": [source_ref(first["span_ids"][0], None, "result")],
                        "evidence": [claim["claim_id"] for claim in members],
                        "children": [],
                    }
                )
        root_summary = _clean(trace_payload.get("summary")) or f"What does the paper '{self.paper.get('title')}' establish?"
        root_refs = []
        if self.spans:
            root_refs = [source_ref(str(self.spans[0]["span_id"]), None, "input")]
        return {
            "node_id": "Q0",
            "node_type": "question",
            "support_level": "explicit" if root_refs else "inferred",
            "summary": root_summary,
            "source_refs": root_refs,
            "evidence": [],
            "children": children,
        }

    def _paper_metadata(self, structure_paper: dict[str, Any], claims: list[dict[str, Any]]) -> dict[str, Any]:
        paper = dict(self.paper)
        for key in ("title", "venue", "doi", "domain", "abstract"):
            value = _clean(structure_paper.get(key))
            if value and not paper.get(key):
                paper[key] = value
        for key in ("authors", "keywords"):
            values = _str_list(structure_paper.get(key))
            if values and not paper.get(key):
                paper[key] = values
        year = structure_paper.get("year")
        if isinstance(year, int) and not paper.get("year"):
            paper["year"] = year
        summary = _str_list(structure_paper.get("claims_summary"))
        if not summary:
            summary = [claim["statement"] for claim in claims if claim["importance"] == "central"][:8]
        paper["claims_summary"] = summary
        paper.setdefault("authors", [])
        paper.setdefault("keywords", [])
        paper.setdefault("abstract", "")
        return paper


# --------------------------------------------------------------------- helpers
def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False)
    return re.sub(r"\s+", " ", str(value)).strip()


def _pick(value: Any, allowed: set[str], default: str) -> str:
    normalized = str(value or "").strip().lower().replace(" ", "_")
    return normalized if normalized in allowed else default


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_clean(item) for item in value if _clean(item)]


def _chunk(items: list[Any], size: int) -> list[list[Any]]:
    size = max(1, size)
    return [items[index : index + size] for index in range(0, len(items), size)]


def _split_text(text: str, max_chars: int) -> list[str]:
    paragraphs = re.split(r"\n\s*\n", text)
    parts: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if current and len(current) + len(paragraph) + 2 > max_chars:
            parts.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        parts.append(current)
    return parts or [text]


def _looks_like_boilerplate_only(text: str) -> bool:
    lowered = text.casefold()
    lines = [line for line in lowered.splitlines() if line.strip()]
    if len(lines) < 8:
        return False
    reference_like = sum(1 for line in lines if re.match(r"^\s*(\d{1,3}\.|\[\d{1,3}\])\s", line) or re.search(r"\b(19|20)\d{2}\b.*\b(doi|https?://|et al)", line))
    return reference_like / max(1, len(lines)) > 0.6


def _importance_rank(value: str) -> int:
    return {"central": 0, "supporting": 1, "minor": 2}.get(value, 1)


def _default_falsification(statement: str, conditions: str) -> str:
    scope = conditions if conditions and conditions != "Not available from provided input" else "the same study design"
    return f"Under {scope}, observing the opposite direction or no difference for the relationship stated ('{statement[:120]}') would refute this claim."


def _evidence_title(claim: dict[str, Any]) -> str:
    pages = ",".join(str(page) for page in claim["pages"]) if claim["pages"] else "n/a"
    return f"{claim['topic']} (p. {pages}): {claim['statement'][:80]}"


def _grounded_summary(claim: dict[str, Any]) -> str:
    summary = claim["evidence"]["summary"] or claim["statement"]
    quotes = [row["quote"] for row in claim["sources"]]
    if ungrounded_numbers(summary, quotes):
        summary = claim["statement"]
    return summary


def _outcome_type(claim: dict[str, Any]) -> str:
    return {
        "quantitative_result": "quantitative_result",
        "predictive_performance": "quantitative_result",
        "association": "association",
        "mechanism": "mechanistic_interpretation",
        "method_contribution": "method_property",
        "validation": "replication",
        "resource_description": "descriptive",
        "limitation": "limitation",
        "implication": "interpretation",
    }.get(claim["claim_type"], "qualitative_result")


def _strip_numbers(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"\(?(?<![A-Za-z0-9\-/_.])[-+]?\d+(?:[.,]\d+)*\s*(%|percent|-fold|×|x)?(?![A-Za-z0-9/_])\)?", "", text)
    cleaned = re.sub(r"\s+", " ", cleaned).replace(" ,", ",").replace(" .", ".").strip()
    return cleaned


def _strip_numbers_if_ungrounded(text: str, refs: list[dict[str, Any]]) -> str:
    if not text:
        return ""
    quotes = [ref.get("quote") or "" for ref in refs]
    if ungrounded_numbers(text, quotes):
        return _strip_numbers(text)
    return text


def _count(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts
