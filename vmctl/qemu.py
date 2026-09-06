"""QEMU command-line argument builders and firmware helpers."""
from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import socket
import threading
import shutil
import subprocess
import sys
import time
from pathlib import Path

from typing import Any

from vmctl import state, ui, runtime, cloud_init
from vmctl.errors import VMError


_ANSI_ESCAPE_RE = re.compile(
    r"\x1B(?:[@-Z\\-_]"
    r"|\([A-Z0-9]"
    r"|\[[0-?]*[ -/]*[@-~]"
    r"|\][^\x07\x1B]*(?:\x07|\x1B\\)?)"
)


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


def expected_partition_layout(vm: dict[str, Any]) -> str:
    return "gpt" if vm["firmware"]["type"] == "efi" else "dos"


def is_container_disk_format(fmt: str) -> bool:
    return fmt in {"qcow2", "qcow", "vmdk", "vhdx"}


def iter_ovmf_candidates(fw: dict[str, Any]) -> list[tuple[Path, Path]]:
    candidates: list[tuple[Path, Path]] = []
    env_code = os.environ.get("OVMF_CODE")
    env_vars = os.environ.get("OVMF_VARS_TEMPLATE")
    if env_code and env_vars:
        candidates.append((Path(env_code), Path(env_vars)))
    configured_code = fw.get("code")
    configured_vars = fw.get("vars_template")
    if configured_code and configured_vars:
        candidates.append((runtime.resolve_path(configured_code), runtime.resolve_path(configured_vars)))
    for code, vars_template in state.COMMON_OVMF_PAIRS:
        candidates.append((Path(code), Path(vars_template)))
    unique: list[tuple[Path, Path]] = []
    seen: set[tuple[str, str]] = set()
    for pair in candidates:
        key = (str(pair[0]), str(pair[1]))
        if key not in seen:
            unique.append(pair)
            seen.add(key)
    return unique


def resolve_efi_firmware(fw: dict[str, Any]) -> tuple[Path, Path, Path]:
    vars_path = runtime.resolve_path(fw["vars_path"])
    for code, vars_template in iter_ovmf_candidates(fw):
        if code.is_file() and vars_template.is_file():
            return code, vars_template, vars_path
    configured_code = runtime.resolve_path(fw["code"]) if fw.get("code") else None
    configured_vars = runtime.resolve_path(fw["vars_template"]) if fw.get("vars_template") else None
    lines = ["Unable to locate OVMF firmware files for EFI guest."]
    if configured_code:
        lines.append(f"Configured code path: {configured_code}")
    if configured_vars:
        lines.append(f"Configured vars template path: {configured_vars}")
    if "OVMF_CODE" in os.environ or "OVMF_VARS_TEMPLATE" in os.environ:
        lines.append("Environment overrides detected: OVMF_CODE / OVMF_VARS_TEMPLATE")
    lines.append("Run 'make setup' to inspect host dependencies and suggested packages.")
    raise VMError(" ".join(lines))


def firmware_status(vm: dict[str, Any]) -> tuple[str, str]:
    fw = vm["firmware"]
    fw_type = fw["type"]
    if fw_type == "bios":
        return fw_type, "SeaBIOS / no OVMF required"
    if fw_type != "efi":
        raise VMError(f"Unsupported firmware type: {fw_type}")
    code, vars_template, vars_path = resolve_efi_firmware(fw)
    return fw_type, f"code={code} vars_template={vars_template} vars_path={vars_path}"


def machine_arg(vm: dict[str, Any], accel: str | None = None) -> str:
    machine = str(vm["machine"])
    if accel:
        return f"{machine},accel={accel}"
    return machine


def firmware_args(vm: dict[str, Any], dry_run: bool = False) -> list[str]:
    fw = vm["firmware"]
    fw_type = fw["type"]
    if fw_type == "bios":
        return []
    if fw_type != "efi":
        raise VMError(f"Unsupported firmware type: {fw_type}")
    code, vars_template, vars_path = resolve_efi_firmware(fw)
    if not vars_path.exists():
        runtime.ensure_parent(vars_path)
        ui.print_note(f"Creating EFI vars store: {ui.pretty_path(vars_path)}")
        if not dry_run:
            shutil.copyfile(vars_template, vars_path)
    return ["-drive", f"if=pflash,format=raw,readonly=on,file={code}", "-drive", f"if=pflash,format=raw,file={vars_path}"]


