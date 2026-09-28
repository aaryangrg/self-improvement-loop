# Self Improvement Loop

Initial bridge for running Harvey LAB tasks against an Introspection legal-agent recipe.

## Setup

```bash
uv sync
git submodule update --init --recursive
nvm use
npm ci
```

## External Prerequisites

Python dependencies are managed by `uv` and pinned to Python 3.13 via `.python-version`.
Node is pinned to 24 via `.nvmrc` because the Introspection CLI requires Node 24+.
The root npm lockfile also installs the exact Codex CLI version used by the
self-improvement researcher.

The Introspection CLI is an external Node/npm tool, not a Python package:

```bash
nvm install 24
nvm use
npm install --global @introspection-ai/cli
introspection setup
introspection login
```

The Harvey LAB dataset is expected at `harvey-labs/` as a pinned Git submodule. After cloning this repository, initialize it with:

```bash
git submodule update --init --recursive
```

Quick environment checks:

```bash
uv run python --version
node --version
command -v introspection
introspection doctor -o report
test -d harvey-labs/tasks
```

## Smoke Check

```bash
uv run python scripts/smoke_check.py --task-id compare-matter-plan-against-engagement-letter
```

## Baseline Agent Recipe

The initial Introspection agent recipe lives at `recipes/legal-agent/`. It is a
first Harvey LAB-compatible baseline, not yet an optimized agent.

Current recipe surface:

- Agent tools: `read_file`, `ls`, `grep`, `find`, `bash`, `write`, and `edit`.
- Custom `read_file` extension for text, `.docx`, `.xlsx`, `.pptx`, and `.pdf`.
- Harvey-style `docx`, `xlsx`, and `pptx` skills adapted to Introspection
  workspace paths.
- Stable sandbox conventions:
  - input task files are read from `/workspace/files`
  - final deliverables are written under `/workspace/outputs`
  - skill helper scripts are available under `/workspace/.pi/skills/<name>/scripts`
- Recipe-local Python runtime declared through
  `recipes/legal-agent/python/pyproject.toml`, `uv.lock`, and
  `package.json#pi.runtime.python`.
- Recipe-local Node runtime dependencies declared in
  `recipes/legal-agent/package.json` and `pnpm-lock.yaml`.

Supported first-cut document capabilities:

- DOCX generation/editing with `python-docx`, `docxtpl`, Markdown-to-DOCX, and
  OOXML helper scripts.
- XLSX generation/editing with `openpyxl`, including formula authoring and
  marking workbooks for recalculation on open.
- PPTX generation/editing with `python-pptx`, `md2ppt`, and `pptxgenjs`.
- PDF/text extraction through the custom `read_file` path where supported by the
  installed Python libraries.

Intentional first-cut gaps:

- No Marp CLI.
- No LibreOffice-backed Office conversion, rendering, or XLSX recalculation.
- No Pandoc-backed conversions.
- No OCR stack for scanned PDFs/images.
- No claim of high-fidelity arbitrary HTML/CSS-to-PPTX conversion.

Validated locally so far:

- `introspection check -o report`
- Markdown-to-PPTX smoke test through `md2ppt`
- JSON-to-PPTX smoke test through `pptxgenjs` under Node 24
- Python compile checks for the new PPTX helper

Still to prove with a real managed run:

- Uploading Harvey task files into Introspection.
- Invoking the cloud sandbox recipe.
- Collecting generated artifacts and conversation trace.
- Running Harvey evaluation against the downloaded outputs.

See `docs/recipe-runtime-dependencies.md` for the detailed comparison between
Harvey's sandbox and this baseline recipe.

See `docs/self-improvement-run-model.md` for the self-improvement recipe
copying, runtime, and epoch-tracking model.

See `docs/self-improvement-loop-implementation.md` for the planned
script-orchestrated improvement loop and researcher-agent boundaries.

## Run One Task

Cloud development runs need a concrete runtime version id:

```bash
introspection runtimes create --manifest .introspection/legal-agent.yaml -o json
```

Use the returned `id`:

```bash
uv run python -m runner run-one \
  --task-id compare-matter-plan-against-engagement-letter \
  --trial 1 \
  --runtime-id <runtime-id>
```

Outputs are written under `results/tasks/<task-id-safe>/trial-001/` by default.
The trial directory contains the conversation trace, downloaded output files,
and Harvey evaluation files when evaluation is enabled.

## Run A Split

Reusable train/test task selections live under `experiment_configs/`.

```bash
uv run python -m runner run-experiment \
  --config experiment_configs/smoke_split.yaml \
  --split train \
  --runtime-id <runtime-id>
```

Experiment outputs are written under:

```text
results/experiment_runs/<experiment-run-id>/
  run.json
  split_config.yaml
  <split>/
    aggregate.json
    <task-id-safe>/
      task_summary.json
      trial-001/
        conversation.json
        outputs/
        evaluation/
        evaluation.json
        incidents.json
        trial_summary.json
```

Split configs use separate task and evaluation concurrency:

