# Claim granularity guide

## What is ONE claim

A claim is one scientific proposition that a judge could mark true or false
against this paper: a finding, association, effect, mechanism, method property,
model performance, or stated limitation. Two claims are distinct when they
differ in at least one of: the entity measured, the population or model
system, the comparison made, the direction or magnitude of effect, the
outcome, or the mechanism asserted.

## Claim types to sweep for (use as `metadata.claim_type`)

| type | what to look for | example statement skeleton |
|---|---|---|
| `quantitative_result` | numbers, percentages, fold changes, AUC, HR, OR, p-values, counts | "X was higher in A than B (median 10.2% vs 3.1%, p = 0.004)." |
| `comparative_finding` | group/condition/tissue differences without a number | "Cluster X was enriched in tumor tissue relative to adjacent nontumor tissue." |
| `association` | correlations, prognostic/predictive links, co-occurrence | "High abundance of X was associated with worse overall survival." |
| `mechanism` | causal or pathway statements the authors assert or infer | "X promotes Y via Z signalling." (status hypothesis if inferred) |
| `method_contribution` | new atlas, panel, model, algorithm, pipeline, assay, cohort, resource | "A 42-marker mass cytometry panel resolved N immune populations across three tissue compartments." |
| `validation` | replication in a cohort, orthogonal assay, external dataset | "The signature was validated in an independent cohort of N patients." |
| `predictive_performance` | classifier/biomarker metrics | "The model predicted response with AUC 0.87." |
| `resource_description` | dataset size/composition when it is a contribution | "The atlas comprises >10 million cells from 25 tumors, 24 adjacent tissues and 23 blood samples." |
| `limitation` | explicit limitations, caveats, negative results | "The authors report that spatial information was not captured." |
| `implication` | clinical/applied conclusions the authors draw | "The authors propose X as a candidate biomarker for immunotherapy response." |

## Split or merge?

- Split when the sentence lists several independent findings ("A increased,
  B decreased, C was unchanged" -> up to three claims, each quotable).
- Merge when the same finding is restated in abstract, results and discussion:
  one claim, pooled quotes, keep the most specific wording.
- Keep a subgroup result separate from the overall result only if the paper
  reports it as a distinct finding.
- Do NOT create separate claims for the same number reported in text and in a
  table; cite both spans on one claim.

## Never make claims from

References list, author affiliations, funding, competing interests, ethics
approval boilerplate, data/code availability statements, journal metadata,
figure/table labels without content, generic textbook background that the
paper only cites, and statements about other papers unless the paper adopts
them as its own premise (then role `input`, status per the paper).

## Statement style

- One sentence, 15-45 words, present tense, specific nouns (marker names,
  cell types, cohorts), the key number where reported, no citations, no
  "this study shows that" preamble, no run/table names as the subject.
- Conditions carry the scope: species, tissue, cohort size, method,
  comparator, timepoint, statistical test if named.
- Falsification: "Under the same panel and cohort, observing no difference in
  X between A and B (or the opposite direction) would refute this."
