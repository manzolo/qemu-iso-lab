"""Void Linux from its live ISO: bootstrap-void.

void-installer is a dialog program with no answer file, but the live image offers a login on
the serial console when the kernel line names it. The host boots the ISO's kernel and initrd
with console=ttyS0, logs in as root (the live's password is voidlinux), mounts a seed CD and
runs install.sh: GPT with an EFI partition, ext4, xbps-install -r /mnt of base-system plus the
profile's packages from the official repository (the live keys copied first), then the
target's configuration (locale, time zone, the user with sudo and the project key, runit
services, LightDM autologin into the session, GRUB for EFI), sync, the token, poweroff.
"""
from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from vmctl import cloud_init, iso, runtime
from vmctl.errors import VMError

BOOTSTRAP_COMPLETE_TOKEN = "==> Void Linux installation complete!"
BOOTSTRAP_FAILED_TOKEN = "==> Void Linux installation FAILED"
LIVE_LOGIN_PROMPT = "void-live login: "
LIVE_PASSWORD_PROMPT = "Password: "
LIVE_PASSWORD = "voidlinux"
# root's live shell is sh with the plain "# " prompt (first live run, 2026-09-30); the banner
# above it has no line starting with "# ", so the newline anchors the match.
LIVE_SHELL_PROMPT = "\n# "
SEED_VOLUME_ID = "VMCTLVOID"
SEED_MOUNTPOINT = "/vmctl-seed"
KERNEL_MEMBER = "boot/vmlinuz"
INITRD_MEMBER = "boot/initrd"
LIVE_KERNEL_APPEND = ("root=live:CDLABEL=VOID_LIVE init=/sbin/init ro rd.luks=0 rd.md=0 rd.dm=0 loglevel=4 "
                      "rd.live.overlay.overlayfs=1 console=tty0 console=ttyS0,115200")
DEFAULT_REPOSITORY = "https://repo-default.voidlinux.org/current"
DEFAULT_PACKAGES = ["base-system", "grub-x86_64-efi", "openssh", "sudo", "dbus", "xorg-minimal", "xorg-fonts",
                    "xfce4", "lightdm", "lightdm-gtk3-greeter"]


def void_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    cfg = vm.get("void_config")
    if cfg is None:
        return None
    if not isinstance(cfg, dict):
        raise VMError("void_config must be an object")
    return cfg


def void_artifact_dir(vm: dict[str, Any]) -> Path:
    return runtime.resolve_path(vm["disk"]["path"]).parent / "void"


def check_profile(vm_name: str, vm: dict[str, Any]) -> None:
    cfg = void_config(vm)
    if cfg is None:
        raise VMError(f"VM '{vm_name}' does not define void_config")
    if not str(cfg.get("username") or "").strip() or not str(cfg.get("password_hash") or "").strip():
        raise VMError(f"{vm_name}: void_config needs username and password_hash")
    if (vm.get("firmware") or {}).get("type") != "efi":
        raise VMError(f"{vm_name}: bootstrap-void installs GRUB for EFI (firmware.type efi)")
    if (vm.get("disk") or {}).get("interface") != "virtio":
        raise VMError(f"{vm_name}: bootstrap-void expects the disk on virtio (/dev/vda)")
    if not isinstance(vm.get("ssh_provision"), dict):
        raise VMError(f"{vm_name}: bootstrap-void verifies the install over SSH (ssh_provision)")


