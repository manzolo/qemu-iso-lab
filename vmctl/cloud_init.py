"""Cloud-init and autoinstall seed image generation."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from typing import Any

from vmctl import ui, runtime
from vmctl.errors import VMError


def _get_vm_section(vm: dict[str, Any], key: str) -> dict[str, Any] | None:
    config = vm.get(key)
    if config is None:
        return None
    if not isinstance(config, dict):
        raise VMError(f"Invalid {key} config: expected object")
    return config


def cloud_init_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    return _get_vm_section(vm, "cloud_init")


def ssh_provision_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    return _get_vm_section(vm, "ssh_provision")


def ssh_access_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    return ssh_provision_config(vm) or cloud_init_config(vm)


def autoinstall_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    return _get_vm_section(vm, "autoinstall")


def _vm_artifact_subdir(vm: dict[str, Any], subdir: str) -> Path:
    return runtime.resolve_path(vm["disk"]["path"]).parent / subdir


def cloud_init_artifact_dir(vm: dict[str, Any]) -> Path:
    return _vm_artifact_subdir(vm, "cloud-init")


def autoinstall_artifact_dir(vm: dict[str, Any]) -> Path:
    return _vm_artifact_subdir(vm, "autoinstall")


def collect_ssh_authorized_keys(config: dict[str, Any], allow_missing_file: bool = False) -> list[str]:
    keys: list[str] = []
    inline_keys = config.get("ssh_authorized_keys") or []
    for key in inline_keys:
        value = str(key).strip()
        if value:
            keys.append(value)
    keys_file = str(config.get("ssh_authorized_keys_file") or "").strip()
    if keys_file:
        path = runtime.expand_host_path(keys_file)
        if not path.is_file():
            if allow_missing_file:
                return keys
            raise VMError(f"SSH authorized keys file not found: {path}")
        for line in path.read_text(encoding="utf-8").splitlines():
            value = line.strip()
            if value and not value.startswith("#"):
                keys.append(value)
    return keys


def render_cloud_init_payload(config: dict[str, Any], dry_run: bool = False, include_user: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {"package_update": True}
    user = str(config.get("user") or "").strip()
    if include_user and user:
        user_entry: dict[str, Any] = {"name": user}
        ssh_keys = collect_ssh_authorized_keys(config, allow_missing_file=dry_run)
        if ssh_keys:
            user_entry["ssh_authorized_keys"] = ssh_keys
        payload["users"] = [user_entry]
    if config.get("packages"):
        payload["packages"] = list(config["packages"])
    if config.get("runcmd"):
        payload["runcmd"] = list(config["runcmd"])
    if config.get("write_files"):
        payload["write_files"] = list(config["write_files"])
    return payload


def render_cloud_init_user_data(vm: dict[str, Any], dry_run: bool = False) -> str:
    config = cloud_init_config(vm)
    if config is None:
        raise VMError("VM profile does not define cloud_init")
    payload = render_cloud_init_payload(config, dry_run=dry_run, include_user=True)
    return "#cloud-config\n" + json.dumps(payload, indent=2) + "\n"


def render_cloud_init_meta_data(vm_name: str, vm: dict[str, Any]) -> str:
    config = cloud_init_config(vm)
    if config is None:
        raise VMError("VM profile does not define cloud_init")
    return json.dumps(
        {"instance-id": config.get("instance_id", vm_name), "local-hostname": config.get("hostname", vm_name)},
        indent=2,
    ) + "\n"


def create_seed_image(artifact_dir: Path, user_data: str, meta_data: str, dry_run: bool = False) -> Path:
    user_data_path = artifact_dir / "user-data"
    meta_data_path = artifact_dir / "meta-data"
    seed_path = artifact_dir / "seed.iso"
    runtime.ensure_parent(user_data_path)
    if not dry_run:
        user_data_path.write_text(user_data, encoding="utf-8")
        meta_data_path.write_text(meta_data, encoding="utf-8")
    if shutil.which("cloud-localds"):
        cmd = ["cloud-localds", str(seed_path), str(user_data_path), str(meta_data_path)]
    elif shutil.which("genisoimage"):
        cmd = ["genisoimage", "-output", str(seed_path), "-volid", "cidata", "-joliet", "-rock", str(user_data_path), str(meta_data_path)]
    elif shutil.which("xorriso"):
        cmd = ["xorriso", "-as", "mkisofs", "-output", str(seed_path), "-volid", "cidata", "-joliet", "-rock", str(user_data_path), str(meta_data_path)]
    else:
        raise VMError("Missing cloud-init seed builder: install cloud-localds, genisoimage, or xorriso")
    runtime.run(cmd, dry_run=dry_run)
    return seed_path


def create_cloud_init_seed(vm_name: str, vm: dict[str, Any], dry_run: bool = False) -> Path:
    if cloud_init_config(vm) is None:
        raise VMError(f"VM '{vm_name}' does not define cloud_init")
    return create_seed_image(
        cloud_init_artifact_dir(vm),
        render_cloud_init_user_data(vm, dry_run=dry_run),
        render_cloud_init_meta_data(vm_name, vm),
        dry_run=dry_run,
    )


def cloud_init_drive_args(seed_path: Path) -> list[str]:
    return ["-drive", f"file={seed_path},format=raw,if=virtio,media=cdrom,readonly=on"]


def render_autoinstall_user_data(vm_name: str, vm: dict[str, Any], dry_run: bool = False) -> str:
    config = autoinstall_config(vm)
    if config is None:
        raise VMError("VM profile does not define autoinstall")
    ci = cloud_init_config(vm)
    username = str(config.get("username") or (ci or {}).get("user") or "").strip()
    hostname = str(config.get("hostname") or (ci or {}).get("hostname") or vm_name).strip()
    password_hash = str(config.get("password_hash") or "").strip()
    if not username:
        raise VMError("autoinstall.username is required")
    if not password_hash:
        raise VMError("autoinstall.password_hash is required")
    autoinstall_section: dict[str, Any] = {
        "version": 1,
        "identity": {"hostname": hostname, "password": password_hash, "realname": str(config.get("realname") or username), "username": username},
        "keyboard": {"layout": str(config.get("keyboard_layout") or "us")},
        "locale": str(config.get("locale") or "en_US.UTF-8"),
        "storage": {"layout": {"name": str(config.get("storage_layout") or "direct")}},
    }
    payload: dict[str, Any] = {"autoinstall": autoinstall_section}
    timezone = str(config.get("timezone") or "").strip()
    if timezone:
        autoinstall_section["timezone"] = timezone
    updates = str(config.get("updates") or "").strip()
    if updates:
        if updates not in {"security", "all"}:
            raise VMError("autoinstall.updates must be one of: security, all")
        autoinstall_section["updates"] = updates
    ssh_keys = collect_ssh_authorized_keys(ci or {}, allow_missing_file=dry_run)
    autoinstall_section["ssh"] = {
        "install-server": bool(config.get("install_ssh", True)),
        "authorized-keys": ssh_keys,
        "allow-pw": bool(config.get("allow_password", not ssh_keys)),
    }
    if config.get("packages"):
        autoinstall_section["packages"] = list(config["packages"])
    if ci is not None:
        autoinstall_section["user-data"] = render_cloud_init_payload(ci, dry_run=dry_run, include_user=False)
    return "#cloud-config\n" + json.dumps(payload, indent=2) + "\n"


def create_autoinstall_seed(vm_name: str, vm: dict[str, Any], dry_run: bool = False) -> Path:
    if autoinstall_config(vm) is None:
        raise VMError(f"VM '{vm_name}' does not define autoinstall")
    meta_data = json.dumps({"instance-id": f"{vm_name}-autoinstall", "local-hostname": vm_name}, indent=2) + "\n"
    return create_seed_image(
        autoinstall_artifact_dir(vm),
        render_autoinstall_user_data(vm_name, vm, dry_run=dry_run),
        meta_data,
        dry_run=dry_run,
    )
