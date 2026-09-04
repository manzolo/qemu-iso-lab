"""Omarchy ISO unattended-install configuration and cidata image helpers."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from vmctl import cloud_init, runtime
from vmctl.errors import VMError


EFI_PARTITION_ID = "ea21d3f2-82bb-49cc-ab5d-6f81ae94e18d"
ROOT_PARTITION_ID = "8c2c2b92-1070-455d-b76a-56263bab24aa"
MIB = 1024 * 1024
GIB = 1024 * MIB


def omarchy_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    config = vm.get("omarchy_config")
    if config is None:
        return None
    if not isinstance(config, dict):
        raise VMError("Invalid omarchy_config: expected object")
    return config


def omarchy_artifact_dir(vm: dict[str, Any]) -> Path:
    return runtime.resolve_path(vm["disk"]["path"]).parent / "omarchy"


def _disk_size_bytes(vm: dict[str, Any]) -> int:
    value = str(vm.get("disk", {}).get("size") or "").strip()
    match = re.fullmatch(r"([1-9][0-9]*)([KMGT])(?:i?B)?", value, re.IGNORECASE)
    if match is None:
        raise VMError(f"Omarchy requires disk.size such as '64G', got {value!r}")
    units = {"K": 1024, "M": MIB, "G": GIB, "T": 1024 * GIB}
    return int(match.group(1)) * units[match.group(2).upper()]


def render_user_configuration(vm_name: str, vm: dict[str, Any]) -> str:
    config = omarchy_config(vm)
    if config is None:
        raise VMError("VM profile does not define omarchy_config")

    disk_size = _disk_size_bytes(vm)
    boot_start = MIB
    boot_size = 2 * GIB
    root_start = boot_start + boot_size
    root_size = disk_size - root_start - MIB
    if root_size < 30 * GIB:
        raise VMError("Omarchy requires a disk of at least 32G")

    device = str(config.get("disk_device") or "/dev/vda").strip()
    hostname = str(config.get("hostname") or vm_name).strip()
    timezone = str(config.get("timezone") or "UTC").strip()
    keyboard = str(config.get("keyboard_layout") or "us").strip()
    locale = str(config.get("locale") or "en_US.UTF-8").strip()
    encrypted = bool(config.get("encrypt", False))

    disk_config: dict[str, Any] = {
        "config_type": "default_layout",
        "device_modifications": [
            {
                "device": device,
                "partitions": [
                    {
                        "btrfs": [],
                        "dev_path": None,
                        "flags": ["boot", "esp"],
                        "fs_type": "fat32",
                        "mount_options": [],
                        "mountpoint": "/boot",
                        "obj_id": EFI_PARTITION_ID,
                        "size": {"sector_size": {"unit": "B", "value": 512}, "unit": "B", "value": boot_size},
                        "start": {"sector_size": {"unit": "B", "value": 512}, "unit": "B", "value": boot_start},
                        "status": "create",
                        "type": "primary",
                    },
                    {
                        "btrfs": [
                            {"mountpoint": "/", "name": "@"},
                            {"mountpoint": "/home", "name": "@home"},
                            {"mountpoint": "/var/log", "name": "@log"},
                            {"mountpoint": "/var/cache/pacman/pkg", "name": "@pkg"},
                        ],
                        "dev_path": None,
                        "flags": [],
                        "fs_type": "btrfs",
                        "mount_options": ["compress=zstd"],
                        "mountpoint": None,
                        "obj_id": ROOT_PARTITION_ID,
                        "size": {"sector_size": {"unit": "B", "value": 512}, "unit": "B", "value": root_size},
                        "start": {"sector_size": {"unit": "B", "value": 512}, "unit": "B", "value": root_start},
                        "status": "create",
                        "type": "primary",
                    },
                ],
                "wipe": True,
            }
        ],
    }
    if encrypted:
        password = str(config.get("password") or "")
        if not password:
            raise VMError("omarchy_config.password is required when encrypt is true")
        disk_config["disk_encryption"] = {
            "encryption_type": "luks",
            "lvm_volumes": [],
            "iter_time": 2000,
            "partitions": [ROOT_PARTITION_ID],
            "encryption_password": password,
        }

    payload: dict[str, Any] = {
        "app_config": None,
        "archinstall-language": "English",
        "auth_config": {},
        "audio_config": {"audio": "pipewire"},
        "bootloader_config": {"bootloader": "Limine", "uki": False, "removable": False},
        "custom_commands": [],
        "omarchy_install": {
            "mode": "full_disk",
            "defer_provisioning": False,
            "target_mount": "/mnt",
            "boot": {
                "esp_mount": "/boot",
                "esp_path": "/EFI/limine",
                "efi_binary": "limine_x64.efi",
                "enable_fallback": True,
            },
            "storage": {"kernel": "linux"},
        },
        "disk_config": disk_config,
        "hostname": hostname,
        "kernels": ["linux"],
        "network_config": {"type": "iso"},
        "ntp": True,
        "parallel_downloads": 8,
        "script": None,
        "services": [],
        "swap": True,
        "timezone": timezone,
        "locale_config": {"kb_layout": keyboard, "sys_enc": "UTF-8", "sys_lang": locale},
        "mirror_config": {
            "custom_repositories": [],
            "custom_servers": [
                {"url": "https://mirror.omarchy.org/$repo/os/$arch"},
                {"url": "https://mirror.rackspace.com/archlinux/$repo/os/$arch"},
                {"url": "https://geo.mirror.pkgbuild.com/$repo/os/$arch"},
            ],
            "mirror_regions": {},
            "optional_repositories": [],
        },
        "packages": ["base-devel", "git", "omarchy-keyring", "omarchy-settings", "omarchy"],
        "profile_config": {"gfx_driver": None, "greeter": None, "profile": {}},
        "version": "3.0.9",
    }
    return json.dumps(payload, indent=2) + "\n"


def render_user_credentials(vm: dict[str, Any]) -> str:
    config = omarchy_config(vm)
    if config is None:
        raise VMError("VM profile does not define omarchy_config")
    username = str(config.get("username") or "").strip()
    password_hash = str(config.get("password_hash") or "").strip()
    if not username:
        raise VMError("omarchy_config.username is required")
    if not password_hash:
        raise VMError("omarchy_config.password_hash is required")
    payload: dict[str, Any] = {
        "root_enc_password": password_hash,
        "users": [{"enc_password": password_hash, "groups": [], "sudo": True, "username": username}],
    }
    if bool(config.get("encrypt", False)):
        password = str(config.get("password") or "")
        if not password:
            raise VMError("omarchy_config.password is required when encrypt is true")
        payload["encryption_password"] = password
    return json.dumps(payload, indent=2) + "\n"


def create_cidata_iso(vm_name: str, vm: dict[str, Any], dry_run: bool = False) -> Path:
    config = omarchy_config(vm)
    if config is None:
        raise VMError(f"VM '{vm_name}' does not define omarchy_config")
    keys = cloud_init._authorized_keys_for_vm(vm, dry_run=dry_run)
    files = {
        "user_configuration.json": render_user_configuration(vm_name, vm),
        "user_credentials.json": render_user_credentials(vm),
        "user_full_name.txt": str(config.get("full_name") or "") + "\n",
        "user_email_address.txt": str(config.get("email") or "") + "\n",
        "user_encrypt_installation.txt": ("true" if config.get("encrypt", False) else "false") + "\n",
        "authorized_keys": "".join(f"{key}\n" for key in keys),
    }
    return cloud_init.create_iso_with_files(
        omarchy_artifact_dir(vm),
        files,
        dry_run=dry_run,
        volume_id="cidata",
    )


def cidata_drive_args(seed_path: Path) -> list[str]:
    return ["-drive", f"file={seed_path},format=raw,if=virtio,media=cdrom,readonly=on"]