```yaml
defaults:
  trials_per_task: 1
  task_concurrency: 3
  eval_concurrency: 5
```

`task_concurrency` controls concurrent Introspection task execution and artifact
download. `eval_concurrency` controls how many completed trials are sent through
Harvey evaluation at once. The CLI `--eval-parallel` flag is still passed through
to Harvey's evaluator for within-evaluation judge parallelism. Evaluation starts
as soon as each trial finishes; it does not wait for every agent task in the split.

Generic `run-experiment` accepts `--eval-judges gpt-6-sol`. Self-improvement
runs instead take judges from the `evaluation` section of their config, currently
GPT-6 Sol in both supplied configs. The runner pins those settings under
`metadata/evaluation.json` and rejects changes within the same run. A small
compatibility wrapper supplies this model's Responses API parameters.

Trial summaries use `scored` as the gate for aggregate calculations. Failed or
unscored trials still keep any conversation, task metadata, partial outputs, and
error files the runner could recover, but they do not enter task or split pass
rate calculations. Aggregates report both `number_of_scored_trials` and
`number_of_failed_trials`, plus task coverage fields:
`number_of_evaluated_tasks` and `number_of_unevaluated_tasks`.
If fewer than half of the tasks have a score after the first pass, each
unevaluated task gets one additional execution attempt. Failed attempts and
their traces remain in separate trial directories. The split continues even
if task coverage is still partial; `progress.json` marks it `incomplete`, and
the aggregate scores reflect only scored trials. A failed root agent span in
the Introspection trace marks that attempt as failed, even when the platform
reports its run as completed.

## Start A Self-Improvement Run

Reusable self-improvement settings live under `self-improvement-configs/`.
The supplied configs pin the Harvey judge to `gpt-6-sol` at medium reasoning,
the Codex researcher to `gpt-6-sol` at xhigh, and the fresh Codex verifier to
`gpt-6-sol` at medium. The legal task agent remains on Claude Sonnet 4.6.
Reasoning effort is required in each of the `evaluation`, `researcher`, and
`verifier` config sections and is recorded with run metadata.

Create the local run skeleton first:

```bash
uv run python -m runner self-improve bootstrap \
  --config self-improvement-configs/smoke.yaml \
  --run-id run-001
```

This creates:

```text
recipes/self-improvement/<run-id>/legal-agent/
.introspection/self-improvement-<run-id>.yaml
results/self-improvement/<run-id>/
results/self-improvement/<run-id>/metadata/runtime.json
```

Commit and push the bootstrap recipe/manifest to `main`, then create the
Introspection runtime and record it:

```bash
uv run python -m runner self-improve commit-bootstrap --run-id run-001
```

```bash
uv run python -m runner self-improve create-runtime --run-id run-001
```

If the runtime was created manually, record it instead:

```bash
uv run python -m runner self-improve record-runtime \
  --run-id run-001 \
  --runtime-id <runtime-id>
```

For the epoch loop, create a branch and draft PR. The branch command checks that
the worktree is clean, switches to `main`, creates the PR branch, and records
that branch in `metadata/runtime.json`. The PR command switches to the recorded
branch, pushes it, opens the PR, and records `pr/N`.

```bash
uv run python -m runner self-improve create-pr-branch --run-id run-001

uv run python -m runner self-improve open-pr --run-id run-001
```

If a PR already exists, record it manually instead:

```bash
uv run python -m runner self-improve record-pr \
  --run-id run-001 \
  --pr-number <number> \
  --branch self-improvement/run-001

uv run python -m runner self-improve pin-staging-pr --run-id run-001
```

Candidate epoch commits need to be pushed to the PR branch so Introspection can
build them. Best-checkpoint or final promotion can still be delayed until the
overall run is done.

After each verified recipe edit, commit and push the candidate:

```bash
uv run python -m runner self-improve commit-candidate \
  --run-id run-001 \
  --epoch 1
```

Run epoch 0 after the runtime is available. Baseline is just epoch `0` using
the same train/test commands as every later epoch:

```bash
uv run python -m runner self-improve run-train \
  --config self-improvement-configs/smoke.yaml \
  --run-id run-001 \
  --epoch 0

uv run python -m runner self-improve run-test \
  --config self-improvement-configs/smoke.yaml \
  --run-id run-001 \
  --epoch 0

uv run python -m runner self-improve refresh-metrics --run-id run-001
uv run python -m runner self-improve update-best-candidate --run-id run-001
```

Epoch 0 should run both splits because it establishes the baseline. Later train
epochs can use the same `run-train --epoch N` command after each verified recipe
change.

If `--run-id` is omitted, the runner generates an id like
`run-20260927-090512-345678`. You can still provide `--run-id` to choose a
specific id.

The run uses convention-based paths:

```text
recipes/self-improvement/<run-id>/legal-agent/
.introspection/self-improvement-<run-id>.yaml
results/self-improvement/<run-id>/
```

For now, self-improvement iteration is expected to use Git-backed Introspection
candidate versions rather than `introspection dev`. We observed that
`introspection dev` exits without attaching in this project and development
tasks fail even when the same runtime works in staging. See
`docs/self-improvement-run-model.md` for the recorded finding and the PR-backed
runtime model.

