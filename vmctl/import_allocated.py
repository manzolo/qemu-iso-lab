"""Filesystem-aware physical imports, with persistent ddrescue state."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vmctl import flash, runtime, ui
from vmctl.errors import VMError
from vmctl.flash_progress import OperationProgress


PARTCLONE = {
    "ext2": "partclone.extfs", "ext3": "partclone.extfs", "ext4": "partclone.extfs",
    "ntfs": "partclone.ntfs", "vfat": "partclone.fat", "exfat": "partclone.exfat",
}

# The end of the disk is always copied: the backup GPT header sits in the last sector and
# its partition entry array just before it (33 sectors on a standard table). One MiB covers
# any entry array a real disk carries and costs nothing.
TAIL_KEEP_BYTES = 1024 * 1024
GPT_HEADER_SIZE = 92


@dataclass(frozen=True)
class Block:
    start: int
    size: int
    status: str

    @property
    def end(self) -> int:
        return self.start + self.size


def read_map(path: Path, limit: int, statuses: str = "?*/-+") -> list[Block]:
    """Parse ddrescue's text format, rejecting gaps and ambiguous extents."""
    blocks = []
    header = False
    end = 0
    try:
        for line in path.read_text(encoding="ascii").splitlines():
            fields = line.partition("#")[0].split()
            if not fields:
                continue
            if not header:
                if len(fields) not in (2, 3) or fields[1] not in ("?", "*", "/", "-", "+", "F", "G"):
                    raise ValueError("invalid status line")
                int(fields[0], 0)
                header = True
                continue
            if len(fields) != 3 or fields[2] not in tuple(statuses):
                raise ValueError("invalid block line")
            start, size = int(fields[0], 0), int(fields[1], 0)
            if start != end or size <= 0 or start + size > limit:
                raise ValueError("invalid block bounds")
            blocks.append(Block(start, size, fields[2]))
            end = start + size
        if not blocks:
            raise ValueError("empty map")
    except (OSError, UnicodeError, ValueError) as exc:
        raise VMError(f"Invalid ddrescue map {path}: {exc}") from exc
    return blocks


def append_block(blocks: list[Block], start: int, size: int, status: str) -> None:
    if size <= 0:
        return
    if blocks and blocks[-1].end == start and blocks[-1].status == status:
        previous = blocks.pop()
        blocks.append(Block(previous.start, previous.size + size, status))
    else:
        blocks.append(Block(start, size, status))


def map_text(blocks: list[Block]) -> str:
    return "# vmctl allocated-block domain\n0x0 ? 1\n" + "".join(
        f"0x{block.start:X} 0x{block.size:X} {block.status}\n" for block in blocks
    )


def require_tools(disk: dict[str, Any]) -> None:
    if disk["format"] not in {"raw", "qcow2"} or disk.get("subformat"):
        raise VMError("--allocated-only currently supports raw and qcow2 targets without subformat")
    for command in ("ddrescue", "ddrescuelog", "sfdisk", "lsblk", "findmnt"):
        runtime.require_command(command)