def disk_args(vm: dict[str, Any], allow_missing: bool = False, bootindex: int | None = None) -> list[str]:
    """The VM disk; *bootindex* pins it in the firmware boot order (OVMF ignores ``-boot order``)."""
    disk = vm["disk"]
    disk_path = runtime.resolve_path(disk["path"])
    if not disk_path.exists() and not allow_missing:
        raise VMError(f"Disk image not found: {disk_path}")
    interface = disk.get("interface", "virtio")
    boot = f",bootindex={bootindex}" if bootindex is not None else ""
    if interface == "virtio":
        if bootindex is None:
            return ["-drive", f"file={disk_path},format={disk['format']},if=virtio"]
        return ["-drive", f"id=disk0,file={disk_path},format={disk['format']},if=none", "-device", f"virtio-blk-pci,drive=disk0{boot}"]
    if interface == "sata":
        return ["-device", "ich9-ahci,id=ahci0", "-drive", f"id=disk0,file={disk_path},format={disk['format']},if=none", "-device", f"ide-hd,drive=disk0,bus=ahci0.0{boot}"]
    raise VMError(f"Unsupported disk interface: {interface}")


def video_args(vm: dict[str, Any], variant: str | None) -> list[str]:
    video = vm.get("video", {})
    variants = video.get("variants", {})
    selected = variant or video.get("default")
    if not selected:
        return []
    if selected not in variants:
        choices = ", ".join(sorted(variants))
        raise VMError(f"Unknown video profile '{selected}'. Choices: {choices}")
    return list(variants[selected])


def spice_display_args(port: int) -> list[str]:
    if port < 1 or port > 65535:
        raise VMError(f"Invalid SPICE port: {port}")
    return ["-vga", "qxl", "-display", "none", "-spice", f"addr=127.0.0.1,port={port},disable-ticketing=on",
            "-device", "virtio-serial-pci", "-chardev", "spicevmc,id=vdagent0,name=vdagent", "-device", "virtserialport,chardev=vdagent0,name=com.redhat.spice.0"]


def installer_video_variant(vm: dict[str, Any], requested: str | None) -> str | None:
    if requested:
        return requested
    video = vm.get("video", {})
    variants = video.get("variants", {})
    order = video.get("installer_order", ("std", "safe"))
    for candidate in order:
        if candidate in variants:
            return str(candidate)
    return None


# ---- shared folder (virtiofs) -------------------------------------------------------------
# What libvirt does for a <filesystem driver='virtiofs'> plus <memoryBacking><source type='memfd'/>
# <access mode='shared'/>: a virtiofsd per VM on a unix socket, the guest RAM as a shared memfd
# backend and a vhost-user-fs-pci device carrying the mount tag.

VIRTIOFSD_CANDIDATES = ("virtiofsd", "/usr/libexec/virtiofsd", "/usr/lib/qemu/virtiofsd")
VIRTIOFSD_SOCKET_WAIT_SEC = 5.0
_TAG_RE = re.compile(r"^[A-Za-z0-9_.-]{1,36}$")


def shared_dir_config(vm: dict[str, Any]) -> dict[str, str] | None:
    """``shared_dir`` of a profile as ``{"source", "tag"}``, or None when the VM shares nothing."""
    cfg = vm.get("shared_dir")
    if cfg is None:
        return None
    if not isinstance(cfg, dict):
        raise VMError("Invalid shared_dir: expected an object with source and tag")
    source = str(cfg.get("source") or "").strip()
    if not source:
        raise VMError("shared_dir.source is required (host directory to share)")
    tag = str(cfg.get("tag") or "shared").strip()
    if not _TAG_RE.match(tag):
        raise VMError(f"shared_dir.tag {tag!r} must be 1-36 characters of letters, digits, '_', '.' or '-'")
    return {"source": source, "tag": tag}


def shared_dir_source(vm: dict[str, Any]) -> Path:
    """Host directory of the share: ``~`` expanded, relative paths under the repository root."""
    cfg = shared_dir_config(vm)
    if cfg is None:
        raise VMError("VM profile does not define shared_dir")
    path = runtime.expand_host_path(cfg["source"])
    return path if path.is_absolute() else runtime.resolve_path(str(path))


