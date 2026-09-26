# Recipe Runtime Dependencies

This note tracks what Harvey LAB gives its benchmark agent and what we plan to
offer our first Introspection recipe.

## Why Pyproject, Uv Lock, And Pi Runtime Are All Needed

For recipe-local Python dependencies, the files have separate jobs:

- `python/pyproject.toml` declares the direct Python dependencies we want.
- `python/uv.lock` pins the exact resolved dependency graph, including
  transitive packages.
- `package.json#pi.runtime.python` tells Introspection to prepare that locked
  Python project before the agent starts.

Without `pyproject.toml`, there is no dependency declaration. Without
`uv.lock`, the managed runtime cannot do a frozen reproducible install. Without
`pi.runtime.python`, the Python project is just an unused directory inside the
recipe; Introspection has no reason to install or activate it for the agent.

The Introspection managed runtime installs the declared Python project into a
recipe-local environment and activates it for Pi and recipe-owned processes.
It does not modify system Python or resolve a newer unlocked dependency graph
at task startup.

## Exact Comparison

### Python Packages

| Package | Harvey LAB sandbox | Our first cut | Notes |
| --- | --- | --- | --- |
| `defusedxml` | Yes | Yes | Safe XML parsing. |
| `diff-match-patch` | Yes | Yes | Useful for redline/diff-style document work. |
| `docxtpl` | Yes | Yes | DOCX template filling. |
| `lxml` | Yes | Yes | Low-level Office XML inspection/editing. |
| `markdown-it-py` | No | Yes | Markdown parsing for Python-only DOCX generation. |
| `markitdown` | Yes | No | Removed from managed runtime because it pulls `magika`/`onnxruntime`, which failed cloud dependency install. |
| `md2ppt` | No | Yes | Markdown-to-PPTX generation using python-pptx. |
| `openpyxl` | Yes | Yes | XLSX read/write. |
| `pandas` | Yes | Yes | Tables/data manipulation. |
| `pdf2image` | Yes | Yes | Installed, but may need Poppler for actual PDF rendering. |
| `pdfplumber` | Yes | Yes | Text-based PDF extraction. |
| `pillow` | Yes | Yes | Image support. |
| `pypdf` | Yes | Yes | PDF structure/text basics. |
| `python-docx` | Yes | Yes | Direct DOCX generation/editing. |
| `python-pptx` | Yes | Yes | Basic PPTX inspection/generation. |
| `anthropic` | Harness package only | No | Not needed inside our task agent when using managed LLM mode. |
| `openai` | Harness package only | No | Not needed inside our task agent when using managed LLM mode. |
| `google-genai` | Harness package only | No | Not needed inside our task agent when using managed LLM mode. |
| `matplotlib` | Harness package only | No | Harvey uses for evaluation dashboards, not task execution. |
| `seaborn` | Harness package only | No | Harvey uses for evaluation dashboards, not task execution. |

### System Tools

| Tool | Harvey LAB sandbox | Our first cut | Notes |
| --- | --- | --- | --- |
| `bash` | Yes | Base/runtime tool | Also exposed as an agent tool. |
| `ca-certificates` | Yes | Not declared | Base image concern; not recipe-level for us. |
| `coreutils` | Yes | Not declared | Likely base image concern. |
| `curl` | Yes | Not declared | We should not rely on network access for task work. |
| `file` | Yes | Not declared | Useful but not first-cut critical. |
| `findutils` | Yes | Not declared | Pi has `find`; shell may also have basic find. |
| `gawk` | Yes | Not declared | Not first-cut critical. |
| `gcc` | Yes | Not declared | Avoid native compilation at task time. |
| `g++` | Yes | Not declared | Avoid native compilation at task time. |
| `git` | Yes | Not declared | Not needed for Harvey document tasks. |
| `grep` | Yes | Base/runtime tool | Also exposed as an agent tool. |
| `procps` | Yes | Not declared | Not first-cut critical. |
| `jq` | Yes | Not declared | Useful, but Python can cover JSON work. |
| `libreoffice` | Yes | No | Important gap for Office conversion and XLSX recalculation. |
| `nodejs` | Yes | Base/runtime concern | Pi itself runs on Node; not a recipe system requirement. |
| `npm` | Yes | Base/runtime concern | Not exposed as first-cut task dependency. |
| `pandoc` | Yes | No | Important gap for Markdown-to-DOCX and DOCX-to-Markdown flows. |
| `poppler-utils` | Yes | No | Gap for PDF rendering; Python text extraction remains available. |
| `ripgrep` | Yes | No | `grep`, `find`, and `bash` are available instead. |
| `sed` | Yes | Not declared | Likely base shell utility; not recipe-level for us. |
| `tesseract-ocr` | Yes | No | Gap for OCR/scanned documents. |

### JavaScript Packages

| Package | Harvey LAB sandbox | Our first cut | Notes |
| --- | --- | --- | --- |
| `docx` | Yes, globally installed | No | We are biasing to Python DOCX tooling. |
| `pptxgenjs` | Yes, globally installed | Yes, recipe-local dependency | JSON/structured PPTX generation. |

### Agent Tools

| Tool | Harvey LAB harness | Our first cut | Notes |
| --- | --- | --- | --- |
| `bash` | Yes | Yes | Shell execution. |
| `read` | Yes | Yes | File reading. |
| `write` | Yes | Yes | File writing. |
| `edit` | Yes | Yes | File editing. |
| `glob` | Yes | Covered by `find` | Pi's `find` tool finds files by glob pattern. |
| `grep` | Yes | Yes | Search file contents. |
| `finish` | Yes | No | Harvey-specific deliverable completion check. Our prompt handles completion. |
| `ls` | Not listed as Harvey tool | Yes | Supported Pi read-only directory listing. |

