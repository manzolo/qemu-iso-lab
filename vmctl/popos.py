"""Pop!_OS 24.04 (COSMIC) from its live ISO: bootstrap-popos.

The graphical installer (pop-installer) is a front end for distinst, and the live medium carries
distinst's command line too. The host boots the ISO's casper kernel on the serial console in
multi-user mode (no live COSMIC session), logs in as the live user, and runs install.sh from a
seed CD with sudo: distinst copies the live squashfs onto a GPT disk (EFI + ext4, systemd-boot
through kernelstub, as the installer does), creates the user, the locale, the keyboard and the
time zone; then a chroot adds OpenSSH, the password hash, the sudo rule and the project key,
greetd's autologin into COSMIC and the ttyS0 getty, and drops the first-login initial setup.
sync, flush, the token, poweroff.
"""
from __future__ import annotations

import re
import shlex
import tempfile
from pathlib import Path
from typing import Any

from vmctl import cloud_init, iso, runtime
from vmctl.errors import VMError

BOOTSTRAP_COMPLETE_TOKEN = "==> Pop!_OS installation complete!"
BOOTSTRAP_FAILED_TOKEN = "==> Pop!_OS installation FAILED"
LIVE_USER = "pop-os"
LIVE_LOGIN_PROMPT = "pop-os login: "
LIVE_PASSWORD_PROMPT = "Password: "
LIVE_SHELL_PROMPT = "pop-os@pop-os:~$ "
SEED_VOLUME_ID = "VMCTLPOP"
SEED_MOUNTPOINT = "/tmp/vmctl-seed"
LIVE_BOOT_CONFIG = "/boot/grub/grub.cfg"
DEFAULT_LIVE_MEDIA_PATH = "casper_pop-os_24.04_amd64_generic_debug_443"
DEFAULT_PACKAGES = ["openssh-server"]


def popos_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    cfg = vm.get("popos_config")
    if cfg is None:
        return None
    if not isinstance(cfg, dict):
        raise VMError("popos_config must be an object")
    return cfg


def popos_artifact_dir(vm: dict[str, Any]) -> Path:
    return runtime.resolve_path(vm["disk"]["path"]).parent / "popos"


def check_profile(vm_name: str, vm: dict[str, Any]) -> None:
    cfg = popos_config(vm)
    if cfg is None:
        raise VMError(f"VM '{vm_name}' does not define popos_config")
    if not str(cfg.get("username") or "").strip() or not str(cfg.get("password_hash") or "").strip():
        raise VMError(f"{vm_name}: popos_config needs username and password_hash")
    if (vm.get("firmware") or {}).get("type") != "efi":
        raise VMError(f"{vm_name}: Pop!_OS boots with systemd-boot (firmware.type efi)")
    if (vm.get("disk") or {}).get("interface") != "virtio":
        raise VMError(f"{vm_name}: bootstrap-popos installs on the virtio disk (/dev/vda)")
    if not isinstance(vm.get("ssh_provision"), dict):
        raise VMError(f"{vm_name}: bootstrap-popos verifies the install over SSH (ssh_provision)")


def parse_live_media_path(text: str) -> str | None:
    """The casper directory of the medium, from its grub.cfg (it carries the build number)."""
    match = re.search(r"live-media-path=/?(\S+)", text)
    return match.group(1).strip("/") if match else None


