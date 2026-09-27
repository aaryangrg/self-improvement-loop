# Self Improvement Loop

Initial bridge for running Harvey LAB tasks against an Introspection legal-agent recipe.

## Setup

```bash
uv sync
git submodule update --init --recursive
nvm use
```

## External Prerequisites

Python dependencies are managed by `uv` and pinned to Python 3.13 via `.python-version`.
Node is pinned to 24 via `.nvmrc` because the Introspection CLI requires Node 24+.

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
to Harvey's evaluator for within-evaluation judge parallelism.

Trial summaries use `scored` as the gate for aggregate calculations. Failed or
unscored trials still keep any conversation, task metadata, partial outputs, and
error files the runner could recover, but they do not enter task or split pass
rate calculations. Aggregates report both `number_of_scored_trials` and
`number_of_failed_trials`, plus task coverage fields:
`number_of_evaluated_tasks` and `number_of_unevaluated_tasks`.

## Start A Self-Improvement Run

Reusable self-improvement settings live under `self-improvement-configs/`.

```bash
uv run python -m runner self-improve start \
  --config self-improvement-configs/smoke.yaml \
  --runtime-id <runtime-id>
```

This creates the working recipe, Introspection manifest, baseline snapshot, and
research workspace, then runs epoch 0 train and epoch 0 test. Epoch 0 always
runs both splits because it establishes the baseline.

If `--run-id` is omitted, the runner generates an id like
`run-20260927-090512-345678`. You can still provide `--run-id` to choose a
specific id.

The run uses convention-based paths:

```text
recipes/self-improvement/<run-id>/legal-agent/
.introspection/self-improvement-<run-id>.yaml
results/self-improvement/<run-id>/
```

You can also create only the local run skeleton without executing tasks:

```bash
uv run python -m runner self-improve bootstrap \
  --config self-improvement-configs/smoke.yaml \
  --run-id run-001
```

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
