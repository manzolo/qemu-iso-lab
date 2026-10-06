"""`vmctl usage`: what the checkout holds on disk, by section (ISO cache, VM disks, checkpoints,
reports, recordings, media tools, git...), and the heaviest VMs. Allocated blocks, never the
apparent size: a sparse raw image is all capacity on paper (Manzolo asked for the table on
2026-10-06, after a day of looking for what was growing)."""
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any

from vmctl import config, state, ui

DISK_SUFFIXES = {".qcow2", ".raw", ".vhd", ".vhdx", ".vmdk", ".vdi", ".img"}
# artifacts/ directories that are tools of the maintainer, not machines
MEDIA_TOOL_DIRS = ("tts", "tour")
SECTIONS = (
    ("isos", "ISO cache (isos/)"),
    ("disks", "VM disks"),
    ("checkpoints", "VM checkpoints"),
    ("vm_other", "VM logs, seeds, installers, keys"),
    ("reports", "check-vms reports"),
    ("recordings", "recordings (vmctl record, web)"),
    ("media_tools", "media tools (artifacts/tts, artifacts/tour)"),
    ("labs", "labs (maps, links)"),
    ("artifacts_other", "other artifacts"),
    ("docs_media", "docs/media (the media branch)"),
    ("git", ".git"),
    ("checkout", "code, docs, tools, venvs"),
)


def allocated(path: Path) -> int:
    """Bytes allocated under path (st_blocks), symlinks not followed, unreadable entries skipped."""
    try:
        st = path.lstat()
    except OSError:
        return 0
    total = st.st_blocks * 512
    if not path.is_dir() or path.is_symlink():
        return total
    try:
        entries = list(os.scandir(path))
    except OSError:
        return total
    for entry in entries:
        total += allocated(Path(entry.path))
    return total


def human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


def vm_breakdown(directory: Path) -> dict[str, int]:
    """One VM's directory: data images at its top level, the checkpoints, the rest."""
    disks = 0
    for entry in directory.iterdir():
        if entry.is_file() and not entry.is_symlink() and entry.suffix.lower() in DISK_SUFFIXES:
            disks += allocated(entry)
    checkpoints = allocated(directory / "checkpoints") if (directory / "checkpoints").is_dir() else 0
    recordings = allocated(directory / "recording") if (directory / "recording").is_dir() else 0
    total = allocated(directory)
    return {"total": total, "disks": disks, "checkpoints": checkpoints, "recordings": recordings,
            "other": max(0, total - disks - checkpoints - recordings)}


def collect() -> dict[str, Any]:
    """The whole table as data: sections (bytes), the VMs (bytes each), the total."""
    root = state.ROOT
    artifacts = root / "artifacts"
    sections = {key: 0 for key, _ in SECTIONS}
    vms: list[dict[str, Any]] = []
    try:
        known = set(config.load_config().get("vms", {}))
    except Exception:  # a broken local.json must not hide the numbers
        known = set()
    if artifacts.is_dir():
        for entry in sorted(artifacts.iterdir()):
            name = entry.name
            if not entry.is_dir() or entry.is_symlink():
                sections["artifacts_other"] += allocated(entry)
            elif name == "check-vms":
                sections["reports"] += allocated(entry)
            elif name == ".web-recordings":
                sections["recordings"] += allocated(entry)
            elif name == "labs":
                sections["labs"] += allocated(entry)
            elif name in MEDIA_TOOL_DIRS:
                sections["media_tools"] += allocated(entry)
            elif name.startswith("."):
                sections["artifacts_other"] += allocated(entry)
            elif name in known or (entry / "disk.qcow2").exists() or any(entry.glob("disk.*")) or (entry / "state.json").exists():
                parts = vm_breakdown(entry)
                sections["disks"] += parts["disks"]
                sections["checkpoints"] += parts["checkpoints"]
                sections["recordings"] += parts["recordings"]
                sections["vm_other"] += parts["other"]
                vms.append({"name": name, **parts})
            else:
                sections["artifacts_other"] += allocated(entry)
    sections["isos"] = allocated(root / "isos") if (root / "isos").is_dir() else 0
    sections["docs_media"] = allocated(root / "docs" / "media") if (root / "docs" / "media").is_dir() else 0
    sections["git"] = allocated(root / ".git") if (root / ".git").exists() else 0
    counted = {"artifacts", "isos", ".git"}
    checkout = 0
    for entry in root.iterdir():
        if entry.name in counted:
            continue
        if entry.name == "docs":
            checkout += sum(allocated(child) for child in entry.iterdir() if child.name != "media")
        else:
            checkout += allocated(entry)
    sections["checkout"] = checkout
    vms.sort(key=lambda v: -int(v["total"]))
    return {"root": str(root), "sections": sections, "vms": vms, "total": sum(sections.values())}


def clipped(text: str, width: int) -> str:
    return text if len(text) <= width else text[:width - 1] + "…"


def render_usage(data: dict[str, Any], top: int) -> None:
    """Plain, like the rest of vmctl's output: the sections, the heaviest VMs, the commands that
    free space. No bars and no rainbow (Manzolo, 2026-10-06: "meno maranza")."""
    sections, total = data["sections"], int(data["total"])
    columns = max(40, min(110, shutil.get_terminal_size(fallback=(100, 24)).columns))
    ui.print_header(f"Disk usage of {ui.pretty_path(Path(data['root']))}")
    ui.print_kv("allocated", f"{human(total)} · {len(data['vms'])} VMs (blocks on disk, not the images' capacity)")
    print()
    label_width = min(max(len(label) for _, label in SECTIONS), columns - 24)
    for key, label in SECTIONS:
        size = int(sections[key])
        if not size:
            continue
        share = f"{100 * size / total if total else 0:5.1f}%"
        print(f"  {clipped(label, label_width):<{label_width}}  {human(size):>10}  {share}")
    print(f"  {ui.style('total', ui.BOLD):<{label_width + len(ui.BOLD) + len(ui.RESET)}}  {ui.style(f'{human(total):>10}', ui.BOLD)}")

    if top and data["vms"]:
        shown = min(top, len(data["vms"]))
        print()
        print("  " + ui.style(f"Heaviest VMs (top {shown} of {len(data['vms'])})", ui.BOLD))
        compact = columns < 76
        longest = max(len(vm["name"]) for vm in data["vms"][:top])
        name_width = min(columns - (16 if compact else 50), max(24, longest))
        details_header = "" if compact else f"  {'DISKS':>9}  {'CHECKPOINTS':>11}  {'OTHER':>9}"
        print(f"  {'VM':<{name_width}}  {'TOTAL':>9}{details_header}")
        for vm in data["vms"][:top]:
            other = vm["other"] + vm["recordings"]
            details = "" if compact else f"  {human(vm['disks']):>9}  {human(vm['checkpoints']):>11}  {human(other):>9}"
            print(f"  {clipped(vm['name'], name_width):<{name_width}}  {human(vm['total']):>9}{details}")
        if len(data["vms"]) > top:
            print(f"  + {len(data['vms']) - top} more: vmctl usage --top N")
    print()
    ui.print_note("Free space: vmctl delete-iso <name> · vmctl checkpoint delete <vm> <name> · vmctl clean-reports --keep 5 · vmctl clean <vm>")


def cmd_usage(args: argparse.Namespace) -> int:
    data = collect()
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2))
        return 0
    top = max(0, int(getattr(args, "top", 10) or 0))
    render_usage(data, top)
    return 0
