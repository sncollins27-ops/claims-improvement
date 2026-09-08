# Onboarding — everything a new session needs

Written 2026-09-08 by the session that built this repo. Read this first, then
[V5-VS-V6.md](V5-VS-V6.md) for the version split and
[IMPROVEMENT-NOTES.md](IMPROVEMENT-NOTES.md) for the full experiment log.

---

## 1. The job

Run a competitive miner on Bittensor subnet 111 (Claims). A validator sends 50
scientific papers; the miner returns one structured artifact per paper
containing claims, each grounded in verbatim quotes from the paper's text. The
validator scores the artifacts and sets weights.

Two directories, and the distinction matters:

| path | what it is | rule |
|---|---|---|
| `/root/claim-james/Claims` | upstream subnet repo, git-tracked, has the reference implementation | **read only.** Never write into it. Tools that run subprocesses with `cwd` there must use absolute output paths |
| `/root/claim-james/claims-improvement-james` | this repo: trimmed miner + improvements + tooling | all work happens here |

Production miner: pm2 app `claims-miner-sn111-improved`, UID 192, wallet
`<wallet>/<hotkey>`, axon on port 8091, systemd-enabled so it survives reboot.
The old app `claims-miner-sn111` (cwd `Claims`) is stopped and kept for
rollback.

---

## 2. How scoring actually works

Read from the validator's own source, not from docs. The authoritative files
are `validator/agent_v1/silver_scoring.py`, `silver_builder.py`,
`orchestrator.py`, `record_projection.py`, and the skills under
`validator/agent_v1/skills/` in the upstream repo.

```
paper score = coverage x diagnostic_quality x adjudication_quality
batch score = mean over eligible papers
```

**coverage** — the fraction of the paper's canonical *Silver units* that one of
your claims was pooled into, weighted by importance: central 0.70, supporting
0.30, minor 0.10, with the minor tier capped at 5% of the total. This is the
dominant term and the one worth optimising.

**diagnostic_quality** — 1.0 minus penalties for findings: blocker 1.00,
critical 0.25, major 0.10, minor 0.03, warning 0.01. Deterministic checks
(schema, cross-references, span ids, quote-in-span) plus an LLM rigor pass.

**adjudication_quality** — 1.0 minus 0.25 for each of your claims the anonymous
judges reject as invalid.

### Where Silver units come from

1. The validator runs its own **reference miner** ("Bronze"). Every Bronze claim
   becomes a *required* unit.
2. Every **miner claim the judges accept** becomes an *accepted_improvement*
   unit.
3. A canonicalizer and an auditor merge all of it into final units and tag each
   central / supporting / minor.

Consequences that drive strategy:

- Units are **merged composites**. A single unit routinely pools 17-140
  candidate claims from across all miners. One unit's statement may read
  "knockdown inhibits proliferation, colony formation, migration and invasion,
  induces arrest and apoptosis" — that is many findings fused.
- Because accepted miner claims *become* units, a large accepted claim set
  raises your coverage and lowers everyone else's. This is why claim volume
  wins, provided every claim survives adjudication.
- Real unit counts per paper ranged **2 to 606** in the batch measured here,
  median about 9. Two papers had 533 and 606 units.
- Mainnet sets `CLAIMS_SILVER_MAX_ELIGIBLE_CLAIMS_PER_MINER=1000` and
  `CLAIMS_SILVER_MAX_ADJUDICATION_CASES_PER_PAPER=1000`, so there is no
  practical cap on claims — but the per-paper case budget is filled
  round-robin by claim index across miners, which is why claims are emitted
  **ordered central → supporting → minor**.

### The evidence gate (easy to fail, fatal when failed)

`_candidate_has_linked_evidence` in `file_agent_workflow.py`: a claim is
eligible only if it has an `evidence_id`, a cited source span, **and** the
evidence id resolves to a stored record. Fail any one and the claim goes into
`mandatory_evidence_exclusions`, which the canonicalizer must honour — it can
never join a unit, so it earns zero coverage.

More evidence beyond that threshold buys nothing. One record per claim is
correct; extra records only add cost and grounding-finding surface.

---

## 3. Why the stock miner loses

The upstream `agent_v1` mounts the ARA `compiler` skill, whose contract asks for
**3-7 claims**. Against records holding 6-600 units, that forfeits most of the
coverage term before quality is even considered.

Measured baseline on a real paper (stock dspy-react + compiler skill): **4
claims**, and 12 of 12 quotes failed the validator's exact-substring check
because the model paraphrased and inserted ellipses. Quote failures cap a paper
at 0.5 and then criticals take it lower.

