"""Detached TUI commands; VM operations remain in the vmctl CLI."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import shlex
import subprocess
import sys


def job_dir(root: Path, name: str) -> Path:
    if not name or name in (".", "..") or Path(name).name != name:
        raise ValueError("Invalid VM name")
    return root / "artifacts" / name / "runtime" / "tui-job"


def status(directory: Path) -> str:
    try:
        with (directory / "lock").open("rb") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return "running"
            result = (directory / "status").read_text().strip()
            return "interrupted" if result == "running" else result
    except FileNotFoundError:
        return ""


def start(root: Path, name: str, command: list[str]) -> Path:
    directory = job_dir(root, name)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f"An installation is already running for '{name}'") from None
        log_path = directory / "output.log"
        with log_path.open("w") as log:
            log.write(f"$ {shlex.join(command)}\n\n")
            log.flush()
            (directory / "status").write_text("running\n")
            try:
                subprocess.Popen(
                    [sys.executable, "-u", str(Path(__file__).resolve()),
                     str(directory), str(lock.fileno()), *command],
                    cwd=root, stdin=subprocess.DEVNULL, stdout=log,
                    stderr=subprocess.STDOUT, start_new_session=True,
                    pass_fds=(lock.fileno(),),
                    env={**os.environ, "PYTHONUNBUFFERED": "1"},
                )
            except OSError:
                (directory / "status").write_text("failed to start\n")
                raise
    return log_path


def worker(directory: Path, lock_fd: int, command: list[str]) -> int:
    code = 1
    try:
        # Keep the lock in the CLI too, so a lost supervisor cannot permit
        # a second install while the first CLI is still working.
        code = subprocess.run(command, pass_fds=(lock_fd,), check=False).returncode
    except Exception as exc:
        print(f"Unable to run command: {exc}", flush=True)
    finally:
        result = "completed" if code == 0 else f"failed ({code})"
        print(f"\nCommand {result}.", flush=True)
        (directory / "status").write_text(result + "\n")
        os.close(lock_fd)
    return code


if __name__ == "__main__":
    sys.exit(worker(Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3:]))
