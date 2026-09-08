# claims-improvement-james

Improvement lab for the SN111 Claims miner. A trimmed copy of the upstream
`Claims` repo (only what the miner needs) plus:

- a **coverage-first SkillPack** (`miner/agent_v1/skills/claims_coverage/`),
- a **staged extraction runtime** (`miner/agent_v1/staged/`) that extracts
  every grounded claim page by page, verifies quotes and numbers
  deterministically, consolidates globally and assembles the artifact,
- a **local evaluator** (`tools/evaluate.py`) that replays the validator's
  deterministic checks, adds stricter grounding audits, and optionally runs an
  LLM self-judge that predicts Silver rejections,
- a **Vue 3 dashboard** (`dashboard/` + `server/app.py`) to monitor runs, audit
  every claim against its source span, compare experiments, watch the live
  subnet, read the skill pack and keep the agenda / next-session handoff.

The live miner in `../Claims` is untouched. This repo runs the same wallet and
neuron code, so switching production over is a one-line PM2 change once local
quality is where we want it.

## Why the standard miner loses

Mainnet Silver scoring is `score = coverage x diagnostic_quality x adjudication_quality`
(see `validator/agent_v1/silver_scoring.py`):

| term | what it is | how to max it |
|---|---|---|
| coverage | importance-weighted share of the paper's canonical Silver units (reference extraction + every accepted miner claim) that one of your claims matches | extract every distinct proposition: 40-100 claims per full paper |
| diagnostic_quality | 1 - penalties for structural/grounding/rigor findings (critical 0.25, major 0.10, minor 0.03) | verbatim quotes, numbers inside quotes, all ids resolve, precise scope |
| adjudication_quality | 1 - 0.25 per claim the anonymous judges reject | no unsupported, trivial, over-general or duplicate claims |

The stock ARA compiler skill asks for **3-7 claims**, so even a perfect
artifact forfeits most of the coverage term. The winner of the latest
mainnet run (UID 125) submitted 77 claims / 77 evidence records on paper 1
and scored 1.0. `CLAIMS_SILVER_MAX_ELIGIBLE_CLAIMS_PER_MINER=1000` on mainnet,
so there is no cap to worry about.

Our own miner (UID 192) scored 0.0 on its last batch because every paper
failed with `OpenAI's reasoning models require temperature=1.0 ... max_tokens >= 16000`
(dspy + gpt-5-mini). The staged runtime talks to the OpenAI-compatible API
directly and never hits that path.

## Layout

```
miner/agent_v1/staged/          staged runtime (llm.py, grounding.py, pipeline.py, runtime.py)
miner/agent_v1/skills/claims_coverage/   SKILL.md, references/, prompts/ (extract, consolidate, structure)
miner/agent_v1/...              upstream agent_v1 (runner, ingest, dspy/langchain/cli runtimes)
neurons/                        miner neuron, tasks, protocol, harness profiles (staged added)
validator/agent_v1/             deterministic validator modules used by tools/evaluate.py
tools/run_paper.py              run one paper -> runs/<exp>/<paper_id>/
tools/evaluate.py               evaluate a run dir -> evaluation.json
tools/fetch_batch_papers.py     download a mainnet batch's PDFs + manifest
tools/run_batch.py              run a manifest with N workers, judge, project 50-paper timing
docs/IMPROVEMENT-NOTES.md       scoring analysis, winner shape, what changed, results
server/app.py                   FastAPI sidecar: runs, artifacts, network proxy, agenda, skill, handoff
dashboard/                      Vue 3 + Vite + Tailwind 4 + Pinia + ECharts
runs/                           experiments (runs/<exp>/<paper>) and neuron outputs (runs/neuron/<net>/<task>)
papers/                         local PDFs
scripts/                        run_local_miner.sh, run_mainnet_miner.sh, PM2 wrappers
```

## Moving this repo to another machine

```bash
git clone https://github.com/sncollins27-ops/claims-improvement.git
cd claims-improvement

python3.12 -m venv .venv
uv pip install --python .venv/bin/python -r requirements.txt fastapi uvicorn pytest
uv pip install --python .venv/bin/python --no-deps -e .
uv pip install --python .venv/bin/python "git+https://github.com/DeSciClaims/claims-reference-miner.git"   # only for tools/make_bronze.py

cp .env.example .env      # then fill OPENROUTER_API_KEY, BT_WALLET_NAME, BT_WALLET_HOTKEY, BT_AXON_EXTERNAL_IP
chmod 600 .env

.venv/bin/python -m pytest tests -q          # 27 tests, no network needed
(cd dashboard && npm install && npm run build:fast)
```

`runs/` and `papers/` are not in git. Re-create them with:

```bash
.venv/bin/python -m tools.fetch_batch_papers --batch-id batch_20260907_d7b6080b73fb --limit 50
.venv/bin/python -m tools.fetch_silver_units --run-id run_20260907_063159_471c07
```

The measured results from the original machine are kept in `results/`
(`real_silver_score.json`, `coverage_estimate.json`, `comparison.json`, and the
real Silver units under `results/silver_real/`).

To run the miner on the new machine you also need the Bittensor wallet at
`~/.bittensor/wallets/<wallet>/`, which is deliberately not in this repo.

## Setup

