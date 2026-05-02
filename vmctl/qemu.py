"""QEMU command-line argument builders and firmware helpers."""
from __future__ import annotations

import os
import selectors
import shutil
import subprocess
import sys
import time
from pathlib import Path

from typing import Any

from vmctl import state, ui, runtime, cloud_init
from vmctl.errors import VMError


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


def disk_args(vm: dict[str, Any], allow_missing: bool = False) -> list[str]:
    disk = vm["disk"]
    disk_path = runtime.resolve_path(disk["path"])
    if not disk_path.exists() and not allow_missing:
        raise VMError(f"Disk image not found: {disk_path}")
    interface = disk.get("interface", "virtio")
    if interface == "virtio":
        return ["-drive", f"file={disk_path},format={disk['format']},if=virtio"]
    if interface == "sata":
        return ["-device", "ich9-ahci,id=ahci0", "-drive", f"id=disk0,file={disk_path},format={disk['format']},if=none", "-device", "ide-hd,drive=disk0,bus=ahci0.0"]
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
    order = video.get("installer_order", ("safe", "std"))
    for candidate in order:
        if candidate in variants:
            return str(candidate)
    return None


def common_args(
    vm: dict[str, Any], variant: str | None, dry_run: bool = False, accel: str | None = "kvm",
    headless: bool = False, serial_stdio: bool = False, no_reboot: bool = False,
    allow_missing_disk: bool = False, enable_clipboard: bool = True, spice_port: int | None = None,
) -> list[str]:
    runtime.require_command("qemu-system-x86_64")
    cpu_model = "host" if accel == "kvm" else vm.get("cpu_model", "max")
    args = ["qemu-system-x86_64", "-m", str(vm["memory_mb"]), "-cpu", cpu_model, "-smp", str(vm["cpus"]),
            "-machine", machine_arg(vm, accel=accel), "-boot", "menu=on"]
    if accel == "kvm":
        args.insert(1, "-enable-kvm")
    args += firmware_args(vm, dry_run=dry_run)
    args += disk_args(vm, allow_missing=allow_missing_disk)
    if spice_port is not None:
        args += spice_display_args(spice_port)
    elif headless:
        args += ["-display", "none", "-monitor", "none"]
    else:
        args += video_args(vm, variant)
    if serial_stdio:
        args += ["-chardev", "stdio,id=char0,signal=off", "-serial", "chardev:char0"]
    if vm.get("usb_tablet"):
        args += ["-usb", "-device", "qemu-xhci", "-device", "usb-tablet"]
    if vm.get("audio"):
        args += ["-device", "ich9-intel-hda", "-device", "hda-duplex"]
    if enable_clipboard and vm.get("clipboard") and not headless and spice_port is None:
        args += ["-device", "virtio-serial-pci", "-chardev", "qemu-vdagent,id=vdagent0,name=vdagent,clipboard=on",
                 "-device", "virtserialport,chardev=vdagent0,name=com.redhat.spice.0"]
    network_mode = vm.get("network", "user")
    if network_mode == "user":
        network_device = vm.get("network_device", "virtio-net-pci")
        netdev = "user,id=n1"
        ssh_cfg = cloud_init.ssh_access_config(vm)
        if ssh_cfg is not None and ssh_cfg.get("ssh_host_port"):
            netdev += f",hostfwd=tcp:127.0.0.1:{int(ssh_cfg['ssh_host_port'])}-:22"
        args += ["-netdev", netdev, "-device", f"{network_device},netdev=n1"]
    else:
        raise VMError(f"Unsupported network mode: {network_mode}")
    if no_reboot:
        args += ["-no-reboot"]
    return args


def run_and_expect(
    cmd: list[str], expected_text: str, timeout_sec: int,
    auto_inputs: list[tuple[str, str]] | None = None, dry_run: bool = False,
) -> None:
    ui.print_command(cmd)
    if dry_run:
        return
    deadline = time.monotonic() + timeout_sec
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
                if chunk:
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                    captured.append(chunk)
                    full_output = "".join(captured)
                    if auto_inputs:
                        for match_text, send_text in auto_inputs:
                            key = (match_text, send_text)
                            if key in sent_inputs:
                                continue
                            if match_text in full_output:
                                process.stdin.write(send_text.encode())
                                process.stdin.flush()
                                sent_inputs.add(key)
                    if expected_text in full_output:
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
                    captured.append(chunk)
                    if expected_text in "".join(captured):
                        return
                raise VMError(f"QEMU exited before emitting '{expected_text}'. Captured output:\n{''.join(captured)[-4000:]}")
            time.sleep(min(0.2, remaining))
    finally:
        selector.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
