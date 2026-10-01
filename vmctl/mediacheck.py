"""vmctl media-check: does a manual profile's medium still boot?

A manual profile (a live system, the manual twin of an automated one) has nothing the matrix can
install or verify, and its medium says nothing on the serial console, so ``boot-check`` has no
token to wait for. This check boots the ISO headless with ``-snapshot`` on a scratch disk and
scratch EFI variables in a short directory under the system temp dir (``work_dir``, removed afterwards: the VM's own
disk is never attached), and watches two things for ``timeout`` seconds:

- the serial console, where OVMF reports a medium it cannot boot (``BdsDxe: failed to load``,
  ``No bootable option``): a FAIL at once;
- the screen, one QMP screendump every ``INTERVAL_SEC``: a graphical frame (``recorder.frame_kind``)
  that then holds still is a PASS; a console that changed at least ``TEXT_BOOT_FRAMES`` times is a
  text-mode boot, also a PASS; a screen that never got past a handful of console frames (a
  firmware message on black) is a FAIL.

The last frame is kept as ``artifacts/<vm>/logs/media-check.png`` and handed to the report.
"""
from __future__ import annotations

import copy
import re
import hashlib
import selectors
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vmctl import iso, qemu, recorder, runtime, ui
from vmctl.errors import VMError

DEFAULT_TIMEOUT_SEC = 180
INTERVAL_SEC = 5.0
MIN_SEC = 45  # a graphical screen must also hold this long into the boot: splash screens change
SETTLED_CAPTURES = 3  # identical captures in a row that make a graphical screen "settled"
TEXT_BOOT_FRAMES = 4
FIRMWARE_FAILURES = ("No bootable option", "No bootable device", "BdsDxe: failed to load", "Boot Failed", "Boot failed",
                     "Could not read from CDROM")
# SeaBIOS says nothing on the serial port: its log goes to the debugcon port (0x402), where a CD
# it could not boot reads "Boot failed: Could not read from CDROM" and the next device, the NIC's
# iPXE, starts as "Booting from ROM". Without this a BIOS profile on a medium that does not boot
# passed as a "text-mode boot", iPXE's DHCP attempts changing the screen (negative check, 2026-09-30).
SEABIOS_FALLTHROUGH = ("Booting from ROM",)
# The medium's own boot loader, seen on the serial console: Linux Mint 22.3's GRUB menu has no
# timeout and waits for Enter forever, a medium that boots all the same (night of 2026-09-30).
# A boot menu without a timeout (KDE neon, the GRUB 2.02 of Ubuntu 14.04/16.04 desktop media, Mint
# 22.3) holds one console frame forever, often drawn on the screen only. After NUDGE_AFTER_SEC of the
# same console frame the check presses Enter once, as a person would: the default entry boots and
# the rest of the watch sees the real boot. Nothing is at stake, the disk is a -snapshot scratch.
NUDGE_AFTER_SEC = 30
LOADER_BANNERS = ("GNU GRUB", "ISOLINUX", "SYSLINUX", "systemd-boot")
ANSI = re.compile(r"\x1b\[[0-9;?=]*[A-Za-z]|\x1b[()][A-Z0-9]")
# the manual reasons whose medium is an ISO worth booting (config.MANUAL_REASONS): templates import
# a device, image profiles need a build, ci has its own serial boot-check
ELIGIBLE_REASONS = ("live", "twin", "todo")


@dataclass
class Outcome:
    passed: bool
    detail: str
    seconds: float = 0.0
    final_png: Path | None = None
    frames: int = 0
    graphic: int = 0
    serial_tail: list[str] = field(default_factory=list)


def eligible(vm: dict[str, Any]) -> bool:
    meta = vm.get("meta") or {}
    return (meta.get("status") == "manual" and meta.get("manual") in ELIGIBLE_REASONS
            and bool(vm.get("iso")) and not vm.get("disk_image"))


