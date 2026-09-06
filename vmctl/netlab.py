"""Network lab: pfSense router + Pi-hole DNS + a desktop client on an isolated LAN segment.

The topology is declared once, in the ``network_lab`` section of the router profile (role
``pfsense``); the other members carry a ``network_lab`` section with their role, their static IP
and the name of the router profile they sit behind. Every member also lists its NICs in
``networks`` (see :mod:`vmctl.qemu`): the Linux guests install and provision on a slirp NIC
(phase ``install``) and live on the LAN segment afterwards (phase ``runtime``); the router has
WAN (slirp) + LAN (segment) in both phases.

On plain QEMU the segment is a multicast socket shared by the VMs of this host, so the host has
no address on the LAN: the pfSense web GUI, the Pi-hole web UI and SSH to the LAN members go
through port forwards on the router's WAN NIC (``hostfwd`` on the profile + NAT rules in the
generated pfSense config). After ``vmctl export-libvirt`` the segment becomes a libvirt network
with the host on ``lan.host_ip`` and everything is reachable directly, as in kvm-lab.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from vmctl import cloud_init, config, qemu, runtime, ssh, state, ui
from vmctl.errors import VMError

ROLES = ("pfsense", "pihole", "client")
DEFAULT_DOMAIN = "lab"
DEFAULT_BRIDGE = "virbr-lab"
DEFAULT_PIHOLE_WEB_PORT = 80
GUEST_STAGE_DIR = "/tmp/vmctl-netlab"
NETPLAN_FILE = "/etc/netplan/01-vmctl-lab.yaml"
CLOUD_INIT_NETWORK_OFF = "/etc/cloud/cloud.cfg.d/99-vmctl-lab-network.cfg"
PIHOLE_INSTALLER_URL = "https://install.pi-hole.net"


def lab_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    cfg = vm.get("network_lab")
    if cfg is None:
        return None
    if not isinstance(cfg, dict):
        raise VMError("Invalid network_lab: expected object")
    role = str(cfg.get("role") or "")
    if role not in ROLES:
        raise VMError(f"network_lab.role must be one of: {', '.join(ROLES)}")
    return cfg


def netlab_artifact_dir(vm: dict[str, Any]) -> Path:
    return runtime.resolve_path(vm["disk"]["path"]).parent / "netlab"


def _ip(text: Any, what: str) -> ipaddress.IPv4Address:
    try:
        return ipaddress.IPv4Address(str(text))
    except ValueError as exc:
        raise VMError(f"network_lab: {what} {text!r} is not an IPv4 address") from exc


def router_name(cfg: dict[str, Any], vm_name: str) -> str:
    """Profile name of the pfSense router that *vm_name* belongs to (itself for the router)."""
    vm = config.get_vm(cfg, vm_name)
    lab = lab_config(vm)
    if lab is None:
        raise VMError(f"VM '{vm_name}' does not define network_lab")
    if lab["role"] == "pfsense":
        return vm_name
    gateway = str(lab.get("gateway_vm") or "").strip()
    if not gateway:
        raise VMError(f"{vm_name}: network_lab.gateway_vm (the pfsense profile) is required for role {lab['role']}")
    if gateway not in cfg["vms"]:
        raise VMError(f"{vm_name}: network_lab.gateway_vm '{gateway}' is not a known profile")
    return gateway


def topology(cfg: dict[str, Any], vm_name: str) -> dict[str, Any]:
    """The resolved lab that *vm_name* belongs to: LAN, router, DNS, members, port forwards.

    Validation is strict: every address inside the subnet, no duplicates, the DHCP pool clear of
    the static members, one Pi-hole at most, and the router profile forwarding every port the
    generated pfSense NAT rules rely on.
    """
    router = router_name(cfg, vm_name)
    router_vm = config.get_vm(cfg, router)
    router_lab = lab_config(router_vm)
    if router_lab is None or router_lab["role"] != "pfsense":
        raise VMError(f"'{router}' is not a pfsense network_lab profile")
    lan_raw = router_lab.get("lan")
    if not isinstance(lan_raw, dict):
        raise VMError(f"{router}: network_lab.lan is required on the pfsense profile")
    try:
        subnet = ipaddress.IPv4Network(str(lan_raw.get("subnet") or ""), strict=True)
    except ValueError as exc:
        raise VMError(f"{router}: network_lab.lan.subnet must be an IPv4 network like 192.168.0.0/24") from exc
    segment = str(lan_raw.get("name") or "").strip()
    if not segment:
        raise VMError(f"{router}: network_lab.lan.name (the segment name) is required")
    lan: dict[str, Any] = {
        "name": segment,
        "subnet": str(subnet),
        "prefix": subnet.prefixlen,
        "netmask": str(subnet.netmask),
        "host_ip": str(_ip(lan_raw.get("host_ip"), "lan.host_ip")),
        "bridge": str(lan_raw.get("bridge") or DEFAULT_BRIDGE),
        "domain": str(lan_raw.get("domain") or DEFAULT_DOMAIN),
    }
    gateway_ip = _ip(router_lab.get("ip"), "ip")
    dhcp_raw = router_lab.get("dhcp") or {}
    if not isinstance(dhcp_raw, dict):
        raise VMError(f"{router}: network_lab.dhcp must be an object")
    dhcp = {
        "enabled": bool(dhcp_raw.get("enabled", False)),
        "start": str(_ip(dhcp_raw.get("start", subnet.network_address + 150), "dhcp.start")),
        "end": str(_ip(dhcp_raw.get("end", subnet.network_address + 199), "dhcp.end")),
        "lease_hours": int(dhcp_raw.get("lease_hours", 24)),
    }

    members: list[dict[str, Any]] = []
    for name, vm in sorted(cfg["vms"].items()):
        lab = lab_config(vm)
        if lab is None or lab["role"] == "pfsense":
            continue
        if router_name(cfg, name) != router:  # validates the member's gateway_vm even when it is another lab
            continue
        ssh_cfg = cloud_init.ssh_access_config(vm) or {}
        member = {
            "name": name, "role": lab["role"], "ip": str(_ip(lab.get("ip"), f"{name} ip")),
            "hostname": str(lab.get("hostname") or name),
            "ssh_port": int(ssh_cfg["ssh_host_port"]) if ssh_cfg.get("ssh_host_port") else None,
            "web_host_port": int(lab["web_host_port"]) if lab.get("web_host_port") else None,
        }
        if lab["role"] == "pihole":
            member["upstream_dns"] = [str(x) for x in (lab.get("upstream_dns") or ["9.9.9.9", "149.112.112.112"])]
            member["hosts"] = [str(x) for x in (lab.get("hosts") or [])]
            member["cnames"] = [str(x) for x in (lab.get("cnames") or [])]
            member["adlists"] = [str(x) for x in (lab.get("adlists") or ["https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts"])]
            member["password"] = str(lab.get("password") or "lab")
        members.append(member)
    piholes = [m for m in members if m["role"] == "pihole"]
    if len(piholes) > 1:
        raise VMError(f"{router}: more than one pihole member ({', '.join(m['name'] for m in piholes)})")
    dns_ip = piholes[0]["ip"] if piholes else str(gateway_ip)

    addresses: dict[str, str] = {lan["host_ip"]: "lan.host_ip", str(gateway_ip): router}
    for member in members:
        if member["ip"] in addresses:
            raise VMError(f"network_lab: {member['name']} and {addresses[member['ip']]} share the address {member['ip']}")
        addresses[member["ip"]] = member["name"]
    for address, owner in addresses.items():
        ip = ipaddress.IPv4Address(address)
        if ip not in subnet or ip in (subnet.network_address, subnet.broadcast_address):
            raise VMError(f"network_lab: {owner} address {address} is not usable inside {subnet}")
    low, high = ipaddress.IPv4Address(dhcp["start"]), ipaddress.IPv4Address(dhcp["end"])
    if low > high or low not in subnet or high not in subnet:
        raise VMError(f"network_lab: DHCP pool {dhcp['start']}-{dhcp['end']} is not inside {subnet}")
    for address, owner in addresses.items():
        if low <= ipaddress.IPv4Address(address) <= high:
            raise VMError(f"network_lab: the DHCP pool {dhcp['start']}-{dhcp['end']} overlaps the static address of {owner}")

    forwards: list[dict[str, Any]] = []
    for member in members:
        if member["ssh_port"]:
            forwards.append({"wan_port": member["ssh_port"], "target": member["ip"], "target_port": 22,
                             "descr": f"vmctl: SSH to {member['name']}"})
        if member["role"] == "pihole" and member["web_host_port"]:
            forwards.append({"wan_port": member["web_host_port"], "target": member["ip"], "target_port": DEFAULT_PIHOLE_WEB_PORT,
                             "descr": f"vmctl: Pi-hole web UI on {member['name']}"})
    wan_forwards = {fwd["host_port"]: fwd["guest_port"] for spec in qemu.network_specs(router_vm, "runtime")
                    if spec["type"] == "user" for fwd in spec["hostfwd"]}
    seen_ports: dict[int, str] = {}
    for fwd in forwards:
        port = int(fwd["wan_port"])
        if port in seen_ports:
            raise VMError(f"network_lab: WAN port {port} is used by both {seen_ports[port]} and {fwd['descr']}")
        seen_ports[port] = str(fwd["descr"])
        if wan_forwards.get(port) != port:
            raise VMError(
                f"{router}: networks[].hostfwd must forward host port {port} to guest port {port} "
                f"({fwd['descr']}); the pfSense NAT rule relies on it"
            )
    router_ssh = cloud_init.ssh_access_config(router_vm) or {}
    gui_ports = {fwd["host_port"]: fwd["guest_port"] for spec in qemu.network_specs(router_vm, "runtime")
                 if spec["type"] == "user" for fwd in spec["hostfwd"] if fwd["guest_port"] in (80, 443)}
    return {
        "router": router, "router_ip": str(gateway_ip), "router_hostname": str(router_lab.get("hostname") or "firewall"),
        "router_ssh_port": int(router_ssh["ssh_host_port"]) if router_ssh.get("ssh_host_port") else None,
        "router_gui": gui_ports,
        "lan": lan, "dns_ip": dns_ip, "dhcp": dhcp, "members": members, "forwards": forwards,
    }


def member_of(top: dict[str, Any], vm_name: str) -> dict[str, Any]:
    for member in top["members"]:
        if member["name"] == vm_name:
            return dict(member)
    raise VMError(f"'{vm_name}' is not a member of the lab behind {top['router']}")


def lab_vm_names(cfg: dict[str, Any], router: str) -> list[str]:
    """Router first, then the Pi-hole, then the clients: the order installs and starts follow."""
    top = topology(cfg, router)
    ordered = [router] + [m["name"] for m in top["members"] if m["role"] == "pihole"]
    return ordered + [m["name"] for m in top["members"] if m["role"] == "client"]


def routers(cfg: dict[str, Any]) -> list[str]:
    return sorted(name for name, vm in cfg["vms"].items() if (lab_config(vm) or {}).get("role") == "pfsense")


# --- libvirt side -----------------------------------------------------------------------

def segment_network_xml(top: dict[str, Any]) -> str:
    """libvirt network for the LAN segment: a plain bridge with the host address, no NAT/DHCP/DNS."""
    lan = top["lan"]
    return (
        "<network>\n"
        f"  <name>{lan['name']}</name>\n"
        f"  <bridge name=\"{lan['bridge']}\" stp=\"on\" delay=\"0\"/>\n"
        "  <dns enable=\"no\"/>\n"
        f"  <ip address=\"{lan['host_ip']}\" prefix=\"{lan['prefix']}\"/>\n"
        "</network>\n"
    )


# --- guest side (Linux members) ----------------------------------------------------------

def runtime_mac(vm: dict[str, Any]) -> str:
    """MAC of the member's LAN NIC (the same NIC carries the install-phase slirp network)."""
    specs = [spec for spec in qemu.network_specs(vm, "runtime") if spec["type"] == "segment"]
    if len(specs) != 1:
        raise VMError("a Linux lab member needs exactly one segment NIC in its runtime networks")
    return str(specs[0]["mac"])


