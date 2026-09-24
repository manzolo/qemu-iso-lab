"""Form the Proxmox VE cluster of a lab: ``proxmox_config.cluster`` on every node, corosync on the segment.

Every node was installed alone, and each one's NAT NIC is the same private slirp 10.0.2.15, which is
also what its hostname resolves to after the install. A cluster needs one distinct, reachable
address per node, so the lab address on vmbr1 (``networks[].address`` of the ``segment`` NIC) goes
into ``/etc/hosts`` first (pmxcfs resolves the node name once, at start) and corosync's link0 uses
it. The primary node runs ``pvecm create``; each other node gets its root key authorized on the
primary and the primary's host key known, then runs ``pvecm add --use_ssh``, which asks nothing.
Everything is checked first, so running it again on a formed cluster changes nothing.
"""
from __future__ import annotations

import ipaddress
import re
import shlex
import subprocess
import time
from typing import Any

from vmctl import config, proxmox, ssh, ui
from vmctl.errors import VMError

QUORUM_WAIT_SEC = 180


def cluster_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    cfg = (proxmox.proxmox_config(vm) or {}).get("cluster")
    if cfg is not None and (not isinstance(cfg, dict) or not cfg.get("name") or not cfg.get("primary")):
        raise VMError("proxmox_config.cluster needs name and primary")
    return cfg


def lan_ip(vm: dict[str, Any]) -> str:
    """The node's address on the lab segment, where corosync and the other nodes reach it."""
    for entry in vm.get("networks") or []:
        if isinstance(entry, dict) and entry.get("type") == "segment" and entry.get("address"):
            return str(ipaddress.ip_interface(str(entry["address"])).ip)
    raise VMError("a cluster node needs networks[].address on its segment NIC")


def clusters(cfg: dict[str, Any], names: list[str]) -> dict[str, dict[str, Any]]:
    """Cluster name -> primary node and member nodes (primary first), for the given profiles."""
    found: dict[str, dict[str, Any]] = {}
    for name in names:
        vm = config.get_vm(cfg, name)
        cluster = cluster_config(vm)
        if cluster is None:
            continue
        entry = found.setdefault(str(cluster["name"]), {"primary": str(cluster["primary"]), "nodes": []})
        if entry["primary"] != cluster["primary"]:
            raise VMError(f"cluster {cluster['name']}: nodes disagree on the primary")
        entry["nodes"].append(name)
    for name, entry in found.items():
        if entry["primary"] not in entry["nodes"]:
            raise VMError(f"cluster {name}: primary {entry['primary']} is not one of its nodes")
        entry["nodes"].sort(key=lambda node: node != entry["primary"])
    return found


def commands(cfg: dict[str, Any], cluster: str, entry: dict[str, Any]) -> list[tuple[str, str]]:
    """(node, shell command) for a manual run: what ``form()`` does, for the runbook."""
    primary = entry["primary"]
    primary_ip = lan_ip(config.get_vm(cfg, primary))
    steps: list[tuple[str, str]] = []
    for node in entry["nodes"]:
        ip = lan_ip(config.get_vm(cfg, node))
        steps.append((node, f"sed -i 's/^10\\.0\\.2\\.15\\s/{ip} /' /etc/hosts && systemctl restart pve-cluster pveproxy"))
    steps.append((primary, f"pvecm create {cluster} --link0 {primary_ip}"))
    for node in entry["nodes"][1:]:
        ip = lan_ip(config.get_vm(cfg, node))
        steps.append((node, f"cat /root/.ssh/id_rsa.pub   # append it to {primary}:/root/.ssh/authorized_keys"))
        steps.append((node, f"ssh-keyscan -H {primary_ip} >> /root/.ssh/known_hosts"))
        steps.append((node, f"pvecm add {primary_ip} --link0 {ip} --use_ssh"))
    steps.append((primary, "pvecm status"))
    return steps


SSH_ATTEMPTS, SSH_RETRY_SEC = 6, 5


def _ssh(vm: dict[str, Any], command: str) -> subprocess.CompletedProcess[str]:
    """One remote command; a dropped connection (exit 255) is retried. Right after the stack
    starts, a node that has just answered the SSH probe closed the next connection, twice, on
    two runs (verified live); the command itself never ran, so repeating it is safe."""
    for attempt in range(SSH_ATTEMPTS):
        result = subprocess.run(ssh.remote_shell_cmd(vm, command), capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=600, check=False)
        if result.returncode != 255 or attempt == SSH_ATTEMPTS - 1:
            return result
        time.sleep(SSH_RETRY_SEC)
    raise AssertionError("unreachable")


def _read(vm: dict[str, Any], command: str) -> str:
    result = _ssh(vm, command)
    if result.returncode:
        raise VMError(f"'{command}' failed on {ssh.ssh_target(vm)[0]}:{ssh.ssh_target(vm)[1]}: "
                      f"{(result.stderr or result.stdout).strip()[-400:]}")
    return result.stdout