Also fixed early: the live miner had scored 0.0 on a whole batch because
dspy + a reasoning model raised `temperature=1.0 ... max_tokens >= 16000` for
all 50 papers.

---

## 4. What this repo does instead

`miner/agent_v1/staged/` — a staged runtime replacing the single agent loop:

```
extract   per page, in parallel, exhaustively
ground    relocate each quote to an exact substring; verify every number
          appears in a quote; one repair pass for what fails
premerge  deterministic merge of obvious restatements
consolidate  LLM merges restatements globally (parallel chunks)
guard     split members that do not really restate the group's primary claim
hygiene   drop boilerplate and unit-mismatch claims
dedupe    deterministic near-duplicate merge
selfcheck (v6 only) merge restatements, drop unsupported wording
structure experiments, concepts, trace
assemble  final quote re-verification, importance ordering, 150-claim cap
```

`miner/agent_v1/skills/claims_coverage/` — the SkillPack: `SKILL.md`, three
stage prompts (`extract`, `consolidate`, `structure`), and references on the
JSON contract, grounding rules and claim granularity.

Result: **110 claims/paper mean**, zero grounding findings on 41 of 50 papers.

---

## 5. Tools

| tool | what it does |
|---|---|
| `tools/run_paper.py` | one paper end to end, then evaluate |
| `tools/run_batch.py` | many papers with N workers; prints projected 50-paper wall time |
| `tools/evaluate.py` | validator's structural + grounding passes, a stricter local audit, optional LLM judge |
| `tools/fetch_batch_papers.py` | download a mainnet batch's PDFs + manifest |
| `tools/fetch_silver_units.py` | **the real Silver units** for a run (see below) |
| `tools/score_against_real_silver.py` | judge our claims against real units, score with validator code |
| `tools/make_bronze.py` | reconstruct the reference extraction (superseded, kept for reference) |
| `tools/estimate_coverage.py` | coverage against reconstructed Bronze (superseded) |
| `tools/compare_silver.py` | our run vs the network's per-miner scores |
| `server/app.py` + `dashboard/` | Vue console: runs, claim auditing with span highlighting, benchmark, agenda |

### The important discovery: Silver units are readable

The API never publishes unit text and `/admin/runs/{id}/silver-records` needs an
operator token. But the public dashboard server-renders them:

```
https://dashboard.claims111.ai/runs?run_id=<run_id>&paper_id=<paper_id>
```

The units sit in the React Server Component stream; `fetch_silver_units.py`
lifts them out. Note the parameter names — `run_id`/`paper_id` work,
`run`/`paper` and `runId`/`paperId` silently return the newest run instead.

That page also exposes the validator's own model choices, e.g. Bronze via
`openai/gpt-5.6-luna-pro` with `hermes-cli`, diagnostics via
`deepseek/deepseek-v4-flash`, and each miner's harness and model.

---

## 6. Measured results

Batch `batch_20260907_d7b6080b73fb` (mainnet run `run_20260907_063159_471c07`),
50 papers, 22 of which the validator scored, 1,404 real Silver units.

| version | claims/paper | rejects | quality | coverage | batch score |
|---|---|---|---|---|---|
| stock baseline | 4 | — | 0.0 (12 quote failures) | — | — |
| staged v2 | 37 | 0 | 0.73 | — | — |
| staged v3 | 130 | 0 | 1.00 | — | — |
| staged v4 | 122 | 0 | 0.50 (span-attribution bug) | — | — |
| **staged v5** | **110** | 17 / 5,497 | **0.9105** | **0.9506** | **0.8640** |
| staged v6 | (6 papers only) | 0 | 0.998 | not measured | not measured |

The real winner of that batch scored **0.7762**. v5's 0.8640 would have placed
first, though it is an estimate: it excludes units our own accepted claims would
have created, and matching is judged by an LLM rather than the validator's
comparator.

Cost and time for the 50-paper run: **$15.06** ($12.41 extraction + $2.65
judging), mean 450 s/paper, 79 min wall including judging.

---

## 7. Timing and worker sizing

The validator's deadline is one hour for the whole batch. Judging is local only;
the miner never judges. From the 50 real per-paper times (375 min of work,
slowest single paper 22 min):

