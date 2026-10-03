"""Labs: declared groups whose members share a network segment, run and drawn as one stack.

A lab is not a new kind of object: it is a ``meta.groups`` entry (``netlab``, ``proxmox-lab``)
with at least one member on a ``segment`` NIC. ``model()`` reads everything the map and the TUI
show from the profiles themselves — NICs of the runtime phase, host forwards, addresses (the
network lab's from its ``network_lab`` topology, the others from ``networks[].address``) — plus
the live state the caller measured, so this module never starts or probes anything.
"""
from __future__ import annotations

import html
import ipaddress
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from vmctl import cloud_init, config, netlab, proxmox, pvecluster, qemu, runtime, state, ui
from vmctl.errors import VMError

# The tracked profiles' shared password ("lab"): a hash the map can recognise without cracking it.
TRACKED_LAB_HASH = "$6$labsalt0$POq.mGL6qhDmEnplwYiYiuKyYy.U8EuL0G.ROmcWjbMIHXpKeoKRB6MI2ObMDS3NHOQiB/R9E4pAiaNp5HKou/"
# Sections that declare a guest login, as (section, user key, plain password key, hash key).
LOGIN_SECTIONS = (
    ("proxmox_config", None, "root_password", "root_password_hash"),
    ("preseed_config", "username", None, "password_hash"),
    ("ubiquity_config", "username", None, "password_hash"),
    ("autoinstall", "username", None, "password_hash"),
    ("kickstart_config", "username", None, "password_hash"),
    ("archinstall_config", "username", "password", "password_hash"),
    ("alpine_config", "username", None, "password_hash"),
    ("nixos_config", "username", None, "password_hash"),
    ("freebsd_config", "username", "password", None),
    ("windows_config", "username", "password", None),
)


def login(vm: dict[str, Any]) -> dict[str, str] | None:
    """Who logs in and with what, as far as the profile knows. Profiles keep hashes, so the
    password is shown when it is plain text, when the hash is the tracked "lab" one, or when the
    local profile carries ``meta.password_hint`` (a reminder that lives in local.json only)."""
    hint = str((vm.get("meta") or {}).get("password_hint") or "")
    for section, user_key, plain_key, hash_key in LOGIN_SECTIONS:
        cfg = vm.get(section)
        if not isinstance(cfg, dict):
            continue
        user = "root" if user_key is None else str(cfg.get(user_key) or "")
        if not user:
            continue
        where = "root@pam, realm Linux PAM" if section == "proxmox_config" else ""
        if hint:
            return {"user": user, "password": hint, "note": where or "meta.password_hint"}
        if plain_key and cfg.get(plain_key):
            return {"user": user, "password": str(cfg[plain_key]), "note": where}
        if hash_key and cfg.get(hash_key) == TRACKED_LAB_HASH:
            return {"user": user, "password": "lab", "note": where or "tracked default"}
        return {"user": user, "password": "",
                "note": (where + "; " if where else "") + "your own (a hash in local.json; meta.password_hint there shows it)"}
    return None


def logins(vm: dict[str, Any]) -> list[dict[str, str]]:
    """Every account worth showing next to a machine. ``meta.logins`` lists the ones a vendor fixes
    (Redox: user with no password, root/password; SerenityOS: anon/foo), which no install section
    can express; otherwise the one login() derives from the profile's own user settings."""
    declared = (vm.get("meta") or {}).get("logins")
    if isinstance(declared, list):
        return [{"user": str(e.get("user") or ""), "password": str(e.get("password") or ""),
                 "note": str(e.get("note") or "")} for e in declared if isinstance(e, dict) and e.get("user")]
    found = login(vm)
    return [found] if found else []


# Started first and stopped last: what the other members route through or run on, then the
# services they resolve names with (the network lab installs pfsense -> pihole -> clients too).
INFRA_ROLES = ("router", "pfsense", "hypervisor")
SERVICE_ROLES = ("pihole", "dns", "server")
SLIRP_SUBNET = "10.0.2.0/24"


def has_segment(vm: dict[str, Any]) -> bool:
    try:
        return any(spec["type"] == "segment" for spec in qemu.network_specs(vm, "runtime"))
    except VMError:
        return False


def group_members(cfg: dict[str, Any], group: str) -> list[str]:
    return [name for name, vm in config.sorted_vm_items(cfg) if group in config.declared_groups(vm)]


def lab_groups(cfg: dict[str, Any]) -> list[str]:
    """Declared groups whose members are all on a segment, or that bring their own content
    (``vms/labs/<group>/lab.json``, a one-server lab has no segment): the labs, in name order."""
    groups = sorted({group for _, vm in config.sorted_vm_items(cfg) for group in config.declared_groups(vm)})
    with_content = set(content_groups())
    # Every member: a category such as ``ubuntu`` holds one lab client and is still no lab.
    return [group for group in groups
            if group in with_content or all(has_segment(config.get_vm(cfg, name)) for name in group_members(cfg, group))]


# --- lab content: vms/labs/<lab>/ ------------------------------------------------------------
#
# What a lab brings besides its profiles (docs/QLAB_IMPORT.md, F3): lab.json (title, summary,
# members, exercises as do/check/try blocks in the runbook's own shape), guide.<lang>.md, the
# provision/ files its members copy in (copy_from_host) and tests/test_NN_*.sh over _common.sh.

LABS_DIR = Path("vms") / "labs"
LOCAL_LABS_DIR = Path("vms") / "labs.local"
BLOCK_KINDS = ("do", "check", "try")
GUIDE_LANGS = ("en", "it")


def content_dir(group: str) -> Path:
    if not config.GROUP_RE.fullmatch(group):
        raise VMError(f"Invalid lab name: {group!r}")
    directories = _content_directories()
    return directories.get(group, runtime.resolve_path(str(LABS_DIR / group)))


def _content_directories() -> dict[str, Path]:
    tracked = runtime.resolve_path(str(LABS_DIR))
    local = runtime.resolve_path(str(LOCAL_LABS_DIR))
    directories = {p.parent.name: p.parent for p in tracked.glob("*/lab.json")}
    if config.tracked_only():
        return directories
    local_dirs = {p.parent.name: p.parent for p in local.glob("*/lab.json")}
    if local_dirs:
        # Also protect tracked labs with no content directory (e.g. proxmox-lab).
        tracked_groups = {g for vm in config.load_tracked().values() for g in config.declared_groups(vm)}
        collisions = set(local_dirs) & (set(directories) | tracked_groups)
        if collisions:
            raise VMError(f"Local lab '{sorted(collisions)[0]}' conflicts with a tracked lab/group; rename the local lab")
    return {**directories, **local_dirs}


