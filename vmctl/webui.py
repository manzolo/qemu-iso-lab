"""`vmctl web`: the lab in a browser, on 127.0.0.1 only (`--lan`: your network too, an explicit choice).

A small JSON API over what the TUI already uses, so the browser drives the same backend:

- the dashboard snapshot (``tui_bridge.ClassicBridge.snapshot``: one fact row per profile) and
  the labs (``vmctl group list --labs --json``);
- every ``vmctl`` subcommand, described from the argparse parser itself (``command_catalog``),
  so a new command appears in the browser with its options and no web code to write;
- runs as detached jobs through ``tui_jobs``: a job started for a VM lives in the same
  ``artifacts/<vm>/runtime/tui-job`` as the TUI's, so each interface sees the other's installs,
  and a job outlives the web server;
- a live view of a running VM (QMP screendump, PNG) and the lab network maps.

Security: the server binds 127.0.0.1, checks the Host header (no DNS rebinding) and wants a
random token on every API call. Terminal/sudo commands cannot run as detached jobs; SSH has
a dedicated PTY endpoint, and flash is copy-only. Destructive jobs require confirmation.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import io
import json
import os
import secrets
import shlex
import re
import shutil
import signal
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from vmctl import catalog, config, integration, isofile, local_identity, profile_overrides, qemu, runtime, state, tui_jobs, ui, web_files, web_recording, web_transfers
from vmctl.errors import VMError

DEFAULT_PORT = 8765
WEB_DIR = Path(__file__).resolve().parent / "web"
# Need an interactive terminal or sudo: excluded from the detached job endpoint.
EXCLUDED_COMMANDS = {"web", "shell", "console", "flash", "import-device", "completion", "identity", "update"}  # identity: its own panel, no password in a job log; update: a git pull under the running server
# Discoverable in the command center and opened in a terminal window on the host (sudo and the
# CLI's own questions happen there), never as a detached job.
TERMINAL_COMMANDS = {"flash", "import-device"}
# Delete or overwrite something: the browser asks first and the request must say it did.
DESTRUCTIVE = {"clean", "delete-iso", "clean-reports", "clean-stale", "unexport-libvirt"}
# group install deletes only the disks of members whose install never finished, so it asks only
# when there are some (lifecycle.group_install_overwrites); a first install is not destructive.
DESTRUCTIVE_ACTIONS = {"checkpoint": {"restore", "delete"}, "group": {"clean", "remove", "reset", "checkpoint"},
                       "lab": {"clean", "install"}}
LOG_CHUNK = 65536


def _subcommands() -> dict[str, argparse.ArgumentParser]:
    from vmctl import cli  # the CLI registers this module's handler: import it lazily

    parser = cli.build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    seen: dict[int, str] = {}
    result: dict[str, argparse.ArgumentParser] = {}
    for name, subparser in sub.choices.items():
        if id(subparser) in seen:  # an alias (test-local = check-vms)
            continue
        seen[id(subparser)] = name
        result[name] = subparser
    return result


def command_catalog() -> list[dict[str, Any]]:
    """Every offered subcommand with its group, help and arguments, in the CLI's own order."""
    from vmctl import cli

    parsers = _subcommands()
    catalog: list[dict[str, Any]] = []
    for group, _, names in cli.COMMAND_GROUPS:
        for name in names:
            if (name in EXCLUDED_COMMANDS and name not in TERMINAL_COMMANDS) or name not in parsers:
                continue
            args: list[dict[str, Any]] = []
            for action in parsers[name]._actions:
                if isinstance(action, argparse._HelpAction) or action.help == argparse.SUPPRESS:
                    continue
                entry: dict[str, Any] = {
                    "dest": action.dest,
                    "flag": action.option_strings[-1] if action.option_strings else "",
                    "help": action.help or "",
                    "required": bool(action.required) if action.option_strings else action.nargs not in ("?", "*"),
                    "choices": [str(choice) for choice in action.choices] if action.choices else [],
                    "kind": "flag" if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction))
                            else "list" if action.nargs in ("*", "+") else "value",
                    "default": action.default if isinstance(action.default, (str, int, float)) else None,
                }
                args.append(entry)
            catalog.append({"name": name, "group": group, "help": cli.COMMAND_HELP.get(name, ""),
                            "args": args, "destructive": name in DESTRUCTIVE,
                            "terminal_only": name in TERMINAL_COMMANDS,
                            "exclusive_groups": [[a.option_strings[-1] if a.option_strings else a.dest
                                                  for a in group._group_actions]
                                                 for group in parsers[name]._mutually_exclusive_groups],
                            "destructive_actions": sorted(DESTRUCTIVE_ACTIONS.get(name, set()))})
    return catalog


def is_destructive(args: list[str]) -> bool:
    if not args:
        return False
    if args[0] in DESTRUCTIVE:
        return True
    actions = DESTRUCTIVE_ACTIONS.get(args[0], set())
    if any(arg in actions for arg in args[1:2]):
        return True
    if args[:2] == ["group", "install"] and len(args) > 2:
        from vmctl import lifecycle
        try:
            return bool(lifecycle.group_install_overwrites(args[2]))
        except VMError:
            return True  # an unknown group fails in the command itself; never skip the question on doubt
    return args[0] == "check-vms" and "--clean-first" in args


def prepare_command(args: list[str], confirmed: bool) -> tuple[list[str], str | None]:
    """Validate a browser request: the full vmctl command line and the VM it concerns, if any."""
    if not args or not all(isinstance(arg, str) for arg in args):
        raise VMError("Empty or invalid command")
    parsers = _subcommands()
    name = args[0]
    if name in EXCLUDED_COMMANDS or name not in parsers:
        raise VMError(f"'{name}' cannot be run from the browser")
    if is_destructive(args) and not confirmed:
        raise VMError(f"'{shlex.join(args)}' deletes or overwrites data: confirm it first")
    parser = parsers[name]
    captured = io.StringIO()
    try:
        with contextlib.redirect_stderr(captured):
            namespace, unknown = parser.parse_known_args(args[1:])
    except SystemExit as exc:
        message = captured.getvalue().strip().splitlines()
        raise VMError(message[-1] if message else f"Invalid arguments for '{name}'") from exc
    if unknown:
        raise VMError(f"Unknown arguments for '{name}': {' '.join(unknown)}")
    command = [str(state.ROOT / "bin" / "vmctl"), *args]
    wants_yes = any("--yes" in action.option_strings for action in parser._actions)
    if confirmed and wants_yes and "--yes" not in args:
        # Jobs run without a terminal and a pipe never counts as yes: the confirmation was the browser's.
        command.append("--yes")
    vm = getattr(namespace, "vm", None)
    if isinstance(vm, str) and vm in config.load_config()["vms"]:
        return command, vm
    return command, None


