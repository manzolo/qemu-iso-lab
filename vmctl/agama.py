"""openSUSE Leap 16 on Agama, the installer that replaced YaST: bootstrap-agama.

Agama reads an unattended profile (JSON) by itself: with no ``inst.auto`` on the kernel line its
autoinstall service looks for ``autoinst.json`` at the root of a file system labelled OEMDRV
(``label://OEMDRV/autoinst.json``, read from the medium's own agama-autoinstall), so the host
packs the rendered profile into a seed CD with that label, boots the ISO's kernel and initrd with
``inst.finish=poweroff`` and the serial console, and waits. The installer's UI stays on the
graphical console; the serial only carries the kernel, systemd and the token.

The profile's post scripts do what Agama's schema cannot say: the user's sudo rule and SSH key,
sshd and the ttyS0 getty, the firewall's ssh service, the display manager's autologin, then (a
second script, outside the chroot) sync, flush and the token. Agama unmounts and powers off
after its post scripts; ``run_and_expect`` gives it ``SHUTDOWN_GRACE_SEC`` for that.
"""
from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

from vmctl import cloud_init, iso, runtime
from vmctl.errors import VMError

BOOTSTRAP_COMPLETE_TOKEN = "==> Agama installation complete!"
SEED_VOLUME_ID = "OEMDRV"
PROFILE_NAME = "autoinst.json"
DEFAULT_PRODUCT = "openSUSE_Leap"
KERNEL_MEMBER = "boot/x86_64/loader/linux"
INITRD_MEMBER = "boot/x86_64/loader/initrd"
# the live root is built into the initrd (etc/cmdline.d/10-liveroot.conf: root=live:LABEL=...)
KERNEL_APPEND = "inst.finish=poweroff splash=silent console=tty0 console=ttyS0,115200"
# after the token: Agama copies its logs, unmounts the target and powers off
SHUTDOWN_GRACE_SEC = 600
DEFAULT_PATTERNS = ["gnome"]
DEFAULT_PACKAGES = ["openssh", "qemu-guest-agent", "spice-vdagent"]
DEFAULT_SERVICES = ["sshd", "qemu-guest-agent", "serial-getty@ttyS0"]


def agama_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    cfg = vm.get("agama_config")
    if cfg is None:
        return None
    if not isinstance(cfg, dict):
        raise VMError("agama_config must be an object")
    return cfg


def agama_artifact_dir(vm: dict[str, Any]) -> Path:
    return runtime.resolve_path(vm["disk"]["path"]).parent / "agama"


def check_profile(vm_name: str, vm: dict[str, Any]) -> None:
    cfg = agama_config(vm)
    if cfg is None:
        raise VMError(f"VM '{vm_name}' does not define agama_config")
    if not str(cfg.get("username") or "").strip() or not str(cfg.get("password_hash") or "").strip():
        raise VMError(f"{vm_name}: agama_config needs username and password_hash")
    if (vm.get("disk") or {}).get("interface") != "virtio":
        raise VMError(f"{vm_name}: bootstrap-agama installs on the virtio disk (/dev/vda)")
    if not isinstance(vm.get("ssh_provision"), dict):
        raise VMError(f"{vm_name}: bootstrap-agama verifies the install over SSH (ssh_provision)")


def render_chroot_script(vm_name: str, vm: dict[str, Any], keys: list[str]) -> str:
    """The post script Agama runs inside the installed system."""
    cfg = agama_config(vm) or {}
    username = str(cfg["username"]).strip()
    q = shlex.quote
    services = [str(s) for s in (cfg.get("enable_services") or DEFAULT_SERVICES)]
    firewall = [str(s) for s in (cfg.get("open_firewall_services") or ["ssh"])]
    keys_text = "\n".join(k.strip() for k in keys if k.strip())
    lines = [
        "#!/bin/bash",
        "# vmctl: configuration Agama's profile cannot express (see vmctl/agama.py)",
        "set -x",
        f"usermod -aG wheel {q(username)} || true",
        "install -d -m0755 /etc/sudoers.d",
        f"printf '%s\\n' '{username} ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/vmctl-{username}",
        f"chmod 0440 /etc/sudoers.d/vmctl-{username}",
        f"install -d -m0700 /home/{username}/.ssh",
        f"cat > /home/{username}/.ssh/authorized_keys <<'VMCTL_KEYS'",
        keys_text,
        "VMCTL_KEYS",
        f"chmod 0600 /home/{username}/.ssh/authorized_keys",
        f"chown -R {q(username)}: /home/{username}/.ssh",
        "install -d -m0755 /etc/ssh/sshd_config.d",
        "printf 'UseDNS no\\n' > /etc/ssh/sshd_config.d/90-vmctl.conf",
    ]
    lines += [f"systemctl enable {q(s)} || true" for s in services]
    lines += [f"firewall-offline-cmd --add-service={q(s)} || true" for s in firewall]
    if cfg.get("autologin", True):
        lines += [
            "if [ -f /etc/sysconfig/displaymanager ]; then",
            f"  sed -i 's/^DISPLAYMANAGER_AUTOLOGIN=.*/DISPLAYMANAGER_AUTOLOGIN=\"{username}\"/' /etc/sysconfig/displaymanager",
            "fi",
            "install -d -m0755 /etc/gdm",
            "cat > /etc/gdm/custom.conf <<'GDMEOF'",
            "[daemon]",
            "AutomaticLoginEnable=True",
            f"AutomaticLogin={username}",
            "GDMEOF",
        ]
    lines += [str(c) for c in (cfg.get("chroot_commands") or [])]
    # Leap 16 is SELinux by default: what this script wrote gets its label here, offline
    # (setfiles needs no SELinux in the running kernel, restorecon does).
    lines += [
        "fc=/etc/selinux/targeted/contexts/files/file_contexts",
        f"[ -f $fc ] && setfiles -F $fc /home/{username} /etc/sudoers.d /etc/ssh /etc/gdm /etc/sysconfig 2>&1 || true",
    ]
    return "\n".join(lines) + "\n"


