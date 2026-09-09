"""Detached TUI commands; VM operations remain in the vmctl CLI."""
from __future__ import annotations

import fcntl
from contextlib import contextmanager
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator


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


@contextmanager
def control_lock(directory: Path) -> Iterator[None]:
    """Serialize launch and cancellation, including VM cleanup after the worker exits."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "control.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("An installation operation is already in progress") from None
        yield


def start(root: Path, name: str, command: list[str]) -> Path:
    directory = job_dir(root, name)
    with control_lock(directory):
        return _start(root, name, directory, command)


def _start(root: Path, name: str, directory: Path, command: list[str]) -> Path:
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


def worker_group(directory: Path) -> int | None:
    """Find the actual session leader, including jobs launched before cancellation existed."""
    script = str(Path(__file__).resolve())
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            argv = (entry / "cmdline").read_bytes().decode().rstrip("\0").split("\0")
            if len(argv) < 6 or argv[1:3] != ["-u", script]:
                continue
            if Path(argv[3]).resolve() != directory.resolve():
                continue
            pid = int(entry.name)
            if os.getpgid(pid) == pid and os.getsid(pid) == pid:
                return pid
        except (OSError, UnicodeError):
            continue
    return None


def group_alive(pgid: int) -> bool:
    # killpg(..., 0) also reports groups consisting solely of unreaped zombies.
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) == pgid and fields[0] not in ("Z", "X"):
                return True
        except (OSError, ValueError, IndexError):
            continue
    return False


def cancel(root: Path, name: str, stop_vm: Callable[[], None], grace_sec: float = 5) -> bool:
    directory = job_dir(root, name)
    if status(directory) != "running":
        return False
    with control_lock(directory):
        if status(directory) != "running":
            return False
        pgid = worker_group(directory)
        if pgid is None:
            if status(directory) != "running":
                return False
            raise RuntimeError("Cannot identify the running installation supervisor; no processes were stopped")
        for sig, timeout in ((signal.SIGTERM, grace_sec), (signal.SIGKILL, 5)):
            try:
                os.killpg(pgid, sig)
            except ProcessLookupError:
                break
            deadline = time.monotonic() + timeout
            while group_alive(pgid) and time.monotonic() < deadline:
                time.sleep(0.05)
            if not group_alive(pgid):
                break
        else:
            raise RuntimeError("Installation processes did not stop")

        # Post-install QEMU can have its own session. Stop it only after the CLI
        # cannot advance to another phase or launch a replacement VM.
        with (directory / "lock").open("a+b") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Installation lock is still held; cancellation is incomplete") from None
            stop_vm()
            (directory / "status").write_text("cancelled\n")
            with (directory / "output.log").open("a") as log:
                log.write("\nInstallation cancelled by user. Disk and logs preserved.\n")
        return True


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
