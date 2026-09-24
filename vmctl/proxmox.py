"""Proxmox VE automated installation: an answer file grafted on the vendor ISO, the installer booted directly.

The ISO's own GRUB offers "Install Proxmox VE (Automated)" as soon as ``/auto-installer-mode.toml``
exists at its root, and the installer then reads ``/answer.toml`` from the same medium. That is all
``proxmox-auto-install-assistant prepare-iso --fetch-from iso`` does (it maps both files with
``xorriso -boot_image any keep``), so the host needs xorriso, not the Proxmox tool or Docker.
The kernel and initrd are booted with ``-kernel`` to add ``console=ttyS0``: the installer's log then
reaches the serial log instead of the framebuffer only.

``reboot-mode = "power-off"`` ends the install with the installer's own shutdown (it unmounts and
exports the ZFS pool first), and QEMU runs with ``-no-reboot``: the natural exit is the completion
signal, like the Ubuntu autoinstall flow. A failed install drops to a shell (``reboot-on-error``
stays false), so a failure shows as the installer timeout with the console in the log.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from vmctl import qemu, runtime, ui
from vmctl.errors import VMError

KERNEL_MEMBER = "boot/linux26"
INITRD_MEMBER = "boot/initrd.img"
KERNEL_APPEND = ("ro ramdisk_size=16777216 rw quiet splash=silent proxmox-start-auto-installer "
                 "console=ttyS0,115200")
FILESYSTEMS = ("ext4", "xfs", "zfs", "btrfs")
ZFS_RAID_DISKS = {"raid0": 1, "raid1": 2, "raid10": 4, "raidz-1": 3, "raidz-2": 4, "raidz-3": 5}


def proxmox_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    cfg = vm.get("proxmox_config")
    if cfg is not None and not isinstance(cfg, dict):
        raise VMError("Invalid proxmox_config: expected object")
    return cfg


def proxmox_artifact_dir(vm: dict[str, Any]) -> Path:
    """Where the per-VM ISO with the answer file lives (removed by ``vmctl clean``)."""
    return runtime.resolve_path(vm["disk"]["path"]).parent / "proxmox"


def disk_names(vm: dict[str, Any]) -> list[str]:
    """Guest names of the profile's disks: the main disk and every extra disk, all virtio (vda, vdb, ...)."""
    count = 1 + len(qemu.extra_disks(vm))
    return [f"vd{chr(ord('a') + index)}" for index in range(count)]


def check_profile(vm_name: str, vm: dict[str, Any]) -> None:
    cfg = proxmox_config(vm)
    if not cfg:
        raise VMError(f"{vm_name}: proxmox_config is required")
    if not cfg.get("root_password_hash") and not cfg.get("root_password"):
        raise VMError(f"{vm_name}: proxmox_config needs root_password_hash (or root_password)")
    if cfg.get("root_password_hash") and cfg.get("root_password"):
        raise VMError(f"{vm_name}: proxmox_config takes root_password_hash or root_password, not both")
    if vm["disk"].get("interface", "virtio") != "virtio":
        raise VMError(f"{vm_name}: the answer file names virtio disks (vda, vdb, ...)")
    filesystem = str(cfg.get("filesystem") or "zfs")
    if filesystem not in FILESYSTEMS:
        raise VMError(f"{vm_name}: proxmox_config.filesystem must be one of {', '.join(FILESYSTEMS)}")
    if filesystem == "zfs":
        raid = str((cfg.get("zfs") or {}).get("raid") or "raid0")
        if raid not in ZFS_RAID_DISKS:
            raise VMError(f"{vm_name}: proxmox_config.zfs.raid must be one of {', '.join(ZFS_RAID_DISKS)}")
        if len(disk_names(vm)) < ZFS_RAID_DISKS[raid]:
            raise VMError(f"{vm_name}: ZFS {raid} needs {ZFS_RAID_DISKS[raid]} disks; the profile has "
                          f"{len(disk_names(vm))} (disk + extra_disks)")
    ssh_cfg = vm.get("ssh_provision") or {}
    if ssh_cfg.get("user") != "root":
        raise VMError(f"{vm_name}: Proxmox VE has only root: ssh_provision.user must be root")
    if (vm.get("memory_mb") or 0) < 2048:
        raise VMError(f"{vm_name}: the Proxmox VE installer needs at least 2048 MB of RAM")


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        # A JSON string is a valid TOML basic string (same escapes, \\uXXXX included).
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise VMError(f"answer.toml: unsupported value {value!r}")


