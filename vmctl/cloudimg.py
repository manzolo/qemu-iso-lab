"""Vendor cloud images (Ubuntu minimal cloudimg, Debian genericcloud): bootstrap-cloudimg.

The lab members of the imported qlab labs are small servers, and an unattended install from
the ISO costs 8-15 minutes each.  A cloud image is already installed: the host downloads it
once into ``isos/`` like an ISO (``iso``/``iso_url``/``iso_sha256_url``: the vendor's SUMS file
pins a moving ``release/`` image, as for Tumbleweed), keeps a hard link of the validated copy
under ``isos/.cloudimg/<sha256>.<format>`` (so ``ensure_iso`` replacing a stale cache never
pulls the base out from under an overlay) and makes the VM disk a **qcow2 overlay** on it
(``qemu-img create -b``, resized to ``disk.size``: ten members cost the image once plus their
own writes).  The first boot reads a NoCloud seed (``cidata`` CD): the user with the project key,
the password hash and NOPASSWD sudo, the packages, the profile's ``write_files`` and ``runcmd``,
then ``provision.sh`` ends with ``sync`` → the completion token on ttyS0 and cloud-init's own
``power_state`` powers the guest off (the token/flush rule of CLAUDE.md).  Later boots have no
seed: ``provision.sh`` disables cloud-init for them (a boot without a datasource would be a new
instance to it: default user created again, host keys regenerated).  The post-install is the
usual SSH one (``run_post_install``).  A fresh overlay is a few hundred KB, so ``vmstate`` counts
a qcow2 with a backing file as a disk with data.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
from pathlib import Path
from typing import Any

from vmctl import cloud_init, iso, runtime, state, ui
from vmctl.errors import VMError

BOOTSTRAP_COMPLETE_TOKEN = "==> Cloud image provisioning complete!"
BOOTSTRAP_FAILED_TOKEN = "==> Cloud image provisioning FAILED"
# cloud-init runs power_state after the token: generous, the first boot also dist-upgrades nothing.
SHUTDOWN_GRACE_SEC = 180
BACKING_DIR = "isos/.cloudimg"
PROVISION_SCRIPT = "/var/lib/vmctl/provision.sh"
IMAGE_FORMATS = ("qcow2", "raw")
# What the minimal images lack and every lab guide uses, as in qlab.
DEFAULT_PACKAGES = ["openssh-server", "sudo", "nano", "iputils-ping", "tcpdump", "net-tools"]


def cloudimg_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    cfg = vm.get("cloudimg_config")
    if cfg is None:
        return None
    if not isinstance(cfg, dict):
        raise VMError("cloudimg_config must be an object")
    return cfg


def cloudimg_artifact_dir(vm: dict[str, Any]) -> Path:
    return runtime.resolve_path(vm["disk"]["path"]).parent / "cloudimg"


def image_format(vm: dict[str, Any]) -> str:
    cfg = cloudimg_config(vm) or {}
    return str(cfg.get("image_format") or "qcow2").strip()


def check_profile(vm_name: str, vm: dict[str, Any]) -> None:
    cfg = cloudimg_config(vm)
    if cfg is None:
        raise VMError(f"VM '{vm_name}' does not define cloudimg_config")
    if not str(cfg.get("username") or "").strip() or not str(cfg.get("password_hash") or "").strip():
        raise VMError(f"{vm_name}: cloudimg_config needs username and password_hash")
    if image_format(vm) not in IMAGE_FORMATS:
        raise VMError(f"{vm_name}: cloudimg_config.image_format must be one of {', '.join(IMAGE_FORMATS)}")
    disk = vm.get("disk") or {}
    if disk.get("format") != "qcow2":
        raise VMError(f"{vm_name}: bootstrap-cloudimg makes the disk a qcow2 overlay on the image (disk.format qcow2)")
    if disk.get("interface") != "virtio":
        raise VMError(f"{vm_name}: bootstrap-cloudimg expects the disk on virtio (/dev/vda)")
    if not isinstance(vm.get("ssh_provision"), dict):
        raise VMError(f"{vm_name}: bootstrap-cloudimg provisions and verifies over SSH (ssh_provision)")
    if vm.get("disk_image"):
        raise VMError(f"{vm_name}: cloudimg_config and disk_image exclude each other")


# --- the base image and the overlay ----------------------------------------------------------

def backing_image(vm: dict[str, Any], source: Path, dry_run: bool = False) -> Path:
    """The validated image under ``isos/.cloudimg/<sha256>.<format>``: a hard link to the cache
    (a copy across file systems), kept by content so a vendor refresh that makes ``ensure_iso``
    replace the cached file leaves every overlay's base in place."""
    fmt = image_format(vm)
    # The real path: the overlay's header records it, and a checkout reached through a symlink
    # (a worktree whose isos/ points at the main checkout's) may not outlive the disk.
    directory = (state.ROOT / BACKING_DIR).parent.resolve() / Path(BACKING_DIR).name
    if dry_run and not source.is_file():
        return directory / f"dry-run.{fmt}"
    digest = str(vm.get("iso_sha256") or "").strip().lower() or iso.sha256_file(source)
    target = directory / f"{digest}.{fmt}"
    if target.is_file():
        return target
    if dry_run:
        ui.print_note(f"Would keep the image as {ui.pretty_path(target)}")
        return target
    runtime.ensure_parent(target)
    staging = target.with_name(f".{target.name}.part-{os.getpid()}")
    try:
        os.link(source, staging)
    except OSError:
        shutil.copyfile(source, staging)
    os.replace(staging, target)
    ui.print_status("ok", f"Image kept as {ui.pretty_path(target)}")
    return target