def virtiofs_socket_path(vm: dict[str, Any]) -> Path:
    cfg = shared_dir_config(vm)
    tag = cfg["tag"] if cfg else "shared"
    return runtime.resolve_path(vm["disk"]["path"]).parent / "runtime" / f"virtiofs-{tag}.sock"


def find_virtiofsd() -> str | None:
    for candidate in VIRTIOFSD_CANDIDATES:
        found = shutil.which(candidate)  # also accepts absolute paths; mockable in tests
        if found:
            return found
    return None


def virtiofsd_command(vm: dict[str, Any]) -> list[str]:
    binary = find_virtiofsd()
    if binary is None:
        raise VMError("Missing virtiofsd (package virtiofsd): required by profiles with shared_dir")
    return [
        binary,
        "--socket-path", str(virtiofs_socket_path(vm)),
        "--shared-dir", str(shared_dir_source(vm)),
        "--sandbox", "none",  # unprivileged: no user namespaces or capabilities needed
        "--cache", "auto",
    ]


def virtiofsd_pid_path(vm: dict[str, Any]) -> Path:
    """virtiofsd writes ``<socket>.pid`` next to its socket and holds a lock on it."""
    sock = virtiofs_socket_path(vm)
    return sock.with_name(sock.name + ".pid")


def _virtiofsd_running(vm: dict[str, Any]) -> bool:
    """A daemon from a previous ``common_args`` call is still waiting on its socket."""
    pid_path = virtiofsd_pid_path(vm)
    if not pid_path.is_file() or not virtiofs_socket_path(vm).exists():
        return False
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
    except (ValueError, OSError):
        return False
    return True


def ensure_virtiofsd(vm: dict[str, Any], dry_run: bool = False) -> None:
    """Start the per-VM virtiofsd right before QEMU (which connects to its socket at startup).

    Idempotent: ``common_args`` may be called more than once per launch (``vmctl start`` does),
    and a daemon that is already waiting on the socket is reused. The daemon exits on its own
    when QEMU disconnects. A relative ``source`` that does not exist yet is created under the repo.
    """
    cfg = shared_dir_config(vm)
    if cfg is None:
        return
    if not dry_run and _virtiofsd_running(vm):
        return
    source = shared_dir_source(vm)
    if not source.is_dir():
        if Path(cfg["source"]).expanduser().is_absolute():
            raise VMError(f"shared_dir.source does not exist: {source}")
        ui.print_note(f"Creating shared directory {ui.pretty_path(source)}")
        if not dry_run:
            source.mkdir(parents=True, exist_ok=True)
    cmd = virtiofsd_command(vm)
    sock = virtiofs_socket_path(vm)
    ui.print_note(f"Sharing {ui.pretty_path(source)} as virtiofs tag '{cfg['tag']}'")
    if dry_run:
        ui.print_command(cmd)
        return
    sock.parent.mkdir(parents=True, exist_ok=True)
    for stale in (sock, virtiofsd_pid_path(vm)):
        if stale.exists():
            stale.unlink()
    log_path = runtime.resolve_path(vm["disk"]["path"]).parent / "logs" / "virtiofsd.log"
    runtime.run_background(cmd, log_path)
    deadline = time.monotonic() + VIRTIOFSD_SOCKET_WAIT_SEC
    while not sock.exists():
        if time.monotonic() > deadline:
            raise VMError(f"virtiofsd did not create {sock} within {VIRTIOFSD_SOCKET_WAIT_SEC:.0f}s (see {ui.pretty_path(log_path)})")
        time.sleep(0.1)


