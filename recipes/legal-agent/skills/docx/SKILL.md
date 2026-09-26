---
name: docx
description: "Use this skill to author, edit, redline, comment on, or validate Microsoft Word .docx files using the available Python document stack. For READING existing .docx files, use the `read_file` tool; this skill is for producing or modifying .docx deliverables. Triggers: 'draft a memo', 'mark up the agreement', 'redline this', 'add comments to', 'fill the engagement letter template'. Does NOT apply to .pdf, .xlsx, .pptx, or legacy .doc files."
---

# DOCX Authoring, Editing, Redlining

> **Reading is not in scope.** To read an existing .docx, use the `read_file` tool.
> It returns extracted document text. Use this skill for writing, editing,
> commenting, redlining, and validating .docx files.

## Quick Reference

| Goal | Use |
|---|---|
| Generate a new doc from Markdown | `/workspace/skills/docx/scripts/generate_from_md.py` |
| Generate a new doc programmatically | `python-docx` directly |
| Fill a templated agreement | `/workspace/skills/docx/scripts/template_fill.py` with `docxtpl` |
| Edit an existing doc | `/workspace/skills/docx/scripts/unpack.py` -> mutate XML -> `/workspace/skills/docx/scripts/pack.py` |
| Produce a tracked-changes redline | `/workspace/skills/docx/scripts/redline.py` |
| Add comments to a passage | `/workspace/skills/docx/scripts/comments_add.py` |
| Accept all redlines | `/workspace/skills/docx/scripts/accept_changes.py` |
| Validate before delivery | `/workspace/skills/docx/scripts/validate.py` |

Skill scripts are available at `/workspace/skills/docx/scripts/`. Invoke them
from `bash` with `python`.

## Creating A New Document

Use `python-docx` for ordinary drafting. It is the default path for memos,
letters, tables, schedules, and documents with computed values.

Use `/workspace/skills/docx/scripts/generate_from_md.py` when you have drafted the document as
Markdown:

```bash
python /workspace/skills/docx/scripts/generate_from_md.py draft.md /workspace/outputs/output.docx
```

Use `/workspace/skills/docx/scripts/template_fill.py` when the input is an existing .docx template with
Jinja-style placeholders:

```bash
python /workspace/skills/docx/scripts/template_fill.py template.docx context.json /workspace/outputs/output.docx
```

Write final deliverables under `/workspace/outputs`.

## Editing An Existing Document

Three-step pattern:

```bash
python /workspace/skills/docx/scripts/unpack.py input.docx workdir/
# edit XML files under workdir/word/
python /workspace/skills/docx/scripts/pack.py workdir/ /workspace/outputs/output.docx
python /workspace/skills/docx/scripts/validate.py /workspace/outputs/output.docx
```

Key files inside the unpacked tree:

- `word/document.xml` - body content.
- `word/styles.xml` - paragraph and run styles.
- `word/numbering.xml` - list numbering definitions.
- `word/comments.xml` - comment thread content.
- `word/header*.xml`, `word/footer*.xml` - headers and footers.
- `[Content_Types].xml` - MIME registration for parts.
- `_rels/` and `word/_rels/` - relationships between parts.

## Redlines

```bash
python /workspace/skills/docx/scripts/redline.py original.docx revised.docx /workspace/outputs/redlined.docx \
  --author "Reviewer" --date "2026-04-30"
```

The script can create native `<w:ins>` and `<w:del>` tracked-change elements.
If the optional redline package is unavailable, use `--mode=manual`; the manual
mode uses the available Python diff libraries.

## Comments

```bash
python /workspace/skills/docx/scripts/comments_add.py document.docx comments.json /workspace/outputs/commented.docx
```

`comments.json` is a list of `{anchor_text, author, comment}` objects. Anchor
matching is exact-string. If `anchor_text` appears multiple times, the script
comments the first occurrence.

## Accept Changes

```bash
python /workspace/skills/docx/scripts/accept_changes.py redlined.docx /workspace/outputs/accepted.docx
```

This script directly edits OOXML: inserted text is kept and deleted text is
removed. It does not require an office application.

## Validation Gate

Always run validation before declaring the task complete:

```bash
python /workspace/skills/docx/scripts/validate.py /workspace/outputs/output.docx
```

Validation checks ZIP integrity, XML well-formedness, content types, and
relationship consistency. Exit code 0 means the .docx package is structurally
valid.

## Common Pitfalls

- Adjacent Word runs may split human-visible text. `unpack.py` merges adjacent
  same-formatted runs where possible.
- Smart quotes and whitespace inside `<w:t>` elements are significant. Let the
  unpack/pack scripts handle escaping and `xml:space`.
- List numbering lives in `word/numbering.xml`; keep `numId` references
  consistent.
- Headers and footers are separate parts; body edits do not update them.
- Legacy `.doc`, signing, encryption, DRM, and macros are out of scope.
