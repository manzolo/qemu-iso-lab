"""``vmctl clone <origin> <destination>``: an independent copy of a stopped VM.

A clone is a new profile in ``vms/profiles/local.json`` plus its own ``artifacts/<dst>/``:

* the **profile** is the origin's resolved profile with every path under
  ``artifacts/<origin>/`` moved to ``artifacts/<dst>/``, a new display name, its own SSH
  host port and its own host ports for every slirp forward (the first free ones from
  ``CLONE_PORT_START``, or ``--ssh-port``), explicit ``mac`` values dropped (the default
  MAC derives from the disk path, so the clone's NICs differ on their own) and
  ``meta.clone_of`` naming the origin. It is written as a *complete* profile, so the clone
  no longer follows later edits of the tracked origin: that is the point of a clone. The
  tracked profiles are never touched, and the existing ``local.json`` is merged into, with
  a backup, never rewritten from scratch.
* the **artifacts** are a full ``qemu-img convert`` copy of the disk (no backing file, no
  hidden dependency on the origin image), the EFI variable store, the generated SSH key
  pair (the guest's ``authorized_keys`` holds its public half: without it the clone could
  not be reached) and the ``state.json`` record. PID files, sockets, logs, TUI jobs,
  checkpoints and generated installer seeds are not copied: they describe the origin.

What a clone does *not* change is inside the guest: hostname, ``/etc/machine-id``, SSH
host keys, static addresses and anything else the installed system wrote about itself
are byte-identical to the origin's. ``--identity regenerate`` handles the first three
over SSH for the Linux guests this lab provisions (systemd or OpenRC, passwordless sudo);
Windows (sysprep), pfSense, ReactOS, FreeBSD and NixOS (whose hostname is in the Nix
configuration) keep theirs and the command says what to do by hand. ``--identity keep``
is the default and states what is kept.

Everything is assembled in ``artifacts/.clone-<dst>-<pid>/`` and renamed into place, and
``local.json`` is written last: a failure at any point leaves the origin untouched and
publishes no half-made clone.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shlex
import shutil
from pathlib import Path
from typing import Any

from vmctl import checkpoint, cloud_init, config, qemu, runtime, state, ui, vmstate
from vmctl.errors import VMError

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")
CLONE_PORT_START = 2300
CLONE_PORT_END = 2999
IDENTITY_CHOICES = ("keep", "regenerate")

# Profile sections whose guests this lab cannot re-identify over SSH.
_NO_REGENERATE_SECTIONS: dict[str, str] = {
    "windows_config": "Windows: run sysprep /generalize inside the clone (or at least rename the computer)",
    "pfsense_config": "pfSense: the hostname and the SSH host keys live in config.xml; change them in the web GUI",
    "reactos_config": "ReactOS: rename the computer in the System properties",
    "freebsd_config": "FreeBSD: sysrc hostname=<name>, rm /etc/ssh/ssh_host_*, service sshd keygen",
    "nixos_config": "NixOS: networking.hostName is part of configuration.nix; edit it and nixos-rebuild switch",
    "windows98_config": "Windows 98: rename the computer in the Network control panel",
    "windowsxp_config": "Windows XP: rename the computer in the System properties",
    "windowsnt4_config": "Windows NT 4.0: rename the computer in the Network control panel",
}


def validate_name(name: str | None) -> str:
    if not name:
        raise VMError("A destination profile name is required (for example debian-server-2)")
    if not NAME_RE.match(name):
        raise VMError(f"Invalid profile name {name!r}: lowercase letters, digits, '.' and '-', "
                      "starting with a letter or digit, up to 64 characters (like the tracked profiles)")
    return name


def artifact_prefix(vm_name: str) -> str:
    return f"artifacts/{vm_name}/"


def _rewrite_paths(value: Any, src: str, dst: str) -> Any:
    """Every string mentioning artifacts/<src>/ now points at artifacts/<dst>/."""
    if isinstance(value, str):
        return value.replace(artifact_prefix(src), artifact_prefix(dst))
    if isinstance(value, list):
        return [_rewrite_paths(item, src, dst) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite_paths(item, src, dst) for key, item in value.items()}
    return value


def used_host_ports(cfg: dict[str, Any]) -> set[int]:
    """Every host port any profile forwards (SSH or hostfwd), in either phase."""
    ports: set[int] = set()
    for _, vm in config.sorted_vm_items(cfg):
        try:
            ports.update(qemu.host_ports(vm))
        except VMError:
            continue
    return ports


def allocate_ports(count: int, taken: set[int], preferred: int | None = None) -> list[int]:
    """`count` distinct free host ports: `preferred` first when given and free."""
    chosen: list[int] = []
    if preferred is not None:
        if preferred in taken:
            raise VMError(f"Host port {preferred} is already forwarded by another profile")
        chosen.append(preferred)
    candidate = CLONE_PORT_START
    while len(chosen) < count:
        if candidate > CLONE_PORT_END:
            raise VMError(f"No free host port left between {CLONE_PORT_START} and {CLONE_PORT_END}")
        if candidate not in taken and candidate not in chosen:
            chosen.append(candidate)
        candidate += 1
    return chosen


def check_source(src: str, vm: dict[str, Any]) -> None:
    if vm.get("network_lab"):
        raise VMError(f"'{src}' is a network lab member: its static address and the router's NAT rules are part "
                      "of the lab topology, so a copy would collide with it. Clone is not supported for lab members.")
    disk_path = runtime.resolve_path(str(vm["disk"]["path"]))
    if not disk_path.is_file():
        raise VMError(f"'{src}' has no disk image to clone: {disk_path}")
    checkpoint.check_profile(src, vm)


def check_destination(cfg: dict[str, Any], dst: str) -> None:
    validate_name(dst)
    if dst in cfg["vms"]:
        raise VMError(f"A profile named '{dst}' already exists")
    if dst in config.PROFILE_ALIASES:
        raise VMError(f"'{dst}' is a deprecated alias of '{config.PROFILE_ALIASES[dst]}'; pick another name")
    base = runtime.vm_artifact_base(dst)
    if base.exists() and any(base.iterdir()):
        raise VMError(f"{ui.pretty_path(base)} already exists and is not empty; remove it first")


def derive_profile(cfg: dict[str, Any], src: str, vm: dict[str, Any], dst: str, ssh_port: int | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """The clone's profile and a report of what changed (``{"ports": {old: new}, "macs_dropped": n}``)."""
    profile = _rewrite_paths(copy.deepcopy(vm), src, dst)
    assert isinstance(profile, dict)
    profile["name"] = f"{vm.get('name', src)} (clone of {src})"
    meta = dict(profile.get("meta") or {})
    meta["clone_of"] = src
    meta["cloned_at"] = vmstate.now()
    meta.pop("verified", None)  # the maintainer's live PASS is about the recipe, not this copy
    profile["meta"] = meta

    taken = used_host_ports(cfg)
    report: dict[str, Any] = {"ports": {}, "macs_dropped": 0}
    ssh_sections = [section for section in ("ssh_provision", "cloud_init")
                    if isinstance(profile.get(section), dict) and profile[section].get("ssh_host_port")]
    old_ssh = int(vm[ssh_sections[0]]["ssh_host_port"]) if ssh_sections else None
    forwards: list[dict[str, Any]] = []
    networks = profile.get("networks")
    if isinstance(networks, list):
        for nic in networks:
            if not isinstance(nic, dict):
                continue
            if nic.pop("mac", None):
                report["macs_dropped"] += 1
            for fwd in nic.get("hostfwd") or []:
                if isinstance(fwd, dict) and "host_port" in fwd:
                    forwards.append(fwd)
    if ssh_port is not None and old_ssh is None:
        raise VMError(f"'{src}' has no SSH host port to replace (--ssh-port applies to ssh_provision/cloud_init profiles)")
    new_ports = allocate_ports((1 if old_ssh is not None else 0) + len(forwards), taken, ssh_port)
    if old_ssh is not None:
        new_ssh = new_ports.pop(0)
        for section in ssh_sections:
            profile[section]["ssh_host_port"] = new_ssh
        report["ports"][old_ssh] = new_ssh
    for fwd in forwards:
        new_port = new_ports.pop(0)
        report["ports"][int(fwd["host_port"])] = new_port
        fwd["host_port"] = new_port
    return profile, report