# --- jobs ------------------------------------------------------------------------------------

def web_jobs_dir() -> Path:
    return state.ROOT / "artifacts" / ".web-jobs"


def start_job(command: list[str], vm: str | None) -> str:
    if vm is not None:
        tui_jobs.start(state.ROOT, vm, command)
        return f"vm:{vm}"
    job_id = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    directory = web_jobs_dir() / job_id
    directory.mkdir(parents=True, exist_ok=True)
    tui_jobs._start(state.ROOT, job_id, directory, command)
    return f"web:{job_id}"


def job_directory(job_id: str) -> Path:
    kind, _, name = job_id.partition(":")
    if kind == "vm":
        return tui_jobs.job_dir(state.ROOT, name)
    if kind == "history":
        vm, _, entry = name.partition(":")
        if entry and entry not in (".", "..") and Path(entry).name == entry:
            return tui_jobs.job_dir(state.ROOT, vm) / "history" / entry
    if kind == "web" and name and Path(name).name == name:
        return web_jobs_dir() / name
    raise VMError(f"Unknown job: {job_id}")


def vm_history(name: str) -> list[dict[str, Any]]:
    config.get_vm(config.load_config(), name)
    directory = tui_jobs.job_dir(state.ROOT, name)
    entries = [(f"vm:{name}", directory)]
    archived = directory / "history"
    if archived.is_dir():
        entries += [(f"history:{name}:{path.name}", path) for path in sorted(archived.iterdir(), reverse=True)[:99] if path.is_dir()]
    return [{"id": job_id, "command": display_job_command(tui_jobs.command(path)),
             "status": tui_jobs.status(path), "updated": (path / "output.log").stat().st_mtime}
            for job_id, path in entries if (path / "output.log").is_file()]


def is_guest_command(argv: list[str]) -> bool:
    return len(argv) == 6 and argv[1:3] == ["guest-command-helper", "--vm"] and argv[4].startswith("--script=")


def display_job_command(argv: list[str]) -> str:
    if is_guest_command(argv):
        return f"Guest command · {argv[3]}: {argv[4][len('--script='):]}"
    return shlex.join(argv).replace(str(state.ROOT / "bin" / "vmctl"), "vmctl")


def prepare_guest_command(name: str, command: Any, confirmed: Any) -> list[str]:
    config.get_vm(config.load_config(), name)
    if confirmed is not True:
        raise VMError("Confirm the guest command explicitly before running it")
    if not isinstance(command, str) or not command.strip() or "\0" in command or len(command.encode()) > 16384:
        raise VMError("Guest command must contain 1–16384 bytes, without NUL characters")
    return [str(state.ROOT / "bin" / "vmctl"), "guest-command-helper", "--vm", name, "--script=" + command, "--yes"]


def list_jobs(limit: int = 40) -> list[dict[str, Any]]:
    entries: list[tuple[str, Path]] = []
    artifacts = state.ROOT / "artifacts"
    if artifacts.is_dir():
        for directory in artifacts.glob("*/runtime/tui-job"):
            entries.append((f"vm:{directory.parent.parent.name}", directory))
    if web_jobs_dir().is_dir():
        entries += [(f"web:{d.name}", d) for d in web_jobs_dir().iterdir() if d.is_dir()]
    jobs: list[dict[str, Any]] = []
    for job_id, directory in entries:
        log = directory / "output.log"
        if not log.is_file():
            continue
        with log.open(errors="replace") as fh:
            first = fh.readline().strip()
        command = first[2:] if first.startswith("$ ") else first
        argv = tui_jobs.command(directory)
        if argv:
            command = display_job_command(argv)
        jobs.append({"id": job_id, "status": tui_jobs.status(directory) or "unknown",
                     "command": command.replace(str(state.ROOT / "bin" / "vmctl"), "vmctl"),
                     "updated": log.stat().st_mtime})
    jobs.sort(key=lambda job: (job["status"] != "running", -job["updated"]))
    return jobs[:limit]


def read_log(job_id: str, offset: int) -> dict[str, Any]:
    directory = job_directory(job_id)
    log = directory / "output.log"
    if not log.is_file():
        raise VMError(f"No log for {job_id}")
    size = log.stat().st_size
    offset = max(0, min(offset, size))
    with log.open("rb") as fh:
        fh.seek(offset)
        data = fh.read(LOG_CHUNK)
    return {"text": data.decode(errors="replace"), "offset": offset + len(data), "size": size,
            "status": tui_jobs.status(directory) or "unknown"}


def cancel_job(job_id: str, force_stop: bool = False) -> bool:
    kind, _, name = job_id.partition(":")
    if kind == "vm":
        vmctl = str(state.ROOT / "bin" / "vmctl")

        def stop_vm() -> None:
            if not force_stop and is_guest_command(tui_jobs.command(tui_jobs.job_dir(state.ROOT, name))):
                return  # cancelling an SSH command does not power off its guest
            result = subprocess.run([vmctl, "stop", name, "--force"], stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, check=False)
            if result.returncode:
                raise VMError(result.stderr.strip() or result.stdout.strip() or "Force stop failed")

        cancelled = tui_jobs.cancel(state.ROOT, name, stop_vm)
        if force_stop and not cancelled:
            # The job may have just finished starting QEMU. The confirmed force stop
            # still applies, even though there is no longer a worker to cancel.
            directory = tui_jobs.job_dir(state.ROOT, name)
            with tui_jobs.control_lock(directory):
                if tui_jobs.status(directory) == "running":
                    raise VMError("A new operation started; retry Force stop")
                stop_vm()
        return cancelled
    directory = job_directory(job_id)
    if tui_jobs.status(directory) != "running":
        return False
    pgid = tui_jobs.worker_group(directory)
    if pgid is None:
        return False
    os.killpg(pgid, signal.SIGTERM)
    return True


