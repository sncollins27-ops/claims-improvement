# Version history — what changed, why, and what it proved

Every version below was measured on the same paper, `openalex_w4415340077`
(a 14-page single-cell atlas of esophageal squamous cell carcinoma), so the
numbers are directly comparable. v5 and v6 were then tested at scale.

| version | claims | evidence | diagnostic | judge a/w/r | quality | cost | time |
|---|---|---|---|---|---|---|---|
| v1 baseline (stock) | 4 | 4 | 0.00 | 4/0/0 | **0.000** | $0.092 | 303 s |
| v2 first staged | 37 | 37 | 0.73 | 37/0/0 | 0.730 | $0.188 | 488 s |
| v3 | 130 | 130 | 1.00 | 129/1/0 | **1.000** | $0.181 | 318 s |
| v4 | 122 | 122 | 0.50 | 120/2/0 | 0.500 | $0.157 | 317 s |
| v5 | 120 | 120 | 1.00 | 120/0/0 | **1.000** | $0.174 | 369 s |
| v6 | see below | | | | | | |

Consolidation behaviour on the same paper:

| version | candidates extracted | claims after consolidation | notes |
|---|---|---|---|
| v2 | 120 | **37** | model over-merged, one claim absorbed 18 candidates |
| v3 | 131 | 130 | merge guard added; now barely merges at all |
| v4 | 127 | 122 | pre-merge 1, guard split 51 members |
| v5 | 128 | 120 | pre-merge 3, guard split 47, dedupe 1 |

---

## v1 — the stock pipeline (baseline)

**What it is.** Upstream `agent_v1`: one DSPy ReAct loop, the whole paper in one
prompt, mounting the ARA `compiler` skill.

**Core logic.** Give a capable model the paper and a skill describing an ARA
artifact; let it decide what to extract in a single pass.

**Why it fails, measured.** Two independent failures.

1. *Coverage.* The compiler skill's contract asks for **3-7 claims**. It
   produced 4. Silver records for these papers hold 6 to 600 units, so a
   4-claim artifact forfeits the coverage term before quality is considered.
2. *Grounding.* All 12 of its quotes failed the validator's exact-substring
   check. The model paraphrased, inserted ellipses, and normalised characters.
   `quote_not_in_source` is a critical finding that caps a paper near 0.5 and
   then subtracts further. Final quality: **0.000**.

**The lesson that shaped everything after.** A single agent loop cannot be
trusted to copy text verbatim, and cannot be trusted to be exhaustive. Both
have to be enforced by code outside the model.

---

## v2 — the staged runtime

**Core idea.** Stop asking one loop to do everything. Split the work into
stages where each stage has one job, and put deterministic verification between
the model and the artifact.

```
extract      one call per page, in parallel, asked to be exhaustive
ground       code relocates every quote to an exact substring of that page;
             code checks every number in the claim appears in a quote;
             one repair call for what fails, then drop the rest
consolidate  one call merges restatements across the whole paper
structure    one call builds experiments, concepts, the trace
assemble     code emits the artifact
```

**The grounding step is the heart of it.** `locate_quote` tries exact match,
then whitespace/punctuation-normalised match, then a fuzzy match, and returns
the *source's own characters*. The model never supplies the final quote text,
so `quote_not_in_source` becomes structurally impossible.

**Result.** 37 claims, zero grounding findings, span coverage 93%. Quality 0.73,
losing only on nits about experiment sections.

**What went wrong.** 120 candidates collapsed into 37 claims. The consolidation
model fused distinct findings: one claim absorbed **18** candidates, merging
separate method steps and separate subset results into a single "workflow"
claim. Every such merge is coverage thrown away.

---

## v3 — stop the over-merging

**Core idea.** Merging is asymmetric in cost. An unmerged duplicate costs at
most one rejected claim; a wrong merge costs a Silver unit forever. So bias the
system toward splitting.

**Changes.**
1. **Merge guard** — after the model proposes a group, code checks each member
   against the group's primary claim. A member survives only if it shares ≥0.3
   token overlap, or shares a number and ≥0.15. Otherwise it is split back out
   as its own claim.
2. **Conservative consolidation prompt** — spelled out that different markers,
   subsets, cohorts, comparisons and directions are distinct claims, and that a
   group with more than four members is a red flag.
3. **Parallel consolidation** in chunks of 70, which also cut runtime.
4. **Identifier-aware number regex.** The number checker was treating `CD206`,
   `IL-6`, `ARL67156` and `4NQO` as numbers and demanding they appear in
   quotes. Fixed by requiring numbers not be glued to letters.

**Result.** 130 claims, 0 findings, 129 accept / 1 weak, quality **1.000**, and
faster than v2 (318 s vs 488 s).

**What it revealed.** The guard swung the pendulum: 131 candidates became 130
claims, so almost nothing merged. True restatements survived — the same AUC
reported twice, three phrasings of one survival association.

---

## v4 — deterministic pre-merge, and a bug worth studying

**Core idea.** Don't ask the model to catch restatements it demonstrably
misses. Catch the obvious ones in code *before* consolidation, where "obvious"
means high token overlap, or a shared number plus moderate overlap.

**Changes.**
1. **`_premerge_restatements`** — deterministic folding at Jaccard ≥ 0.5, or
   ≥ 0.375 with a shared number. The survivor absorbs the other's quotes.
2. **Importance ordering** — claims emitted central → supporting → minor,
   because the validator's per-paper adjudication budget is filled round-robin
   by claim index across miners. If truncation happens, it must hit minor
   claims.
3. **150-claim cap**, applied after that ordering.