def content_groups() -> list[str]:
    """Groups with tracked or local content; local labs respect --tracked-only."""
    return sorted(_content_directories())


def load_content(group: str) -> dict[str, Any] | None:
    """``vms/labs/<group>/lab.json`` validated, plus what sits next to it; None without the file."""
    directory = content_dir(group)
    path = directory / "lab.json"
    if not path.is_file():
        return None
    what = ui.pretty_path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VMError(f"{what}: {exc}") from exc
    if not isinstance(raw, dict):
        raise VMError(f"{what}: expected an object")
    title = str(raw.get("title") or "").strip()
    if not title:
        raise VMError(f"{what}: 'title' is required")
    members = raw.get("members")
    if not isinstance(members, list) or not members or not all(isinstance(m, str) and m.strip() for m in members):
        raise VMError(f"{what}: 'members' must be a non-empty list of profile names")
    names = [str(m).strip() for m in members]
    exercises: list[dict[str, Any]] = []
    for index, item in enumerate(raw.get("exercises") or [], start=1):
        label = f"{what}: exercises[{index}]"
        if not isinstance(item, dict) or not str(item.get("title") or "").strip():
            raise VMError(f"{label}: needs a title")
        blocks: list[dict[str, Any]] = []
        for b_index, block in enumerate(item.get("blocks") or [], start=1):
            b_label = f"{label}.blocks[{b_index}]"
            if not isinstance(block, dict):
                raise VMError(f"{b_label}: expected an object")
            kind = str(block.get("kind") or "do")
            if kind not in BLOCK_KINDS:
                raise VMError(f"{b_label}: 'kind' must be one of {', '.join(BLOCK_KINDS)}, not {kind!r}")
            where = str(block.get("where") or "host")
            if where != "host" and where not in names:
                raise VMError(f"{b_label}: 'where' must be host or a member ({', '.join(names)}), not {where!r}")
            commands = block.get("commands")
            if not isinstance(commands, list) or not commands or not all(isinstance(c, str) for c in commands):
                raise VMError(f"{b_label}: 'commands' must be a non-empty list of strings")
            blocks.append(_block(kind, where, [str(c) for c in commands]))
        exercises.append({"title": str(item["title"]).strip(), "text": str(item.get("text") or "").strip(), "blocks": blocks})
    relative = directory.relative_to(state.ROOT).as_posix()
    guides = {lang: f"{relative}/guide.{lang}.md" for lang in GUIDE_LANGS if (directory / f"guide.{lang}.md").is_file()}
    tests_dir, provision_dir = directory / "tests", directory / "provision"
    tests = sorted(p.name for p in tests_dir.glob("test_*.sh")) if tests_dir.is_dir() else []
    provision = sorted(p.name for p in provision_dir.iterdir() if p.is_file()) if provision_dir.is_dir() else []
    return {"group": group, "dir": relative, "title": title, "summary": str(raw.get("summary") or "").strip(),
            "members": names, "exercises": exercises, "guides": guides, "tests": tests, "provision": provision}


def start_order(cfg: dict[str, Any], names: list[str]) -> list[str]:
    """Infrastructure (router, hypervisor), then services (DNS), then the rest, each in catalog order."""
    def rank(name: str) -> int:
        role = str(config.get_vm(cfg, name).get("meta", {}).get("role") or "")
        return 0 if role in INFRA_ROLES else 1 if role in SERVICE_ROLES else 2
    return sorted(names, key=lambda name: (rank(name), names.index(name)))


def _netlab_addresses(cfg: dict[str, Any], names: list[str]) -> tuple[dict[str, str], dict[int, tuple[str, str]]]:
    """Segment addresses and WAN forward descriptions of the network lab, from its topology."""
    addresses: dict[str, str] = {}
    forwards: dict[int, tuple[str, str]] = {}
    for name in names:
        vm = config.get_vm(cfg, name)
        lab = netlab.lab_config(vm)
        if lab is None or lab.get("role") != "pfsense":
            continue
        try:
            top = netlab.topology(cfg, name)
        except VMError:
            continue
        prefix = top["lan"]["prefix"]
        addresses[top["router"]] = f"{top['router_ip']}/{prefix}"
        for member in top["members"]:
            addresses[member["name"]] = f"{member['ip']}/{prefix}"
        for fwd in top["forwards"]:
            forwards[int(fwd["wan_port"])] = (f"{fwd['target']}:{fwd['target_port']}", fwd["descr"].removeprefix("vmctl: "))
    return addresses, forwards