def _toml_section(name: str, values: dict[str, Any]) -> list[str]:
    """One table; nested dicts become dotted keys (``zfs.raid = "raid1"``), as in the vendor examples."""
    lines = [f"[{name}]"]

    def emit(prefix: str, table: dict[str, Any]) -> None:
        for key, value in table.items():
            if isinstance(value, dict):
                emit(f"{prefix}{key}.", value)
            elif value is not None:
                lines.append(f"{prefix}{key} = {_toml_value(value)}")

    emit("", values)
    return lines + [""]


def render_answer(vm_name: str, vm: dict[str, Any], keys: list[str]) -> str:
    check_profile(vm_name, vm)
    cfg = proxmox_config(vm) or {}
    fqdn = str(cfg.get("fqdn") or f"{vm_name}.lab.internal")
    global_section: dict[str, Any] = {
        "keyboard": str(cfg.get("keyboard") or "en-us"),
        "country": str(cfg.get("country") or "us"),
        "fqdn": fqdn,
        "mailto": str(cfg.get("mailto") or f"root@{fqdn}"),
        "timezone": str(cfg.get("timezone") or "UTC"),
        "root-ssh-keys": list(keys),
        "reboot-on-error": False,
        "reboot-mode": "power-off",
    }
    if cfg.get("root_password_hash"):
        global_section["root-password-hashed"] = str(cfg["root_password_hash"])
    else:
        global_section["root-password"] = str(cfg["root_password"])
    network: dict[str, Any] = {"source": "from-dhcp"}
    network.update(cfg.get("network") or {})
    filesystem = str(cfg.get("filesystem") or "zfs")
    disk_setup: dict[str, Any] = {"filesystem": filesystem, "disk-list": disk_names(vm)}
    if filesystem == "zfs":
        disk_setup["zfs"] = dict(cfg.get("zfs") or {"raid": "raid0"})
    elif cfg.get(filesystem):
        disk_setup[filesystem] = dict(cfg[filesystem])
    lines = [f"# Rendered by vmctl for {vm_name}: Proxmox VE automated installation answer file.", ""]
    lines += _toml_section("global", global_section)
    lines += _toml_section("network", network)
    lines += _toml_section("disk-setup", disk_setup)
    return "\n".join(lines)


AUTO_INSTALLER_MODE = 'mode = "iso"\n'


def ensure_install_iso(vm_name: str, vm: dict[str, Any], source: Path,
                       keys: list[str], dry_run: bool = False) -> Path:
    """Copy of the vendor ISO with ``/answer.toml`` and ``/auto-installer-mode.toml`` added (cached by stamp)."""
    answer = render_answer(vm_name, vm, keys)
    directory = proxmox_artifact_dir(vm)
    dest = directory / "install.iso"
    stamp_path = dest.with_suffix(".iso.source")
    stamp = None
    if source.is_file():
        st = source.stat()
        stamp = (f"{source.resolve()}\n{st.st_size}\n{st.st_mtime_ns}\n"
                 + hashlib.sha256((answer + AUTO_INSTALLER_MODE).encode()).hexdigest())
    if stamp and dest.is_file() and stamp_path.is_file() and stamp_path.read_text() == stamp:
        ui.print_status("ok", f"Proxmox VE unattended ISO: {ui.pretty_path(dest)}")
        return dest
    runtime.require_command("xorriso")
    work = directory / "iso-work"
    partial = dest.with_suffix(".iso.part")
    if not dry_run:
        work.mkdir(parents=True, exist_ok=True)
        (work / "answer.toml").write_text(answer, encoding="utf-8")
        (work / "answer.toml").chmod(0o600)
        (work / "auto-installer-mode.toml").write_text(AUTO_INSTALLER_MODE, encoding="utf-8")
    runtime.run(["cp", "--reflink=auto", str(source), str(partial)], dry_run=dry_run, quiet=True)
    # What prepare-iso runs: a new session on the copy, boot records kept as they are.
    runtime.run(["xorriso", "-boot_image", "any", "keep", "-dev", str(partial),
                 "-map", str(work / "answer.toml"), "/answer.toml",
                 "-map", str(work / "auto-installer-mode.toml"), "/auto-installer-mode.toml"],
                dry_run=dry_run, quiet=True)
    if not dry_run:
        partial.replace(dest)
        shutil.rmtree(work)
        if stamp:
            stamp_path.write_text(stamp)
    ui.print_status("ok", f"Proxmox VE unattended ISO: {ui.pretty_path(dest)}")
    return dest


def install_media_args(path: Path) -> list[str]:
    return ["-drive", f"id=pvecd,file={path},format=raw,if=none,media=cdrom,readonly=on",
            "-device", "ide-cd,drive=pvecd,bus=ide.1"]
