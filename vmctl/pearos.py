"""pearOS NiceC0re unattended install helpers: seed ISO and the install script.

pearOS is an Arch-based, macOS-like Plasma 6 desktop shipped as a plain archiso
live image, and its installer is **Calamares in unpackfs mode**: it does not
pacstrap a package list, it copies the live squashfs onto the disk and then
configures it. There is no answer file for Calamares, so this flow reproduces
what its own configuration does (read from the ``pear-calamares-config``
package and its ``/usr/local/bin/alg-*`` helpers) in one script:

1. GPT ``EFI`` + ``ROOT`` on the profile's disk, fat32 + ext4 (its
   ``partition.conf`` defaults).
2. ``unsquashfs`` of ``/run/archiso/bootmnt/arch/x86_64/airootfs.sfs`` onto the
   root, plus the ISO's kernel as ``/boot/vmlinuz-linux-cachyos-lts`` — exactly
   the two entries of its ``unpackfs.conf``.
3. fstab, machine-id, locale, keymap, timezone.
4. The archiso hooks leave ``mkinitcpio.conf`` first, then the live-only
   packages go, then the initramfs is built. In any other order the rebuild
   that removing ``mkinitcpio-archiso`` fires ends in "Hook 'archiso' cannot be
   found ... the image may not be complete" and overwrites a good initramfs
   (verified live on 2026-09-19).
5. The user, sudoers, SDDM autologin and services. Their Calamares creates a
   throwaway ``default`` user and leaves the real one to a first-boot OOBE
   script (``/usr/local/bin/post_setup``, started from an autostart entry);
   this flow creates the profile's user directly and drops that autostart
   entry, so the installed guest is ready without a first-boot wizard.
6. GRUB, then sync -> flush -> completion token -> poweroff (see CLAUDE.md:
   the token never precedes the flush).
"""
from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from vmctl import archinstall, cloud_init, iso, runtime, ssh
from vmctl.errors import VMError


BOOTSTRAP_COMPLETE_TOKEN = "==> pearOS NiceC0re installation complete!"
# Printed by the install script's ERR trap, right before poweroff.
BOOTSTRAP_FAILED_TOKEN = "==> pearOS NiceC0re installation FAILED"

# Serial console prompts of the live ISO. The hostname is the one its archiso
# profile ships in /etc/hostname; root has no password and its shell is zsh.
PEAROS_SERIAL_LOGIN_PROMPT = "pearOS-Live-System login:"
PEAROS_LIVE_PROMPT = "root@pearOS-Live-System"

SEED_VOLUME_ID = "PEARSEED"
SEED_MOUNTPOINT = "/run/vmctl-seed"

# The live session autologins into SDDM; the install is driven over ttyS0, so it
# boots to multi-user instead (the same trick the CachyOS profiles use).
LIVE_KERNEL_APPEND = (
    "archisobasedir=arch archisolabel={label} console=ttyS0,115200 "
    "systemd.unit=multi-user.target"
)

# What their Calamares removes from the installed system (packages.conf).
DEFAULT_REMOVE_PACKAGES = [
    "calamares",
    "hwinfo",
    "squashfs-tools",
    "mkinitcpio-archiso",
    "arch-install-scripts",
    "ckbcomp",
    "pearos-livecd-desktop",
]

# Services the installed guest needs: the desktop, the network and the two ways
# in (SSH for provisioning, ttyS0 so post-install-serial.log keeps saying something).
DEFAULT_SERVICES = ["sddm", "NetworkManager", "sshd", "serial-getty@ttyS0.service"]


def pearos_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    cfg = vm.get("pearos_config")
    if cfg is None:
        return None
    if not isinstance(cfg, dict):
        raise VMError("Invalid pearos_config: expected object")
    return cfg


def pearos_artifact_dir(vm: dict[str, Any]) -> Path:
    return runtime.resolve_path(vm["disk"]["path"]).parent / "pearos"


def _resolve_ssh_pubkey(vm: dict[str, Any]) -> str | None:
    ssh_cfg = vm.get("ssh_provision")
    if not isinstance(ssh_cfg, dict):
        return None
    pubkey = ssh.resolve_ssh_public_key(vm, ssh_cfg)
    if pubkey is None:
        return None
    return pubkey.read_text(encoding="utf-8").strip()


