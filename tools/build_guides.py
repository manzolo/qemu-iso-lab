#!/usr/bin/env python3
"""Render docs/guides/*.md into printable PDFs (docs/guides/pdf/) with markdown + weasyprint.

Developer tool, not part of vmctl: `make guides`. Needs the `markdown` and `weasyprint`
Python packages (pip install --user markdown weasyprint).
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

try:
    import markdown
    from weasyprint import HTML
except ImportError as exc:  # pragma: no cover - developer tool
    sys.exit(f"missing dependency: {exc.name}. Install with: pip install --user markdown weasyprint")

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "guides"
OUT = SRC / "pdf"

CSS = """
@page { size: A4; margin: 18mm 16mm 20mm 16mm;
  @bottom-center { content: "qemu-iso-lab · " string(doctitle) · " · pagina " counter(page) " di " counter(pages);
                   font: 8.5pt "DejaVu Sans", sans-serif; color: #666; } }
html { font: 10.5pt/1.45 "DejaVu Sans", "Noto Sans", sans-serif; color: #1c1c1c; }
h1 { font-size: 21pt; margin: 0 0 4mm; padding-bottom: 2mm; border-bottom: 2px solid #2b5797; string-set: doctitle content(); }
h2 { font-size: 14.5pt; margin: 7mm 0 2.5mm; color: #2b5797; page-break-after: avoid; }
h3 { font-size: 11.5pt; margin: 5mm 0 1.5mm; page-break-after: avoid; }
p { margin: 0 0 2.2mm; }
code { font: 9pt "DejaVu Sans Mono", monospace; background: #f2f4f7; padding: 0 2px; border-radius: 2px; }
pre { font: 8.6pt/1.35 "DejaVu Sans Mono", monospace; background: #f2f4f7; border-left: 3px solid #2b5797;
      padding: 2.2mm 3mm; margin: 1.5mm 0 3mm; white-space: pre-wrap; word-break: break-all; page-break-inside: avoid; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; width: 100%; margin: 1.5mm 0 3.5mm; font-size: 9.3pt; page-break-inside: avoid; }
th, td { border: 1px solid #cfd4dc; padding: 1.3mm 2mm; text-align: left; vertical-align: top; }
th { background: #e8edf5; }
ol, ul { margin: 0 0 2.5mm 5mm; padding: 0; }
li { margin: 0 0 1.2mm; }
blockquote { margin: 2mm 0 3mm; padding: 1.5mm 3mm; border-left: 3px solid #d9a441; background: #fdf7e8; }
blockquote p { margin: 0; }
.meta { color: #666; font-size: 9pt; margin-bottom: 5mm; }
"""


def render(md_path: Path) -> Path:
    text = md_path.read_text(encoding="utf-8")
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists", "toc"])
    meta = f'<p class="meta">qemu-iso-lab · generato il {date.today().isoformat()} da docs/guides/{md_path.name}</p>'
    html = f"<!doctype html><html lang='it'><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body}{meta}</body></html>"
    OUT.mkdir(parents=True, exist_ok=True)
    pdf_path = OUT / (md_path.stem + ".pdf")
    HTML(string=html, base_url=str(SRC)).write_pdf(str(pdf_path))
    return pdf_path


def main(argv: list[str]) -> int:
    sources = [Path(a) for a in argv[1:]] or sorted(SRC.glob("*.md"))
    for src in sources:
        print(f"  {src.relative_to(ROOT)} -> {render(src).relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
