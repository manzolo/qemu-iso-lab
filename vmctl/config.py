"""VM profile loading and validation."""
from __future__ import annotations

import copy
import re
import sys
from datetime import date
from typing import Any, cast

from pathlib import Path

from vmctl import profile_bases, state, runtime
from vmctl.errors import VMError

PROFILE_ALIASES: dict[str, str] = {
    "arch-dms-local": "arch-dms",
    "arch-dms-nvidia-local": "arch-dms-nvidia",
    "arch-noctalia-local": "arch-noctalia",
    "arch-omarchy-nvidia-local": "arch-omarchy-nvidia",
    "cachyos-local": "cachyos-desktop",
    "cachyos-nvidia-local": "cachyos-nvidia",
    "fedora-niri-dms-local": "fedora-niri-dms",
    "ubuntu-niri-local": "ubuntu-niri",
    "ubuntu-niri-gl": "ubuntu-niri",
    "archlinux": "arch",
    "cachyos": "cachyos-live",
    "ubuntu-desktop": "ubuntu-desktop-live",
    "ubuntu-server": "ubuntu-server-live",
    "ubuntu-server-headless": "ubuntu-server-ci",
    "rocky9": "rocky-9",
    "lubuntu22-lab": "lubuntu-lab",
    "alpine-installed-ci": "alpine-ci-installed",
    # 2026-09-26: "unattended" is the profile's meta.status, not part of its name. The unattended
    # profile takes the clean name; a manual twin is <name>-installer. The manual twins' old names
    # (freebsd, haiku, pearos-nicecore) now belong to the unattended profiles and cannot be aliases.
    "freebsd-unattended": "freebsd",
    "haiku-unattended": "haiku",
    "pearos-nicecore-unattended": "pearos-nicecore",
    "ubuntu-8.04-unattended": "ubuntu-8.04",
    "ubuntu-10.04-unattended": "ubuntu-10.04",
    "ubuntu-12.04-unattended": "ubuntu-12.04",
    "ubuntu-14.04-unattended": "ubuntu-14.04",
    "ubuntu-16.04-unattended": "ubuntu-16.04",
    "ubuntu-18.04-unattended": "ubuntu-18.04",
    "ubuntu-20.04-unattended": "ubuntu-20.04",
    "ubuntu-22.04-unattended": "ubuntu-22.04",
    "ubuntu-26.04-unattended": "ubuntu-26.04",
    "windows11-unattended": "windows-11",
    "windows10-unattended": "windows-10",
    "windows7-unattended": "windows-7",
    "windowsxp-unattended": "windows-xp",
    "windows2000-unattended": "windows-2000",
    "windowsnt4-unattended": "windows-nt4",
    "windows98-unattended": "windows-98",
    "windows11-template": "windows-11-installer",
    "windows10-template": "windows-10-installer"
}


_NATURAL_SPLIT = re.compile(r"(\d+)")


def natural_key(name: str) -> tuple[Any, ...]:
    """Sort key that orders embedded numbers by value: ubuntu-8.04 before ubuntu-10.04."""
    parts: list[Any] = []
    for i, chunk in enumerate(_NATURAL_SPLIT.split(name.lower())):
        parts.append((1, int(chunk)) if i % 2 else (0, chunk))
    return tuple(parts)


def sorted_vm_names(cfg: dict[str, Any]) -> list[str]:
    return sorted(cfg["vms"], key=natural_key)


