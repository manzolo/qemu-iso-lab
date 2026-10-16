"""ADB access and the host scrcpy console for Android guests."""
from __future__ import annotations

import argparse
import math
import subprocess
import time
from typing import Any

from vmctl import config, qemu, runtime, state, ui
from vmctl.errors import VMError


def adb_endpoint(vm: dict[str, Any]) -> str:
    """Use the runtime NIC's forward, so clones keep their independently allocated port."""
    if vm.get("meta", {}).get("family") != "android":
        raise VMError("android-console requires an Android profile (meta.family: android)")
    ports = [fwd["host_port"] for nic in qemu.network_specs(vm)
             if nic["type"] == "user" for fwd in nic["hostfwd"] if fwd["guest_port"] == 5555]
    if len(ports) != 1 or not 1 <= ports[0] <= 65535:
        raise VMError("Android needs exactly one runtime user-network forward to guest TCP 5555")
    return f"127.0.0.1:{ports[0]}"


def wait_for_boot(endpoint: str, timeout: float) -> None:
    """Bound connection attempts and shell probes; never select another attached device."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise VMError("Android boot timeout must be finite and positive")
    runtime.require_command("adb")
    deadline = time.monotonic() + timeout
    detail = "no response"
    reconnect = False
    while time.monotonic() < deadline:
        commands = [["adb", "connect", endpoint],
                    ["adb", "-s", endpoint, "shell", "getprop", "sys.boot_completed"]]
        if reconnect:
            commands.insert(0, ["adb", "-s", endpoint, "reconnect"])
            reconnect = False
        for command in commands:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                result = subprocess.run(command, capture_output=True, text=True,
                                        timeout=min(5.0, remaining), check=False)
            except subprocess.TimeoutExpired:
                detail = "ADB request timed out"
                break
            detail = (result.stderr.strip() + "\n" + result.stdout.strip()).strip()[-500:] or "no response"
            if "unauthorized" in detail.lower():
                raise VMError(f"ADB {endpoint} is unauthorized: authorize this host in the Android VNC console")
            if "offline" in detail.lower():
                reconnect = True
            if "shell" in command and result.returncode == 0 and result.stdout.strip() == "1":
                return
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(1.0, remaining))
    raise VMError(f"Android {endpoint} did not finish booting within {timeout:g}s: {detail}. "
                  "Start the VM and check its VNC console and guest adbd TCP 5555 listener.")


def poweroff(vm: dict[str, Any]) -> None:
    """Ask only this guest to shut down, without waiting for Android to finish booting."""
    endpoint = adb_endpoint(vm)
    runtime.require_command("adb")
    for command in (["adb", "connect", endpoint],
                    ["adb", "-s", endpoint, "shell", "reboot", "-p"]):
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise VMError(f"ADB power-off request failed for {endpoint}: {exc}") from exc
        if result.returncode:
            detail = (result.stderr + "\n" + result.stdout).strip()[-500:]
            raise VMError(f"ADB power-off request failed for {endpoint}: {detail or 'command rejected'}")


def repair_bliss_codecs(endpoint: str, timeout: float) -> None:
    """Apply the opt-in, build-guarded workaround in guest tmpfs, never in the ISO."""
    script = (state.ROOT / "vms/profile-files/android/fix-bliss-codecs.sh").read_text()
    try:
        result = subprocess.run(["adb", "-s", endpoint, "root"], capture_output=True,
                                text=True, timeout=10, check=False)
        if result.returncode:
            raise VMError(f"Cannot request root ADB in Bliss: {result.stderr or result.stdout}")
        wait_for_boot(endpoint, timeout)
        result = subprocess.run(["adb", "-s", endpoint, "shell", "sh"], input=script,
                                capture_output=True, text=True, timeout=15, check=False)
    except subprocess.TimeoutExpired as exc:
        raise VMError("Timed out applying the Bliss codec workaround") from exc
    if result.returncode:
        raise VMError(f"Bliss codec workaround refused or failed: {(result.stderr + result.stdout).strip()}")
    ui.print_note(result.stdout.strip())


def cmd_android_console(args: argparse.Namespace) -> int:
    vm = config.get_vm(config.load_config(), args.vm)
    endpoint = adb_endpoint(vm)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise VMError("Android boot timeout must be finite and positive")
    command = ["scrcpy", "--serial=" + endpoint]
    if args.no_audio:
        command.append("--no-audio")
    if args.record:
        command += ["--record", args.record]
    if args.no_playback:
        if not args.record:
            raise VMError("--no-playback requires --record")
        command.append("--no-playback")
    if args.time_limit is not None:
        if args.time_limit <= 0:
            raise VMError("scrcpy time limit must be positive")
        command += ["--time-limit", str(args.time_limit)]
    if args.dry_run:
        ui.print_command(["adb", "connect", endpoint])
        ui.print_note(f"Wait up to {args.timeout:g}s for sys.boot_completed=1 on {endpoint}")
        if args.fix_bliss_codecs:
            ui.print_note("Apply the temporary Bliss 16.9.7 codec workaround (checks guest build and virtio mapper 4)")
        ui.print_command(command)
        return 0
    runtime.require_command("scrcpy")
    ui.print_note(f"Waiting for Android on {endpoint} (up to {args.timeout:g}s)")
    wait_for_boot(endpoint, args.timeout)
    if args.fix_bliss_codecs:
        repair_bliss_codecs(endpoint, args.timeout)
    runtime.run(command)
    return 0
