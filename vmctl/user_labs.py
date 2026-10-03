"""Deterministic local lab scaffolds; no guest, download or tracked-file writes."""
from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import re
import shlex
import shutil
from typing import Any

from vmctl import catalog, clone, config, iso, labs, profile_bases, qemu, runtime, scheduler, state, ui
from vmctl.errors import VMError


def _revision() -> str:
    digest = hashlib.sha256()
    for path in sorted((state.CONFIG_DIR / "profiles").glob("*.json")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _check_revision(revision: str) -> None:
    if revision != _revision():
        raise VMError("Profiles changed during validation; retry the group command")


def _name(name: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name):
        raise VMError(f"Invalid lab/role name {name!r}: use 1-64 lowercase letters, digits and hyphens")
    return name


def allocate_subnet(cfg: dict[str, Any]) -> int:
    """First unused 172.20.N.0/24, N=1..255, including wider overlapping networks."""
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    segments: set[str] = set()
    for _, vm in config.sorted_vm_items(cfg):
        for nic in vm.get("networks") or []:
            if nic.get("type") == "segment":
                segments.add(str(nic.get("name") or ""))
            if nic.get("address"):
                try:
                    networks.append(ipaddress.ip_interface(nic["address"]).network)
                except ValueError as exc:
                    raise VMError(f"Invalid network address {nic['address']!r}") from exc
    for number in range(1, 256):
        candidate = ipaddress.ip_network(f"172.20.{number}.0/24")
        if f"user-lab-{number}" not in segments and not any(candidate.overlaps(net) for net in networks):
            return number
    raise VMError("No free lab subnet left in 172.20.1.0/24 through 172.20.255.0/24")


def _bases(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    bases: dict[str, dict[str, Any]] = {}
    for path in sorted((state.CONFIG_DIR / "profiles").glob("*.json")):
        if path.name != "local.json":
            bases.update(profile_bases.bases_of(runtime.load_json_file(path), str(path)))
    bases.update(profile_bases.bases_of(document, "local.json"))
    return bases


def _template(source: str, name: str, cfg: dict[str, Any], bases: dict[str, dict[str, Any]],
              document: dict[str, Any]) -> tuple[str, dict[str, Any], str | None]:
    """A simple base stays inherited. A profile/complex base becomes a private snapshot base.

    Lists append in extends: a snapshot lets us discard the source's NICs/groups without
    changing that contract for every existing recipe. Never share mutable source artifacts.
    """
    is_base = source in bases
    if is_base:
        vm = profile_bases.resolve_extends({name: {"extends": source}}, bases)[name]
    else:
        source = config.canonical_vm_name(source, warn=False)
        # A catalog recipe keeps {{user}} literal and excludes personal local overrides.
        tracked = config.load_tracked()
        vm = copy.deepcopy(tracked[source] if source in tracked else config.get_vm(cfg, source))
        vm = profile_bases.replace_placeholder(vm, f"artifacts/{source}/", f"artifacts/{name}/")
    vm.pop("extends", None)
    # The disk name is private even when the source uses an absolute/custom path.
    disk = dict(vm.get("disk") or {})
    disk["path"] = f"artifacts/{name}/disk.{disk.get('format', 'qcow2')}"
    firmware = dict(vm.get("firmware") or {})
    if firmware.get("type") == "efi":
        firmware["vars_path"] = f"artifacts/{name}/OVMF_VARS.fd"
    vm["disk"], vm["firmware"] = disk, firmware
    extra = qemu.extra_disks(vm)
    for index, entry in enumerate(extra, 1):
        entry["path"] = f"artifacts/{name}/extra-{index}.{entry['format']}"
    if extra:
        vm["extra_disks"] = extra
    for section, value in vm.items():
        if isinstance(value, dict) and "hostname" in value:
            value["hostname"] = name
    for section in ("ssh_provision", "cloud_init"):
        if isinstance(vm.get(section), dict):
            vm[section]["ssh_key"] = None
    simple = is_base and not vm.get("networks") and not vm.get("meta", {}).get("groups") and not extra
    simple = simple and not any(vm.get(key) for key in ("network_lab", "lab_services"))
    simple = simple and not vm.get("proxmox_config", {}).get("cluster")
    if simple:
        return source, vm, None
    for key in ("networks", "network_lab", "lab_services"):
        vm.pop(key, None)
    if isinstance(vm.get("proxmox_config"), dict):
        vm["proxmox_config"].pop("cluster", None)
    meta = vm.setdefault("meta", {})
    for key in ("groups", "version", "verified"):
        meta.pop(key, None)
    private_base = f"{name}-base"
    if private_base in bases or private_base in cfg["vms"]:
        raise VMError(f"Profile/base '{private_base}' already exists")
    document.setdefault("bases", {})[private_base] = copy.deepcopy(vm)
    bases[private_base] = copy.deepcopy(vm)
    return private_base, vm, private_base


def _files(group: str, title: str, members: list[str], number: int, owned_bases: list[str],
           cloud_only: bool) -> dict[str, str]:
    peers = {name: f"172.20.{number}.{index}" for index, name in enumerate(members, 1)}
    checks = [f"vmctl shell {name} -- ping -c 2 {peers[peer]}"
              for name in members for peer in members if peer != name]
    document: dict[str, Any] = {"title": title, "summary": f"A private lab on 172.20.{number}.0/24.", "members": members,
                "scaffold_bases": owned_bases,
                "exercises": [{"title": "Reachability", "text": "Check every direction on the private segment.",
                               "blocks": [{"kind": "do", "where": "host", "commands": [f"vmctl group install {group}"]},
                                          {"kind": "check", "where": "host", "commands": checks},
                                          {"kind": "try", "where": "host", "commands": [f"vmctl group down {group}", f"vmctl group up {group}"]}]}]}
    script = ['#!/usr/bin/env bash', 'set -euo pipefail',
              'source "$(dirname "${BASH_SOURCE[0]}")/../../../labs/_common.sh"', '']
    for name in members:
        for peer in members:
            if name != peer:
                # Windows ping uses -n, unlike Linux/BSD. The scaffold is reviewed before install.
                script.append(f"assert {shlex.quote(name + ' reaches ' + peer)} on {name} ping -c 2 {peers[peer]}")
    script.append(f"report_results {shlex.quote(group + ' reachability')}")
    files = {"tests/test_01_reachability.sh": "\n".join(script) + "\n"}
    if cloud_only:
        # Cloud-init guests already have Python; HTTPS also tests DNS without relying on
        # ICMP forwarding through slirp or an extra curl package in the guest.
        probe = 'import urllib.request; urllib.request.urlopen("https://example.org", timeout=10).close()'
        command = f"python3 -c {shlex.quote(probe)}"
        internet_script = script[:4] + [
            f"assert {shlex.quote(name + ' reaches the Internet via NAT (HTTPS)')} on {name} {command}"
            for name in members]
        internet_script.append(f"report_results {shlex.quote(group + ' Internet access')}")
        files["tests/test_02_internet.sh"] = "\n".join(internet_script) + "\n"
        document["exercises"].append({"title": "Internet access", "text": "Check DNS and HTTPS through each member's NAT connection.",
                                       "blocks": [{"kind": "check", "where": "host", "commands": [
                                           f"vmctl shell {name} -- {command}" for name in members]}]})
    files["lab.json"] = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    for lang in ("en", "it"):
        intro = ("Edit this guide and lab.json to add your exercises. Each VM has its own NAT connection and a private NIC; "
                 "a firewall role does not route the other members automatically. Cloud images configure the segment "
                 "address at install time; other guests need their own network configuration."
                 if lang == "en" else
                 "Modifica questa guida e lab.json per aggiungere gli esercizi. Ogni VM ha una connessione NAT e una NIC privata; "
                 "il ruolo firewall non instrada automaticamente gli altri membri. Le cloud image configurano "
                 "l'indirizzo del segmento durante l'installazione; gli altri guest richiedono una configurazione di rete dedicata.")
        table = "\n".join(f"| {name} | {address}/24 |" for name, address in peers.items())
        files[f"guide.{lang}.md"] = (f"# {title}\n\n{intro}\n\n| VM | IP |\n|---|---|\n{table}\n\n"
                                    f"```sh\nvmctl group install {group}\nvmctl group test {group}\nvmctl group map {group}\n"
                                    f"vmctl group down {group}\n```\n\n"
                                    + ("Cleanup" if lang == "en" else "Pulizia")
                                    + f": `vmctl group clean {group}`, `vmctl group remove {group}`.\n")
    return files


def create(group: str, specifications: list[str], title: str | None = None, dry_run: bool = False) -> None:
    _name(group)
    if config.tracked_only():
        raise VMError("group new requires local profiles; unset VMCTL_TRACKED_ONLY")
    revision = _revision()
    document = catalog.read_document()
    cfg = config.load_config(local_profiles=document)
    directory = runtime.resolve_path(str(labs.LOCAL_LABS_DIR / group))
    groups = {g for _, vm in config.sorted_vm_items(cfg) for g in config.declared_groups(vm)}
    if group in groups or group in labs.content_groups() or directory.exists() or (state.ROOT / labs.LABS_DIR / group).exists():
        raise VMError(f"Lab/group '{group}' already exists; choose a new name")
    if not 2 <= len(specifications) <= 254:
        raise VMError("group new needs 2..254 --member role=base-or-profile entries")
    if title is not None and not title.strip():
        raise VMError("The lab title must not be empty")
    bases = _bases(document)
    number = allocate_subnet(cfg)
    taken = clone.used_host_ports(cfg)
    # load_config reserves SSH ports even on profiles whose current NICs do not expose them.
    for _, vm in config.sorted_vm_items(cfg):
        for section in ("ssh_provision", "cloud_init"):
            if isinstance(vm.get(section), dict) and vm[section].get("ssh_host_port"):
                taken.add(int(vm[section]["ssh_host_port"]))
    ports = clone.allocate_ports(len(specifications), taken)
    members: list[str] = []
    owned_bases: list[str] = []
    for index, specification in enumerate(specifications, 1):
        role, separator, source = specification.partition("=")
        if not separator or not source:
            raise VMError(f"Invalid member {specification!r}: expected role=base-or-profile")
        _name(role)
        name = clone.validate_name(f"{group}-{role}")
        if name in members or name in bases:
            raise VMError(f"Duplicate member/profile/base '{name}'")
        clone.check_destination(cfg, name)
        parent, vm, owned = _template(source, name, cfg, bases, document)
        if owned:
            owned_bases.append(owned)
        profile: dict[str, Any] = {"extends": parent, "name": f"{group}: {role}",
                                  "meta": {"groups": [group], "role": role},
                                  "disk": vm["disk"], "firmware": vm["firmware"],
                                  "network": "user",
                                  "networks": [{"id": "nat", "type": "user", "ssh": True},
                                               {"id": "lan", "type": "segment", "name": f"user-lab-{number}",
                                                "phase": "runtime", "address": f"172.20.{number}.{index}/24"}]}
        for section, value in vm.items():
            if isinstance(value, dict) and "hostname" in value:
                profile[section] = {"hostname": name}
        for section in ("ssh_provision", "cloud_init"):
            if isinstance(vm.get(section), dict):
                profile.setdefault(section, {}).update(ssh_host_port=ports[index - 1], ssh_key=None)
        document["vms"][name] = profile
        members.append(name)
    candidate = config.load_config(local_profiles=document)
    total_ram = total_cpu = total_disk = 0
    for name, port in zip(members, ports):
        vm = config.get_vm(candidate, name)
        for field in ("memory_mb", "cpus"):
            if type(vm[field]) is not int or vm[field] <= 0:
                raise VMError(f"{name}: {field} must be a positive integer")
        total_ram += vm["memory_mb"]
        total_cpu += vm["cpus"]
        total_disk += sum(scheduler.size_gb(disk["size"]) for disk in [vm["disk"], *qemu.extra_disks(vm)])
        ssh_label = f"SSH host port {port}" if any(isinstance(vm.get(s), dict) for s in ("ssh_provision", "cloud_init")) else "no SSH access configured"
        ui.print_note(f"{name}: {vm['networks'][-1]['address']}, {ssh_label}")
        if iso.iso_source_kind(vm) == "manual":
            ui.print_status("warn", f"{name} requires a user-supplied ISO/image: {ui.pretty_path(iso.medium_path(vm))}", ok=False)
        if "cloudimg_config" not in vm:
            ui.print_status("warn", f"{name}: configure the segment address inside the guest and adapt its ping check before running tests", ok=False)
    ui.print_note(f"Estimated total: {total_ram} MiB RAM, {total_cpu} vCPU, {total_disk} GiB virtual disk capacity (rounded up; excludes cached media)")
    files = _files(group, (title or group).strip(), members, number, owned_bases,
                   all("cloudimg_config" in config.get_vm(candidate, name) for name in members))
    _check_revision(revision)
    if dry_run:
        ui.print_note(f"Would update local.json and create {ui.pretty_path(directory)}: {', '.join(files)}")
        return
    directory.mkdir(parents=True)
    try:
        for relative, contents in files.items():
            path = directory / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents, encoding="utf-8")
        labs.model(candidate, group)  # Validate content/member agreement before publishing profiles.
        _check_revision(revision)
        catalog.write_document(document)
    except BaseException:
        shutil.rmtree(directory)
        raise
    ui.print_status("ok", f"Created {group}; review with vmctl group guide {group}, then vmctl group install {group}")


def remove(group: str, dry_run: bool = False) -> None:
    _name(group)
    if config.tracked_only():
        raise VMError("group remove requires local profiles; unset VMCTL_TRACKED_ONLY")
    revision = _revision()
    document = catalog.read_document()
    cfg = config.load_config(local_profiles=document)
    directory = runtime.resolve_path(str(labs.LOCAL_LABS_DIR / group))
    content = labs.load_content(group)
    if content is None or labs.content_dir(group) != directory or directory.is_symlink():
        raise VMError(f"'{group}' is not a removable local lab")
    members = labs.model(cfg, group)["content"]["members"]
    tracked = clone.tracked_profile_names()
    for name in members:
        if name in tracked or name not in document["vms"]:
            raise VMError(f"'{name}' is not a local-only profile; refusing to remove '{group}'")
        vm = config.get_vm(cfg, name)
        if any(runtime.resolve_path(disk["path"]).exists() for disk in [vm["disk"], *qemu.extra_disks(vm)]):
            raise VMError(f"'{name}' still has disks; run vmctl group clean {group} first")
    for name in members:
        del document["vms"][name]
    raw = runtime.load_json_file(directory / "lab.json")
    owned_bases = raw.get("scaffold_bases", [])
    if not isinstance(owned_bases, list) or not all(isinstance(name, str) for name in owned_bases):
        raise VMError("lab.json: scaffold_bases must be a list of base names")
    for name in owned_bases:
        # Only bases created in this namespace, and no longer inherited by another entry.
        if name not in {f"{member}-base" for member in members}:
            raise VMError(f"Unexpected scaffold base {name!r}; refusing removal")
        document.get("bases", {}).pop(name, None)
    config.load_config(local_profiles=document)  # Also catches other profiles depending on these bases.
    _check_revision(revision)
    if dry_run:
        ui.print_note(f"Would remove {group}: {', '.join(members)} and {ui.pretty_path(directory)}")
        return
    # Rename first, so a failed write can restore the intact content directory.
    staging = directory.with_name(f".{group}-removing")
    if staging.exists():
        raise VMError(f"Unfinished removal at {ui.pretty_path(staging)}; restore it before retrying")
    directory.rename(staging)
    try:
        catalog.write_document(document)
    except BaseException:
        staging.rename(directory)
        raise
    shutil.rmtree(staging)
    ui.print_status("ok", f"Removed local lab {group} (previous local.json saved as local.json.bak)")
