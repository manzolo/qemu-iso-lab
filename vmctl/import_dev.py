"""Import a physical block device into a VM disk image (DESTRUCTIVE)."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from typing import Any

from vmctl import config, disk_inspect, flash, runtime, ui
from vmctl import state
from vmctl.errors import VMError


def validate_import_source(device: str) -> dict[str, Any]:
    info = disk_inspect.inspect_block_device_basic(device)
    if info["is_root_disk"]:
        raise VMError(f"Refusing to read from the host root disk: {device}")
    if info["mountpoints"]:
        raise VMError(f"Refusing mounted source device: {device}")
    return info


def suggested_import_bytes(info: dict[str, Any]) -> tuple[int, bool]:
    children = [child for child in info.get("children") or [] if child.get("type") == "part"]
    if not children:
        return int(info["size"]), False

    logical_sector_size = int(info.get("logical_sector_size", 512) or 512)
    last_partition_end = max(disk_inspect.partition_extent_bytes(child, logical_sector_size) for child in children)
    layout = str(info.get("pttype") or "").lower()
    if layout == "gpt":
        backup_gpt_bytes = disk_inspect.gpt_backup_overhead_bytes(info)
        bounded_size = min(int(info["size"]), runtime.round_up(last_partition_end + backup_gpt_bytes, 1024**2))
        if bounded_size <= 0:
            return int(info["size"]), False
        return bounded_size, bounded_size < int(info["size"])

    bounded_size = min(int(info["size"]), runtime.round_up(last_partition_end, 1024**2))
    if bounded_size <= 0:
        return int(info["size"]), False
    return bounded_size, bounded_size < int(info["size"])


def cmd_import_device(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    disk = vm["disk"]
    disk_path = runtime.resolve_path(disk["path"])

    if args.confirm_device != args.device:
        raise VMError("import-device requires --confirm-device to exactly match --device")

    runtime.require_command("qemu-img")
    runtime.require_command("dd")
    runtime.require_command("lsblk")
    runtime.require_command("findmnt")
    runtime.require_command("sudo")

    info = validate_import_source(args.device)
    import_bytes, is_compacted = suggested_import_bytes(info)
    layout = str(info.get("pttype") or "").lower()
    if is_compacted and layout == "gpt":
        runtime.require_command("sgdisk")

    ui.print_header(f"Import physical disk into VM: {args.vm}")
    ui.print_kv("source", args.device)
    ui.print_kv("size", runtime.format_bytes(info["size"]))
    ui.print_kv("model", info["model"] or "-")
    ui.print_kv("target", ui.pretty_path(disk_path))
    ui.print_kv("format", disk["format"])
    ui.print_kv("import", runtime.format_bytes(import_bytes))
    if disk_path.exists():
        ui.print_status("warn", f"Existing VM disk will be overwritten: {ui.pretty_path(disk_path)}", ok=False)
    if is_compacted:
        if layout == "gpt":
            ui.print_status("ok", "GPT source will be compacted and its backup GPT will be relocated")
        else:
            ui.print_status("ok", "Trailing free space after the last partition will be skipped")

    helper_cmd = [
        "sudo",
        str((state.ROOT / "bin" / "vmctl").resolve()),
        "import-helper",
        "--vm",
        args.vm,
        "--device",
        args.device,
        "--confirm-device",
        args.confirm_device,
    ]
    runtime.run(helper_cmd, dry_run=args.dry_run)
    if args.dry_run:
        ui.print_status("ok", f"Would import {args.device} into {ui.pretty_path(disk_path)} via sudo helper")
    else:
        ui.print_status("ok", f"Imported {args.device} into {ui.pretty_path(disk_path)}")
    return 0


def cmd_import_helper(args: argparse.Namespace) -> int:
    if os.geteuid() != 0:
        raise VMError("import-helper must run as root")
    if args.confirm_device != args.device:
        raise VMError("import-helper requires --confirm-device to exactly match --device")

    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    disk = vm["disk"]
    disk_path = runtime.resolve_path(disk["path"])
    runtime.ensure_parent(disk_path)

    runtime.require_command("qemu-img")
    runtime.require_command("dd")
    runtime.require_command("lsblk")
    runtime.require_command("findmnt")

    info = validate_import_source(args.device)
    if str(info.get("pttype") or "").lower() == "gpt":
        info.update(disk_inspect.maybe_read_gpt_geometry(args.device, int(info.get("logical_sector_size", 512) or 512)))
    import_bytes, is_compacted = suggested_import_bytes(info)
    layout = str(info.get("pttype") or "").lower()
    convert_cmd = ["qemu-img", "convert", "-p", "-f", "raw", "-O", disk["format"]]
    if disk.get("subformat"):
        convert_cmd += ["-o", f"subformat={disk['subformat']}"]
    if is_compacted and layout == "gpt":
        runtime.require_command("sgdisk")
        temp_dir_path = Path(tempfile.mkdtemp(dir=str(disk_path.parent), prefix=f"{disk_path.stem}.import-"))
        keep_temp_dir = False
        try:
            temp_raw = temp_dir_path / "source.raw"
            ui.print_note(f"Stage 1/2: copying {runtime.format_bytes(import_bytes)} from {args.device}")
            runtime.run(
                [
                    "dd",
                    f"if={args.device}",
                    f"of={temp_raw}",
                    "iflag=fullblock,count_bytes",
                    f"count={import_bytes}",
                    "bs=4M",
                    "conv=sparse",
                    "status=progress",
                ],
                dry_run=False,
            )
            ui.print_note("Stage 2/2: repairing GPT backup header and converting to VM disk")
            try:
                runtime.run(["sgdisk", "-e", str(temp_raw)], dry_run=False)
                runtime.run(["sgdisk", "-v", str(temp_raw)], dry_run=False)
            except subprocess.CalledProcessError as exc:
                keep_temp_dir = True
                flash.maybe_restore_sudo_owner_tree(temp_dir_path)
                raise VMError(
                    "Failed to compact GPT for truncated import. "
                    f"Temporary raw image kept at: {temp_raw}"
                ) from exc
            runtime.run_progress(convert_cmd + [str(temp_raw), str(disk_path)], dry_run=False)
        except Exception:
            keep_temp_dir = True
            flash.maybe_restore_sudo_owner_tree(temp_dir_path)
            raise
        finally:
            if not keep_temp_dir and temp_dir_path.exists():
                shutil.rmtree(temp_dir_path, ignore_errors=True)
    elif is_compacted:
        ui.print_note(f"Stage 1/1: copying {runtime.format_bytes(import_bytes)} from {args.device} and converting to VM disk")
        runtime.run_pipeline(
            [
                [
                    "dd",
                    f"if={args.device}",
                    "iflag=fullblock,count_bytes",
                    f"count={import_bytes}",
                    "bs=4M",
                    "status=progress",
                ],
                convert_cmd + ["-", str(disk_path)],
            ],
            dry_run=False,
        )
    else:
        ui.print_note("Stage 1/1: converting full device to VM disk")
        runtime.run_progress(convert_cmd + [args.device, str(disk_path)], dry_run=False)
    flash.maybe_restore_sudo_owner(disk_path)
    flash.maybe_restore_sudo_owner_tree(disk_path.parent)
    ui.print_status("ok", f"Imported {args.device} into {ui.pretty_path(disk_path)}")
    return 0
