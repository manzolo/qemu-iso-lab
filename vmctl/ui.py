"""ANSI terminal styling and print helpers."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from vmctl import state

USE_COLOR = (
    os.environ.get("NO_COLOR") != "1"
    and os.environ.get("TERM", "") not in ("", "dumb")
    and getattr(sys.stdout, "isatty", lambda: False)() is True
)
RESET = "\033[0m" if USE_COLOR else ""
BOLD = "\033[1m" if USE_COLOR else ""
BLUE = "\033[34m" if USE_COLOR else ""
CYAN = "\033[36m" if USE_COLOR else ""
GREEN = "\033[32m" if USE_COLOR else ""
YELLOW = "\033[33m" if USE_COLOR else ""
RED = "\033[31m" if USE_COLOR else ""


def style(text: str, *codes: str) -> str:
    if not USE_COLOR:
        return text
    return "".join(codes) + text + RESET


def print_header(title: str) -> None:
    print(f"{style('==>', BOLD, BLUE)} {style(title, BOLD)}")


def print_kv(label: str, value: str) -> None:
    print(f"  {style(f'{label:<10}', CYAN)} {value}")


def print_status(marker: str, text: str, ok: bool = True) -> None:
    color = GREEN if ok else YELLOW
    print(f"  [{style(marker, color, BOLD)}] {text}")


def print_command(cmd: list[str]) -> None:
    print(f"{style('$', BOLD, BLUE)} {' '.join(cmd)}")


def print_note(text: str) -> None:
    print(f"{style('::', BOLD, CYAN)} {text}")


def pretty_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(state.ROOT))
    except ValueError:
        return str(path)


def pretty_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    tail = parsed.path.rsplit("/", 1)[-1]
    if tail:
        return f"{parsed.netloc}/{tail}"
    return parsed.netloc
