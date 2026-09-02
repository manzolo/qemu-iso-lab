"""VM profile loading and validation."""
from __future__ import annotations

import copy
from typing import Any, cast

from vmctl import state, runtime
from vmctl.errors import VMError

USER_PLACEHOLDER = "{{user}}"
# (section, field) pairs that declare the guest user of a VM profile.  They
# must agree with each other; their value replaces ``{{user}}`` everywhere
# else in the profile (paths, commands, sudoers content...).
USER_IDENTITY_FIELDS: tuple[tuple[str, str], ...] = (
    ("ssh_provision", "user"),
    ("cloud_init", "user"),
    ("autoinstall", "username"),
    ("archinstall_config", "username"),
    ("preseed_config", "username"),
    ("kickstart_config", "username"),
)


def merge_vm_profile(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_vm_profile(cast(dict[str, Any], merged[key]), value)
        elif isinstance(value, list) and isinstance(merged.get(key), list):
            merged[key] = copy.deepcopy(merged[key]) + copy.deepcopy(value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def resolve_vm_user(vm: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return ``(user, error)`` from the identity fields of a VM profile."""
    found: dict[str, str] = {}
    for section, field in USER_IDENTITY_FIELDS:
        sec = vm.get(section)
        if not isinstance(sec, dict):
            continue
        value = str(sec.get(field) or "").strip()
        if not value:
            continue
        if USER_PLACEHOLDER in value:
            return None, f"{section}.{field} cannot itself contain {USER_PLACEHOLDER}"
        found[f"{section}.{field}"] = value
    distinct = sorted(set(found.values()))
    if len(distinct) > 1:
        detail = ", ".join(f"{k}={v!r}" for k, v in found.items())
        return None, f"guest user fields disagree ({detail})"
    return (distinct[0] if distinct else None), None


def _contains_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return USER_PLACEHOLDER in value
    if isinstance(value, dict):
        return any(_contains_placeholder(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_placeholder(v) for v in value)
    return False


def _substitute(value: Any, user: str) -> Any:
    if isinstance(value, str):
        return value.replace(USER_PLACEHOLDER, user)
    if isinstance(value, dict):
        return {k: _substitute(v, user) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, user) for v in value]
    return value


def expand_user_placeholder(name: str, vm: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Replace ``{{user}}`` in every string of *vm* with the declared guest user."""
    user, error = resolve_vm_user(vm)
    if error:
        return vm, [f"{name}: {error}"]
    if not _contains_placeholder(vm):
        return vm, []
    if user is None:
        return vm, [f"{name}: profile uses {USER_PLACEHOLDER} but declares no guest user (e.g. ssh_provision.user)"]
    return cast(dict[str, Any], _substitute(vm, user)), []


def validate_vm_profile(name: str, vm: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    def err(msg: str) -> None:
        errors.append(f"{name}: {msg}")

    for key in ("name", "iso", "disk", "firmware", "video", "memory_mb", "cpus"):
        if key not in vm:
            err(f"missing required field '{key}'")

    if "name" in vm and not isinstance(vm["name"], str):
        err("name must be a string")
    if "iso" in vm and not isinstance(vm["iso"], str):
        err("iso must be a string")
    if "memory_mb" in vm and not isinstance(vm["memory_mb"], int):
        err("memory_mb must be an integer")
    if "cpus" in vm and not isinstance(vm["cpus"], int):
        err("cpus must be an integer")

    disk = vm.get("disk")
    if isinstance(disk, dict):
        for k in ("path", "size", "format", "interface"):
            if k not in disk:
                err(f"disk.{k} is required")
            elif not isinstance(disk[k], str):
                err(f"disk.{k} must be a string")
    elif "disk" in vm:
        err("disk must be an object")

    firmware = vm.get("firmware")
    if isinstance(firmware, dict):
        fw_type = firmware.get("type")
        if fw_type not in ("efi", "bios"):
            err(f"firmware.type must be 'efi' or 'bios', got {fw_type!r}")
        elif fw_type == "efi":
            for k in ("code", "vars_template", "vars_path"):
                if k not in firmware:
                    err(f"firmware.{k} is required when firmware.type is 'efi'")
    elif "firmware" in vm:
        err("firmware must be an object")

    video = vm.get("video")
    if isinstance(video, dict):
        variants = video.get("variants")
        if not isinstance(variants, dict) or not variants:
            err("video.variants must be a non-empty object")
        default = video.get("default")
        if not isinstance(default, str):
            err("video.default must be a string")
        elif isinstance(variants, dict) and default not in variants:
            err(f"video.default {default!r} is not declared in video.variants")
        order = video.get("installer_order")
        if order is not None:
            if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
                err("video.installer_order must be a list of strings")
            elif isinstance(variants, dict):
                for v in order:
                    if v not in variants:
                        err(f"video.installer_order entry {v!r} is not declared in video.variants")
    elif "video" in vm:
        err("video must be an object")

    installer_boot = vm.get("installer_boot")
    if installer_boot is not None:
        if not isinstance(installer_boot, dict):
            err("installer_boot must be an object")
        else:
            for key in ("kernel", "initrd"):
                if key not in installer_boot:
                    err(f"installer_boot.{key} is required")
                elif not isinstance(installer_boot[key], str):
                    err(f"installer_boot.{key} must be a string")

    autoinstall = vm.get("autoinstall")
    if isinstance(autoinstall, dict):
        password_hash = str(autoinstall.get("password_hash") or "").strip()
        if password_hash == "REPLACE_WITH_SHA512_HASH":
            err("autoinstall.password_hash still uses the placeholder value")

    return errors


def _ssh_port_conflicts(vms: dict[str, dict[str, Any]]) -> list[str]:
    seen: dict[int, str] = {}
    errors: list[str] = []
    for name, vm in vms.items():
        cfg = vm.get("ssh_provision")
        if not isinstance(cfg, dict):
            cfg = vm.get("cloud_init")
        if not isinstance(cfg, dict):
            continue
        port = cfg.get("ssh_host_port")
        if port is None:
            continue
        try:
            port_int = int(port)
        except (TypeError, ValueError):
            continue
        other = seen.get(port_int)
        if other is None:
            seen[port_int] = name
            continue
        errors.append(f"Duplicate ssh_host_port {port_int} in VM profiles '{other}' and '{name}'")
    return errors


def load_config() -> dict[str, Any]:
    profiles_dir = state.CONFIG_DIR / "profiles"

    if not state.CONFIG_DIR.is_dir():
        raise VMError(f"Missing config directory: {state.CONFIG_DIR}")

    if not profiles_dir.is_dir():
        raise VMError(f"Missing profiles directory: {profiles_dir}")

    merged_vms: dict[str, dict[str, Any]] = {}
    profile_paths = sorted(profiles_dir.glob("*.json"), key=lambda p: (p.name == "local.json", p.name))
    for path in profile_paths:
        profile_data = runtime.load_json_file(path)
        if "vms" not in profile_data or not isinstance(profile_data["vms"], dict):
            raise VMError(f"Invalid profile file: {path}")
        for name, vm in profile_data["vms"].items():
            if name in merged_vms:
                if path.name == "local.json":
                    merged_vms[name] = merge_vm_profile(merged_vms[name], cast(dict[str, Any], vm))
                    continue
                raise VMError(f"Duplicate VM profile '{name}' in {path}")
            merged_vms[name] = cast(dict[str, Any], vm)

    if not merged_vms:
        raise VMError(f"No VM profiles found in: {profiles_dir}")

    all_errors: list[str] = []
    for name, vm in list(merged_vms.items()):
        expanded, errors = expand_user_placeholder(name, vm)
        merged_vms[name] = expanded
        all_errors.extend(errors)
        all_errors.extend(validate_vm_profile(name, expanded))
    all_errors.extend(_ssh_port_conflicts(merged_vms))
    if all_errors:
        raise VMError("Invalid VM profile(s):\n  " + "\n  ".join(all_errors))

    return {"vms": merged_vms}


def get_vm(config: dict[str, Any], name: str) -> dict[str, Any]:
    try:
        return cast(dict[str, Any], config["vms"][name])
    except KeyError as exc:
        raise VMError(f"VM profile not found: {name}") from exc