Current compatibility choice: only `read` is overridden. The recipe-local
extension keeps Pi's native `read({ path, offset?, limit? })` schema and native
path/truncation/image behavior by reusing Pi's `createReadToolDefinition`. It
swaps in document-aware reads for `.docx`, `.xlsx`, `.pptx`, and `.pdf` through
`recipes/legal-agent/tools/parse_document.py`. `write` and `edit` remain Pi
native.

## Harvey LAB Agent Sandbox

Harvey's agent sandbox is defined in
`harvey-labs/lab_core/sandbox/Dockerfile`.

System tools installed for the agent:

- `bash`
- `ca-certificates`
- `coreutils`
- `curl`
- `file`
- `findutils`
- `gawk`
- `gcc`
- `g++`
- `git`
- `grep`
- `procps`
- `jq`
- `libreoffice`
- `nodejs`
- `npm`
- `pandoc`
- `poppler-utils`
- `ripgrep`
- `sed`
- `tesseract-ocr`

Python packages installed in the agent sandbox:

- `defusedxml`
- `diff-match-patch`
- `docxtpl`
- `lxml`
- `markitdown`
- `openpyxl`
- `pandas`
- `pdf2image`
- `pdfplumber`
- `pillow`
- `pypdf`
- `python-docx`
- `python-pptx`

Global JavaScript document packages:

- `docx`
- `pptxgenjs`

Harvey also has file-format skills for DOCX, XLSX, and PPTX under
`harvey-labs/lab_core/harness/skills/`. Those scripts use the packages above
and sometimes rely on system binaries such as `pandoc` and LibreOffice.

## Our Current Recipe

Current agent tools in `recipes/legal-agent/agents/agent.yaml`:

- `read`
- `ls`
- `grep`
- `find`
- `bash`
- `write`
- `edit`

Pi also supports `ls`, and `find` is the built-in file glob tool. There is no
separate built-in `glob` tool we need to add.

Current recipe runtime dependencies:

- Recipe-local Python project at `recipes/legal-agent/python`.
- `pi.runtime.python` points at `python/pyproject.toml` and `python/uv.lock`.
- Python import preflights are declared in `package.json#pi.runtime.python.imports`.
- No system capability declaration yet.

## First-Cut Recommendation

Skip LibreOffice, Pandoc, OCR, and other system capabilities initially. They
are the hardest to make portable because Introspection accepts reviewed system
capability IDs, not arbitrary `apt-get` packages or startup scripts.

Start with recipe-local Python libraries that match Harvey's Python document
stack:

- `defusedxml` for safe XML parsing.
- `diff-match-patch` for diff/redline workflows.
- `python-docx` for DOCX memos and tables.
- `docxtpl` for templated DOCX generation.
- `lxml` for lower-level Office XML inspection and edits when needed.
- `markdown-it-py` for Python-only Markdown-to-DOCX generation.
- `openpyxl` for XLSX creation and inspection.
- `pandas` for tabular manipulation.
- `python-pptx` for basic PPTX inspection or generation.
- `md2ppt` for Markdown-to-PPTX generation without Marp CLI.
- `pdf2image` for PDF-to-image conversion when its system backend exists.
- `pdfplumber` for text-based PDF extraction.
- `pypdf` for PDF structure and text basics.
- `pillow` for image support.

Likely `pi.runtime.python.imports` preflight list:

- `defusedxml`
- `docx`
- `docxtpl`
- `diff_match_patch`
- `lxml`
- `markdown_it`
- `md2ppt`
- `openpyxl`
- `pandas`
- `pdf2image`
- `pptx`
- `pdfplumber`
- `pypdf`
- `PIL`
- `markitdown`

Add `ls` to the agent tools because it is a supported read-only built-in.

## Known Gaps Versus Harvey

The first cut will not match Harvey's full sandbox.

Missing or unconfirmed system tools:

- `pandoc`: Harvey uses this for DOCX and Markdown conversion. Without it, the
  agent should create DOCX directly with `python-docx` instead of converting
  Markdown to DOCX.
- `libreoffice`: Harvey uses this for Office rendering/conversion and XLSX
  recalculation. Without it, XLSX formulas may not recalculate.
- `tesseract-ocr`: without OCR, scanned PDFs/images are weak.
- `poppler-utils`: useful for PDF rendering/extraction; Python libraries cover
  some but not all use cases.
- `ripgrep`: helpful but not essential because the agent has `grep`, `find`,
  and `bash`.
- JS package `docx`: not needed in the first cut if we bias toward Python
  document libraries.
- Marp CLI: skipped for now because its PPTX/PDF/image export depends on
  browser rendering, and editable output also depends on LibreOffice.

System capability `document.pdf-tools@1` is documented as an example
Introspection capability ID, but the public docs do not specify exactly which
binaries it contains. We should not assume it includes Pandoc, LibreOffice, or
OCR. Add it only after a cloud probe or a specific failing task shows we need
it.

## System Prompt Implication

The base prompt should tell the agent not to assume Pandoc or LibreOffice are
available. For DOCX deliverables, it should prefer direct generation with
`python-docx` and validate that the requested file exists under
`/workspace/outputs`. For XLSX deliverables, it should use `openpyxl` and avoid
claiming formulas were recalculated unless it actually verified that capability.