def work_dir(vm_name: str) -> Path:
    """Short on purpose: QMP and VNC live in <work_dir>/runtime/, and a unix socket path stops at
    107 bytes. Under artifacts/<vm>/media-check/ of the batch worktree the long names reached 108
    and 113: QEMU refused the socket or the captures never connected, and 12 media checks of the
    night of 2026-09-30 failed on a medium that boots (0 frames, "QEMU exited after 0 s")."""
    digest = hashlib.sha1(str(runtime.vm_artifact_base(vm_name)).encode()).hexdigest()[:10]
    return Path(tempfile.gettempdir()) / f"vmctl-mc-{digest}"


def scratch_profile(vm_name: str, vm: dict[str, Any]) -> dict[str, Any]:
    """The profile with its disk and EFI variables moved into the scratch directory: every path the
    boot writes (sockets, vars) lands there, and the VM's own disk is not attached at all."""
    scratch = copy.deepcopy(vm)
    base = work_dir(vm_name)
    scratch["disk"] = {**scratch["disk"], "path": str(base / "disk.qcow2"), "format": "qcow2"}
    scratch["disk"].pop("subformat", None)
    firmware = scratch.get("firmware")
    if isinstance(firmware, dict) and firmware.get("vars_path"):
        scratch["firmware"] = {**firmware, "vars_path": str(base / "OVMF_VARS.fd")}
    scratch.pop("extra_disks", None)
    scratch.pop("shared_dir", None)  # nothing of the host is lent to a medium under test
    return scratch


def failure_line(text: str, needles: tuple[str, ...]) -> str | None:
    """The first complete line naming a firmware failure, escape sequences removed (OVMF paints
    its console on the serial port: the raw line starts with a dozen cursor moves)."""
    for line in ANSI.sub("\n", text).splitlines(keepends=True):
        if any(needle in line for needle in needles) and line.endswith(("\n", "\r")):
            return line.strip()
    return None


def classify(frames: int, graphic: int, settled: bool, early_exit: str | None, firmware_line: str | None,
             seconds: float, loader: str | None = None) -> tuple[bool, str]:
    """The verdict from what the watch saw (pure: tested on its own)."""
    if firmware_line:
        return False, f"the firmware could not boot the medium: {firmware_line.strip()}"
    if early_exit:
        return False, early_exit
    if graphic:
        state = "settled" if settled else "still changing"
        return True, f"graphical screen after {round(seconds)} s ({graphic} graphical of {frames} distinct frames, {state})"
    if frames >= TEXT_BOOT_FRAMES:
        return True, f"text-mode boot: {frames} distinct console frames in {round(seconds)} s"
    if loader:
        return True, f"the medium's boot loader ({loader}) is up and waits at its menu"
    return False, f"the screen never got past the firmware: {frames} distinct console frame(s) in {round(seconds)} s"