def partition_plan(device: str, info: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if int(info["logical_sector_size"]) != 512:
        raise VMError("--allocated-only currently requires 512-byte logical sectors")
    if info.get("pttype") not in {"gpt", "dos"}:
        raise VMError("--allocated-only requires a GPT or MBR partition table")
    children = info.get("children") or []
    if any(child.get("type") != "part" or child.get("children") for child in children):
        raise VMError("Refusing active device mappings or nested partitions; deactivate them before import")
    try:
        table = json.loads(runtime.run_output(["sfdisk", "--json", device]))["partitiontable"]
        sector = int(table["sectorsize"])
        if table["label"] != info["pttype"] or table["unit"] != "sectors" or sector != 512:
            raise ValueError("inconsistent partition table")
        nodes = {child["path"]: child for child in children}
        parts = sorted(table["partitions"], key=lambda part: int(part["start"]))
        if not parts or {part["node"] for part in parts} != set(nodes):
            raise ValueError("kernel partitions do not match the on-disk table")
        plan = []
        end = sector
        for part in parts:
            if table["label"] == "dos" and int(part["type"], 16) in {0x05, 0x0F, 0x85}:
                raise VMError("Extended MBR partitions are not supported by --allocated-only")
            start, size = int(part["start"]) * sector, int(part["size"]) * sector
            node = nodes[part["node"]]
            if (start < end or size <= 0 or start + size > int(info["size"])
                    or size != int(node["size"]) or start != int(node["start"]) * sector):
                raise ValueError("overlapping or inconsistent partition bounds")
            plan.append({"path": part["node"], "start": start, "size": size, "fstype": node.get("fstype") or "unknown"})
            end = start + size
    except (KeyError, TypeError, ValueError) as exc:
        raise VMError(f"Cannot safely map source partitions: {exc}") from exc
    return table, plan


def gpt_backup_start(device: str, size: int, sector: int = 512) -> int | None:
    """Byte offset of the backup GPT partition entry array, read from the backup header in
    the disk's last sector; ``None`` when there is no valid backup header there."""
    try:
        with Path(device).open("rb") as handle:
            handle.seek(size - sector)
            header = handle.read(GPT_HEADER_SIZE)
    except OSError:
        return None
    if len(header) < GPT_HEADER_SIZE or header[:8] != b"EFI PART":
        return None
    my_lba = int.from_bytes(header[24:32], "little")
    entry_lba = int.from_bytes(header[72:80], "little")
    count = int.from_bytes(header[80:84], "little")
    entry_size = int.from_bytes(header[84:88], "little")
    if my_lba != size // sector - 1 or entry_lba <= 0 or entry_lba >= my_lba or count <= 0 or entry_size <= 0:
        return None
    return entry_lba * sector


def trailing_keep_from(device: str | None, label: str | None, plan_end: int, size: int, sector: int = 512) -> tuple[int, str]:
    """Where the copy of the tail after the last partition starts, and why.

    Between the last partition and the backup GPT structures there is only unpartitioned
    space: no filesystem, no bootloader, nothing a guest can see. On the 250 GB disk that
    motivated this, sda2 ended at 34 GB and the remaining 199 GB were read in full at
    500 MB/s for nothing (2026-09-20). GPT: keep from the backup entry array (read from the
    backup header itself, never assumed) or the last MiB, whichever comes first; if the
    header cannot be read, copy the tail whole rather than guess. MBR: nothing lives after
    the last partition, the last MiB is kept out of caution. No device or unknown table:
    copy everything, the historical behaviour.
    """
    if device is None or label not in ("gpt", "dos"):
        return plan_end, "unpartitioned tail copied in full"
    keep = max(plan_end, size - TAIL_KEEP_BYTES)
    if label == "gpt":
        backup = gpt_backup_start(device, size, sector)
        if backup is None:
            return plan_end, "the backup GPT header could not be read, so the unpartitioned tail is copied in full"
        return max(plan_end, min(keep, backup)), "backup GPT kept"
    return keep, "MBR disk, nothing lives after the last partition"


def build_domain(plan: list[dict[str, Any]], size: int, scratch: Path,
                 progress: OperationProgress | None = None,
                 device: str | None = None, table_label: str | None = None) -> list[Block]:
    """The ddrescue domain: ``+`` blocks are read, ``?`` blocks are skipped. *device* and
    *table_label* (``gpt``/``dos``) let the unpartitioned tail be skipped; without them the
    tail is copied in full. (The loop's own ``label`` is the progress caption.)"""
    blocks: list[Block] = []
    end = 0
    for index, part in enumerate(plan):
        start, length = part["start"], part["size"]
        # Preserve everything outside known filesystems, including bootloader gaps.
        append_block(blocks, end, start - end, "+")
        command = PARTCLONE.get(part["fstype"])
        if command is None:
            label = f"Partition {index + 1}: {part['fstype']}" if progress else part['path']
            ui.print_note(f"{label}: copying the entire partition")
            append_block(blocks, start, length, "+")
        else:
            runtime.require_command(command)
            if progress is None:
                ui.print_note(f"{part['path']}: mapping allocated {part['fstype']} blocks")
            domain = scratch / f"partition-{index}.map"
            log = scratch / f"partition-{index}.log"
            try:
                cmd = [command, "--domain", "--source", part["path"], "--output", str(domain),
                       "--logfile", str(log)]
                if progress is None:
                    runtime.run(cmd)
                else:
                    progress.run(cmd, title=f"Partition {index + 1}/{len(plan)} · {part['fstype']} · {runtime.format_bytes(length)}")
            except subprocess.CalledProcessError as exc:
                guidance = (
                    " If NTFS is unclean or hibernated, check the volume in Windows and shut it down fully."
                    if part["fstype"] == "ntfs" else " Check the filesystem before retrying."
                )
                raise VMError(
                    f"{command} could not map allocated blocks on {part['path']} (exit code {exc.returncode})."
                    f" No disk copy was started in this attempt. Partclone log: {log}.{guidance}"
                    " After changing the source, move the import state directory aside and start a fresh import; do not use --resume."
                ) from exc
            mapped = read_map(domain, length, "?+")
            if not any(block.status == "+" for block in mapped):
                raise VMError(f"No allocated filesystem metadata in {domain}")
            for block in mapped:
                if block.start % 512 or block.size % 512:
                    raise VMError(f"Unaligned filesystem map: {domain}")
                append_block(blocks, start + block.start, block.size, block.status)
            append_block(blocks, start + mapped[-1].end, length - mapped[-1].end, "+")
        end = start + length
    keep_from, reason = trailing_keep_from(device, table_label, end, size)
    if keep_from > end:
        ui.print_note(f"After the last partition: skipping {runtime.format_bytes(keep_from - end)} of unpartitioned space ({reason})")
        append_block(blocks, end, keep_from - end, "?")
    append_block(blocks, keep_from, size - keep_from, "+")
    return blocks


def verify_recovered(device: str, raw: Path, rescue: Path, size: int) -> None:
    """A matching allocation map alone cannot detect changes to existing files."""
    if raw.stat().st_size != size:
        raise VMError("Cannot resume: temporary RAW size changed")
    if not rescue.exists():
        return  # No blocks have been marked as recovered yet.
    ui.print_note("Checking previously recovered bytes against the source before resuming")
    with Path(device).open("rb") as source, raw.open("rb") as target:
        for block in read_map(rescue, size):
            if block.status != "+":
                continue
            source.seek(block.start)
            target.seek(block.start)
            remaining = block.size
            while remaining:
                count = min(remaining, 4 * 1024**2)
                data = source.read(count)
                if len(data) != count or data != target.read(count):
                    raise VMError("Cannot resume: source or recovered RAW content changed; start a new import")
                remaining -= count


def target_stamp(path: Path) -> list[int] | None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise VMError(f"Import target must be a regular file: {path}")
    if not path.exists():
        return None
    st = path.stat()
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns]


