"""``bootstrap-ubiquity``: Linux Mint (and any Ubiquity live medium) installed without a click.

Mint 22 still installs with Ubiquity on a casper live ISO, and Ubiquity has no autoinstall: what it
has is d-i style preseeding plus an *automatic* mode. The flow renders a seed (locale, keyboard,
timezone, whole-disk partitioning, the user with the profile's password hash, no summary page, no
codecs page) and a late script, grafts both into a per-VM copy of the vendor ISO under
``/preseed/`` (``xorriso -boot_image any keep``: a new session on the copy, nothing else moves) and
boots ``casper/vmlinuz`` + ``casper/initrd.lz`` straight from the ISO with
``boot=casper only-ubiquity automatic-ubiquity file=/cdrom/preseed/vmctl.seed``: casper applies the
seed with debconf-set-selections at boot, and ``only-ubiquity`` runs the installer instead of the
desktop. Ubiquity works on the framebuffer, so the serial console shows the kernel only; the late
script (``ubiquity/success_command``, run in the live system with ``/target`` still mounted)
reports on ttyS0 while it configures the installed system in a chroot: openssh-server and the
profile's packages from the mirror, the project SSH key, passwordless sudo, ``UseDNS no``, LightDM
autologin into the session, the ttyS0 getty, the profile's ``late_commands``. Then it unmounts
the target, ``sync``s, flushes the disk, prints the completion token and powers off: the token
comes after the flush, never before (see CLAUDE.md). A failed step prints the FAILED token instead.
"""
from __future__ import annotations

import hashlib
import shlex
import shutil
from pathlib import Path
from typing import Any

from vmctl import cloud_init, iso, runtime, ssh, ui
from vmctl.errors import VMError

BOOTSTRAP_COMPLETE_TOKEN = "==> Ubiquity install complete!"
BOOTSTRAP_FAILED_TOKEN = "==> Ubiquity install FAILED"
SEED_ISO_PATH = "/preseed/vmctl.seed"
LATE_ISO_PATH = "/preseed/vmctl-late.sh"
DEFAULT_INSTALLER_BOOT = {"kernel": "casper/vmlinuz", "initrd": "casper/initrd.lz"}
DEFAULT_PACKAGES = ["openssh-server"]


def ubiquity_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    cfg = vm.get("ubiquity_config")
    if cfg is None:
        return None
    if not isinstance(cfg, dict):
        raise VMError("ubiquity_config must be an object")
    return cfg


def artifact_dir(vm: dict[str, Any]) -> Path:
    return runtime.resolve_path(vm["disk"]["path"]).parent / "ubiquity"