def _require_identity(cfg: dict[str, Any]) -> tuple[str, str]:
    username = str(cfg.get("username") or "").strip()
    password_hash = str(cfg.get("password_hash") or "").strip()
    if not username:
        raise VMError("pearos_config.username is required")
    if not password_hash:
        raise VMError("pearos_config.password_hash is required (openssl passwd -6)")
    return username, password_hash


def live_kernel_append(iso_label: str) -> str:
    return LIVE_KERNEL_APPEND.format(label=iso_label)


def pearos_iso_label(iso_path: Path) -> str:
    """Volume label of the live ISO (``pearOS_NiceC0re_YYYYMM``).

    The build is monthly and the label carries its month, so it is read from the
    medium rather than pinned; ``archinstall.arch_iso_label`` already does that
    with blkid and only its Arch-specific fallbacks do not apply here.
    """
    label = archinstall.arch_iso_label(iso_path)
    if label.startswith("ARCH_"):
        return "pearOS_NiceC0re"
    return label


def render_install_script(vm_name: str, vm: dict[str, Any]) -> str:
    """Render ``install.sh``: runs as root in the live environment, bash."""
    cfg = pearos_config(vm)
    if cfg is None:
        raise VMError("VM profile does not define pearos_config")
    username, password_hash = _require_identity(cfg)

    disk_device = str(cfg.get("disk_device") or "vda").strip()
    hostname = str(cfg.get("hostname") or vm_name).strip()
    timezone = str(cfg.get("timezone") or "UTC").strip()
    locale = str(cfg.get("locale") or "en_US.UTF-8").strip()
    keymap = str(cfg.get("keymap") or "us").strip()
    session = str(cfg.get("session") or "plasma").strip()
    user_groups = ",".join(
        str(g) for g in (cfg.get("user_groups") or ["wheel", "audio", "video", "storage", "network", "power", "lp"])
    )
    remove_packages = [str(p) for p in (cfg.get("remove_packages") or DEFAULT_REMOVE_PACKAGES)]
    services = [str(s) for s in (cfg.get("services") or DEFAULT_SERVICES)]
    chroot_commands = [str(c) for c in (cfg.get("chroot_commands") or [])]

    pubkey = _resolve_ssh_pubkey(vm)
    ssh_block = "true"
    if pubkey:
        ssh_block = f"""install -d -m 700 "$TARGET/home/{username}/.ssh"
printf '%s\\n' {shlex.quote(pubkey)} > "$TARGET/home/{username}/.ssh/authorized_keys"
chmod 600 "$TARGET/home/{username}/.ssh/authorized_keys"
arch-chroot "$TARGET" chown -R {shlex.quote(username)}:users /home/{username}/.ssh"""

    remove_block = "true"
    if remove_packages:
        remove_block = (
            'arch-chroot "$TARGET" pacman -R --noconfirm '
            + " ".join(shlex.quote(p) for p in remove_packages)
            + " 2>/dev/null || true"
        )
    services_block = (
        'arch-chroot "$TARGET" systemctl enable ' + " ".join(shlex.quote(s) for s in services)
        if services
        else "true"
    )
    custom_block = "\n".join(chroot_commands) if chroot_commands else "true"

    return f"""\
#!/usr/bin/env bash
# pearOS NiceC0re unattended install generated by vmctl for {vm_name}
set -Eeuo pipefail

# A failed step must not leave the live system sitting at its prompt: the host would
# otherwise learn of it only from its own timeout, as a bare "Timed out" with no cause.
trap 'echo "{BOOTSTRAP_FAILED_TOKEN}: line $LINENO: $BASH_COMMAND"; sleep 2; poweroff -f' ERR

DISK=/dev/{disk_device}
TARGET=/mnt
SFS=/run/archiso/bootmnt/arch/x86_64/airootfs.sfs
KERNEL_SRC=/run/archiso/bootmnt/arch/boot/x86_64/vmlinuz-linux

log() {{ echo "[vmctl-pearos] $*"; }}

log "Partitioning $DISK"
sgdisk --zap-all "$DISK"
sgdisk --new=1:0:+512MiB --typecode=1:ef00 --change-name=1:EFI "$DISK"
sgdisk --new=2:0:0       --typecode=2:8300 --change-name=2:ROOT "$DISK"
partprobe "$DISK"
sleep 2

log "Formatting"
mkfs.fat -F32 -n EFI "$DISK"1
mkfs.ext4 -L ROOT -F "$DISK"2
mount "$DISK"2 "$TARGET"
mkdir -p "$TARGET/boot/efi"
mount "$DISK"1 "$TARGET/boot/efi"

log "Unpacking the live squashfs (their Calamares unpackfs module)"
[ -f "$SFS" ] || {{ log "ERROR: $SFS not found on the live medium"; exit 1; }}
unsquashfs -f -d "$TARGET" "$SFS"
install -Dm644 "$KERNEL_SRC" "$TARGET/boot/vmlinuz-linux-cachyos-lts"

log "Base configuration"
genfstab -U "$TARGET" >> "$TARGET/etc/fstab"
echo {shlex.quote(hostname)} > "$TARGET/etc/hostname"
ln -sf /usr/share/zoneinfo/{shlex.quote(timezone)} "$TARGET/etc/localtime"
sed -i 's/^#\\({locale}\\)/\\1/' "$TARGET/etc/locale.gen"
echo "LANG={locale}" > "$TARGET/etc/locale.conf"
echo "KEYMAP={keymap}" > "$TARGET/etc/vconsole.conf"
rm -f "$TARGET/etc/machine-id"
arch-chroot "$TARGET" systemd-machine-id-setup
arch-chroot "$TARGET" locale-gen
arch-chroot "$TARGET" hwclock --systohc --utc || true

log "Kernel and initramfs (their alg-preset)"
rm -f "$TARGET/etc/mkinitcpio.d/alg" "$TARGET/etc/mkinitcpio.d/linux.preset"
rm -f "$TARGET/boot/vmlinuz-linux" "$TARGET/boot/initramfs-linux.img" "$TARGET/boot/initramfs-linux-fallback.img"
# The archiso hooks leave the installed system with mkinitcpio.conf first: removing
# mkinitcpio-archiso just below fires a post-transaction rebuild, and with those hooks
# still listed that rebuild ends in "Hook 'archiso' cannot be found ... the image may not
# be complete" and overwrites a good initramfs (verified live 2026-09-19).
sed -i -E 's/\\s*archiso[a-z_]*//g' "$TARGET/etc/mkinitcpio.conf"

log "Removing the live-only packages"
{remove_block}

arch-chroot "$TARGET" mkinitcpio -P

log "User {username}"
arch-chroot "$TARGET" userdel -rf liveuser 2>/dev/null || true
rm -rf "$TARGET/home/liveuser"
arch-chroot "$TARGET" useradd -m -g users -G {shlex.quote(user_groups)} -s /bin/bash {shlex.quote(username)}
echo {shlex.quote(f"{username}:{password_hash}")} | arch-chroot "$TARGET" chpasswd -e
sed -i 's/^# %wheel ALL=(ALL) ALL$/%wheel ALL=(ALL) ALL/' "$TARGET/etc/sudoers"
printf '%s ALL=(ALL) NOPASSWD: ALL\\n' {shlex.quote(username)} > "$TARGET/etc/sudoers.d/99-vmctl"
chmod 440 "$TARGET/etc/sudoers.d/99-vmctl"
# Their first-boot OOBE (post_setup) would create its own user and reboot: this flow
# already did its job, so the autostart entry that launches it goes away.
rm -f "$TARGET/etc/skel/.config/autostart/xyz.pearos-post-install.desktop"
rm -f "$TARGET"/home/*/.config/autostart/xyz.pearos-post-install.desktop
{ssh_block}

log "Display manager autologin"
printf '[Autologin]\\nUser=%s\\nSession=%s\\n\\n[Theme]\\nCurrent=pearOS\\n' {shlex.quote(username)} {shlex.quote(session)} > "$TARGET/etc/sddm.conf"
rm -f "$TARGET/etc/sddm.conf.d/autologin.conf"
arch-chroot "$TARGET" systemctl set-default graphical.target
{services_block}

log "Stripping the live session (their alg-finalisation)"
rm -f "$TARGET/etc/sudoers.d/g_wheel" "$TARGET/etc/polkit-1/rules.d/49-nopasswd_global.rules"
rm -f "$TARGET/root/.automated_script.sh" "$TARGET/root/.zlogin"
rm -f "$TARGET/etc/systemd/system/multi-user.target.wants/pacman-init.service"
rm -f "$TARGET/etc/systemd/system/pacman-init.service" "$TARGET/etc/systemd/system/etc-pacman.d-gnupg.mount"
rm -f "$TARGET/etc/systemd/system/getty@tty1.service.d/autologin.conf"
rm -f "$TARGET/etc/systemd/system/multi-user.target.wants/choose-mirror.service"
rm -f "$TARGET/etc/systemd/system/multi-user.target.wants/reflector.service"
rm -f "$TARGET/etc/systemd/system/multi-user.target.wants/livecd-talk.service"
rm -f "$TARGET/etc/systemd/system/choose-mirror.service" "$TARGET/etc/systemd/system/livecd-talk.service"
rm -f "$TARGET/etc/systemd/system/livecd-alsa-unmuter.service"
rm -f "$TARGET/etc/systemd/system/sound.target.wants/livecd-alsa-unmuter.service"
rm -rf "$TARGET/etc/systemd/system/cloud-init.target.wants"
rm -rf "$TARGET/etc/pacman.d/gnupg"
rm -f "$TARGET"/usr/local/bin/{{alg-finalisation,alg-preset,livecd-sound,Installation_guide,choose-mirror,bin_install}}
rm -f "$TARGET/usr/share/applications/calamares.desktop"
rm -f "$TARGET"/home/*/Desktop/calamares.desktop "$TARGET/etc/skel/Desktop/calamares.desktop"
arch-chroot "$TARGET" pacman-key --init
arch-chroot "$TARGET" pacman-key --populate
sed -i 's/GRUB_DISTRIBUTOR=.*/GRUB_DISTRIBUTOR="pearOS NiceC0re"/' "$TARGET/etc/default/grub"

log "Guest customization"
{custom_block}

log "Bootloader"
arch-chroot "$TARGET" grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=pearOS --removable
arch-chroot "$TARGET" grub-mkconfig -o /boot/grub/grub.cfg

log "Flushing"
sync
umount -R "$TARGET"
blockdev --flushbufs "$DISK" || true
sync
echo "{BOOTSTRAP_COMPLETE_TOKEN}"
poweroff -f
"""


