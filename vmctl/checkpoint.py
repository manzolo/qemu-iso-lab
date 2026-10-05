"""Checkpoints: named, self-contained copies of a stopped VM's disk and firmware state.

``artifacts/<vm>/checkpoints/<name>/`` holds a *full copy* of the disk image written by
``qemu-img convert`` in the VM's own format, the EFI variable store when the profile is
EFI, the ``state.json`` record of what was known about the disk, and a ``manifest.json``.
A checkpoint therefore depends on nothing: it stays usable however the current disk
changes afterwards, it survives ``vmctl clean``, and it can be restored onto a disk that
no longer exists. That is the strategy for every format the profiles use (qcow2, raw for
NT 4, vpc for the VHD template): qcow2 internal snapshots would have excluded two of them
and would have lived inside the very file ``clean`` deletes. The price is space, and
``--compress`` (qcow2 only) buys some of it back.

Two exceptions to "depends on nothing", both because the disk itself already depends on the
same thing: a qcow2 *overlay* (a cloud-image VM, its base hard-linked under isos/.cloudimg/)
is copied as an overlay on the same base (``qemu-img convert -B``), a few hundred MB instead
of the whole flattened system, and restored the same way; and the profile's ``extra_disks``
(lvm-lab, zfs-lab, mdadm-lab, the Proxmox mirror) travel with the main disk, all or none, so
a restored pool or array never meets a disk from another moment (manifest version 2; a
version 1 checkpoint holds the main disk only and restores only into a profile without
extra disks).

Nothing here is a VM-running snapshot: create and restore work on a powered-off VM, and
the caller (``lifecycle.ensure_vm_quiescent``) refuses a running VM, an installation in
progress and a VM handed to libvirt. TPM state does not exist on plain QEMU (the Windows
profiles bypass the TPM check; swtpm appears only in the libvirt export), so a profile
that declares ``tpm`` is refused rather than checkpointed halfway.

Every write goes through staging: a checkpoint is assembled under a temporary directory
and renamed into place, a restore converts the checkpoint into a temporary directory next
to the disk while the current disk is untouched, then swaps the files with renames and
puts the previous ones back if a rename fails. A delete renames first and removes after.
An existing checkpoint is never overwritten without ``--replace``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from vmctl import qemu, runtime, ui, vmstate
from vmctl.errors import VMError

CHECKPOINTS_DIR = "checkpoints"
MANIFEST_FILE = "manifest.json"
MANIFEST_VERSION = 2
# The qcow2 header extension that names the backing file's format.
QCOW2_EXT_BACKING_FORMAT = 0xE2792ACA
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# Formats ``qemu-img convert`` writes back losslessly for our purposes.
SUPPORTED_FORMATS = ("qcow2", "raw", "vpc", "vdi", "vmdk")

DISK_EXTENSIONS = {"qcow2": "qcow2", "raw": "img", "vpc": "vhd", "vdi": "vdi", "vmdk": "vmdk"}


# --- paths and validation ---------------------------------------------------------------

def checkpoints_dir(vm_name: str) -> Path:
    return runtime.vm_artifact_base(vm_name) / CHECKPOINTS_DIR


def checkpoint_dir(vm_name: str, name: str) -> Path:
    return checkpoints_dir(vm_name) / validate_name(name)


def validate_name(name: str | None) -> str:
    """A checkpoint name is a directory name: letters, digits, ``.``, ``_``, ``-``, up to 64
    characters, starting with a letter or digit (so never ``.``, ``..`` or a hidden staging
    directory), never a path."""
    if not name:
        raise VMError("A checkpoint name is required (for example clean-install, before-update)")
    if not NAME_RE.match(name):
        raise VMError(
            f"Invalid checkpoint name {name!r}: use letters, digits, '.', '_' or '-' (up to 64 characters, "
            "starting with a letter or digit)"
        )
    return name


def disk_format(vm: dict[str, Any]) -> str:
    return str((vm.get("disk") or {}).get("format", "qcow2"))


def disk_file_name(fmt: str) -> str:
    return f"disk.{DISK_EXTENSIONS.get(fmt, fmt)}"


def firmware_vars_path(vm: dict[str, Any]) -> Path | None:
    fw = vm.get("firmware") or {}
    if fw.get("type") != "efi" or not fw.get("vars_path"):
        return None
    return runtime.resolve_path(str(fw["vars_path"]))


def check_profile(vm_name: str, vm: dict[str, Any]) -> None:
    """What a checkpoint can and cannot carry for this profile."""
    if vm.get("tpm"):
        raise VMError(
            f"VM '{vm_name}' declares TPM state: checkpoints carry the disk and the EFI vars only, "
            "and a TPM whose state is not saved would not match the restored disk. Not supported."
        )
    for disk in disks_of(vm):
        if disk["format"] not in SUPPORTED_FORMATS:
            raise VMError(f"VM '{vm_name}' uses disk format '{disk['format']}' ({disk['role']}), which checkpoints "
                          f"do not handle (supported: {', '.join(SUPPORTED_FORMATS)})")


def disks_of(vm: dict[str, Any]) -> list[dict[str, Any]]:
    """Every disk a checkpoint carries: the main one, then the profile's extra_disks in order.
    ``file`` is the name inside the checkpoint directory."""
    fmt = disk_format(vm)
    rows = [{"role": "disk", "path": runtime.resolve_path(str(vm["disk"]["path"])), "format": fmt, "file": disk_file_name(fmt)}]
    for index, extra in enumerate(qemu.extra_disks(vm)):
        efmt = str(extra["format"])
        rows.append({"role": f"extra{index}", "path": runtime.resolve_path(str(extra["path"])), "format": efmt,
                     "file": f"extra{index}.{DISK_EXTENSIONS.get(efmt, efmt)}"})
    return rows


def qcow2_backing(path: Path) -> tuple[Path, str] | None:
    """The backing file of a qcow2 overlay and its format, read from the header (no qemu-img:
    tests and dry runs see it too); None for an image without one. A relative name is relative
    to the overlay's directory, as QEMU reads it."""
    try:
        with path.open("rb") as handle:
            header = handle.read(112)
            if len(header) < 72 or header[:4] != b"QFI\xfb":
                return None
            version = int.from_bytes(header[4:8], "big")
            offset = int.from_bytes(header[8:16], "big")
            size = int.from_bytes(header[16:20], "big")
            if offset == 0 or size == 0:
                return None
            handle.seek(offset)
            name = handle.read(size).decode("utf-8", errors="replace")
            fmt = ""
            position = int.from_bytes(header[100:104], "big") if version >= 3 and len(header) >= 104 else 72
            for _ in range(64):
                handle.seek(position)
                head = handle.read(8)
                if len(head) < 8:
                    break
                ext_type, ext_len = int.from_bytes(head[:4], "big"), int.from_bytes(head[4:8], "big")
                if ext_type == 0:
                    break
                data = handle.read(ext_len)
                if ext_type == QCOW2_EXT_BACKING_FORMAT:
                    fmt = data.decode("ascii", errors="replace")
                    break
                position += 8 + ((ext_len + 7) // 8) * 8
    except OSError:
        return None
    backing = Path(name)
    if not backing.is_absolute():
        backing = path.parent / backing
    if not fmt:
        raise VMError(f"{path} is an overlay on {backing} but its header names no backing format: "
                      "checkpoints cannot copy it as an overlay (qemu-img rebase -F <format> fixes the header)")
    return backing, fmt


# --- reading ----------------------------------------------------------------------------

def load_manifest(directory: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads((directory / MANIFEST_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def list_checkpoints(vm_name: str) -> list[dict[str, Any]]:
    """Every complete checkpoint of the VM, oldest first, with what is measured about its
    copy (bytes on the host) and what its record said about the disk (``label``)."""
    base = checkpoints_dir(vm_name)
    if not base.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for directory in sorted(base.iterdir()):
        if directory.name.startswith(".") or not directory.is_dir():
            continue  # staging, a replaced or half-deleted checkpoint
        manifest = load_manifest(directory)
        if manifest is None:
            continue
        disk = manifest.get("disk") or {}
        disk_path = directory / str(disk.get("file", ""))
        facts = vmstate.image_facts(disk_path, str(disk.get("format", "")))
        extras = [e for e in (manifest.get("extra_disks") or []) if isinstance(e, dict)]
        host_bytes = int(facts["host_bytes"] or 0) + sum(
            int(vmstate.image_facts(directory / str(e.get("file", "")), str(e.get("format", "")))["host_bytes"] or 0) for e in extras)
        record: dict[str, Any] = {}
        state_file = manifest.get("state_file")
        if state_file:
            try:
                loaded = json.loads((directory / str(state_file)).read_text(encoding="utf-8"))
                record = loaded if isinstance(loaded, dict) else {}
            except (OSError, ValueError):
                record = {}
        known = vmstate.describe(record, facts)
        rows.append({
            "name": directory.name,
            "path": str(directory),
            "created_at": manifest.get("created_at"),
            "note": manifest.get("note"),
            "format": disk.get("format"),
            "compressed": bool(manifest.get("compressed")),
            "virtual_bytes": disk.get("virtual_bytes") or facts["virtual_bytes"],
            "host_bytes": host_bytes,
            "extra_disks": len(extras),
            "overlay": bool(disk.get("backing")),
            "nvram": bool(manifest.get("nvram_file")),
            "label": known["label"],
            "detail": known["detail"],
            "complete": facts["present"],
        })
    # Oldest first; two checkpoints written in the same second keep their directory order.
    rows.sort(key=lambda row: (str(row.get("created_at") or ""), Path(row["path"]).stat().st_mtime_ns))
    return rows


# --- create -----------------------------------------------------------------------------

def convert_image(src: Path, src_format: str, dst: Path, dst_format: str, compress: bool, dry_run: bool,
                  backing: tuple[Path, str] | None = None) -> None:
    """One copy through ``qemu-img convert``: format-aware, zero-detecting, sparse on the way
    out. With *backing* (qcow2 output only) the copy is an overlay on that file holding only
    what differs from it. ``-p`` prints its own progress line; the output stays on the terminal."""
    runtime.require_command("qemu-img")
    cmd = ["qemu-img", "convert", "-p", "-f", src_format, "-O", dst_format]
    if compress and dst_format == "qcow2":
        cmd.append("-c")
    if backing is not None:
        if dst_format != "qcow2":
            raise VMError(f"an overlay copy needs qcow2 output, not {dst_format}")
        cmd += ["-B", str(backing[0]), "-F", backing[1]]
    cmd += [str(src), str(dst)]
    runtime.run(cmd, dry_run=dry_run)
    if not dry_run and not dst.is_file():
        raise VMError(f"qemu-img convert produced no image at {dst}")


def create(vm_name: str, vm: dict[str, Any], name: str, note: str | None = None,
           compress: bool = False, replace: bool = False, dry_run: bool = False) -> Path:
    """Copy the current disks (and EFI vars, and state record) into a new checkpoint."""
    check_profile(vm_name, vm)
    final = checkpoint_dir(vm_name, name)
    disks = disks_of(vm)
    main = disks[0]
    for disk in disks:
        if not disk["path"].is_file():
            what = "no disk image to checkpoint" if disk["role"] == "disk" else f"no {disk['role']} image (an extra disk)"
            raise VMError(f"VM '{vm_name}' has {what}: {disk['path']}")
        disk["backing"] = qcow2_backing(disk["path"]) if disk["format"] == "qcow2" else None
        if disk["backing"] is not None and not disk["backing"][0].is_file():
            raise VMError(f"{disk['path']} is an overlay on {disk['backing'][0]}, which is missing: the disk itself cannot boot")
        disk["facts"] = vmstate.image_facts(disk["path"], disk["format"])
    if final.exists() and not replace:
        raise VMError(f"Checkpoint '{name}' of '{vm_name}' already exists; pick another name or pass --replace")
    if compress and main["format"] != "qcow2":
        ui.print_status("warn", f"--compress applies to qcow2 only; the {main['format']} copy is written uncompressed", ok=False)
        compress = False

    vars_path = firmware_vars_path(vm)
    staging = final.parent / f".{final.name}.tmp-{os.getpid()}"
    ui.print_header(f"Checkpoint '{name}' of {vm_name}")
    for disk in disks:
        label = "disk" if disk["role"] == "disk" else disk["role"]
        overlay = f", overlay on {ui.pretty_path(disk['backing'][0])}: only what differs is copied" if disk["backing"] else ""
        ui.print_kv(label, f"{ui.pretty_path(disk['path'])} ({disk['format']}, {runtime.format_bytes(disk['facts']['host_bytes'])} on the host{overlay})")
    if vars_path is not None:
        ui.print_kv("EFI vars", ui.pretty_path(vars_path) if vars_path.is_file() else f"{ui.pretty_path(vars_path)} (not created yet: none saved)")
    ui.print_kv("into", ui.pretty_path(final))
    if final.exists():
        ui.print_status("warn", f"Replacing the existing checkpoint '{name}'", ok=False)
    if dry_run:
        ui.print_note(f"Would assemble the copy in {ui.pretty_path(staging)} and rename it into place")

    if staging.exists():
        shutil.rmtree(staging)
    if not dry_run:
        staging.mkdir(parents=True)
    try:
        entries: list[dict[str, Any]] = []
        for disk in disks:
            convert_image(disk["path"], disk["format"], staging / disk["file"], disk["format"],
                          compress and disk["format"] == "qcow2", dry_run, backing=disk["backing"])
            entries.append({"format": disk["format"], "file": disk["file"], "virtual_bytes": disk["facts"]["virtual_bytes"],
                            "source_host_bytes": disk["facts"]["host_bytes"],
                            "backing": str(disk["backing"][0]) if disk["backing"] else None,
                            "backing_format": disk["backing"][1] if disk["backing"] else None})
        nvram_file: str | None = None
        if vars_path is not None and vars_path.is_file():
            nvram_file = "nvram.fd"
            if not dry_run:
                shutil.copy2(vars_path, staging / nvram_file)
        state_file: str | None = None
        record_path = vmstate.state_path(vm_name)
        if record_path.is_file():
            state_file = vmstate.STATE_FILE
            if not dry_run:
                shutil.copy2(record_path, staging / state_file)
        manifest = {
            "version": MANIFEST_VERSION,
            "name": name,
            "vm": vm_name,
            "created_at": vmstate.now(),
            "note": note or None,
            "firmware": str((vm.get("firmware") or {}).get("type", "bios")),
            "disk": entries[0],
            "extra_disks": entries[1:],
            "compressed": compress,
            "nvram_file": nvram_file,
            "state_file": state_file,
        }
        if not dry_run:
            (staging / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            replaced: Path | None = None
            if final.exists():
                replaced = final.parent / f".{final.name}.replaced-{os.getpid()}"
                os.rename(final, replaced)
            os.rename(staging, final)
            if replaced is not None:
                shutil.rmtree(replaced, ignore_errors=True)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    ui.print_status("ok", f"Checkpoint '{name}' written for '{vm_name}'" + (" (dry run)" if dry_run else ""))
    return final


# --- restore ----------------------------------------------------------------------------

def _saved_backing(entry: dict[str, Any], name: str) -> tuple[Path, str] | None:
    if not entry.get("backing"):
        return None
    backing = Path(str(entry["backing"]))
    if not backing.is_file():
        raise VMError(f"Checkpoint '{name}' is an overlay on {backing}, which is missing: it cannot be restored "
                      "(the cloud image under isos/.cloudimg/ was removed)")
    return backing, str(entry.get("backing_format") or "qcow2")


def restore(vm_name: str, vm: dict[str, Any], name: str, dry_run: bool = False) -> None:
    """Put the checkpoint's disks, EFI vars and record back in place of the current ones.

    The expensive and fallible step (the conversions) runs into a staging directory while the
    current disks are untouched; the swap is renames only, and a failed rename puts back what
    was already moved. The previous files are removed with the staging directory at the end.
    """
    check_profile(vm_name, vm)
    source = checkpoint_dir(vm_name, name)
    manifest = load_manifest(source)
    if manifest is None:
        raise VMError(f"Checkpoint '{name}' of '{vm_name}' does not exist (vmctl checkpoint list {vm_name})")
    targets = disks_of(vm)
    saved = [manifest.get("disk") or {}] + [e for e in (manifest.get("extra_disks") or []) if isinstance(e, dict)]
    if len(saved) != len(targets):
        raise VMError(f"Checkpoint '{name}' holds {len(saved)} disk(s) but '{vm_name}' has {len(targets)} now "
                      f"(main disk plus extra_disks): restoring part of them would mix two moments, refused")
    pairs: list[tuple[dict[str, Any], Path, str, tuple[Path, str] | None]] = []
    for target, entry in zip(targets, saved):
        src = source / str(entry.get("file", ""))
        if not src.is_file():
            raise VMError(f"Checkpoint '{name}' is incomplete: {src} is missing")
        backing = _saved_backing(entry, name)
        if backing is not None and target["format"] != "qcow2":
            raise VMError(f"Checkpoint '{name}' holds a qcow2 overlay but the profile's {target['role']} is {target['format']} now")
        pairs.append((target, src, str(entry.get("format", "qcow2")), backing))

    disk_path = targets[0]["path"]
    vars_path = firmware_vars_path(vm)
    nvram_src = source / str(manifest["nvram_file"]) if manifest.get("nvram_file") else None
    state_src = source / str(manifest["state_file"]) if manifest.get("state_file") else None
    record_path = vmstate.state_path(vm_name)

    ui.print_header(f"Restore checkpoint '{name}' into {vm_name}")
    ui.print_kv("from", ui.pretty_path(source))
    ui.print_kv("created", str(manifest.get("created_at") or "?") + (f"  ({manifest['note']})" if manifest.get("note") else ""))
    for target, src, src_format, _backing in pairs:
        ui.print_kv("disk" if target["role"] == "disk" else target["role"],
                    f"{ui.pretty_path(target['path'])} <- {src.name} ({src_format} -> {target['format']})")
    if vars_path is not None:
        ui.print_kv("EFI vars", f"{ui.pretty_path(vars_path)} <- {nvram_src.name if nvram_src else 'none saved: the store is reset'}")

    staging = disk_path.parent / f".restore-{name}-{os.getpid()}"
    previous = staging / "previous"

    def staged_name(target: dict[str, Any]) -> str:
        return f"{target['role']}-{target['path'].name}"

    if dry_run:
        ui.print_note(f"Would convert into {ui.pretty_path(staging)} and swap the files with renames")
        for target, src, src_format, backing in pairs:
            convert_image(src, src_format, staging / staged_name(target), target["format"], False, True, backing=backing)
        return

    if staging.exists():
        shutil.rmtree(staging)
    previous.mkdir(parents=True)
    try:
        for target, src, src_format, backing in pairs:
            convert_image(src, src_format, staging / staged_name(target), target["format"], False, False, backing=backing)
        if nvram_src is not None and vars_path is not None:
            shutil.copy2(nvram_src, staging / vars_path.name)
        if state_src is not None:
            shutil.copy2(state_src, staging / record_path.name)

        # (current, staged or None, parked name): None means the current file is removed, not replaced.
        swaps: list[tuple[Path, Path | None, str]] = [(t["path"], staging / staged_name(t), staged_name(t)) for t, _s, _f, _b in pairs]
        if vars_path is not None:
            swaps.append((vars_path, staging / vars_path.name if nvram_src is not None else None, "vars-" + vars_path.name))
        swaps.append((record_path, staging / record_path.name if state_src is not None else None, "state-" + record_path.name))
        moved: list[tuple[Path, Path]] = []
        try:
            for current, staged, parked_name in swaps:
                if current.exists():
                    parked = previous / parked_name
                    os.rename(current, parked)
                    moved.append((current, parked))
                if staged is not None and staged.exists():
                    current.parent.mkdir(parents=True, exist_ok=True)
                    os.rename(staged, current)
        except OSError as exc:
            for current, parked in reversed(moved):
                try:
                    if current.exists():
                        current.unlink()
                    os.rename(parked, current)
                except OSError:
                    pass
            raise VMError(f"Restore of '{name}' into '{vm_name}' failed while swapping files: {exc}; "
                          f"the previous files were put back (leftovers, if any, under {staging})") from exc
    except BaseException:
        # A conversion or a copy failed: nothing has been swapped yet.
        if previous.exists() and not any(previous.iterdir()):
            shutil.rmtree(staging, ignore_errors=True)
        raise
    shutil.rmtree(staging, ignore_errors=True)
    # The record travelled with the disk (keep_facts); without one the disk is a restore of
    # unknown standing until a check runs on it.
    vmstate.record_origin(vm_name, "restore", name, keep_facts=state_src is not None)
    ui.print_status("ok", f"Checkpoint '{name}' restored into '{vm_name}'")


# --- delete -----------------------------------------------------------------------------

def delete(vm_name: str, name: str, dry_run: bool = False) -> None:
    target = checkpoint_dir(vm_name, name)
    if not target.is_dir():
        raise VMError(f"Checkpoint '{name}' of '{vm_name}' does not exist")
    ui.print_note(f"Removing checkpoint {ui.pretty_path(target)}")
    if dry_run:
        return
    doomed = target.parent / f".{target.name}.deleting-{os.getpid()}"
    os.rename(target, doomed)
    shutil.rmtree(doomed)
    base = target.parent
    if base.is_dir() and not any(base.iterdir()):
        base.rmdir()
    ui.print_status("ok", f"Checkpoint '{name}' of '{vm_name}' removed")


def delete_all(vm_name: str, dry_run: bool = False) -> int:
    """``vmctl clean --checkpoints``: every checkpoint of the VM, staging leftovers included."""
    base = checkpoints_dir(vm_name)
    if not base.is_dir():
        return 0
    count = sum(1 for entry in base.iterdir() if entry.is_dir())
    ui.print_note(f"Removing {count} checkpoint directories under {ui.pretty_path(base)}")
    if not dry_run:
        shutil.rmtree(base)
    return count