def identity_support(vm: dict[str, Any]) -> tuple[bool, str]:
    """Whether ``--identity regenerate`` can run on this guest, and why not otherwise."""
    for section, advice in _NO_REGENERATE_SECTIONS.items():
        if section in vm:
            return False, advice
    if cloud_init.ssh_access_config(vm) is None:
        return False, "the profile has no SSH provisioning, so nothing can run inside the guest"
    return True, ""


def identity_script(new_hostname: str, old_hostname: str | None) -> str:
    """POSIX sh, run as root inside a Linux clone: new hostname, new machine-id, new SSH host keys.

    systemd hosts get ``hostnamectl`` and ``systemd-machine-id-setup``; OpenRC (Alpine) falls back
    to ``/etc/hostname`` + ``hostname`` and ``dbus-uuidgen``. ``/etc/hosts`` keeps working because the
    old name is rewritten where it appears. Nothing here reboots: the caller stops the VM.
    """
    new = shlex.quote(new_hostname)
    old = shlex.quote(old_hostname or "")
    return f"""set -e
NEW={new}
OLD={old}
if command -v hostnamectl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    hostnamectl set-hostname "$NEW"
else
    printf '%s\\n' "$NEW" > /etc/hostname
    hostname "$NEW" 2>/dev/null || true
fi
if [ -n "$OLD" ] && [ -f /etc/hosts ]; then
    sed -i "s/\\([[:space:]]\\)$OLD\\([[:space:]]\\|$\\)/\\1$NEW\\2/g" /etc/hosts
fi
rm -f /etc/machine-id /var/lib/dbus/machine-id
if command -v systemd-machine-id-setup >/dev/null 2>&1; then
    systemd-machine-id-setup
elif command -v dbus-uuidgen >/dev/null 2>&1; then
    dbus-uuidgen --ensure=/etc/machine-id
fi
if [ -f /etc/machine-id ] && [ -d /var/lib/dbus ] && [ ! -e /var/lib/dbus/machine-id ]; then
    ln -s /etc/machine-id /var/lib/dbus/machine-id
fi
rm -f /etc/ssh/ssh_host_*
ssh-keygen -A
sync
echo "==> identity regenerated: $(cat /etc/hostname 2>/dev/null || hostname) $(cat /etc/machine-id 2>/dev/null)"
"""


