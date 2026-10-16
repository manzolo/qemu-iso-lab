"""Network boot (F6 of docs/QLAB_IMPORT.md): ``bootstrap-netboot`` installs a VM whose installer
arrives over a lab segment from another VM of the lab, the PXE server, never from vmctl.

The client has an empty disk and a segment NIC that declares ``"boot": true``. vmctl renders the
answers the installer will ask for (the d-i preseed and its late script, with the identity, the
project key and the completion token: the same renderer as ``bootstrap-preseed``) and copies them
into the server's web root; everything else is the lab's: DHCP, TFTP, the boot menu, the kernel
and initrd, the route to the mirror. That is what the lab teaches, so vmctl never answers DHCP on
the segment. During the install the NIC is the first boot device (its iPXE option ROM); the disk
keeps the plain ``if=virtio`` attachment every other boot uses, so the PCI layout, and with it the
interface names the installer writes, stay those of the installed system (pinning the disk with
a bootindex moved every NIC one slot: enp0s4 at the install was enp0s3 afterwards, verified live).
``vmctl start`` pins nothing and boots the disk.
"""
from __future__ import annotations

import re
import shlex
from typing import Any

from vmctl import preseed, qemu, ssh
from vmctl.errors import VMError

BOOTSTRAP_COMPLETE_TOKEN = preseed.BOOTSTRAP_COMPLETE_TOKEN
NIC_BOOTINDEX = 1
PAYLOADS = ("preseed.cfg", "late_command.sh")


def netboot_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    cfg = vm.get("netboot_config")
    if cfg is None:
        return None
    if not isinstance(cfg, dict):
        raise VMError("Invalid netboot_config: expected object")
    return cfg


def boot_nic(vm: dict[str, Any]) -> dict[str, Any]:
    """The install-phase NIC the firmware netboots from: exactly one, on a lab segment."""
    nics = [spec for spec in qemu.network_specs(vm, "install") if spec["boot"]]
    if len(nics) != 1:
        raise VMError('netboot needs one install-phase NIC with "boot": true in networks')
    if nics[0]["type"] != "segment":
        raise VMError("the netboot NIC must be a segment NIC: the PXE server is another VM of the lab, "
                      "QEMU's own network (slirp) has no PXE server")
    return nics[0]


def check_profile(cfg: dict[str, Any], name: str, vm: dict[str, Any]) -> dict[str, Any]:
    """The checks a netboot profile must pass before anything boots; returns its netboot_config."""
    nb = netboot_config(vm)
    if nb is None:
        raise VMError(f"VM '{name}' does not define netboot_config")
    if preseed.preseed_config(vm) is None:
        raise VMError(f"{name}: netboot installs with a d-i preseed served by the lab: preseed_config is required")
    if vm.get("ssh_provision") is None:
        raise VMError(f"{name}: netboot ends with the SSH post-install: ssh_provision is required")
    if not any(spec["type"] == "user" and spec["ssh"] for spec in qemu.network_specs(vm, "install")):
        raise VMError(f"{name}: the host reaches the installed client over SSH: add a user NIC with ssh")
    server = str(nb.get("server") or "").strip()
    if not server:
        raise VMError(f"{name}: netboot_config.server names the lab's PXE server")
    if server == name or server not in cfg.get("vms", {}):
        raise VMError(f"{name}: netboot_config.server '{server}' is not another VM of the catalog")
    segment = boot_nic(vm)["name"]
    server_vm = cfg["vms"][server]
    if not any(spec["type"] == "segment" and spec["name"] == segment for spec in qemu.network_specs(server_vm, "runtime")):
        raise VMError(f"{name}: the server {server} has no runtime NIC on segment '{segment}', where the client netboots")
    if server_vm.get("ssh_provision") is None:
        raise VMError(f"{name}: vmctl copies the answers to {server} over SSH: the server needs ssh_provision")
    payload_dir = str(nb.get("payload_dir") or "")
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", payload_dir) or ".." in payload_dir:
        raise VMError(f"{name}: netboot_config.payload_dir is an absolute path on the server (the web root it serves)")
    if not re.fullmatch(r"http://[A-Za-z0-9.:-]+(/[A-Za-z0-9_./-]*)?", str(nb.get("payload_url") or "")):
        raise VMError(f"{name}: netboot_config.payload_url is the http:// URL of payload_dir as the client sees it")
    return nb


def payload_url(nb: dict[str, Any], filename: str) -> str:
    return str(nb["payload_url"]).rstrip("/") + "/" + filename


def payloads(name: str, vm: dict[str, Any]) -> dict[str, str]:
    """The files vmctl hands the server: the preseed (fetched with ``url=`` from the lab's boot
    menu) and its late script (fetched by the preseed itself), both from the d-i renderer."""
    nb = netboot_config(vm) or {}
    return {
        "preseed.cfg": preseed.render_preseed(name, vm, late_script_url=payload_url(nb, "late_command.sh")),
        "late_command.sh": preseed.render_late_command_script(name, vm),
    }


def upload_command(server_vm: dict[str, Any], payload_dir: str, filename: str, dry_run: bool = False) -> list[str]:
    """Write stdin to *payload_dir*/*filename* on the server as root, world-readable for the web
    server, through a temporary name so a client never fetches half a file."""
    target = f"{payload_dir.rstrip('/')}/{filename}"
    script = (f"install -d -m 755 {shlex.quote(payload_dir)} && cat > {shlex.quote(target + '.vmctl-tmp')} "
              f"&& chmod 644 {shlex.quote(target + '.vmctl-tmp')} && mv -f {shlex.quote(target + '.vmctl-tmp')} {shlex.quote(target)}")
    return ssh.remote_sudo_shell_cmd(server_vm, script, dry_run=dry_run)