def create_pearos_seed_iso(vm_name: str, vm: dict[str, Any], dry_run: bool = False) -> Path:
    """Pack install.sh into the seed ISO that the live trigger mounts."""
    artifact_dir = pearos_artifact_dir(vm)
    return cloud_init.create_iso_with_files(
        artifact_dir,
        {"install.sh": render_install_script(vm_name, vm)},
        dry_run=dry_run,
        volume_id=SEED_VOLUME_ID,
    )


def seed_iso_drive_args(iso_path: Path) -> list[str]:
    return ["-drive", f"file={iso_path},format=raw,if=virtio,media=cdrom,readonly=on"]


def live_trigger_command() -> str:
    """What run_and_expect types at the live root prompt: mount the seed, run it."""
    return (
        f"mkdir -p {SEED_MOUNTPOINT} && mount -t iso9660 /dev/vdb {SEED_MOUNTPOINT} "
        f"&& bash {SEED_MOUNTPOINT}/install.sh"
    )


def extract_pearos_boot_artifacts(vm: dict[str, Any], iso_path: Path, dry_run: bool = False) -> tuple[Path, Path]:
    """Extract the live kernel/initramfs (plain archiso paths unless the profile says otherwise)."""
    boot = vm.get("installer_boot", {})
    kernel_member = str(boot.get("kernel") or "arch/boot/x86_64/vmlinuz-linux")
    initrd_member = str(boot.get("initrd") or "arch/boot/x86_64/initramfs-linux.img")
    artifact_dir = iso.installer_artifact_dir(vm)
    kernel_path = artifact_dir / "vmlinuz"
    initrd_path = artifact_dir / "initrd"
    iso.extract_iso_member(iso_path, kernel_member, kernel_path, dry_run=dry_run)
    iso.extract_iso_member(iso_path, initrd_member, initrd_path, dry_run=dry_run)
    return kernel_path, initrd_path


def check_profile(vm_name: str, vm: dict[str, Any]) -> list[str]:
    """Profile requirements this flow depends on; returned as human-readable problems."""
    problems: list[str] = []
    cfg = pearos_config(vm)
    if cfg is None:
        return [f"VM '{vm_name}' does not define pearos_config"]
    try:
        _require_identity(cfg)
    except VMError as exc:
        problems.append(str(exc))
    firmware = (vm.get("firmware") or {}).get("type")
    if firmware != "efi":
        # The script installs GRUB with --target=x86_64-efi onto an ESP.
        problems.append(f"pearOS installs in UEFI mode: firmware.type must be 'efi', not {firmware!r}")
    interface = (vm.get("disk") or {}).get("interface")
    if interface != "virtio":
        problems.append(f"disk.interface must be 'virtio' (the script partitions /dev/vda), not {interface!r}")
    return problems
