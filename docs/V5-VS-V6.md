# v5 vs v6 — handoff for another session

Both versions are the same `staged` runtime in `miner/agent_v1/staged/`. There
is no v5 branch or v5 tag: **v6 is what is committed**, and v5 is v6 with two
features switched off. The switches are in `.env`.

| | v5 (validated) | v6 (partly validated) |
|---|---|---|
| how to get it | `SUBNET_CLAIMS_STAGED_SELFCHECK=false` and `SUBNET_CLAIMS_STAGED_RETENTION_FLOOR=0` | `SELFCHECK=true`, `RETENTION_FLOOR=0.55` |
| status in production | **this is what the miner runs** | not enabled |
| validated on | 50 papers, scored against 1,404 real Silver units | 6 papers, all of which had failed under v5 |

## What v6 adds, exactly

Three code changes, all in `miner/agent_v1/staged/pipeline.py`, plus prompt
edits in `miner/agent_v1/skills/claims_coverage/`.

### 1. `_stage_selfcheck` — runs after consolidation, before structure

Called from `StagedPipeline.run()` only when `settings.selfcheck` is true. Two
LLM passes over the final claim list:

- **`_merge_restatements`** — one call listing every final claim, asking which
  ones restate each other. Groups are merged: the survivor is the claim with
  the most quotes, then the longest statement; the others' quotes and candidate
  ids are pooled into it.
- **`_drop_unsupported`** — claims batched 25 at a time with their own quotes,
  each labelled `ok` / `contradicted` / `mislabelled` / `unsupported`. Anything
  not `ok` is dropped.

### 2. `_enforce_retention` — guards against consolidation collapse

If the consolidation model returns fewer than `retention_floor` (0.55) of the
candidates as claims, its grouping is discarded and the deterministic pre-merge
result is used instead. Aimed at short papers: one 3-page paper collapsed 13
candidates into 4 claims under v5.

### 3. Prompt edits — active in BOTH versions

These are in the SkillPack, not behind a switch, so they apply even with the
switches off. They are additive precision rules:

- copy comparison operators and thresholds exactly (`< 2` is not `>= 2`)
- name what a number measures with the source's own noun (a median is not a
  proportion)
- preserve the direction of a preference or comparison
- do not name a cohort, model system or experiment the quoted span lacks
- a "Precision self-check" section in `SKILL.md`

If strict v5 is wanted, these must be reverted separately — the env switches do
not disable them.

### New settings (`StagedSettings`, overridable by env)

| setting | env var | v6 default |
|---|---|---|
| `selfcheck` | `SUBNET_CLAIMS_STAGED_SELFCHECK` | true |
| `selfcheck_batch` | `SUBNET_CLAIMS_STAGED_SELFCHECK_BATCH` | 25 |
| `retention_floor` | `SUBNET_CLAIMS_STAGED_RETENTION_FLOOR` | 0.55 |

## Why v6 exists

v5 scored 5,497 claims across 50 papers and drew **17 rejections** from the
local judge. Each rejection costs 0.25 of that paper's quality. The causes split
cleanly:

- **9 restatements** — our own claim asserting what another of our claims
  already said, in different words. All nine were single-candidate claims on
  both sides, so the merge guard did not cause them; the consolidator simply
  left them unmerged. They shared only 32-54% of their words, which is why the
  deterministic threshold missed them.
- **8 semantic errors against their own quotes** — a threshold copied inverted
  (`>= 2` where the source says `< 2`), medians relabelled as proportions, a
  reversed preference (authors prioritised contrastive methods, the claim said
  masking), and specifics the span never mentions (an E0771 mouse experiment
  asserted from a general statement).

`_merge_restatements` targets the first group, `_drop_unsupported` the second.

## Measured results

### v5, full and trustworthy

50 papers of mainnet batch `batch_20260907_d7b6080b73fb`; 22 of them were scored
by the validator, giving 1,404 real Silver units to check against.

