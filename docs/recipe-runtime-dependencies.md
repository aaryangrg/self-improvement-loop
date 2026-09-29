# Recipe Runtime Dependencies

Harvey LAB defines its agent sandbox in
`harvey-labs/lab_core/sandbox/Dockerfile`. The Introspection baseline at
`recipes/legal-agent/` provides a deliberate subset, with recipe-local
packages and a document-aware `read_file` tool.

## How The Recipe Installs Packages

- `recipes/legal-agent/python/pyproject.toml` declares direct Python packages.
- `recipes/legal-agent/python/uv.lock` pins their resolved versions.
- `recipes/legal-agent/package.json#pi.runtime.python` tells Introspection to
  install that locked project and preflight selected imports in the managed
  agent runtime. The root repository's `pyproject.toml` and `uv.lock` install
  the runner, not the cloud agent's Python packages.
- The recipe's `package.json` and `pnpm-lock.yaml` pin its Node dependency,
  PptxGenJS. System binaries are not installed by either lockfile.

## Python Packages

| Package | Harvey agent | Our recipe | Purpose or gap |
| --- | --- | --- | --- |
| `defusedxml` | Yes | Yes | Safer XML parsing. |
| `diff-match-patch` | Yes | Yes | Diff/redline support. |
| `docxtpl` | Yes | Yes | DOCX template filling. |
| `lxml` | Yes | Yes | Office XML inspection/editing. |
| `markdown-it-py` | No | Yes | Markdown parsing for Python DOCX generation. |
| `markitdown` | Yes | No | Its `magika`/`onnxruntime` dependency failed cloud installation; `read_file` uses format-specific parsers instead. |
| `md2ppt` | No | Yes | Markdown-to-PPTX without Marp CLI. |
| `openpyxl` | Yes | Yes | XLSX reading/writing and formulas, not calculation. |
| `pandas` | Yes | Yes | Tables and data manipulation. |
| `pdf2image` | Yes | Yes | Installed, but PDF rendering needs an available Poppler backend. |
| `pdfplumber` | Yes | Yes | Text/table extraction from digital PDFs. |
| `pillow` | Yes | Yes | Image support. |
| `pypdf` | Yes | Yes | PDF structure and basic text extraction. |
| `python-docx` | Yes | Yes | DOCX creation/editing. |
| `python-pptx` | Yes | Yes | PPTX inspection/generation. |

Harvey also installs `anthropic`, `openai`, `google-genai`, `matplotlib`, and
`seaborn` for its harness and evaluation work. They are not installed inside
our task-agent recipe: Introspection supplies the agent's managed LLM, and
the local runner owns evaluation and dashboard work.

## System And Node Tools

| Dependency | Harvey agent | Our recipe | Practical effect |
| --- | --- | --- | --- |
| `bash`, `grep` | Yes | Agent tools | Shell work and content search are exposed. |
| `findutils`, `ripgrep` | Yes | Not declared | Pi's `find` and `grep` cover file discovery/search. |
| `libreoffice` | Yes | No | No Office rendering, conversion, or local XLSX formula recalculation. |
| `pandoc` | Yes | No | DOCX/PPTX authoring uses direct Python helpers instead of conversion. |
| `poppler-utils` | Yes | No | Text-based PDF reading remains; do not assume PDF-to-image rendering. |
| `tesseract-ocr` | Yes | No | No OCR path for scanned PDFs/images. |
| `docx` (Node) | Global | No | `python-docx` covers our DOCX authoring path. |
| `pptxgenjs` (Node) | Global | Recipe-local | Structured slide generation. |

Harvey's image also installs `ca-certificates`, `coreutils`, `curl`, `file`,
`gawk`, `gcc`, `g++`, `git`, `jq`, `nodejs`, `npm`, `procps`, and `sed`.
These are not declared as recipe system capabilities; some may exist in the
Introspection base image, but the recipe does not rely on them. Marp CLI is
also not part of this recipe.

## Agent Tool Mapping

| Harvey tool | Our agent | Mapping |
| --- | --- | --- |
| `read` | `read_file` | Pi's read-tool definition with document parsing for DOCX, XLSX, PPTX, and PDF; text/image behavior stays native. |
| `write`, `edit`, `bash`, `grep` | Same names | Pi's native implementations. |
| `glob` | `find` | Pi's file-pattern discovery tool. |
| `finish` | Not exposed | Harvey-specific deliverable check; the task prompt specifies output location. |
| No listed `ls` | `ls` | Pi's read-only directory listing. |

`read_file` extracts text and tables where supported; it does not reproduce
Harvey's full document rendering or OCR environment. The adapted DOCX, XLSX,
and PPTX skills describe authoring paths backed by the packages above. See
[Architecture](architecture.md#baseline-agent-construction) for the prompt and
skill changes.