def require_stopped(name: str, vm: dict[str, Any]) -> None:
    from vmctl import lifecycle

    if lifecycle.running_qemu_pid(name, vm) is not None:
        raise VMError(f"Stop VM '{name}' before importing its disk")


def import_disk(args: argparse.Namespace, vm: dict[str, Any], disk_path: Path, info: dict[str, Any]) -> None:
    require_stopped(args.vm, vm)
    table, plan = partition_plan(args.device, info)
    for part in plan:
        if part["fstype"] in PARTCLONE:
            runtime.require_command(PARTCLONE[part["fstype"]])
    size = int(info["size"])
    work = disk_path.with_name(disk_path.name + ".allocated-import")
    resume = getattr(args, "resume", False)
    if work.is_symlink():
        raise VMError(f"Import state must not be a symlink: {work}")
    if resume and not work.is_dir():
        raise VMError(f"No allocated import to resume: {work}")
    if not resume:
        try:
            work.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise VMError(f"Import state already exists: {work}. Use --resume with an unchanged source, or move this directory aside for a fresh import.") from exc
    completed = False
    try:
        if (work / "lock").is_symlink():
            raise VMError("Invalid import lock file")
        with (work / "lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise VMError("Another allocated import is using this target") from exc
            progress = OperationProgress(disk_path.parent / "logs", operation="import")
            _import_locked(args, vm, disk_path, info, table, plan, size, work, resume, progress)
            completed = True
    finally:
        if completed:
            shutil.rmtree(work)
        else:
            flash.maybe_restore_sudo_owner_tree(work)
            ui.print_note(f"Import state kept at: {work}. Resume with --allocated-only --resume only while the source remains unchanged.")


def _import_locked(args: argparse.Namespace, vm: dict[str, Any], disk_path: Path,
                   info: dict[str, Any], table: dict[str, Any], plan: list[dict[str, Any]],
                   size: int, work: Path, resume: bool, progress: OperationProgress) -> None:
    from vmctl.import_dev import validate_import_source

    raw, domain, rescue = work / "source.raw", work / "domain.map", work / "rescue.map"
    manifest = work / "manifest.json"
    staged = work / "converted.img"
    for path in (raw, domain, rescue, manifest, staged):
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise VMError(f"Invalid import state file: {path}")
    scratch = Path(tempfile.mkdtemp(dir=work, prefix="scan-"))
    progress.stage(1, "Find used filesystem blocks")
    try:
        try:
            blocks = build_domain(plan, size, scratch, progress=progress, device=args.device, table_label=table.get("label"))
        finally:
            progress.preserve_logs(scratch)
    except BaseException:
        ui.print_note(f"Partition scan diagnostics kept at: {scratch}")
        raise
    else:
        shutil.rmtree(scratch)
    content = map_text(blocks)
    identity = {
        "version": 1, "source": str(Path(args.device).resolve()),
        "device_number": Path(args.device).stat().st_rdev,
        "size": size, "table": table, "partitions": plan,
        "target": str(disk_path), "format": vm["disk"]["format"],
        "previous_target": target_stamp(disk_path),
        "domain_sha256": hashlib.sha256(content.encode("ascii")).hexdigest(),
    }
    if resume and manifest.exists():
        try:
            previous = json.loads(manifest.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise VMError(f"Cannot read import manifest: {manifest}") from exc
        if previous != identity:
            raise VMError("Cannot resume: source, partition allocation, target or configuration changed")
        if not raw.is_file() or not domain.is_file() or domain.read_text(encoding="ascii") != content:
            raise VMError("Cannot resume: RAW or domain map is missing or changed")
        verify_recovered(args.device, raw, rescue, size)
    else:
        if raw.exists() or rescue.exists():
            raise VMError("Incomplete import state without a manifest; move the state directory aside and start again")
        domain.write_text(content, encoding="ascii")
        with raw.open("xb") as output:
            output.truncate(size)
        manifest.write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")
    selected = sum(block.size for block in blocks if block.status == "+")
    plan_end = max((part["start"] + part["size"] for part in plan), default=0)
    # A skipped tail merges with free space at the end of the last filesystem: count only the part past the partition.
    tail_skipped = sum(max(0, block.end - max(block.start, plan_end)) for block in blocks if block.status == "?")
    print()
    ui.print_kv("To copy", runtime.format_bytes(selected))
    ui.print_kv("Skipped", f"{runtime.format_bytes(size - selected - tail_skipped)} of free filesystem space")
    if tail_skipped:
        ui.print_kv("Skipped", f"{runtime.format_bytes(tail_skipped)} of unpartitioned space after the last partition")
    outside = size - sum(part["size"] for part in plan) - tail_skipped
    if outside:
        ui.print_kv("Included", f"{runtime.format_bytes(outside)} outside filesystems (boot areas, gaps, partition table) preserved in full")
    ui.print_kv("State", ui.pretty_path(work))
    progress.stage(2, "Copy from the physical disk")
    # On resume, write zeros too: an interrupted write may not be in the map yet.
    sparse = [] if resume else ["--sparse"]
    progress.run(["ddrescue", *sparse, "--no-scrape", "--retry-passes=0", "--sector-size=512",
                  f"--size={size}", f"--domain-mapfile={domain}", args.device, str(raw), str(rescue)],
                 title=f"Reading allocated blocks from {args.device}")
    progress.stage(3, "Verify the copied disk")
    if read_map(rescue, size)[-1].end != size:
        raise VMError("Import incomplete: rescue map does not cover the entire source disk")
    try:
        progress.run(["ddrescuelog", "--done-status", f"--size={size}", f"--domain-mapfile={domain}", str(rescue)],
                     title="Verify all selected blocks were copied")
    except subprocess.CalledProcessError as exc:
        raise VMError("Import incomplete: required blocks remain unread or damaged; the VM disk has not been replaced") from exc
    current = validate_import_source(args.device)
    if int(current["size"]) != size or partition_plan(args.device, current) != (table, plan):
        raise VMError("Source partition layout changed during import")
    progress.stage(4, "Create the VM disk image")
    progress.run(["qemu-img", "convert", "-p", "-f", "raw", "-O", vm["disk"]["format"], str(raw), str(staged)],
                 title=f"Convert to {vm['disk']['format']}")
    if vm["disk"]["format"] == "qcow2":
        progress.run(["qemu-img", "check", "-f", "qcow2", str(staged)], title="Check the VM disk image")
    require_stopped(args.vm, vm)
    if target_stamp(disk_path) != identity["previous_target"]:
        raise VMError("VM disk changed during import; converted image kept in the state directory")
    flash.maybe_restore_sudo_owner(staged)
    os.replace(staged, disk_path)