# --- VM views --------------------------------------------------------------------------------


def prepare_terminal_command(args: list[str]) -> list[str]:
    """Validate a request for a terminal-only command (flash, import-device): its own parser, nothing else.

    No browser confirmation is asked for: the command runs in a terminal window on the host, where
    sudo asks for the password and the CLI asks its own questions."""
    if not args or not all(isinstance(arg, str) for arg in args):
        raise VMError("Empty or invalid command")
    name = args[0]
    parsers = _subcommands()
    if name not in TERMINAL_COMMANDS or name not in parsers:
        raise VMError(f"'{name}' is not a terminal command")
    captured = io.StringIO()
    try:
        with contextlib.redirect_stderr(captured):
            _, unknown = parsers[name].parse_known_args(args[1:])
    except SystemExit as exc:
        message = captured.getvalue().strip().splitlines()
        raise VMError(message[-1] if message else f"Invalid arguments for '{name}'") from exc
    if unknown:
        raise VMError(f"Unknown arguments for '{name}': {' '.join(unknown)}")
    return [str(state.ROOT / "bin" / "vmctl"), *args]


# The terminal window is the last stop before a disk is overwritten, and sudo may still hold a
# valid timestamp: show the command and wait for Enter before running it (Ctrl-C aborts), then
# keep the window open after the command so its last lines and the exit status can be read.
HOLD_SCRIPT = ('printf "\\n  %s\\n\\nThis overwrites the target disk. Press Enter to start, Ctrl-C to abort. " "$*"; '
               'read _ || exit 130; "$@"; s=$?; '
               'printf "\\n[vmctl exited with status %s] Press Enter to close this window." "$s"; read _')