def render_install_script(vm_name: str, vm: dict[str, Any], keys: list[str]) -> str:
    cfg = void_config(vm) or {}
    username = str(cfg["username"]).strip()
    password_hash = str(cfg["password_hash"]).strip()
    hostname = str(cfg.get("hostname") or vm_name).strip()
    timezone = str(cfg.get("timezone") or "UTC").strip()
    keymap = str(cfg.get("keymap") or "us").strip()
    locale = str(cfg.get("locale") or "en_US.UTF-8").strip()
    session = str(cfg.get("session") or "xfce").strip()
    repository = str(cfg.get("repository") or DEFAULT_REPOSITORY).strip()
    packages = [str(p) for p in (cfg.get("packages") or DEFAULT_PACKAGES)]
    services = [str(s) for s in (cfg.get("services") or ["dhcpcd", "sshd", "dbus", "lightdm", "agetty-ttyS0"])]
    keys_text = "\n".join(k.strip() for k in keys if k.strip())
    q = shlex.quote
    return f"""#!/bin/bash
# vmctl: Void Linux unattended install, run from the live ISO's root shell (see vmctl/void.py).
set -Eeuo pipefail
trap 'echo "{BOOTSTRAP_FAILED_TOKEN}: line $LINENO: $BASH_COMMAND"; sync; poweroff -f' ERR
DISK=/dev/vda
echo "==> Partitioning $DISK (GPT: EFI + root)..."
printf 'label: gpt\\n,512M,U\\n,,L\\n' | sfdisk --wipe always $DISK
mkfs.vfat -F32 -n EFI ${{DISK}}1
mkfs.ext4 -F -L voidroot ${{DISK}}2
mount ${{DISK}}2 /mnt
mkdir -p /mnt/boot/efi
mount ${{DISK}}1 /mnt/boot/efi
echo "==> Installing packages from {repository}..."
mkdir -p /mnt/var/db/xbps/keys
cp /var/db/xbps/keys/* /mnt/var/db/xbps/keys/
for attempt in 1 2 3; do
    XBPS_ARCH=x86_64 xbps-install -Sy -R {q(repository)} -r /mnt {" ".join(q(p) for p in packages)} && break
    [ $attempt = 3 ] && false
    echo "    xbps-install failed, retrying in 15 s ($attempt/3)"; sleep 15
done
for d in dev proc sys; do mount --rbind /$d /mnt/$d; done
cp /etc/resolv.conf /mnt/etc/resolv.conf
echo "==> Configuring the system..."
echo {q(hostname)} > /mnt/etc/hostname
ln -sf /usr/share/zoneinfo/{timezone} /mnt/etc/localtime
sed -i "s/^#KEYMAP=.*/KEYMAP={keymap}/; s/^KEYMAP=.*/KEYMAP={keymap}/" /mnt/etc/rc.conf
grep -q '^KEYMAP=' /mnt/etc/rc.conf || echo 'KEYMAP={keymap}' >> /mnt/etc/rc.conf
echo 'LANG={locale}' > /mnt/etc/locale.conf
sed -i "s/^#\\({locale.split('.')[0]}.*UTF-8\\)/\\1/" /mnt/etc/default/libc-locales || true
ROOT_UUID=$(blkid -s UUID -o value ${{DISK}}2)
EFI_UUID=$(blkid -s UUID -o value ${{DISK}}1)
printf 'UUID=%s / ext4 defaults 0 1\\nUUID=%s /boot/efi vfat defaults 0 2\\ntmpfs /tmp tmpfs defaults,nosuid,nodev 0 0\\n' "$ROOT_UUID" "$EFI_UUID" > /mnt/etc/fstab
chroot /mnt xbps-reconfigure -fa
echo "==> User {username}..."
chroot /mnt groupadd -r autologin || true
chroot /mnt useradd -m -G wheel,audio,video,autologin -s /bin/bash {q(username)}
chroot /mnt usermod -p {q(password_hash)} {q(username)}
chroot /mnt usermod -p {q(password_hash)} root
echo '%wheel ALL=(ALL:ALL) NOPASSWD: ALL' > /mnt/etc/sudoers.d/vmctl-wheel
chmod 440 /mnt/etc/sudoers.d/vmctl-wheel
install -d -m 700 /mnt/home/{username}/.ssh
cat > /mnt/home/{username}/.ssh/authorized_keys <<'EOF'
{keys_text}
EOF
chmod 600 /mnt/home/{username}/.ssh/authorized_keys
chroot /mnt chown -R {q(username)}: /home/{username}/.ssh
grep -q '^UseDNS' /mnt/etc/ssh/sshd_config && sed -i 's/^UseDNS.*/UseDNS no/' /mnt/etc/ssh/sshd_config || echo 'UseDNS no' >> /mnt/etc/ssh/sshd_config
echo "==> Services and the desktop session..."
for s in {" ".join(services)}; do [ -d /mnt/etc/sv/$s ] && ln -sf /etc/sv/$s /mnt/etc/runit/runsvdir/default/; done
if [ -f /mnt/etc/lightdm/lightdm.conf ]; then
    sed -i "s/^#autologin-user=.*/autologin-user={username}/; s/^#autologin-session=.*/autologin-session={session}/" /mnt/etc/lightdm/lightdm.conf
    grep -q '^autologin-user={username}' /mnt/etc/lightdm/lightdm.conf || printf '[Seat:*]\\nautologin-user={username}\\nautologin-session={session}\\n' >> /mnt/etc/lightdm/lightdm.conf
fi
echo "==> GRUB for EFI..."
chroot /mnt grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=void --removable
sed -i 's/^GRUB_CMDLINE_LINUX_DEFAULT=.*/GRUB_CMDLINE_LINUX_DEFAULT="loglevel=4 console=tty0 console=ttyS0,115200"/' /mnt/etc/default/grub
chroot /mnt grub-mkconfig -o /boot/grub/grub.cfg
echo "==> Finishing..."
sync
umount -R /mnt
sync
trap - ERR
echo "{BOOTSTRAP_COMPLETE_TOKEN}"
poweroff -f
"""


def create_seed_iso(vm_name: str, vm: dict[str, Any], keys: list[str], dry_run: bool = False) -> Path:
    return cloud_init.create_iso_with_files(void_artifact_dir(vm), {"install.sh": render_install_script(vm_name, vm, keys)},
                                            dry_run=dry_run, volume_id=SEED_VOLUME_ID)


def seed_iso_drive_args(iso_path: Path) -> list[str]:
    return ["-drive", f"file={iso_path},format=raw,if=ide,index=3,media=cdrom,readonly=on"]


def live_trigger_command() -> str:
    return (f"mkdir -p {SEED_MOUNTPOINT}; for d in /dev/sr1 /dev/sr0 /dev/sr2; do "
            f"mount -t iso9660 -o ro $d {SEED_MOUNTPOINT} 2>/dev/null && [ -f {SEED_MOUNTPOINT}/install.sh ] && break; "
            f"umount {SEED_MOUNTPOINT} 2>/dev/null; done; bash {SEED_MOUNTPOINT}/install.sh")


def extract_boot_artifacts(vm: dict[str, Any], iso_path: Path, dry_run: bool = False) -> tuple[Path, Path]:
    artifact_dir = iso.installer_artifact_dir(vm)
    kernel_path, initrd_path = artifact_dir / "vmlinuz", artifact_dir / "initrd"
    iso.extract_iso_member(iso_path, KERNEL_MEMBER, kernel_path, dry_run=dry_run)
    iso.extract_iso_member(iso_path, INITRD_MEMBER, initrd_path, dry_run=dry_run)
    return kernel_path, initrd_path


def resolve_ssh_pubkey(vm: dict[str, Any], dry_run: bool = False) -> list[str]:
    keys = cloud_init._authorized_keys_for_vm(vm, dry_run=dry_run)
    if not keys and not dry_run:
        raise VMError("the Void bootstrap needs the project SSH key (ssh_provision): none resolved")
    return keys