def check_medium(vm_name: str, vm: dict[str, Any], timeout_sec: int = DEFAULT_TIMEOUT_SEC, dry_run: bool = False,
        keep_frame: bool = True) -> Outcome:
    iso_path = iso.ensure_iso(vm, dry_run=dry_run)
    scratch = scratch_profile(vm_name, vm)
    base = work_dir(vm_name)
    if not dry_run:
        shutil.rmtree(base, ignore_errors=True)
        base.mkdir(parents=True)
        runtime.run(["qemu-img", "create", "-q", "-f", "qcow2", str(base / "disk.qcow2"), str(vm["disk"].get("size") or "8G")], quiet=True)
        firmware = scratch.get("firmware") or {}
        if firmware.get("vars_template") and firmware.get("vars_path"):
            shutil.copyfile(runtime.resolve_path(firmware["vars_template"]), runtime.resolve_path(firmware["vars_path"]))
    command = qemu.common_args(scratch, None, dry_run=dry_run, headless=True, serial_stdio=True, no_reboot=True,
                               enable_clipboard=False, network_phase="install", allow_missing_disk=dry_run)
    command += ["-snapshot", "-boot", "once=d", "-cdrom", str(iso_path)]
    bios_log = runtime.vm_artifact_base(vm_name) / "logs" / "media-check-seabios.log"
    if (vm.get("firmware") or {}).get("type") != "efi":
        command += ["-chardev", f"file,id=seabios,path={bios_log}", "-device", "isa-debugcon,iobase=0x402,chardev=seabios"]
    ui.print_command(command)
    if dry_run:
        return Outcome(True, "dry run")
    logs = runtime.vm_artifact_base(vm_name) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    serial_log = logs / "media-check-serial.log"
    final_png = logs / "media-check.png"
    sock, vnc = qemu.qmp_socket_path(scratch), qemu.vnc_socket_path(scratch)
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    started = time.monotonic()
    serial = b""
    seen: set[str] = set()
    frames = graphic = same = 0
    last_hash = ""
    last_png: bytes | None = None
    early_exit: str | None = None
    firmware_line: str | None = None
    settled = False
    nudged = False
    still_since = started
    next_capture = started + INTERVAL_SEC
    try:
        with serial_log.open("wb") as log:
            while True:
                now = time.monotonic()
                elapsed = now - started
                if elapsed >= timeout_sec:
                    break
                for key, _ in selector.select(timeout=0.5):
                    chunk = process.stdout.read1(65536) if hasattr(process.stdout, "read1") else b""
                    if chunk:
                        serial += chunk
                        log.write(chunk)
                        log.flush()
                firmware_line = failure_line(serial.decode("utf-8", "replace"), FIRMWARE_FAILURES)
                if firmware_line is None and bios_log.exists():
                    firmware_log = bios_log.read_text(encoding="utf-8", errors="replace")
                    firmware_line = failure_line(firmware_log, FIRMWARE_FAILURES + SEABIOS_FALLTHROUGH)
                if firmware_line:
                    break
                if process.poll() is not None:
                    early_exit = f"QEMU exited after {round(elapsed)} s (status {process.returncode})"
                    break
                if now < next_capture:
                    continue
                next_capture = now + INTERVAL_SEC
                ppm = recorder.capture(sock, vnc)
                if ppm is None:
                    continue
                digest = hashlib.sha1(ppm).hexdigest()
                if digest == last_hash:
                    same += 1
                else:
                    same, last_hash, still_since = 0, digest, now
                if digest not in seen:
                    seen.add(digest)
                    frames += 1
                    try:
                        png, _, _ = recorder.ppm_to_png(ppm)
                    except (ValueError, IndexError):
                        continue
                    last_png = png
                    scratch_png = base / "frame.png"
                    scratch_png.write_bytes(png)
                    if recorder.frame_kind(scratch_png) == "graphic":
                        graphic += 1
                if not nudged and not graphic and frames and now - still_since >= NUDGE_AFTER_SEC:
                    nudged = True
                    ui.print_note("The medium holds one screen (a boot menu without a timeout?): pressing Enter once")
                    try:
                        with qemu.QMP_LOCK:
                            qemu.qmp_execute(sock, "send-key", arguments={"keys": [{"type": "qcode", "data": "ret"}]}, timeout=5.0)
                    except (VMError, OSError):
                        pass
                if graphic and same >= SETTLED_CAPTURES - 1 and elapsed >= MIN_SEC:
                    settled = True
                    break
    finally:
        seconds = time.monotonic() - started
        if process.poll() is None:
            try:
                qemu.qmp_execute(sock, "quit", timeout=5.0)
            except (VMError, OSError):
                pass
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        selector.close()
        if last_png is not None and keep_frame:
            final_png.write_bytes(last_png)
        shutil.rmtree(base, ignore_errors=True)
    text = ANSI.sub("\n", serial.decode("utf-8", "replace"))
    loader = next((banner for banner in LOADER_BANNERS if banner in text), None)
    passed, detail = classify(frames, graphic, settled, early_exit, firmware_line, seconds, loader)
    if nudged:
        detail += " (after one Enter at a screen that held still)"
    tail = [line for line in serial.decode("utf-8", "replace").splitlines() if line.strip()][-10:]
    return Outcome(passed, detail, seconds, final_png if last_png is not None else None, frames, graphic, tail)
