"""Process execution, filesystem helpers, and utility functions."""
from __future__ import annotations

import json
import os
import pty
import shlex
import shutil
import subprocess
import sys
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


def run(cmd: list[str], dry_run: bool = False, quiet: bool = False) -> None:
    ui.print_command(cmd)
    if not dry_run:
        if quiet:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run(cmd, check=True)


def reread_partition_table(device: str, dry_run: bool = False) -> None:
    try:
        run(["blockdev", "--rereadpt", device], dry_run=dry_run, quiet=True)
    except subprocess.CalledProcessError:
        ui.print_status(
            "warn",
            f"Kernel did not reread the partition table for {device}; continuing anyway",
            ok=False,
        )


def run_background(cmd: list[str], log_path: Path, dry_run: bool = False) -> int | None:
    ui.print_command(cmd)
    ui.print_kv("log", ui.pretty_path(log_path))
    if dry_run:
        return None

    ensure_parent(log_path)
    with log_path.open("ab") as fh:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=fh,
            stderr=subprocess.STDOUT,
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
