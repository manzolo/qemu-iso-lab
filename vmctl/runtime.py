"""Process execution, filesystem helpers, and utility functions."""
from __future__ import annotations

import json
import os
import pty
import shlex
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from vmctl import state, ui
from vmctl.errors import VMError


def load_json_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise VMError(f"Invalid config file: {path}")
    return data


def run_output(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True)


def find_free_tcp_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def image_info(path: Path, quiet: bool = False) -> dict[str, Any]:
    cmd = ["qemu-img", "info", "--output=json", str(path)]
    if quiet:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd, output=result.stdout, stderr=result.stderr)
        output = result.stdout
    else:
        output = run_output(cmd)
    payload = json.loads(output)
    if not isinstance(payload, dict):
        raise VMError(f"Unexpected qemu-img info output for: {path}")
    return payload


def resolve_path(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else state.ROOT / path


def expand_host_path(path_str: str) -> Path:
    return Path(path_str).expanduser()


def require_command(name: str) -> None:
    if shutil.which(name) is None:
        raise VMError(f"Missing command: {name}")


def confirm_default_no(prompt: str) -> bool:
    """Never read a pipe or treat missing input as consent."""
    if not sys.stdin.isatty():
        return False
    try:
        answer = input(f"{prompt} [y/N] ").strip().casefold()
    except (EOFError, OSError, KeyboardInterrupt):
        return False
    return answer in {"s", "si", "sì", "y", "yes"}


def _stream_pipe(pipe: Any, stream: Any, log_fh: Any) -> None:
    try:
        for chunk in iter(pipe.readline, ""):
            if not chunk:
                break
            if stream is not None:
                stream.write(chunk)
                stream.flush()
            if log_fh is not None:
                log_fh.write(chunk)
                log_fh.flush()
    finally:
        pipe.close()


TERMINATE_GRACE_SEC = 15
# Same budget `qemu.run_and_expect` gives its own "Captured output:" tail.
TIMEOUT_TAIL_CHARS = 4000


def log_tail(log: Path | None, limit: int = TIMEOUT_TAIL_CHARS) -> str:
    """The last *limit* readable characters of *log*, or "" when there is nothing to read.

    A serial console log is written by the guest: it carries NUL padding (`tr -d '\000'` is
    how you read one by hand) and is not necessarily valid UTF-8, so nothing here may raise.
    """
    if log is None:
        return ""
    try:
        with log.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            # Read well past the budget: NULs and multi-byte sequences shrink on decoding.
            handle.seek(max(0, size - 16 * limit))
            data = handle.read()
    except OSError:
        return ""
    return data.replace(b"\0", b"").decode("utf-8", "replace")[-limit:]


def _timed_out(cmd: list[str], timeout_sec: float | None, log: Path | None) -> VMError:
    """The message a stuck install must leave behind: how long, what did not exit, and the
    console itself — not just its path. `check-vms --restore` deletes the row's artifacts as
    soon as it ends, so a path alone points at a file that no longer exists by the time
    anyone reads the report (2026-09-21)."""
    where = f" Output: {log}" if log is not None else ""
    tail = log_tail(log)
    captured = f"\nCaptured output:\n{tail}" if tail.strip() else ""
    return VMError(f"Timed out after {int(timeout_sec or 0)}s: {Path(cmd[0]).name} did not exit "
                   f"(the guest never powered itself off).{where}{captured}")


def run(
    cmd: list[str],
    dry_run: bool = False,
    quiet: bool = False,
    stdout_log: Path | None = None,
    stderr_log: Path | None = None,
    append: bool = False,
    stdin_text: str | None = None,
    show_command: bool = True,
    capture_error_output: bool = False,
    timeout_sec: float | None = None,
) -> None:
    """Run *cmd* to completion. ``timeout_sec`` bounds the wait and is what the unattended
    install phases pass: a guest that never powers itself off would otherwise hold the
    caller forever (an Ubuntu 20.04 autoinstall spinning in subiquity's network loop held a
    full check-vms run for five hours on 2026-09-20, because this wait had no bound while
    `qemu.run_and_expect` — used by every other flow — always had one)."""
    if show_command:
        ui.print_command(cmd)
    if not dry_run:
        if quiet and stdout_log is None and stderr_log is None:
            try:
                subprocess.run(
                    cmd,
                    check=True,
                    input=stdin_text,
                    text=stdin_text is not None or capture_error_output,
                    stdout=subprocess.PIPE if capture_error_output else subprocess.DEVNULL,
                    stderr=subprocess.PIPE if capture_error_output else subprocess.DEVNULL,
                    timeout=timeout_sec,
                )
            except subprocess.TimeoutExpired as exc:
                raise _timed_out(cmd, timeout_sec, None) from exc
            return
        if stdout_log is None and stderr_log is None:
            try:
                subprocess.run(cmd, check=True, input=stdin_text, text=stdin_text is not None, timeout=timeout_sec)
            except subprocess.TimeoutExpired as exc:
                raise _timed_out(cmd, timeout_sec, None) from exc
            return

        stdout_fh = None
        stderr_fh = None
        mode = "a" if append else "w"
        try:
            if stdout_log is not None:
                ensure_parent(stdout_log)
                stdout_fh = stdout_log.open(mode, encoding="utf-8")
            if stderr_log is not None:
                ensure_parent(stderr_log)
                stderr_fh = stderr_log.open(mode, encoding="utf-8")

            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE if stdin_text is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            assert process.stdout is not None
            assert process.stderr is not None

            stdout_stream = None if quiet else sys.stdout
            stderr_stream = None if quiet else sys.stderr
            stdout_thread = threading.Thread(
                target=_stream_pipe,
                args=(process.stdout, stdout_stream, stdout_fh),
                daemon=True,
            )
            stderr_thread = threading.Thread(
                target=_stream_pipe,
                args=(process.stderr, stderr_stream, stderr_fh),
                daemon=True,
            )
            stdout_thread.start()
            stderr_thread.start()
            if stdin_text is not None:
                assert process.stdin is not None
                process.stdin.write(stdin_text)
                process.stdin.close()
            try:
                returncode = process.wait(timeout=timeout_sec)
            except subprocess.TimeoutExpired as exc:
                # SIGTERM first: QEMU closes the qcow2 on it, SIGKILL would not.
                process.terminate()
                try:
                    process.wait(timeout=TERMINATE_GRACE_SEC)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                stdout_thread.join(timeout=5)
                stderr_thread.join(timeout=5)
                raise _timed_out(cmd, timeout_sec, stdout_log) from exc
            stdout_thread.join()
            stderr_thread.join()
            if returncode != 0:
                raise subprocess.CalledProcessError(returncode, cmd)
        finally:
            if stdout_fh is not None:
                stdout_fh.close()
            if stderr_fh is not None:
                stderr_fh.close()


def reread_partition_table(device: str, dry_run: bool = False) -> None:
    try:
        run(["blockdev", "--rereadpt", device], dry_run=dry_run, quiet=True)
    except subprocess.CalledProcessError:
        ui.print_status(
            "warn",
            f"Kernel did not reread the partition table for {device}; continuing anyway",
            ok=False,
        )


def run_background(cmd: list[str], log_path: Path, dry_run: bool = False, stderr_path: Path | None = None) -> int | None:
    ui.print_command(cmd)
    ui.print_kv("log", ui.pretty_path(log_path))
    if stderr_path is not None:
        ui.print_kv("stderr", ui.pretty_path(stderr_path))
    if dry_run:
        return None

    ensure_parent(log_path)
    if stderr_path is None:
        with log_path.open("ab") as fh:
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        return process.pid

    ensure_parent(stderr_path)
    with log_path.open("ab") as stdout_fh, stderr_path.open("ab") as stderr_fh:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=stdout_fh,
            stderr=stderr_fh,
            start_new_session=True,
        )
    return process.pid


