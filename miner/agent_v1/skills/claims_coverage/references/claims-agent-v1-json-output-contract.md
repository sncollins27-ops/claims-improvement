# Claims agent_v1 JSON output contract (coverage edition)

Return STRICT JSON ONLY. No markdown fences, no commentary. `agent_schema.json`
in the run directory is authoritative when anything here is ambiguous.

## Top-level shape

```json
{
  "ara_version": "1.0",
  "paper": {
    "paper_id": "...", "title": "...", "authors": ["..."], "year": 2024,
    "venue": "...", "doi": "...", "domain": "...", "keywords": ["..."],
    "abstract": "...", "claims_summary": ["one line per central finding"]
  },
  "logic": {
    "problem_observations": ["..."], "gaps": ["..."], "key_insight": "...",
    "assumptions": ["..."],
    "claims": [ { ...Claim... } ],
    "concepts": [ { "concept_id": "K01", "label": "...", "definition": "...", "source_refs": [ SourceRef ] } ],
    "experiments": [ { "experiment_id": "E01", "title": "...", "verifies": ["C01"], "setup": "...",
                       "procedure": "...", "expected_outcome": "...", "evidence_ids": ["EV01"],
                       "run": "...", "source_refs": [ SourceRef ] } ],
    "related_work": ["..."], "constraints": ["..."]
  },
  "evidence": {
    "records": [ { "evidence_id": "EV01", "title": "...", "role": "support", "summary": "...",
                   "evidence_method": "...", "outcome_type": "...", "presentation_type": "text|table|figure|mixed",
                   "source_refs": [ SourceRef ], "linked_claim_ids": ["C01"], "metadata": {} } ],
    "ledger_notes": ["..."]
  },
  "trace": { "node_id": "Q0", "node_type": "question", "support_level": "explicit|inferred",
             "summary": "...", "source_refs": [ SourceRef ], "evidence": ["C01"], "children": [ ...TraceNode ] },
  "src": { "environment": ["..."], "artifacts": ["..."] },
  "metadata": {}
}
```

## Claim

```json
{
  "claim_id": "C01",
  "statement": "One specific scientific proposition of this paper, with its key number when reported.",
  "conditions": "Population/system, setting, method, comparator, stated caveats.",
  "status": "supported | partially_supported | hypothesis",
  "falsification_criteria": "A concrete observation under the same conditions that would refute the statement.",
  "proof": ["E01"],
  "evidence_ids": ["EV01"],
  "dependencies": [],
  "sources": [
    { "source_id": "S01", "source_type": "span", "path": null,
      "span_ids": ["<paper>-span-0004"], "quote": "exact contiguous substring of that span", "role": "result" }
  ],
  "metadata": { "importance": "central | supporting | minor", "claim_type": "..." }
}
```

## SourceRef rules

- `source_type` is always `"span"`, `path` is `null`.
- `span_ids` contains only ids present in `source_payload.spans[].span_id`.
  One quote belongs to exactly one span: never put two span ids on one quote.
- `quote` is a verbatim contiguous substring of that span's `text`
  (whitespace differences are tolerated, nothing else). 40-300 characters.
- `role` is one of `input`, `result`, `method`, `interpretation`, `metadata`.
  Use `result` for reported findings/numbers, `method` for procedures and
  setups, `interpretation` for the authors' explanations, `input` for
  definitions/background, `metadata` for paper metadata.
- Every load-bearing number in `statement`/`conditions` must appear in one of
  the claim's own quotes (or in a quote of a linked evidence record).

## Coverage targets

- Full-length research article (8-20 pages): 40-100 claims. Short paper or
  letter: 15-40. Review article: one claim per distinct reviewed finding or
  synthesised conclusion the review asserts, typically 30-80.
- `evidence.records`: at least one record per claim; records may be shared by
  claims that rest on the same figure/table/test, but do not share one record
  across unrelated claims.
- `logic.experiments`: one per distinct experimental/analytical procedure
  (typically 5-20), each verifying the claims it produced.
- `logic.concepts`: 8-30 defined technical terms with source refs.
- `trace`: root question plus one child per major research thread, each with
  result nodes whose `evidence` lists claim ids.

## Identifier conventions

`C01..C99` claims (zero-padded, use `C100` beyond 99), `EV01..`, `E01..`,
`K01..` concepts, trace node ids `Q0`, `Q1`, `R1_1`, ... All unique.