def sorted_vm_items(cfg: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [(name, cfg["vms"][name]) for name in sorted_vm_names(cfg)]


def canonical_vm_name(name: str, *, warn: bool = True) -> str:
    canonical = PROFILE_ALIASES.get(name, name)
    if warn and canonical != name:
        print(f"warning: profile '{name}' is deprecated; use '{canonical}'", file=sys.stderr)
    return canonical


USER_PLACEHOLDER = "{{user}}"
# (section, field) pairs that declare the guest user of a VM profile.  They
# must agree with each other; their value replaces ``{{user}}`` everywhere
# else in the profile (paths, commands, sudoers content...).
USER_IDENTITY_FIELDS: tuple[tuple[str, str], ...] = (
    ("ssh_provision", "user"),
    ("cloud_init", "user"),
    ("autoinstall", "username"),
    ("archinstall_config", "username"),
    ("omarchy_config", "username"),
    ("preseed_config", "username"),
    ("ubiquity_config", "username"),
    ("kickstart_config", "username"),
    ("alpine_config", "username"),
    ("autoyast_config", "username"),
    ("pearos_config", "username"),
    ("nixos_config", "username"),
    ("windows_config", "username"),
    ("pfsense_config", "username"),
    ("freebsd_config", "username"),
    ("slackware_config", "username"),
)


def merge_vm_profile(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """A local override (or a profile's delta over its base): see ``profile_bases.merge``."""
    return profile_bases.merge(base, override)


def load_tracked(profiles_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """The tracked catalog alone (never local.json): every entry resolved over its base
    (``profile_bases``) with ``{{user}}`` still literal and nothing validated. What the profile
    versions, the web editor's catalog template and the repository tests look at."""
    directory = profiles_dir or (state.CONFIG_DIR / "profiles")
    vms: dict[str, dict[str, Any]] = {}
    bases: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        if path.name.startswith("local"):
            continue
        document = runtime.load_json_file(path)
        if not isinstance(document.get("vms"), dict):
            raise VMError(f"Invalid profile file: {path}")
        for name, entry in document["vms"].items():
            if name in vms:
                raise VMError(f"Duplicate VM profile '{name}' in {path}")
            vms[name] = cast(dict[str, Any], entry)
        for name, entry in profile_bases.bases_of(document, str(path)).items():
            if name in bases:
                raise VMError(f"Duplicate base '{name}' in {path}")
            bases[name] = entry
    return profile_bases.resolve_extends(vms, bases)


# meta.manual of a manual profile: why it is manual, what the catalog shows next to "Manual install".
MANUAL_REASONS = ("live", "image", "template", "ci", "twin", "todo")
MANUAL_LABELS = {"live": "live media", "image": "disk image", "template": "import template", "ci": "CI boot check",
                 "twin": "automated as", "todo": "awaiting automation"}
IDENTITY_KEYS = ("user", "password", "password_hash", "realname")


def validate_identity(identity: Any, path: str) -> dict[str, str]:
    """The optional top-level ``identity`` of local.json: one guest identity for every tracked profile."""
    if not isinstance(identity, dict):
        raise VMError(f"Invalid 'identity' in {path}: expected an object")
    unknown = sorted(set(identity) - set(IDENTITY_KEYS))
    if unknown:
        raise VMError(f"Invalid 'identity' in {path}: unknown key(s) {', '.join(unknown)} (allowed: {', '.join(IDENTITY_KEYS)})")
    clean: dict[str, str] = {}
    for key, value in identity.items():
        if not isinstance(value, str) or not value.strip():
            raise VMError(f"Invalid 'identity' in {path}: '{key}' must be a non-empty string")
        if USER_PLACEHOLDER in value:
            raise VMError(f"Invalid 'identity' in {path}: '{key}' cannot contain {USER_PLACEHOLDER}")
        clean[key] = value
    if "user" not in clean:
        raise VMError(f"Invalid 'identity' in {path}: 'user' is required")
    return clean


def apply_identity(vm: dict[str, Any], identity: dict[str, str], explicit: dict[str, Any]) -> None:
    """Move every identity section the profile has to ``identity``, keeping what a per-VM override set.

    Only sections that exist are touched (a profile without ``windows_config`` gets none), only
    the user name and the credential fields the section already carries (``password_hash``,
    ``password``, ``realname``): the per-VM override of ``local.json`` still wins field by field.
    """
    if vm.get("haiku_config") is not None:
        return  # Haiku has one user, `user` (haiku.SSH_USER): the identity cannot move it (matrix of 2026-09-29)
    for section, field in USER_IDENTITY_FIELDS:
        sec = vm.get(section)
        if not isinstance(sec, dict):
            continue
        explicit_section = explicit.get(section)
        kept: dict[str, Any] = explicit_section if isinstance(explicit_section, dict) else {}
        values = {field: identity["user"]}
        for key in ("password_hash", "password"):
            if key in sec and key in identity:
                values[key] = identity[key]
        if "realname" in sec:
            values["realname"] = identity.get("realname", identity["user"])
        for key, value in values.items():
            if key not in kept:
                sec[key] = value


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
    return profile_bases.replace_placeholder(value, USER_PLACEHOLDER, user)


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


GROUP_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def declared_groups(vm: dict[str, Any]) -> list[str]:
    """``meta.groups``: the categories a profile is tagged with by hand, for the ones the
    rest of the metadata cannot express (``ubuntu`` spans a dozen ``meta.slug`` values, the
    Windows retro set spans two families of answer file). Derived categories (family,
    status, role, install flow) are not declared here; see ``lifecycle.profile_groups``."""
    groups = (vm.get("meta") or {}).get("groups")
    if not isinstance(groups, list):
        return []
    return [str(group) for group in groups]


def validate_vm_profile(name: str, vm: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    def err(msg: str) -> None:
        errors.append(f"{name}: {msg}")

    for key in ("name", "iso", "disk", "firmware", "video", "memory_mb", "cpus"):
        if key == "iso" and "disk_image" in vm:
            continue  # the medium is a prepared disk image (SerenityOS: built, never installed)
        if key not in vm:
            err(f"missing required field '{key}'")
    image = vm.get("disk_image")
    if image is not None:
        if not isinstance(image, dict) or not isinstance(image.get("path"), str) or not image["path"]:
            err("disk_image needs a path")
        elif image.get("format", "raw") not in ("raw", "qcow2"):
            err("disk_image.format must be raw or qcow2")
        if "iso" in vm:
            err("a profile boots either an ISO or a disk_image, not both")

    if "name" in vm and not isinstance(vm["name"], str):
        err("name must be a string")
    if "iso" in vm and not isinstance(vm["iso"], str):
        err("iso must be a string")
    if "memory_mb" in vm and not isinstance(vm["memory_mb"], int):
        err("memory_mb must be an integer")
    if "cpus" in vm and not isinstance(vm["cpus"], int):
        err("cpus must be an integer")
    if "guest_agent" in vm and not isinstance(vm["guest_agent"], bool):
        err("guest_agent must be a boolean")

    meta = vm.get("meta")
    if "meta" in vm:
        if not isinstance(meta, dict):
            err("meta must be an object")
        else:
            if "status" in meta and meta["status"] not in ("manual", "unattended", "experimental"):
                err("meta.status must be manual, unattended or experimental")
            if "manual" in meta:
                # Why a manual profile is manual (the catalog badge): live media, a disk image, an import
                # template, a CI boot check, the manual twin of an automated profile, or still to automate.
                if meta.get("status", "manual") != "manual":
                    err("meta.manual belongs to manual profiles only")
                elif meta["manual"] not in MANUAL_REASONS:
                    err(f"meta.manual must be one of {', '.join(MANUAL_REASONS)}")
                elif meta["manual"] == "twin" and not (isinstance(meta.get("automated_as"), str) and meta["automated_as"].strip()):
                    err("meta.manual 'twin' names the automated profile in meta.automated_as")
            elif "automated_as" in meta:
                err("meta.automated_as goes with meta.manual: twin")
            if "version" in meta and not (isinstance(meta["version"], str) and re.fullmatch(r"\d+\.\d+\.\d+", meta["version"])):
                err("meta.version must be MAJOR.MINOR.PATCH (tools/bump_profile.py keeps it)")
            if "verified" in meta:
                verified = meta["verified"]
                try:
                    if not isinstance(verified, str) or date.fromisoformat(verified).isoformat() != verified:
                        raise ValueError
                except ValueError:
                    err("meta.verified must be a valid YYYY-MM-DD date")
            if "groups" in meta:
                groups = meta["groups"]
                if not isinstance(groups, list) or not all(isinstance(group, str) for group in groups):
                    err("meta.groups must be a list of strings")
                elif len(set(groups)) != len(groups):
                    err("meta.groups must not repeat a group")
                else:
                    for group in groups:
                        if not GROUP_RE.match(group):
                            err(f"meta.groups entry {group!r} must be lowercase letters, digits and hyphens")
            if "logins" in meta:
                logins = meta["logins"]
                if not isinstance(logins, list) or not all(
                        isinstance(e, dict) and isinstance(e.get("user"), str) and e["user"]
                        and all(isinstance(e.get(k, ""), str) for k in ("password", "note")) for e in logins):
                    err("meta.logins must be a list of {user, password?, note?} objects with string values")

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
        if "headless" in video:
            headless = video["headless"]
            if not isinstance(headless, list) or not headless or not all(isinstance(arg, str) for arg in headless):
                err("video.headless must be a non-empty list of strings")
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

    omarchy = vm.get("omarchy_config")
    if isinstance(omarchy, dict):
        password_hash = str(omarchy.get("password_hash") or "").strip()
        if password_hash == "REPLACE_WITH_SHA512_HASH":
            err("omarchy_config.password_hash still uses the placeholder value")

    return errors


def _disk_path_conflicts(vms: dict[str, dict[str, Any]]) -> list[str]:
    """Two profiles on one disk or one EFI vars file would install over each other (the manual
    twins renamed on 2026-09-26 kept artifacts/<old name>/, which became the unattended profile's)."""
    seen: dict[str, str] = {}
    errors: list[str] = []
    for name, vm in vms.items():
        raw_disk, raw_firmware = vm.get("disk"), vm.get("firmware")
        disk: dict[str, Any] = raw_disk if isinstance(raw_disk, dict) else {}
        firmware: dict[str, Any] = raw_firmware if isinstance(raw_firmware, dict) else {}
        for kind, path in (("disk.path", disk.get("path")), ("firmware.vars_path", firmware.get("vars_path"))):
            if not isinstance(path, str) or not path:
                continue
            other = seen.get(path)
            if other is None:
                seen[path] = name
            elif other != name:
                errors.append(f"VM profiles '{other}' and '{name}' share {kind} {path}")
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


def load_config(*, local_profiles: dict[str, Any] | None = None) -> dict[str, Any]:
    """Load profiles; an explicit local document allows validation before saving it."""
    profiles_dir = state.CONFIG_DIR / "profiles"

    if not state.CONFIG_DIR.is_dir():
        raise VMError(f"Missing config directory: {state.CONFIG_DIR}")

    if not profiles_dir.is_dir():
        raise VMError(f"Missing profiles directory: {profiles_dir}")

    merged_vms: dict[str, dict[str, Any]] = {}
    bases: dict[str, dict[str, Any]] = {}
    identity: dict[str, str] | None = None
    local_entries: dict[str, dict[str, Any]] = {}  # per-VM overrides of local.json, by canonical name
    tracked_names: set[str] = set()
    profile_paths = sorted(profiles_dir.glob("*.json"), key=lambda p: (p.name == "local.json", p.name))
    if local_profiles is not None and profiles_dir / "local.json" not in profile_paths:
        profile_paths.append(profiles_dir / "local.json")
    for path in profile_paths:
        profile_data = local_profiles if path.name == "local.json" and local_profiles is not None else runtime.load_json_file(path)
        if "vms" not in profile_data or not isinstance(profile_data["vms"], dict):
            raise VMError(f"Invalid profile file: {path}")
        if path.name == "local.json":
            tracked_names = set(merged_vms)
            if "identity" in profile_data:
                identity = validate_identity(profile_data["identity"], str(path))
        for base_name, base in profile_bases.bases_of(profile_data, str(path)).items():
            if base_name in bases:
                raise VMError(f"Duplicate base '{base_name}' in {path}")
            bases[base_name] = base
        local_names: set[str] = set()
        for name, vm in profile_data["vms"].items():
            if not isinstance(vm, dict):
                raise VMError(f"Invalid VM profile '{name}' in {path}: expected an object")
            if path.name == "local.json":
                name = canonical_vm_name(name)
                if name in local_names:
                    raise VMError(f"Conflicting local overrides for '{name}' in {path}")
                local_names.add(name)
            if name in merged_vms:
                if path.name == "local.json":
                    local_entries[name] = cast(dict[str, Any], vm)
                    merged_vms[name] = merge_vm_profile(merged_vms[name], cast(dict[str, Any], vm))
                    continue
                raise VMError(f"Duplicate VM profile '{name}' in {path}")
            merged_vms[name] = cast(dict[str, Any], vm)

    if not merged_vms:
        raise VMError(f"No VM profiles found in: {profiles_dir}")

    # A local override sits on the raw tracked entry and the base is applied afterwards: the
    # merge is associative, so base + (delta + override) is (base + delta) + override.
    merged_vms = profile_bases.resolve_extends(merged_vms, bases)

    # After the bases, so an identity section inherited from a base is seen too; the tracked
    # profiles only (a VM defined in local.json alone is already the user's own recipe).
    if identity is not None:
        for name in tracked_names:
            apply_identity(merged_vms[name], identity, local_entries.get(name, {}))

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
    name = canonical_vm_name(name)
    try:
        return cast(dict[str, Any], config["vms"][name])
    except KeyError as exc:
        raise VMError(f"VM profile not found: {name}") from exc
