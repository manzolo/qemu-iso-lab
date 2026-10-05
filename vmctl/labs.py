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

_BOX_W, _BOX_H, _GAP, _MARGIN = 284, 190, 44, 44
_TOP_Y, _TOP_H = 30, 78           # the host and Internet nodes
_NAT_Y = _TOP_Y + _TOP_H + 46     # the NAT bus every VM hangs from
_PILL_H, _PILL_GAP = 24, 8        # one forward = one pill on the VM's cable


def _pill_width(text: str) -> float:
    return 18 + 7.4 * len(text)


def _device_icon(role: str, x: float, y: float) -> str:
    """Small vector devices, sharing the enclosing node's power state."""
    if role in ("client", "desktop", "host"):
        shapes = ('<path class="device-top" d="M5 8 13 2H65L57 8Z"/>'
                  '<rect class="device-body" x="5" y="8" width="52" height="35" rx="4"/>'
                  '<path class="device-side" d="M57 8 65 2V36L57 43Z"/>'
                  '<rect class="device-screen" x="10" y="13" width="42" height="24" rx="2"/>'
                  '<path class="device-detail" d="m17 20 5 4-5 4m11 0h12M31 44v8m-14 2h29"/>'
                  '<circle class="device-led" cx="48" cy="40" r="1.5"/>')
    elif role in ("router", "firewall"):
        shapes = ('<path class="device-top" d="M3 24 17 12H66L53 24Z"/>'
                  '<path class="device-side" d="m53 24 13-12v23L53 48Z"/>'
                  '<rect class="device-body" x="3" y="24" width="50" height="24" rx="3"/>'
                  '<path class="device-detail" d="M17 18h24m-4-3 4 3-4 3M12 33h7v7h-7m13-7h7v7h-7m13-7h7v7h-7"/>'
                  '<circle class="device-led" cx="9" cy="44" r="2"/>')
    else:
        shapes = ('<path class="device-top" d="M13 9 25 1H60L48 9Z"/>'
                  '<path class="device-side" d="m48 9 12-8v46L48 57Z"/>'
                  '<rect class="device-body" x="13" y="9" width="35" height="48" rx="3"/>'
                  '<rect class="device-slot" x="18" y="15" width="25" height="9" rx="2"/>'
                  '<rect class="device-slot" x="18" y="28" width="25" height="9" rx="2"/>'
                  '<path class="device-detail" d="M20 46h12m-12 4h12"/>'
                  '<circle class="device-led" cx="38" cy="19.5" r="2"/>'
                  '<circle class="device-led" cx="38" cy="32.5" r="2"/>'
                  '<circle class="device-led" cx="39" cy="48" r="2.5"/>')
    return f'<g class="device" transform="translate({x} {y})">{shapes}</g>'


def _link(x1: float, y1: float, x2: float, y2: float, active: bool, kind: str) -> str:
    power = "online" if active else "offline"
    coords = f'x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"'
    return (f'<g class="link {kind} {power}"><line class="cable-hit" {coords}/><line class="cable-halo" {coords}/><line class="cable" {coords}/>'
            f'<line class="signal tx" {coords}/><line class="signal rx" {coords}/>'
            f'<circle class="socket" cx="{x1}" cy="{y1}" r="4"/>'
            f'<circle class="socket" cx="{x2}" cy="{y2}" r="4"/></g>')


def _host_node(x: float, y: float) -> list[str]:
    """The computer running QEMU: a monitor icon, its address and what reaches the lab from it."""
    return [
        f'<g class="node host-n online"><rect class="node-panel" x="{x}" y="{y}" width="320" height="{_TOP_H}" rx="12"/>',
        _device_icon("host", x + 10, y + 10),
        f'<text class="node-t" x="{x + 88}" y="{y + 32}">Host · 127.0.0.1</text>',
        f'<text class="small" x="{x + 88}" y="{y + 53}">vmctl / SSH / browser</text></g>',
    ]


def _internet_node(x: float, y: float) -> list[str]:
    cloud = (f"M{x + 40} {y + 58} h120 a26 26 0 0 0 0 -52 a34 34 0 0 0 -62 -8 "
             f"a30 30 0 0 0 -56 14 a22 22 0 0 0 -2 46 z")
    return [f'<g class="node net-n"><path class="cloud" d="{cloud}"/>',
            f'<text class="node-t" x="{x + 100}" y="{y + 40}" text-anchor="middle">Internet</text></g>']