def create_overlay(vm: dict[str, Any], backing: Path, dry_run: bool = False) -> Path:
    """The VM disk: a qcow2 overlay on *backing*, with the profile's ``disk.size`` as capacity
    (cloud-init's growpart takes the rest at first boot)."""
    disk = vm["disk"]
    disk_path = runtime.resolve_path(disk["path"])
    runtime.ensure_parent(disk_path)
    runtime.run(["qemu-img", "create", "-f", "qcow2", "-b", str(backing), "-F", image_format(vm), str(disk_path), disk["size"]],
                dry_run=dry_run, quiet=True)
    return disk_path


# --- the seed ----------------------------------------------------------------------------------

def _shell_line(entry: Any) -> str:
    if isinstance(entry, list):
        return " ".join(shlex.quote(str(part)) for part in entry)
    return str(entry)


def render_provision_script(vm_name: str, vm: dict[str, Any]) -> str:
    """What runs last in the first boot (cloud-init's runcmd): the serial getty, the profile's
    ``runcmd`` lines, cloud-init disabled for the next boots, sync, the token.  A failed line
    prints the FAILED token instead (sh has no ERR trap: the EXIT trap reads the status)."""
    cfg = cloudimg_config(vm) or {}
    lines = [_shell_line(entry) for entry in (cfg.get("runcmd") or [])]
    body = "\n".join(lines) + ("\n" if lines else "")
    return f"""#!/bin/sh
# vmctl: first-boot provisioning of {vm_name} (see vmctl/cloudimg.py); cloud-init powers off after it.
set -eu
trap 'rc=$?; if [ "$rc" -ne 0 ]; then echo "{BOOTSTRAP_FAILED_TOKEN}: exit $rc at line $LINENO" | tee /dev/ttyS0; sync; fi' EXIT
systemctl enable serial-getty@ttyS0.service >/dev/null 2>&1 || true
{body}# Later boots carry no seed: without a datasource cloud-init would treat them as a new instance.
touch /etc/cloud/cloud-init.disabled
sync
echo "{BOOTSTRAP_COMPLETE_TOKEN}" | tee /dev/ttyS0
"""


def render_user_data(vm_name: str, vm: dict[str, Any], keys: list[str]) -> str:
    cfg = cloudimg_config(vm) or {}
    username = str(cfg["username"]).strip()
    hostname = str(cfg.get("hostname") or vm_name).strip()
    user: dict[str, Any] = {
        "name": username,
        "gecos": str(cfg.get("realname") or username),
        "shell": "/bin/bash",
        "groups": "adm,sudo",
        "sudo": ["ALL=(ALL) NOPASSWD:ALL"],
        "lock_passwd": False,
        "passwd": str(cfg["password_hash"]).strip(),
        "ssh_authorized_keys": [key.strip() for key in keys if key.strip()],
    }
    write_files = [dict(entry) for entry in (cfg.get("write_files") or [])]
    write_files.append({"path": PROVISION_SCRIPT, "permissions": "0755", "content": render_provision_script(vm_name, vm)})
    payload: dict[str, Any] = {
        # A name with dots (ubuntu-24.04-cloud) is a host name here, not host + domain: without
        # fqdn + prefer_fqdn_over_hostname cloud-init wrote "ubuntu-24" (live, 2026-10-02).
        "hostname": hostname,
        "fqdn": hostname,
        "prefer_fqdn_over_hostname": True,
        "manage_etc_hosts": True,
        "users": [user],
        "ssh_pwauth": True,
        "package_update": True,
        "package_upgrade": False,
        "packages": [str(p) for p in (cfg.get("packages") or DEFAULT_PACKAGES)],
        "write_files": write_files,
        "runcmd": [["sh", PROVISION_SCRIPT]],
        # After runcmd, the last module of the final stage: the natural power-off the host waits for.
        "power_state": {"mode": "poweroff", "timeout": 60, "condition": True},
    }
    for key, field in (("timezone", "timezone"), ("locale", "locale")):
        if str(cfg.get(field) or "").strip():
            payload[key] = str(cfg[field]).strip()
    if str(cfg.get("keyboard") or "").strip():
        payload["keyboard"] = {"layout": str(cfg["keyboard"]).strip()}
    return "#cloud-config\n" + json.dumps(payload, indent=2) + "\n"


def render_meta_data(vm_name: str, vm: dict[str, Any]) -> str:
    cfg = cloudimg_config(vm) or {}
    return json.dumps({"instance-id": f"vmctl-{vm_name}", "local-hostname": str(cfg.get("hostname") or vm_name)}) + "\n"


def create_seed(vm_name: str, vm: dict[str, Any], keys: list[str], dry_run: bool = False) -> Path:
    return cloud_init.create_seed_image(cloudimg_artifact_dir(vm), render_user_data(vm_name, vm, keys),
                                        render_meta_data(vm_name, vm), dry_run=dry_run)


def seed_drive_args(seed_path: Path) -> list[str]:
    return cloud_init.cloud_init_drive_args(seed_path)


def resolve_ssh_pubkey(vm: dict[str, Any], dry_run: bool = False) -> list[str]:
    keys = cloud_init._authorized_keys_for_vm(vm, dry_run=dry_run)
    if not keys and not dry_run:
        raise VMError("the cloud image bootstrap needs the project SSH key (ssh_provision): none resolved")
    return keys