def shared_dir_args(vm: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    """(extra -machine option, memory-backend objects, vhost-user-fs device) for a shared_dir VM."""
    cfg = shared_dir_config(vm)
    if cfg is None:
        return "", [], []
    memory = ["-object", f"memory-backend-memfd,id=mem0,size={int(vm['memory_mb'])}M,share=on"]
    device = [
        "-chardev", f"socket,id=virtiofs0,path={virtiofs_socket_path(vm)}",
        "-device", f"vhost-user-fs-pci,chardev=virtiofs0,tag={cfg['tag']}",
    ]
    return ",memory-backend=mem0", memory, device


# --- networking ------------------------------------------------------------------
#
# Without a ``networks`` list a profile has the historical single slirp NIC (``network: user``,
# SSH port forward from ``ssh_host_port``). ``networks`` describes several NICs, each with a
# *type* (``user`` = slirp, ``segment`` = a host-local L2 segment shared by every VM that names
# the same segment: a multicast socket netdev on plain QEMU, a libvirt network after
# ``export-libvirt``) and a *phase*: ``install`` NICs exist only while a bootstrap runs and
# provisions the guest over SSH, ``runtime`` NICs only afterwards, ``both`` always. A NIC keeps
# its position (PCI slot) and MAC across phases, so the guest sees the same interface.

NETWORK_PHASES = ("install", "runtime")
_NETWORK_TYPES = ("user", "segment")
_MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")


def network_specs(vm: dict[str, Any], phase: str = "runtime") -> list[dict[str, Any]]:
    """Normalised NIC list of *vm* for *phase* (``install`` or ``runtime``)."""
    if phase not in NETWORK_PHASES:
        raise VMError(f"Unknown network phase: {phase}")
    raw = vm.get("networks")
    if raw is None:
        mode = vm.get("network", "user")
        if mode != "user":
            raise VMError(f"Unsupported network mode: {mode}")
        return [{"id": "n1", "type": "user", "phase": "both", "ssh": True, "hostfwd": [], "legacy": True,
                 "device": str(vm.get("network_device", "virtio-net-pci")), "mac": None, "name": None, "mcast": None}]
    if not isinstance(raw, list) or not raw:
        raise VMError("networks must be a non-empty list of NIC objects")
    specs: list[dict[str, Any]] = []
    ssh_seen = False
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise VMError("networks entries must be objects")
        kind = str(entry.get("type") or "user")
        if kind not in _NETWORK_TYPES:
            raise VMError(f"networks[{index}].type must be one of: {', '.join(_NETWORK_TYPES)}")
        nic_phase = str(entry.get("phase") or "both")
        if nic_phase not in ("both", *NETWORK_PHASES):
            raise VMError(f"networks[{index}].phase must be install, runtime or both")
        if nic_phase not in ("both", phase):
            continue
        slot = len(specs)
        name = str(entry.get("name") or "").strip() or None
        if kind == "segment" and not name:
            raise VMError(f"networks[{index}]: a segment NIC needs a name (the segment shared with the other VMs)")
        mac = str(entry.get("mac") or "").strip().lower() or None
        if mac is not None and not _MAC_RE.match(mac):
            raise VMError(f"networks[{index}].mac {mac!r} is not a valid MAC address")
        forwards: list[dict[str, int]] = []
        for fwd in entry.get("hostfwd", []) or []:
            if not isinstance(fwd, dict) or "host_port" not in fwd or "guest_port" not in fwd:
                raise VMError(f"networks[{index}].hostfwd entries need host_port and guest_port")
            forwards.append({"host_port": int(fwd["host_port"]), "guest_port": int(fwd["guest_port"])})
        if kind != "user" and forwards:
            raise VMError(f"networks[{index}]: hostfwd applies to user (slirp) NICs only")
        ssh_default = kind == "user" and not ssh_seen
        ssh = bool(entry.get("ssh", ssh_default)) if kind == "user" else False
        ssh_seen = ssh_seen or ssh
        specs.append({
            "id": str(entry.get("id") or f"net{slot}"), "type": kind, "phase": nic_phase, "ssh": ssh,
            "hostfwd": forwards, "legacy": False, "device": str(entry.get("device") or vm.get("network_device", "virtio-net-pci")),
            "mac": mac or default_nic_mac(vm, slot), "name": name, "mcast": entry.get("mcast"),
        })
    if not specs:
        raise VMError(f"networks: no NIC is active in the {phase} phase")
    return specs


def default_nic_mac(vm: dict[str, Any], slot: int) -> str:
    """A stable, locally administered MAC per (VM disk, NIC slot): guests keep their interface names."""
    digest = hashlib.sha256(f"{vm['disk']['path']}#{slot}".encode()).digest()
    return "52:54:00:" + ":".join(f"{b:02x}" for b in digest[:3])


def segment_endpoint(name: str, override: Any = None) -> str:
    """``group:port`` of the multicast socket that carries segment *name* between the VMs of this host."""
    if override:
        text = str(override)
        if not re.fullmatch(r"2(2[4-9]|3\d)\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{1,5}", text):
            raise VMError(f"mcast {text!r} must look like 239.x.y.z:port")
        return text
    digest = hashlib.sha256(name.encode()).digest()
    port = 20000 + int.from_bytes(digest[3:5], "big") % 40000
    return f"239.{digest[0]}.{digest[1]}.{digest[2]}:{port}"


def network_args(vm: dict[str, Any], phase: str = "runtime") -> list[str]:
    args: list[str] = []
    ssh_cfg = cloud_init.ssh_access_config(vm)
    for spec in network_specs(vm, phase):
        if spec["type"] == "user":
            netdev = f"user,id={spec['id']}"
            if spec["ssh"] and ssh_cfg is not None and ssh_cfg.get("ssh_host_port"):
                netdev += f",hostfwd=tcp:127.0.0.1:{int(ssh_cfg['ssh_host_port'])}-:22"
            for fwd in spec["hostfwd"]:
                netdev += f",hostfwd=tcp:127.0.0.1:{fwd['host_port']}-:{fwd['guest_port']}"
        else:
            netdev = f"socket,id={spec['id']},mcast={segment_endpoint(str(spec['name']), spec['mcast'])}"
        device = f"{spec['device']},netdev={spec['id']}"
        if not spec["legacy"]:
            device += f",mac={spec['mac']}"
        args += ["-netdev", netdev, "-device", device]
    return args


def serial_socket_path(vm: dict[str, Any]) -> Path:
    """Unix socket of the guest's first serial port (COM1 / ttyS0 / cuau0) for background VMs: `vmctl console`."""
    return runtime.resolve_path(vm["disk"]["path"]).parent / "runtime" / "serial.sock"


def serial_socket_args(sock_path: Path, log_path: Path | None) -> list[str]:
    """COM1 on a unix socket (server, no wait) that also logs everything the guest prints to *log_path*."""
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    chardev = f"socket,id=char0,path={sock_path},server=on,wait=off"
    if log_path is not None:
        runtime.ensure_parent(log_path)
        chardev += f",logfile={log_path},logappend=on"
    return ["-chardev", chardev, "-serial", "chardev:char0"]


CONSOLE_ESCAPE = b"\x1d"  # Ctrl-]


def serial_console(sock_path: Path, escape: bytes = CONSOLE_ESCAPE) -> None:
    """Attach the terminal to the guest serial socket until *escape* (Ctrl-]) is typed."""
    import termios
    import tty

    if not sys.stdin.isatty():
        raise VMError("vmctl console needs an interactive terminal")
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        conn.connect(str(sock_path))
    except OSError as exc:
        raise VMError(f"Cannot connect to the serial socket {sock_path}: {exc}") from exc
    stdin_fd = sys.stdin.fileno()
    saved = termios.tcgetattr(stdin_fd)
    tty.setraw(stdin_fd)
    try:
        sel = selectors.DefaultSelector()
        sel.register(conn, selectors.EVENT_READ)
        sel.register(stdin_fd, selectors.EVENT_READ)
        conn.sendall(b"\r")  # nudge the getty / menu to redraw its prompt
        while True:
            for key, _ in sel.select():
                if key.fileobj is conn:
                    data = conn.recv(4096)
                    if not data:
                        return
                    os.write(sys.stdout.fileno(), data)
                else:
                    data = os.read(stdin_fd, 1024)
                    if not data or escape in data:
                        return
                    conn.sendall(data)
    finally:
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, saved)
        conn.close()
        os.write(sys.stdout.fileno(), b"\r\n")


