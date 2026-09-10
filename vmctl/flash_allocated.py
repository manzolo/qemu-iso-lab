"""Filesystem-aware flash preparation; never infer free space from zero bytes."""
from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vmctl import import_allocated, lifecycle, runtime, ui
from vmctl.errors import VMError


def require_tools(force_target: bool) -> None:
    if not force_target:
        raise VMError("--allocated-only requires --force-target to wipe old target signatures")
    for command in ("qemu-img", "sfdisk", "losetup", "blkid", "ddrescue", "ddrescuelog"):
        runtime.require_command(command)


@contextmanager
def partition_view(raw: Path, start: int, size: int) -> Iterator[str]:
    """Expose only this partition, read-only, without kernel partition scanning."""
    device = runtime.run_output([
        "losetup", "--find", "--show", "--read-only", "--offset", str(start),
        "--sizelimit", str(size), "--sector-size", "512", str(raw),
    ]).strip()
    try:
        yield device
    finally:
        runtime.run(["losetup", "--detach", device], quiet=True)


def partition_geometry(raw: Path, size: int) -> list[tuple[int, int]]:
    try:
        table = json.loads(runtime.run_output(["sfdisk", "--json", str(raw)]))["partitiontable"]
        if (table["label"] not in {"gpt", "dos"} or table["unit"] != "sectors"
                or int(table["sectorsize"]) != 512 or not table["partitions"]):
            raise ValueError("requires a GPT/MBR table with 512-byte sectors")
        geometry = []
        end = 512
        for part in sorted(table["partitions"], key=lambda part: int(part["start"])):
            if table["label"] == "dos" and int(part["type"], 16) in {0x05, 0x0F, 0x85}:
                raise ValueError("extended MBR partitions are unsupported")
            start, length = int(part["start"]) * 512, int(part["size"]) * 512
            if start < end or length <= 0 or start + length > size:
                raise ValueError("overlapping or out-of-bounds partitions")
            geometry.append((start, length))
            end = start + length
        return geometry
    except (KeyError, TypeError, ValueError) as exc:
        raise VMError(f"Cannot safely map flash source: {exc}") from exc


def build_domain(raw: Path, size: int, work: Path) -> list[import_allocated.Block]:
    geometry = partition_geometry(raw, size)
    with ExitStack() as views:
        plan = []
        for start, length in geometry:
            device = views.enter_context(partition_view(raw, start, length))
            try:
                fstype = runtime.run_output(["blkid", "-p", "-s", "TYPE", "-o", "value", device]).strip().lower()
            except subprocess.CalledProcessError as exc:
                if exc.returncode != 2:
                    raise
                fstype = "unknown"
            plan.append({"path": device, "start": start, "size": length, "fstype": fstype})
        return import_allocated.build_domain(plan, size, work)


@dataclass(frozen=True)
class PreparedCopy:
    raw: Path
    domain: Path
    rescue: Path
    size: int

    def copy_to(self, device: str) -> None:
        # --sparse would skip zero-valued *allocated* data on a used target.
        runtime.run([
            "ddrescue", "--force", "--no-scrape", "--retry-passes=0", "--sector-size=512",
            f"--size={self.size}", f"--domain-mapfile={self.domain}",
            str(self.raw), device, str(self.rescue),
        ])
        if import_allocated.read_map(self.rescue, self.size)[-1].end != self.size:
            raise VMError("Flash incomplete: rescue map does not cover the source disk")
        try:
            runtime.run(["ddrescuelog", "--done-status", f"--size={self.size}",
                         f"--domain-mapfile={self.domain}", str(self.rescue)])
        except subprocess.CalledProcessError as exc:
            raise VMError("Flash incomplete: required blocks remain unread or unwritten") from exc


@contextmanager
def prepare(name: str, vm: dict[str, Any], disk_path: Path, target_info: dict[str, Any]) -> Iterator[PreparedCopy]:
    if int(target_info.get("logical_sector_size", 512)) != 512:
        raise VMError("--allocated-only currently requires a target with 512-byte logical sectors")
    if lifecycle.running_qemu_pid(name, vm) is not None:
        raise VMError(f"Stop VM '{name}' before flashing its disk")
    # Conversion resolves backing files and freezes the source before any target
    # writes. A private directory prevents other users from replacing the maps.
    with tempfile.TemporaryDirectory(prefix=".allocated-flash-", dir=disk_path.parent) as directory:
        work = Path(directory)
        raw = work / "source.raw"
        ui.print_note("Preparing a temporary sparse RAW copy and scanning filesystem allocation")
        runtime.run(["qemu-img", "convert", "-p", "-f", vm["disk"]["format"],
                     "-O", "raw", str(disk_path), str(raw)])
        size = raw.stat().st_size
        if size <= 0 or size % 512 or size > int(target_info["size"]):
            raise VMError("Prepared image has an invalid size or exceeds the flash target")
        try:
            blocks = build_domain(raw, size, work)
        except (VMError, subprocess.CalledProcessError) as exc:
            logs = "\n".join(path.read_text(errors="replace")[-4096:] for path in sorted(work.glob("*.log")))
            raise VMError(f"Allocated flash scan failed before target writes: {exc}\n{logs}") from exc
        domain = work / "domain.map"
        domain.write_text(import_allocated.map_text(blocks), encoding="ascii")
        selected = sum(block.size for block in blocks if block.status == "+")
        ui.print_kv("to copy", runtime.format_bytes(selected))
        ui.print_kv("free filesystem space skipped", runtime.format_bytes(size - selected))
        yield PreparedCopy(raw, domain, work / "rescue.map", size)
