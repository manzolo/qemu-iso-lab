"""Per-profile documentation out of a check-vms report: facts, outcome, screenshot timeline.

`vmctl report-pdf <report-dir>` writes, for every `results/<vm>.json`, one Markdown page and one
PDF per language under `<report-dir>/pdf/<lang>/`, plus an index. The pictures are the frames the
timeline watcher kept while the row ran (`report.watch_timeline`) and the final screen. Markdown
is always written; the PDF needs the `markdown` and `weasyprint` packages, like `make guides`.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from vmctl import cloud_init, config, report, runtime, ui
from vmctl.errors import VMError

LANGS = ("en", "it")

LABELS: dict[str, dict[str, str]] = {
    "en": {
        "title": "Profile sheet", "facts": "Profile", "outcome": "Outcome of the run", "timeline": "Installation, step by step",
        "final": "Final screen", "flow": "Flow", "status": "Profile status", "verified": "Last live PASS", "iso": "Installation media",
        "memory": "Memory", "cpus": "vCPUs", "disk": "Disk", "firmware": "Firmware", "machine": "Machine", "ssh": "SSH port on the host",
        "checks": "What the post-install verifies", "result": "Result", "duration": "Duration", "phase": "Phase reached", "detail": "Detail",
        "minutes": "min", "elapsed": "after", "no_frames": "No timeline frames were kept for this row (the guest never painted a screen the watcher could read).",
        "generated": "Generated on", "from_report": "from the check-vms report", "index_title": "Validation matrix", "profiles": "profiles",
        "none": "—", "truncated": "The timeline stops here: the watcher keeps at most {max} frames.",
        "phase_install": "installer", "phase_post-install": "post-install", "phase_boot": "boot check", "phase_validation": "validation",
        "console": "serial console",
    },
    "it": {
        "title": "Scheda profilo", "facts": "Profilo", "outcome": "Esito del giro", "timeline": "Installazione, passo dopo passo",
        "final": "Schermo finale", "flow": "Flow", "status": "Stato del profilo", "verified": "Ultimo PASS dal vivo", "iso": "Supporto di installazione",
        "memory": "Memoria", "cpus": "vCPU", "disk": "Disco", "firmware": "Firmware", "machine": "Macchina", "ssh": "Porta SSH sull'host",
        "checks": "Cosa verifica il post-install", "result": "Risultato", "duration": "Durata", "phase": "Fase raggiunta", "detail": "Dettaglio",
        "minutes": "min", "elapsed": "dopo", "no_frames": "Nessun fotogramma conservato per questa riga (il guest non ha mai disegnato uno schermo leggibile dal watcher).",
        "generated": "Generato il", "from_report": "dal report check-vms", "index_title": "Matrice di validazione", "profiles": "profili",
        "none": "—", "truncated": "La sequenza si ferma qui: il watcher conserva al massimo {max} fotogrammi.",
        "phase_install": "installer", "phase_post-install": "post-install", "phase_boot": "boot check", "phase_validation": "validazione",
        "console": "console seriale",
    },
}

PRINT_CSS = """
@page { size: A4; margin: 18mm 16mm; @bottom-center { content: counter(page); font-size: 9pt; color: #666; } }
body { font-family: "DejaVu Sans", "Noto Sans", sans-serif; font-size: 10.5pt; color: #222; line-height: 1.4; }
h1 { font-size: 20pt; margin: 0 0 4pt 0; } h2 { font-size: 13pt; margin: 18pt 0 6pt 0; border-bottom: 1px solid #ccc; padding-bottom: 2pt; }
p.badge { color: #555; margin: 0 0 12pt 0; font-size: 9.5pt; }
table { border-collapse: collapse; width: 100%; font-size: 9.5pt; } th, td { text-align: left; vertical-align: top; padding: 3pt 6pt; border-bottom: 1px solid #e3e3e3; }
th { width: 30%; color: #444; font-weight: 600; }
figure { margin: 10pt 0; page-break-inside: avoid; } figure img { max-width: 100%; border: 1px solid #ccc; }
figcaption { font-size: 9pt; color: #555; margin-top: 3pt; }
code { font-family: "DejaVu Sans Mono", monospace; font-size: 9pt; } pre { white-space: pre-wrap; font-size: 8.5pt; background: #f6f6f6; padding: 6pt; }
.PASS { color: #1a7f37; font-weight: 700; } .FAIL { color: #b42318; font-weight: 700; } .SKIP, .WARN { color: #8a6d00; font-weight: 700; }
footer { margin-top: 18pt; font-size: 8.5pt; color: #777; }
"""


def natural_key(name: str) -> list[object]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def load_results(directory: Path) -> list[dict[str, Any]]:
    results_dir = directory / "results"
    if not results_dir.is_dir():
        raise VMError(f"Not a check-vms report directory (no results/): {ui.pretty_path(directory)}")
    rows: list[dict[str, Any]] = []
    for path in results_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise VMError(f"Unreadable result {ui.pretty_path(path)}: {exc}") from exc
        data.setdefault("id", path.stem)
        rows.append(data)
    return sorted(rows, key=lambda r: natural_key(str(r["id"])))


def verification_commands(vm: dict[str, Any]) -> list[str]:
    ssh_cfg = vm.get("ssh_provision") or {}
    commands: list[str] = []
    for key in ("post_install_run", "verify_after_reboot"):
        value = ssh_cfg.get(key)
        if isinstance(value, list):
            commands += [str(c) for c in value if "verify-desktop" in str(c) or "pgrep" in str(c) or "is-active" in str(c)]
    return commands


def profile_facts(vm: dict[str, Any], result: dict[str, Any], lang: str) -> list[tuple[str, str]]:
    t = LABELS[lang]
    meta = vm.get("meta", {})
    disk = vm.get("disk", {})
    ssh_cfg = cloud_init.ssh_access_config(vm) or {}
    iso_name = str(vm.get("iso", "")).rsplit("/", 1)[-1] or t["none"]
    facts = [
        (t["flow"], str(result.get("flow") or t["none"])),
        (t["status"], str(meta.get("status", "manual"))),
        (t["verified"], str(meta.get("verified") or t["none"])),
        (t["iso"], iso_name),
        (t["memory"], f"{vm.get('memory_mb', '?')} MB"),
        (t["cpus"], str(vm.get("cpus", "?"))),
        (t["disk"], f"{disk.get('size', '?')} {disk.get('format', '')} ({disk.get('interface', 'virtio')})".strip()),
        (t["firmware"], str((vm.get("firmware") or {}).get("type", "?"))),
        (t["machine"], str(vm.get("machine", "?"))),
        (t["ssh"], str(ssh_cfg.get("ssh_host_port") or t["none"])),
    ]
    checks = verification_commands(vm)
    if checks:
        facts.append((t["checks"], "<br>".join(f"<code>{escape(c)}</code>" for c in checks)))
    return facts


def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def phase_label(phase: str, lang: str) -> str:
    if phase.startswith("bootstrap-") or phase.startswith("install"):
        return LABELS[lang]["phase_install"]
    return LABELS[lang].get(f"phase_{phase}", phase)


def elapsed_text(seconds: int, lang: str) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{LABELS[lang]['elapsed']} {minutes:d}:{secs:02d}"


def render_markdown(result: dict[str, Any], vm: dict[str, Any], directory: Path, lang: str) -> str:
    """The profile sheet as Markdown (raw HTML for the table and figures, which weasyprint prints well)."""
    t = LABELS[lang]
    vm_id = str(result["id"])
    status = str(result.get("status", "WARN"))
    minutes = (float(result.get("seconds") or 0.0)) / 60.0
    lines = [f"# {escape(str(result.get('name') or vm_id))}",
             f'<p class="badge"><code>{escape(vm_id)}</code> · {t["title"]} · <span class="{status}">{status}</span> · {minutes:.1f} {t["minutes"]}</p>',
             "", f"## {t['facts']}", "", "<table>"]
    for label, value in profile_facts(vm, result, lang):
        lines.append(f"<tr><th>{escape(label)}</th><td>{value if '<code>' in value else escape(value)}</td></tr>")
    lines += ["</table>", "", f"## {t['outcome']}", "", "<table>",
              f'<tr><th>{t["result"]}</th><td><span class="{status}">{status}</span></td></tr>',
              f"<tr><th>{t['duration']}</th><td>{minutes:.1f} {t['minutes']}</td></tr>",
              f"<tr><th>{t['phase']}</th><td>{escape(phase_label(str(result.get('phase') or 'validation'), lang))}</td></tr>",
              f"<tr><th>{t['detail']}</th><td>{escape(str(result.get('detail') or t['none']))}</td></tr>",
              "</table>", "", f"## {t['timeline']}", ""]
    frames = [f for f in (result.get("timeline") or []) if isinstance(f, dict) and (f.get("file") or f.get("text"))]
    if not frames:
        frames = report.load_timeline(directory, vm_id)
    if not frames:
        lines.append(t["no_frames"])
    for frame in frames:
        caption = f"{elapsed_text(int(frame.get('elapsed', 0)), lang)} · {phase_label(str(frame.get('phase', 'install')), lang)}"
        if frame.get("file"):
            lines.append(f'<figure><img src="{escape(str(frame["file"]))}" alt="{escape(caption)}"><figcaption>{escape(caption)}</figcaption></figure>')
            continue
        # A text frame: the serial console while the framebuffer was black.
        try:
            text = (directory / str(frame["text"])).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines.append(f'<figure><pre>{escape(text.rstrip())}</pre><figcaption>{escape(caption)} · {t["console"]}</figcaption></figure>')
    if len(frames) >= report.TIMELINE_MAX_FRAMES:
        lines.append(t["truncated"].format(max=report.TIMELINE_MAX_FRAMES))
    screenshot = result.get("screenshot")
    if screenshot:
        lines += ["", f"## {t['final']}", "", f'<figure><img src="{escape(str(screenshot))}" alt="{t["final"]}"><figcaption>{t["final"]}</figcaption></figure>']
    lines += ["", f"<footer>{t['generated']} {date.today().isoformat()} {t['from_report']} <code>{escape(directory.name)}</code> · qemu-iso-lab</footer>", ""]
    return "\n".join(lines)


def render_index_markdown(results: list[dict[str, Any]], directory: Path, lang: str) -> str:
    t = LABELS[lang]
    counts = {s: sum(1 for r in results if r.get("status") == s) for s in ("PASS", "FAIL", "WARN", "SKIP")}
    lines = [f"# {t['index_title']} — {escape(directory.name)}", "",
             f'<p class="badge">{len(results)} {t["profiles"]} · ' + " · ".join(f'<span class="{s}">{s} {n}</span>' for s, n in counts.items() if n) + "</p>", "",
             "<table>", f"<tr><th>{t['facts']}</th><th>{t['flow']}</th><th>{t['result']}</th><th>{t['duration']}</th></tr>"]
    for r in results:
        minutes = (float(r.get("seconds") or 0.0)) / 60.0
        lines.append(f'<tr><td><a href="{escape(str(r["id"]))}.pdf">{escape(str(r.get("name") or r["id"]))}</a><br><code>{escape(str(r["id"]))}</code></td>'
                     f'<td>{escape(str(r.get("flow") or t["none"]))}</td><td><span class="{r.get("status", "WARN")}">{r.get("status", "WARN")}</span></td><td>{minutes:.1f} {t["minutes"]}</td></tr>')
    lines += ["</table>", "", f"<footer>{t['generated']} {date.today().isoformat()} · qemu-iso-lab</footer>", ""]
    return "\n".join(lines)


def to_pdf(markdown_text: str, base_url: Path, destination: Path) -> None:
    """Markdown → HTML → PDF; the two packages are optional, as for `make guides`."""
    try:
        import markdown  # type: ignore[import-untyped]
        from weasyprint import HTML  # type: ignore[import-untyped]
    except ImportError as exc:
        raise VMError(f"report-pdf needs the '{exc.name}' package: pip install --user markdown weasyprint") from exc
    body = markdown.markdown(markdown_text, extensions=["tables"])
    page = f"<!doctype html><html><head><meta charset='utf-8'><style>{PRINT_CSS}</style></head><body>{body}</body></html>"
    runtime.ensure_parent(destination)
    HTML(string=page, base_url=str(base_url)).write_pdf(str(destination))


def build(directory: Path, langs: tuple[str, ...] = LANGS, dry_run: bool = False) -> list[Path]:
    """Write `pdf/<lang>/<vm>.md` + `.pdf` for every result and an index per language."""
    unknown = [lang for lang in langs if lang not in LABELS]
    if unknown:
        raise VMError(f"Unknown language(s) {', '.join(unknown)}; choose from {', '.join(LABELS)}")
    results = load_results(directory)
    cfg = config.load_config()
    written: list[Path] = []
    for lang in langs:
        out = directory / "pdf" / lang
        for result in results:
            vm_id = str(result["id"])
            try:
                vm = config.get_vm(cfg, vm_id)
            except VMError:
                vm = {"name": result.get("name", vm_id)}  # a profile that has since been removed or renamed
            text = render_markdown(result, vm, directory, lang)
            md_path, pdf_path = out / f"{vm_id}.md", out / f"{vm_id}.pdf"
            ui.print_note(f"{lang}: {ui.pretty_path(pdf_path)}")
            if not dry_run:
                runtime.ensure_parent(md_path)
                md_path.write_text(text, encoding="utf-8")
                to_pdf(text, directory, pdf_path)
            written += [md_path, pdf_path]
        index_text = render_index_markdown(results, directory, lang)
        md_path, pdf_path = out / "index.md", out / "index.pdf"
        if not dry_run:
            runtime.ensure_parent(md_path)
            md_path.write_text(index_text, encoding="utf-8")
            to_pdf(index_text, directory, pdf_path)
        written += [md_path, pdf_path]
    return written
