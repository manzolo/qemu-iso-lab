#!/usr/bin/env python3
"""Render one Markdown page of docs/ (with its screenshots) into a PDF next to it, in the style
of the guides: `python3 tools/build_doc_pdf.py docs/FIRST_VM.md` -> docs/FIRST_VM.pdf.

Developer tool, like tools/build_guides.py (same CSS, same `markdown` + `weasyprint` needs).
Links to other pages stay links to the repository on GitHub, because a PDF is read on its own.
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_guides import CSS, HTML, markdown  # noqa: E402  (exits with a clear message without them)

ROOT = Path(__file__).resolve().parent.parent
REPO = "https://github.com/manzolo/qemu-iso-lab/blob/main/"
EXTRA_CSS = """
img { display: block; width: 100%; margin: 2mm 0 4.5mm; border: 1px solid #cfd4dc; border-radius: 2mm;
      page-break-inside: avoid; }
h2 { page-break-before: auto; }
p:has(> img) { page-break-inside: avoid; }
"""


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    src = (ROOT / argv[1]).resolve() if not Path(argv[1]).is_absolute() else Path(argv[1])
    text = src.read_text(encoding="utf-8")
    # Other .md pages become absolute GitHub links; in-page anchors and images are left alone.
    text = re.sub(r"\]\((?!https?:|#|screenshots/)([^)#]+\.md)(#[^)]*)?\)",
                  lambda m: f"]({REPO}{src.parent.relative_to(ROOT).as_posix()}/{m.group(1)}{m.group(2) or ''})", text)
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists", "toc"])
    meta = (f'<p class="meta">qemu-iso-lab · generated on {date.today().isoformat()} from '
            f'{src.relative_to(ROOT).as_posix()} · {REPO}{src.relative_to(ROOT).as_posix()}</p>')
    html = (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><style>{CSS}{EXTRA_CSS}</style></head>"
            f"<body>{body}{meta}</body></html>")
    out = src.with_suffix(".pdf")
    HTML(string=html, base_url=str(src.parent)).write_pdf(str(out))
    print(f"{out.relative_to(ROOT)}  {out.stat().st_size // 1024} KiB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