| workers | in-flight calls | makespan | +30% slow | verdict |
|---|---|---|---|---|
| 4 | 16 | 95 min | 124 min | fails |
| 8 (current) | 32 | 49 min | 63 min | fails under stress |
| 10 | 40 | 40 min | 52 min | ok |
| **12** | 48 | **32 min** | 42 min | recommended |
| 16 | 64 | 25 min | 33 min | overkill |

In-flight calls = `CLAIMS_MINER_BATCH_MAX_WORKERS` x
`SUBNET_CLAIMS_STAGED_EXTRACT_CONCURRENCY` (currently 8 x 4). The machine is not
the constraint (16 cores, 34 GB free, miner uses 0.2 GB); the provider is. No
worker count beats the slowest single paper.

---

## 8. How to measure honestly

This mattered more than any single code change. Every one of these caught a
real error in my own work:

1. **Verify before you change.** When coverage looked bad on a paper, I grepped
   our artifact for the supposedly missing content and found 12, 3 and 12
   matching claims. The measurement was broken, not the artifact. Had I
   "fixed" the miner instead, I would have made it worse.
2. **Vote, don't trust one pass.** Judging the same comparison twice gave batch
   estimates from 0.75 to 0.85, and 16 of 81 units split across three passes.
   Always `--passes 3`.
3. **Run a negative control.** After reframing the coverage judge, I fed three
   unrelated papers' units against one paper's claims. All 12 correctly
   returned no match, proving the judge had not simply become permissive.
4. **Measure a filter's false positives before shipping it.** Four
   deterministic rules were built for claim hygiene; two were cut after
   measuring against all 5,497 claims — an entity-name check that caught 0
   rejects while dropping 30 good claims, and a signature dedupe that merged
   108 good claims to catch 1 bad one.
5. **Never score an unjudged item as a failure.** A judge pass that silently
   dropped units was counting them as misses and biasing coverage down.
6. **State assumptions as ranges, not a single number.** Before real units were
   available, coverage was reported as a band with each assumption named.

---

## 9. Traps that cost time here

- **`grep -r` on this box is ugrep and silently skips dotfiles.** A secret scan
  with `grep -rl "sk-or-" .` returns clean while `.env` holds the key. Use
  `find ... -exec grep -l`. This nearly let a key reach a public repo.
- **pm2 only treats a file as an ecosystem config if it is named
  `*.config.cjs`.** Given `pm2.miner.mainnet.cjs` it runs the file as a script
  and the app sits there doing nothing. Use `scripts/ecosystem.config.cjs`.
- **A restart loads whatever is on disk.** Editing the pipeline changes
  production the moment pm2 restarts for any reason. Pin unvalidated features
  behind env switches rather than leaving them live on disk.
- **Scripts with a hardcoded `--run-name` plus `--force` destroy baselines.**
  `run_50_paper_test.sh` now takes `RUN_NAME`.
- **Subprocesses with `cwd` in the upstream repo plus a relative output path
  write into that repo.** Resolve paths against `ROOT`.
- **litellm needs the `openrouter/` prefix.** `qwen/qwen3.5-plus` fails with
  "LLM Provider NOT provided"; `openrouter/qwen/...` works. `deepseek/...`
  happens to work because litellm knows that provider name.
- **Ask the right question of a judge.** Silver units are merged composites;
  asking "does one claim restate this whole unit" scored 3/6 where the truth
  was 6/6. Ask "would this claim be pooled into the unit".

---

## 10. Current state (2026-09-08)

- Miner online, UID 192, v5 behaviour (v6 switches off in `.env`), 8 workers.
- UID 192 has 3 evaluations, all 0.0, from the batch that failed on the dspy
  temperature error before any of this work. It has not been selected since the
  fix, so **no real score yet reflects the new pipeline.** The next selection is
  the real test.
- OpenRouter: ~$98 credit left; this whole investigation cost about $35.
- Repo pushed to `git@github.com:sncollins27-ops/claims-improvement.git`
  (SSH key `~/.ssh/id_ed25519_sncollins`; the stored HTTPS credential is a
  different account and gets 403).

### Next steps, in priority order

1. **Finish validating v6** — regression check on healthy papers, then the full
   50, then `score_against_real_silver`. Ship only if coverage holds at 0.95+
   and rejects fall. Commands are in V5-VS-V6.md.
2. **Raise workers to 12** for deadline margin, between validator batches.
3. **Watch the next real evaluation of UID 192** and compare the true Silver
   score to the local estimate of 0.8640. That calibrates everything here.
4. **Short commentary papers** still under-extract: a 3-page highlight produced
   4 claims against 8 real units (v6's retention floor took it to 10, unproven
   at scale).
