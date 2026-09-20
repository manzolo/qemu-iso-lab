"""Block-device inspection helpers (wipefs, lsblk, GPT geometry)."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from typing import Any

from vmctl import runtime
from vmctl.errors import VMError


def wipefs_signatures(path: Path) -> list[dict[str, Any]]:
    result = subprocess.run(
        ["wipefs", "-n", "--json", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        if "Permission denied" in stderr:
            raise VMError(f"Need elevated privileges to inspect block device signatures: {path}")
        return []

    output = result.stdout

    if not output.strip():
        return []

    payload = json.loads(output)
    signatures = payload.get("signatures", [])
    if not isinstance(signatures, list):
        return []
    return [item for item in signatures if isinstance(item, dict)]


def partition_layout(path: Path) -> str | None:
    for entry in wipefs_signatures(path):
        sig_type = str(entry.get("type", "")).lower()
        if sig_type in {"gpt", "dos"}:
            return sig_type
    return None


def collect_mountpoints(node: dict[str, Any]) -> list[str]:
    mountpoints = [mp for mp in node.get("mountpoints") or [] if mp]
    for child in node.get("children") or []:
        mountpoints.extend(collect_mountpoints(child))
    return mountpoints


def find_device_node(nodes: list[dict[str, Any]], device: str) -> dict[str, Any] | None:
    for node in nodes:
        if node.get("path") == device:
            return node
        child = find_device_node(node.get("children") or [], device)
        if child is not None:
            return child
    return None


def unallocated_bytes(node: dict[str, Any]) -> int | None:
    """Approximate bytes outside partitions, including small table/alignment gaps.

    lsblk START uses the kernel's 512-byte sector offsets, including on 4Kn
    disks. Merge ranges so extended/overlapping partitions are not counted twice.
    This display-only estimate says nothing about free space inside filesystems.
    """
    size = int(node.get("size") or 0)
    if size <= 0:
        return None
    children = node.get("children") or []
    if not children:
        return 0 if node.get("fstype") else size
    ranges = []
    for child in children:
        if child.get("type") != "part" or child.get("start") is None or child.get("size") is None:
            return None
        start = int(child["start"]) * 512
        end = start + int(child["size"])
        if start < 0 or end <= start or end > size:
            return None
        ranges.append((start, end))
    occupied = 0
    previous_end = 0
    for start, end in sorted(ranges):
        occupied += max(0, end - max(start, previous_end))
        previous_end = max(previous_end, end)
    return size - occupied


def lsblk_devices() -> list[dict[str, Any]]:
    output = runtime.run_output(
        [
            "lsblk",
            "--json",
            "-b",
            "-o",
            "PATH,NAME,TYPE,SIZE,START,LOG-SEC,MODEL,SERIAL,TRAN,LABEL,MOUNTPOINTS,PKNAME,PTTYPE,FSTYPE",
        ]
    )
    payload = json.loads(output)
    devices = payload.get("blockdevices", [])
    if not isinstance(devices, list):
        raise VMError("Unexpected lsblk output")
    return devices


def root_block_device() -> str | None:
    try:
        source = runtime.run_output(["findmnt", "-n", "-o", "SOURCE", "/"]).strip()
    except subprocess.CalledProcessError:
        return None
    if not source.startswith("/dev/"):
        return None

    try:
        parent = runtime.run_output(["lsblk", "-n", "-o", "PKNAME", source]).strip()
    except subprocess.CalledProcessError:
        parent = ""
    if parent:
        return f"/dev/{parent}"
    return source


def inspect_block_device(device: str) -> dict[str, Any]:
    devices = lsblk_devices()
    node = find_device_node(devices, device)
    if node is None:
        raise VMError(f"Device not found: {device}")
    if node.get("type") != "disk":
        raise VMError(f"Target is not a whole disk device: {device}")

    mountpoints = collect_mountpoints(node)
    signatures = wipefs_signatures(Path(device))
    root_disk = root_block_device()
    empty = not mountpoints and not signatures and not (node.get("children") or [])

    return {
        "path": device,
        "name": node.get("name", ""),
        "size": int(node.get("size", 0) or 0),
        "model": str(node.get("model") or "").strip(),
        "serial": node.get("serial"),
        "tran": node.get("tran"),
        "label": node.get("label"),
        "unallocated_bytes": unallocated_bytes(node),
        "mountpoints": mountpoints,
        "children": node.get("children") or [],
        "logical_sector_size": int(node.get("log-sec", 512) or 512),
        "signatures": signatures,
        "pttype": node.get("pttype"),
        "fstype": node.get("fstype"),
        "is_root_disk": root_disk == device,
        "is_empty": empty,
    }


def inspect_block_device_basic(device: str) -> dict[str, Any]:
    devices = lsblk_devices()
    node = find_device_node(devices, device)
    if node is None:
        raise VMError(f"Device not found: {device}")
    if node.get("type") != "disk":
        raise VMError(f"Target is not a whole disk device: {device}")

    return {
        "path": device,
        "name": node.get("name", ""),
        "size": int(node.get("size", 0) or 0),
        "model": str(node.get("model") or "").strip(),
        "serial": node.get("serial"),
        "tran": node.get("tran"),
        "label": node.get("label"),
        "unallocated_bytes": unallocated_bytes(node),
        "mountpoints": collect_mountpoints(node),
        "children": node.get("children") or [],
        "logical_sector_size": int(node.get("log-sec", 512) or 512),
        "pttype": node.get("pttype"),
        "fstype": node.get("fstype"),
        "is_root_disk": root_block_device() == device,
    }


def list_non_root_devices() -> list[dict[str, Any]]:
    result = []
    root_disk = root_block_device()
    for node in lsblk_devices():
        path = node.get("path")
        if node.get("type") != "disk" or not path:
            continue
        if path == root_disk:
            continue
        result.append(
            {
                "path": path,
                "size": int(node.get("size", 0) or 0),
                "model": str(node.get("model") or "").strip(),
                "serial": node.get("serial"),
                "tran": node.get("tran"),
                "label": node.get("label"),
                "fstype": node.get("fstype"),
                "unallocated_bytes": unallocated_bytes(node),
                "mountpoints": collect_mountpoints(node),
                "children": node.get("children") or [],
                "is_root_disk": False,
            }
        )
    return result


def list_flashable_devices() -> list[dict[str, Any]]:
    result = []
    for node in lsblk_devices():
        path = node.get("path")
        if node.get("type") != "disk" or not path:
            continue
        try:
            info = inspect_block_device(path)
        except VMError:
            continue
        if info["is_empty"] and not info["is_root_disk"]:
            result.append(info)
    return result


def gpt_backup_overhead_bytes(info: dict[str, Any]) -> int:
    logical_sector_size = int(info.get("logical_sector_size", 512) or 512)
    partition_entry_count = int(info.get("gpt_partition_entry_count", 128) or 128)
    partition_entry_size = int(info.get("gpt_partition_entry_size", 128) or 128)
    first_usable_lba = int(info.get("gpt_first_usable_lba", 34) or 34)
    entry_array_bytes = partition_entry_count * partition_entry_size
    entry_array_sectors = runtime.round_up_div(entry_array_bytes, logical_sector_size)
    # When relocating the secondary GPT, sgdisk preserves the effective GPT trailer
    # footprint implied by the disk's first usable LBA. Some disks leave a large
    # reserved/alignment gap before the first partition (for example first usable
    # sector 2048), and the compacted image needs matching space at the tail.
    trailer_sectors = max(1 + entry_array_sectors, first_usable_lba - 1)
    return logical_sector_size * trailer_sectors


def maybe_read_gpt_geometry(device: str, logical_sector_size: int) -> dict[str, Any]:
    header_offset = logical_sector_size
    header_size = 92
    try:
        with Path(device).open("rb") as fh:
            fh.seek(header_offset)
            header = fh.read(header_size)
    except OSError:
        return {}

    if len(header) < header_size or header[:8] != b"EFI PART":
        return {}

    partition_entry_count = int.from_bytes(header[80:84], "little")
    partition_entry_size = int.from_bytes(header[84:88], "little")
    if partition_entry_count <= 0 or partition_entry_size <= 0:
        return {}

    return {
        "gpt_partition_entry_count": partition_entry_count,
        "gpt_partition_entry_size": partition_entry_size,
        "gpt_first_usable_lba": int.from_bytes(header[40:48], "little"),
    }


def partition_extent_bytes(node: dict[str, Any], logical_sector_size: int) -> int:
    start = int(node.get("start", 0) or 0)
    size = int(node.get("size", 0) or 0)
    return (start * logical_sector_size) + size


def device_contents(info: dict[str, Any]) -> str:
    """Describe existing filesystems without claiming that an unrecognized disk is empty."""
    entries = []

    def visit(node: dict[str, Any]) -> None:
        details = []
        if node.get("fstype"):
            details.append(str(node["fstype"]))
        if node.get("label"):
            details.append(f'label={node["label"]}')
        if details or node is not info:
            name = Path(node.get("path") or node.get("name") or "?").name
            entries.append(f"{name}: {' '.join(details) or 'unknown filesystem'}")
        for child in node.get("children") or []:
            visit(child)

    visit(info)
    return "; ".join(entries) or "no recognized filesystems"


def print_device_row(info: dict[str, Any]) -> None:
    # Keep the original three TSV columns; append identification and contents.
    contents = device_contents(info)
    if info.get("mountpoints"):
        contents += "; mounted: " + ", ".join(info["mountpoints"])
    fields = [info["path"], runtime.format_bytes(info["size"]), info.get("model"),
              info.get("serial"), info.get("tran"), contents]
    # Disk labels and model strings must not create extra menu rows/columns.
    print("\t".join(" ".join(str(value or "-").split()) for value in fields))


def cmd_list_empty_devices(args: argparse.Namespace) -> int:
    runtime.require_command("lsblk")
    runtime.require_command("wipefs")
    devices = list_flashable_devices()
    if getattr(args, "json", False):
        print(json.dumps(devices))
        return 0
    for info in devices:
        print_device_row(info)
    return 0


def cmd_list_target_devices(args: argparse.Namespace) -> int:
    runtime.require_command("lsblk")
    devices = list_non_root_devices()
    if getattr(args, "json", False):
        print(json.dumps(devices))
        return 0
    for info in devices:
        print_device_row(info)
    return 0
