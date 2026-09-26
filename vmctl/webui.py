"""`vmctl web`: the lab in a browser, on 127.0.0.1 only.

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
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from vmctl import config, profile_overrides, qemu, runtime, state, tui_jobs, ui
from vmctl.errors import VMError

DEFAULT_PORT = 8765
WEB_DIR = Path(__file__).resolve().parent / "web"
# Need an interactive terminal or sudo: excluded from the detached job endpoint.
EXCLUDED_COMMANDS = {"web", "shell", "console", "flash", "import-device", "completion"}
# Discoverable in the command center, but still refused by the execution endpoint.
TERMINAL_COMMANDS = {"flash"}
# Delete or overwrite something: the browser asks first and the request must say it did.
DESTRUCTIVE = {"clean", "delete-iso", "clean-reports", "clean-stale", "unexport-libvirt"}
DESTRUCTIVE_ACTIONS = {"checkpoint": {"restore", "delete"}, "group": {"clean", "install"},
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
    if kind == "web" and name and Path(name).name == name:
        return web_jobs_dir() / name
    raise VMError(f"Unknown job: {job_id}")


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


def cancel_job(job_id: str) -> bool:
    kind, _, name = job_id.partition(":")
    if kind == "vm":
        vmctl = str(state.ROOT / "bin" / "vmctl")

        def stop_vm() -> None:
            subprocess.run([vmctl, "stop", name, "--force"], stdin=subprocess.DEVNULL, capture_output=True, check=False)

        return tui_jobs.cancel(state.ROOT, name, stop_vm)
    directory = job_directory(job_id)
    if tui_jobs.status(directory) != "running":
        return False
    pgid = tui_jobs.worker_group(directory)
    if pgid is None:
        return False
    os.killpg(pgid, signal.SIGTERM)
    return True


# --- VM views --------------------------------------------------------------------------------


def open_ssh_terminal(vm_name: str) -> str:
    """Open the existing SSH CLI in a host terminal, never in a detached web job."""
    from vmctl import ssh

    vm = config.get_vm(config.load_config(), vm_name)
    ssh.ssh_target(vm)  # validate access without creating keys or opening a connection
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise VMError("No desktop session is available. Copy the command into a terminal on the host.")
    command = [str(state.ROOT / "bin" / "vmctl"), "shell", vm_name]
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
                    row["job_command"] = argv[1] if len(argv) > 1 and Path(argv[0]).name == "vmctl" else ""
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

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - BaseHTTPRequestHandler API
        return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
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

    def _allowed(self) -> bool:
        host = self.headers.get("Host", "")
        if host not in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):
            self._error("Host not allowed", HTTPStatus.FORBIDDEN)
            return False
        query = parse_qs(urlparse(self.path).query)
        given = self.headers.get("X-Vmctl-Token") or (query.get("token") or [""])[0]
        if not secrets.compare_digest(given, self.token):
            self._error("Missing or wrong token: open the URL vmctl web printed", HTTPStatus.UNAUTHORIZED)
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        url = urlparse(self.path)
        # Percent-decoded: the page encodes each segment (a job id "vm:<name>" arrives as "vm%3A<name>").
        path = unquote(url.path)
        if path in ("/", "/index.html"):
            self._send(HTTPStatus.OK, (WEB_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            return
        if not self._allowed():
            return
        query = parse_qs(url.query)
        try:
            if path == "/api/state":
                self._json({**self.snapshot.get(fresh="fresh" in query), "version": _version()})
            elif path == "/api/commands":
                self._json(command_catalog())
            elif path.startswith("/api/vm/") and path.endswith("/override"):
                self._json(profile_overrides.read_override(path[len("/api/vm/"):-len("/override")]))
            elif path == "/api/jobs":
                self._json(list_jobs())
            elif path.startswith("/api/jobs/") and path.endswith("/log"):
                job_id = path[len("/api/jobs/"):-len("/log")]
                self._json(read_log(job_id, int((query.get("offset") or ["0"])[0])))
            elif path.startswith("/api/vm/") and path.endswith("/show"):
                self._json(run_json(["show", path.split("/")[3], "--json"]))
            elif path.startswith("/api/vm/") and path.endswith("/vnc"):
                self._vnc(path.split("/")[3])
            elif path.startswith("/api/vm/") and path.endswith("/ssh"):
                self._ssh(path[len("/api/vm/"):-len("/ssh")])
            elif path.startswith("/api/vm/") and path.endswith("/screen.png"):
                image = screenshot_png(path.split("/")[3])
                if image is None:
                    self._error("No screen: the VM is not running headless with a QMP socket", HTTPStatus.NOT_FOUND)
                else:
                    self._send(HTTPStatus.OK, image, "image/png")
            elif path.startswith("/labs/") and path.endswith("/map"):
                group = path.split("/")[2]
                page = state.ROOT / "artifacts" / "labs" / group / "network.html"
                if Path(group).name != group or not page.is_file():
                    self._error(f"No map for {group} yet: run 'group map {group}' first", HTTPStatus.NOT_FOUND)
                else:
                    self._send(HTTPStatus.OK, page.read_bytes(), "text/html; charset=utf-8")
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

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._allowed():
            return
        path = unquote(urlparse(self.path).path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 <= length <= 1024 * 1024:
                self.close_connection = True
                raise VMError("Request body must be no larger than 1 MiB")
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            if not isinstance(body, dict):
                raise VMError("Request body must be a JSON object")
            if path == "/api/run":
                command, vm = prepare_command(list(body.get("args") or []), bool(body.get("confirmed")))
                job_id = start_job(command, vm)
                self.snapshot.get(fresh=True)
                self._json({"job": job_id, "command": shlex.join(["vmctl", *command[1:]])})
            elif path.startswith("/api/jobs/") and path.endswith("/cancel"):
                self._json({"cancelled": cancel_job(path[len("/api/jobs/"):-len("/cancel")])})
            elif path.startswith("/api/vm/") and path.endswith("/ssh-terminal"):
                terminal = open_ssh_terminal(path[len("/api/vm/"):-len("/ssh-terminal")])
                self._json({"terminal": terminal})
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


def make_server(port: int, token: str) -> ThreadingHTTPServer:
    handler: type[Handler] = type("BoundHandler", (Handler,), {"token": token, "port": port, "snapshot": Snapshot()})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    handler.port = server.server_address[1]  # --port 0: the Host check needs the port actually bound
    return server


def open_browser(url: str) -> None:
    """The browser's own chatter (Chrome's Wayland/Vulkan/GCM warnings) must not land in the
    terminal the server prints to: start it detached with its output discarded."""
    import shutil
    import webbrowser

    opener = shutil.which("xdg-open")
    if opener:
        subprocess.Popen([opener, url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    else:
        webbrowser.open(url)


def cmd_web(args: argparse.Namespace) -> int:
    token = secrets.token_urlsafe(18)
    try:
        server = make_server(args.port, token)
    except OSError as exc:
        raise VMError(f"Cannot listen on 127.0.0.1:{args.port}: {exc} (choose another with --port)") from exc
    url = f"http://127.0.0.1:{server.server_address[1]}/?token={token}"
    ui.print_header("vmctl web: the lab in your browser (127.0.0.1 only)")
    ui.print_kv("open", url)
    ui.print_note("Jobs started here keep running after Ctrl-C; the TUI shows them too.")
    sys.stdout.flush()  # the URL carries the token: it must reach a log even without a terminal
    if getattr(args, "open", False):
        open_browser(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