def qmp_socket_path(vm: dict[str, Any]) -> Path:
    """QMP control socket of a headless VM, next to its runtime PID file."""
    return runtime.resolve_path(vm["disk"]["path"]).parent / "runtime" / "qmp.sock"


def vnc_socket_path(vm: dict[str, Any]) -> Path:
    """VNC display socket of a headless VM, next to its QMP socket (`vmctl attach`)."""
    return runtime.resolve_path(vm["disk"]["path"]).parent / "runtime" / "vnc.sock"


class UnixSocketBridge:
    """Expose a unix-domain socket on 127.0.0.1:<port> for viewers that only speak TCP.

    QEMU serves the headless display on a unix socket (no port collisions
    between VMs, nothing listening while nobody is attached); most VNC viewers
    want a host:port. Each accepted TCP client gets its own upstream
    connection and two pump threads, one per direction.
    """

    def __init__(self, unix_path: Path, port: int = 0, host: str = "127.0.0.1") -> None:
        self.unix_path = unix_path
        self.host = host
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind((host, port))
        self._listener.listen(4)
        self.port: int = int(self._listener.getsockname()[1])
        self._closing = False
        self._thread = threading.Thread(target=self._accept_loop, name="vnc-bridge", daemon=True)

    @property
    def url(self) -> str:
        return f"vnc://{self.host}:{self.port}"

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._closing = True
        try:
            self._listener.close()
        except OSError:
            pass

    def _accept_loop(self) -> None:
        while not self._closing:
            try:
                client, _ = self._listener.accept()
            except OSError:
                return
            upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                upstream.connect(str(self.unix_path))
            except OSError:
                client.close()
                upstream.close()
                continue
            for src, dst in ((client, upstream), (upstream, client)):
                threading.Thread(target=self._pump, args=(src, dst), daemon=True).start()

    @staticmethod
    def _pump(src: socket.socket, dst: socket.socket) -> None:
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for sock in (src, dst):
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass


