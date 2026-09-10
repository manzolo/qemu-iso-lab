"""Flash a VM disk image onto a physical block device (DESTRUCTIVE)."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from typing import Any

from vmctl import config, disk_inspect, qemu, runtime, ui
from vmctl import state
from vmctl.errors import VMError


MIN_EXPANSION_PROMPT_BYTES = 1024**3
REREAD_ATTEMPTS = 5


def _settle_udev() -> None:
    if shutil.which("udevadm") is None:
        return
    try:
        runtime.run(["udevadm", "settle", "--timeout=15"], quiet=True, show_command=False)
    except subprocess.CalledProcessError:
        return  # a settle timeout is not fatal: the reread below decides


def _reread_partitions(device: str) -> None:
    """Strict partition-table reread for the resize steps.

    Right after a table write (sgdisk -e, sfdisk) udev and udisks probe the new
    partitions and hold them open for a moment; BLKRRPART then fails with EBUSY
    (seen live: the second reread one second after sgdisk -e). Wait for udev,
    retry a few times, and turn a real mount into the explicit unmount message.
    """
    for attempt in range(1, REREAD_ATTEMPTS + 1):
        _settle_udev()
        try:
            runtime.run(["blockdev", "--rereadpt", device], quiet=True, show_command=attempt == 1)
            return
        except subprocess.CalledProcessError:
            _require_unmounted_flash_target(device)
            if attempt == REREAD_ATTEMPTS:
                raise VMError(
                    f"Kernel did not reread the partition table for {device} after {attempt} attempts: "
                    "a partition is still held open (udev/udisks probe or another process)"
                )
            time.sleep(1)


def add_expansion_options(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--expand", dest="expand", action="store_true", default=None,
                       help="expand a supported final NTFS partition after copying, without asking")
    group.add_argument("--no-expand", dest="expand", action="store_false",
                       help="preserve partition/filesystem sizes after copying, without asking")


def add_copy_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--allocated-only", action="store_true",
        help="copy allocated filesystem blocks using partclone and ddrescue; "
             "free filesystem space keeps its previous bytes (requires --force-target)",
    )


def require_allocated_only(force_target: bool) -> None:
    from vmctl import flash_allocated

    flash_allocated.require_tools(force_target)


def _require_unmounted_flash_target(device: str) -> None:
    info = disk_inspect.inspect_block_device_basic(device)
    if info["is_root_disk"]:
        raise VMError(f"Refusing to resize the host root disk: {device}")
    if info["mountpoints"]:
        mounts = ", ".join(info["mountpoints"])
        raise VMError(
            f"Refusing to resize mounted target {device} ({mounts}). "
            "Desktop automount may have mounted the copied partitions. "
            "Close files on the target, unmount its partitions with udisksctl unmount -b <partition> "
            "or sudo umount <mountpoint>, and disable desktop automount before retrying."
        )


def _partition_geometry(table: dict[str, Any]) -> list[tuple[str, int, int, str, str]]:
    """Compare partition identity and extents, allowing cosmetic sfdisk changes."""
    return sorted(
        (str(part["node"]), int(part["start"]), int(part["size"]),
         str(part["type"]).lower(), str(part["uuid"]).lower())
        for part in table["partitions"]
    )


def _restore_flash_partition_table(
    device: str, backup: str, original: dict[str, Any], expanded: dict[str, Any],
) -> None:
    """Restore only before any filesystem write, with known, unmounted geometry."""
    _require_unmounted_flash_target(device)
    current = json.loads(runtime.run_output(["sfdisk", "--json", device]))["partitiontable"]
    if _partition_geometry(current) == _partition_geometry(original):
        return
    if _partition_geometry(current) != _partition_geometry(expanded):
        raise VMError("Partition geometry changed unexpectedly; automatic restore refused")
    runtime.run(["sfdisk", "--wipe", "never", "--wipe-partitions", "never", device], stdin_text=backup)
    restored = json.loads(runtime.run_output(["sfdisk", "--json", device]))["partitiontable"]
    if _partition_geometry(restored) != _partition_geometry(original):
        raise VMError("Partition table restore could not be verified")
    _reread_partitions(device)


def grow_flashed_ntfs(device: str, backup_path: Path, expand: bool | None = None) -> None:
    """Offer optional NTFS growth after GPT repair; no consent means no resize."""
    table = json.loads(runtime.run_output(["sfdisk", "--json", device]))["partitiontable"]
    partitions = table.get("partitions", [])
    if table.get("label") != "gpt" or not partitions:
        ui.print_status("warn", "No GPT data partition to expand", ok=False)
        return
    last = max(partitions, key=lambda part: int(part["start"]) + int(part["size"]))
    new_size = int(table["lastlba"]) - int(last["start"]) + 1
    if new_size <= int(last["size"]):
        ui.print_status("ok", "Last partition already uses the available disk space")
        return
    free_bytes = (new_size - int(last["size"])) * int(table["sectorsize"])
    free_space = runtime.format_bytes(free_bytes)
    partition_type = str(last.get("type", "")).lower()
    if partition_type != "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7":
        reason = (
            "is a Windows recovery partition"
            if partition_type == "de94bba4-06d1-4d40-a16a-bfd50179d6ac"
            else "is not a supported Windows data partition"
        )
        size = runtime.format_bytes(int(last["size"]) * int(table["sectorsize"]))
        ui.print_status(
            "warn",
            f"Automatic expansion skipped: the last partition {last['node']} ({size}) {reason}.",
            ok=False,
        )
        ui.print_kv("unallocated", f"{free_space} (partition sizes preserved)")
        ui.print_note("To use this space, review the copied disk's layout with a partition editor; no need to flash again.")
        return
    node = str(last["node"])
    match = re.fullmatch(re.escape(device) + r"p?([1-9][0-9]*)", node)
    if not match:
        raise VMError(f"Unexpected partition path for {device}: {node}")
    try:
        fstype = runtime.run_output(["blkid", "-p", "-s", "TYPE", "-o", "value", node]).strip().lower()
    except subprocess.CalledProcessError as exc:
        if exc.returncode != 2:  # blkid found no recognizable filesystem.
            raise
        fstype = ""
    if fstype != "ntfs":
        ui.print_status("warn", f"{free_space} left unallocated: {node} uses {fstype or 'an unknown filesystem'}; expansion supports NTFS only", ok=False)
        return
    if expand is False or (expand is None and not sys.stdin.isatty()):
        ui.print_status("ok", f"{free_space} left unallocated; partition sizes preserved. To expand this copy later, enlarge the partition with a partition editor, then run ntfsresize (dry-run first).")
        return
    if expand is None and free_bytes < MIN_EXPANSION_PROMPT_BYTES:
        ui.print_status("ok", f"{free_space} left unallocated (below the 1 GiB prompt threshold); partition sizes preserved")
        return
    # Unlike the best-effort refresh used by flashing, resizing must not use
    # stale kernel partition nodes after a failed reread.
    _reread_partitions(device)
    _require_unmounted_flash_target(device)
    if shutil.which("ntfsresize") is None:
        ui.print_status("warn", f"{free_space} left unallocated: install ntfs-3g (ntfsresize) for NTFS expansion", ok=False)
        return

    # Check the original filesystem before changing its partition. Never force
    # a dirty, hibernated or otherwise unsafe Windows volume through checks.
    try:
        runtime.run(["ntfsresize", "--check", node], quiet=True, show_command=False, capture_error_output=True)
        runtime.run(["ntfsresize", "--no-action", node], quiet=True, show_command=False, capture_error_output=True)
    except subprocess.CalledProcessError as exc:
        detail = " ".join(str(exc.stderr or exc.stdout or exc).split())
        ui.print_status("warn", f"{free_space} left unallocated: ntfsresize rejected {node}; no expansion offered. {detail}", ok=False)
        return
    if expand is None:
        free_gib = f"{free_bytes / 1024**3:.1f}"
        size_gib = f"{int(last['size']) * int(table['sectorsize']) / 1024**3:.1f}"
        if not runtime.confirm_default_no(
            f"The disk has {free_gib} GiB unallocated after the last partition "
            f"({Path(node).name}, NTFS {size_gib} GiB).\n"
            "Expand the partition and filesystem to use the entire disk?"
        ):
            ui.print_status("ok", f"{free_space} left unallocated; partition and filesystem sizes preserved")
            return
    runtime.ensure_parent(backup_path)
    backup = runtime.run_output(["sfdisk", "--dump", device])
    backup_path.write_text(backup, encoding="utf-8")
    maybe_restore_sudo_owner(backup_path)
    maybe_restore_sudo_owner_tree(backup_path.parent)
    ui.print_kv("partition table backup", ui.pretty_path(backup_path))
    expanded = dict(table, partitions=[dict(part, size=new_size) if part == last else part for part in partitions])
    filesystem_write_started = False
    # Recheck after the potentially long filesystem check and backup: a desktop
    # may have mounted the target since the first inspection.
    _require_unmounted_flash_target(device)
    current = json.loads(runtime.run_output(["sfdisk", "--json", device]))["partitiontable"]
    if _partition_geometry(current) != _partition_geometry(table):
        raise VMError("Partition geometry changed while waiting; expansion stopped")
    try:
        runtime.run(
            ["sfdisk", "--wipe", "never", "--wipe-partitions", "never", "-N", match[1], device],
            stdin_text=f"size={new_size}\n",
        )
        _reread_partitions(device)
        resized = json.loads(runtime.run_output(["sfdisk", "--json", device]))["partitiontable"]
        if _partition_geometry(resized) != _partition_geometry(expanded):
            raise VMError("Unexpected partition geometry after expansion; filesystem resize stopped")
        actual_bytes = int(runtime.run_output(["blockdev", "--getsize64", node]).strip())
        if actual_bytes != new_size * int(table["sectorsize"]):
            raise VMError(f"Kernel partition size is stale for {node}; filesystem resize stopped")
        _require_unmounted_flash_target(device)
        runtime.run(["ntfsresize", "--no-action", node])
        _require_unmounted_flash_target(device)
        filesystem_write_started = True
        runtime.run(["ntfsresize", node], stdin_text="y\n")
    except (VMError, subprocess.CalledProcessError, ValueError, KeyError, OSError) as exc:
        if filesystem_write_started:
            raise VMError(
                f"NTFS resize failed: {exc}. The filesystem may have been modified; "
                "the expanded partition was retained. Do not restore the smaller partition table; "
                "inspect/repair the filesystem before retrying its resize."
            ) from exc
        try:
            _restore_flash_partition_table(device, backup, table, expanded)
        except (VMError, subprocess.CalledProcessError, ValueError, KeyError, OSError) as restore_exc:
            raise VMError(
                f"Expansion stopped: {exc}. Automatic partition restore did not complete: {restore_exc}. "
                f"No NTFS resize writes were started; partition table backup: {backup_path}"
            ) from exc
        raise VMError(f"Expansion stopped: {exc}. Original partition geometry restored; NTFS was not modified.") from exc
    ui.print_status("ok", f"Expanded {node} and its NTFS filesystem to use the remaining disk space")


def _device_has_gpt_metadata(info: dict[str, Any]) -> bool:
    if str(info.get("pttype") or "").lower() == "gpt":
        return True
    for item in info.get("signatures") or []:
        if str(item.get("type") or "").lower() == "gpt":
            return True
    return False


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

    allocated_only = bool(getattr(args, "allocated_only", False))
    if allocated_only:
        require_allocated_only(args.force_target)

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
    ui.print_kv("mode", "allocated filesystem blocks (partclone + ddrescue)" if allocated_only else "every sector")
    if source_layout is None and qemu.is_container_disk_format(str(disk.get("format", "")).lower()):
        ui.print_note("Guest partition layout will be detected on the target disk after copying the image.")
    if args.force_target:
        ui.print_status("warn", "Force mode enabled: existing partition table/signatures will be wiped", ok=False)
    if allocated_only:
        ui.print_status("warn", "Allocated-only copy: free filesystem blocks keep their previous bytes; a temporary sparse RAW copy needs local disk space", ok=False)
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
    if allocated_only:
        helper_cmd.append("--allocated-only")
    expand = getattr(args, "expand", None)
    # sudo may allocate a PTY even when the caller has piped stdin. Preserve the
    # caller's noninteractive default instead of asking inside that new PTY.
    if expand is None and not sys.stdin.isatty():
        expand = False
    if expand is not None:
        helper_cmd.append("--expand" if expand else "--no-expand")
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

    allocated_only = bool(getattr(args, "allocated_only", False))
    # Check before wipefs/sgdisk: an unusable option must not strand a wiped target.
    if allocated_only:
        require_allocated_only(args.force_target)

    info, source_layout, _ = validate_flash_target(vm, disk_path, args.device, force_target=args.force_target)
    # Container images hide their partition table until conversion. Check GPT
    # tooling before writing so a missing utility cannot strand the copy.
    if source_layout != "dos":
        for command in ("sgdisk", "sfdisk", "blkid", "blockdev"):
            runtime.require_command(command)
    with ExitStack() as cleanup:
        prepared = None
        if allocated_only:
            from vmctl import flash_allocated

            prepared = cleanup.enter_context(flash_allocated.prepare(args.vm, vm, disk_path, info))
            # Scanning may take minutes. Revalidate the actual target before wiping.
            info, _, _ = validate_flash_target(vm, disk_path, args.device, force_target=args.force_target)
            if prepared.size > info["size"]:
                raise VMError("Prepared image exceeds the flash target")
        try:
            if args.force_target:
                if _device_has_gpt_metadata(info):
                    runtime.require_command("sgdisk")
                for child in info["children"]:
                    child_path = child.get("path")
                    if child_path:
                        runtime.run(["wipefs", "-a", "-f", child_path], dry_run=False, quiet=True)
                if _device_has_gpt_metadata(info):
                    runtime.run(["sgdisk", "--zap-all", args.device], dry_run=False, quiet=True)
                runtime.run(["wipefs", "-a", "-f", args.device], dry_run=False, quiet=True)
                runtime.reread_partition_table(args.device, dry_run=False)
            if prepared is not None:
                prepared.copy_to(args.device)
            else:
                runtime.run(["qemu-img", "convert", "-n", "-p", "-f", disk["format"],
                             "-O", "raw", str(disk_path), args.device], dry_run=False)
            try:
                runtime.reread_partition_table(args.device, dry_run=False)
                # Probe the written bytes rather than lsblk/udev's cached table.
                if disk_inspect.partition_layout(Path(args.device)) == "gpt":
                    runtime.require_command("sgdisk")
                    runtime.run(["sgdisk", "-e", args.device], dry_run=False, quiet=True, show_command=False)
                    for command in ("sfdisk", "blkid", "blockdev"):
                        runtime.require_command(command)
                    grow_flashed_ntfs(args.device, disk_path.parent / "flash-partitions.sfdisk", expand=getattr(args, "expand", None))
                else:
                    ui.print_status("warn", "Partition expansion supports GPT/NTFS only; other layouts are copied as-is", ok=False)
            except (VMError, subprocess.CalledProcessError, ValueError, KeyError, OSError) as exc:
                raise VMError(f"Disk image copied to {args.device}, but post-flash repair/expansion did not complete: {exc}") from exc
        finally:
            runtime.run(["sync"], dry_run=False, quiet=True)

    return 0


def _bin_vmctl_path() -> str:
    """Resolve the absolute path to the bin/vmctl entry-point script.

    Used to re-exec the helper subcommand under sudo from the same install.
    """
    return str((state.ROOT / "bin" / "vmctl").resolve())