def resolve_live_media_path(vm: dict[str, Any], iso_path: Path, dry_run: bool = False) -> str:
    configured = str((popos_config(vm) or {}).get("live_media_path") or "").strip("/")
    if configured:
        return configured
    if not dry_run and iso_path.is_file():
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "grub.cfg"
            try:
                runtime.run(["xorriso", "-osirrox", "on", "-indev", str(iso_path), "-extract", LIVE_BOOT_CONFIG, str(dest)], quiet=True)
                found = parse_live_media_path(dest.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                found = None
            if found:
                return found
    return DEFAULT_LIVE_MEDIA_PATH


def kernel_append(live_media_path: str) -> str:
    return (f"boot=casper live-media-path=/{live_media_path} hostname=pop-os username={LIVE_USER} noprompt "
            "systemd.unit=multi-user.target console=tty0 console=ttyS0,115200")


def render_install_script(vm_name: str, vm: dict[str, Any], keys: list[str], live_media_path: str) -> str:
    cfg = popos_config(vm) or {}
    username = str(cfg["username"]).strip()
    realname = str(cfg.get("realname") or username).strip()
    password_hash = str(cfg["password_hash"]).strip()
    hostname = str(cfg.get("hostname") or vm_name).strip()
    timezone = str(cfg.get("timezone") or "Etc/UTC").strip()
    keyboard = str(cfg.get("keyboard") or "us").strip()
    locale = str(cfg.get("locale") or "en_US.UTF-8").strip()
    packages = [str(p) for p in (cfg.get("packages") or DEFAULT_PACKAGES)]
    extra = [str(c) for c in (cfg.get("chroot_commands") or [])]
    keys_text = "\n".join(k.strip() for k in keys if k.strip())
    q = shlex.quote
    media = f"/cdrom/{live_media_path}"
    chroot_lines = "\n".join(extra)
    return f"""#!/bin/bash
# vmctl: Pop!_OS unattended install with distinst, run as root from the live shell (see vmctl/popos.py).
set -Eeuo pipefail
trap 'echo "{BOOTSTRAP_FAILED_TOKEN}: line $LINENO: $BASH_COMMAND"; sync; poweroff -f' ERR
DISK=/dev/vda
echo "==> Waiting for the live network..."
for i in $(seq 1 60); do getent hosts archive.ubuntu.com >/dev/null 2>&1 && break; sleep 2; done
echo "==> distinst: {media}/filesystem.squashfs onto $DISK..."
# the password is replaced by the hash below; distinst only takes plain text
distinst -s {media}/filesystem.squashfs -r {media}/filesystem.manifest-remove \\
    -h {q(hostname)} -k {q(keyboard)} -l {q(locale)} --tz {q(timezone)} \\
    -b $DISK -t "$DISK:gpt" \\
    -n "$DISK:primary:start:1024M:fat32:mount=/boot/efi:flags=esp" \\
    -n "$DISK:primary:1024M:end:ext4:mount=/" \\
    --username {q(username)} --realname {q(realname)} --password vmctl-temporary
echo "==> Configuring the installed system..."
mkdir -p /mnt/vmctl
mount ${{DISK}}2 /mnt/vmctl
mount ${{DISK}}1 /mnt/vmctl/boot/efi
for d in dev dev/pts proc sys run; do mount --bind /$d /mnt/vmctl/$d; done
T=/mnt/vmctl
[ -e $T/etc/resolv.conf ] && mv $T/etc/resolv.conf $T/etc/resolv.conf.vmctl
cp -L /etc/resolv.conf $T/etc/resolv.conf
for attempt in 1 2 3; do
    chroot $T env DEBIAN_FRONTEND=noninteractive apt-get update && \\
    chroot $T env DEBIAN_FRONTEND=noninteractive apt-get install -y {" ".join(q(p) for p in packages)} && break
    [ $attempt = 3 ] && false
    echo "    apt failed, retrying in 20 s ($attempt/3)"; sleep 20
done
rm -f $T/etc/resolv.conf
[ -e $T/etc/resolv.conf.vmctl ] && mv $T/etc/resolv.conf.vmctl $T/etc/resolv.conf
chroot $T usermod -p {q(password_hash)} {q(username)}
echo '{username} ALL=(ALL) NOPASSWD: ALL' > $T/etc/sudoers.d/vmctl-{username}
chmod 0440 $T/etc/sudoers.d/vmctl-{username}
install -d -m 700 $T/home/{username}/.ssh
cat > $T/home/{username}/.ssh/authorized_keys <<'VMCTL_KEYS'
{keys_text}
VMCTL_KEYS
chmod 600 $T/home/{username}/.ssh/authorized_keys
chroot $T chown -R {q(username)}: /home/{username}/.ssh
install -d -m 755 $T/etc/ssh/sshd_config.d
printf 'UseDNS no\\n' > $T/etc/ssh/sshd_config.d/90-vmctl.conf
chroot $T systemctl enable ssh.service serial-getty@ttyS0.service
chroot $T systemctl set-default graphical.target
# greetd runs cosmic-greeter with this file: the initial session logs the user straight in
cat >> $T/etc/greetd/cosmic-greeter.toml <<'GREETD'

[initial_session]
command = "start-cosmic"
user = "{username}"
GREETD
# the first-login wizard (keyboard, user, appearance) would cover the desktop
rm -f $T/etc/xdg/autostart/com.system76.CosmicInitialSetup.desktop
{chroot_lines}
echo "==> Finishing..."
sync
for d in run sys proc dev/pts dev; do umount $T/$d; done
umount $T/boot/efi
umount $T
sync
blockdev --flushbufs $DISK ${{DISK}}1 ${{DISK}}2 || true
trap - ERR
echo "{BOOTSTRAP_COMPLETE_TOKEN}"
poweroff -f
"""


def create_seed_iso(vm_name: str, vm: dict[str, Any], keys: list[str], live_media_path: str, dry_run: bool = False) -> Path:
    return cloud_init.create_iso_with_files(popos_artifact_dir(vm),
                                            {"install.sh": render_install_script(vm_name, vm, keys, live_media_path)},
                                            dry_run=dry_run, volume_id=SEED_VOLUME_ID)


def seed_iso_drive_args(iso_path: Path) -> list[str]:
    return ["-drive", f"file={iso_path},format=raw,if=ide,index=3,media=cdrom,readonly=on"]


def live_trigger_command() -> str:
    # the live user's sudo needs no password on casper
    return (f"mkdir -p {SEED_MOUNTPOINT}; for d in /dev/sr1 /dev/sr0 /dev/sr2; do "
            f"sudo mount -t iso9660 -o ro $d {SEED_MOUNTPOINT} 2>/dev/null && [ -f {SEED_MOUNTPOINT}/install.sh ] && break; "
            f"sudo umount {SEED_MOUNTPOINT} 2>/dev/null; done; sudo bash {SEED_MOUNTPOINT}/install.sh")


def extract_boot_artifacts(vm: dict[str, Any], iso_path: Path, live_media_path: str, dry_run: bool = False) -> tuple[Path, Path]:
    artifact_dir = iso.installer_artifact_dir(vm)
    kernel_path, initrd_path = artifact_dir / "vmlinuz", artifact_dir / "initrd.gz"
    iso.extract_iso_member(iso_path, f"{live_media_path}/vmlinuz.efi", kernel_path, dry_run=dry_run)
    iso.extract_iso_member(iso_path, f"{live_media_path}/initrd.gz", initrd_path, dry_run=dry_run)
    return kernel_path, initrd_path


def resolve_ssh_pubkey(vm: dict[str, Any], dry_run: bool = False) -> list[str]:
    keys = cloud_init._authorized_keys_for_vm(vm, dry_run=dry_run)
    if not keys and not dry_run:
        raise VMError("the Pop!_OS bootstrap needs the project SSH key (ssh_provision): none resolved")
    return keys