def netplan_yaml(top: dict[str, Any], member: dict[str, Any], mac: str, ifname: str) -> str:
    renderer = "NetworkManager" if member["role"] == "client" else "networkd"
    lan = top["lan"]
    return (
        "network:\n"
        "  version: 2\n"
        f"  renderer: {renderer}\n"
        "  ethernets:\n"
        f"    {ifname}:\n"
        "      match:\n"
        f"        macaddress: \"{mac}\"\n"
        f"      set-name: {ifname}\n"
        "      dhcp4: false\n"
        "      dhcp6: false\n"
        "      addresses:\n"
        f"        - {member['ip']}/{lan['prefix']}\n"
        "      routes:\n"
        "        - to: default\n"
        f"          via: {top['router_ip']}\n"
        "      nameservers:\n"
        "        addresses:\n"
        f"          - {top['dns_ip']}\n"
        "        search:\n"
        f"          - {lan['domain']}\n"
    )


def pihole_toml(top: dict[str, Any], member: dict[str, Any], ifname: str) -> str:
    """Pi-hole v6 configuration; a pre-seeded pihole.toml skips every installer dialog."""
    hosts = list(member["hosts"])
    hosts.append(f"{top['router_ip']} {top['router_hostname']}.{top['lan']['domain']}")
    for other in top["members"]:
        hosts.append(f"{other['ip']} {other['hostname']}.{top['lan']['domain']}")
    dhcp = top["dhcp"]
    return (
        "# Rendered by vmctl (network_lab); DHCP is activated only after the move to the LAN.\n"
        "[dns]\n"
        f"upstreams = {json.dumps(member['upstream_dns'])}\n"
        f"hosts = {json.dumps(hosts)}\n"
        f"cnameRecords = {json.dumps(list(member['cnames']))}\n"
        f"interface = \"{ifname}\"\n"
        "listeningMode = \"LOCAL\"\n"
        "domainNeeded = true\n"
        "bogusPriv = true\n"
        "queryLogging = true\n"
        "[dns.domain]\n"
        f"name = \"{top['lan']['domain']}\"\n"
        "[dns.cache]\n"
        "size = 10000\n"
        "[dhcp]\n"
        "active = false\n"
        f"start = \"{dhcp['start']}\"\n"
        f"end = \"{dhcp['end']}\"\n"
        f"router = \"{top['router_ip']}\"\n"
        f"netmask = \"{top['lan']['netmask']}\"\n"
        f"leaseTime = \"{dhcp['lease_hours']}h\"\n"
        "ipv6 = false\n"
        "[misc]\n"
        "privacylevel = 0\n"
    )