Once `metadata/runtime.json` contains a runtime id, later train/test commands do
not need `--runtime-id`; they read it from metadata.

Self-improvement epoch runs write graph-ready metrics to:

```text
results/self-improvement/run-001/metadata/metrics.jsonl
results/self-improvement/run-001/metadata/metrics.csv
```

Regenerate metrics after train/test epochs with:

```bash
uv run python -m runner self-improve refresh-metrics --run-id run-001
```

Best-candidate state is updated explicitly:

```bash
uv run python -m runner self-improve update-best-candidate --run-id run-001
```

That writes `results/self-improvement/run-001/metadata/best_candidate.json`.

## Codex Researcher

Start an end-to-end self-improvement run from a clean `main` worktree:

```bash
uv run python -m runner self-improve start --config self-improvement-configs/smoke.yaml
```

`--run-id` is optional. The command bootstraps and pushes the run's recipe,
creates its runtime, evaluates epoch 0 on train and test, and then runs the
configured research epochs. Each candidate passes a fresh read-only verifier;
rejections return to the same researcher session up to the configured revision
limit. Approved edits are promoted, committed on the run's PR branch, matched
to a ready Introspection version by Git SHA, and evaluated. Test runs occur at
the configured cadence and on the final epoch. Results live under
`results/self-improvement/<run-id>/`. A failure stops the loop with its files
available for inspection; automatic resume is not implemented.

`self-improve start` also launches a read-only Streamlit dashboard bound to
`127.0.0.1` and prints its URL before the baseline begins. It reads local
`results/self-improvement/` files and refreshes every three seconds; it does not
upload results or control the run. To inspect previous runs without starting an
experiment:

```bash
uv run python -m runner self-improve ui --run-id run-001
```

The dashboard shows task and rubric pass rates, live trial states, researcher
and verifier decisions, Introspection-reported generation cost, and Codex token
usage. New GPT-6 Sol evaluations record each judge response's token usage in
`evaluation/judge_usage.json`; the dashboard estimates judge cost using published
standard API rates. Older runs have no judge-usage history, and ChatGPT-signed-in
Codex usage is shown as tokens rather than a dollar charge. Stop the runner with
Ctrl-C; the local dashboard remains available for inspection.

The researcher can also be invoked separately after a train epoch completes. It reads
the original train epoch results, including Harvey `scores.json` feedback, and
the original baseline recipe. It writes research notes directly under the run's
`workspace/`. Only the candidate recipe is copied into
`results/self-improvement/<run-id>/metadata/researcher/epoch-XXX/sandbox/recipe/`.
Held-out test results and the Harvey source task criteria are not accessible.
A JSON Schema validates the final report, while `events.jsonl` preserves
Codex's event stream. Manual `verify` and `promote` commands are available for
inspecting one candidate outside the full loop.

Prepare the workspace, then invoke the researcher:

```bash
uv run python -m runner self-improve prepare-researcher --run-id run-001 --epoch 1
uv run python -m runner self-improve research --run-id run-001 --epoch 1
uv run python -m runner self-improve verify --run-id run-001 --epoch 1
uv run python -m runner self-improve promote --run-id run-001 --epoch 1
```

The researcher and verifier models are required in the self-improvement config
and pinned in the run metadata at bootstrap. Neither role falls back to your
personal Codex model setting.

The researcher and verifier use `~/.self-improvement-loop/codex` as a dedicated
`CODEX_HOME`, separate from your personal Codex profile and from the run's
isolated `HOME`. `self-improve start` checks this profile before bootstrap and
opens Codex sign-in if needed when run in a terminal. For non-interactive runs,
authenticate this profile first with your ChatGPT account:

```bash
CODEX_HOME="$HOME/.self-improvement-loop/codex" ./node_modules/.bin/codex login
```

The subprocess does not require or pass `OPENAI_API_KEY`, and does not copy
credentials into `results/`. Keep this dedicated profile free of personal
`AGENTS.md` files and skills. The researcher-only `AGENTS.md` and skills are
copied into its prepared workspace; they are not installed at the repository
root. The verifier has its own instructions in `verifier/AGENTS.md`.

The pinned CLI's actual `codex exec` path was smoke-tested with this profile:
allowed reads and writes succeeded, while a read outside the permitted paths and a
write to a read-only folder were denied. The earlier `codex sandbox` debug
command's `TIOCSTI` failure does not affect that result. `events.jsonl` is an
audit/debug record of researcher tool calls and failures; scoring does not
require it.
Each epoch can be invoked only once without explicitly inspecting and resetting
its prepared workspace. See `docs/self-improvement-loop-implementation.md`.

## Development Verification

```bash
uv run python -m compileall runner scripts
uv run python -m runner --help
uv run pre-commit run --all-files
```

After `harvey-labs/` is initialized:

```bash
uv run python scripts/smoke_check.py --task-id compare-matter-plan-against-engagement-letter
```
