---
name: claims_coverage
description: |
  Claims subnet coverage-first claim compiler. Converts one scientific paper
  (delivered as ordered source spans) into a strict Claims agent_v1 JSON
  artifact whose claim set covers EVERY distinct, source-supported scientific
  proposition in the paper, with every claim grounded by verbatim quotes.
  Optimised for the SN111 Silver scoring rule: score = coverage x quality.
argument-hint: "<run-dir with request.json, source_payload.json, agent_schema.json>"
allowed-tools: Read, Write, Edit, Bash(python *), Glob, Grep
metadata:
  author: claims-improvement-james
  category: claims-mining
  version: "2.0.0"
  tags: [claims, extraction, grounding, coverage, sn111]
---

# Claims Coverage Compiler (agent_v1, v2)

You compile ONE scientific paper into ONE `agent_output.json` for the Claims
subnet. The validator scores you with

```
paper score = coverage x diagnostic_quality x adjudication_quality
```

- **coverage**: fraction (importance-weighted: central 0.70, supporting 0.30,
  minor 0.10) of the canonical "Silver" claim units of the paper that one of
  YOUR claims is judged equivalent to. Silver units come from a reference
  extraction plus every accepted claim from every miner. A Silver unit you have
  no equivalent for is a miss. Missing a central unit is the single most
  expensive mistake.
- **diagnostic_quality**: 1.0 minus penalties for concrete rigor findings on
  your artifact (critical -0.25, major -0.10, minor -0.03): ungrounded numbers,
  quotes that are not in the cited span, unresolved ids, empty required fields,
  claims that overreach their evidence, vague falsification criteria.
- **adjudication_quality**: 1.0 minus 0.25 for EVERY one of your claims that
  the anonymous judges reject (unsupported, contradicted, over-generalised,
  trivial/bibliographic, or not a scientific proposition of this paper).

So the winning artifact is: **many claims, each one a distinct scientific
proposition of this paper, each one verbatim-grounded, none of them
rejectable.** Winners submit 40-100 claims per full-length paper with zero
rejections. A 3-7 claim artifact cannot win: it forfeits most of the coverage.

## Inputs (all in the run directory)

- `request.json`: task metadata and file names.
- `paper.json`: known paper metadata (title, ids). May be sparse.
- `source_payload.json`: `spans[]`, each `{span_id, section_name, page, text}`.
  Spans are usually one PDF page of markdown. **Only these span_ids exist.**
- `agent_schema.json`: authoritative JSON Schema of the output.
- `validation_feedback.json`: deterministic issues from a previous attempt.

Read `references/claims-agent-v1-json-output-contract.md` for the exact JSON
shape, `references/grounding-rules.md` for what "grounded" means to the
validator, and `references/claim-granularity-guide.md` for what counts as one
claim.

## Method (staged, exhaustive, verified)

1. **Sweep every span in order.** For each span, list every distinct
   scientific proposition it supports: quantitative results, qualitative
   findings, comparisons between groups/conditions, mechanisms and causal
   interpretations the authors assert, methodological contributions (new
   tools, panels, models, pipelines, datasets), validation results, clinical
   or applied implications the authors state, and explicit limitations. Do
   not stop at the abstract; results, figure legends, tables, discussion and
   methods pages carry most of the units.
2. **Attach verbatim quotes from that span** to each proposition. A quote is
   an exact contiguous substring of `span.text` (up to ~300 characters). Every
   number, percentage, p-value, n, fold-change, hazard ratio, marker name,
   cell-type name and threshold that appears in the claim `statement` or
   `conditions` must appear inside one of its quotes. If you cannot quote the
   number, do not write the number.
3. **Consolidate globally.** Merge restatements of the same proposition
   (abstract vs results vs discussion) into ONE claim that keeps the most
   specific grounded wording and pools all quotes. Keep materially different
   propositions separate (different population, marker, outcome, direction,
   comparison, or mechanism). Drop bibliographic, funding, licence, byline,
   ethics-boilerplate, data-availability and pure background statements: they
   never score and can be rejected.
4. **Write each claim as a takeaway with its numbers.** Statement = the
   scientific proposition, specific, self-contained, one sentence, containing
   the key quantitative anchor when the paper gives one (numbers are what make
   equivalence judgements easy). Conditions = population/system, setting,
   method, comparison, and stated caveats. Falsification criteria = a concrete
   observation that would refute it under the same conditions. Status =
   `supported` for results the paper reports; `hypothesis` only for
   author-speculated mechanisms; never `not_available` for a kept claim.
5. **Build the evidence, experiment, concept and trace layers** from the
   claims (see contract). One evidence record per distinct support basis
   (a result, table, figure panel, statistical test) with its own verbatim
   quotes; every claim links at least one; do not point every claim at one
   generic record. Experiments describe how a group of claims was tested
   without restating exact result numbers. The trace tree mirrors the
   research path (root question -> sub-questions -> results), `explicit`
   nodes carry span refs.
6. **Verify deterministically before submitting**: every quote is a substring
   of the cited span (whitespace-insensitive), every span_id exists, every
   number in statement/conditions appears in a connected quote, all ids are
   unique, every proof/evidence/verifies/linked_claim id resolves, no required
   field is empty, no two claims are restatements of each other. Fix, then
   submit. Use `validate_agent_artifact` / `submit_agent_artifact` tools when
   the runtime provides them.

## Hard rules

1. Only span ids from `source_payload.spans[].span_id`; quotes verbatim.
2. No invented results, numbers, sample sizes, methods, figures or citations.
3. Every load-bearing number in a claim appears in a connected quote. Prefer
   the paper's original expression and units; no derived percentages.
4. One claim = one scientific proposition. Split compound findings; merge
   restatements. Never pad with trivia; never collapse a paper into 3 claims.
5. Do not over-generalise: keep the population, model system, and comparator
   in `conditions`; a mouse result is a mouse result.
6. Every claim: non-empty `conditions`, `falsification_criteria`, `proof`
   (experiment ids), `evidence_ids`, `sources` (with quotes).
7. Experiments contain no exact result numbers. Method constants only when
   grounded in the experiment's own `source_refs`, else omitted.
8. If information is genuinely absent, write
   "Not available from provided input" in that field rather than guessing.
9. Return STRICT JSON only (no fences, no commentary) that validates against
   `agent_schema.json`.

## Precision self-check before finishing

Every claim an adjudicator rejects costs a quarter of the paper's quality, and
the two repeatable causes are both avoidable:

- **Restatement.** Two of your claims asserting the same proposition in
  different words. Before finishing, scan for pairs that a reviewer would call
  the same finding and merge them, keeping the more specific wording and both
  sets of quotes.
- **Unsupported wording.** A statement its own quotes do not support: a
  reversed direction or preference, a flipped comparison operator, a number
  labelled as something it is not, or a cohort or experiment the span never
  mentions. Re-read each statement against its quotes and fix or drop it.

## Coverage self-check before finishing

- Did every results/figure/table page yield claims? A results page with zero
  claims is almost always a miss.
- Are the abstract's findings each represented by a specific, quantified
  claim from the results section?
- Are method contributions (new panel, model, algorithm, cohort, assay)
  represented as claims?
- Are the authors' stated mechanisms, prognostic/predictive associations,
  and stated limitations represented?
- Is any claim a near-duplicate of another? Merge it.
- Is any claim not about this paper's science? Delete it.
