"""``vmctl link``: connect VMs on a private segment, running or stopped, without editing their profiles.

Every VM booted on its own has an isolated slirp network (the same 10.0.2.15 in each guest), so
two running machines cannot talk. A link hot-plugs a NIC into each of them over QMP, on the
multicast socket of a shared segment (the same transport the labs use), gives it a stable MAC
and an address in a private /24 (192.168.100.0/24 for the default segment) configured over SSH where the guest is a Linux or
FreeBSD system vmctl can reach. A stopped VM can be linked too: it is recorded as *pending* and
``vmctl start`` gives it the NIC on its own command line, then sets the address once SSH answers
(``vmctl link --settle``, detached). Nothing is written to the profiles: the record of a segment
lives in ``artifacts/labs/links/<segment>.json`` until ``vmctl link --off``; a member whose QEMU
stopped goes back to pending and rejoins at its next start.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from vmctl import cloud_init, qemu, ssh, state, windows
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


RUNTIME_KEYS = ("pid", "interface", "configured", "netdev", "device", "bus", "error")


def is_up(member: dict[str, Any]) -> bool:
    return bool(member.get("pid")) and qemu_alive(int(member["pid"]))


def prune(record: dict[str, Any]) -> dict[str, Any]:
    """A member whose QEMU is gone goes back to pending (address and MAC kept): it rejoins the
    segment at its next ``vmctl start``. Only ``vmctl link --off`` removes a member."""
    changed = False
    for member in record["members"].values():
        if member.get("pid") and not qemu_alive(int(member["pid"])):
            for key in RUNTIME_KEYS:
                member.pop(key, None)
            changed = True
    if changed:
        save_record(record)
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
            found.append({"segment": record["segment"], "address": me["address"], "up": is_up(me),
                          "peers": [{"name": n, "address": m["address"], "up": is_up(m)}
                                    for n, m in record["members"].items() if n != name]})
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
    qemu.qmp_execute(sock, "netdev_add", arguments={"type": "socket", "id": ident, "localaddr": qemu.MCAST_LOCALADDR,
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
        # A NetworkManager guest (Kali, desktops) grabs the new interface, tries DHCP on it and
        # flushes the address `ip` set once that fails (verified live: eth1 "disconnected", no
        # address, a minute after the link worked). There the address is a manual connection.
        nm = (f"nmcli -t -f DEVICE,STATE dev 2>/dev/null | grep \"^$IF:\" | grep -qv unmanaged")
        return (f"for i in $(seq 1 30); do IF=$(ip -o link | awk '/{mac}/{{sub(\":\",\"\",$2); print $2; exit}}'); "
                f"[ -n \"$IF\" ] && break; sleep 1; done; [ -n \"$IF\" ] || {{ echo 'no interface with {mac}' >&2; exit 3; }}; "
                f"if command -v nmcli >/dev/null 2>&1 && {nm}; then "
                f"nmcli con delete \"vmctl-link-$IF\" >/dev/null 2>&1; "
                f"nmcli con add type ethernet ifname \"$IF\" con-name \"vmctl-link-$IF\" ipv4.method manual ipv4.addresses {address} "
                f"ipv6.method disabled autoconnect yes >/dev/null && nmcli con up \"vmctl-link-$IF\" >/dev/null; "
                f"else ip link set \"$IF\" up && ip addr replace {address} dev \"$IF\"; fi && {networkd_unmanaged(mac)}echo \"$IF\"")
    return None


def networkd_unmanaged(mac: str) -> str:
    """Shell that tells a running systemd-networkd to leave the link NIC alone (a .network by MAC
    with Unmanaged=yes, kept for the next boots). Without it, a catch-all .network (Ubuntu 26.04's
    initramfs leaves dracut's `DHCP=yes` on every link in /run/systemd/network) runs DHCP on the
    segment, where no server answers, and systemd-networkd-wait-online holds graphical.target
    for its 120 s: a black screen for two minutes at every boot of a linked desktop (2026-10-06)."""
    unit = f"[Match]\\nMACAddress={mac}\\n\\n[Link]\\nUnmanaged=yes\\n"
    return (f"if command -v networkctl >/dev/null 2>&1 && systemctl is-active -q systemd-networkd 2>/dev/null; then "
            f"mkdir -p /etc/systemd/network && printf '{unit}' > \"/etc/systemd/network/10-vmctl-link-$IF.network\" "
            f"&& networkctl reload >/dev/null 2>&1; fi; ")


def configures_windows(vm: dict[str, Any]) -> bool:
    """Windows 10/11 from bootstrap-windows: OpenSSH as the local administrator, PowerShell 5.
    Windows 7 and the retro versions have neither, so they keep the manual hint."""
    cfg = vm.get("windows_config")
    return isinstance(cfg, dict) and not windows.is_legacy_windows(cfg)


def windows_script(mac: str, address: str) -> str:
    """PowerShell that gives the adapter with *mac* a static *address* and lets ping in: Windows
    otherwise leaves the new adapter on an APIPA 169.254 address, on the Public profile, where
    inbound ICMP is blocked (verified live on windows-11). Idempotent: the adapter's previous
    IPv4 addresses and the firewall rule are replaced."""
    ip, _, prefix = address.partition("/")
    win_mac = mac.upper().replace(":", "-")
    return (
        "$ErrorActionPreference = 'Stop'; $a = $null; "
        f"for ($i = 0; $i -lt 30 -and -not $a; $i++) {{ $a = Get-NetAdapter | Where-Object MacAddress -eq '{win_mac}'; "
        "if (-not $a) { Start-Sleep 1 } }; "
        f"if (-not $a) {{ [Console]::Error.WriteLine('no interface with {mac}'); exit 3 }}; "
        "Set-NetIPInterface -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -Dhcp Disabled; "
        "Get-NetIPAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
        "Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue; "
        f"New-NetIPAddress -InterfaceIndex $a.ifIndex -IPAddress {ip} -PrefixLength {prefix or 24} | Out-Null; "
        "Set-NetConnectionProfile -InterfaceIndex $a.ifIndex -NetworkCategory Private -ErrorAction SilentlyContinue; "
        "Remove-NetFirewallRule -Name vmctl-link-icmp -ErrorAction SilentlyContinue; "
        "New-NetFirewallRule -Name vmctl-link-icmp -DisplayName 'vmctl link: ping' -Protocol ICMPv4 -IcmpType 8 "
        "-Direction Inbound -Action Allow | Out-Null; "
        "Write-Output $a.Name"
    )


def windows_command(vm: dict[str, Any], script: str, dry_run: bool = False) -> list[str]:
    """The script as one -EncodedCommand (UTF-16LE base64): nothing for cmd.exe, the OpenSSH
    default shell, to re-quote."""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return ssh.ssh_base_cmd(vm, dry_run=dry_run) + [f"powershell -NoProfile -NonInteractive -EncodedCommand {encoded}"]


def configure_guest(vm: dict[str, Any], mac: str, address: str, dry_run: bool = False) -> str | None:
    """Give the hot-plugged interface its address over SSH. Returns the interface name, or None when
    the guest is not one vmctl configures (Windows 7 and older, hobby systems, no SSH): the caller
    then prints the address to set by hand."""
    if cloud_init.ssh_access_config(vm) is None:
        return None
    if configures_windows(vm):
        command = windows_command(vm, windows_script(mac, address), dry_run=dry_run)
    else:
        script = guest_script(guest_family(vm), mac, address)
        if script is None:
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


def link(vm: dict[str, Any], name: str, pid: int | None, segment: str = DEFAULT_SEGMENT, dry_run: bool = False,
         mcast: str | None = None, subnet_base: str | None = None) -> dict[str, Any]:
    """Put *name* on *segment*: running as *pid*, plug, record, configure; stopped (``pid`` None),
    record it as pending for its next start. Idempotent for a VM already there. *subnet_base* (A.B.C)
    and *mcast* are honoured for a segment's first member; later ones follow the record."""
    record = prune(load_record(segment))
    if not record["members"]:
        if subnet_base:
            record["subnet"] = subnet_base
        if mcast:
            record["mcast"] = mcast
    member: dict[str, Any] | None = record["members"].get(name)
    if member and (pid is None or int(member.get("pid") or 0) == pid):
        return {**member, "already": True, "pending": not is_up(member)}
    address = member["address"] if member else next_address(record)
    mac = nic_mac(segment, name)
    if dry_run:
        where = f"into {name} (pid {pid})" if pid else f"on {name}'s next start (it is stopped)"
        print(f"  would add {vm.get('network_device', 'virtio-net-pci')} {mac} on segment {segment} {where}, address {address}")
        if pid:
            configure_guest(vm, mac, address, dry_run=True)
        return {"address": address, "mac": mac, "pid": pid, "interface": None, "already": False, "pending": not pid}
    if pid is None:
        record["members"][name] = {"address": address, "mac": mac}
        save_record(record)
        return {"address": address, "mac": mac, "already": False, "pending": True}
    plugged = hotplug(vm, name, pid, segment, mac, record.get("mcast"))
    member = {"address": address, "pid": pid, "interface": None, "configured": False, **plugged}
    record["members"][name] = member
    save_record(record)  # recorded before the SSH step: a failed guest step still leaves a NIC to unlink
    try:
        member["interface"] = configure_guest(vm, mac, address)
        member["configured"] = member["interface"] is not None
    except (VMError, subprocess.TimeoutExpired) as exc:
        member["error"] = str(exc)
    save_record(record)
    return {**member, "already": False, "pending": False}


def boot_args(name: str, vm: dict[str, Any]) -> list[str]:
    """QEMU arguments that put a starting VM on every segment it is a member of: the NICs a pending
    link waits for, on the machine's own bus (no hot-plug needed, any display mode)."""
    device = str(vm.get("network_device", "virtio-net-pci"))
    args: list[str] = []
    for record in all_records():
        member = record["members"].get(name)
        if member is None or is_up(member):
            continue
        ident = netdev_id(str(record["segment"]))
        endpoint = qemu.segment_endpoint(str(record["segment"]), record.get("mcast"))
        args += ["-netdev", qemu.mcast_netdev(ident, endpoint),
                 "-device", f"{device},netdev={ident},id={ident}-nic,mac={member.get('mac') or nic_mac(str(record['segment']), name)}"]
    return args


def pending_segments(name: str) -> list[str]:
    return [str(r["segment"]) for r in all_records() if name in r["members"] and not is_up(r["members"][name])]


def settle(name: str, vm: dict[str, Any], pid: int) -> list[dict[str, Any]]:
    """After a start with boot-time link NICs: record the QEMU as the member and set each address
    (the caller waited for SSH). Returns one result per segment settled."""
    results = []
    for segment in pending_segments(name):
        record = load_record(segment)
        member = record["members"][name]
        ident = netdev_id(segment)
        member.update({"pid": pid, "netdev": ident, "device": f"{ident}-nic", "bus": None,
                       "interface": None, "configured": False, "boot": True})
        member.pop("error", None)
        save_record(record)
        try:
            member["interface"] = configure_guest(vm, str(member.get("mac") or nic_mac(segment, name)), str(member["address"]))
            member["configured"] = member["interface"] is not None
        except (VMError, subprocess.TimeoutExpired) as exc:
            member["error"] = str(exc)
        save_record(record)
        results.append({"segment": segment, **member})
    return results


def unlink(vm: dict[str, Any], name: str, segment: str) -> str:
    """``absent`` (not a member), ``removed`` (NIC unplugged, or a pending member forgotten) or
    ``stays`` (forgotten, but the running QEMU refused to unplug a NIC it booted with: q35's root
    bus is not hot-pluggable, so it leaves with the next stop)."""
    record = prune(load_record(segment))
    member = record["members"].pop(name, None)
    if member is None:
        return "absent"
    status = "removed"
    if is_up(member) and not unplug(vm, member):
        status = "stays"
    if record["members"]:
        save_record(record)
    else:
        record_path(segment).unlink(missing_ok=True)
    return status


def session_labs() -> list[dict[str, Any]]:
    """The live segments as lab entries for ``vmctl group list --labs``: members, addresses, temporary."""
    entries = []
    for record in all_records():
        members = list(record["members"])
        entries.append({"group": f"link:{record['segment']}", "segment": record["segment"], "members": members,
                        "pending": [n for n, m in record["members"].items() if not is_up(m)],
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

