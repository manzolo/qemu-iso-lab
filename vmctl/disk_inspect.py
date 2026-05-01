"""Block-device inspection helpers (wipefs, lsblk, GPT geometry)."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from vmctl import runtime
from vmctl.errors import VMError


def wipefs_signatures(path: Path) -> list[dict]:
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


def collect_mountpoints(node: dict) -> list[str]:
    mountpoints = [mp for mp in node.get("mountpoints") or [] if mp]
    for child in node.get("children") or []:
        mountpoints.extend(collect_mountpoints(child))
    return mountpoints


def find_device_node(nodes: list[dict], device: str) -> dict | None:
    for node in nodes:
        if node.get("path") == device:
            return node
        child = find_device_node(node.get("children") or [], device)
        if child is not None:
            return child
    return None


def lsblk_devices() -> list[dict]:
    output = runtime.run_output(
        [
            "lsblk",
            "--json",
            "-b",
            "-o",
            "PATH,NAME,TYPE,SIZE,START,LOG-SEC,MODEL,MOUNTPOINTS,PKNAME,PTTYPE,FSTYPE",
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


def inspect_block_device(device: str) -> dict:
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
        "mountpoints": mountpoints,
        "children": node.get("children") or [],
        "logical_sector_size": int(node.get("log-sec", 512) or 512),
        "signatures": signatures,
        "pttype": node.get("pttype"),
        "fstype": node.get("fstype"),
        "is_root_disk": root_disk == device,
        "is_empty": empty,
    }


def inspect_block_device_basic(device: str) -> dict:
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
        "mountpoints": collect_mountpoints(node),
        "children": node.get("children") or [],
        "logical_sector_size": int(node.get("log-sec", 512) or 512),
        "pttype": node.get("pttype"),
        "fstype": node.get("fstype"),
        "is_root_disk": root_block_device() == device,
    }


def list_non_root_devices() -> list[dict]:
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
                "mountpoints": collect_mountpoints(node),
                "children": node.get("children") or [],
                "is_root_disk": False,
            }
        )
    return result


def list_flashable_devices() -> list[dict]:
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


def gpt_backup_overhead_bytes(info: dict) -> int:
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


def maybe_read_gpt_geometry(device: str, logical_sector_size: int) -> dict[str, int]:
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


def partition_extent_bytes(node: dict, logical_sector_size: int) -> int:
    start = int(node.get("start", 0) or 0)
    size = int(node.get("size", 0) or 0)
    return (start * logical_sector_size) + size


def cmd_list_empty_devices(args: argparse.Namespace) -> int:
    runtime.require_command("lsblk")
    runtime.require_command("wipefs")
    for info in list_flashable_devices():
        print("\t".join([info["path"], runtime.format_bytes(info["size"]), info["model"] or "-"]))
    return 0


def cmd_list_target_devices(args: argparse.Namespace) -> int:
    runtime.require_command("lsblk")
    for info in list_non_root_devices():
        print("\t".join([info["path"], runtime.format_bytes(info["size"]), info["model"] or "-"]))
    return 0
