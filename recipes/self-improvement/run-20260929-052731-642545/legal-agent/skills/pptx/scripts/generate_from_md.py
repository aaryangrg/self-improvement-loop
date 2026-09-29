"""Generate a .pptx from Markdown using md2ppt.

Usage:
    python generate_from_md.py input.md output.pptx [--theme theme.json]

The Markdown should follow md2ppt conventions:
- A single "# Title" heading for the title slide.
- "## Heading" for each content slide.
- Bullets, numbered lists, and plain paragraphs for slide body content.
"""

import argparse
import json
from pathlib import Path

from md2ppt import SlidesBuilder


def load_theme(path: str | None) -> dict[str, str] | None:
    if path is None:
        return None
    with Path(path).open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("theme JSON must be an object")
    return {str(k): str(v).lstrip("#") for k, v in data.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_md")
    parser.add_argument("output_pptx")
    parser.add_argument("--theme", help="Optional JSON object of Office theme colors")
    args = parser.parse_args()

    input_path = Path(args.input_md)
    output_path = Path(args.output_pptx)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    markdown = input_path.read_text(encoding="utf-8")
    theme_colors = load_theme(args.theme)
    if theme_colors is None:
        SlidesBuilder(markdown, str(output_path)).build()
    else:
        SlidesBuilder(markdown, str(output_path), theme_colors=theme_colors).build()

    print(f"OK: wrote {output_path}")


if __name__ == "__main__":
    main()