| metric | value |
|---|---|
| claims | 5,497 (mean 110/paper) |
| coverage vs real Silver units | 0.9506 (1,101 of 1,404 matched) |
| quality (diagnostic x adjudication) | 0.9105 |
| estimated batch score | 0.8640 |
| the real winner of that batch | 0.7762 (UID 249) |
| cost | $15.06 |
| time | 450 s/paper |

### v6, only on the six papers that were failing

These six were chosen *because* they carried the rejects, so this is a biased
sample. It shows the fix works; it cannot show regressions elsewhere.

| | v5 | v6 |
|---|---|---|
| rejects | 8 | **0** |
| weak | 6 | 4 |
| mean quality | 0.660 | **0.998** |
| claims | 578 | 556 |

Per paper, the self-check merged 3-14 restatements and dropped 2-4% of claims.
The 3-page paper went from 4 claims to 10 (retention floor).

## What is NOT known about v6

1. **Whether it damages healthy papers.** `_drop_unsupported` removes claims,
   and some drops looked like real findings (an RP2D dose definition, a
   variance-explained statistic). On a paper that already scored 1.0, a wrong
   drop is a pure coverage loss.
2. **Its coverage against real Silver units.** v6 has never been scored with
   `tools/score_against_real_silver.py`. v5's 0.9506 is the number to beat.
3. **Its cost and timing at 50 papers.** The self-check adds 2 extra LLM calls
   per paper plus one per 25 claims.

## How to finish validating it

```bash
cd claims-improvement
# 1. regression check on papers v5 already scored 1.000 (~$2, ~15 min)
SUBNET_CLAIMS_STAGED_SELFCHECK=true SUBNET_CLAIMS_STAGED_RETENTION_FLOOR=0.55 \
  .venv/bin/python -m tools.run_batch --pdf papers/<healthy_paper>.pdf ... \
  --run-name v6-healthy --workers 4 --judge

# 2. full 50 papers, same batch as v5 (~$16, ~50 min at 8 workers)
RUN_NAME=v6-50paper ./scripts/run_50_paper_test.sh 12 --judge

# 3. score against the same real Silver units and compare with v5
.venv/bin/python -m tools.score_against_real_silver \
  --run-name v6-50paper --run-id run_20260907_063159_471c07 --passes 3
```

Ship v6 only if coverage stays at or above 0.95 **and** rejects fall. If
coverage drops, the likely culprit is `_drop_unsupported`; loosen it to flag
rather than delete before abandoning the idea.

## Enabling v6 in production

```bash
# in .env
SUBNET_CLAIMS_STAGED_SELFCHECK=true
SUBNET_CLAIMS_STAGED_RETENTION_FLOOR=0.55

pm2 restart claims-miner-sn111-improved --update-env   # between validator batches
```

The running process loads code and env at startup, so edits on disk do nothing
until a restart — and conversely, any restart picks up whatever is on disk.
That is why the switches are pinned off rather than the code being reverted.

## Traps worth knowing (they cost time here)

- **`grep -r` on this box is ugrep and silently skips dotfiles.** A secret scan
  with `grep -rl "sk-or-" .` returns clean even when `.env` holds the key. Use
  `find ... -exec grep -l`.
- **pm2 only reads an ecosystem file named `*.config.cjs`.** Given
  `pm2.miner.mainnet.cjs` it runs the file as a plain script and the app comes
  up doing nothing. The working file is `scripts/ecosystem.config.cjs`.
- **`tools/make_bronze.py` runs subprocesses with `cwd` inside the live Claims
  checkout.** A relative `--out` writes into that repo. It now resolves paths.
- **litellm needs the `openrouter/` prefix** on model ids. `qwen/qwen3.5-plus`
  fails instantly with "LLM Provider NOT provided"; `openrouter/qwen/...` works.
- **Silver units are merged composites** pooling 17-140 candidate claims. When
  judging coverage, ask whether a claim would be *pooled into* the unit, not
  whether it restates the whole thing. Getting this wrong scored 3/6 on a paper
  whose true coverage was 6/6.
