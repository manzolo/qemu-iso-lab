"""``vmctl link``: connect running VMs on a private segment, without restarting them.

Every VM booted on its own has an isolated slirp network (the same 10.0.2.15 in each guest), so
two running machines cannot talk. A link hot-plugs a NIC into each of them over QMP, on the
multicast socket of a shared segment (the same transport the labs use), gives it a stable MAC
and an address in a private /24 (192.168.100.0/24 for the default segment) configured over SSH where the guest is a Linux or
FreeBSD system vmctl can reach. Nothing is written to the profiles: the record of who is on a
segment lives in ``artifacts/labs/links/<segment>.json`` and is only as alive as the QEMU
processes it names, so the temporary lab disappears when the VMs stop.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from vmctl import cloud_init, qemu, ssh, state
from vmctl.errors import VMError

DEFAULT_SEGMENT = "session"
LINKS_DIR = ("artifacts", "labs", "links")
# Families whose guests vmctl configures itself; anything else gets the address in the summary.
LINUX_LIKE = frozenset({"debian", "arch", "fedora", "rhel", "suse", "opensuse", "alpine", "nixos", "proxmox", "gentoo", "void", "linux"})
SSH_TIMEOUT_SEC = 90


def record_path(segment: str) -> Path:
    return state.ROOT.joinpath(*LINKS_DIR) / f"{segment}.json"


def load_record(segment: str) -> dict[str, Any]:
    path = record_path(segment)
    if not path.is_file():
        return {"segment": segment, "members": {}}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"segment": segment, "members": {}}
    data.setdefault("segment", segment)
    data.setdefault("members", {})
    return dict(data)


def save_record(record: dict[str, Any]) -> None:
    path = record_path(str(record["segment"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2) + "\n")
    tmp.replace(path)


def qemu_alive(pid: int) -> bool:
    """The recorded QEMU is still the one running: a reused PID is not a member."""
    try:
        raw = (Path("/proc") / str(pid) / "cmdline").read_bytes()
    except OSError:
        return False
    return b"qemu-system" in raw


def prune(record: dict[str, Any]) -> dict[str, Any]:
    """Drop the members whose QEMU is gone; the file follows (and disappears when empty)."""
    members = {name: m for name, m in record["members"].items() if qemu_alive(int(m.get("pid") or 0))}
    if members != record["members"]:
        record["members"] = members
        if members:
            save_record(record)
        else:
            record_path(str(record["segment"])).unlink(missing_ok=True)
    return record


def all_records() -> list[dict[str, Any]]:
    base = state.ROOT.joinpath(*LINKS_DIR)
    if not base.is_dir():
        return []
    records = [prune(load_record(p.stem)) for p in sorted(base.glob("*.json"))]
    return [r for r in records if r["members"]]


def links_of(name: str) -> list[dict[str, Any]]:
    """The segments *name* is on right now, with its address and the other members: a dashboard fact."""
    found = []
    for record in all_records():
        me = record["members"].get(name)
        if me:
            found.append({"segment": record["segment"], "address": me["address"],
                          "peers": [{"name": n, "address": m["address"]} for n, m in record["members"].items() if n != name]})
    return found


SUBNET_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.0/24$")


def subnet(segment: str) -> str:
    """The first three octets of a segment's /24: 192.168.100 for the default one, other names hash
    into 192.168.101-254 (10.0.2.x is slirp, 192.168.0.x and 10.10.10.x are the tracked labs)."""
    if segment == DEFAULT_SEGMENT:
        return "192.168.100"
    return f"192.168.{101 + hashlib.sha256(segment.encode()).digest()[0] % 154}"


def parse_subnet(text: str) -> str:
    """``--subnet A.B.C.0/24`` (the only prefix length the links use) -> ``A.B.C``."""
    match = SUBNET_RE.match(text.strip())
    if not match or any(int(octet) > 255 for octet in match.groups()):
        raise VMError(f"--subnet {text!r}: expected something like 192.168.50.0/24")
    return ".".join(match.groups())


def nic_mac(segment: str, name: str) -> str:
    """Locally administered, stable per (segment, VM): a re-link after a reboot gives the same interface."""
    digest = hashlib.sha256(f"link:{segment}:{name}".encode()).digest()
    return "52:54:01:" + ":".join(f"{b:02x}" for b in digest[:3])


def next_address(record: dict[str, Any]) -> str:
    taken = {str(m.get("address", "")).split("/")[0] for m in record["members"].values()}
    base = str(record.get("subnet") or subnet(str(record["segment"])))
    for host in range(1, 255):
        candidate = f"{base}.{host}"
        if candidate not in taken:
            return f"{candidate}/24"
    raise VMError(f"Segment {record['segment']} has no free address left")


def netdev_id(segment: str) -> str:
    return "link-" + "".join(ch if ch.isalnum() else "-" for ch in segment)[:24]


def used_ports(name: str, pid: int) -> set[str]:
    """Hot-plug root ports this QEMU already holds, across every segment it is on."""
    return {str(m.get("bus")) for r in all_records() for n, m in r["members"].items()
            if n == name and int(m.get("pid") or 0) == pid and m.get("bus")}


def hotplug(vm: dict[str, Any], name: str, pid: int, segment: str, mac: str, mcast: str | None = None) -> dict[str, Any]:
    """Add the NIC to the running QEMU over QMP and return what was plugged (id, bus, mac)."""
    sock = qemu.qmp_socket_path(vm)
    if not sock.exists():
        raise VMError(f"{name} has no QMP socket ({sock}): only a VM booted headless (vmctl start --headless, "
                      "the dashboard's Boot headless) can be linked while it runs")
    device = str(vm.get("network_device", "virtio-net-pci"))
    ident = netdev_id(segment)
    bus: str | None = None
    if str(vm.get("machine")) == "q35":
        free = [port for port in qemu.HOTPLUG_PORTS if port not in used_ports(name, pid)]
        if not free:
            raise VMError(f"{name} has no free hot-plug slot left ({len(qemu.HOTPLUG_PORTS)} per VM)")
        bus = free[0]
    qemu.qmp_execute(sock, "netdev_add", arguments={"type": "socket", "id": ident,
                                                    "mcast": qemu.segment_endpoint(segment, mcast)})
    arguments: dict[str, Any] = {"driver": device, "netdev": ident, "id": f"{ident}-nic", "mac": mac}
    if bus:
        arguments["bus"] = bus
    try:
        qemu.qmp_execute(sock, "device_add", arguments=arguments)
    except VMError as exc:
        qemu.qmp_command(sock, "netdev_del", arguments={"id": ident})
        text = str(exc)
        if bus and ("not found" in text or "No 'PCI' bus" in text):
            raise VMError(f"{name} was booted before hot-plug slots existed: stop it and boot it headless again, "
                          f"then link ({text})") from exc
        raise
    return {"netdev": ident, "device": f"{ident}-nic", "bus": bus, "mac": mac}


def unplug(vm: dict[str, Any], member: dict[str, Any]) -> bool:
    sock = qemu.qmp_socket_path(vm)
    ok = qemu.qmp_command(sock, "device_del", arguments={"id": str(member.get("device") or "")})
    qemu.qmp_command(sock, "netdev_del", arguments={"id": str(member.get("netdev") or "")})
    return ok


def guest_family(vm: dict[str, Any]) -> str:
    return str((vm.get("meta") or {}).get("family") or "").lower()


def guest_script(family: str, mac: str, address: str) -> str | None:
    """What configures the new interface inside the guest, found by its MAC; None when vmctl does not know how."""
    if family == "freebsd":
        return (f"for i in $(seq 1 30); do IF=$(ifconfig -a | awk '/^[a-z]/{{i=$1}} /ether {mac}/{{sub(\":\",\"\",i); print i; exit}}'); "
                f"[ -n \"$IF\" ] && break; sleep 1; done; [ -n \"$IF\" ] || {{ echo 'no interface with {mac}' >&2; exit 3; }}; "
                f"ifconfig \"$IF\" inet {address} up && echo \"$IF\"")
    if family in LINUX_LIKE:
        return (f"for i in $(seq 1 30); do IF=$(ip -o link | awk '/{mac}/{{sub(\":\",\"\",$2); print $2; exit}}'); "
                f"[ -n \"$IF\" ] && break; sleep 1; done; [ -n \"$IF\" ] || {{ echo 'no interface with {mac}' >&2; exit 3; }}; "
                f"ip link set \"$IF\" up && ip addr replace {address} dev \"$IF\" && echo \"$IF\"")
    return None


def configure_guest(vm: dict[str, Any], mac: str, address: str, dry_run: bool = False) -> str | None:
    """Give the hot-plugged interface its address over SSH. Returns the interface name, or None when
    the guest is not one vmctl configures (Windows, hobby systems, no SSH): the caller then prints
    the address to set by hand."""
    script = guest_script(guest_family(vm), mac, address)
    if script is None or cloud_init.ssh_access_config(vm) is None:
        return None
    command = ssh.remote_sudo_shell_cmd(vm, script, dry_run=dry_run)
    if dry_run:
        print("  " + shlex.join(command))
        return "(dry run)"
    result = subprocess.run(command, capture_output=True, text=True, timeout=SSH_TIMEOUT_SEC, check=False)
    if result.returncode != 0:
        detail = (result.stderr.strip() or result.stdout.strip()).splitlines()
        raise VMError(f"could not configure the guest over SSH (exit {result.returncode}): {detail[-1] if detail else 'no output'}")
    lines = result.stdout.strip().splitlines()
    return lines[-1] if lines else "?"


def link(vm: dict[str, Any], name: str, pid: int, segment: str = DEFAULT_SEGMENT, dry_run: bool = False,
         mcast: str | None = None, subnet_base: str | None = None) -> dict[str, Any]:
    """Put *name* (running as *pid*) on *segment*: plug, record, configure. Idempotent for a VM already there.
    *subnet_base* (A.B.C) is honoured for a segment's first member; later ones follow the record."""
    record = prune(load_record(segment))
    if subnet_base and not record["members"]:
        record["subnet"] = subnet_base
    member: dict[str, Any] | None = record["members"].get(name)
    if member and int(member.get("pid") or 0) == pid:
        member["already"] = True
        return dict(member)
    address = next_address(record)
    mac = nic_mac(segment, name)
    if dry_run:
        print(f"  would hot-plug {vm.get('network_device', 'virtio-net-pci')} {mac} on segment {segment} into {name} (pid {pid}), address {address}")
        configure_guest(vm, mac, address, dry_run=True)
        return {"address": address, "mac": mac, "pid": pid, "interface": None, "already": False}
    plugged = hotplug(vm, name, pid, segment, mac, mcast)
    member = {"address": address, "pid": pid, "interface": None, "configured": False, **plugged}
    record["members"][name] = member
    save_record(record)  # recorded before the SSH step: a failed guest step still leaves a NIC to unlink
    try:
        member["interface"] = configure_guest(vm, mac, address)
        member["configured"] = member["interface"] is not None
    except (VMError, subprocess.TimeoutExpired) as exc:
        member["error"] = str(exc)
    save_record(record)
    member["already"] = False
    return member


def unlink(vm: dict[str, Any], name: str, segment: str) -> bool:
    record = prune(load_record(segment))
    member = record["members"].pop(name, None)
    if member is None:
        return False
    removed = unplug(vm, member)
    if record["members"]:
        save_record(record)
    else:
        record_path(segment).unlink(missing_ok=True)
    return removed


def session_labs() -> list[dict[str, Any]]:
    """The live segments as lab entries for ``vmctl group list --labs``: members, addresses, temporary."""
    entries = []
    for record in all_records():
        members = list(record["members"])
        entries.append({"group": f"link:{record['segment']}", "segment": record["segment"], "members": members,
                        "lab": True, "session": True, "start_order": members,
                        "addresses": {n: [m["address"]] for n, m in record["members"].items()}})
    return entries


def manual_hint(vm: dict[str, Any], address: str) -> str:
    """What to type in a guest vmctl did not configure."""
    family = guest_family(vm)
    if family == "windows":
        return f"in the guest: netsh interface ip set address \"<new adapter>\" static {address.split('/')[0]} 255.255.255.0"
    if family == "freebsd":
        return f"in the guest: ifconfig <new interface> inet {address} up"
    return f"in the guest: ip addr add {address} dev <new interface> && ip link set <new interface> up"