def model(cfg: dict[str, Any], group: str, states: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    names = group_members(cfg, group)
    if not names:
        raise VMError(f"No profile declares the group '{group}' (meta.groups)")
    states = states or {}
    lab_addresses, lab_forwards = _netlab_addresses(cfg, names)
    members: list[dict[str, Any]] = []
    segments: dict[str, dict[str, Any]] = {}
    for name in start_order(cfg, names):
        vm = config.get_vm(cfg, name)
        raw_nics = {str(entry.get("id") or ""): entry for entry in vm.get("networks") or [] if isinstance(entry, dict)}
        ssh_cfg = cloud_init.ssh_access_config(vm) or {}
        nics: list[dict[str, Any]] = []
        for spec in qemu.network_specs(vm, "runtime"):
            nic: dict[str, Any] = {"id": spec["id"], "type": spec["type"], "mac": spec["mac"]}
            if spec["type"] == "user":
                forwards = []
                if spec["ssh"] and ssh_cfg.get("ssh_host_port"):
                    forwards.append({"host_port": int(ssh_cfg["ssh_host_port"]), "guest": "22", "what": "SSH"})
                for fwd in spec["hostfwd"]:
                    # A router's forward goes on through its NAT rules: via = where it really lands.
                    via, what = lab_forwards.get(int(fwd["host_port"]), ("", ""))
                    forwards.append({"host_port": fwd["host_port"], "guest": str(fwd["guest_port"]), "what": what, "via": via})
                nic.update(address="10.0.2.15/24 (DHCP)", network=SLIRP_SUBNET, forwards=forwards)
            else:
                segment = str(spec["name"])
                address = lab_addresses.get(name) or str(raw_nics.get(spec["id"], {}).get("address") or "")
                nic.update(segment=segment, address=address)
                seg = segments.setdefault(segment, {"name": segment, "subnet": "", "members": []})
                seg["members"].append(name)
                if address and not seg["subnet"]:
                    try:
                        seg["subnet"] = str(ipaddress.ip_interface(address).network)
                    except ValueError:
                        pass
            nics.append(nic)
        meta = vm.get("meta", {})
        state = states.get(name, {})
        members.append({
            "name": name, "label": str(vm.get("name") or name), "role": str(meta.get("role") or ""),
            "status": str(meta.get("status") or ""), "memory_mb": int(vm.get("memory_mb") or 0),
            "cpus": int(vm.get("cpus") or 0), "nics": nics,
            "running": bool(state.get("running")), "install": str(state.get("install") or ""),
            "disks": 1 + len(qemu.extra_disks(vm)),
            "flow": str(state.get("flow") or ""),
            "ssh": _ssh_line(name, vm),
            "login": login(vm),
            "zfs": _zfs(vm),
            # Guests of a hypervisor member (Proxmox containers) that the lab reaches on the segment.
            "services": [dict(entry) for entry in vm.get("lab_services") or [] if isinstance(entry, dict)],
        })
    lab: dict[str, Any] = {"group": group, "members": members, "segments": list(segments.values()),
                           "start_order": [member["name"] for member in members]}
    content = load_content(group)
    if content is not None and set(content["members"]) != set(names):
        raise VMError(f"{content['dir']}/lab.json lists {', '.join(content['members'])} but the profiles declaring "
                      f"the group '{group}' are {', '.join(names)}: the two must agree")
    lab["content"] = content
    lab["runbook"] = runbook(cfg, lab)
    return lab


def _ssh_line(name: str, vm: dict[str, Any]) -> str:
    ssh_cfg = cloud_init.ssh_access_config(vm) or {}
    if not ssh_cfg.get("ssh_host_port"):
        return ""
    return (f"ssh -i artifacts/{name}/ssh/id_ed25519 -p {int(ssh_cfg['ssh_host_port'])} "
            f"{ssh_cfg.get('user') or 'root'}@127.0.0.1")


def _zfs(vm: dict[str, Any]) -> dict[str, Any] | None:
    cfg = proxmox.proxmox_config(vm)
    if not cfg or str(cfg.get("filesystem") or "zfs") != "zfs":
        return None
    return {"raid": str((cfg.get("zfs") or {}).get("raid") or "raid0"), "disks": proxmox.disk_names(vm)}


COMMUNITY_URL = "https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/ct/{script}.sh"


def _block(kind: str, where: str, commands: list[str]) -> dict[str, Any]:
    """``kind``: do (changes something), check (read-only), try (a reversible experiment)."""
    return {"kind": kind, "where": where, "commands": commands}


def _web_urls(member: dict[str, Any]) -> list[str]:
    urls = []
    for nic in member["nics"]:
        if nic["type"] == "segment" and nic.get("address") and member["role"] == "hypervisor":
            urls.append(f"https://{str(nic['address']).split('/')[0]}:8006/")
    return urls + [str(svc["url"]) for svc in member["services"] if svc.get("url")]


def runbook(cfg: dict[str, Any], lab: dict[str, Any]) -> list[dict[str, Any]]:
    """The commands behind the lab, in the order a person would run them, from the same profiles.

    Each phase has a title, a sentence and blocks; a block is do/check/try, runs on the host or on a
    member (over the SSH line shown next to it), and every check block is read-only."""
    group = lab["group"]
    members = lab["members"]
    by_name = {member["name"]: member for member in members}
    hypervisors = [m for m in members if m["zfs"] or m["role"] == "hypervisor"]
    lan_of = {m["name"]: str(nic["address"]).split("/")[0] for m in members for nic in m["nics"]
              if nic["type"] == "segment" and nic.get("address")}
    phases: list[dict[str, Any]] = []

    install = [f"vmctl group install {group}    # what is missing, in this order, then up (+ cluster)"]
    install += [f"vmctl {m['flow']} {m['name']}" for m in members if m["flow"].startswith("bootstrap-")]
    blocks = [_block("do", "host", install), _block("check", "host", [f"vmctl group status {group}"])]
    blocks += [_block("check", m["name"], ["pveversion", "systemctl is-active pveproxy pvedaemon pve-cluster"])
               for m in hypervisors]
    phases.append({"title": "Install", "text": "One command for the whole lab (cumulative: installed members are kept), or one per member with its own unattended flow. Then every node answers.",
                   "blocks": blocks})

    segments = ", ".join(f"{seg['name']} {seg['subnet']}".strip() for seg in lab["segments"])
    blocks = []
    for m in members:
        others = [ip for name, ip in lan_of.items() if name != m["name"]]
        commands = ["ip -br addr    # NAT NIC + the lab segment"]
        if m["name"] in lan_of and m["role"] == "hypervisor":
            commands = ["ip -br addr show vmbr1", "bridge -d link show | grep -E 'master vmbr1|learning'    # learning off on the segment port"]
        commands += [f"ping -c1 -W2 {ip}" for ip in others]
        blocks.append(_block("check", m["name"], commands))
    phases.append({"title": "Lab network", "text": f"Every VM has a NAT NIC (Internet, SSH and GUI forwards on 127.0.0.1) and a NIC on {segments}; the post-install configured the segment by MAC for the runtime boot.",
                   "blocks": blocks})

    mirrors = [m for m in members if m["zfs"]]
    if mirrors:
        first = mirrors[0]["zfs"]
        disks = ", ".join(f'"{disk}"' for disk in first["disks"])
        blocks = [_block("do", "answer.toml (rendered by vmctl, grafted on the ISO)",
                         ["[disk-setup]", 'filesystem = "zfs"', f"disk-list = [{disks}]", f'zfs.raid = "{first["raid"]}"'])]
        for m in mirrors:
            blocks.append(_block("check", m["name"], [
                "zpool status -x rpool         # 'pool rpool is healthy'",
                "zpool status rpool            # mirror-0 with one partition per disk, all ONLINE",
                "zpool list -v rpool           # size, allocation, fragmentation per device",
                "zfs list -o name,used,avail,mountpoint",
                "pvesm status                  # local (dir) and local-zfs (zfspool) active",
                "proxmox-boot-tool status      # one ESP per disk: the node boots from either",
            ]))
        blocks.append(_block("try", mirrors[0]["name"], [
            "zpool scrub rpool && sleep 20 && zpool status rpool | grep -A1 scan:    # read and verify every block",
            "dev=$(zpool list -vHP rpool | awk '$1 ~ /^\\/dev\\// {d=$1} END {print d}')    # the second mirror half",
            "zpool offline rpool \"$dev\" && zpool status -x rpool    # DEGRADED: the node keeps running on one disk",
            "zpool online rpool \"$dev\" && sleep 5 && zpool status rpool | grep -E 'state|scan|resilver'    # back to ONLINE after a resilver",
        ]))
        phases.append({"title": "ZFS pool (mirror)", "text": f"The root pool is ZFS {first['raid']} over {' + '.join(first['disks'])} (disk + extra_disks) on every node. The checks are read-only; the drill takes one half of the mirror offline and back.",
                       "blocks": blocks})

    blocks = []
    for m in members:
        for svc in m["services"]:
            nat = str(svc.get("nat_address") or "")
            ctid = svc.get("container")
            blocks.append(_block("do", m["name"], [
                f"/root/pve-community.sh {ctid} {svc.get('script')} {svc.get('hostname')} {svc.get('port')} {nat} {svc.get('address')}    # what vmctl ran",
                "# the same by hand, with the upstream script:",
                "mkdir -p /usr/local/community-scripts && echo DIAGNOSTICS=no > /usr/local/community-scripts/diagnostics",
                f"TERM=xterm mode=default var_ctid={ctid} var_hostname={svc.get('hostname')} var_brg=vmbr0 var_net={nat} "
                "var_gateway=10.0.2.2 var_ns=10.0.2.3 var_container_storage=local-zfs var_template_storage=local \\",
                f'  bash -c "$(curl -fsSL {COMMUNITY_URL.format(script=svc.get("script"))})"',
                f"pct set {ctid} -onboot 1 -net1 name=eth1,bridge=vmbr1,ip={svc.get('address')}",
            ]))
    if blocks:
        where = next(m["name"] for m in members if m["services"])
        blocks.append(_block("check", where, [
            "pvesh get /cluster/resources --type vm --output-format text    # every container and the node it runs on",
            "pct list    # the containers of this node",
        ] + [f"grep -H -E '^(hostname|net0|net1|onboot):' /etc/pve/nodes/*/lxc/{svc.get('container')}.conf    # cluster-wide: the path names the node"
             for svc in by_name[where]["services"]]))
        phases.append({"title": "LXC containers", "text": "Two light apps from the Proxmox VE Helper-Scripts (community-scripts.org, main branch, not pinned): created on the NAT bridge at a static address outside slirp's DHCP pool, then given a NIC on the lab segment.",
                       "blocks": blocks})

    names = [m["name"] for m in members]
    for cluster, entry in pvecluster.clusters(cfg, names).items():
        blocks = []
        for node, command in pvecluster.commands(cfg, cluster, entry):
            if blocks and blocks[-1]["where"] == node and blocks[-1]["kind"] == "do":
                blocks[-1]["commands"].append(command)
            else:
                blocks.append(_block("do", node, [command]))
        blocks = [b for b in blocks if b["commands"] != ["pvecm status"]]
        primary = entry["primary"]
        blocks.append(_block("check", primary, [
            "pvecm status | grep -E 'Nodes|Quorate|Expected votes'    # 3 nodes, quorate",
            "pvecm nodes",
            "corosync-cfgtool -s    # link 0 on the lab addresses, every node connected",
            "ha-manager status",
        ]))
        services = [svc for m in members for svc in m["services"]]
        if services and len(entry["nodes"]) > 1:
            target = entry["nodes"][1]
            ctid = services[0].get("container")
            blocks.append(_block("try", primary, [
                f"pct migrate {ctid} {target} --restart    # the container moves; its lab address follows it",
                f"pvesh get /cluster/resources --type vm --output-format text | grep {ctid}",
            ]))
        phases.append({"title": f"Cluster {cluster}", "text": f"corosync on the lab segment: {primary} creates the cluster, the others join it (vmctl group install does this and checks quorum; vmctl group cluster {group} repeats only this step). Every node's GUI shows the whole datacenter.",
                       "blocks": blocks})

    clients = [m for m in members if m["role"] == "client"]
    urls = [url for m in members for url in _web_urls(m)]
    if clients and urls:
        phases.append({"title": "From the client", "text": "What a user of the lab sees: the client's Firefox opens the first URL and one tab per app at login.",
                       "blocks": [_block("check", clients[0]["name"],
                                         [f'curl -ks -o /dev/null -w "%{{http_code}}  {url}\\n" {url}' for url in urls])]})

    # The lab's own exercises (vms/labs/<lab>/lab.json), between what vmctl derives and the stack.
    for exercise in (lab.get("content") or {}).get("exercises") or []:
        phases.append({"title": exercise["title"], "text": exercise["text"],
                       "blocks": [dict(block) for block in exercise["blocks"]]})

    phases.append({"title": "Run the stack", "text": "Infrastructure starts first and stops last.",
                   "blocks": [_block("do", "host", [f"vmctl group up {group}", f"vmctl group map {group} --open",
                                                    f"vmctl group down {group}",
                                                    f"vmctl group clean {group}    # asks; checkpoints and ISOs are kept"])]})
    ssh_of = {m["name"]: m["ssh"] for m in members}
    for phase in phases:
        for block in phase["blocks"]:
            block["ssh"] = ssh_of.get(block["where"], "")
    return phases


_CHECK_LINE = re.compile(r"^\s*(?:\x1b\[[0-9;]*m)*\s*\[(PASS|FAIL)\]")


def run_lab_tests(content: dict[str, Any], dry_run: bool = False, stream: bool = True) -> list[dict[str, Any]]:
    """``vms/labs/<lab>/tests/test_*.sh`` in order, each with bash from the checkout root and
    ``VMCTL`` pointing at this checkout's vmctl, its output shown as it comes (and counted:
    ``[PASS]``/``[FAIL]`` lines are _common.sh's). A script's exit status is its number of failed
    checks; a status that is not that count is an error of the script itself (SSH down, bash
    error), told apart from a failed check."""
    results: list[dict[str, Any]] = []
    env = {**os.environ, "VMCTL": str(state.ROOT / "bin" / "vmctl"), "NO_COLOR": "1"}
    for script in content["tests"]:
        path = runtime.resolve_path(f"{content['dir']}/tests/{script}")
        if dry_run:
            ui.print_note(f"Would run bash {ui.pretty_path(path)}")
            results.append({"script": script, "status": "skipped", "passed": 0, "failed": 0, "exit": 0, "seconds": 0.0})
            continue
        started = time.monotonic()
        passed = failed = 0
        with subprocess.Popen(["bash", str(path)], cwd=str(state.ROOT), env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, errors="replace") as process:
            assert process.stdout is not None
            for line in process.stdout:
                mark = _CHECK_LINE.match(line)
                if mark:
                    passed += mark.group(1) == "PASS"
                    failed += mark.group(1) == "FAIL"
                if stream:
                    sys.stdout.write(line)
                    sys.stdout.flush()
            code = process.wait()
        status = "passed" if code == 0 and failed == 0 else "failed" if code == failed and failed else "error"
        results.append({"script": script, "status": status, "passed": passed, "failed": failed, "exit": code,
                        "seconds": round(time.monotonic() - started, 3)})
    return results


def tests_summary(results: list[dict[str, Any]]) -> tuple[str, str]:
    """(status, detail) over a lab's scripts: passed only when every script passed."""
    ok = sum(r["status"] == "passed" for r in results)
    checks = sum(r["passed"] for r in results), sum(r["failed"] for r in results)
    errors = [r["script"] for r in results if r["status"] == "error"]
    detail = f"{ok}/{len(results)} scripts passed, {checks[0]} checks passed, {checks[1]} failed"
    if errors:
        detail += f"; script error: {', '.join(errors)}"
    return ("passed" if ok == len(results) and results else "failed", detail)


# --- the map ---------------------------------------------------------------------------------

_BOX_W, _BOX_H, _GAP, _MARGIN = 240, 124, 36, 40
_TOP_Y, _TOP_H = 24, 70           # the host and Internet nodes
_NAT_Y = _TOP_Y + _TOP_H + 46     # the NAT bus every VM hangs from
_PILL_H, _PILL_GAP = 24, 8        # one forward = one pill on the VM's cable


def _pill_width(text: str) -> float:
    return 18 + 7.4 * len(text)


def _host_node(x: float, y: float) -> list[str]:
    """The computer running QEMU: a monitor icon, its address and what reaches the lab from it."""
    return [
        f'<g class="node host-n"><rect x="{x}" y="{y}" width="300" height="{_TOP_H}" rx="12"/>',
        f'<rect class="icon" x="{x + 16}" y="{y + 15}" width="44" height="30" rx="4"/>',
        f'<rect class="icon-s" x="{x + 32}" y="{y + 47}" width="12" height="6"/>',
        f'<rect class="icon-s" x="{x + 25}" y="{y + 53}" width="26" height="4" rx="2"/>',
        f'<text class="node-t" x="{x + 76}" y="{y + 31}">Host · 127.0.0.1</text>',
        f'<text class="small" x="{x + 76}" y="{y + 52}">this computer: vmctl, SSH, browser</text></g>',
    ]


def _internet_node(x: float, y: float) -> list[str]:
    cloud = (f"M{x + 40} {y + 58} h120 a26 26 0 0 0 0 -52 a34 34 0 0 0 -62 -8 "
             f"a30 30 0 0 0 -56 14 a22 22 0 0 0 -2 46 z")
    return [f'<g class="node net-n"><path d="{cloud}"/>',
            f'<text class="node-t" x="{x + 100}" y="{y + 40}" text-anchor="middle">Internet</text></g>']


def _svg(lab: dict[str, Any]) -> str:
    members = lab["members"]
    count = max(1, len(members))
    box_h = _BOX_H + 22 * max((len(member["services"]) for member in members), default=0)
    width = max(820, 2 * _MARGIN + count * _BOX_W + (count - 1) * _GAP)
    pills = max((len(nic["forwards"]) for m in members for nic in m["nics"] if nic["type"] == "user"), default=0)
    box_y = _NAT_Y + 26 + pills * (_PILL_H + _PILL_GAP) + 16
    seg_top = box_y + box_h + 90
    height = seg_top + 70 * max(1, len(lab["segments"])) + 10
    x_of = {m["name"]: _MARGIN + index * (_BOX_W + _GAP) for index, m in enumerate(members)}
    seg_y = {seg["name"]: seg_top + 70 * index for index, seg in enumerate(lab["segments"])}
    esc = html.escape
    out: list[str] = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Network map of {esc(lab["group"])}">']
    # Top: the host and the Internet, joined by the NAT bus. Each VM's slirp NIC is its own NAT: the
    # guest reaches the Internet through it, the host reaches the guest only through the forwards.
    internet_x = width - _MARGIN - 200
    out += _host_node(_MARGIN, _TOP_Y)
    out += _internet_node(internet_x, _TOP_Y)
    out.append(f'<line class="natbus" x1="{_MARGIN}" y1="{_NAT_Y}" x2="{width - _MARGIN}" y2="{_NAT_Y}"/>')
    out.append(f'<line class="natbus-l" x1="{_MARGIN + 150}" y1="{_TOP_Y + _TOP_H}" x2="{_MARGIN + 150}" y2="{_NAT_Y}"/>')
    out.append(f'<line class="natbus-l" x1="{internet_x + 100}" y1="{_TOP_Y + _TOP_H - 6}" x2="{internet_x + 100}" y2="{_NAT_Y}"/>')
    out.append(f'<text class="small" x="{width / 2}" y="{_NAT_Y - 10}" text-anchor="middle">'
               f'NAT (slirp {SLIRP_SUBNET}, one per VM) · forwards on 127.0.0.1</text>')
    for member in members:
        x = x_of[member["name"]]
        cx = x + _BOX_W / 2
        for nic in [nic for nic in member["nics"] if nic["type"] == "user"]:
            out.append(f'<circle class="natport" cx="{cx}" cy="{_NAT_Y}" r="5"/>')
            out.append(f'<line class="nat" x1="{cx}" y1="{_NAT_Y}" x2="{cx}" y2="{box_y}"/>')
            for line, fwd in enumerate(nic["forwards"]):
                if fwd.get("via"):
                    text = f'{fwd["host_port"]} → {fwd["via"]}'
                else:
                    what = f' {fwd["what"]}' if fwd["what"] and len(fwd["what"]) <= 12 else ""
                    text = f'{fwd["host_port"]} → :{fwd["guest"]}{what}'
                pill_w = _pill_width(text)
                y = _NAT_Y + 22 + line * (_PILL_H + _PILL_GAP)
                out.append(f'<rect class="pill" x="{cx - pill_w / 2}" y="{y}" width="{pill_w}" height="{_PILL_H}" rx="12"/>')
                out.append(f'<text class="pill-t" x="{cx}" y="{y + 16.5}" text-anchor="middle">{esc(text)}</text>')
        state = "running" if member["running"] else ("stopped" if member["install"] not in ("", "no disk") else "absent")
        badge = {"running": "● running", "stopped": "○ stopped", "absent": "no disk"}[state]
        out.append(f'<g class="vm {state}"><rect x="{x}" y="{box_y}" width="{_BOX_W}" height="{box_h}" rx="12"/>')
        out.append(f'<text class="vm-n" x="{x + 14}" y="{box_y + 28}">{esc(member["name"])}</text>')
        role = member["role"] or "vm"
        ram = f'{member["memory_mb"] / 1024:g} GB' if member["memory_mb"] >= 1024 else f'{member["memory_mb"]} MB'
        disks = f' · {member["disks"]} disks' if member["disks"] > 1 else ""
        out.append(f'<text class="small" x="{x + 14}" y="{box_y + 50}">{esc(role)} · {ram} · {member["cpus"]} vCPU{disks}</text>')
        out.append(f'<text class="badge {state}" x="{x + 14}" y="{box_y + 76}">{badge}</text>')
        if member["install"] and state != "absent":
            out.append(f'<text class="small" x="{x + 14}" y="{box_y + 98}">disk: {esc(member["install"])}</text>')
        for index, service in enumerate(member["services"]):
            where = str(service.get("address") or "").split("/")[0]
            label = f'CT {service.get("container", "?")} {service.get("name", "")} · {where}'
            out.append(f'<text class="svc" x="{x + 14}" y="{box_y + _BOX_H + 6 + 20 * index}">▣ {esc(label)}</text>')
        out.append('</g>')
        for nic in member["nics"]:
            if nic["type"] != "segment":
                continue
            y = seg_y[nic["segment"]]
            out.append(f'<line class="seg-l" x1="{cx}" y1="{box_y + box_h}" x2="{cx}" y2="{y}"/>')
            out.append(f'<circle class="port" cx="{cx}" cy="{y}" r="5"/>')
            out.append(f'<text class="addr" x="{cx + 8}" y="{box_y + box_h + 22}">{esc(nic["address"] or "address not declared")}</text>')
            out.append(f'<text class="small" x="{cx + 8}" y="{box_y + box_h + 38}">{esc(nic["mac"])}</text>')
    for seg in lab["segments"]:
        y = seg_y[seg["name"]]
        out.append(f'<line class="bus" x1="{_MARGIN}" y1="{y}" x2="{width - _MARGIN}" y2="{y}"/>')
        subnet = f' · {seg["subnet"]}' if seg["subnet"] else ""
        out.append(f'<text class="bus-t" x="{_MARGIN}" y="{y + 26}">segment {esc(seg["name"])}{esc(subnet)}</text>')
    out.append('</svg>')
    return "\n".join(out)


_CSS = """
:root { --bg:#f6f8fb; --fg:#17202b; --muted:#5b6778; --card:#ffffff; --line:#c7d1dd;
        --accent:#1f7aa8; --ok:#1e8a52; --warn:#a36a00; --bus:#6d4bb8; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
        --bg:#111820; --fg:#dce5ef; --muted:#93a1b3; --card:#18222e; --line:#304050;
        --accent:#84c9e7; --ok:#86d5ab; --warn:#e8be78; --bus:#b9a2f0; } }
body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; }
main { max-width:1200px; margin:0 auto; padding:24px 16px 48px; }
h1 { font-size:1.5rem; margin:0 0 4px; } h2 { font-size:1.05rem; margin:28px 0 8px; }
.sub { color:var(--muted); margin:0 0 20px; }
.map { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:8px; overflow-x:auto; }
svg { width:100%; min-width:680px; height:auto; display:block; }
svg text { fill:var(--fg); font:14px system-ui, sans-serif; }
svg .small { fill:var(--muted); font-size:12.5px; } svg .fwd { fill:var(--accent); font-size:11.5px; }
svg .node rect, svg .node path { fill:var(--card); stroke:var(--accent); stroke-width:2; }
svg .node .icon { fill:none; stroke:var(--accent); stroke-width:2.5; } svg .node .icon-s { fill:var(--accent); stroke:none; }
svg .node-t { font-weight:700; font-size:16px; fill:var(--accent); }
svg .natbus { stroke:var(--accent); stroke-width:3; stroke-linecap:round; }
svg .natbus-l { stroke:var(--accent); stroke-width:2; }
svg .natport { fill:var(--accent); }
svg .nat { stroke:var(--accent); stroke-width:1.8; stroke-dasharray:5 4; }
svg .pill { fill:var(--card); stroke:var(--accent); stroke-width:1.5; }
svg .pill-t { fill:var(--accent); font-size:12.5px; font-weight:600; }
svg .vm rect { fill:var(--card); stroke:var(--line); stroke-width:1.5; }
svg .vm.running rect { stroke:var(--ok); stroke-width:2.5; }
svg .vm-n { font-weight:700; font-size:17px; }
svg .badge.running { fill:var(--ok); font-weight:600; } svg .badge.stopped { fill:var(--muted); }
svg .badge.absent { fill:var(--warn); }
svg .seg-l, svg .bus { stroke:var(--bus); stroke-width:2.5; } svg .bus { stroke-width:4; stroke-linecap:round; }
svg .port { fill:var(--bus); } svg .svc { fill:var(--bus); font-size:12px; font-weight:600; } svg .bus-t { fill:var(--bus); font-weight:600; } svg .addr { font-weight:600; }
table { border-collapse:collapse; width:100%; background:var(--card); border:1px solid var(--line); border-radius:10px; overflow:hidden; }
th, td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top; font-size:14px; }
th { color:var(--muted); font-weight:600; font-size:12.5px; text-transform:uppercase; letter-spacing:.03em; }
code { font:13px ui-monospace, monospace; } a { color:var(--accent); }
pre { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px; overflow-x:auto; }
.table-wrap { overflow-x:auto; }
h3 { font-size:1rem; margin:22px 0 2px; } .where { font-size:13px; color:var(--muted); margin:10px 0 4px; }
pre { margin:0 0 6px; font:13px/1.45 ui-monospace, monospace; white-space:pre-wrap; word-break:break-word; }
pre.check { border-left:4px solid var(--ok); } pre.do { border-left:4px solid var(--accent); } pre.try { border-left:4px solid var(--warn); }
.kind { display:inline-block; min-width:44px; text-align:center; font-size:11px; font-weight:700; text-transform:uppercase;
        border-radius:6px; padding:1px 6px; margin-right:6px; color:var(--bg); }
.kind.do { background:var(--accent); } .kind.check { background:var(--ok); } .kind.try { background:var(--warn); }
.toc { columns:2; margin:6px 0 10px; padding-left:22px; } .toc li { margin:2px 0; }
"""


def _login_html(entry: dict[str, str] | None) -> str:
    if not entry:
        return '<span class="sub">no login in the profile</span>'
    password = f'<code>{html.escape(entry["password"])}</code>' if entry["password"] else '<span class="sub">—</span>'
    note = f'<br><span class="sub">{html.escape(entry["note"])}</span>' if entry["note"] else ""
    return f'<code>{html.escape(entry["user"])}</code> / {password}{note}'


def _access_html(lab: dict[str, Any]) -> str:
    """Every web UI of the lab with the login it takes, from the host and from the segment."""
    esc = html.escape
    rows: list[str] = []
    for member in lab["members"]:
        entry = member.get("login")
        lan = next((str(nic["address"]).split("/")[0] for nic in member["nics"]
                    if nic["type"] == "segment" and nic.get("address")), "")
        for nic in member["nics"]:
            for fwd in nic.get("forwards", []):
                scheme = WEB_PORTS.get(int(fwd["guest"])) if str(fwd["guest"]).isdigit() else None
                if not scheme or fwd.get("via"):
                    continue
                host_url = f'{scheme}://127.0.0.1:{fwd["host_port"]}/'
                lan_url = f'{scheme}://{lan}:{fwd["guest"]}/' if lan else ""
                where = f'<a href="{host_url}">{esc(host_url)}</a>' + (f'<br><span class="sub">from the lab: {esc(lan_url)}</span>' if lan_url else "")
                rows.append(f'<tr><td><b>{esc(member["name"])}</b> web GUI</td><td>{where}</td><td>{_login_html(entry)}</td></tr>')
        if member["role"] in ("client", "desktop") and entry:
            rows.append(f'<tr><td><b>{esc(member["name"])}</b> desktop</td><td><code>vmctl attach {esc(member["name"])}</code>'
                        f'<span class="sub"> · autologin</span></td><td>{_login_html(entry)}</td></tr>')
        if member["ssh"]:
            rows.append(f'<tr><td><b>{esc(member["name"])}</b> SSH</td><td><code>{esc(member["ssh"])}</code></td>'
                        f'<td><span class="sub">project key, no password</span></td></tr>')
        for svc in member["services"]:
            url = str(svc.get("url") or "")
            rows.append(f'<tr><td><b>{esc(str(svc.get("name")))}</b> (CT {esc(str(svc.get("container")))})</td>'
                        f'<td><span class="sub">from the lab:</span> <a href="{esc(url)}">{esc(url)}</a></td>'
                        f'<td><span class="sub">no login</span></td></tr>')
    if not rows:
        return '<p class="sub">No web UI or SSH access declared.</p>'
    return ('<div class="table-wrap"><table><thead><tr><th>What</th><th>Where</th><th>Login</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


# Guest ports that serve a web page, and the scheme they speak.
WEB_PORTS = {80: "http", 8080: "http", 8081: "http", 443: "https", 8006: "https", 8443: "https"}


def _forward_html(fwd: dict[str, Any]) -> str:
    target = f'127.0.0.1:{fwd["host_port"]} → ' + (str(fwd["via"]) if fwd.get("via") else f':{fwd["guest"]}')
    what = f' · {html.escape(fwd["what"])}' if fwd["what"] else ""
    scheme = WEB_PORTS.get(int(fwd["guest"])) if str(fwd["guest"]).isdigit() else None
    if scheme:
        url = f'{scheme}://127.0.0.1:{fwd["host_port"]}/'
        return f'<a href="{url}">{html.escape(target)}</a>{what}'
    return f'{html.escape(target)}{what}'


def render_html(lab: dict[str, Any], generated: datetime | None = None) -> str:
    esc = html.escape
    when = (generated or datetime.now()).strftime("%Y-%m-%d %H:%M")
    running = sum(member["running"] for member in lab["members"])
    rows = []
    for member in lab["members"]:
        nics = []
        for nic in member["nics"]:
            if nic["type"] == "user":
                fwd = "<br>".join(_forward_html(f) for f in nic["forwards"]) or "no forwards"
                nics.append(f'<b>NAT</b> <code>{esc(nic["mac"])}</code><br>{fwd}')
            else:
                nics.append(f'<b>{esc(nic["segment"])}</b> <code>{esc(nic["mac"])}</code><br>{esc(nic["address"] or "—")}')
        for service in member["services"]:
            url = str(service.get("url") or "")
            link = f'<a href="{esc(url)}">{esc(url)}</a>' if url else esc(str(service.get("address") or ""))
            nics.append(f'<b>CT {esc(str(service.get("container", "?")))} {esc(str(service.get("name", "")))}</b><br>{link}')
        state = "running" if member["running"] else "stopped"
        rows.append(f'<tr><td><b>{esc(member["name"])}</b><br><span class="sub">{esc(member["label"])}</span></td>'
                    f'<td>{esc(member["role"])}</td><td>{state}<br><span class="sub">{esc(member["install"])}</span></td>'
                    f'<td>{_login_html(member.get("login"))}</td>'
                    f'<td>{"<br><br>".join(nics)}</td></tr>')
    group = esc(lab["group"])
    order = " → ".join(esc(name) for name in lab["start_order"])
    content = lab.get("content") or {}
    heading = esc(str(content.get("title") or lab["group"]))
    intro = f'<p>{esc(content["summary"])}</p>' if content.get("summary") else ""
    if content:
        pieces = [f'content in <code>{esc(content["dir"])}/</code>']
        if content.get("guides"):
            pieces.append("guide " + ", ".join(f'<code>{esc(path)}</code>' for path in content["guides"].values()))
        if content.get("tests"):
            pieces.append(f'{len(content["tests"])} test script(s) under <code>tests/</code>')
        intro += f'<p class="sub">{" · ".join(pieces)}</p>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{group} network map</title><style>{_CSS}</style></head>
<body><main>
<h1>{heading}</h1>
<p class="sub">{group} · {len(lab["members"])} VMs · {running} running · {len(lab["segments"])} segment(s) · generated {when} by <code>vmctl group map {group}</code></p>
{intro}
<div class="map">{_svg(lab)}</div>
<p class="sub">Every NAT NIC is a private slirp {SLIRP_SUBNET} of its own VM: the guest reaches the Internet, the host reaches it only through the forwards on 127.0.0.1. The segments are shared between the VMs of this host only.</p>
<h2>Access</h2>
{_access_html(lab)}
<h2>Members</h2>
<div class="table-wrap"><table><thead><tr><th>VM</th><th>Role</th><th>State</th><th>Login</th><th>NICs (runtime)</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<h2>Runbook</h2>
<p class="sub">Start order: {order}. Every command below comes from the same profiles as the map, in the order you would run them: <b>run</b> changes something, <b>check</b> is read-only, <b>try</b> is a reversible experiment.</p>
{_runbook_html(lab.get("runbook") or [])}
</main></body></html>
"""


KIND_LABELS = {"do": "run", "check": "check", "try": "try"}


def _runbook_html(sections: list[dict[str, Any]]) -> str:
    esc = html.escape
    toc = "".join(f'<li><a href="#step-{index}">{esc(section["title"])}</a></li>'
                  for index, section in enumerate(sections, start=1))
    parts = [f'<ol class="toc">{toc}</ol>']
    for index, section in enumerate(sections, start=1):
        parts.append(f'<h3 id="step-{index}">{index}. {esc(section["title"])}</h3><p class="sub">{esc(section["text"])}</p>')
        for block in section["blocks"]:
            where = block["where"]
            if block["ssh"]:
                label = f'on <b>{esc(where)}</b> · <code>{esc(block["ssh"])}</code>'
            elif where == "host":
                label = "on the host, in the repository"
            else:
                label = esc(where)
            kind = block.get("kind", "do")
            parts.append(f'<div class="where"><span class="kind {kind}">{KIND_LABELS.get(kind, kind)}</span> {label}</div>'
                         f'<pre class="{kind}">{esc(chr(10).join(block["commands"]))}</pre>')
    return "\n".join(parts)


# --- the guide as a page ----------------------------------------------------------------------

_SAFE_URL = re.compile(r"^https?://[^\s\"'<>]+$")


def _inline(text: str) -> str:
    """Escaped first, then `code`, **bold**, *em* and [links](http...); a relative link (a path
    inside the repository) has no meaning in the browser and stays its text."""
    out: list[str] = []
    for index, part in enumerate(text.split("`")):
        if index % 2:
            out.append(f"<code>{html.escape(part)}</code>")
            continue
        piece = html.escape(part, quote=False)
        piece = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", piece)
        piece = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])", r"<em>\1</em>", piece)

        def link(match: re.Match[str]) -> str:
            label, url = match.group(1), html.unescape(match.group(2))
            if _SAFE_URL.match(url):
                return f'<a href="{html.escape(url)}" target="_blank" rel="noopener">{label}</a>'
            return label
        out.append(re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, piece))
    return "".join(out)


def render_markdown(text: str) -> str:
    """The subset the lab guides use: headings, paragraphs, fenced code, tables, lists, quotes.
    Every piece of text is escaped; nothing in a guide is ever HTML."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                body.append(lines[i])
                i += 1
            out.append(f"<pre>{html.escape(chr(10).join(body))}</pre>")
            i += 1
            continue
        heading = re.match(r"^(#{1,4})\s+(.*)$", line)
        if heading:
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            i += 1
            continue
        if line.startswith("|"):
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [cell.strip() for cell in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells):
                    rows.append(cells)
                i += 1
            head, *body_rows = rows or [[]]
            out.append("<div class=\"table-wrap\"><table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head)
                       + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>" for row in body_rows)
                       + "</tbody></table></div>")
            continue
        listed = re.match(r"^\s*([-*]|\d+\.)\s+(.*)$", line)
        if listed:
            tag = "ol" if listed.group(1)[0].isdigit() else "ul"
            items: list[str] = []
            while i < len(lines):
                item = re.match(r"^\s*([-*]|\d+\.)\s+(.*)$", lines[i])
                if item:
                    items.append(item.group(2))
                elif lines[i].startswith("  ") and lines[i].strip() and items:
                    items[-1] += " " + lines[i].strip()  # a wrapped item
                else:
                    break
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(item)}</li>" for item in items) + f"</{tag}>")
            continue
        if line.startswith(">"):
            quoted: list[str] = []
            while i < len(lines) and lines[i].startswith(">"):
                quoted.append(lines[i].lstrip(">").strip())
                i += 1
            out.append(f"<blockquote>{_inline(' '.join(quoted))}</blockquote>")
            continue
        if not line.strip():
            i += 1
            continue
        paragraph: list[str] = []
        while i < len(lines) and lines[i].strip() and not re.match(r"^(```|#{1,4}\s|\||>|\s*([-*]|\d+\.)\s)", lines[i]):
            paragraph.append(lines[i].strip())
            i += 1
        out.append(f"<p>{_inline(' '.join(paragraph))}</p>")
    return "\n".join(out)


def guide_page(content: dict[str, Any], lang: str, token: str = "") -> str:
    """``vms/labs/<lab>/guide.<lang>.md`` as a self-contained page in the map's style, with a
    link to the other language (the token travels so the link works in the web UI)."""
    path = runtime.resolve_path(content["guides"][lang])
    body = render_markdown(path.read_text(encoding="utf-8"))
    group = html.escape(content["group"])
    suffix = f"&amp;token={html.escape(token)}" if token else ""
    langs = " · ".join(f"<b>{code.upper()}</b>" if code == lang else f'<a href="?lang={code}{suffix}">{code.upper()}</a>'
                       for code in GUIDE_LANGS if code in content["guides"])
    return f"""<!doctype html>
<html lang="{html.escape(lang)}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{group} guide</title><style>{_CSS}
main blockquote {{ margin:12px 0; padding:8px 14px; border-left:3px solid var(--accent); color:var(--muted); }}
main pre {{ white-space:pre-wrap; }}
</style></head>
<body><main>
<p class="sub">{group} · guide · {langs} · <code>{html.escape(content["guides"][lang])}</code></p>
{body}
</main></body></html>
"""


def map_path(group: str) -> Path:
    return runtime.resolve_path(f"artifacts/labs/{group}/network.html")


def write_map(lab: dict[str, Any], dest: Path | None = None) -> Path:
    path = dest or map_path(lab["group"])
    runtime.ensure_parent(path)
    path.write_text(render_html(lab), encoding="utf-8")
    (path.parent / "lab.json").write_text(json.dumps(lab, indent=2) + "\n", encoding="utf-8")
    return path