def run_progress(cmd: list[str], dry_run: bool = False) -> None:
    ui.print_command(cmd)
    if dry_run:
        return
    if not getattr(sys.stdout, "isatty", lambda: False)():
        subprocess.run(cmd, check=True)
        return

    master_fd, slave_fd = pty.openpty()
    try:
        process = subprocess.Popen(
            cmd,
            stdin=None,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
        )
    finally:
        os.close(slave_fd)

    try:
        while True:
            try:
                chunk = os.read(master_fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            os.write(sys.stdout.fileno(), chunk)
        returncode = process.wait()
        if returncode != 0:
            raise subprocess.CalledProcessError(returncode, cmd)
    finally:
        os.close(master_fd)


def run_pipeline(commands: list[list[str]], dry_run: bool = False) -> None:
    print(f"{ui.style('$', ui.BOLD, ui.BLUE)} {' | '.join(shell_join(cmd) for cmd in commands)}")
    if dry_run:
        return

    processes = []
    prev_stdout = None
    try:
        for index, cmd in enumerate(commands):
            process = subprocess.Popen(
                cmd,
                stdin=prev_stdout,
                stdout=subprocess.PIPE if index < len(commands) - 1 else None,
            )
            if prev_stdout is not None:
                prev_stdout.close()
            prev_stdout = process.stdout
            processes.append(process)

        for process in processes:
            returncode = process.wait()
            if returncode != 0:
                raise subprocess.CalledProcessError(returncode, process.args)
    finally:
        if prev_stdout is not None:
            prev_stdout.close()


def ensure_parent(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise VMError(f"Unable to create parent directory for {path}: {exc}") from exc


def shell_join(cmd: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in cmd)


def format_bytes(size: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def round_up(value: int, alignment: int) -> int:
    if value <= 0:
        return alignment
    return ((value + alignment - 1) // alignment) * alignment


def round_up_div(value: int, divisor: int) -> int:
    if value <= 0:
        return 0
    return (value + divisor - 1) // divisor


def ensure_vm_dirs(name: str) -> Path:
    base = state.ROOT / "artifacts" / name
    try:
        (base / "logs").mkdir(parents=True, exist_ok=True)
        (base / "runtime").mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise VMError(f"Unable to prepare artifact directories for '{name}': {exc}") from exc
    return base


def vm_artifact_base(name: str) -> Path:
    return state.ROOT / "artifacts" / name