def check_profile(vm_name: str, vm: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    cfg = ubiquity_config(vm)
    if cfg is None:
        return [f"{vm_name}: no ubiquity_config section"]
    if not cfg.get("username"):
        problems.append("ubiquity_config.username is required")
    if not cfg.get("password_hash") and not cfg.get("password"):
        problems.append("ubiquity_config.password_hash (or password) is required")
    if cloud_init.ssh_access_config(vm) is None:
        problems.append("ssh_provision is required: the post-install verifies the guest over SSH")
    if str((vm.get("disk") or {}).get("interface") or "virtio") != "virtio":
        problems.append("disk.interface must be virtio (the seed partitions /dev/vda)")
    return problems


def _resolve_ssh_pubkey(vm: dict[str, Any]) -> str | None:
    cfg = vm.get("ssh_provision")
    if not isinstance(cfg, dict):
        return None
    path = ssh.resolve_ssh_public_key(vm, cfg)
    if path is None or not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip()


def render_seed(vm_name: str, vm: dict[str, Any]) -> str:
    """The debconf answers Ubiquity needs to never ask: every page it would show, preseeded."""
    cfg = ubiquity_config(vm) or {}
    username = str(cfg["username"])
    hostname = str(cfg.get("hostname") or vm_name)
    locale = str(cfg.get("locale") or "en_US.UTF-8")
    language = str(cfg.get("language") or locale.split("_")[0])
    keymap = str(cfg.get("keyboard_layout") or "us")
    timezone = str(cfg.get("timezone") or "UTC")
    disk = str(cfg.get("disk_device") or "/dev/vda")
    lines = [
        "# Rendered by vmctl (bootstrap-ubiquity): applied by casper at boot, read by Ubiquity in automatic mode.",
        f"d-i debian-installer/locale string {locale}",
        f"d-i localechooser/languagelist select {language}",
        "d-i console-setup/ask_detect boolean false",
        f"d-i keyboard-configuration/layoutcode string {keymap}",
        f"d-i keyboard-configuration/xkb-keymap select {keymap}",
        f"d-i netcfg/get_hostname string {hostname}",
        f"d-i netcfg/get_domain string {cfg.get('domain') or 'local'}",
        f"d-i time/zone string {timezone}",
        "d-i clock-setup/utc boolean true",
        "d-i clock-setup/ntp boolean false",
        # Whole disk, one partition (+ the ESP partman adds under UEFI), swap as the recipe says.
        f"d-i partman-auto/disk string {disk}",
        "d-i partman-auto/method string regular",
        "d-i partman-lvm/device_remove_lvm boolean true",
        "d-i partman-md/device_remove_md boolean true",
        "d-i partman-auto/choose_recipe select atomic",
        "d-i partman-partitioning/confirm_write_new_label boolean true",
        "d-i partman/choose_partition select finish",
        "d-i partman/confirm boolean true",
        "d-i partman/confirm_nooverwrite boolean true",
        f"d-i passwd/user-fullname string {cfg.get('fullname') or username}",
        f"d-i passwd/username string {username}",
    ]
    if cfg.get("password_hash"):
        lines.append(f"d-i passwd/user-password-crypted password {cfg['password_hash']}")
    else:
        lines += [f"d-i passwd/user-password password {cfg['password']}",
                  f"d-i passwd/user-password-again password {cfg['password']}"]
    lines += [
        "d-i user-setup/allow-password-weak boolean true",
        "d-i user-setup/encrypt-home boolean false",
        "d-i grub-installer/only_debian boolean true",
        f"d-i grub-installer/bootdev string {disk}",
        # Ubiquity's own pages: no summary, no updates download, no codecs, and our script at the end.
        "ubiquity ubiquity/summary note",
        "ubiquity ubiquity/reboot boolean true",
        "ubiquity ubiquity/minimal_install boolean false",
        "ubiquity ubiquity/download_updates boolean false",
        "ubiquity ubiquity/use_nonfree boolean false",
        f"ubiquity ubiquity/success_command string sh /cdrom{LATE_ISO_PATH}",
    ]
    for extra in cfg.get("extra") or []:
        lines.append(str(extra))
    return "\n".join(lines) + "\n"


def render_late_script(vm_name: str, vm: dict[str, Any]) -> str:
    """``ubiquity/success_command``: runs as root in the live system, ``/target`` mounted and
    installed. Configures the target in a chroot, reports every step on ttyS0, and ends with
    umount → sync → flush → token → poweroff. A failed step prints the FAILED token and powers off."""
    cfg = ubiquity_config(vm) or {}
    user = str(cfg["username"])
    packages = list(cfg.get("packages") or DEFAULT_PACKAGES)
    if "openssh-server" not in packages:
        packages.insert(0, "openssh-server")
    session = str(cfg.get("session") or "cinnamon")
    autologin = cfg.get("autologin", True)
    pubkey = _resolve_ssh_pubkey(vm)
    disk = str(cfg.get("disk_device") or "/dev/vda")
    inner: list[str] = [
        "set -e",
        "export DEBIAN_FRONTEND=noninteractive",
        "apt-get update",
        f"apt-get install -y -o Dpkg::Options::=--force-confnew {' '.join(shlex.quote(p) for p in packages)}",
        f"install -d -m 700 -o {user} -g {user} /home/{user}/.ssh",
    ]
    if pubkey:
        inner += [f"printf '%s\\n' {shlex.quote(pubkey)} > /home/{user}/.ssh/authorized_keys",
                  f"chown {user}:{user} /home/{user}/.ssh/authorized_keys",
                  f"chmod 600 /home/{user}/.ssh/authorized_keys"]
    inner += [
        f"printf '%s ALL=(ALL) NOPASSWD: ALL\\n' {user} > /etc/sudoers.d/nopasswd-{user}",
        f"chmod 440 /etc/sudoers.d/nopasswd-{user}",
        f"usermod -aG sudo {user}",
        "grep -q '^UseDNS no' /etc/ssh/sshd_config || printf 'UseDNS no\\n' >> /etc/ssh/sshd_config",
        "systemctl enable ssh.service serial-getty@ttyS0.service",
        "systemctl set-default graphical.target",
    ]
    if autologin:
        inner += [
            "install -d /etc/lightdm/lightdm.conf.d",
            f"printf '[Seat:*]\\nautologin-user={user}\\nautologin-user-timeout=0\\nautologin-session={session}\\n' > /etc/lightdm/lightdm.conf.d/50-vmctl-autologin.conf",
        ]
    for command in cfg.get("late_commands") or []:
        inner.append(f"bash -lc {shlex.quote(str(command))}")
    inner_script = "\n".join(inner) + "\n"
    return f"""#!/bin/sh
# Rendered by vmctl for {vm_name}: ubiquity/success_command, run in the live system after the copy.
exec >/dev/ttyS0 2>&1
T=/target
log() {{ echo "vmctl: $*"; }}
fail() {{
    log "FAILED: $*"
    echo "{BOOTSTRAP_FAILED_TOKEN}: $*"
    sync
    poweroff -f
}}
[ -d "$T/etc" ] || fail "no installed system under /target"
log "configuring the installed system"
for d in dev dev/pts proc sys run; do mount --bind "/$d" "$T/$d" || fail "bind mount $d"; done
# The target's resolv.conf is a symlink for systemd-resolved: use the live one for apt, put it back after.
mv "$T/etc/resolv.conf" "$T/etc/resolv.conf.vmctl" 2>/dev/null || true
cp -L /etc/resolv.conf "$T/etc/resolv.conf" || true
cat > "$T/tmp/vmctl-target.sh" <<'VMCTL_TARGET'
{inner_script}VMCTL_TARGET
chroot "$T" /bin/sh /tmp/vmctl-target.sh || fail "post-install commands in the target (see above)"
rm -f "$T/tmp/vmctl-target.sh"
rm -f "$T/etc/resolv.conf"; mv "$T/etc/resolv.conf.vmctl" "$T/etc/resolv.conf" 2>/dev/null || true
for d in run sys proc dev/pts dev; do umount "$T/$d" 2>/dev/null || umount -l "$T/$d" 2>/dev/null || true; done
log "unmounting the target"
sync
umount -R "$T" 2>/dev/null || umount -l "$T" 2>/dev/null || true
sync
blockdev --flushbufs {disk} {disk}1 {disk}2 {disk}3 2>/dev/null || true
log "DONE"
echo "{BOOTSTRAP_COMPLETE_TOKEN}"
sleep 1
poweroff -f
"""


def kernel_append(vm: dict[str, Any]) -> str:
    """casper + Ubiquity in automatic mode, the seed from the medium, the kernel on the serial line.
    ``only-ubiquity`` runs the installer instead of the live desktop; ``noprompt`` skips casper's
    'remove the medium' question at shutdown."""
    cfg = ubiquity_config(vm) or {}
    locale = str(cfg.get("locale") or "en_US.UTF-8")
    keymap = str(cfg.get("keyboard_layout") or "us")
    return (f"boot=casper only-ubiquity automatic-ubiquity noprompt username=mint hostname=mint "
            f"file=/cdrom{SEED_ISO_PATH} debian-installer/locale={locale} keyboard-configuration/layoutcode={keymap} "
            f"console=ttyS0,115200n8")


def ensure_install_iso(vm_name: str, vm: dict[str, Any], source: Path, dry_run: bool = False) -> Path:
    """A copy of the vendor ISO with ``/preseed/vmctl.seed`` and ``/preseed/vmctl-late.sh`` added, cached
    by a stamp of the source and of both rendered files. The boot records stay as they are: the
    kernel is booted directly, the medium only has to be found by casper."""
    seed = render_seed(vm_name, vm)
    late = render_late_script(vm_name, vm)
    directory = artifact_dir(vm)
    dest = directory / "install.iso"
    stamp_path = dest.with_suffix(".iso.source")
    stamp = None
    if source.is_file():
        st = source.stat()
        stamp = (f"{source.resolve()}\n{st.st_size}\n{st.st_mtime_ns}\n"
                 + hashlib.sha256((seed + late).encode()).hexdigest())
    if stamp and dest.is_file() and stamp_path.is_file() and stamp_path.read_text() == stamp:
        ui.print_status("ok", f"Ubiquity unattended ISO: {ui.pretty_path(dest)}")
        return dest
    runtime.require_command("xorriso")
    work = directory / "iso-work"
    partial = dest.with_suffix(".iso.part")
    if not dry_run:
        work.mkdir(parents=True, exist_ok=True)
        (work / "vmctl.seed").write_text(seed, encoding="utf-8")
        (work / "vmctl.seed").chmod(0o600)  # the password hash travels in it
        (work / "vmctl-late.sh").write_text(late, encoding="utf-8")
    runtime.run(["cp", "--reflink=auto", str(source), str(partial)], dry_run=dry_run, quiet=True)
    runtime.run(["xorriso", "-boot_image", "any", "keep", "-dev", str(partial),
                 "-map", str(work / "vmctl.seed"), SEED_ISO_PATH,
                 "-map", str(work / "vmctl-late.sh"), LATE_ISO_PATH],
                dry_run=dry_run, quiet=True)
    if not dry_run:
        partial.replace(dest)
        shutil.rmtree(work)
        if stamp:
            stamp_path.write_text(stamp)
    ui.print_status("ok", f"Ubiquity unattended ISO: {ui.pretty_path(dest)}")
    return dest


def extract_boot_artifacts(vm: dict[str, Any], iso_path: Path, dry_run: bool = False) -> tuple[Path, Path]:
    """casper's kernel and initrd (``initrd.lz`` on Mint; ``installer_boot`` overrides)."""
    boot = {**DEFAULT_INSTALLER_BOOT, **(vm.get("installer_boot") or {})}
    return iso.extract_installer_boot_artifacts({**vm, "installer_boot": boot}, iso_path, dry_run=dry_run)
