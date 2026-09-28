"""Read-only connection inspection and bounded guest commands over existing transports."""
from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import os
from pathlib import Path
import selectors
import shlex
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from vmctl import config, guest_agent, ssh, state, web_files
from vmctl.errors import VMError

PROBE_TIMEOUT = 3.0
COMMAND_TIMEOUT = 10.0
MAX_OUTPUT = 128 * 1024  # combined stdout/stderr, per diagnostic command
MAX_SERIAL_LOGS = 4


def bounded_run(command: list[str], timeout: float, limit: int = MAX_OUTPUT) -> dict[str, Any]:
    """Drain both pipes without unbounded communicate() buffers or temporary output files."""
    output: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    reason = ""
    deadline = time.monotonic() + timeout
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE) as process:
        assert process.stdout is not None and process.stderr is not None
        try:
            with selectors.DefaultSelector() as selector:
                for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
                    os.set_blocking(pipe.fileno(), False)
                    selector.register(pipe, selectors.EVENT_READ, name)
                total = 0
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        reason = "timeout"
                        break
                    for key, _ in selector.select(remaining):
                        chunk = os.read(key.fd, min(65536, limit - total + 1))
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        allowed = min(len(chunk), limit - total)
                        output[key.data].extend(chunk[:allowed])
                        total += allowed
                        if len(chunk) > allowed:
                            reason = "output limit"
                            break
                    if reason:
                        break
                if not reason:
                    try:
                        process.wait(timeout=max(0.001, deadline - time.monotonic()))
                    except subprocess.TimeoutExpired:
                        reason = "timeout"
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
    return {**{key: value.decode("utf-8", errors="replace") for key, value in output.items()},
            "returncode": process.returncode, "stopped": reason}


def remote(vm: dict[str, Any], command: str, timeout: float = COMMAND_TIMEOUT,
           limit: int = MAX_OUTPUT) -> dict[str, Any]:
    base = ssh.ssh_base_cmd(vm, read_only=True)
    # Options precede profile options: OpenSSH uses the first value it sees.
    base[1:1] = ["-o", "ConnectTimeout=3", "-o", "ConnectionAttempts=1", "-o", "BatchMode=yes"]
    return bounded_run([*base[:-1], "--", base[-1], command], timeout, limit)