def render_finish_script(vm: dict[str, Any]) -> str:
    """The last post script, outside the chroot: sync and flush, then the token (CLAUDE.md, the
    token/flush rule); Agama itself unmounts the target and powers off afterwards."""
    disk = "/dev/" + str((agama_config(vm) or {}).get("disk_device") or "vda")
    return "\n".join([
        "#!/bin/bash",
        "sync",
        f"blockdev --flushbufs {disk} {disk}1 {disk}2 {disk}3 2>/dev/null || true",
        f'echo "{BOOTSTRAP_COMPLETE_TOKEN}" > /dev/ttyS0',
    ]) + "\n"


def render_profile(vm_name: str, vm: dict[str, Any], keys: list[str]) -> str:
    cfg = agama_config(vm) or {}
    username = str(cfg["username"]).strip()
    disk = "/dev/" + str(cfg.get("disk_device") or "vda")
    profile: dict[str, Any] = {
        "product": {"id": str(cfg.get("product") or DEFAULT_PRODUCT)},
        "hostname": {"static": str(cfg.get("hostname") or vm_name)},
        "localization": {
            "language": str(cfg.get("language") or "en_US.UTF-8"),
            "keyboard": str(cfg.get("keyboard") or "us"),
            "timezone": str(cfg.get("timezone") or "UTC"),
        },
        "user": {
            "fullName": str(cfg.get("realname") or username),
            "userName": username,
            "password": str(cfg["password_hash"]).strip(),
            "hashedPassword": True,
        },
        "root": {"password": str(cfg["password_hash"]).strip(), "hashedPassword": True},
        "software": {
            "patterns": [str(p) for p in (cfg.get("patterns") or DEFAULT_PATTERNS)],
            "packages": [str(p) for p in (cfg.get("packages") or DEFAULT_PACKAGES)],
        },
        "storage": {"drives": [{"search": disk, "partitions": [{"search": "*", "delete": True}, {"generate": "default"}]}]},
        "scripts": {"post": [
            {"name": "vmctl-configure.sh", "chroot": True, "content": render_chroot_script(vm_name, vm, keys)},
            {"name": "vmctl-finish.sh", "chroot": False, "content": render_finish_script(vm)},
        ]},
    }
    if keys:
        profile["root"]["sshPublicKey"] = keys[0].strip()
    return json.dumps(profile, indent=2) + "\n"


def create_seed_iso(vm_name: str, vm: dict[str, Any], keys: list[str], dry_run: bool = False) -> Path:
    return cloud_init.create_iso_with_files(agama_artifact_dir(vm), {PROFILE_NAME: render_profile(vm_name, vm, keys)},
                                            dry_run=dry_run, volume_id=SEED_VOLUME_ID)


def seed_iso_drive_args(iso_path: Path) -> list[str]:
    return ["-drive", f"file={iso_path},format=raw,if=ide,index=3,media=cdrom,readonly=on"]


def extract_boot_artifacts(vm: dict[str, Any], iso_path: Path, dry_run: bool = False) -> tuple[Path, Path]:
    artifact_dir = iso.installer_artifact_dir(vm)
    kernel_path, initrd_path = artifact_dir / "linux", artifact_dir / "initrd"
    iso.extract_iso_member(iso_path, KERNEL_MEMBER, kernel_path, dry_run=dry_run)
    iso.extract_iso_member(iso_path, INITRD_MEMBER, initrd_path, dry_run=dry_run)
    return kernel_path, initrd_path


def resolve_ssh_pubkey(vm: dict[str, Any], dry_run: bool = False) -> list[str]:
    keys = cloud_init._authorized_keys_for_vm(vm, dry_run=dry_run)
    if not keys and not dry_run:
        raise VMError("the Agama bootstrap needs the project SSH key (ssh_provision): none resolved")
    return keys
