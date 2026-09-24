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
from datetime import datetime
from pathlib import Path
from typing import Any

from vmctl import cloud_init, config, netlab, proxmox, pvecluster, qemu, runtime
from vmctl.errors import VMError

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
    """Declared groups whose members are all on a segment: the labs, in name order."""
    groups = sorted({group for _, vm in config.sorted_vm_items(cfg) for group in config.declared_groups(vm)})
    # Every member: a category such as ``ubuntu`` holds one lab client and is still no lab.
    return [group for group in groups
            if all(has_segment(config.get_vm(cfg, name)) for name in group_members(cfg, group))]


def start_order(cfg: dict[str, Any], names: list[str]) -> list[str]:
    """Infrastructure (router, hypervisor), then services (DNS), then the rest, each in catalog order."""
    def rank(name: str) -> int:
        role = str(config.get_vm(cfg, name).get("meta", {}).get("role") or "")
        return 0 if role in INFRA_ROLES else 1 if role in SERVICE_ROLES else 2
    return sorted(names, key=lambda name: (rank(name), names.index(name)))


def _netlab_addresses(cfg: dict[str, Any], names: list[str]) -> tuple[dict[str, str], dict[int, str]]:
    """Segment addresses and WAN forward descriptions of the network lab, from its topology."""
    addresses: dict[str, str] = {}
    forwards: dict[int, str] = {}
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
            forwards[int(fwd["wan_port"])] = f"{fwd['target']}:{fwd['target_port']} ({fwd['descr'].removeprefix('vmctl: ')})"
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
                    what = lab_forwards.get(int(fwd["host_port"]), "")
                    forwards.append({"host_port": fwd["host_port"], "guest": str(fwd["guest_port"]), "what": what})
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
            "zfs": _zfs(vm),
            # Guests of a hypervisor member (Proxmox containers) that the lab reaches on the segment.
            "services": [dict(entry) for entry in vm.get("lab_services") or [] if isinstance(entry, dict)],
        })
    lab = {"group": group, "members": members, "segments": list(segments.values()),
           "start_order": [member["name"] for member in members]}
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


def runbook(cfg: dict[str, Any], lab: dict[str, Any]) -> list[dict[str, Any]]:
    """The commands behind the lab, from the same profiles: each section is a title, a sentence
    and blocks of (where, commands); ``where`` is ``host`` or a member (run over its SSH line)."""
    group = lab["group"]
    members = lab["members"]
    ssh_of = {member["name"]: member["ssh"] for member in members}
    sections: list[dict[str, Any]] = []
    install = [f"vmctl group install {group}    # everything missing, in this order, then up"]
    install += [f"vmctl {member['flow']} {member['name']}" for member in members
                if member["flow"].startswith("bootstrap-")]
    sections.append({"title": "Install", "text": "One command for the whole lab (cumulative: installed members are kept), or one per member with its own unattended flow.",
                     "blocks": [("host", install)]})
    mirrors = [member for member in members if member["zfs"]]
    if mirrors:
        first = mirrors[0]["zfs"]
        answer = ["[disk-setup]", 'filesystem = "zfs"', f'disk-list = [{", ".join(repr(d).replace(chr(39), chr(34)) for d in first["disks"])}]',
                  f'zfs.raid = "{first["raid"]}"']
        blocks: list[tuple[str, list[str]]] = [("answer.toml (rendered by vmctl, grafted on the ISO)", answer)]
        for member in mirrors:
            blocks.append((member["name"], ["zpool status rpool", "zpool list -v rpool",
                                             "proxmox-boot-tool status    # one ESP per disk: boots from either"]))
        sections.append({"title": "ZFS mirror", "text": f"The root pool is ZFS {first['raid']} over {' + '.join(first['disks'])} (disk + extra_disks); these commands show it on each node.",
                         "blocks": blocks})
    containers: list[tuple[str, list[str]]] = []
    for member in members:
        for svc in member["services"]:
            nat = str(svc.get("nat_address") or "")
            upstream = (f"TERM=xterm mode=default var_ctid={svc.get('container')} var_hostname={svc.get('hostname')} "
                        f"var_brg=vmbr0 var_net={nat} var_gateway=10.0.2.2 var_ns=10.0.2.3 "
                        "var_container_storage=local-zfs var_template_storage=local \
  "
                        f'bash -c "$(curl -fsSL {COMMUNITY_URL.format(script=svc.get("script"))})"')
            containers.append((member["name"], [
                f"# {svc.get('name')}: what vmctl runs",
                f"/root/pve-community.sh {svc.get('container')} {svc.get('script')} {svc.get('hostname')} "
                f"{svc.get('port')} {nat} {svc.get('address')}",
                "# the same by hand, upstream script",
                "mkdir -p /usr/local/community-scripts && echo DIAGNOSTICS=no > /usr/local/community-scripts/diagnostics",
                upstream,
                f"pct set {svc.get('container')} -onboot 1 -net1 name=eth1,bridge=vmbr1,ip={svc.get('address')}",
                f"# then: {svc.get('url')}",
            ]))
    if containers:
        sections.append({"title": "LXC containers", "text": "Created by the Proxmox VE Helper-Scripts (community-scripts.org, main branch, not pinned) on the NAT bridge at a static address outside slirp's DHCP pool, then given a NIC on the lab segment.",
                         "blocks": containers})
    names = [member["name"] for member in members]
    for cluster, entry in pvecluster.clusters(cfg, names).items():
        steps = pvecluster.commands(cfg, cluster, entry)
        blocks = []
        for node, command in steps:
            if blocks and blocks[-1][0] == node:
                blocks[-1][1].append(command)
            else:
                blocks.append((node, [command]))
        sections.append({"title": f"Cluster {cluster}", "text": f"corosync over the lab segment; {entry['primary']} creates the cluster, the others join it. vmctl group install {group} does all of this and checks quorum.",
                         "blocks": blocks})
    sections.append({"title": "Run the stack", "text": "Infrastructure starts first and stops last.",
                     "blocks": [("host", [f"vmctl group up {group}", f"vmctl group status {group}",
                                          f"vmctl group map {group} --open", f"vmctl group down {group}",
                                          f"vmctl group clean {group}    # asks, keeps checkpoints"])]})
    for section in sections:
        section["blocks"] = [{"where": where, "ssh": ssh_of.get(where, ""), "commands": list(cmds)}
                             for where, cmds in section["blocks"]]
    return sections