def qmp_command(sock_path: Path, command: str, timeout: float = 5.0, *, arguments: dict[str, Any] | None = None) -> bool:
    """Send one QMP command (after the capabilities handshake); True if QEMU acknowledged it."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(sock_path))
            stream = sock.makefile("rwb", buffering=0)
            greeting = json.loads(stream.readline() or b"{}")
            if "QMP" not in greeting:
                return False
            for execute in ("qmp_capabilities", command):
                payload: dict[str, Any] = {"execute": execute}
                if execute == command and arguments is not None:
                    payload["arguments"] = arguments
                stream.write((json.dumps(payload) + "\n").encode())
                while True:  # skip asynchronous events until the reply arrives
                    line = stream.readline()
                    if not line:
                        return False
                    reply = json.loads(line)
                    if "error" in reply:
                        return False
                    if "return" in reply:
                        break
            return True
    except (OSError, ValueError):
        return False


def common_args(
    vm: dict[str, Any], variant: str | None, dry_run: bool = False, accel: str | None = "kvm",
    headless: bool = False, serial_stdio: bool = False, no_reboot: bool = False,
    allow_missing_disk: bool = False, enable_clipboard: bool = True, spice_port: int | None = None,
    disk_bootindex: int | None = None, network_phase: str = "runtime",
    serial_socket: Path | None = None, serial_log: Path | None = None,
) -> list[str]:
    runtime.require_command("qemu-system-x86_64")
    cpu_model = "host" if accel == "kvm" else vm.get("cpu_model", "max")
    machine_extra, memory_objects, shared_device = shared_dir_args(vm)
    args = ["qemu-system-x86_64", "-m", str(vm["memory_mb"]), "-cpu", cpu_model, "-smp", str(vm["cpus"]),
            "-machine", machine_arg(vm, accel=accel) + machine_extra, "-boot", "menu=on"]
    if accel == "kvm":
        args.insert(1, "-enable-kvm")
    args += memory_objects
    args += firmware_args(vm, dry_run=dry_run)
    args += disk_args(vm, allow_missing=allow_missing_disk, bootindex=disk_bootindex)
    if spice_port is not None:
        args += spice_display_args(spice_port)
    elif headless:
        # Wayland desktops may require a render-capable GPU even without a
        # local window (e.g. virtio-vga-gl with the egl-headless backend).
        args += vm.get("video", {}).get("headless", ["-display", "none"])
        args += ["-monitor", "none"]
        # A QMP socket lets `vmctl stop` ask the guest for an ACPI power-off instead of
        # killing QEMU: a SIGTERM is a power cut and left half-written files behind.
        qmp = qmp_socket_path(vm)
        qmp.parent.mkdir(parents=True, exist_ok=True)
        args += ["-qmp", f"unix:{qmp},server,nowait"]
        # The guest still renders to its emulated VGA; a VNC server on a unix socket keeps
        # that screen reachable, and `vmctl attach` bridges it to a local viewer to watch
        # a headless boot or an unattended install without restarting the VM.
        args += ["-vnc", f"unix:{vnc_socket_path(vm)}"]
    else:
        args += video_args(vm, variant)
    if serial_stdio and serial_socket is not None:
        raise VMError("serial_stdio and serial_socket are mutually exclusive")
    if serial_stdio:
        args += ["-chardev", "stdio,id=char0,signal=off", "-serial", "chardev:char0"]
    elif serial_socket is not None:
        # Background VMs: the serial console stays reachable (`vmctl console`) and everything the
        # guest prints on it is still logged.
        args += serial_socket_args(serial_socket, serial_log)
    if vm.get("usb_tablet"):
        args += ["-usb", "-device", "qemu-xhci", "-device", "usb-tablet"]
    if vm.get("audio"):
        args += ["-device", "ich9-intel-hda", "-device", "hda-duplex"]
    if enable_clipboard and vm.get("clipboard") and not headless and spice_port is None:
        args += ["-device", "virtio-serial-pci", "-chardev", "qemu-vdagent,id=vdagent0,name=vdagent,clipboard=on",
                 "-device", "virtserialport,chardev=vdagent0,name=com.redhat.spice.0"]
    args += network_args(vm, network_phase)
    if shared_device:
        # virtiofsd must be listening before QEMU starts: it is launched here, right before the caller
        # hands these arguments to Popen; it exits by itself when this QEMU goes away.
        ensure_virtiofsd(vm, dry_run=dry_run)
        args += shared_device
    if no_reboot:
        args += ["-no-reboot"]
    return args


def run_and_expect(
    cmd: list[str], expected_text: str, timeout_sec: int,
    auto_inputs: list[tuple[str, str]] | None = None, dry_run: bool = False,
    log_path: Path | None = None, exit_grace_sec: int = 30,
) -> None:
    """Drive QEMU on the serial console until *expected_text* appears, then let the guest power off.

    *exit_grace_sec* is how long the guest gets to exit on its own after the token (Windows
    prints it right before its own shutdown, which takes minutes, hence the parameter).
    Only after that does QEMU get terminated, then killed: see CLAUDE.md, the token/flush rule.
    """
    ui.print_command(cmd)
    if dry_run:
        return
    deadline = time.monotonic() + timeout_sec
    log_file = None
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("w", encoding="utf-8", errors="replace")
        ui.print_kv("serial log", str(log_path))
    process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert process.stdout is not None
    assert process.stdin is not None
    captured: list[str] = []
    sent_inputs: set[tuple[str, str]] = set()
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VMError(f"Timed out after {timeout_sec}s waiting for '{expected_text}'. Captured output:\n{''.join(captured)[-4000:]}")
            events = selector.select(timeout=min(0.2, remaining))
            if events:
                chunk = os.read(process.stdout.fileno(), 4096).decode(errors="replace")
                # An empty read is EOF: QEMU closed stdout (it died or was stopped). select()
                # would report it readable forever, so fall through to the exit check below
                # instead of `continue`, or this loop spins until the timeout.
                if chunk:
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                    if log_file is not None:
                        log_file.write(chunk)
                        log_file.flush()
                    captured.append(chunk)
                    full_output_clean = _strip_ansi("".join(captured))
                    if auto_inputs:
                        for match_text, send_text in auto_inputs:
                            key = (match_text, send_text)
                            if key in sent_inputs:
                                continue
                            if match_text in full_output_clean:
                                process.stdin.write(send_text.encode())
                                process.stdin.flush()
                                sent_inputs.add(key)
                                if log_file is not None:
                                    log_file.write(f"\n[run_and_expect] matched {match_text!r} -> sent {send_text!r}\n")
                                    log_file.flush()
                    if expected_text in full_output_clean:
                        try:
                            process.wait(timeout=exit_grace_sec)
                        except subprocess.TimeoutExpired:
                            process.terminate()
                            try:
                                process.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait(timeout=10)
                        return
                    continue
            if process.poll() is not None:
                remaining_output = process.stdout.read()
                if remaining_output:
                    chunk = remaining_output.decode(errors="replace")
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                    if log_file is not None:
                        log_file.write(chunk)
                        log_file.flush()
                    captured.append(chunk)
                    if expected_text in _strip_ansi("".join(captured)):
                        return
                raise VMError(f"QEMU exited before emitting '{expected_text}'. Captured output:\n{''.join(captured)[-4000:]}")
            time.sleep(min(0.2, remaining))
    finally:
        selector.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        if log_file is not None:
            log_file.close()
