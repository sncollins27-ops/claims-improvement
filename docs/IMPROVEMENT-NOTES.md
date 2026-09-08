# Improvement notes (2026-09-07)

## 1. How mainnet actually scores a paper

From `validator/agent_v1/silver_scoring.py`, `orchestrator.py`, `silver_builder.py`
and the validator skills (`claims-silver-comparator`, `claims-silver-adjudicator`,
`claims-silver-canonicalizer`, `claims-diagnostic-batch`):

1. Every miner artifact is projected to **candidates** = its `logic.claims[]`
   (statement, conditions as qualifier, quotes, span ids, linked evidence).
   Duplicate claim ids drop the whole claim.
2. A **Bronze** reference extraction (the subnet's own reference miner) supplies
   reference candidates. A comparator agent links each miner candidate to
   reference candidates (`semantic_equivalent`, `compatible_refinement`,
   `compatible_split_merge`, `partial_overlap`, `contradiction`).
3. Two anonymous judges (plus a tiebreaker) adjudicate every case: linked
   pairs, Bronze-only candidates, and **miner-only candidates** ("is this a
   valid, paper-relevant improvement?"). Accepted miner-only claims become
   Silver units too (`accepted_improvement`).
4. A canonicalizer + auditor merge all accepted candidates into unique
   **Silver units**, assign `central / supporting / minor`, and exclude
   trivial or irrelevant ones.
5. Score per paper:

   ```
   coverage = weighted share of scored Silver units this miner has an equivalent for
              (central 0.70, supporting 0.30, minor 0.10; minor tier capped at 5%)
   diagnostic_quality = 1 - Σ penalties(structural + grounding + rigor findings)
   adjudication_quality = 1 - 0.25 × (# of this miner's claims rejected by judges)
   score = coverage × diagnostic_quality × adjudication_quality
   ```

   Batch score = mean over eligible papers; validator-failed papers are
   excluded for everyone, missing papers count as 0.

Consequences:

- **Coverage is the whole game.** Every distinct proposition any miner or the
  reference extracted becomes a unit you either match or miss. 3-7 claims
  match at most 3-7 units.
- **Each rejected claim costs 0.25**, so precision matters just as much: no
  unsupported numbers, no over-generalisation, no bibliographic trivia.
- **Deterministic grounding failures cap the paper at 0.5** (`quote_not_in_source`,
  `missing_source_span`, `no_source_refs`).
- `CLAIMS_SILVER_MAX_ELIGIBLE_CLAIMS_PER_MINER=1000` on mainnet: no cap.

## 2. What the winner looks like

Run `run_20260907_121647_21873c` (2026-09-07): UID 125 ranked first with batch
score 0.8848 (16 eligible papers, 34 validator-failed). On paper 1
(`openalex_w4415340077`, ESCC single-cell atlas, 14 pages) it submitted
**77 claims and 77 evidence records** and scored 1.0 (coverage 1.0, no findings,
no rejections). Its other papers: many 1.0s, a few 0.75 (= exactly one rejected
claim) and one 0.14 (coverage 0.19: it missed most units on that paper).

Public API does not expose miner artifacts, so the winner's text cannot be
downloaded; the shape (one claim per distinct grounded finding, one evidence
record per claim) is inferred from counts and the scoring code.

## 3. Why UID 192 scored 0

`runs/neuron/mainnet` logs from the old repo show all 50 papers failing in
seconds with `OpenAI's reasoning models require passing temperature=1.0 or None
and max_tokens >= 16000` (dspy + `openrouter/openai/gpt-5-mini`). The .env was
later corrected (temperature 1.0, max_tokens 16384), and a local run then
produced **3 claims** (the compiler skill's 3-7 target) at $0.08 and 4 min.

## 4. What changed here

| area | before | after |
|---|---|---|
| skill | ARA universal compiler (markdown ARA, 3-7 claims, screenshots) | `claims_coverage`: Claims-only, coverage-first, grounding rules, granularity guide, stage prompts |
| runtime | one dspy ReAct loop, whole paper in one prompt, 16k output budget | `staged`: per-page extraction in parallel → deterministic quote/number verification with repair → conservative consolidation (+merge guard) → structure synthesis → assembly |
| grounding | model-side only | `locate_quote` relocates quotes to exact substrings; numbers must appear in quotes; identifiers (CD8, IL-6) are not numbers |
| evaluation | none | `tools/evaluate.py`: validator structural+grounding passes, local rigor audit, LLM self-judge |
| visibility | pm2 logs | dashboard: runs, claim-by-claim audit with span highlighting, merges, findings, network, agenda |

## 5. Results on paper 1

| run | claims | evidence | findings (crit/maj/min/warn) | judge | quality est. | cost | time |
|---|---|---|---|---|---|---|---|
| baseline (dspy-react + stock compiler skill) | 4 | 4 | 12/0/2/0 (12 × `quote_not_in_source`: quotes are paraphrased) | 4 accept | 0.0 (real validator: cap 0.5, then −0.25 per critical) | $0.09 | 303 s |
| staged-v2 (first staged) | 37 | 37 | 0/0/7/6 | 37 accept | 0.73 (local, experiment-number nits) | $0.19 | 488 s |
| staged-v3 (merge guard, low-effort parallel consolidation, id-regex fix) | 130 | 130 | 0/0/0/0 | 129 accept, 1 weak, 0 reject | 1.0 | $0.18 (+$0.04 judge) | 318 s |
| staged-v4 (deterministic pre-merge, refined prompts, importance ordering) | 122 | 122 | 2/0/0/0 (pre-merge attributed an absorbed quote to the wrong span) | 120 accept, 2 weak | 0.5 | $0.16 | 317 s |
| **staged-v5** (span-correct pre-merge + final quote guard, assertion/comparator/scope rules, 150-claim cap) | **120** | 120 | **0/0/0/0** | **120 accept, 0 weak, 0 reject** | **1.0** | $0.17 | 371 s |

v2's 120 candidates collapsed to 37 claims because the consolidation model
merged distinct method steps and subset results into single "workflow"
claims (one claim had 18 members). v3 makes merging conservative in the
prompt and adds a deterministic split guard (a member must share ≥0.3 token
Jaccard or a number with the group's primary statement); it then merged
almost nothing (131 → 130), leaving a handful of true restatements (same AUC
reported twice, three phrasings of one survival association) and a few
"model X was used" descriptions. v4 adds a deterministic pre-merge (token
Jaccard ≥ 0.5, or ≥ 0.375 with a shared number), tells the consolidator
which restatements to fold and which descriptions to drop, and orders claims
central → supporting → minor so the validator's per-paper case budget
(round-robin by claim index across miners) never truncates central claims.

## 5b. Five more papers from the same mainnet batch (staged-v3 code)

| paper | pages | claims | span coverage | findings | judge | cost | extraction time |
|---|---|---|---|---|---|---|---|
| openalex_w3208308706 (intranasal mRNA antibody) | 8 | 69 | 88% | 0 | 67 accept / 2 weak | $0.11 | 283 s |
| openalex_w4323535544 (circRNA DICAR) | 12 | 116 | 83% | 0 | 115 / 1 weak | $0.15 | 302 s |
| openalex_w4323670197 (microbiota-immune metasystem) | 26 | 84 | 50% | 0 | 82 / 2 weak | $0.22 | 321 s |
| openalex_w4366603018 (NEMO calcium indicators) | 20 | 158 | 95% | 0 | 158 accept | $0.27 | 403 s |
| openalex_w7130652695 (PHGDH serine, macrophages) | 41 | 313 | 93% | 2 warnings | 308 / 5 weak | $0.40 | 507 s |

No claim was rejected by the local judge on any paper; "weak" verdicts are
scope or comparator wording issues, now addressed by prompt rules in v5 and
by the 150-claim importance-ordered cap (the 313-claim paper is an outlier
with supplementary pages). Average extraction time 366 s/paper → ~38 min for
50 papers on 8 workers.

## 5c. Where v5 landed

Paper 1: 120 claims, 120 evidence records, 33 experiments, 93% of pages
cited, zero structural/grounding/local findings, and every claim accepted by
the local judge with no weak verdicts. The pre-merge folded 3 restatements,
the split guard rescued 47 members the consolidator had over-merged, and one
near-duplicate pair was merged deterministically.

## 5d. Confirmation batch with v5 code, and deployment

The same five papers re-run with v5: 606 claims total, **600 accept, 4 weak,
2 reject**, zero grounding findings on four of five papers. Both rejects were
duplicate claims (one lexically close at 0.54 Jaccard, one only semantically
close at 0.32), so the deterministic near-duplicate threshold moved to 0.50
and the consolidation prompt gained an explicit mechanism-restatement rule.
Extraction averaged 392 s/paper, projecting ~41 min for 50 papers at 8
workers.

Deployed to mainnet on 2026-09-07 18:08 UTC: `pm2 stop claims-miner-sn111`
(the old `../Claims` app, kept stopped for rollback) and
`pm2 start scripts/ecosystem.config.cjs` from this repo as
`claims-miner-sn111-improved`. Same wallet `<wallet> / <hotkey>`, same axon
`<external-ip>:8091`. Startup confirmed: "Miner registered with uid 192",
"Serving Claims miner axon on port 8091", harness `staged`, runtime
`StagedRuntime`, skill `claims_coverage`, 8 paper workers x 4 extract
concurrency, outputs to `runs/neuron/mainnet` so the dashboard sees live
tasks.

## 5e. Full 50-paper replay (v5, held-out batch)

`batch_20260907_d7b6080b73fb` (mainnet run `run_20260907_063159_471c07`,
06:31 UTC), 50 papers, 8 workers, with the local judge.

| metric | value |
|---|---|
| papers | 50 / 50 completed |
| claims | 5,497 total; mean 110, median 128, min 4, max 150 |
| extraction cost | $12.41 ($0.248/paper) |
| judge cost | $2.65 |
| total | $15.06 |
| extraction time | mean 450 s, median 403 s, slowest 1,328 s |
| wall clock | 4,745 s (79 min) including judging; extraction alone projects to ~47 min |
| diagnostic quality | mean 0.9950 (41/50 perfect) |
| adjudication quality | mean 0.9150 (36/50 perfect) |
| combined quality | mean 0.9105 (31/50 perfect) |
| judge verdicts | 5,402 accept, 78 weak, 17 reject (0.31% reject rate) |

22 of the 50 papers were in the validator's scored set. Projected batch score
(our measured quality times an assumed coverage):

| coverage assumption | projected batch score | rank it would have taken |
|---|---|---|
| median of all 11 real miners (includes four that scored 0) | 0.5669 | 6-7 |
| median of miners that actually scored | 0.7373 | 3 |
| best real miner per paper | 0.8515 | 1 |
| 1.0 upper bound | 0.9061 | 1 |

The real winner (UID 249) scored 0.7762. Caveat: coverage is measured against
a Silver record that would itself change if we participated, because accepted
miner claims become Silver units. These are indicative bands, not predictions.

Losses are now almost entirely adjudication, not grounding: diagnostic quality
0.995 across 50 papers, while 17 rejected claims cost 0.25 each on their
paper. Three papers carry most of the damage.

## 5f. Claim hygiene: what the 17 rejects taught, and what survived validation

Reject causes across 5,497 claims: 9 semantic duplicates, 5 transcription
errors (entity typo, unit prefix, reversed direction, mislabelled statistic),
2 unsupported specifics, 1 empty statement.

Four deterministic rules were built and each measured against all 5,497
claims before shipping:

| rule | catches | wrongly drops judge-accepted claims | shipped |
|---|---|---|---|
| not-a-scientific-claim (data availability, ethics, bare statistics, placeholder) | 2 rejects | 19, all boilerplate Silver excludes anyway | yes |
| measurement not in paper (2 uM vs 2 mM) | 1 reject | 5 | yes |
| entity name not in paper | 0 rejects | 30 | **no** |
| signature-based duplicate merge (same entities + same numbers) | 1 reject | 108 | **no** |

Shipped rules drop 27 of 5,497 claims (0.49%). The two rejected rules would
have cost far more coverage than the quality they bought. The remaining
duplicate rejects are a consolidation-prompt problem, not a filter problem.

## 5g. Measured coverage of pre-existing Silver units

The public API never publishes Silver unit text, so coverage was measured
rather than assumed:

1. `tools/make_bronze.py` reconstructs the reference extraction with the
   subnet's own `claims-reference-miner`, run against the untouched Claims
   checkout in a subprocess with a minimal environment. Every Bronze claim is a
   required Silver unit in `build_silver_record`. 22 papers, 81 units,
   3-5 units per paper.
2. `tools/estimate_coverage.py` judges each unit against our claims with the
   validator's own comparator wording (equivalent / refinement / split-merge
   count as the same unit), three independent passes with a majority vote.
3. `score_miner_against_silver` and `score_batch`, the validator's real
   functions, produce coverage and the batch score.

| metric | value |
|---|---|
| Bronze units reconstructed | 81 across 22 papers |
| units our claims matched | 71 |
| mean coverage | 0.8854 |
| estimated batch score (coverage x measured quality) | 0.8017 |
| real winner on that batch (UID 249) | 0.7762 |
| papers where we beat the median scoring miner | 13 of 22 |

Two measurement defects were found and fixed while building this, both of
which had biased coverage downward:

- a judge pass that silently dropped units scored them as misses; unjudged
  units are now retried and then excluded rather than counted against us;
- a unit the judge matched but labelled with a malformed submission id was
  counted as a miss; the closest claim by wording is now named instead.

Judge noise is real and worth stating: 16 of 81 units had a split vote across
three passes, and single-pass runs of the same comparison produced batch
estimates from 0.75 to 0.85. The three-pass majority is the number to quote.

### Correction: Bronze is only part of the Silver denominator

Coverage is scored against **Silver** units, not Bronze. Silver =
Bronze-anchored required units + accepted-improvement units contributed by the
participating miners, after canonicalization and importance filtering. Our
reconstruction rebuilds only the Bronze-anchored part.

How big is the gap? Each paper's real coverage values share a denominator, so
fitting that denominator across the 11 miners recovers the paper's true total
Silver weight. Against our reconstructed Bronze weight:

| | ratio real Silver weight to reconstructed Bronze weight |
|---|---|
| median | 2.25 |
| range on well-constrained papers | 0.59 to 3.48 |

(Papers whose fit returned very large denominators, 40+, are unreliable: with
11 miners a large denominator can fit spuriously, so those are weak evidence.)

So a typical real Silver record is about twice the weight of Bronze alone, and
0.8854 was measured against roughly half the real denominator.

That makes the estimate **uncertain in both directions, not a floor** (an
earlier note in this file called it a floor; that was wrong):

- units our own accepted claims would create raise numerator and denominator
  together, pushing our coverage up and every other miner's down;
- units other miners contributed raise the denominator, and we only match the
  ones we happen to state, pushing our coverage down.

With roughly 110 claims per paper against a reference of 3 to 5, we plausibly
match a large share of the improvement units, but that is reasoning, not
measurement. The honest summary: coverage on the reference backbone is 0.885,
and the full-Silver number cannot be known without participating.

## 5h. Scored against the REAL Silver units

The Silver units turned out to be reachable after all. The public dashboard
server-renders them when the run and paper are named as query parameters:

    https://dashboard.claims111.ai/runs?run_id=<run_id>&paper_id=<paper_id>

`tools/fetch_silver_units.py` lifts them out of the React Server Component
stream; `tools/score_against_real_silver.py` judges our claims against them and
scores with the validator's own `score_miner_against_silver`. This supersedes
the Bronze reconstruction and the competitor simulation, both of which were
proxies for exactly this data.

Result on `run_20260907_063159_471c07`, 22 papers, 1,404 real units:

| metric | value |
|---|---|
| real Silver units | 1,404 |
| units our claims matched | 1,101 |
| mean coverage | 0.9506 |
| our measured quality | 0.9105 |
| estimated batch score | 0.8640 |
| real winner (UID 249) | 0.7762 |
| papers at coverage 1.000 | 17 of 22 |
| papers beating the median scoring miner | 14 of 22 |

Two corrections this exposed, both of which had understated us badly:

1. **Bronze is a small fraction of Silver.** Reconstructed Bronze gave 3-5
   units per paper. The real records hold 2 to 606, median about 9, because
   accepted miner claims become units too. The papers where my denominator
   fit had returned "implausible" values of 52 were not artifacts: they really
   do carry 533 and 606 units.
2. **Silver units are merged composites.** Each pools 17 to 138 candidate
   claims. My first judge asked whether one of our claims restates the whole
   composite, which fine-grained claims never do, and it scored 3/6 on a paper
   where we in fact cover all six. Reframed to the canonicalizer's actual
   question - would this claim be pooled into the unit - the same paper scores
   6/6 unanimously. A negative control (three unrelated papers' units against
   one paper's claims) correctly returned zero matches on all 12 units, so the
   reframed judge still discriminates.

Coverage is now effectively solved at 0.95. Every remaining large loss except
one is quality, not coverage:

| paper | score | coverage loss | quality loss |
|---|---|---|---|
| w7166575844 | 0.247 | 0.000 | 0.752 |
| w4224249713 | 0.309 | 0.588 | 0.250 |
| w4406365831 | 0.728 | 0.000 | 0.272 |
| w4324381152 | 0.750 | 0.000 | 0.250 |

`w4224249713` is the one real coverage failure: a 3-page research highlight
where our extraction produced only 4 claims against 8 units. Short commentary
pieces need their own handling.

## 6. Open risks

- **Timing**: the validator gives 1 h for 50 papers. With 8 paper workers the
  staged runtime must average < ~9 min/paper including queueing; measure with
  `tools/run_batch.py`.
- **Provider throughput**: 50 papers × ~25 calls each in parallel can hit
  OpenRouter rate limits; the client retries with backoff, but the extract
  concurrency (`SUBNET_CLAIMS_STAGED_EXTRACT_CONCURRENCY`) may need lowering.
- **Judge realism**: the local judge is one gpt-5-mini pass; the real Silver
  path uses a comparator + two judges + canonical audit with other models.
  Calibrate against the next real evaluation of UID 192.
- **Reference (Bronze) style**: units the reference extracts that we phrase
  very differently may not be linked by the comparator. Keeping the paper's
  own wording and numbers in statements maximises linkability.