def _run(vm: dict[str, Any], command: str, dry_run: bool) -> None:
    ui.print_command(ssh.remote_shell_cmd(vm, command, dry_run=dry_run))
    if dry_run:
        return
    result = _ssh(vm, command)
    if result.stdout.strip():
        print(result.stdout.rstrip())
    if result.returncode:
        raise VMError(f"'{command}' failed on {ssh.ssh_target(vm)[0]}:{ssh.ssh_target(vm)[1]} "
                      f"(exit {result.returncode}): {(result.stderr or result.stdout).strip()[-600:]}")


def members_of(vm: dict[str, Any]) -> int:
    """How many nodes the cluster this node belongs to has (0: not in a cluster)."""
    out = _read(vm, "pvecm status 2>/dev/null | awk '/^Nodes:/ {print $2}' || true").strip()
    return int(out) if out.isdigit() else 0


def wait_writable(vm: dict[str, Any], dry_run: bool) -> None:
    """``pvecm create``/``add`` restart pmxcfs, and /etc/pve (root's authorized_keys lives there)
    answers "I/O error" for a few seconds after it (verified live): wait until a write succeeds."""
    if dry_run:
        return
    deadline = time.monotonic() + QUORUM_WAIT_SEC
    while _read(vm, "touch /etc/pve/.vmctl-probe 2>/dev/null && rm -f /etc/pve/.vmctl-probe && echo ok || true").strip() != "ok":
        if time.monotonic() > deadline:
            raise VMError(f"/etc/pve stayed read-only on {ssh.ssh_target(vm)[0]}:{ssh.ssh_target(vm)[1]}")
        time.sleep(3)


def pin_hostname(vm: dict[str, Any], ip: str, dry_run: bool) -> None:
    """Resolve the node name to its lab address (it resolves to the shared slirp 10.0.2.15 after install)."""
    script = (f"if ! grep -q '^{ip}[[:space:]]' /etc/hosts; then "
              f"sed -i 's/^10\\.0\\.2\\.15[[:space:]]/{ip} /' /etc/hosts; "
              "systemctl restart pve-cluster && systemctl restart pvedaemon pveproxy; fi; "
              "getent hosts \"$(hostname)\"")
    _run(vm, script, dry_run)


def form(cfg: dict[str, Any], names: list[str], timeout_sec: int, dry_run: bool = False) -> None:
    for cluster, entry in clusters(cfg, names).items():
        nodes = {name: config.get_vm(cfg, name) for name in entry["nodes"]}
        ui.print_header(f"Cluster {cluster}: {' + '.join(entry['nodes'])} (corosync on the lab segment)")
        for name, vm in nodes.items():
            ssh.wait_for_ssh(vm, timeout_sec, dry_run=dry_run)
        primary = nodes[entry["primary"]]
        primary_ip = lan_ip(primary)
        if not dry_run and members_of(primary) == len(nodes):
            ui.print_status("ok", f"Cluster {cluster} already has its {len(nodes)} nodes")
            continue
        for name, vm in nodes.items():
            pin_hostname(vm, lan_ip(vm), dry_run)
        if dry_run or members_of(primary) == 0:
            _run(primary, f"pvecm create {shlex.quote(cluster)} --link0 {primary_ip}", dry_run)
        wait_writable(primary, dry_run)
        for name in entry["nodes"][1:]:
            vm = nodes[name]
            if not dry_run and members_of(vm) > 0:
                ui.print_status("ok", f"{name} is already a cluster member")
                continue
            if dry_run:
                key = "ssh-rsa DRY-RUN"
            else:
                key = _read(vm, "test -f /root/.ssh/id_rsa.pub || ssh-keygen -q -t rsa -N '' -f /root/.ssh/id_rsa; "
                                "cat /root/.ssh/id_rsa.pub").strip()
            _run(primary, f"grep -qxF {shlex.quote(key)} /root/.ssh/authorized_keys || "
                          f"echo {shlex.quote(key)} >> /root/.ssh/authorized_keys", dry_run)
            _run(vm, f"ssh-keygen -R {primary_ip} >/dev/null 2>&1; ssh-keyscan -H {primary_ip} >> /root/.ssh/known_hosts 2>/dev/null", dry_run)
            _run(vm, f"pvecm add {primary_ip} --link0 {lan_ip(vm)} --use_ssh", dry_run)
            wait_writable(primary, dry_run)
        if dry_run:
            continue
        deadline = time.monotonic() + QUORUM_WAIT_SEC
        while True:
            status = _read(primary, "pvecm status")
            if re.search(r"^Quorate:\s+Yes", status, re.MULTILINE) and members_of(primary) == len(nodes):
                break
            if time.monotonic() > deadline:
                raise VMError(f"cluster {cluster} did not reach quorum with {len(nodes)} nodes:\n{status[-800:]}")
            time.sleep(5)
        ui.print_status("ok", f"Cluster {cluster} formed: {len(nodes)} nodes, quorate")