def _svg(lab: dict[str, Any]) -> str:
    members = lab["members"]
    count = max(1, len(members))
    box_h = _BOX_H + 22 * max((len(m["services"]) for m in members), default=0)
    width = max(860, 2 * _MARGIN + count * _BOX_W + (count - 1) * _GAP)
    pills = max((len(n["forwards"]) for m in members for n in m["nics"] if n["type"] == "user"), default=0)
    box_y = _NAT_Y + 48 + pills * (_PILL_H + _PILL_GAP)
    seg_top = box_y + box_h + 100
    height = seg_top + 100 * max(1, len(lab["segments"]))
    start_x = (width - count * _BOX_W - (count - 1) * _GAP) / 2
    x_of = {m["name"]: start_x + i * (_BOX_W + _GAP) for i, m in enumerate(members)}
    seg_y = {seg["name"]: seg_top + 100 * i for i, seg in enumerate(lab["segments"])}
    esc = html.escape
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="Network map of {esc(lab["group"])}">',
           '<defs><linearGradient id="chassis" x2="0" y2="1"><stop stop-color="#21364b"/><stop offset="1" stop-color="#101e2e"/></linearGradient>'
           '<linearGradient id="panel" x2="0" y2="1"><stop stop-color="#15283b"/><stop offset="1" stop-color="#0d1929"/></linearGradient>'
           '<pattern id="grid" width="24" height="24" patternUnits="userSpaceOnUse"><path d="M24 0H0V24" fill="none" stroke="#274054" stroke-width=".5" opacity=".5"/></pattern></defs>',
           f'<rect width="{width}" height="{height}" fill="url(#grid)"/>']
    internet_x = width - _MARGIN - 200
    nat_active = any(m["running"] and any(n["type"] == "user" for n in m["nics"]) for m in members)
    out.append(_link(_MARGIN, _NAT_Y, width - _MARGIN, _NAT_Y, nat_active, "nat-bus"))
    out.append(_link(_MARGIN + 160, _TOP_Y + _TOP_H, _MARGIN + 160, _NAT_Y, nat_active, "uplink"))
    out.append(_link(internet_x + 100, _TOP_Y + _TOP_H - 20, internet_x + 100, _NAT_Y, nat_active, "uplink"))
    out += _host_node(_MARGIN, _TOP_Y)
    out += _internet_node(internet_x, _TOP_Y)
    out.append(f'<text class="network-caption" x="{width / 2}" y="{_NAT_Y - 13}" text-anchor="middle">NAT / {SLIRP_SUBNET} per VM / localhost forwards</text>')
    # Shared segments are a logical bus, not an extra switch or a claimed Internet probe.
    for seg in lab["segments"]:
        y = seg_y[seg["name"]]
        active = any(m["running"] and m["name"] in seg["members"] for m in members)
        out.append(f'<g class="segment-track" data-segment="{esc(seg["name"])}">'
                   + _link(_MARGIN, y, width - _MARGIN, y, active, "segment-bus") + '</g>')
        subnet = f' · {seg["subnet"]}' if seg["subnet"] else ""
        out.append(f'<text class="bus-t" x="{_MARGIN}" y="{y + 29}">segment {esc(seg["name"])}{esc(subnet)}</text>')
        out.append(f'<text class="network-caption" x="{width - _MARGIN}" y="{y + 29}" text-anchor="end">ISOLATED LAN</text>')
    for member in members:
        x = x_of[member["name"]]
        cx = x + _BOX_W / 2
        power = "online" if member["running"] else "offline"
        status = "running" if member["running"] else ("stopped" if member["install"] not in ("", "no disk") else "absent")
        badge = {"running": "RUNNING", "stopped": "STOPPED", "absent": "NO DISK"}[status]
        for nic in member["nics"]:
            attrs = f'data-vm="{esc(member["name"])}" data-nic="{esc(nic["id"])}"'
            if nic["type"] == "user":
                out.append(f'<g class="nic nat-nic {power}" {attrs}><title>NAT · {esc(member["name"])} · {badge}</title>')
                out.append(_link(cx, box_y, cx, _NAT_Y, member["running"], "nat"))
                for line, fwd in enumerate(nic["forwards"]):
                    if fwd.get("via"):
                        text = f'{fwd["host_port"]} → {fwd["via"]}'
                    else:
                        what = f' {fwd["what"]}' if fwd["what"] and len(fwd["what"]) <= 12 else ""
                        text = f'{fwd["host_port"]} → :{fwd["guest"]}{what}'
                    pill_w = min(_BOX_W + _GAP - 12, _pill_width(text))
                    y = _NAT_Y + 18 + line * (_PILL_H + _PILL_GAP)
                    fit = f' textLength="{pill_w - 18}" lengthAdjust="spacingAndGlyphs"' if _pill_width(text) > pill_w else ""
                    out.append(f'<rect class="pill" x="{cx - pill_w / 2}" y="{y}" width="{pill_w}" height="{_PILL_H}" rx="6"/>')
                    out.append(f'<text class="pill-t" x="{cx}" y="{y + 16.5}" text-anchor="middle"{fit}>{esc(text)}</text>')
                out.append(f'<text class="traffic-label" x="{cx + 10}" y="{box_y - 12}"></text></g>')
        out.append(f'<g class="vm {status} {power}"><title>{esc(member["label"])} · {badge}</title>'
                   f'<rect class="vm-panel" x="{x}" y="{box_y}" width="{_BOX_W}" height="{box_h}" rx="14"/>'
                   f'<path class="vm-accent" d="M{x + 20} {box_y}h{_BOX_W - 40}"/>')
        role = member["role"] or "vm"
        out.append(_device_icon(role, x + 15, box_y + 16))
        out.append(f'<circle class="status-led" cx="{x + _BOX_W - 94}" cy="{box_y + 33}" r="3"/>')
        out.append(f'<text class="badge {status}" x="{x + _BOX_W - 82}" y="{box_y + 37}">{badge}</text>')
        out.append(f'<text class="role" x="{x + 94}" y="{box_y + 65}">{esc(role.upper())}</text>')
        fit = f' textLength="{_BOX_W - 36}" lengthAdjust="spacingAndGlyphs"' if len(member["name"]) > 25 else ""
        out.append(f'<text class="vm-n" x="{x + 18}" y="{box_y + 106}"{fit}>{esc(member["name"])}</text>')
        ram = f'{member["memory_mb"] / 1024:g} GB' if member["memory_mb"] >= 1024 else f'{member["memory_mb"]} MB'
        disks = f' · {member["disks"]} disks' if member["disks"] > 1 else ""
        out.append(f'<text class="small" x="{x + 18}" y="{box_y + 131}">{ram} RAM · {member["cpus"]} vCPU{disks}</text>')
        out.append(f'<path class="divider" d="M{x + 18} {box_y + 146}h{_BOX_W - 36}"/>')
        out.append(f'<text class="small" x="{x + 18}" y="{box_y + 170}">disk / {esc(member["install"] or "not prepared")}</text>')
        for index, service in enumerate(member["services"]):
            where = str(service.get("address") or "").split("/")[0]
            label = f'CT {service.get("container", "?")} {service.get("name", "")} · {where}'
            out.append(f'<text class="svc" x="{x + 18}" y="{box_y + _BOX_H + 6 + 22 * index}">{esc(label)}</text>')
        out.append('</g>')
        lans = [nic for nic in member["nics"] if nic["type"] == "segment"]
        for index, nic in enumerate(lans):
            y = seg_y[nic["segment"]]
            # Separate ports and labels for guests attached to more than one segment.
            port_x = x + 24 + index * 18
            attrs = f'data-vm="{esc(member["name"])}" data-nic="{esc(nic["id"])}"'
            out.append(f'<g class="nic lan-nic {power}" data-segment="{esc(nic["segment"])}" {attrs}><title>{esc(nic["segment"])} · {esc(member["name"])} · {badge}</title>')
            out.append(_link(port_x, box_y + box_h, port_x, y, member["running"], "seg-l"))
            label_x = x + 32 + len(lans) * 18
            out.append(f'<text class="addr" x="{label_x}" y="{y - 58}">{esc(nic["address"] or "address not declared")}</text>')
            out.append(f'<text class="small mac" x="{label_x}" y="{y - 39}">{esc(nic["mac"] or "MAC not specified")}</text>')
            out.append(f'<text class="traffic-label" x="{label_x}" y="{y - 17}"></text>')
            # The packet inspector opens from this small lens, never from hovering the cable
            # (Manzolo, 2026-10-05: windows popping up while moving the pointer were a nuisance).
            bx, by = label_x + 150, y - 43
            out.append(f'<g class="inspect-btn" role="button" tabindex="0" aria-controls="packet-inspector" aria-pressed="false" '
                       f'aria-label="Inspect packets: {esc(member["name"])} / {esc(nic["id"])}">'
                       f'<circle class="bg" cx="{bx}" cy="{by}" r="11"/><circle class="lens" cx="{bx - 1.5}" cy="{by - 1.5}" r="4"/>'
                       f'<line class="lens" x1="{bx + 1.5}" y1="{by + 1.5}" x2="{bx + 5}" y2="{by + 5}"/></g></g>')
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
.map-shell { margin:24px 0 12px; border:1px solid #2b4258; border-radius:16px; overflow:hidden; background:#091321; color:#e4eef9; box-shadow:0 16px 50px #0002; }
.map-toolbar { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:12px; padding:16px 22px; border-bottom:1px solid #24384c; background:#102031; }
.map-title { font-size:12px; font-weight:750; letter-spacing:.14em; }
.map-legend { display:flex; gap:18px; font-size:12px; color:#a5b9cf; align-items:center; }
.map-legend span { display:flex; align-items:center; gap:6px; }
.map-legend i { display:inline-block; width:7px; height:7px; border-radius:50%; background:#52657a; }
.map-legend .on { background:#4fe0b6; box-shadow:0 0 9px #4fe0b655; }
.map-legend .flow { background:#70d9ff; border-radius:2px; width:15px; height:3px; }
.map { overflow-x:auto; background:radial-gradient(ellipse at 48% 40%, #12283c99, transparent 65%); }
.map-footer { display:flex; gap:12px; justify-content:space-between; flex-wrap:wrap; padding:12px 22px; border-top:1px solid #24384c; font-size:12px; color:#9bb0c6; }
#map-live { color:#83d9ed; }
.map svg { width:100%; min-width:850px; height:auto; display:block; }
.map svg text { fill:#e0ebf7; font:14px system-ui, sans-serif; }
.map svg .small { fill:#97aec6; font-size:12.5px; }
.map svg .mac { font:11px ui-monospace, monospace; fill:#8197ae; }
.map svg .network-caption { fill:#8fa7bf; font:10px ui-monospace, monospace; letter-spacing:.07em; }
.map svg .node-panel, .map svg .cloud { fill:url(#panel); stroke:#3c718a; stroke-width:1.5; }
.map svg .node-t { font-weight:650; font-size:16px; fill:#a3dff4; }
.map svg .device-body { fill:url(#chassis); stroke:#7597b4; stroke-width:1.2; }
.map svg .device-top { fill:#354c63; stroke:#7597b4; stroke-width:1; }
.map svg .device-side { fill:#152438; stroke:#7597b4; stroke-width:1; }
.map svg .device-slot, .map svg .device-screen { fill:#0a1625; stroke:#49667f; stroke-width:1; }
.map svg .device-detail { fill:none; stroke:#7794ac; stroke-width:1.5; stroke-linecap:round; stroke-linejoin:round; }
.map svg .device-led, .map svg .status-led { fill:#536477; }
.map svg .online .device-led, .map svg .online .status-led { fill:#50e6b5; filter:drop-shadow(0 0 4px #50e6b588); }
.map svg .online .device-screen { fill:#103345; stroke:#479eac; }
.map svg .online .device-detail { stroke:#7ce1e7; }
.map svg .link { --cable:#58c9ef; }
.map svg .link.segment-bus, .map svg .link.seg-l { --cable:#a997ff; }
.map svg .cable { stroke:#34475c; stroke-width:2; fill:none; }
.map svg .offline .cable { stroke-dasharray:5 6; }
.map svg .online .cable { stroke:var(--cable); stroke-opacity:.25; }
.map svg .cable-halo { stroke:var(--cable); stroke-width:9; opacity:0; pointer-events:none; }
.map svg .link.active .cable { stroke-opacity:var(--traffic-strength, .8); stroke-width:3; filter:drop-shadow(0 0 5px var(--cable)); }
.map svg .link.active .cable-halo { opacity:var(--traffic-halo, .18); filter:blur(4px); }
.map svg .segment-bus .cable, .map svg .nat-bus .cable { stroke-width:3; }
.map svg .socket { fill:#132337; stroke:#536b82; stroke-width:1.5; }
.map svg .online .socket { fill:#254052; stroke:#698694; }
.map svg .link.active .socket { fill:var(--cable); stroke:#d5f5ff; filter:drop-shadow(0 0 5px var(--cable)); }
.map svg .signal { stroke:#c9f6ff; stroke-width:3; stroke-dasharray:3 24; stroke-linecap:round; opacity:0; pointer-events:none; }
.map svg .signal.tx { transform:translateX(-2px); }
.map svg .signal.rx { transform:translateX(2px); stroke:#c4b5ff; }
.map svg .nic.transmitting .signal.tx { opacity:1; animation:packet-flow var(--traffic-speed, 1.5s) linear infinite; filter:drop-shadow(0 0 5px #72e7ff); }
.map svg .nic.receiving .signal.rx { opacity:1; animation:packet-flow var(--traffic-speed, 1.5s) linear infinite reverse; }
@keyframes packet-flow { to { stroke-dashoffset:-54; } }
.map svg .pill { fill:#102236; stroke:#36506a; stroke-width:1; }
.map svg .online .pill { stroke:#43869b; }
.map svg .pill-t { fill:#a3b9d0; font:11.5px ui-monospace, monospace; }
.map svg .online .pill-t { fill:#a5e8fb; }
.map svg .vm-panel { fill:url(#panel); stroke:#334a62; stroke-width:1.2; }
.map svg .vm.online .vm-panel { stroke:#398573; filter:drop-shadow(0 4px 12px #42ddb112); }
.map svg .vm-accent { stroke:#53677c; stroke-width:2; }
.map svg .vm.online .vm-accent { stroke:#50e6b5; filter:drop-shadow(0 0 5px #50e6b566); }
.map svg .vm-n { font-weight:650; font-size:18px; letter-spacing:-.3px; }
.map svg .role { fill:#8ca7c0; font:10px ui-monospace, monospace; letter-spacing:.12em; }
.map svg .badge { fill:#97aabd; font:10px ui-monospace, monospace; letter-spacing:.04em; }
.map svg .badge.running { fill:#62e3bc; } .map svg .badge.absent { fill:#e7b768; }
.map svg .divider { stroke:#293e53; stroke-width:1; }
.map svg .svc { fill:#bcb0fa; font-size:12px; }
.map svg .bus-t { fill:#b6aafa; font:12px ui-monospace, monospace; }
.map svg .addr { font:12px ui-monospace, monospace; fill:#c8daf0; }
.map svg .traffic-label { fill:#77def3; font:10px ui-monospace, monospace; }
.map svg .nic.offline .traffic-label { fill:#778b9f; }
.map svg .cable-hit { stroke:transparent; stroke-width:18; pointer-events:none; }
.map svg .nic.inspected .cable { stroke:#eefaff; stroke-width:3; }
.map svg .inspect-btn { cursor:pointer; }
.map svg .inspect-btn .bg { fill:#182f43; stroke:#3c5c74; stroke-width:1.5; }
.map svg .inspect-btn .lens { fill:none; stroke:#9bd8ee; stroke-width:1.8; stroke-linecap:round; }
.map svg .inspect-btn:hover .bg, .map svg .inspect-btn:focus .bg { stroke:#a3ecff; fill:#1f3d55; }
.map svg .inspect-btn:focus { outline:none; }
.map svg .nic.inspected .inspect-btn .bg { fill:#245369; stroke:#6fcfe3; }
.map svg .nic.offline .inspect-btn { opacity:.45; }
.packet-inspector { position:fixed; z-index:20; width:min(820px, calc(100vw - 24px)); height:min(560px, calc(100dvh - 24px)); min-width:320px; min-height:240px;
  resize:both; overflow:hidden; display:flex; flex-direction:column; background:#101c2a; color:#dce9f5; border:1px solid #3c5c74; border-radius:12px; box-shadow:0 18px 48px rgba(0,0,0,.55); }
.packet-inspector[hidden] { display:none; }
.packet-head { display:flex; justify-content:space-between; align-items:center; gap:12px; padding:12px 16px; border-bottom:1px solid #263b50; cursor:move; user-select:none; }
.packet-head > div:first-child { display:flex; align-items:baseline; gap:12px; min-width:0; }
.packet-head strong { font-size:12px; letter-spacing:.12em; color:#8fd3ea; }
.packet-head #packet-link { font:600 14px/1.3 system-ui, sans-serif; color:#e8f2fb; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.packet-actions { display:flex; gap:6px; flex-shrink:0; }
.packet-inspector button { background:#182f43; color:#cce5f7; border:1px solid #3c5c74; padding:5px 9px; border-radius:6px; font:inherit; cursor:pointer; }
.packet-inspector button[aria-pressed="true"] { background:#245369; border-color:#6fcfe3; color:#fff; }
.packet-inspector button:focus-visible, .packet-inspector input:focus-visible { outline:2px solid #a3ecff; outline-offset:2px; }
.packet-filters { display:flex; flex-wrap:wrap; align-items:center; gap:8px 14px; padding:10px 16px; border-bottom:1px solid #263b50; background:#0d1823; }
.packet-chips { display:inline-flex; gap:4px; }
.packet-inspector .chip { padding:3px 9px; border-radius:999px; font-size:11px; font-family:ui-monospace, monospace; }
.packet-inspector .chip-tx[aria-pressed="true"] { color:#78dff7; } .packet-inspector .chip-rx[aria-pressed="true"] { color:#c2adff; }
.packet-inspector #packet-search { flex:1 1 160px; min-width:120px; background:#0b1420; color:#dce9f5; border:1px solid #3c5c74; border-radius:6px; padding:4px 8px; font:12px ui-monospace, monospace; }
.packet-inspector #packet-state { margin:0; padding:6px 16px; font-size:11px; color:#9bb7cf; }
.packet-scroll { flex:1 1 auto; overflow:auto; }
.packet-inspector table { border:0; border-radius:0; background:transparent; table-layout:fixed; width:100%; margin:0; }
.packet-inspector th, .packet-inspector td { border-color:#263b50; padding:8px; font-size:11px; }
.packet-inspector th { position:sticky; top:0; background:#142639; color:#9bb7cf; font-size:10px; }
.packet-inspector td { font-family:ui-monospace,monospace; }
.packet-inspector .packet-time { width:64px; } .packet-inspector .packet-dir { width:28px; }
.packet-inspector .packet-proto { width:53px; } .packet-inspector .packet-size { width:40px; text-align:right; }
.packet-inspector .packet-detail { color:#9bb7cf; font-size:10px; }
.packet-inspector .packet-tx { color:#78dff7; } .packet-inspector .packet-rx { color:#c2adff; }
.packet-inspector #packet-empty { margin:0; padding:18px 16px; color:#9bb7cf; font-size:12px; }
.packet-foot { display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; margin:0; padding:8px 16px; border-top:1px solid #263b50; font-size:10px; color:#7f98b0; }
.packet-foot #packet-counts { font-family:ui-monospace, monospace; color:#9bd8ee; }
@media (max-width:480px) { .packet-inspector .packet-time { display:none; } .packet-head { flex-wrap:wrap; } .packet-inspector { resize:none; height:min(70dvh, calc(100dvh - 24px)); } }
@media (prefers-reduced-motion: reduce) {
  .map svg .nic.transmitting .signal.tx, .map svg .nic.receiving .signal.rx { animation:none; }
}
@media (max-width:600px) { .map-toolbar, .map-footer { padding:12px; } .map-legend { gap:12px; } }
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
                nics.append(f'<b>NAT</b> <code>{esc(nic["mac"] or "MAC not specified")}</code><br>{fwd}')
            else:
                nics.append(f'<b>{esc(nic["segment"])}</b> <code>{esc(nic["mac"] or "MAC not specified")}</code><br>{esc(nic["address"] or "—")}')
        for service in member["services"]:
            url = str(service.get("url") or "")
            link = f'<a href="{esc(url)}">{esc(url)}</a>' if url else esc(str(service.get("address") or ""))
            nics.append(f'<b>CT {esc(str(service.get("container", "?")))} {esc(str(service.get("name", "")))}</b><br>{link}')
        state = "running" if member["running"] else "stopped"
        rows.append(f'<tr data-member="{esc(member["name"])}"><td><b>{esc(member["name"])}</b><br><span class="sub">{esc(member["label"])}</span></td>'
                    f'<td>{esc(member["role"])}</td><td><span class="member-state">{state}</span><br><span class="sub member-install">{esc(member["install"])}</span></td>'
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
<p class="sub">{group} · {len(lab["members"])} VMs · <span id="running-count">{running} running</span> · {len(lab["segments"])} segment(s) · generated {when} by <code>vmctl group map {group}</code></p>
{intro}
<section class="map-shell" aria-label="Lab topology">
<div class="map-toolbar"><span class="map-title">NETWORK TOPOLOGY</span><div class="map-legend"><span><i class="on"></i>VM on</span><span><i></i>VM off</span><span><i class="flow"></i>Measured traffic</span></div></div>
<div class="map" id="lab-topology">{_svg(lab)}</div>
<div class="map-footer"><span id="map-live" role="status">Snapshot · generated with vmctl</span><span>The lens on a LAN cable opens its packet inspector</span></div>
</section>
<aside id="packet-inspector" class="packet-inspector" aria-label="Packet inspector" hidden>
<div class="packet-head" id="packet-drag" title="Drag to move"><div><strong>PACKET INSPECTOR</strong><span id="packet-link"></span></div><div class="packet-actions">
<button id="packet-pause" type="button" aria-pressed="false">Pause</button>
<button id="packet-clear" type="button">Clear</button>
<button id="packet-close" type="button" aria-label="Close packet inspector">Close</button></div></div>
<div class="packet-filters" role="group" aria-label="Packet filters">
<span class="packet-chips" data-filter="proto"><button type="button" class="chip" data-proto="all" aria-pressed="true">All</button><button type="button" class="chip" data-proto="TCP" aria-pressed="false">TCP</button><button type="button" class="chip" data-proto="UDP" aria-pressed="false">UDP</button><button type="button" class="chip" data-proto="ICMP" aria-pressed="false">ICMP</button><button type="button" class="chip" data-proto="ARP" aria-pressed="false">ARP</button><button type="button" class="chip" data-proto="other" aria-pressed="false">Other</button></span>
<span class="packet-chips" data-filter="dir"><button type="button" class="chip chip-tx" data-dir="TX" aria-pressed="true">TX</button><button type="button" class="chip chip-rx" data-dir="RX" aria-pressed="true">RX</button></span>
<input id="packet-search" type="search" placeholder="IP, port, text…" aria-label="Filter packets by text" autocomplete="off">
</div>
<p id="packet-state"></p>
<div class="packet-scroll"><table aria-label="Recent packets"><thead><tr><th class="packet-time">Time</th><th class="packet-dir">Dir</th><th class="packet-proto">Protocol</th><th>Source → destination / detail</th><th class="packet-size">Bytes</th></tr></thead><tbody id="packet-rows"></tbody></table>
<p id="packet-empty">Click the lens on a LAN cable to inspect its packets.</p></div>
<p class="packet-foot"><span id="packet-counts"></span><span>headers only · sampled up to 100 packets/s per segment · no payload stored</span></p>
</aside>
<p class="sub">Every NAT NIC is a private slirp {SLIRP_SUBNET} of its own VM: the guest reaches the Internet, the host reaches it only through the forwards on 127.0.0.1. The segments are shared between the VMs of this host only.</p>
<h2>Access</h2>
{_access_html(lab)}
<h2>Members</h2>
<div class="table-wrap"><table><thead><tr><th>VM</th><th>Role</th><th>State</th><th>Login</th><th>NICs (runtime)</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<h2>Runbook</h2>
<p class="sub">Start order: {order}. Every command below comes from the same profiles as the map, in the order you would run them: <b>run</b> changes something, <b>check</b> is read-only, <b>try</b> is a reversible experiment.</p>
{_runbook_html(lab.get("runbook") or [])}
</main><script id="lab-map-script">{(Path(__file__).parent / "web" / "lab-map.js").read_text()}</script></body></html>
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
