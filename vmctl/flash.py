"""Flash a VM disk image onto a physical block device (DESTRUCTIVE)."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from typing import Any

from vmctl import config, disk_inspect, qemu, runtime, ui
from vmctl import state
from vmctl.errors import VMError


def validate_flash_target(vm: dict[str, Any], disk_path: Path, device: str, force_target: bool = False) -> tuple[dict[str, Any], str | None, int]:
    source_layout = disk_inspect.partition_layout(disk_path)
    source_info = runtime.image_info(disk_path)
    virtual_size = int(source_info.get("virtual-size", 0) or 0)
    disk_format = str(vm["disk"].get("format", "")).lower()
    allow_unknown_layout = qemu.is_container_disk_format(disk_format)

    if vm["firmware"]["type"] == "efi" and source_layout != "gpt" and not (allow_unknown_layout and source_layout is None):
        raise VMError(f"EFI guest requires a GPT VM disk before flash; detected: {source_layout or 'unknown'}")
    if vm["firmware"]["type"] == "bios" and source_layout not in {"dos", "gpt"} and not (allow_unknown_layout and source_layout is None):
        raise VMError(f"BIOS guest requires an MBR/GPT VM disk before flash; detected: {source_layout or 'unknown'}")

    info = disk_inspect.inspect_block_device(device)
    if info["is_root_disk"]:
        raise VMError(f"Refusing to overwrite the host root disk: {device}")
    if info["mountpoints"]:
        raise VMError(f"Refusing mounted target device: {device}")
    if info["children"] and not force_target:
        raise VMError(f"Refusing device with existing partitions: {device}")
    if info["signatures"] and not force_target:
        sig_list = ", ".join(sorted({str(item.get('type', '?')) for item in info["signatures"]}))
        raise VMError(f"Refusing non-empty target device '{device}' with signatures: {sig_list}")
    if not info["is_empty"] and not force_target:
        raise VMError(f"Refusing target device that does not look empty: {device}")
    if virtual_size and info["size"] < virtual_size:
        raise VMError(
            f"Target device '{device}' is smaller than the VM disk "
            f"({runtime.format_bytes(info['size'])} < {runtime.format_bytes(virtual_size)})"
        )

    return info, source_layout, virtual_size


def maybe_restore_sudo_owner(path: Path) -> None:
    sudo_uid = os.environ.get("SUDO_UID")
    sudo_gid = os.environ.get("SUDO_GID")
    if not sudo_uid or not sudo_gid:
        return
    try:
        os.chown(path, int(sudo_uid), int(sudo_gid))
    except (OSError, ValueError):
        return


def maybe_restore_sudo_owner_tree(path: Path) -> None:
    sudo_uid = os.environ.get("SUDO_UID")
    sudo_gid = os.environ.get("SUDO_GID")
    if not sudo_uid or not sudo_gid:
        return
    try:
        uid = int(sudo_uid)
        gid = int(sudo_gid)
    except ValueError:
        return

    current = path
    while True:
        try:
            os.chown(current, uid, gid)
        except OSError:
            pass
        if current == state.ROOT or current.parent == current:
            break
        current = current.parent


def cmd_flash(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    disk = vm["disk"]
    disk_path = runtime.resolve_path(disk["path"])

    if not disk_path.is_file():
        raise VMError(f"VM disk image not found: {disk_path}")
    if args.confirm_device != args.device:
        raise VMError("flash requires --confirm-device to exactly match --device")

    runtime.require_command("qemu-img")
    runtime.require_command("lsblk")
    runtime.require_command("wipefs")
    runtime.require_command("findmnt")
    runtime.require_command("sudo")

    helper_will_recheck = False
    try:
        info, source_layout, virtual_size = validate_flash_target(vm, disk_path, args.device, force_target=args.force_target)
    except VMError as exc:
        if "Need elevated privileges to inspect block device signatures" not in str(exc):
            raise
        helper_will_recheck = True
        source_layout = disk_inspect.partition_layout(disk_path)
        virtual_size = int(runtime.image_info(disk_path).get("virtual-size", 0) or 0)
        disk_format = str(disk.get("format", "")).lower()
        allow_unknown_layout = qemu.is_container_disk_format(disk_format)
        if vm["firmware"]["type"] == "efi" and source_layout != "gpt" and not (allow_unknown_layout and source_layout is None):
            raise VMError(f"EFI guest requires a GPT VM disk before flash; detected: {source_layout or 'unknown'}")
        if vm["firmware"]["type"] == "bios" and source_layout not in {"dos", "gpt"} and not (allow_unknown_layout and source_layout is None):
            raise VMError(f"BIOS guest requires an MBR/GPT VM disk before flash; detected: {source_layout or 'unknown'}")
        info = disk_inspect.inspect_block_device_basic(args.device)
        if info["is_root_disk"]:
            raise VMError(f"Refusing to overwrite the host root disk: {args.device}")
        if info["mountpoints"]:
            raise VMError(f"Refusing mounted target device: {args.device}")
        if (info["children"] or not args.force_target) and info["children"]:
            raise VMError(f"Refusing device with existing partitions before sudo validation: {args.device}")

    ui.print_header(f"Flash VM to physical disk: {args.vm}")
    ui.print_kv("source", ui.pretty_path(disk_path))
    ui.print_kv("format", disk["format"])
    ui.print_kv("layout", source_layout or "unknown")
    ui.print_kv("image", runtime.format_bytes(virtual_size))
    ui.print_kv("target", args.device)
    ui.print_kv("size", runtime.format_bytes(info["size"]))
    ui.print_kv("model", info["model"] or "-")
    if source_layout is None and qemu.is_container_disk_format(str(disk.get("format", "")).lower()):
        ui.print_status("warn", "Guest partition layout is hidden inside the disk container; proceeding with caution", ok=False)
    if args.force_target:
        ui.print_status("warn", "Force mode enabled: existing partition table/signatures will be wiped", ok=False)
    if helper_will_recheck:
        ui.print_status("warn", "Full target validation will run after sudo elevation", ok=False)
    if vm["firmware"]["type"] == "bios" and source_layout == "gpt":
        ui.print_status("warn", "BIOS VM disk uses GPT; flashing as-is", ok=False)

    helper_cmd = [
        "sudo",
        _bin_vmctl_path(),
        "flash-helper",
        "--vm",
        args.vm,
        "--device",
        args.device,
        "--confirm-device",
        args.confirm_device,
    ]
    if args.force_target:
        helper_cmd.append("--force-target")
    runtime.run(helper_cmd, dry_run=args.dry_run)
    if args.dry_run:
        ui.print_status("ok", f"Would flash {ui.pretty_path(disk_path)} to {args.device} via sudo helper")
    else:
        ui.print_status("ok", f"Flashed {ui.pretty_path(disk_path)} to {args.device}")
    return 0


def cmd_flash_helper(args: argparse.Namespace) -> int:
    if os.geteuid() != 0:
        raise VMError("flash-helper must run as root")
    if args.confirm_device != args.device:
        raise VMError("flash-helper requires --confirm-device to exactly match --device")

    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    disk = vm["disk"]
    disk_path = runtime.resolve_path(disk["path"])
    if not disk_path.is_file():
        raise VMError(f"VM disk image not found: {disk_path}")

    runtime.require_command("qemu-img")
    runtime.require_command("lsblk")
    runtime.require_command("wipefs")
    runtime.require_command("findmnt")

    info, _, virtual_size = validate_flash_target(vm, disk_path, args.device, force_target=args.force_target)
    if args.force_target:
        for child in info["children"]:
            child_path = child.get("path")
            if child_path:
                runtime.run(["wipefs", "-a", "-f", child_path], dry_run=False, quiet=True)
        runtime.run(["wipefs", "-a", "-f", args.device], dry_run=False, quiet=True)
        runtime.reread_partition_table(args.device, dry_run=False)
    runtime.run(
        [
            "qemu-img",
            "convert",
            "-n",
            "-p",
            "-f",
            disk["format"],
            "-O",
            "raw",
            str(disk_path),
            args.device,
        ],
        dry_run=False,
    )
    if virtual_size and info["size"] > virtual_size:
        runtime.reread_partition_table(args.device, dry_run=False)
        flashed_info = disk_inspect.inspect_block_device_basic(args.device)
        if str(flashed_info.get("pttype") or "").lower() == "gpt":
            runtime.require_command("sgdisk")
            runtime.run(["sgdisk", "-e", args.device], dry_run=False, quiet=True)
            runtime.reread_partition_table(args.device, dry_run=False)
    runtime.run(["sync"], dry_run=False, quiet=True)
    return 0


def _bin_vmctl_path() -> str:
    """Resolve the absolute path to the bin/vmctl entry-point script.

    Used to re-exec the helper subcommand under sudo from the same install.
    """
    return str((state.ROOT / "bin" / "vmctl").resolve())