def guest_hostname(vm: dict[str, Any]) -> str | None:
    for section in ("ssh_provision", "cloud_init", "autoinstall", "archinstall_config", "preseed_config",
                    "kickstart_config", "alpine_config", "autoyast_config", "omarchy_config", "pearos_config"):
        sec = vm.get(section)
        if isinstance(sec, dict) and sec.get("hostname"):
            return str(sec["hostname"])
    return None


def copy_artifacts(src: str, vm: dict[str, Any], dst: str, profile: dict[str, Any], dry_run: bool = False) -> Path:
    """Disk (converted), EFI vars, SSH keys and record into a staging directory renamed to
    ``artifacts/<dst>`` at the end. Never touches ``artifacts/<src>``."""
    fmt = checkpoint.disk_format(vm)
    src_disk = runtime.resolve_path(str(vm["disk"]["path"]))
    dst_disk = runtime.resolve_path(str(profile["disk"]["path"]))
    if dst_disk == src_disk:
        raise VMError(f"'{src}' keeps its disk outside artifacts/{src}/ ({src_disk}); the clone would share it. "
                      "Move the disk under the VM's artifact directory first.")
    final = runtime.vm_artifact_base(dst)
    staging = final.parent / f".clone-{dst}-{os.getpid()}"
    src_vars = checkpoint.firmware_vars_path(vm)
    dst_vars = checkpoint.firmware_vars_path(profile)
    key_dir = runtime.vm_artifact_base(src) / "ssh"

    ui.print_kv("disk", f"{ui.pretty_path(src_disk)} -> {ui.pretty_path(dst_disk)} ({fmt}, full copy)")
    if src_vars is not None and src_vars.is_file() and dst_vars is not None:
        ui.print_kv("EFI vars", f"{ui.pretty_path(src_vars)} -> {ui.pretty_path(dst_vars)}")
    if key_dir.is_dir():
        ui.print_kv("SSH keys", f"{ui.pretty_path(key_dir)} -> {ui.pretty_path(final / 'ssh')} (the guest trusts this public key)")
    if dry_run:
        ui.print_note(f"Would assemble in {ui.pretty_path(staging)} and rename it to {ui.pretty_path(final)}")
        checkpoint.convert_image(src_disk, fmt, staging / dst_disk.relative_to(final), fmt, False, True)
        return final

    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        def staged(path: Path) -> Path:
            target = staging / path.relative_to(final)
            target.parent.mkdir(parents=True, exist_ok=True)
            return target
        checkpoint.convert_image(src_disk, fmt, staged(dst_disk), fmt, False, False)
        if src_vars is not None and src_vars.is_file() and dst_vars is not None:
            shutil.copy2(src_vars, staged(dst_vars))
        if key_dir.is_dir():
            shutil.copytree(key_dir, staging / "ssh")
        record = vmstate.state_path(src)
        if record.is_file():
            shutil.copy2(record, staging / vmstate.STATE_FILE)
        (staging / "logs").mkdir(exist_ok=True)
        (staging / "runtime").mkdir(exist_ok=True)
        if final.exists():
            if any(final.iterdir()):
                raise VMError(f"{ui.pretty_path(final)} appeared during the clone; not overwriting it")
            final.rmdir()
        os.rename(staging, final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    vmstate.record_origin(dst, "clone", src, keep_facts=record.is_file())
    return final


def write_profile(dst: str, profile: dict[str, Any], dry_run: bool = False) -> tuple[Path, bool]:
    """Add the clone to ``vms/profiles/local.json`` (created if missing, merged and backed
    up otherwise). The tracked profiles are never written. Returns the path and whether
    the file was created by this call (so a rollback knows to delete it)."""
    path = state.CONFIG_DIR / "profiles" / "local.json"
    created = not path.is_file()
    current: dict[str, Any] = {"vms": {}}
    if path.is_file():
        current = runtime.load_json_file(path)
        if not isinstance(current.get("vms"), dict):
            raise VMError(f"{path} has no 'vms' object")
    if dst in current["vms"]:
        raise VMError(f"{path} already defines '{dst}'")
    current["vms"][dst] = profile
    if dry_run:
        ui.print_note(f"Would add '{dst}' to {ui.pretty_path(path)}:")
        print(json.dumps({"vms": {dst: profile}}, indent=2))
        return path, created
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        backup = path.with_suffix(".json.bak")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        ui.print_note(f"Previous local.json saved as {ui.pretty_path(backup)}")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path, created


def remove_profile(dst: str, created: bool = False) -> None:
    """Undo ``write_profile`` when a later step fails (the clone must not stay published):
    the file goes away when this run created it, otherwise only the clone's entry."""
    path = state.CONFIG_DIR / "profiles" / "local.json"
    if not path.is_file():
        return
    if created:
        path.unlink()
        return
    current = runtime.load_json_file(path)
    vms = current.get("vms")
    if isinstance(vms, dict) and dst in vms:
        del vms[dst]
        path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")


def tracked_profile_names() -> set[str]:
    """Names defined by the tracked profile files (everything but local.json)."""
    names: set[str] = set()
    for path in sorted((state.CONFIG_DIR / "profiles").glob("*.json")):
        if path.name == "local.json":
            continue
        try:
            vms = runtime.load_json_file(path).get("vms")
        except VMError:
            continue
        if isinstance(vms, dict):
            names.update(str(name) for name in vms)
    return names


def delete_local_profile(name: str, dry_run: bool = False) -> None:
    """``vmctl clean <vm> --remove-profile``: drop a profile that exists only in local.json (a
    clone, or one written by hand there). A tracked profile is refused: its local.json entry
    is an override of the tracked one, not the profile itself."""
    path = state.CONFIG_DIR / "profiles" / "local.json"
    if name in tracked_profile_names():
        raise VMError(f"'{name}' is a tracked profile; --remove-profile removes only profiles that live in local.json alone. "
                      f"Edit vms/profiles/local.json by hand if you meant its override.")
    if not path.is_file():
        raise VMError(f"'{name}' has no entry in {path}")
    current = runtime.load_json_file(path)
    vms = current.get("vms")
    if not isinstance(vms, dict) or name not in vms:
        raise VMError(f"'{name}' has no entry in {ui.pretty_path(path)}")
    ui.print_note(f"Removing profile '{name}' from {ui.pretty_path(path)}")
    if dry_run:
        return
    backup = path.with_suffix(".json.bak")
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    del vms[name]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    ui.print_status("ok", f"Profile '{name}' removed (previous local.json saved as {ui.pretty_path(backup)})")


def describe_identity(vm: dict[str, Any], src: str, dst: str, mode: str) -> list[str]:
    """What the clone keeps from the origin's guest and what to do about it."""
    supported, advice = identity_support(vm)
    lines = []
    if mode == "regenerate" and supported:
        lines.append(f"guest identity: hostname -> {dst}, new /etc/machine-id and SSH host keys (regenerated over SSH)")
    else:
        lines.append(f"guest identity: kept from '{src}' (same hostname, /etc/machine-id, SSH host keys and any static address)")
        if supported:
            lines.append("  rerun with --identity regenerate, or: sudo hostnamectl set-hostname <name>; "
                         "sudo rm /etc/machine-id && sudo systemd-machine-id-setup; sudo rm /etc/ssh/ssh_host_* && sudo ssh-keygen -A")
        else:
            lines.append(f"  --identity regenerate is not available for this guest: {advice}")
    return lines