def guest_setup_script(top: dict[str, Any], member: dict[str, Any]) -> str:
    """Runs as root in the member over SSH, on the install-phase network, right before the stop.

    The static LAN address is only *written* here (netplan applies it at the next boot): applying
    it now would cut the SSH session we are on. cloud-init is told to leave the network alone.
    """
    lines = [
        "#!/bin/bash",
        "set -Eeuo pipefail",
        f"cd {GUEST_STAGE_DIR}",
        "export DEBIAN_FRONTEND=noninteractive",
    ]
    if member["role"] == "pihole":
        lines += [
            "echo '==> netlab: installing Pi-hole (unattended, pre-seeded pihole.toml)'",
            "install -d -m 0755 /etc/pihole",
            "install -m 0644 pihole.toml /etc/pihole/pihole.toml",
            "install -m 0644 adlists.list /etc/pihole/adlists.list",
            f"curl --fail --location --retry 3 {PIHOLE_INSTALLER_URL} -o basic-install.sh",
            "bash basic-install.sh --unattended",
            "pihole setpassword \"$(cat web-password)\"",
            "systemctl is-active --quiet pihole-FTL",
            "pihole version",
        ]
    lines += [
        "echo '==> netlab: writing the static LAN configuration (applied at the next boot)'",
        "rm -f /etc/netplan/*.yaml",
        f"install -m 0600 lan.yaml {NETPLAN_FILE}",
        f"printf 'network: {{config: disabled}}\\n' > {CLOUD_INIT_NETWORK_OFF}",
    ]
    if member["role"] == "pihole":
        lines.append(f"pihole-FTL --config dhcp.active {str(bool(top['dhcp']['enabled'])).lower()}")
    else:
        # The desktop renderer is NetworkManager: networkd has no link left to manage, but the
        # wait-online unit inherited from Ubuntu Server would stall every boot for 120 s.
        lines.append("systemctl disable systemd-networkd-wait-online.service systemd-networkd.service systemd-networkd.socket")
    lines.append("echo '==> netlab: guest configured'")
    return "\n".join(lines) + "\n"


