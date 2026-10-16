"""Launch timestamps, independent of disks, checkpoints and browser sessions."""
from __future__ import annotations

import os
import re
import tempfile
import time
from pathlib import Path

from vmctl import state


def command_vm(cmd: list[str]) -> str | None:
    if not cmd or not Path(cmd[0]).name.startswith("qemu-system-"):
        return None
    try:
        label = cmd[cmd.index("-name") + 1]
    except (ValueError, IndexError):
        return None
    if not label.startswith("vmctl:"):
        return None
    name = label.removeprefix("vmctl:")
    return name if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) else None


def record(cmd: list[str]) -> None:
    """Best effort: history must never interrupt a running guest."""
    name = command_vm(cmd)
    if name is None:
        return
    directory = state.ROOT / "artifacts" / ".recent"
    temporary = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(str(time.time_ns()))
        os.replace(temporary, directory / name)
    except OSError:
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def last_started(name: str) -> float | None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        return None
    try:
        stamp = int((state.ROOT / "artifacts" / ".recent" / name).read_text())
        return stamp / 1_000_000_000 if 0 < stamp < 10**20 else None
    except (OSError, ValueError):
        return None
