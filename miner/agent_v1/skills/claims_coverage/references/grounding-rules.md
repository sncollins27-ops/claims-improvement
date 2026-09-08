# Grounding rules (what the validator actually checks)

Deterministic checks (fail = critical/major finding, score cap 0.5 for the paper):

| Check | Rule |
|---|---|
| span ids | every `span_ids[]` entry exists in `source_payload.spans[].span_id` |
| roles | `role` in {input, result, method, interpretation, metadata} |
| claim grounding | every claim has `sources[]` or `evidence_ids[]`; every evidence record has `source_refs[]` |
| cross refs | `proof` -> experiment ids, `evidence_ids` -> evidence ids, `verifies` -> claim ids, `linked_claim_ids` -> claim ids, `dependencies` -> claim ids, trace `evidence` -> claim/experiment/evidence/concept ids |
| required fields | claim statement/conditions/falsification_criteria, evidence summary, experiment setup/procedure/expected_outcome are non-empty |
| unique ids | no duplicate claim/concept/experiment/evidence/trace ids (duplicates are also silently dropped from Silver) |

Semantic checks by the rigor/diagnostic agent (findings reduce diagnostic_quality):

- `grounding_adjudication`: the cited span really contains the quote; every
  load-bearing number, sample size, p-value, threshold, identifier and unit in
  the claim appears in connected quotes/spans; multi-span assertions cite every
  span they need.
- `scope_calibration`: the claim does not generalise beyond the population,
  model, comparator or conditions in the evidence.
- `evidence_relevance`: linked evidence substantively supports the claim (not
  a neighbouring result, not boilerplate).
- `falsifiability_quality`: criteria are specific, actionable and scoped.
- `argument_coherence` / `exploration_integrity` / `methodological_rigor`:
  layers agree with each other; the trace is honest; methods are adequate.

Adjudication (each rejected claim costs 0.25 of quality):

- Judges see: statement, conditions, your quotes, your evidence summaries, and
  the cited spans' text. They reject claims that the spans do not support,
  claims that contradict the spans, over-generalisations, and trivial or
  non-scientific statements.
- They accept refinements and splits as long as each piece is supported.

Practical consequences:

1. Copy quotes character-for-character from `span.text`; do not fix typos,
   do not join sentences across pages, do not add ellipses inside a quote.
2. Put the number in the quote, then in the statement, in the same form
   (`10.2%` not `10 percent`; `n = 25` not `25 patients` unless quoted).
3. A claim about a figure must quote the figure legend or the sentence in the
   text that reports the finding; never quote only a figure label.
4. If the abstract states a finding and results give the number, the claim
   should carry BOTH quotes (abstract sentence + results sentence), each on its
   own SourceRef with its own span id.
5. Do not include derived numbers (differences, ratios you computed).
6. Author interpretations ("suggesting that", "may reflect") are claims with
   status `hypothesis` and role `interpretation`; state them as the authors'
   interpretation, not as fact.
