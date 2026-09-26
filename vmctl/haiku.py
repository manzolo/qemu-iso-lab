"""Haiku: install from the live medium's own command line, driven over QMP.

Haiku has no answer file and its Installer is graphical only, but the live system carries every
tool an install needs (``mkfs -t bfs``, ``copyattr``, ``makebootable``). The flow therefore boots
the anyboot ISO, and a host-side *pilot* plays the part of the user through QMP: it recognises
each screen by a few pixels of a screendump, clicks "Try Haiku", opens a Terminal from the
Deskbar and types one line that mounts a seed CD and runs ``install.sh``. The script writes BFS
over the whole disk (no partition table, like Haiku's own raw images), copies the real
directories of the live volume (not the packagefs views over them), makes the disk bootable,
installs the project's SSH key, unmounts, then prints the completion token on COM1
(``/dev/ports/pc_serial0``) and powers off: flush, token, natural shutdown, as every flow.

Everything the pilot relies on was measured on R1/beta6 (2026-09-26) at the 1280x800 mode Haiku
picks on ``-vga std``: the coordinates and colours below belong to that pinned ISO.
"""
from __future__ import annotations

import shlex
import threading
import time
from pathlib import Path
from typing import Any, Callable

from vmctl import qemu, runtime, ui
from vmctl.errors import VMError

BOOTSTRAP_COMPLETE_TOKEN = "==> Haiku installation complete!"
BOOTSTRAP_FAILED_TOKEN = "==> Haiku installation FAILED"
SHUTDOWN_GRACE_SEC = 120
SEED_LABEL = "VMCTLSEED"
TARGET_DEVICE = "/dev/disk/scsi/0/0/0/raw"  # the first AHCI port: the profile's SATA disk
VOLUME_NAME = "Haiku"
# Mounted by path, not by name: mountvolume found the live medium already called "Haiku" and
# mounted the new volume on /Haiku1, while the copy went to /Haiku, a directory in the RAM root.
TARGET_MOUNT = "/vmctl-target"
SSH_USER = "user"  # Haiku has one user, uid 0; sshd reads its keys from ~/config/settings/ssh

SCREEN = (1280, 800)
# (x, y) -> expected RGB, each state recognised by all of its points (tolerance PIXEL_TOLERANCE).
WELCOME = {(400, 170): (223, 162, 40), (640, 480): (216, 216, 216)}      # "Welcome to Haiku!" tab + dialog
DESKTOP = {(1270, 60): (217, 217, 217), (640, 480): (51, 102, 152)}      # Deskbar, bare desktop
DESKBAR_MENU = {(1060, 110): (216, 216, 216)}                            # the leaf menu is open
TERMINAL = {(300, 300): (255, 255, 255)}                                 # the Terminal's text area
PIXEL_TOLERANCE = 12
TRY_HAIKU = (903, 582)
DESKBAR_LEAF = (1212, 14)
APPLICATIONS = (1040, 226)
STATE_TIMEOUT_SEC = 300
POLL_SEC = 2.0
KEY_DELAY_SEC = 0.12

# US keymap: the live system starts on US-International, whose ' " ` ~ ^ are dead keys, so the
# typed line avoids them. Shifted characters are followed by a lone shift tap: without it a
# shift release got lost once and the next word arrived in capitals (verified live).
_PLAIN = {" ": "spc", "\n": "ret", "-": "minus", "=": "equal", "[": "bracket_left", "]": "bracket_right",
          ";": "semicolon", "\\": "backslash", ",": "comma", ".": "dot", "/": "slash"}
_SHIFTED = {"!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "&": "7", "*": "8", "(": "9", ")": "0",
            "_": "minus", "+": "equal", "{": "bracket_left", "}": "bracket_right", ":": "semicolon",
            "|": "backslash", "<": "comma", ">": "dot", "?": "slash"}


def haiku_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    cfg = vm.get("haiku_config")
    if cfg is not None and not isinstance(cfg, dict):
        raise VMError("Invalid haiku_config: expected object")
    return cfg


def check_profile(vm_name: str, vm: dict[str, Any]) -> None:
    if vm["firmware"]["type"] != "bios":
        raise VMError(f"{vm_name}: the Haiku install writes BIOS boot code (firmware.type bios)")
    if vm["disk"].get("interface") != "sata":
        raise VMError(f"{vm_name}: the Haiku install targets the first AHCI disk (disk.interface sata)")
    if not vm.get("usb_tablet", True):
        raise VMError(f"{vm_name}: the Haiku pilot clicks through an absolute pointer (usb_tablet)")
    ssh_cfg = vm.get("ssh_provision") or {}
    if ssh_cfg.get("user") != SSH_USER:
        raise VMError(f"{vm_name}: Haiku's only user is '{SSH_USER}' (ssh_provision.user)")


