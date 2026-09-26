---
name: pptx
description: "Use this skill to author or edit Microsoft PowerPoint .pptx files. Covers generating decks from scratch with PptxGenJS, Markdown-to-PPTX with md2ppt, or python-pptx; editing existing decks in place; and validating output. For READING existing .pptx files, use the `read_file` tool; do not invoke this skill. Triggers: 'build a deck', 'create slides', 'edit slide N', 'add a chart slide'. Does NOT apply to .pdf, .docx, .xlsx, or .ppt (legacy)."
---

# PPTX Authoring And Editing

> **Reading is not in scope.** To read an existing .pptx, use the `read_file` tool.
> It returns extracted slide text. Use this skill for writing, editing, and
> validating .pptx files.

## Quick Reference

| Goal | Use |
|---|---|
| Generate a deck from a JSON spec | `/workspace/skills/pptx/scripts/generate_pptxgenjs.js` |
| Generate a deck from Markdown | `/workspace/skills/pptx/scripts/generate_from_md.py` |
| Build slides programmatically | `python-pptx` directly |
| Edit a shape on an existing slide | `/workspace/skills/pptx/scripts/edit_shape.py` |
| Add or remove a slide | `/workspace/skills/pptx/scripts/unpack.py` -> mutate XML -> `/workspace/skills/pptx/scripts/pack.py` |
| QA a deck deterministically | `/workspace/skills/pptx/scripts/deterministic_qa.py` |
| Validate before delivery | `/workspace/skills/pptx/scripts/validate.py` |

Skill scripts are available at `/workspace/skills/pptx/scripts/`. Invoke them
from `bash` with `python`.

## Creating A New Deck

Use `/workspace/skills/pptx/scripts/generate_pptxgenjs.js` when a deck is
easiest to describe as structured JSON with text, bullets, images, notes, and
positioned shapes:

```bash
node /workspace/skills/pptx/scripts/generate_pptxgenjs.js deck.json /workspace/outputs/output.pptx
```

Use `/workspace/skills/pptx/scripts/generate_from_md.py` when the source content
is already Markdown:

```bash
python /workspace/skills/pptx/scripts/generate_from_md.py deck.md /workspace/outputs/output.pptx
```

For best Markdown conversion, use one `#` title slide and `##` headings for
content slides. Bullets, numbered lists, and plain paragraphs become slide body
content.

Use `python-pptx` directly when building a new deck. It is best for structured
legal and analytical decks where slide content, tables, and computed values can
be placed programmatically.

Write final deliverables under `/workspace/outputs`.

## Editing Existing Decks

Three-step pattern, like docx:

```bash
python /workspace/skills/pptx/scripts/unpack.py input.pptx workdir/
# edit XML files under workdir/ppt/slides/
python /workspace/skills/pptx/scripts/pack.py workdir/ /workspace/outputs/output.pptx
python /workspace/skills/pptx/scripts/validate.py /workspace/outputs/output.pptx
```

For surgical shape edits without unpacking, use `edit_shape.py`:

```bash
python /workspace/skills/pptx/scripts/edit_shape.py input.pptx \
  /workspace/outputs/output.pptx \
  --slide 2 --shape "Title 1" --op set_text --value "New title"
```

JSON patch ops: `set_text`, `set_position` (EMU), `set_size`, `recolor`, `delete`.

## OOXML Gotchas Specific To PPTX

- **Use `defusedxml.minidom`, NOT `xml.etree.ElementTree`.** ElementTree corrupts presentation namespaces during round-tripping. `unpack.py` and `pack.py` use minidom; if you write your own XML manipulation, do the same.
- **EMU units everywhere.** 1 inch = 914400 EMU. Slide positions, sizes, font sizes (in pt × 100) all use derived EMU values.
- **Placeholders vs free shapes.** Placeholders inherit from slide masters; free shapes don't. Editing a placeholder's text is `<a:t>` content; editing its layout requires master-slide changes.
- **Don't pretty-print pptx XML on pack.** Whitespace-significant runs (`<a:r>`) break if reformatted. `pack.py` preserves the original whitespace.
- **Slide cloning is more than a file copy.** Use `unpack` + `pack` — manual file copies miss the rIds in `_rels/` and the `Content_Types.xml` registration.

## Deterministic QA Loop

After every generation, run:

```bash
python /workspace/skills/pptx/scripts/deterministic_qa.py /workspace/outputs/output.pptx > /workspace/outputs/qa.json
```

`deterministic_qa.py` checks:
- Shape bounding boxes don't extend past slide edges
- No two shapes overlap with > 50% area intersection
- Font sizes ≥ 11pt for body text, ≥ 18pt for titles
- All placeholders are filled (no `Click to add title` defaults)
- Bullet lists don't exceed 7 items per slide

Output is JSON listing each violation with slide number and shape id. Fix
violations before delivery.

## Validation Gate

**Always run `validate.py` before declaring done.** Schema-validates against ECMA-376 PresentationML XSDs, checks rId consistency, content-type registration.

```bash
python /workspace/skills/pptx/scripts/validate.py /workspace/outputs/output.pptx
```

## Out Of Scope

- Reading: use the `read_file` tool.
- Full Marp CLI rendering, browser-based rendering, and image/PDF export.
- High-fidelity arbitrary HTML/CSS-to-PPTX conversion.
- SmartArt creation (limited python-pptx support).
- Complex embedded charts beyond what python-pptx exposes.
- Slide transitions / animations (rarely matter for legal output).
- Vision-model layout review (deferred to v2).