# --- the map ---------------------------------------------------------------------------------

_BOX_W, _BOX_H, _GAP, _MARGIN = 230, 118, 40, 40
_HOST_Y, _BOX_Y = 40, 200


def _svg(lab: dict[str, Any]) -> str:
    members = lab["members"]
    count = max(1, len(members))
    box_h = _BOX_H + 20 * max((len(member["services"]) for member in members), default=0)
    width = max(760, 2 * _MARGIN + count * _BOX_W + (count - 1) * _GAP)
    seg_top = _BOX_Y + box_h + 90
    height = seg_top + 70 * max(1, len(lab["segments"])) + 10
    x_of = {m["name"]: _MARGIN + index * (_BOX_W + _GAP) for index, m in enumerate(members)}
    seg_y = {seg["name"]: seg_top + 70 * index for index, seg in enumerate(lab["segments"])}
    esc = html.escape
    out: list[str] = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Network map of {esc(lab["group"])}">']
    # Host / Internet bar: every slirp NIC is its own NAT to the host and to the Internet.
    out.append(f'<rect class="host" x="{_MARGIN}" y="{_HOST_Y}" width="{width - 2 * _MARGIN}" height="46" rx="10"/>')
    out.append(f'<text class="host-t" x="{_MARGIN + 16}" y="{_HOST_Y + 28}">Host 127.0.0.1 · Internet</text>')
    for member in members:
        x = x_of[member["name"]]
        cx = x + _BOX_W / 2
        nat = [nic for nic in member["nics"] if nic["type"] == "user"]
        for nic in nat:
            out.append(f'<line class="nat" x1="{cx}" y1="{_HOST_Y + 46}" x2="{cx}" y2="{_BOX_Y}"/>')
            labels = [f'{f["host_port"]} → :{f["guest"]}' + (f' {f["what"]}' if f["what"] else "") for f in nic["forwards"]]
            for line, text in enumerate(labels[:6]):
                out.append(f'<text class="fwd" x="{cx + 8}" y="{_HOST_Y + 70 + 15 * line}">{esc(text)}</text>')
        state = "running" if member["running"] else ("stopped" if member["install"] not in ("", "no disk") else "absent")
        badge = {"running": "● running", "stopped": "○ stopped", "absent": "no disk"}[state]
        out.append(f'<g class="vm {state}"><rect x="{x}" y="{_BOX_Y}" width="{_BOX_W}" height="{box_h}" rx="12"/>')
        out.append(f'<text class="vm-n" x="{x + 14}" y="{_BOX_Y + 28}">{esc(member["name"])}</text>')
        role = member["role"] or "vm"
        ram = f'{member["memory_mb"] / 1024:g} GB' if member["memory_mb"] >= 1024 else f'{member["memory_mb"]} MB'
        disks = f' · {member["disks"]} disks' if member["disks"] > 1 else ""
        out.append(f'<text class="small" x="{x + 14}" y="{_BOX_Y + 50}">{esc(role)} · {ram} · {member["cpus"]} vCPU{disks}</text>')
        out.append(f'<text class="badge {state}" x="{x + 14}" y="{_BOX_Y + 76}">{badge}</text>')
        if member["install"] and state != "absent":
            out.append(f'<text class="small" x="{x + 14}" y="{_BOX_Y + 98}">disk: {esc(member["install"])}</text>')
        for index, service in enumerate(member["services"]):
            where = str(service.get("address") or "").split("/")[0]
            label = f'CT {service.get("container", "?")} {service.get("name", "")} · {where}'
            out.append(f'<text class="svc" x="{x + 14}" y="{_BOX_Y + _BOX_H + 6 + 20 * index}">▣ {esc(label)}</text>')
        out.append('</g>')
        for nic in member["nics"]:
            if nic["type"] != "segment":
                continue
            y = seg_y[nic["segment"]]
            out.append(f'<line class="seg-l" x1="{cx}" y1="{_BOX_Y + box_h}" x2="{cx}" y2="{y}"/>')
            out.append(f'<circle class="port" cx="{cx}" cy="{y}" r="5"/>')
            out.append(f'<text class="addr" x="{cx + 8}" y="{_BOX_Y + box_h + 22}">{esc(nic["address"] or "address not declared")}</text>')
            out.append(f'<text class="small" x="{cx + 8}" y="{_BOX_Y + box_h + 38}">{esc(nic["mac"])}</text>')
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
svg text { fill:var(--fg); font:13px system-ui, sans-serif; }
svg .small { fill:var(--muted); font-size:11.5px; } svg .fwd { fill:var(--accent); font-size:11.5px; }
svg .host { fill:none; stroke:var(--accent); stroke-width:1.5; stroke-dasharray:6 4; }
svg .host-t { font-weight:600; fill:var(--accent); }
svg .nat { stroke:var(--accent); stroke-width:1.5; stroke-dasharray:4 4; }
svg .vm rect { fill:var(--card); stroke:var(--line); stroke-width:1.5; }
svg .vm.running rect { stroke:var(--ok); stroke-width:2.5; }
svg .vm-n { font-weight:700; font-size:15px; }
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
"""


# Guest ports that serve a web page, and the scheme they speak.
WEB_PORTS = {80: "http", 8080: "http", 8081: "http", 443: "https", 8006: "https", 8443: "https"}


def _forward_html(fwd: dict[str, Any]) -> str:
    target = f'127.0.0.1:{fwd["host_port"]} → :{fwd["guest"]}'
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
                    f'<td>{"<br><br>".join(nics)}</td></tr>')
    group = esc(lab["group"])
    order = " → ".join(esc(name) for name in lab["start_order"])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{group} network map</title><style>{_CSS}</style></head>
<body><main>
<h1>{group}</h1>
<p class="sub">{len(lab["members"])} VMs · {running} running · {len(lab["segments"])} segment(s) · generated {when} by <code>vmctl group map {group}</code></p>
<div class="map">{_svg(lab)}</div>
<p class="sub">Every NAT NIC is a private slirp {SLIRP_SUBNET} of its own VM: the guest reaches the Internet, the host reaches it only through the forwards on 127.0.0.1. The segments are shared between the VMs of this host only.</p>
<h2>Members</h2>
<div class="table-wrap"><table><thead><tr><th>VM</th><th>Role</th><th>State</th><th>NICs (runtime)</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<h2>Runbook</h2>
<p class="sub">Start order: {order}. Every command below comes from the same profiles as the map.</p>
{_runbook_html(lab.get("runbook") or [])}
</main></body></html>
"""


def _runbook_html(sections: list[dict[str, Any]]) -> str:
    esc = html.escape
    parts: list[str] = []
    for section in sections:
        parts.append(f'<h3>{esc(section["title"])}</h3><p class="sub">{esc(section["text"])}</p>')
        for block in section["blocks"]:
            where = block["where"]
            if block["ssh"]:
                label = f'on <b>{esc(where)}</b> · <code>{esc(block["ssh"])}</code>'
            elif where == "host":
                label = "on the host, in the repository"
            else:
                label = esc(where)
            parts.append(f'<div class="where">{label}</div><pre>{esc(chr(10).join(block["commands"]))}</pre>')
    return "\n".join(parts)


def map_path(group: str) -> Path:
    return runtime.resolve_path(f"artifacts/labs/{group}/network.html")


def write_map(lab: dict[str, Any], dest: Path | None = None) -> Path:
    path = dest or map_path(lab["group"])
    runtime.ensure_parent(path)
    path.write_text(render_html(lab), encoding="utf-8")
    (path.parent / "lab.json").write_text(json.dumps(lab, indent=2) + "\n", encoding="utf-8")
    return path