def detect_interface(vm: dict[str, Any], mac: str, dry_run: bool = False) -> str:
    """Name the guest gave to the NIC with *mac* (the same slot in both phases keeps it stable)."""
    if dry_run:
        return "enp0s2"
    command = f"ip -o link | awk -F': ' '/{mac}/ {{print $2; exit}}'"
    name = runtime.run_output(ssh.ssh_base_cmd(vm) + [command]).strip()
    if not name:
        raise VMError(f"Could not find the guest interface with MAC {mac}")
    return name


def render_member_files(vm_name: str, vm: dict[str, Any], top: dict[str, Any], ifname: str, dry_run: bool = False) -> Path:
    member = member_of(top, vm_name)
    mac = runtime_mac(vm)
    directory = netlab_artifact_dir(vm)
    files = {"lan.yaml": netplan_yaml(top, member, mac, ifname), "setup.sh": guest_setup_script(top, member)}
    if member["role"] == "pihole":
        files["pihole.toml"] = pihole_toml(top, member, ifname)
        files["adlists.list"] = "\n".join(member["adlists"]) + "\n"
        files["web-password"] = member["password"] + "\n"
    if not dry_run:
        directory.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            path = directory / name
            path.write_text(content, encoding="utf-8")
            path.chmod(0o600 if name == "web-password" else 0o644)
    return directory