def typed_command() -> str:
    """The one line the pilot types in the live Terminal."""
    return f"mountvolume {SEED_LABEL} && sh /{SEED_LABEL}/install.sh > /dev/ports/pc_serial0 2>&1\n"


def render_install_script(vm_name: str, vm: dict[str, Any], keys: list[str]) -> str:
    check_profile(vm_name, vm)
    target = TARGET_MOUNT
    key_text = shlex.quote("\n".join(keys) + "\n")
    return f'''#!/bin/sh
# vmctl: install Haiku from the live medium onto {TARGET_DEVICE} ({vm_name}).
set -x
fail() {{ set +x; sync; echo "{BOOTSTRAP_FAILED_TOKEN}: $1"; shutdown -q; exit 1; }}
T={target}
mkfs -t bfs -q {TARGET_DEVICE} {VOLUME_NAME} || fail "mkfs"
mkdir -p $T && mount -t bfs {TARGET_DEVICE} $T || fail "mount"
mkdir -p $T/system $T/home/config/settings/ssh || fail "mkdir"
# The real directories only: /boot/system and /boot/home/config are packagefs views, and their
# packaged content comes back from system/packages at the next boot.
copyattr -d -r /boot/system/packages /boot/system/settings $T/system/ || fail "copy system"
for d in non-packaged var cache; do
    [ -d /boot/system/$d ] && {{ copyattr -d -r /boot/system/$d $T/system/ || fail "copy system/$d"; }}
done
copyattr -d /boot/system/haiku_loader.bios_ia32 $T/system/ || fail "copy loader"
copyattr -d -r /boot/home/Desktop /boot/home/mail $T/home/ || fail "copy home"
for d in settings packages non-packaged var cache; do
    [ -d /boot/home/config/$d ] && {{ copyattr -d -r /boot/home/config/$d $T/home/config/ || fail "copy config/$d"; }}
done
# The live desktop's link to the Installer has no job on an installed system.
rm -f $T/home/Desktop/Installer
printf '%s' {key_text} > $T/home/config/settings/ssh/authorized_keys || fail "ssh key"
chmod 600 $T/home/config/settings/ssh/authorized_keys
makebootable $T || fail "makebootable"
sync
unmount $T || fail "unmount"
sync
set +x  # the trace would print the token a moment before the echo itself
echo "{BOOTSTRAP_COMPLETE_TOKEN}"
shutdown -q
'''


def ensure_seed_iso(vm_name: str, vm: dict[str, Any], keys: list[str], dry_run: bool = False) -> Path:
    directory = runtime.resolve_path(vm["disk"]["path"]).parent / "haiku"
    work = directory / "seed"
    dest = directory / "seed.iso"
    script = render_install_script(vm_name, vm, keys)
    if not dry_run:
        work.mkdir(parents=True, exist_ok=True)
        (work / "install.sh").write_text(script)
    runtime.require_command("xorriso")
    runtime.run(["xorriso", "-as", "mkisofs", "-quiet", "-V", SEED_LABEL, "-R", "-J",
                 "-o", str(dest), str(work)], dry_run=dry_run, quiet=True)
    ui.print_status("ok", f"Haiku seed CD: {ui.pretty_path(dest)}")
    return dest


def install_media_args(install_iso: Path, seed_iso: Path) -> list[str]:
    """Both CDs on the disk's AHCI controller, after it: the disk stays /dev/disk/scsi/0/0/0."""
    return ["-drive", f"id=haikucd,file={install_iso},format=raw,if=none,media=cdrom,readonly=on",
            "-device", "ide-cd,drive=haikucd,bus=ahci0.1,bootindex=2",
            "-drive", f"id=haikuseed,file={seed_iso},format=raw,if=none,media=cdrom,readonly=on",
            "-device", "ide-cd,drive=haikuseed,bus=ahci0.2"]


# --- the pilot -----------------------------------------------------------------------------

def read_ppm(path: Path) -> tuple[int, int, bytes]:
    """Width, height and RGB bytes of a binary PPM (what QMP screendump writes)."""
    data = path.read_bytes()
    fields: list[bytes] = []
    index = 0
    while len(fields) < 4:
        while data[index:index + 1].isspace():
            index += 1
        if data[index:index + 1] == b"#":
            index = data.index(b"\n", index) + 1
            continue
        end = index
        while not data[end:end + 1].isspace():
            end += 1
        fields.append(data[index:end])
        index = end
    if fields[0] != b"P6" or fields[3] != b"255":
        raise VMError(f"Unexpected screendump format in {path}")
    return int(fields[1]), int(fields[2]), data[index + 1:]


