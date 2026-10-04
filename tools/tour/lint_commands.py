#!/usr/bin/env python3
"""Commands a viewer types or a lesson replays, checked for what breaks them in an interactive shell.

The lab tests run their commands non-interactively, so they never see these; a lesson recording (an
interactive bash in the studio) and a reader copying from a guide do:

- ``!`` inside double quotes: bash's history expansion ("event not found"; mysql-lab's Reader123!,
  2026-10-04).
- a command that stops to ask: ``mdadm --create`` without ``--run``, ``apt``/``apt-get install``
  without ``-y``, ``mkfs`` over a disk that held a file system without ``-F``/``-f`` (mdadm-lab's RAID5,
  2026-10-04), ``adduser`` and ``passwd`` (they ask for answers).

Sources: the lesson clips (tools/tour/clips/*.mjs: the strings given to d.run/d.guest), the labs'
lab.json commands and the ```sh blocks of their guides. Usage: tools/tour/lint_commands.py [paths];
exit 1 when something is found. tests/test_tour_lint.py runs it over the repository.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# (pattern, unless this also matches, message)
PROMPTS = [
    (re.compile(r"\bmdadm\b[^;&|]*--create\b"), re.compile(r"--run\b"), "mdadm --create asks to continue without --run"),
    (re.compile(r"\bapt(?:-get)?\s+(?:install|remove|purge|upgrade|full-upgrade)\b"), re.compile(r"(?:^|\s)-\w*y\b|--yes\b|--assume-yes\b"),
     "apt asks for confirmation without -y"),
    (re.compile(r"\bmkfs(?:\.\w+)?\s"), re.compile(r"\s-[a-zA-Z]*[Ff]\b|mkfs\.(?:vfat|fat|msdos)\b"),
     "mkfs asks before overwriting an existing file system without -F (or -f)"),
    (re.compile(r"(?:^|[;&|]\s*|sudo\s+)adduser\b"), re.compile(r"--disabled-password\b"), "adduser asks for a password and details"),
    (re.compile(r"(?:^|[;&|]\s*|sudo\s+)passwd\b(?!\s*-)"), re.compile(r"chpasswd"), "passwd asks for the password"),
]


def double_quoted(command: str) -> list[str]:
    """The double-quoted pieces bash would expand, outside single quotes."""
    pieces, i, n = [], 0, len(command)
    while i < n:
        c = command[i]
        if c == "'":
            j = command.find("'", i + 1)
            i = n if j < 0 else j + 1
        elif c == '"':
            j = i + 1
            while j < n and command[j] != '"':
                j += 2 if command[j] == "\\" else 1
            pieces.append(command[i + 1:j])
            i = j + 1
        elif c == "\\":
            i += 2
        else:
            i += 1
    return pieces


def problems(command: str) -> list[str]:
    found = []
    code = command.split("    #", 1)[0]  # the lessons' trailing comments are not typed as code
    if any(re.search(r"!(?=[^\s=)\"]|$)", piece) for piece in double_quoted(code)):
        found.append('"!" inside double quotes: history expansion in an interactive bash')
    for pattern, unless, message in PROMPTS:
        if pattern.search(code) and not unless.search(code):
            found.append(message)
    return found


def clip_commands(path: Path) -> list[tuple[int, str]]:
    """The JavaScript string literals passed to d.run / d.guest, unescaped the way JS would."""
    out = []
    text = path.read_text(encoding="utf-8")
    # d.run(...), d.guest(...), and a clip's own wrapper guest(d, ...) (lab-lvm).
    for m in re.finditer(r"(?:d\.(?:run|guest)\(|\bguest\(\s*d\s*,)\s*(\"(?:[^\"\\]|\\.)*\"|`(?:[^`\\]|\\.)*`)", text):
        literal = m.group(1)
        body = literal[1:-1]
        body = re.sub(r"\\(.)", r"\1", body)
        body = re.sub(r"\$\{[^}]*\}", "X", body) if literal.startswith("`") else body
        out.append((text.count("\n", 0, m.start()) + 1, body))
    return out


def guide_commands(path: Path) -> list[tuple[int, str]]:
    out, inside = [], False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("```"):
            inside = line.strip() in ("```sh", "```bash", "```shell") if not inside else False
            continue
        if inside and line.strip() and not line.lstrip().startswith("#"):
            out.append((number, line))
    return out


def lab_commands(path: Path) -> list[tuple[int, str]]:
    lab = json.loads(path.read_text(encoding="utf-8"))
    return [(0, command) for exercise in lab.get("exercises", []) for block in exercise.get("blocks", [])
            for command in block.get("commands", [])]


def sources(paths: list[Path]) -> list[tuple[Path, list[tuple[int, str]]]]:
    found = []
    for path in paths:
        if path.suffix == ".mjs":
            found.append((path, clip_commands(path)))
        elif path.name == "lab.json":
            found.append((path, lab_commands(path)))
        elif path.suffix == ".md":
            found.append((path, guide_commands(path)))
    return found


def default_paths() -> list[Path]:
    return sorted([*(ROOT / "tools/tour/clips").glob("*.mjs"), *(ROOT / "vms/labs").glob("*/lab.json"),
                   *(ROOT / "vms/labs").glob("*/guide.*.md")])


def lint(paths: list[Path]) -> list[str]:
    report = []
    for path, commands in sources(paths):
        for number, command in commands:
            for message in problems(command):
                where = f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}" + (f":{number}" if number else "")
                report.append(f"{where}: {message}\n    {command.strip()[:160]}")
    return report


def main() -> int:
    paths = [Path(p).resolve() for p in sys.argv[1:]] or default_paths()
    report = lint(paths)
    print("\n".join(report) if report else f"{len(paths)} files: no command that breaks in an interactive shell")
    return 1 if report else 0


if __name__ == "__main__":
    sys.exit(main())