def provision_guest(vm_name: str, vm: dict[str, Any], dry_run: bool = False,
                    stdout_log: Path | None = None, stderr_log: Path | None = None) -> None:
    """Post-install hook for Linux members: Pi-hole install (if any) + static LAN address for the next boot."""
    lab = lab_config(vm)
    if lab is None or lab["role"] == "pfsense":
        return
    top = topology(config.load_config(), vm_name)
    mac = runtime_mac(vm)
    ui.print_note(f"Network lab: configuring {vm_name} as {lab['role']} ({member_of(top, vm_name)['ip']} on {top['lan']['name']})")
    ifname = detect_interface(vm, mac, dry_run=dry_run)
    directory = render_member_files(vm_name, vm, top, ifname, dry_run=dry_run)
    # scp -r onto a path that does not exist yet copies the directory *as* that path.
    ssh.post_install_run(vm, f"rm -rf {GUEST_STAGE_DIR}", dry_run=dry_run, stdout_log=stdout_log, stderr_log=stderr_log)
    ssh.post_install_copy_raw(vm, {"source": str(directory), "dest": GUEST_STAGE_DIR}, dry_run=dry_run, stdout_log=stdout_log, stderr_log=stderr_log)
    ssh.post_install_run(vm, f"sudo bash {GUEST_STAGE_DIR}/setup.sh", dry_run=dry_run, stdout_log=stdout_log, stderr_log=stderr_log)
    ssh.post_install_run(vm, f"rm -rf {GUEST_STAGE_DIR}", dry_run=dry_run, stdout_log=stdout_log, stderr_log=stderr_log)


# --- vmctl lab ---------------------------------------------------------------------------

def describe(top: dict[str, Any]) -> list[str]:
    lan = top["lan"]
    lines = [
        f"Internet -> slirp WAN -> {top['router']} ({top['router_ip']}) -> segment {lan['name']} ({lan['subnet']}) -> members",
        f"gateway {top['router_ip']}, DNS {top['dns_ip']}, domain {lan['domain']}, host on libvirt {lan['host_ip']} (bridge {lan['bridge']})",
        f"DHCP (Pi-hole): {'on' if top['dhcp']['enabled'] else 'off'}, pool {top['dhcp']['start']}-{top['dhcp']['end']}",
    ]
    for member in top["members"]:
        extra = f", SSH 127.0.0.1:{member['ssh_port']} through the router" if member["ssh_port"] else ""
        lines.append(f"  {member['name']:<18} {member['role']:<7} {member['ip']}{extra}")
    for host_port, guest_port in sorted(top["router_gui"].items()):
        scheme = "https" if guest_port == 443 else "http"
        lines.append(f"pfSense web GUI: {scheme}://127.0.0.1:{host_port}/")
    for fwd in top["forwards"]:
        lines.append(f"forward 127.0.0.1:{fwd['wan_port']} -> {fwd['target']}:{fwd['target_port']}  ({fwd['descr']})")
    return lines


def resolve_router(cfg: dict[str, Any], requested: str | None) -> str:
    known = routers(cfg)
    if requested:
        return router_name(cfg, requested)
    if len(known) == 1:
        return known[0]
    if not known:
        raise VMError("No network_lab router profile (role pfsense) is defined")
    raise VMError(f"Several labs are defined ({', '.join(known)}): name one of them")


def attach_snippet(cfg: dict[str, Any], router: str, vm_name: str) -> dict[str, Any]:
    """local.json override that puts *vm_name* on the lab LAN (a plain client, DHCP from Pi-hole)."""
    top = topology(cfg, router)
    vm = config.get_vm(cfg, vm_name)
    if lab_config(vm) is not None:
        raise VMError(f"'{vm_name}' is already part of a network lab")
    return {"vms": {vm_name: {"networks": [{"type": "segment", "name": top["lan"]["name"]}]}}}