def matches(frame: tuple[int, int, bytes], expected: dict[tuple[int, int], tuple[int, int, int]]) -> bool:
    width, height, pixels = frame
    if (width, height) != SCREEN:
        return False
    for (x, y), colour in expected.items():
        offset = (y * width + x) * 3
        actual = pixels[offset:offset + 3]
        if len(actual) != 3 or any(abs(a - b) > PIXEL_TOLERANCE for a, b in zip(actual, colour)):
            return False
    return True


def keys_for(char: str) -> list[str]:
    if char.isalpha() and char.isascii():
        return (["shift"] if char.isupper() else []) + [char.lower()]
    if char.isdigit():
        return [char]
    if char in _PLAIN:
        return [_PLAIN[char]]
    if char in _SHIFTED:
        return ["shift", _SHIFTED[char]]
    raise VMError(f"The Haiku pilot cannot type {char!r} (dead key on the live keymap, or unmapped)")


class Pilot:
    """Plays the user in the live session: Try Haiku, a Terminal, the one typed line."""

    def __init__(self, qmp_socket: Path, workdir: Path, log: Callable[[str], None] = ui.print_note,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
        self.qmp_socket = qmp_socket
        self.frame_path = workdir / "pilot.ppm"
        self.log = log
        self.sleep = sleep
        self.clock = clock
        self.error: str | None = None
        self.stop = threading.Event()

    def qmp(self, command: str, arguments: dict[str, Any]) -> None:
        if not qemu.qmp_command(self.qmp_socket, command, arguments=arguments):
            raise VMError(f"QMP {command} was refused")

    def frame(self) -> tuple[int, int, bytes] | None:
        self.frame_path.unlink(missing_ok=True)
        if not qemu.qmp_command(self.qmp_socket, "screendump", arguments={"filename": str(self.frame_path)}):
            return None
        try:
            return read_ppm(self.frame_path)
        except (OSError, ValueError, IndexError, VMError):
            return None

    def wait_for(self, name: str, expected: dict[tuple[int, int], tuple[int, int, int]],
                 timeout_sec: float = STATE_TIMEOUT_SEC) -> None:
        deadline = self.clock() + timeout_sec
        while not self.stop.is_set():
            frame = self.frame()
            if frame is not None and matches(frame, expected):
                self.log(f"Haiku pilot: {name}")
                return
            if self.clock() > deadline:
                raise VMError(f"the live session never showed {name} ({timeout_sec:.0f}s); "
                              f"last screen: {self.frame_path}")
            self.sleep(POLL_SEC)
        raise VMError("stopped")

    def click(self, x: int, y: int) -> None:
        position = [{"type": "abs", "data": {"axis": "x", "value": x * 32767 // SCREEN[0]}},
                    {"type": "abs", "data": {"axis": "y", "value": y * 32767 // SCREEN[1]}}]
        self.qmp("input-send-event", {"events": position})
        self.sleep(0.3)
        for down in (True, False):
            self.qmp("input-send-event", {"events": [{"type": "btn", "data": {"down": down, "button": "left"}}]})
            self.sleep(0.1)

    def press(self, keys: list[str]) -> None:
        self.qmp("send-key", {"keys": [{"type": "qcode", "data": key} for key in keys], "hold-time": 80})
        self.sleep(KEY_DELAY_SEC)

    def type_text(self, text: str) -> None:
        sequence = [keys_for(char) for char in text]  # refuse an untypeable line before typing any of it
        self.press(["shift"])
        for keys in sequence:
            self.press(keys)
            if "shift" in keys:
                self.press(["shift"])

    def drive(self) -> None:
        self.wait_for("the Welcome dialog", WELCOME)
        self.click(*TRY_HAIKU)
        self.wait_for("the desktop", DESKTOP)
        self.click(*DESKBAR_LEAF)
        self.wait_for("the Deskbar menu", DESKBAR_MENU, timeout_sec=30)
        self.click(*APPLICATIONS)
        self.sleep(4)  # Tracker opens the Applications folder; its type-ahead selects by name
        self.type_text("Terminal")
        self.press(["ret"])
        self.wait_for("a Terminal", TERMINAL, timeout_sec=60)
        self.sleep(1)
        self.type_text(typed_command())
        self.log("Haiku pilot: install command typed; waiting for the serial token")

    def run(self) -> None:
        try:
            self.drive()
        except VMError as exc:
            if self.stop.is_set():
                return
            self.error = str(exc)
            ui.print_status("fail", f"Haiku pilot: {self.error}", ok=False)
            # Ending QEMU makes run_and_expect return at once instead of at its timeout.
            qemu.qmp_command(self.qmp_socket, "quit")

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.run, name="haiku-pilot", daemon=True)
        thread.start()
        return thread
