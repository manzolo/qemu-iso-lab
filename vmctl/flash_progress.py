"""Readable flash progress with persistent, unabridged command diagnostics."""
from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from vmctl import ui
from vmctl.errors import VMError


def percentage(output: str) -> float | None:
    matches = re.findall(
        r"(?:\((\d+(?:\.\d+)?)/100%\)|(?:Completed|pct rescued):\s*(\d+(?:\.\d+)?)%)",
        output, re.IGNORECASE,
    )
    if not matches:
        return None
    return min(100.0, max(0.0, float(next(value for value in matches[-1] if value))))


def stop_process(process: "subprocess.Popen[bytes]") -> None:
    """Reap the entire tool group before temporary images/loop views are removed."""
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:
            continue
    process.wait()


class FlashProgress:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="flash-", suffix=".log", dir=directory)
        self.log_path = Path(name)
        os.close(fd)
        # sudo creates the file; the caller must be able to read diagnostics later.
        from vmctl.flash import maybe_restore_sudo_owner

        maybe_restore_sudo_owner(self.log_path)
        maybe_restore_sudo_owner(directory)
        self.tty = sys.stdout.isatty()
        ui.print_kv("Details", ui.pretty_path(self.log_path))

    def stage(self, number: int, title: str) -> None:
        print()
        ui.print_header(f"{number}/4  {title}")
        with self.log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n== {number}/4 {title} ==\n")

    def _draw(self, title: str, progress: float | None, elapsed: float) -> None:
        width = max(20, shutil.get_terminal_size((80, 24)).columns - 1)
        clock = f"{int(elapsed) // 60:02d}:{int(elapsed) % 60:02d}"
        if progress is None:
            spinner = "|/-\\"[int(elapsed * 4) % 4]
            indicator = f"{spinner} working"
        else:
            filled = int(progress / 100 * 16)
            indicator = f"[{'#' * filled}{'-' * (16 - filled)}] {progress:5.1f}%"
        suffix = f"  {indicator}  {clock}"
        label = title[:max(1, width - len(suffix) - 2)]
        print(f"\r\033[2K  {label}{suffix}", end="", flush=True)

    def run(self, cmd: list[str], *, title: str) -> None:
        started = time.monotonic()
        if not self.tty:
            print(f"  ... {title}", flush=True)
        tail = ""
        progress = None
        try:
            with self.log_path.open("ab", buffering=0) as log:
                log.write(("\n$ " + shlex.join(cmd) + "\n").encode())
                offset = log.tell()
                process = subprocess.Popen(
                    cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    env={**os.environ, "LC_ALL": "C"}, start_new_session=True,
                )
                try:
                    with self.log_path.open("rb") as reader:
                        while True:
                            try:
                                process.wait(timeout=0.2)
                            except subprocess.TimeoutExpired:
                                pass
                            end = os.fstat(reader.fileno()).st_size
                            reader.seek(max(offset, end - 8192))
                            tail = reader.read().decode("utf-8", errors="replace")
                            current = percentage(tail)
                            if current is not None:
                                progress = current
                            if self.tty:
                                self._draw(title, progress, time.monotonic() - started)
                            if process.poll() is not None:
                                break
                except BaseException:
                    stop_process(process)
                    raise
                log.write(f"\nExit code: {process.returncode}\n".encode())
                if process.returncode:
                    # Keep the original status and captured output for existing callers.
                    raise subprocess.CalledProcessError(process.returncode, cmd, output=tail)
        except BaseException as exc:
            if self.tty:
                print("\r\033[2K", end="")
            ui.print_status("failed", title, ok=False)
            if tail:
                # Render plain diagnostics; subprocess cursor/colour codes stay in the log.
                plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", tail)
                for line in plain.splitlines()[-8:]:
                    print("    " + "".join(char for char in line if char.isprintable()))
            ui.print_kv("Details", ui.pretty_path(self.log_path))
            if isinstance(exc, KeyboardInterrupt):
                raise VMError(f"{title} interrupted; the command has stopped. See {self.log_path}") from exc
            raise
        if self.tty:
            print("\r\033[2K", end="")
        ui.print_status("ok", f"{title} ({time.monotonic() - started:.1f}s)")

    def preserve_logs(self, directory: Path) -> None:
        with self.log_path.open("ab") as output:
            for path in sorted(directory.glob("*.log")):
                output.write(f"\n--- {path.name} ---\n".encode())
                with path.open("rb") as source:
                    shutil.copyfileobj(source, output)
