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
  - skill helper scripts are available under `/workspace/skills/<name>/scripts`
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