```bash
python3.12 -m venv .venv
uv pip install --python .venv/bin/python -r requirements.txt fastapi uvicorn pytest
uv pip install --python .venv/bin/python --no-deps -e .
cp examples/miner.env.example .env   # then set OPENROUTER_API_KEY, wallet names, external IP
```

## Run one paper and evaluate

```bash
.venv/bin/python -m tools.run_paper \
  --pdf papers/openalex_w4415340077.pdf --paper-id openalex_w4415340077 \
  --title "Single-cell atlas of the esophageal squamous cell carcinoma immune ecosystem ..." \
  --run-name staged-v2 --judge

# evaluate again later (no extraction):
.venv/bin/python -m tools.evaluate runs/staged-v2/openalex_w4415340077 --judge
```

`--runtime dspy-react --skill-dir miner/agent_v1/skills/compiler` reproduces the
old baseline for comparison.

## Replay a mainnet batch locally

```bash
# fetch the first 6 papers of the latest run (PDFs -> papers/, manifest -> papers/<batch>.json)
.venv/bin/python -m tools.fetch_batch_papers --run-id run_20260907_121647_21873c --limit 6
# run them with 4 workers like the neuron would, judge each, and print the projected 50-paper wall time
.venv/bin/python -m tools.run_batch --manifest papers/batch_20260907_71b95ea13aa3.json --run-name staged-v3-batch --workers 4 --judge
```

## Dashboard

```bash
./run.sh            # builds dashboard/dist if needed, serves http://localhost:8795
./run.sh --build    # force rebuild
cd dashboard && npm run dev   # hot reload on :5174 (proxies /api to :8795)
```

Views: Overview, Runs (every experiment and neuron task), Paper (claims with
source-span highlighting, findings, pipeline funnel, structure, raw JSON,
log), Compare (two runs on one paper: overlap, quality, cost), Network (live
runs, leaderboard, my miner), Network run (batch scores, per-paper Silver
heatmap, paper PDFs), Skill (mounted pack), Agenda (next steps + handoff prompt
for the next Claude session).

## Versions

The committed code is v6. v5 is the same code with two features switched off in
`.env`, and it is what the miner currently runs. See
[docs/V5-VS-V6.md](docs/V5-VS-V6.md) for the exact differences, the measured
results for each, and what still needs validating.

## The improvement loop

1. Run a paper (`tools/run_paper.py ... --judge`).
2. Open it in the dashboard: fix every `quote_not_in_source` /
   `number_not_grounded` finding and every judge `reject`.
3. Edit `SKILL.md`, `prompts/*.md`, or `pipeline.py`; re-run under a new
   `--run-name`; check Compare.
4. When claims >= 40 and quality >= 0.95 hold across several papers, deploy.

## Local 50-paper test

A full-size dry run on a real mainnet batch (`batch_20260907_d7b6080b73fb`,
50 papers, already downloaded to `papers/`):

```bash
./scripts/run_50_paper_test.sh              # 8 workers, ~40 min, ~$11
./scripts/run_50_paper_test.sh 8 --judge    # + LLM self-judge, ~50 min, ~$14
./scripts/run_50_paper_test.sh 4            # gentler while the live miner runs
```

Results appear in `runs/v5-50paper/` and in the dashboard under Runs. The
final line prints average extraction seconds per paper and the projected
50-paper wall time at 8 workers against the validator's 60-minute deadline.

## Deploy (replaces the live miner)

Done on 2026-09-07 18:08 UTC. The old app is stopped but kept for rollback:

```bash
# what is running now
pm2 list                                   # claims-miner-sn111-improved online, claims-miner-sn111 stopped
pm2 logs claims-miner-sn111-improved
tail -f runs/neuron/mainnet/pm2-out.log

# how it was deployed
pm2 stop claims-miner-sn111                # old miner in ../Claims
pm2 start scripts/ecosystem.config.cjs     # this repo, same wallet <wallet>/<hotkey>, axon 8091
pm2 save

# rollback
pm2 stop claims-miner-sn111-improved && pm2 start claims-miner-sn111 && pm2 save
```

`.env` keys that matter: `SUBNET_CLAIMS_AGENT_HARNESS=staged`,
`SUBNET_CLAIMS_AGENT_MODEL`, `SUBNET_CLAIMS_STAGED_*` (per-stage models,
concurrency, reasoning effort), `CLAIMS_MINER_BATCH_MAX_WORKERS`,
`CLAIMS_MINER_OUTPUT_DIR=runs/neuron/mainnet` (so the dashboard sees live
tasks).

## Measured timing (2026-09-07, gpt-5-mini all stages)

Five papers from mainnet batch `batch_20260907_71b95ea13aa3`, 8-41 pages:
69-313 claims each, zero grounding findings, zero judge rejections, $0.11-$0.40
per paper, extraction 283-507 s (avg 366 s). At `CLAIMS_MINER_BATCH_MAX_WORKERS=8`
that projects to roughly 38 min for a 50-paper batch, inside the validator's
one-hour deadline. Total in-flight provider calls are
`SUBNET_CLAIMS_STAGED_EXTRACT_CONCURRENCY x CLAIMS_MINER_BATCH_MAX_WORKERS`
(4 x 8 = 32 by default).

## Tests

```bash
.venv/bin/python -m pytest tests -q
```