def write_local_override(snippet: dict[str, Any], dry_run: bool = False) -> Path:
    path = state.CONFIG_DIR / "profiles" / "local.json"
    current: dict[str, Any] = {"vms": {}}
    if path.is_file():
        current = runtime.load_json_file(path)
        current.setdefault("vms", {})
    for name, override in snippet["vms"].items():
        current["vms"][name] = config.merge_vm_profile(current["vms"].get(name, {}), override)
    if dry_run:
        ui.print_note(f"Would merge into {ui.pretty_path(path)}:")
        print(json.dumps(snippet, indent=2))
        return path
    if path.is_file():
        backup = path.with_suffix(".json.bak")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        ui.print_note(f"Previous local.json saved as {ui.pretty_path(backup)}")
    path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    return path


def shell_hint(top: dict[str, Any], member: dict[str, Any]) -> str:
    if member["ssh_port"]:
        return f"vmctl shell {member['name']} (127.0.0.1:{member['ssh_port']} forwarded by {top['router']})"
    return f"{member['name']}: no SSH port"


# --- reachability checks -------------------------------------------------------------------

def probe_http(url: str, timeout: float = 3.0) -> int | None:
    """HTTP status of *url* (redirects followed), None when nothing answers."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - lab-local URLs
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def probe_tcp(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_targets(top: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    """What must answer once the lab is up: the GUIs and the SSH ports, from the host's point of view.

    ``qemu``: everything goes through the router's WAN forwards on 127.0.0.1. ``libvirt``: the host
    sits on the LAN (``lan.host_ip``) and reaches the real addresses.
    """
    targets: list[dict[str, Any]] = []
    if mode == "qemu":
        for host_port, guest_port in sorted(top["router_gui"].items()):
            scheme = "https" if guest_port == 443 else "http"
            targets.append({"label": f"{top['router']} web GUI", "kind": "http", "url": f"{scheme}://127.0.0.1:{host_port}/"})
        if top["router_ssh_port"]:
            targets.append({"label": f"{top['router']} SSH", "kind": "tcp", "host": "127.0.0.1", "port": top["router_ssh_port"]})
        for fwd in top["forwards"]:
            if fwd["target_port"] == DEFAULT_PIHOLE_WEB_PORT:
                targets.append({"label": fwd["descr"], "kind": "http", "url": f"http://127.0.0.1:{fwd['wan_port']}/admin/"})
            else:
                targets.append({"label": fwd["descr"], "kind": "tcp", "host": "127.0.0.1", "port": int(fwd["wan_port"])})
        return targets
    if mode != "libvirt":
        raise VMError(f"Unknown check mode: {mode}")
    targets.append({"label": f"{top['router']} web GUI", "kind": "http", "url": f"http://{top['router_ip']}/"})
    targets.append({"label": f"{top['router']} SSH", "kind": "tcp", "host": top["router_ip"], "port": 22})
    for member in top["members"]:
        if member["role"] == "pihole":
            targets.append({"label": f"Pi-hole web UI on {member['name']}", "kind": "http", "url": f"http://{member['ip']}/admin/"})
        targets.append({"label": f"SSH to {member['name']}", "kind": "tcp", "host": member["ip"], "port": 22})
    return targets


def wait_targets(targets: list[dict[str, Any]], timeout_sec: int, sleep: float = 3.0) -> list[tuple[dict[str, Any], bool, str]]:
    """Poll every target until it answers or *timeout_sec* elapses; returns (target, ok, detail) per target."""
    deadline = time.monotonic() + timeout_sec
    pending = list(targets)
    results: dict[int, tuple[bool, str]] = {}
    while pending:
        for target in list(pending):
            if target["kind"] == "http":
                status = probe_http(str(target["url"]))
                if status is not None:
                    results[id(target)] = (status < 500, f"HTTP {status}")
                    pending.remove(target)
            else:
                if probe_tcp(str(target["host"]), int(target["port"])):
                    results[id(target)] = (True, "port open")
                    pending.remove(target)
        if not pending or time.monotonic() >= deadline:
            break
        time.sleep(sleep)
    out: list[tuple[dict[str, Any], bool, str]] = []
    for target in targets:
        ok, detail = results.get(id(target), (False, "no answer"))
        out.append((target, ok, detail))
    return out
