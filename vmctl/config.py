"""VM profile loading and validation."""
from __future__ import annotations

from typing import Any, cast

from vmctl import state, runtime
from vmctl.errors import VMError


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

    return errors


def load_config() -> dict[str, Any]:
    profiles_dir = state.CONFIG_DIR / "profiles"

    if not state.CONFIG_DIR.is_dir():
        raise VMError(f"Missing config directory: {state.CONFIG_DIR}")

    if not profiles_dir.is_dir():
        raise VMError(f"Missing profiles directory: {profiles_dir}")

    merged_vms: dict[str, dict[str, Any]] = {}
    for path in sorted(profiles_dir.glob("*.json")):
        profile_data = runtime.load_json_file(path)
        if "vms" not in profile_data or not isinstance(profile_data["vms"], dict):
            raise VMError(f"Invalid profile file: {path}")
        for name, vm in profile_data["vms"].items():
            if name in merged_vms:
                raise VMError(f"Duplicate VM profile '{name}' in {path}")
            merged_vms[name] = vm

    if not merged_vms:
        raise VMError(f"No VM profiles found in: {profiles_dir}")

    all_errors: list[str] = []
    for name, vm in merged_vms.items():
        all_errors.extend(validate_vm_profile(name, vm))
    if all_errors:
        raise VMError("Invalid VM profile(s):\n  " + "\n  ".join(all_errors))

    return {"vms": merged_vms}


def get_vm(config: dict[str, Any], name: str) -> dict[str, Any]:
    try:
        return cast(dict[str, Any], config["vms"][name])
    except KeyError as exc:
        raise VMError(f"VM profile not found: {name}") from exc
