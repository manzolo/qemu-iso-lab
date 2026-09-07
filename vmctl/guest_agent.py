"""Small, bounded QEMU guest agent client over the per-VM Unix socket."""
from __future__ import annotations

import ipaddress
import json
import math
import secrets
import socket
import time
from pathlib import Path
from typing import Any

from vmctl import qemu, ui
from vmctl.errors import VMError


DEFAULT_TIMEOUT_SEC = 5.0
MAX_REPLY_BYTES = 4 * 1024 * 1024


def enabled(vm: dict[str, Any]) -> bool:
    return vm.get("guest_agent") is True


def socket_path(vm: dict[str, Any]) -> Path:
    return qemu.guest_agent_socket_path(vm)


class _Connection:
    """Preserve buffered replies and share one deadline across all socket operations."""

    def __init__(self, sock: socket.socket, timeout: float):
        self.sock = sock
        self.deadline = time.monotonic() + timeout
        self.buffer = b""

    def set_timeout(self) -> None:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("guest agent request timed out")
        self.sock.settimeout(remaining)

    def send(self, payload: dict[str, Any], *, sync: bool = False) -> None:
        self.set_timeout()
        self.sock.sendall((b"\xff" if sync else b"") + json.dumps(payload).encode() + b"\n")

    def receive(self, *, synchronizing: bool = False) -> dict[str, Any]:
        while True:
            self.set_timeout()
            if b"\n" not in self.buffer:
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise OSError("guest agent closed the connection")
                self.buffer += chunk
                if len(self.buffer) > MAX_REPLY_BYTES:
                    raise VMError("Guest agent reply exceeds the size limit")
                continue
            line, _, self.buffer = self.buffer.partition(b"\n")
            # The delimiter resets any incomplete response from a previous client.
            if synchronizing:
                line = line.rsplit(b"\xff", 1)[-1]
            try:
                parsed = json.loads(line)
            except (ValueError, UnicodeError) as exc:
                if synchronizing:
                    continue
                raise VMError("Invalid JSON in guest agent reply") from exc
            if not isinstance(parsed, dict) or not ({"return", "error"} & parsed.keys()):
                if synchronizing:
                    continue
                raise VMError("Unexpected guest agent reply: expected return or error")
            return parsed


def command(
    vm: dict[str, Any],
    name: str,
    arguments: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> Any:
    """Return a command result; shutdown only submits its asynchronous request."""
    if not enabled(vm):
        raise VMError("Guest agent is disabled; set guest_agent: true and restart the VM")
    if not math.isfinite(timeout) or timeout <= 0:
        raise VMError("Guest agent timeout must be a positive finite number")
    path = socket_path(vm)
    if not path.exists():
        raise VMError(f"Guest agent socket not found: {path} (restart the VM with guest_agent enabled)")
    payload: dict[str, Any] = {"execute": name}
    if arguments is not None:
        payload["arguments"] = arguments
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            connection = _Connection(sock, timeout)
            connection.set_timeout()
            sock.connect(str(path))
            sync_id = secrets.randbits(63)
            connection.send({"execute": "guest-sync-delimited", "arguments": {"id": sync_id}}, sync=True)
            while True:
                reply = connection.receive(synchronizing=True)
                if type(reply.get("return")) is int and reply["return"] == sync_id:
                    break
            connection.send(payload)
            # QGA deliberately sends no success response to guest-shutdown. The
            # lifecycle caller must watch QEMU exit to establish actual shutdown.
            if name == "guest-shutdown":
                return None
            reply = connection.receive()
    except OSError as exc:
        raise VMError(f"Guest agent request '{name}' failed on {path}: {exc}. Check that qemu-guest-agent is running in the guest.") from exc
    if "error" in reply:
        error = reply["error"]
        description = error.get("desc", error) if isinstance(error, dict) else error
        raise VMError(f"Guest agent refused '{name}': {description}")
    return reply["return"]


def responds(vm: dict[str, Any], timeout: float = DEFAULT_TIMEOUT_SEC) -> bool:
    if not enabled(vm):
        return False
    try:
        command(vm, "guest-ping", timeout=timeout)
    except VMError:
        return False
    return True


def addresses(vm: dict[str, Any]) -> list[tuple[str, str]]:
    """Report unique IPv4/IPv6 addresses, excluding loopback on any guest OS."""
    found: list[tuple[str, str]] = []
    interfaces = command(vm, "guest-network-get-interfaces")
    if not isinstance(interfaces, list):
        raise VMError("Invalid guest agent network interface list")
    for interface in interfaces:
        if not isinstance(interface, dict):
            raise VMError("Invalid guest agent network interface")
        name = str(interface.get("name", "?"))
        for entry in interface.get("ip-addresses") or []:
            if not isinstance(entry, dict):
                continue
            address = str(entry.get("ip-address", ""))
            try:
                parsed = ipaddress.ip_address(address)
            except ValueError:
                continue
            pair = (name, address)
            if not parsed.is_loopback and not parsed.is_unspecified and pair not in found:
                found.append(pair)
    return found


def os_description(vm: dict[str, Any]) -> str:
    info = command(vm, "guest-get-osinfo")
    if not isinstance(info, dict):
        raise VMError("Invalid guest agent OS information")
    parts = [str(info[key]) for key in ("pretty-name", "kernel-release") if info.get(key)]
    return " · ".join(parts) or "unknown guest"


def shutdown(vm: dict[str, Any], mode: str = "powerdown") -> None:
    if mode not in {"powerdown", "halt", "reboot"}:
        raise VMError(f"Invalid guest shutdown mode: {mode}")
    command(vm, "guest-shutdown", {"mode": mode})


def print_report(vm_name: str, vm: dict[str, Any]) -> None:
    command(vm, "guest-ping")
    ui.print_status("ok", f"Guest agent answers for '{vm_name}'")
    # Older agents can answer ping/network queries without supporting OS info.
    try:
        ui.print_kv("guest", os_description(vm))
    except VMError as exc:
        ui.print_status("warn", str(exc), ok=False)
    for interface, address in addresses(vm):
        ui.print_kv(interface, address)
