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


PARTCLONE = {
    "ext2": "partclone.extfs", "ext3": "partclone.extfs", "ext4": "partclone.extfs",
    "ntfs": "partclone.ntfs", "vfat": "partclone.fat", "exfat": "partclone.exfat",
}


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


def build_domain(plan: list[dict[str, Any]], size: int, scratch: Path) -> list[Block]:
    blocks: list[Block] = []
    end = 0
    for index, part in enumerate(plan):
        start, length = part["start"], part["size"]
        # Preserve everything outside known filesystems, including bootloader gaps.
        append_block(blocks, end, start - end, "+")
        command = PARTCLONE.get(part["fstype"])
        if command is None:
            ui.print_note(f"{part['path']}: {part['fstype']}, copying the entire partition")
            append_block(blocks, start, length, "+")
        else:
            runtime.require_command(command)
            ui.print_note(f"{part['path']}: mapping allocated {part['fstype']} blocks")
            domain = scratch / f"partition-{index}.map"
            runtime.run([command, "--domain", "--source", part["path"], "--output", str(domain),
                         "--logfile", str(scratch / f"partition-{index}.log")])
            mapped = read_map(domain, length, "?+")
            if not any(block.status == "+" for block in mapped):
                raise VMError(f"No allocated filesystem metadata in {domain}")
            for block in mapped:
                if block.start % 512 or block.size % 512:
                    raise VMError(f"Unaligned filesystem map: {domain}")
                append_block(blocks, start + block.start, block.size, block.status)
            append_block(blocks, start + mapped[-1].end, length - mapped[-1].end, "+")
        end = start + length
    append_block(blocks, end, size - end, "+")
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
            _import_locked(args, vm, disk_path, info, table, plan, size, work, resume)
            completed = True
    finally:
        if completed:
            shutil.rmtree(work)
        else:
            flash.maybe_restore_sudo_owner_tree(work)
            ui.print_note(f"Import state kept at: {work}. Resume with --allocated-only --resume only while the source remains unchanged.")


def _import_locked(args: argparse.Namespace, vm: dict[str, Any], disk_path: Path,
                   info: dict[str, Any], table: dict[str, Any], plan: list[dict[str, Any]],
                   size: int, work: Path, resume: bool) -> None:
    from vmctl.import_dev import validate_import_source

    raw, domain, rescue = work / "source.raw", work / "domain.map", work / "rescue.map"
    manifest = work / "manifest.json"
    staged = work / "converted.img"
    for path in (raw, domain, rescue, manifest, staged):
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise VMError(f"Invalid import state file: {path}")
    with tempfile.TemporaryDirectory(dir=work, prefix="scan-") as scratch:
        blocks = build_domain(plan, size, Path(scratch))
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
    ui.print_kv("to copy", runtime.format_bytes(selected))
    ui.print_kv("skipped", runtime.format_bytes(size - selected))
    ui.print_kv("state", str(work))
    # On resume, write zeros too: an interrupted write may not be in the map yet.
    sparse = [] if resume else ["--sparse"]
    runtime.run(["ddrescue", *sparse, "--no-scrape", "--retry-passes=0", "--sector-size=512",
                 f"--size={size}", f"--domain-mapfile={domain}", args.device, str(raw), str(rescue)])
    if read_map(rescue, size)[-1].end != size:
        raise VMError("Import incomplete: rescue map does not cover the entire source disk")
    try:
        runtime.run(["ddrescuelog", "--done-status", f"--size={size}", f"--domain-mapfile={domain}", str(rescue)])
    except subprocess.CalledProcessError as exc:
        raise VMError("Import incomplete: required blocks remain unread or damaged; the VM disk has not been replaced") from exc
    current = validate_import_source(args.device)
    if int(current["size"]) != size or partition_plan(args.device, current) != (table, plan):
        raise VMError("Source partition layout changed during import")
    ui.print_note("All required blocks recovered; converting the staged disk")
    runtime.run_progress(["qemu-img", "convert", "-p", "-f", "raw", "-O", vm["disk"]["format"], str(raw), str(staged)])
    if vm["disk"]["format"] == "qcow2":
        runtime.run(["qemu-img", "check", "-f", "qcow2", str(staged)])
    require_stopped(args.vm, vm)
    if target_stamp(disk_path) != identity["previous_target"]:
        raise VMError("VM disk changed during import; converted image kept in the state directory")
    flash.maybe_restore_sudo_owner(staged)
    os.replace(staged, disk_path)