**Result: quality fell to 0.500.** Two `quote_not_in_source` findings appeared —
the exact failure v2 had eliminated.

**The bug.** When the pre-merge absorbed a candidate, it copied the quote text
but not the quote's *span id*. The absorbed quote was then attributed to the
surviving candidate's page. A quote genuinely present on page 7 was cited
against page 4, so the validator's check failed correctly.

**The fix, and the principle.** Quotes now carry their own span id through
every merge, and a final guard at assembly re-verifies every quote against its
cited span, dropping any that fail. Deterministic verification belongs at the
*end* of the pipeline as well as the middle, because later stages can break
what earlier stages established.

---

## v5 — the version that was measured at scale

**Changes over v4.**
1. Span-correct pre-merge (the v4 bug fixed).
2. **Final quote guard** at assembly — every pooled quote re-verified against
   its own span; anything unverifiable is dropped rather than shipped.
3. **Comparator and scope rules** in the extraction prompt: state the
   comparison exactly as the source does, never widen population or model
   system, keep author hedging as hedging.
4. **Claim hygiene** — a deterministic filter dropping statements that are
   never scientific propositions (data-availability lines, ethics boilerplate,
   bare statistics sentences, placeholders) and claims whose concentration or
   mass units the paper never states (`2 µM` where the source says `2 mM`).

**The hygiene filter is also a lesson in discipline.** Four rules were built
and each was measured against all 5,497 claims of the 50-paper run before
shipping:

| rule | rejects caught | judge-accepted claims wrongly dropped | shipped |
|---|---|---|---|
| not-a-scientific-claim | 2 | 19, all boilerplate Silver excludes anyway | yes |
| measurement not in paper | 1 | 5 | yes |
| entity name not in paper | 0 | 30 | **no** |
| signature-based duplicate merge | 1 | 108 | **no** |

The two rejected rules looked clever and were measurably harmful. Shipped
rules drop 0.49% of claims.

**Measured at scale** — 50 papers, then scored against the 1,404 real Silver
units of that batch:

| metric | value |
|---|---|
| claims | 5,497 (mean 110/paper) |
| coverage vs real Silver units | **0.9506** (1,101 of 1,404 matched) |
| quality | 0.9105 |
| estimated batch score | **0.8640** |
| the batch's actual winner | 0.7762 |
| cost / time | $15.06, 450 s/paper |

---

## v6 — precision, from reading the 17 rejections

**Core idea.** With coverage at 0.95, the remaining loss is adjudication
quality. Each rejected claim costs 0.25 of a paper's score. So read every
rejection and fix the causes rather than guessing.

**The 17 rejections split cleanly.**

- **9 restatements** — our own claim saying what another already said, in
  different words. All nine were single-candidate claims on both sides, so the
  merge guard was not the cause; the consolidator simply left them. They shared
  only 32-54% of their words, which is why the deterministic threshold missed
  them.
- **8 semantic errors against a claim's own quotes** — a threshold copied
  inverted (`≥ 2` where the source says `< 2`), medians relabelled as
  proportions, a reversed preference (the authors prioritised contrastive
  methods; the claim said masking), and specifics the span never mentions.

**Changes.**
1. **`_merge_restatements`** — one call over the *final* claim list asking which
   claims restate each other; groups merge, quotes pool, the most specific
   survives. Semantic rather than lexical, which is what the nine required.
2. **`_drop_unsupported`** — claims batched with their own quotes and labelled
   `ok` / `contradicted` / `mislabelled` / `unsupported`; anything not `ok` is
   dropped. This is the same question the validator's rigor agent asks.
3. **Retention floor** — if consolidation returns fewer than 55% of candidates
   as claims, discard the model's grouping and keep the deterministic
   pre-merge result. Aimed at short papers, where a 3-page commentary had
   collapsed 13 candidates into 4 claims.
4. **Sharpened extraction rules** for operators, what a number measures,
   preference direction, and not naming cohorts absent from the span. These are
   in the SkillPack, so they apply even when the v6 switches are off.

**Results.**

On the six papers that carried the rejections:

| | v5 | v6 |
|---|---|---|
| rejects | 8 | **0** |
| weak | 6 | 4 |
| mean quality | 0.660 | **0.998** |
| claims | 578 | 556 |

On the 3-page commentary, scored against its 8 real Silver units:

| | claims | units matched | coverage |
|---|---|---|---|
| v5 | 4 | 2 of 8 | 0.412 |
| v6 | 10 | 6 of 8 | **0.824** |

On a fresh 32-page review (`s13045-021-01191-2`, TLR/glioma): 150 claims,
**0 findings**, 149 accept / 1 weak / 0 reject, quality **1.000**, $0.43,
12.8 min. 352 candidates extracted, 12 dropped for ungrounded quotes, 3
restatements merged, 6 dropped as unsupported.

**Still unproven.** v6 has never been scored against real Silver units across a
full 50-paper batch. The open risk is `_drop_unsupported` deleting genuine
findings on papers that were already perfect, which shows up as coverage loss,
not as an error.

---

## The through-line

Six versions, one repeated pattern: **every durable gain came from moving a
guarantee out of the model and into code**, and every regression came from a
model being trusted with something code should have checked.

- verbatim quotes → `locate_quote` relocates them; the model never supplies the
  final text
- numbers → checked against connected quotes, with identifiers excluded
- span attribution → carried through merges, re-verified at assembly
- over-merging → merge guard, then retention floor
- under-merging → deterministic pre-merge, then a semantic restatement pass
- claim validity → hygiene filters, each measured for false positives before
  shipping

The model's job is proposing candidates and judging meaning. Everything that
can be checked mechanically, is.