def open_host_terminal(command: list[str], hold: bool = False) -> str:
    """Run a fixed vmctl command line in a terminal window on the host, never in a detached web job."""
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise VMError("No desktop session is available. Copy the command into a terminal on the host.")
    if hold:
        command = ["sh", "-c", HOLD_SCRIPT, "vmctl-terminal", *command]
    terminals = [("x-terminal-emulator", "-e"), ("gnome-terminal", "--"),
                 ("konsole", "-e"), ("xfce4-terminal", "-x"), ("kitty", "--"),
                 ("alacritty", "-e"), ("foot", "--"), ("xterm", "-e")]
    for name, separator in terminals:
        executable = shutil.which(name)
        if executable:
            subprocess.Popen([executable, separator, *command], cwd=state.ROOT,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return name
    raise VMError("No supported terminal was found. Copy the command into your terminal on the host.")


def open_ssh_terminal(vm_name: str) -> str:
    """Open the existing SSH CLI in a host terminal."""
    from vmctl import ssh

    vm = config.get_vm(config.load_config(), vm_name)
    ssh.ssh_target(vm)  # validate access without creating keys or opening a connection
    return open_host_terminal([str(state.ROOT / "bin" / "vmctl"), "shell", vm_name])

def lab_map_page(group: str) -> bytes | None:
    """The lab's network map, rendered now from the profiles and the live states.

    Serving only the file `group map` left behind answered 404 until someone ran it, and a
    map written earlier showed the states of that moment. Render the interactive page
    directly; exported files remain self-contained snapshots without web controls.
    """
    from vmctl import labs, lifecycle

    if Path(group).name != group or not group:
        return None
    cfg = config.load_config()
    names = labs.group_members(cfg, group)
    if not names:
        return None
    lab = labs.model(cfg, group, lifecycle.group_states(cfg, names))
    return labs.render_html(lab, interactive=True).encode("utf-8")


def lab_map_state(group: str, inspect: tuple[str, str, int] | None = None) -> dict[str, Any] | None:
    """Refresh the topology and sample each NIC without changing a VM; ``inspect`` = the NIC open
    in the packet inspector and the last packet id the page holds (every newer one comes back)."""
    from vmctl import lab_traffic, labs, lifecycle

    if Path(group).name != group or not group:
        return None
    cfg = config.load_config()
    names = labs.group_members(cfg, group)
    if not names:
        return None
    states = lifecycle.group_states(cfg, names)
    lab = labs.model(cfg, group, states)
    return {"svg": labs._svg(lab, interactive=True), "states": states, "traffic": lab_traffic.monitor.sample(cfg, lab, inspect)}


def lab_guide_page(group: str, lang: str | None, token: str = "") -> bytes | None:
    """The lab's guide (vms/labs/<group>/guide.<lang>.md) rendered as a page; None without one."""
    from vmctl import labs

    if Path(group).name != group or not group:
        return None
    content = labs.load_content(group)
    if not content or not content["guides"]:
        return None
    chosen: str = lang if lang and lang in content["guides"] else ("en" if "en" in content["guides"] else sorted(content["guides"])[0])
    return labs.guide_page(content, chosen, token).encode("utf-8")


def console_info(vm_name: str) -> dict[str, Any]:
    """Inspect the running process/channel, not just the next-boot profile."""
    from vmctl import lifecycle

    vm = config.get_vm(config.load_config(), vm_name)
    running = lifecycle.running_qemu_pid(vm_name, vm) is not None
    channel = None
    if running:
        try:
            if not qemu.QMP_LOCK.acquire(timeout=2.0):
                raise VMError("QMP is busy")
            try:  # QMP serves one client: never race the recorder or the report
                devices = qemu.qmp_execute(qemu.qmp_socket_path(vm), "query-chardev", timeout=2.0)
            finally:
                qemu.QMP_LOCK.release()
            channel = any(device.get("label") == "vdagent0" for device in devices)
        except VMError:
            pass  # An unavailable monitor cannot prove that the channel is absent.
    return {"running": running, "clipboard_channel": channel, "clipboard_enabled": bool(vm.get("clipboard"))}


def screenshot_png(vm_name: str) -> bytes | None:
    from vmctl import report

    vm = config.get_vm(config.load_config(), vm_name)
    sock = qemu.qmp_socket_path(vm)
    if not sock.exists():
        return None
    target = runtime.resolve_path(vm["disk"]["path"]).parent / "runtime" / "web-screen.ppm"
    target.unlink(missing_ok=True)
    if not qemu.qmp_command(sock, "screendump", arguments={"filename": str(target)}):
        # An accelerated display (virtio-vga-gl + egl-headless: the niri/Noctalia/DMS profiles)
        # answers "no surface" to screendump: read the same screen over VNC, as the report does.
        try:
            return report.capture_via_vnc(vm) if qemu.vnc_socket_path(vm).exists() else None
        except (OSError, ValueError, VMError):
            return None
    for _ in range(20):  # QEMU writes the file asynchronously to the reply on some versions
        if target.is_file() and target.stat().st_size > 16:
            break
        time.sleep(0.05)
    try:
        return report.ppm_to_png(target.read_bytes())
    except (OSError, ValueError):
        return None
    finally:
        target.unlink(missing_ok=True)


# --- the VM's own screen, keyboard and mouse: noVNC in the page, bridged to runtime/vnc.sock ---

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def ws_accept(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()


def ws_frame(opcode: int, payload: bytes) -> bytes:
    """One unmasked server frame (RFC 6455): servers never mask."""
    length = len(payload)
    if length < 126:
        header = bytes((0x80 | opcode, length))
    elif length < 1 << 16:
        header = bytes((0x80 | opcode, 126)) + struct.pack("!H", length)
    else:
        header = bytes((0x80 | opcode, 127)) + struct.pack("!Q", length)
    return header + payload


def ws_read_frame(stream: Any) -> tuple[int, bytes] | None:
    """(opcode, payload) of the next client frame, unmasked; None when the peer is gone."""
    head = stream.read(2)
    if len(head) < 2:
        return None
    opcode, length = head[0] & 0x0F, head[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", stream.read(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", stream.read(8))[0]
    if length > 4 * 1024 * 1024:
        raise ValueError("WebSocket frame is too large")
    mask = stream.read(4) if head[1] & 0x80 else b""
    payload = stream.read(length)
    if mask:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return opcode, payload


def bridge_vnc(handler: "Handler", sock_path: Path) -> None:
    """Relay a browser WebSocket to the VM's VNC unix socket until either side closes."""
    vnc = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    vnc.connect(str(sock_path))
    lock = threading.Lock()

    def send(opcode: int, payload: bytes) -> None:
        with lock:
            handler.wfile.write(ws_frame(opcode, payload))
            handler.wfile.flush()

    def pump() -> None:  # VM -> browser
        try:
            while True:
                data = vnc.recv(65536)
                if not data:
                    break
                send(0x2, data)
        except OSError:
            pass
        finally:
            try:
                send(0x8, b"")
            except OSError:
                pass

    threading.Thread(target=pump, daemon=True).start()
    try:
        while True:  # browser -> VM
            frame = ws_read_frame(handler.rfile)
            if frame is None or frame[0] == 0x8:
                break
            opcode, payload = frame
            if opcode == 0x9:
                send(0xA, payload)
            elif opcode in (0x0, 0x1, 0x2):
                vnc.sendall(payload)
    except OSError:
        pass
    finally:
        vnc.close()


def run_json(args: list[str], timeout: float = 120) -> Any:
    result = subprocess.run([str(state.ROOT / "bin" / "vmctl"), *args], capture_output=True, text=True,
                            stdin=subprocess.DEVNULL, timeout=timeout, check=False)
    if result.returncode:
        raise VMError((result.stderr or result.stdout).strip()[-800:] or f"vmctl {args[0]} failed")
    return json.loads(result.stdout)


class Snapshot:
    """The dashboard rows, cached for a few seconds: one bash + Python pass over every profile."""

    def __init__(self, ttl: float = 4.0) -> None:
        self.ttl = ttl
        self.lock = threading.Lock()
        self.value: dict[str, Any] | None = None
        self.taken = 0.0

    def get(self, fresh: bool = False) -> dict[str, Any]:
        with self.lock:
            if fresh or self.value is None or time.monotonic() - self.taken > self.ttl:
                from vmctl.tui_bridge import ClassicBridge

                bridge = ClassicBridge()
                rows = bridge.snapshot()
                for row in rows:
                    argv = tui_jobs.command(tui_jobs.job_dir(state.ROOT, row["name"]))
                    row["job_command"] = "guest-command" if is_guest_command(argv) else argv[1] if len(argv) > 1 and Path(argv[0]).name == "vmctl" else ""
                self.value = {"vms": rows, "labs": bridge.labs(), "time": time.time()}
                self.taken = time.monotonic()
            return self.value


# --- HTTP ------------------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "vmctl-web"
    protocol_version = "HTTP/1.1"  # browsers want "HTTP/1.1 101" for the WebSocket upgrade
    token = ""
    port = DEFAULT_PORT
    snapshot = Snapshot()
    recordings = web_recording.Recordings()
    connections = integration.ConnectionCache()
    uploads = web_files.UploadSessions()
    transfers = web_transfers.Transfers()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - BaseHTTPRequestHandler API
        return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if getattr(self, "close_connection", False):
                self.send_header("Connection", "close")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True  # page closed/reloaded while a snapshot was being built

    def _json(self, value: Any, status: int = HTTPStatus.OK) -> None:
        self._send(status, json.dumps(value).encode(), "application/json")

    def _error(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        self._json({"error": message}, status)

    hosts: frozenset[str] = frozenset()  # the Host values accepted, set by make_server (127.0.0.1 and localhost; --lan adds the host's addresses)

    def _refuse(self, message: str, status: int) -> None:
        # The request body stays unread: on a kept-alive HTTP/1.1 connection it would be parsed as
        # the next request (a 400 "Bad request syntax" written to a client already gone).
        self.close_connection = True
        self._error(message, status)

    def _allowed(self) -> bool:
        host = self.headers.get("Host", "")
        if host not in self.hosts:
            self._refuse("Host not allowed", HTTPStatus.FORBIDDEN)
            return False
        query = parse_qs(urlparse(self.path).query)
        given = self.headers.get("X-Vmctl-Token") or (query.get("token") or [""])[0]
        if not secrets.compare_digest(given, self.token):
            self._refuse("Missing or wrong token: open the URL vmctl web printed", HTTPStatus.UNAUTHORIZED)
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        url = urlparse(self.path)
        # Percent-decoded: the page encodes each segment (a job id "vm:<name>" arrives as "vm%3A<name>").
        path = unquote(url.path)
        if path in ("/", "/index.html"):
            self._send(HTTPStatus.OK, (WEB_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            return
        if path in ("/multi", "/multi.html"):
            # Several consoles side by side (#vms=a,b): every pane is index.html's detached console in
            # an iframe, the page itself only calls the API (token in the URL once, like the dashboard).
            self._send(HTTPStatus.OK, (WEB_DIR / "multi.html").read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/assets/distro-icons.svg":
            self._send(HTTPStatus.OK, (WEB_DIR / "distro-icons.svg").read_bytes(), "image/svg+xml")
            return
        if path == "/assets/icons.js":
            self._send(HTTPStatus.OK, (WEB_DIR / "icons.js").read_bytes(), "text/javascript; charset=utf-8")
            return
        if path in ("/assets/qemu-iso-lab.svg", "/favicon.ico"):
            self._send(HTTPStatus.OK, (WEB_DIR / "qemu-iso-lab.svg").read_bytes(), "image/svg+xml")
            return
        if not self._allowed():
            return
        query = parse_qs(url.query)
        try:
            if path == "/api/state":
                self._json({**self.snapshot.get(fresh="fresh" in query), "version": _version()})
            elif path == "/api/commands":
                self._json(command_catalog())
            elif path == "/api/identity":
                # The Welcome / Identity panel: does local.json exist, which user it names (never the credentials).
                self._json(local_identity.read())
            elif path == "/api/devices":
                self._json(run_json(["list-target-devices", "--json"]))
            elif path == "/api/fs":
                # The "Choose ISO" picker: one host directory (subfolders and .iso files). The medium
                # never travels through the browser; vmctl iso set reads it where it already is.
                self._json(isofile.browse((query.get("path") or [""])[0] or None))
            elif path.startswith("/api/vm/") and path.endswith("/override"):
                self._json(profile_overrides.read_override(path[len("/api/vm/"):-len("/override")]))
            elif path == "/api/jobs":
                self._json(list_jobs())
            elif path.startswith("/api/transfers/"):
                self._json(self.transfers.get(path[len("/api/transfers/"):]).info())
            elif path.startswith("/api/recordings/"):
                self._json(self.recordings.get(path[len("/api/recordings/"):]).info())
            elif path.startswith("/api/jobs/") and path.endswith("/log"):
                job_id = path[len("/api/jobs/"):-len("/log")]
                self._json(read_log(job_id, int((query.get("offset") or ["0"])[0])))
            elif path.startswith("/api/vm/") and path.endswith("/show"):
                self._json(run_json(["show", path.split("/")[3], "--json"]))
            elif path.startswith("/api/vm/") and path.endswith("/vnc"):
                self._vnc(path.split("/")[3])
            elif path.startswith("/api/vm/") and path.endswith("/console-info"):
                self._json(console_info(path[len("/api/vm/"):-len("/console-info")]))
            elif path.startswith("/api/vm/") and path.endswith("/connections"):
                self._json(self.connections.get(path[len("/api/vm/"):-len("/connections")], console_info))
            elif path.startswith("/api/vm/") and path.endswith("/history"):
                self._json(vm_history(path[len("/api/vm/"):-len("/history")]))
            elif path.startswith("/api/vm/") and path.endswith("/files"):
                vm = config.get_vm(config.load_config(), path[len("/api/vm/"):-len("/files")])
                with web_files.SFTP(vm, timeout=30) as client:
                    self._json(client.listing((query.get("path") or ["."])[0]))
            elif path.startswith("/api/vm/") and path.endswith("/file"):
                self._download_file(path[len("/api/vm/"):-len("/file")], (query.get("path") or [""])[0])
            elif path.startswith("/api/vm/") and path.endswith("/ssh"):
                self._ssh(path[len("/api/vm/"):-len("/ssh")])
            elif path.startswith("/api/vm/") and path.endswith("/screen.png"):
                image = screenshot_png(path.split("/")[3])
                if image is None:
                    self._error("No screen: the VM is not running headless with a QMP socket", HTTPStatus.NOT_FOUND)
                else:
                    self._send(HTTPStatus.OK, image, "image/png")
            elif path.startswith("/labs/") and path.endswith("/guide"):
                group = unquote(path.split("/")[2])
                page = lab_guide_page(group, (query.get("lang") or [""])[0] or None, (query.get("token") or [""])[0])
                if page is None:
                    self._error(f"No guide for {group}", HTTPStatus.NOT_FOUND)
                else:
                    self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
            elif path.startswith("/labs/") and path.endswith("/map-state"):
                inspected = (query.get("inspect") or [""])[0].split("/", 1)
                try:
                    since = max(0, int((query.get("since") or ["0"])[0]))
                except ValueError:
                    since = 0
                snapshot = lab_map_state(path.split("/")[2], (inspected[0], inspected[1], since) if len(inspected) == 2 else None)
                if snapshot is None:
                    self._error("Lab not found", HTTPStatus.NOT_FOUND)
                else:
                    self._json(snapshot)
            elif path.startswith("/labs/") and path.endswith("/map"):
                group = unquote(path.split("/")[2])
                page = lab_map_page(group)
                if page is None:
                    self._error(f"No lab called {group}", HTTPStatus.NOT_FOUND)
                else:
                    self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
            else:
                self._error("Not found", HTTPStatus.NOT_FOUND)
        except VMError as exc:
            self._error(str(exc))
        except (ValueError, OSError, subprocess.SubprocessError, RuntimeError) as exc:
            self._error(f"{type(exc).__name__}: {exc}", HTTPStatus.INTERNAL_SERVER_ERROR)

    def _ssh(self, vm_name: str) -> None:
        from vmctl import ssh, web_terminal

        vm = config.get_vm(config.load_config(), vm_name)
        key = self.headers.get("Sec-WebSocket-Key")
        if self.headers.get("Upgrade", "").lower() != "websocket" or not key:
            raise VMError("Expected a WebSocket upgrade")
        command = ssh.ssh_shell_cmd(vm)
        # Keep SSH escapes from launching a local host shell in the browser terminal.
        command[1:1] = ["-o", "EscapeChar=none"]
        self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", ws_accept(key))
        self.end_headers()
        self.close_connection = True
        web_terminal.bridge(self, command)

    def _vnc(self, vm_name: str) -> None:
        """WebSocket upgrade to the VM's VNC socket (headless VMs: `vmctl start --headless`, bootstraps)."""
        vm = config.get_vm(config.load_config(), vm_name)
        sock = qemu.vnc_socket_path(vm)
        key = self.headers.get("Sec-WebSocket-Key")
        if self.headers.get("Upgrade", "").lower() != "websocket" or not key:
            self._error("Expected a WebSocket upgrade")
            return
        if not sock.exists():
            self._error("No screen: the VM is not running headless (a VM with its own window has no VNC socket)",
                        HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", ws_accept(key))
        requested = [p.strip() for p in (self.headers.get("Sec-WebSocket-Protocol") or "").split(",") if p.strip()]
        if "binary" in requested:
            self.send_header("Sec-WebSocket-Protocol", "binary")
        self.end_headers()
        self.close_connection = True
        bridge_vnc(self, sock)

    def _download_file(self, name: str, path: str) -> None:
        web_files.validate_path(path)
        vm = config.get_vm(config.load_config(), name)
        # Stage before sending headers so an SSH failure remains a JSON error,
        # never a successful-looking but truncated download.
        with tempfile.TemporaryFile() as target:
            with web_files.SFTP(vm) as client:
                size = client.download(path, target)
            target.seek(0)
            try:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                shutil.copyfileobj(target, self.wfile, length=65536)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

    def _upload_file(self, name: str, query: dict[str, list[str]]) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 <= length <= web_files.MAX_FILE_SIZE:
            self.close_connection = True
            raise VMError("Files must be no larger than 256 MiB")
        path = web_files.validate_path((query.get("path") or ["."])[0])
        filename = web_files.validate_name((query.get("name") or [""])[0])
        vm = config.get_vm(config.load_config(), name)
        token = (query.get("session") or [""])[0]
        session = self.uploads.use(name, token) if token else web_files.SFTP(vm)
        with session as client, tempfile.TemporaryFile() as source:
            remaining = length
            previous_timeout = self.connection.gettimeout()
            self.connection.settimeout(30)
            deadline = time.monotonic() + 180
            try:
                while remaining:
                    if time.monotonic() >= deadline:
                        raise VMError("Upload timed out")
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        raise VMError("Upload interrupted")
                    source.write(chunk)
                    remaining -= len(chunk)
            finally:
                self.connection.settimeout(previous_timeout)
            source.seek(0)
            client.deadline = time.monotonic() + 180
            self._json(client.upload(path, filename, source, length))

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._allowed():
            return
        path = unquote(urlparse(self.path).path)
        try:
            if path.startswith("/api/transfers/") and path.endswith("/data"):
                self.close_connection = True
                transfer = self.transfers.get(path[len("/api/transfers/"):-len("/data")])
                length = int(self.headers.get("Content-Length") or 0)
                self.connection.settimeout(30)
                transfer.receive(self.rfile, length)
                self._json(transfer.info())
                return
            if path.startswith("/api/vm/") and path.endswith("/files-upload"):
                # Raw file bodies have their own bounded, disk-backed reader.
                self.close_connection = True
                self._upload_file(path[len("/api/vm/"):-len("/files-upload")], parse_qs(urlparse(self.path).query))
                return
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 <= length <= 1024 * 1024:
                self.close_connection = True
                raise VMError("Request body must be no larger than 1 MiB")
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            if not isinstance(body, dict):
                raise VMError("Request body must be a JSON object")
            if path == "/api/transfers":
                self._json(self.transfers.create(body, config.load_config()))
            elif path.startswith("/api/transfers/") and path.endswith("/cancel"):
                self._json(self.transfers.get(path[len("/api/transfers/"):-len("/cancel")]).cancel())
            elif path.startswith("/api/vm/") and path.endswith("/diagnostics"):
                if body:
                    raise VMError("Diagnostics accepts no commands or options")
                name = path[len("/api/vm/"):-len("/diagnostics")]
                target = integration.diagnostics(name)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
                self.send_header("Content-Length", str(target.stat().st_size))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(target.read_bytes())
            elif path.startswith("/api/vm/") and path.endswith("/guest-command"):
                name = path[len("/api/vm/"):-len("/guest-command")]
                command = prepare_guest_command(name, body.get("command"), body.get("confirmed"))
                self._json({"job": start_job(command, name), "command": body["command"]})
            elif path.startswith("/api/vm/") and path.endswith("/files-session"):
                name = path[len("/api/vm/"):-len("/files-session")]
                profile = config.get_vm(config.load_config(), name)
                self._json({"session": self.uploads.create(name, profile)})
            elif path.startswith("/api/vm/") and path.endswith("/files-session-close"):
                name = path[len("/api/vm/"):-len("/files-session-close")]
                self.uploads.close(name, str(body.get("session", "")))
                self._json({"closed": True})
            elif path == "/api/recordings":
                name = str(body.get("vm", ""))
                profile = config.get_vm(config.load_config(), name)
                self._json(self.recordings.start(name, qemu.qmp_socket_path(profile),
                                                state.ROOT / "artifacts" / ".web-recordings", body.get("fps", 10),
                                                vnc=qemu.vnc_socket_path(profile)))
            elif path.startswith("/api/recordings/") and path.endswith("/stop"):
                self._json(self.recordings.get(path[len("/api/recordings/"):-len("/stop")]).stop())
            elif path.startswith("/api/recordings/") and path.endswith("/export"):
                session = self.recordings.get(path[len("/api/recordings/"):-len("/export")])
                kind = str(body.get("format", ""))
                data = session.export(kind)
                self._send(HTTPStatus.OK, data, "image/gif" if kind == "gif" else "video/mp4")
            elif path.startswith("/api/vm/") and path.endswith("/iso-check"):
                medium = body.get("path")
                if not isinstance(medium, str) or not medium:
                    raise VMError("path must be the ISO file to check")
                self._json(isofile.check(path[len("/api/vm/"):-len("/iso-check")], medium))
            elif path == "/api/run":
                command, vm = prepare_command(list(body.get("args") or []), bool(body.get("confirmed")))
                job_id = start_job(command, vm)
                self.snapshot.get(fresh=True)
                self._json({"job": job_id, "command": shlex.join(["vmctl", *command[1:]])})
            elif path.startswith("/api/jobs/") and path.endswith("/cancel"):
                self._json({"cancelled": cancel_job(path[len("/api/jobs/"):-len("/cancel")],
                                                   force_stop=bool(body.get("force_stop")))})
            elif path == "/api/terminal":
                command = prepare_terminal_command(list(body.get("args") or []))
                self._json({"terminal": open_host_terminal(command, hold=True),
                            "command": shlex.join(["vmctl", *command[1:]])})
            elif path.startswith("/api/vm/") and path.endswith("/ssh-terminal"):
                terminal = open_ssh_terminal(path[len("/api/vm/"):-len("/ssh-terminal")])
                self._json({"terminal": terminal})
            elif path == "/api/identity":
                # vmctl identity: {"user", "password" (empty keeps the current one), "realname", "store_password"};
                # creates local.json on a fresh checkout, keeps every other key of it afterwards.
                fields = {key: body.get(key, "") for key in ("user", "password", "realname")}
                if not all(isinstance(value, str) for value in fields.values()):
                    raise VMError("user, password and realname must be strings")
                locale = body.get("locale")
                if locale is not None and not (isinstance(locale, dict) and all(isinstance(v, str) for v in locale.values())):
                    raise VMError("locale must be an object of strings (language, keyboard, timezone)")
                result = local_identity.save(fields["user"], fields["password"], fields["realname"],
                                             store_password=bool(body.get("store_password", True)), locale=locale)
                with self.snapshot.lock:
                    self.snapshot.value = None
                self._json(result)
            elif path == "/api/protect":
                # vmctl protect / unprotect: {"action": add|remove, "names": [...]}, saved in local.json.
                names = body.get("names") or []
                if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
                    raise VMError("names must be a list of profile names")
                action = str(body.get("action") or "")
                if action not in ("add", "remove"):
                    raise VMError("action must be add or remove")
                result = catalog.update_protected(action, names, config.load_config())
                with self.snapshot.lock:
                    self.snapshot.value = None
                self._json(result)
            elif path == "/api/catalog":
                # My VMs: {"action": add|remove|set|clear, "names": [...]}; the selection is validated
                # against the catalog and saved in local.json, then the snapshot is rebuilt.
                names = body.get("names") or []
                if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
                    raise VMError("names must be a list of profile names")
                action = str(body.get("action") or "")
                if action in ("hide", "unhide"):  # vmctl catalog hide / unhide: the dashboards' lists leave them out
                    result = catalog.update_hidden("add" if action == "hide" else "remove", names, config.load_config())
                else:
                    result = catalog.update(action, names, config.load_config())
                with self.snapshot.lock:
                    self.snapshot.value = None
                self._json(result)
            elif path.startswith("/api/vm/") and path.endswith("/override"):
                saved = profile_overrides.save_override(path[len("/api/vm/"):-len("/override")],
                                                        body.get("override"), body.get("revision", ""))
                with self.snapshot.lock:
                    self.snapshot.value = None
                self._json(saved)
            else:
                self._error("Not found", HTTPStatus.NOT_FOUND)
        except VMError as exc:
            self._error(str(exc))
        except RuntimeError as exc:  # a job already running for this VM
            self._error(str(exc), HTTPStatus.CONFLICT)
        except (ValueError, OSError) as exc:
            self._error(f"{type(exc).__name__}: {exc}", HTTPStatus.INTERNAL_SERVER_ERROR)


def _version() -> str:
    import vmctl

    return str(vmctl.__version__)


VIRTUAL_INTERFACES = re.compile(r"^(lo|br-|docker|virbr|mpqemu|tun|tap|veth|wg|vmnet|lxc|lxd|cni|flannel|zt)")


def interface_addresses() -> list[tuple[str, str]]:
    """(interface, IPv4 address) for every address of this host, loopback excluded."""
    found: list[tuple[str, str]] = []
    tool = shutil.which("ip")
    if tool:
        try:
            out = subprocess.run([tool, "-4", "-o", "addr", "show", "scope", "global"], capture_output=True, text=True,
                                 timeout=5, check=False).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        for line in out.splitlines():
            parts = line.split()
            if len(parts) > 3 and "inet" in parts:
                found.append((parts[1], parts[parts.index("inet") + 1].split("/")[0]))
    if not found:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect(("192.0.2.1", 9))  # no packet is sent: the kernel only picks the route
                found.append(("default", str(probe.getsockname()[0])))
        except OSError:
            pass
    return [(name, address) for name, address in found if not address.startswith("127.")]


def lan_addresses(physical_only: bool = False) -> list[str]:
    """The host's IPv4 addresses: all of them for the Host check (a VM on a libvirt bridge may open
    the page too); with *physical_only* the ones a phone on the LAN can reach (ethernet, Wi-Fi), for
    the URLs printed: a host with Docker, libvirt and a VPN has twenty addresses nobody wants listed."""
    pairs = interface_addresses()
    if physical_only:
        kept = [address for name, address in pairs if not VIRTUAL_INTERFACES.match(name)]
        if kept:
            return kept
    return [address for _, address in pairs]


def allowed_hosts(port: int, lan: bool = False) -> frozenset[str]:
    """The Host header values the server answers: loopback, and with --lan the host's own
    addresses and names on that port. Anything else is a page served to the wrong name (DNS
    rebinding) and gets 403."""
    names = ["127.0.0.1", "localhost"]
    if lan:
        hostname = socket.gethostname()
        names += lan_addresses() + [hostname, f"{hostname}.local", hostname.lower(), f"{hostname.lower()}.local"]
    return frozenset(f"{name}:{port}" for name in names)


TLS_DIR = ("artifacts", ".web-tls")  # the self-signed certificate of --lan, made once with openssl


def tls_context(lan_addresses_now: list[str] | None = None) -> ssl.SSLContext:
    """The TLS context of `--lan`: a self-signed certificate under artifacts/.web-tls/, generated
    with openssl the first time (CN = the host name, the host's addresses as SANs). Browsers warn
    once and remember it; the point is a *secure context*, which noVNC and the clipboard API need
    and which plain HTTP has only on 127.0.0.1 (a phone on the LAN got a black console, 2026-10-05)."""
    directory = state.ROOT.joinpath(*TLS_DIR)
    cert, key = directory / "cert.pem", directory / "key.pem"
    if not (cert.is_file() and key.is_file()):
        openssl = shutil.which("openssl")
        if not openssl:
            raise VMError("vmctl web --lan needs HTTPS and `openssl` is not installed (apt install openssl)")
        directory.mkdir(parents=True, exist_ok=True)
        hostname = socket.gethostname()
        sans = ["DNS:localhost", "IP:127.0.0.1", f"DNS:{hostname}", f"DNS:{hostname}.local"]
        sans += [f"IP:{address}" for address in (lan_addresses_now if lan_addresses_now is not None else lan_addresses())]
        subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650", "-subj", f"/CN={hostname}",
                        "-addext", "subjectAltName=" + ",".join(sans), "-keyout", str(key), "-out", str(cert)],
                       check=True, capture_output=True)
        key.chmod(0o600)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(cert), str(key))
    return context


def make_server(port: int, token: str, lan: bool = False) -> ThreadingHTTPServer:
    """The server on 127.0.0.1 over plain HTTP, or with *lan* on every interface over HTTPS (a
    self-signed certificate: the browser warns once): the same token, the Host check widened to the
    host's own addresses, nothing else. An explicit choice for a network the user trusts."""
    uploads = web_files.UploadSessions()
    transfers = web_transfers.Transfers()
    class WebServer(ThreadingHTTPServer):
        def server_close(self) -> None:
            from vmctl import lab_traffic

            uploads.close_all()
            transfers.close_all()
            lab_traffic.monitor.close()
            super().server_close()

    handler: type[Handler] = type("BoundHandler", (Handler,), {"token": token, "port": port, "snapshot": Snapshot(),
                                                              "recordings": web_recording.Recordings(),
                                                              "connections": integration.ConnectionCache(), "uploads": uploads,
                                                              "transfers": transfers})
    server = WebServer(("0.0.0.0" if lan else "127.0.0.1", port), handler)
    server.daemon_threads = True
    if lan:
        server.socket = tls_context().wrap_socket(server.socket, server_side=True)
    handler.port = server.server_address[1]  # --port 0: the Host check needs the port actually bound
    handler.hosts = allowed_hosts(handler.port, lan)
    return server


def default_browser() -> str | None:
    """The desktop's handler for http links, or None: a fresh Lubuntu 22.04 has none (Firefox is
    a snap that is not installed) and xdg-open then falls back to w3m, detached and invisible."""
    query = shutil.which("xdg-mime")
    if not query:
        return None
    try:
        result = subprocess.run([query, "query", "default", "x-scheme-handler/http"], capture_output=True,
                                text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def open_browser(url: str) -> bool:
    """The browser's own chatter (Chrome's Wayland/Vulkan/GCM warnings) must not land in the
    terminal the server prints to: start it detached with its output discarded. False when
    there is no graphical browser to open: the caller says so instead of failing silently."""
    import webbrowser

    opener = shutil.which("xdg-open")
    if opener and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        if not default_browser():
            return False
        subprocess.Popen([opener, url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    return bool(webbrowser.open(url))


def cmd_web(args: argparse.Namespace) -> int:
    token = secrets.token_urlsafe(18)
    lan = bool(getattr(args, "lan", False))
    try:
        server = make_server(args.port, token, lan=lan)
    except OSError as exc:
        where = "every interface" if lan else "127.0.0.1"
        raise VMError(f"Cannot listen on {where}:{args.port}: {exc} (choose another with --port)") from exc
    port = server.server_address[1]
    scheme = "https" if lan else "http"
    url = f"{scheme}://127.0.0.1:{port}/?token={token}"
    ui.print_header("vmctl web: the lab in your browser" + (" (this computer and your LAN, HTTPS)" if lan else " (127.0.0.1 only)"))
    ui.print_kv("open", url)
    if lan:
        # The same page from a phone or another computer on the network: the token is the whole
        # access control, so the URL is as secret as a password; TLS is self-signed (the browser
        # warns once), there because a secure context is what the console and the clipboard need.
        for address in lan_addresses(physical_only=True):
            ui.print_kv("on the LAN", f"https://{address}:{port}/?token={token}")
        ui.print_note("Self-signed certificate (artifacts/.web-tls/): accept the browser's warning once per device. "
                      "A firewall on this computer (ufw) may still block the port for the others: allow it from your network only.")
        ui.print_status("warn", "LAN mode: whoever has this URL controls the lab. "
                        "Use it on a network you trust, stop it with Ctrl-C when done.", ok=False)
    ui.print_note("Jobs started here keep running after Ctrl-C; the TUI shows them too.")
    sys.stdout.flush()  # the URL carries the token: it must reach a log even without a terminal
    if getattr(args, "open", False) and not open_browser(url):
        ui.print_status("warn", "No graphical browser found: open the URL above in one "
                        "(on Ubuntu: sudo snap install firefox).", ok=False)
        sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        handler_class: Any = server.RequestHandlerClass
        handler_class.recordings.close()
        server.server_close()
    return 0
