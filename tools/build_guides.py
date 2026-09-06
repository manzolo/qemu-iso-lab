#!/usr/bin/env python3
"""Render docs/guides/<lang>/*.md (markdown) and *.html (curated pages) into PDFs under docs/guides/pdf/<lang>/ with weasyprint.

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
OUT = SRC / "pdf"  # pdf/<lang>/qemu-iso-lab-guide.pdf (cover + every chapter) and pdf/<lang>/singole|single/<name>.pdf

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


LANGS = {
    "it": {
        "title": "guida", "html_lang": "it",
        "sub": "Installazioni non presidiate su QEMU/KVM, laboratorio di rete, libvirt: le guide passo passo in un solo manuale",
        "chapters": "Capitoli", "singles": "singole",
        "foot": "Generato il {date} da docs/guides/it/ con make guides · sorgenti Markdown e HTML nel repository manzolo/qemu-iso-lab · i capitoli seguono l'ordine dei nomi file (00-, 05-, 10-...)",
        "meta": "qemu-iso-lab · generato il {date} da docs/guides/it/{name}",
    },
    "en": {
        "title": "guide", "html_lang": "en",
        "sub": "Unattended installs on QEMU/KVM, the network lab, libvirt: the step-by-step guides in one manual",
        "chapters": "Chapters", "singles": "single",
        "foot": "Generated on {date} from docs/guides/en/ with make guides · Markdown and HTML sources in the manzolo/qemu-iso-lab repository · chapters follow the file-name order (00-, 05-, 10-...)",
        "meta": "qemu-iso-lab · generated on {date} from docs/guides/en/{name}",
    },
}

COVER_CSS = """
@page { size: A4; margin: 0; }
html { font: 11pt/1.5 "DejaVu Sans", "Noto Sans", sans-serif; color: #1c1c1c; }
.cover { height: 297mm; padding: 40mm 22mm; box-sizing: border-box;
  background: linear-gradient(160deg, #11223f 0%, #1d3a6e 55%, #2f6fb0 100%); color: #fff; }
.cover h1 { font-size: 34pt; margin: 0 0 4mm; letter-spacing: -0.01em; }
.cover .mono { font-family: "DejaVu Sans Mono", monospace; color: #ffd479; }
.cover .sub { font-size: 13pt; color: #c7d6ee; margin-bottom: 18mm; }
.cover h2 { font-size: 12pt; color: #ffd479; margin: 0 0 3mm; text-transform: uppercase; letter-spacing: .08em; }
.cover ol { margin: 0; padding-left: 8mm; font-size: 11.5pt; line-height: 1.9; }
.cover ol li span { color: #aebfdc; font-size: 9.5pt; margin-left: 2mm; }
.cover .foot { position: absolute; bottom: 18mm; left: 22mm; right: 22mm; font-size: 9pt; color: #aebfdc; }
"""


def title_of(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".html":
        import re
        match = re.search(r"<title>(.*?)</title>", text, re.S)
        return match.group(1).strip() if match else path.stem
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def cover_html(lang: str, sources: list[Path]) -> str:
    text = LANGS[lang]
    items = "".join(f"<li>{title_of(src)} <span>{src.name}</span></li>" for src in sources)
    return (
        f"<!doctype html><html lang='{text['html_lang']}'><head><meta charset='utf-8'><style>{COVER_CSS}</style></head><body>"
        f"<div class='cover'><h1><span class='mono'>qemu-iso-lab</span> · {text['title']}</h1>"
        f"<div class='sub'>{text['sub']}</div>"
        f"<h2>{text['chapters']}</h2><ol>{items}</ol>"
        f"<div class='foot'>{text['foot'].format(date=date.today().isoformat())}</div>"
        f"</div></body></html>"
    )


def document(lang: str, src: Path):  # type: ignore[no-untyped-def] - weasyprint Document, developer tool
    if src.suffix == ".html":
        return HTML(filename=str(src)).render()
    text = src.read_text(encoding="utf-8")
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists", "toc"])
    meta = f'<p class="meta">{LANGS[lang]["meta"].format(date=date.today().isoformat(), name=src.name)}</p>'
    html = f"<!doctype html><html lang='{LANGS[lang]['html_lang']}'><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body}{meta}</body></html>"
    return HTML(string=html, base_url=str(src.parent)).render()


def guide_sources(lang: str) -> list[Path]:
    folder = SRC / lang
    return sorted(p for p in list(folder.glob("*.md")) + list(folder.glob("*.html")) if p.name != "README.md")


def build_language(lang: str) -> None:
    sources = guide_sources(lang)
    out = OUT / lang
    singles = out / LANGS[lang]["singles"]
    singles.mkdir(parents=True, exist_ok=True)
    documents = []
    for src in sources:
        doc = document(lang, src)
        pdf_path = singles / (src.stem + ".pdf")
        doc.write_pdf(str(pdf_path))
        documents.append(doc)
        print(f"  {src.relative_to(ROOT)} -> {pdf_path.relative_to(ROOT)}")
    cover = HTML(string=cover_html(lang, sources)).render()
    pages = [page for doc in [cover, *documents] for page in doc.pages]
    manual = out / "qemu-iso-lab-guide.pdf"
    cover.copy(pages).write_pdf(str(manual))
    print(f"  {len(pages)} pages -> {manual.relative_to(ROOT)}")


def main(argv: list[str]) -> int:
    langs = argv[1:] or sorted(LANGS)
    for lang in langs:
        if lang not in LANGS:
            sys.exit(f"unknown language {lang!r}: choose from {', '.join(sorted(LANGS))}")
        build_language(lang)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
