"""Generate a .docx from Markdown using python-docx.

Usage:
    python generate_from_md.py input.md output.docx [template.docx]

Supports common document Markdown: headings, paragraphs, emphasis, inline code,
bulleted/numbered lists, block quotes, horizontal rules, and pipe tables.
"""

from __future__ import annotations

import sys
from pathlib import Path

from docx import Document
from docx.document import Document as DocumentObject
from docx.oxml import OxmlElement
from docx.text.paragraph import Paragraph
from markdown_it import MarkdownIt
from markdown_it.token import Token

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def clear_body(document: DocumentObject) -> None:
    body = document._body._element  # noqa: SLF001
    for child in list(body):
        if child.tag.endswith("}sectPr"):
            continue
        body.remove(child)


def add_horizontal_rule(paragraph: Paragraph) -> None:
    p_pr = paragraph._p.get_or_add_pPr()  # noqa: SLF001
    border = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(f"{W}val", "single")
    bottom.set(f"{W}sz", "6")
    bottom.set(f"{W}space", "1")
    bottom.set(f"{W}color", "auto")
    border.append(bottom)
    p_pr.append(border)


def add_inline(paragraph: Paragraph, children: list[Token]) -> None:
    bold = False
    italic = False

    for child in children:
        if child.type == "text":
            run = paragraph.add_run(child.content)
            run.bold = bold
            run.italic = italic
        elif child.type == "code_inline":
            run = paragraph.add_run(child.content)
            run.font.name = "Courier New"
        elif child.type == "strong_open":
            bold = True
        elif child.type == "strong_close":
            bold = False
        elif child.type == "em_open":
            italic = True
        elif child.type == "em_close":
            italic = False
        elif child.type in {"softbreak", "hardbreak"}:
            paragraph.add_run().add_break()


def collect_table(tokens: list[Token], start: int) -> tuple[list[list[list[Token]]], int]:
    rows: list[list[list[Token]]] = []
    row: list[list[Token]] | None = None
    index = start + 1

    while index < len(tokens):
        token = tokens[index]
        if token.type == "table_close":
            if row is not None:
                rows.append(row)
            return rows, index
        if token.type == "tr_open":
            row = []
        elif token.type == "tr_close":
            if row is not None:
                rows.append(row)
            row = None
        elif token.type == "inline" and row is not None:
            row.append(token.children or [token])
        index += 1

    return rows, index


def render_tokens(document: DocumentObject, tokens: list[Token]) -> None:
    list_stack: list[str] = []
    quote_depth = 0
    index = 0

    while index < len(tokens):
        token = tokens[index]

        if token.type == "heading_open":
            level = min(int(token.tag[1]), 9)
            inline = tokens[index + 1]
            paragraph = document.add_heading(level=level)
            add_inline(paragraph, inline.children or [])
            index += 3
            continue

        if token.type == "paragraph_open":
            inline = tokens[index + 1]
            style = None
            if list_stack:
                style = "List Bullet" if list_stack[-1] == "bullet" else "List Number"
            elif quote_depth:
                style = "Intense Quote"
            paragraph = document.add_paragraph(style=style)
            add_inline(paragraph, inline.children or [])
            index += 3
            continue

        if token.type == "bullet_list_open":
            list_stack.append("bullet")
        elif token.type == "ordered_list_open":
            list_stack.append("ordered")
        elif token.type in {"bullet_list_close", "ordered_list_close"} and list_stack:
            list_stack.pop()
        elif token.type == "blockquote_open":
            quote_depth += 1
        elif token.type == "blockquote_close":
            quote_depth = max(0, quote_depth - 1)
        elif token.type == "hr":
            add_horizontal_rule(document.add_paragraph())
        elif token.type == "table_open":
            rows, index = collect_table(tokens, index)
            if rows:
                table = document.add_table(rows=len(rows), cols=max(len(row) for row in rows))
                table.style = "Table Grid"
                for row_index, row in enumerate(rows):
                    for col_index, cell_tokens in enumerate(row):
                        add_inline(table.cell(row_index, col_index).paragraphs[0], cell_tokens)

        index += 1


def generate(md_path: Path, output_path: Path, template_path: Path | None = None) -> None:
    document = Document(str(template_path)) if template_path else Document()
    if template_path:
        clear_body(document)

    markdown = md_path.read_text(encoding="utf-8")
    parser = MarkdownIt("commonmark").enable("table")
    render_tokens(document, parser.parse(markdown))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(output_path)
    print(f"OK: wrote {output_path}")


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        print(
            "Usage: generate_from_md.py <input.md> <output.docx> [template.docx]",
            file=sys.stderr,
        )
        sys.exit(2)
    template = Path(sys.argv[3]) if len(sys.argv) == 4 else None
    generate(Path(sys.argv[1]), Path(sys.argv[2]), template)