def connections(vm_name: str, console: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
    vm = config.get_vm(config.load_config(), vm_name)
    info = console(vm_name)
    def result(status: str, detail: str) -> dict[str, str]:
        return {"status": status, "detail": detail}

    checks = {"console": result("available" if info["running"] else "stopped",
                                "VM process is running; the console footer shows the live connection."
                                if info["running"] else "VM is stopped."),
              "clipboard": result("unknown", "Clipboard sharing requires a guest desktop agent; channel presence alone is not proof.")}
    if info["clipboard_channel"] is False:
        checks["clipboard"] = result("unavailable", "Clipboard channel is absent. Enable clipboard and restart the VM.")
    if not info["running"]:
        for key in ("ssh", "agent", "sftp", "clipboard"):
            checks[key] = result("stopped", "VM is stopped; no guest checks were made.")
        return {"checks": checks, "time": time.time()}
    try:
        # Same harmless login-shell probe as wait_for_ssh (exit 0 also works in cmd.exe).
        probe = remote(vm, "exit 0", PROBE_TIMEOUT, 4096)
        if probe["returncode"] == 0 and not probe["stopped"]:
            checks["ssh"] = result("available", "SSH authentication succeeded.")
        else:
            kind = ssh.classify_ssh_failure(probe["stderr"])
            hints = {"denied": "SSH key rejected. Check the guest authorized keys and the profile key.",
                     "negotiate": "Incompatible SSH algorithm. Check the profile legacy SSH options.",
                     "closed": "SSH port is closed. Check that the guest SSH service has started."}
            checks["ssh"] = result(kind or "unavailable", hints.get(kind, probe["stopped"] or probe["stderr"][:800] or "SSH failed."))
    except (VMError, OSError) as exc:
        checks["ssh"] = result("unavailable", str(exc))
    if checks["ssh"]["status"] == "available":
        try:
            with web_files.SFTP(vm, timeout=PROBE_TIMEOUT) as client:
                client.canonical(".")
            checks["sftp"] = result("available", "SFTP home directory is accessible.")
        except (VMError, OSError) as exc:
            checks["sftp"] = result("unavailable", str(exc))
    else:
        checks["sftp"] = result("unavailable", "Requires SSH key access; see SSH status.")
    if not guest_agent.enabled(vm):
        checks["agent"] = result("disabled", "Guest agent is disabled in the profile.")
    else:
        alive = guest_agent.responds(vm, timeout=PROBE_TIMEOUT)
        checks["agent"] = result("available" if alive else "unavailable",
                                 "Guest agent answers ping." if alive else "Guest agent did not answer; check its guest service and VM channel.")
    return {"checks": checks, "time": time.time()}


class ConnectionCache:
    """Lazy per-VM cache; concurrent requests share a probe, dashboard refreshes do no work."""

    def __init__(self, ttl: float = 5.0):
        self.ttl = ttl
        self.lock = threading.Lock()
        self.locks: dict[str, threading.Lock] = {}
        self.values: dict[str, tuple[float, dict[str, Any]]] = {}

    def get(self, name: str, console: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
        # Validate before creating cache entries, including arbitrary URL segments.
        config.get_vm(config.load_config(), name)
        with self.lock:
            lock = self.locks.setdefault(name, threading.Lock())
        with lock:
            taken, value = self.values.get(name, (0.0, {}))
            if not value or time.monotonic() - taken >= self.ttl:
                value = connections(name, console)
                self.values[name] = (time.monotonic(), value)
            return value


# Every diagnostic command is defined here. The browser supplies only the VM name.
# Detect the actual init system, not the distribution age (Debian 7 has no journalctl).
# The guest user of every tracked profile has NOPASSWD sudo: without it the installer's syslog,
# /var/log/syslog and the whole journal read as empty sections (verified: xubuntu-12.04, 2026-09-28).
DETECT = ("if test -d /run/systemd/system; then printf systemd; else printf sysv; fi; "
          "if command -v apt-cache >/dev/null 2>&1; then printf -- '-apt'; "
          "elif command -v dnf >/dev/null 2>&1; then printf -- '-dnf'; "
          "else printf -- '-generic'; fi; "
          "if sudo -n true >/dev/null 2>&1; then printf -- '+sudo'; fi")
POSIX_COMMANDS = (
    ("System", "uname -a; cat /etc/os-release /etc/debian_version /etc/redhat-release 2>/dev/null"),
    ("Network", "ip addr 2>/dev/null || ifconfig -a; ip route 2>/dev/null || netstat -rn; cat /etc/resolv.conf"),
    ("Storage", "df -h; mount"),
)
INIT_COMMANDS = {
    "systemd": (("Services", "systemctl --no-pager --failed; systemctl --no-pager status ssh sshd qemu-guest-agent"),
                ("System log", "journalctl --no-pager -n 200")),
    "sysv": (("Services", "ps -ef; service ssh status; service sshd status; service qemu-guest-agent status"),
             ("System log", "tail -n 200 /var/log/syslog /var/log/messages 2>/dev/null")),
}
PACKAGE_COMMANDS = {
    "apt": (("APT sources", "cat /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources 2>/dev/null"),
            ("APT policy", "apt-cache policy"),
            ("Installer / APT log", "tail -n 150 /var/log/installer/syslog /var/log/apt/term.log /var/log/dpkg.log 2>/dev/null")),
    # Read configuration/cache only: dnf itself may refresh metadata and write caches/logs.
    "dnf": (("DNF repositories", "cat /etc/yum.repos.d/*.repo 2>/dev/null"),
            ("DNF log", "tail -n 200 /var/log/dnf.log /var/log/dnf.rpm.log 2>/dev/null")),
    "generic": (),
}
WINDOWS_COMMANDS = (
    ("System", "cmd /c ver"), ("Network", "ipconfig /all"),
    ("Routes", "route print"), ("SSH service", "sc query sshd"),
    ("Guest agent service", "sc query QEMU-GA"),
    ("System log", "wevtutil qe System /c:100 /rd:true /f:text"),
)


def windows_guest(vm: dict[str, Any]) -> bool:
    return any(key.startswith("windows") and key.endswith("_config") for key in vm)


def diagnostic_commands(family: str) -> tuple[tuple[str, str], ...]:
    """The fixed read-only commands of a detected family; `+sudo` runs each as root."""
    if family == "windows":
        return WINDOWS_COMMANDS
    family, _, elevation = family.partition("+")
    init, _, packages = family.partition("-")
    if init not in INIT_COMMANDS or packages not in PACKAGE_COMMANDS or elevation not in ("", "sudo"):
        raise VMError("Unrecognized diagnostic family")
    commands = POSIX_COMMANDS + INIT_COMMANDS[init] + PACKAGE_COMMANDS[packages]
    if elevation:
        return tuple((title, f"sudo -n sh -c {shlex.quote(command)}") for title, command in commands)
    return commands


def format_result(title: str, command: str, result: dict[str, Any]) -> str:
    return (f"\n=== {title} ===\n$ {command}\n[stdout]\n{result['stdout']}\n"
            f"[stderr]\n{result['stderr']}\n[exit: {result['returncode']}"
            f"{'; stopped: ' + result['stopped'] if result['stopped'] else ''}]\n")


def diagnostics(name: str) -> Path:
    vm = config.get_vm(config.load_config(), name)
    directory = state.ROOT / "artifacts" / name / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    target = directory / f"diagnostics-{stamp}.txt"
    with target.open("x", encoding="utf-8") as report:
        report.write(f"VM: {name}\nUTC: {stamp}\nRead-only diagnostics. Per command: {COMMAND_TIMEOUT:g}s, {MAX_OUTPUT} bytes.\n")
        try:
            family = "windows" if windows_guest(vm) else ""
            command = "exit 0" if family else DETECT
            detected = remote(vm, command, PROBE_TIMEOUT)
            report.write(format_result("SSH / family detection", command, detected))
            if detected["returncode"] or detected["stopped"]:
                raise VMError("SSH unavailable; using existing serial logs.")
            family = family or detected["stdout"].strip()
            for title, command in diagnostic_commands(family):
                result = remote(vm, command)
                report.write(format_result(title, command, result))
                if result["returncode"] == 255:
                    report.write("SSH connection lost; remaining guest commands skipped.\n")
                    break
        except (VMError, OSError) as exc:
            report.write(f"\n{exc}\n")
        # Existing files only: never open the serial socket or wake/type into the console.
        logs = sorted(directory.glob("*serial*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not logs:
            report.write("\nNo existing serial log is available.\n")
        for path in logs[:MAX_SERIAL_LOGS]:
            with contextlib.suppress(OSError):
                with path.open("rb") as source:
                    source.seek(0, os.SEEK_END)
                    size = source.tell()
                    source.seek(max(0, size - MAX_OUTPUT))
                    report.write(f"\n=== Existing serial log: {path.name} (last {MAX_OUTPUT} bytes) ===\n")
                    report.write(source.read(MAX_OUTPUT).decode("utf-8", errors="replace"))
    return target


def run_guest(name: str, command: str) -> int:
    """Worker payload for tui_jobs; output and exit code stay in its VM job history."""
    vm = config.get_vm(config.load_config(), name)
    try:
        result = remote(vm, command, timeout=60.0, limit=1024 * 1024)
        print(format_result("Guest command", command, result), flush=True)
        if result["stopped"]:
            print("SSH was closed; a guest process may continue running.", flush=True)
            return 124 if result["stopped"] == "timeout" else 125
        return int(result["returncode"]) if result["returncode"] >= 0 else 1
    except (VMError, OSError) as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1

