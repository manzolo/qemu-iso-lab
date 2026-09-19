"""NixOS unattended install helpers: configuration.nix, install script, seed ISO.

NixOS has no answer file because it needs none: the installation *is* a
configuration. The flow boots the official installer ISO headless, and at the
live shell runs a script that partitions the disk, lets
``nixos-generate-config`` write the hardware part and drops the profile's own
``configuration.nix`` on top, then ``nixos-install``. Everything the guest ends
up with — user, SSH key, sudo rule, display manager, autologin, packages —
comes from that one file, so a desktop variant is a profile field, not code.

Two things are read from the medium instead of being pinned, because the ISO of
a channel is rebuilt continuously (``resolve_live_boot``): the kernel/initrd
paths under ``/boot/nix/store/...`` and the ``init=`` store path of the live
system, which change with every build, plus the ISO's own volume label, which
its ``root=LABEL=`` needs. They all live in the medium's ``isolinux.cfg``.

The live ISO logs a shell in on the serial console by itself (``nixos``, with
passwordless sudo), so the trigger only has to mount the seed and run it.
"""
from __future__ import annotations

import re
import shlex
import tempfile
from pathlib import Path
from typing import Any

from vmctl import cloud_init, iso, runtime, ssh
from vmctl.errors import VMError


BOOTSTRAP_COMPLETE_TOKEN = "==> NixOS installation complete!"
# Printed by the install script's ERR trap, right before poweroff.
BOOTSTRAP_FAILED_TOKEN = "==> NixOS installation FAILED"

# The installer ISO autologins the ``nixos`` user on every console it is given, so the
# serial console lands on a shell prompt with no login step. The bracket that opens that
# prompt is followed by the escape sequence setting the terminal title, so the literal
# "[nixos@nixos:~]$" never appears in the stream (the first run waited out its timeout on
# it, 2026-09-19): match the part that does.
NIXOS_LIVE_PROMPT = "nixos@nixos:~]$"

SEED_VOLUME_ID = "NIXSEED"
SEED_MOUNTPOINT = "/run/vmctl-seed"

LIVE_BOOT_CONFIG = "/isolinux/isolinux.cfg"

# Desktops the profile can ask for by name; anything else belongs in extra_config.
DESKTOPS = {
    "gnome": {
        "display_manager": "gdm",
        "enable": "services.xserver.desktopManager.gnome.enable = true;",
        "process": "gnome-shell",
    },
    "plasma": {
        "display_manager": "sddm",
        "enable": "services.desktopManager.plasma6.enable = true;",
        "process": "plasmashell",
    },
}


def nixos_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    cfg = vm.get("nixos_config")
    if cfg is None:
        return None
    if not isinstance(cfg, dict):
        raise VMError("Invalid nixos_config: expected object")
    return cfg


def nixos_artifact_dir(vm: dict[str, Any]) -> Path:
    return runtime.resolve_path(vm["disk"]["path"]).parent / "nixos"


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
        raise VMError("nixos_config.username is required")
    if not password_hash:
        raise VMError("nixos_config.password_hash is required (mkpasswd -m sha-512)")
    return username, password_hash


def resolve_live_boot(iso_path: Path, dry_run: bool = False) -> dict[str, str] | None:
    """Kernel, initrd, ``init=`` and volume label of the installer ISO, from its own config.

    Every NixOS ISO of a channel carries its kernel and initrd under
    ``/boot/nix/store/<hash>-.../`` and boots through the ``init`` of another
    store path; both hashes change with each rebuild, and ``root=LABEL=`` names
    the ISO's own label (``nixos-minimal-25.11-x86_64``). Pinning any of them in
    a profile would break at the next image. Returns None when the medium cannot
    be read (dry run, missing ISO, no xorriso), so the caller can fall back to
    the profile's ``installer_boot``.
    """
    if dry_run or not iso_path.is_file():
        return None
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "isolinux.cfg"
        try:
            runtime.run(
                ["xorriso", "-osirrox", "on", "-indev", str(iso_path), "-extract", LIVE_BOOT_CONFIG, str(dest)],
                quiet=True,
            )
            text = dest.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None
    return parse_live_boot(text)


def parse_live_boot(text: str) -> dict[str, str] | None:
    """Pull the first LABEL block's kernel, initrd, init= and root=LABEL= out of isolinux.cfg."""
    kernel = re.search(r"^\s*LINUX\s+(\S+)", text, re.MULTILINE)
    initrd = re.search(r"^\s*INITRD\s+(\S+)", text, re.MULTILINE)
    append = re.search(r"^\s*APPEND\s+(.*)$", text, re.MULTILINE)
    if not (kernel and initrd and append):
        return None
    init = re.search(r"init=(\S+)", append.group(1))
    label = re.search(r"root=LABEL=(\S+)", append.group(1))
    if not (init and label):
        return None
    # The ISO writes paths as /boot//nix/store/...; xorriso wants them collapsed.
    return {
        "kernel": re.sub(r"/{2,}", "/", kernel.group(1)).lstrip("/"),
        "initrd": re.sub(r"/{2,}", "/", initrd.group(1)).lstrip("/"),
        "init": init.group(1),
        "root_label": label.group(1),
    }


def live_kernel_append(live_boot: dict[str, str]) -> str:
    return (
        f"init={live_boot['init']} boot.shell_on_fail "
        f"root=LABEL={live_boot['root_label']} nohibernate loglevel=4 "
        "lsm=landlock,yama,bpf console=ttyS0,115200"
    )


def desktop_blocks(cfg: dict[str, Any], username: str) -> list[str]:
    """The display-manager, desktop and autologin lines for ``nixos_config.desktop``."""
    desktop = str(cfg.get("desktop") or "none").strip().lower()
    if desktop in ("", "none"):
        return []
    spec = DESKTOPS.get(desktop)
    if spec is None:
        raise VMError(f"Unsupported nixos_config.desktop: {desktop!r} (known: {', '.join(sorted(DESKTOPS))})")
    return [
        "  services.xserver.enable = true;",
        f"  services.displayManager.{spec['display_manager']}.enable = true;",
        f"  {spec['enable']}",
        "  services.displayManager.autoLogin = {",
        "    enable = true;",
        f"    user = {json_string(username)};",
        "  };",
        "  # Autologin races the tty1 getty; NixOS documents disabling it for this case.",
        '  systemd.services."getty@tty1".enable = false;',
        '  systemd.services."autovt@tty1".enable = false;',
    ]


def json_string(value: str) -> str:
    """Nix string literal: the same escaping rules as a JSON string for our inputs."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("${", "\\${")
    return f'"{escaped}"'


def render_configuration(vm_name: str, vm: dict[str, Any]) -> str:
    """Render the ``configuration.nix`` that defines the whole installed guest."""
    cfg = nixos_config(vm)
    if cfg is None:
        raise VMError("VM profile does not define nixos_config")
    username, password_hash = _require_identity(cfg)

    hostname = str(cfg.get("hostname") or vm_name).strip()
    timezone = str(cfg.get("timezone") or "UTC").strip()
    locale = str(cfg.get("locale") or "en_US.UTF-8").strip()
    keymap = str(cfg.get("keymap") or "us").strip()
    state_version = str(cfg.get("state_version") or "").strip()
    if not state_version:
        raise VMError("nixos_config.state_version is required (the channel's release, e.g. \"25.11\")")
    groups = [str(g) for g in (cfg.get("user_groups") or ["wheel", "networkmanager", "video", "audio"])]
    packages = [str(p) for p in (cfg.get("packages") or [])]
    extra_config = [str(line) for line in (cfg.get("extra_config") or [])]

    pubkey = _resolve_ssh_pubkey(vm)
    keys = f"[ {json_string(pubkey)} ]" if pubkey else "[ ]"

    lines = [
        "# Generated by vmctl: the whole guest is this file plus the generated hardware part.",
        "{ config, lib, pkgs, ... }:",
        "{",
        "  imports = [ ./hardware-configuration.nix ];",
        "",
        "  boot.loader.systemd-boot.enable = true;",
        "  boot.loader.efi.canTouchEfiVariables = true;",
        "  # Keep a serial console on the installed system: post-install-serial.log lives on it.",
        '  boot.kernelParams = [ "console=tty0" "console=ttyS0,115200" ];',
        "",
        f"  networking.hostName = {json_string(hostname)};",
        "  networking.useDHCP = lib.mkDefault true;",
        f"  time.timeZone = {json_string(timezone)};",
        f"  i18n.defaultLocale = {json_string(locale)};",
        f"  console.keyMap = {json_string(keymap)};",
        "",
        "  users.mutableUsers = false;",
        f"  users.users.{username} = {{",
        "    isNormalUser = true;",
        f"    description = {json_string(username)};",
        "    extraGroups = [ " + " ".join(json_string(g) for g in groups) + " ];",
        f"    hashedPassword = {json_string(password_hash)};",
        f"    openssh.authorizedKeys.keys = {keys};",
        "  };",
        "  security.sudo.wheelNeedsPassword = false;",
        "",
        "  services.openssh.enable = true;",
        '  services.openssh.settings.PermitRootLogin = "no";',
        "  services.qemuGuest.enable = true;",
    ]
    lines += desktop_blocks(cfg, username)
    if packages:
        lines += ["", "  environment.systemPackages = with pkgs; [ " + " ".join(packages) + " ];"]
    if extra_config:
        lines += [""] + [f"  {line}" for line in extra_config]
    lines += [
        "",
        f"  system.stateVersion = {json_string(state_version)};",
        "}",
        "",
    ]
    return "\n".join(lines)


def render_install_script(vm_name: str, vm: dict[str, Any]) -> str:
    """Render ``install.sh``: runs at the live shell through sudo, bash."""
    cfg = nixos_config(vm)
    if cfg is None:
        raise VMError("VM profile does not define nixos_config")
    _require_identity(cfg)
    disk_device = str(cfg.get("disk_device") or "vda").strip()
    install_args = [str(a) for a in (cfg.get("install_args") or [])]
    extra_install = (" " + " ".join(shlex.quote(a) for a in install_args)) if install_args else ""

    return f"""\
#!/usr/bin/env bash
# NixOS unattended install generated by vmctl for {vm_name}
set -Eeuo pipefail

# A failed step must not leave the live system sitting at its prompt: the host would
# otherwise learn of it only from its own timeout, as a bare "Timed out" with no cause.
trap 'echo "{BOOTSTRAP_FAILED_TOKEN}: line $LINENO: $BASH_COMMAND"; sleep 2; poweroff -f' ERR

SEED_DIR="$(dirname "$(readlink -f "$0")")"
DISK=/dev/{disk_device}
TARGET=/mnt

log() {{ echo "[vmctl-nixos] $*"; }}

log "Partitioning $DISK"
sgdisk --zap-all "$DISK"
sgdisk --new=1:0:+512MiB --typecode=1:ef00 --change-name=1:BOOT "$DISK"
sgdisk --new=2:0:0       --typecode=2:8300 --change-name=2:ROOT "$DISK"
partprobe "$DISK"
sleep 2

log "Formatting"
mkfs.fat -F32 -n BOOT "$DISK"1
mkfs.ext4 -L nixos -F "$DISK"2
# The by-label symlinks appear only once udev has processed the new filesystems;
# mounting by device path does not have to wait for them (a by-label mount lost
# that race on the first live run, 2026-09-19).
udevadm settle || true
mount "$DISK"2 "$TARGET"
mkdir -p "$TARGET/boot"
mount "$DISK"1 "$TARGET/boot"

log "Generating the hardware configuration"
nixos-generate-config --root "$TARGET"
install -m 644 "$SEED_DIR/configuration.nix" "$TARGET/etc/nixos/configuration.nix"

log "nixos-install (packages come from the binary cache)"
nixos-install --no-root-passwd --root "$TARGET"{extra_install}

log "Flushing"
sync
umount -R "$TARGET"
blockdev --flushbufs "$DISK" || true
sync
echo "{BOOTSTRAP_COMPLETE_TOKEN}"
poweroff -f
"""


def create_nixos_seed_iso(vm_name: str, vm: dict[str, Any], dry_run: bool = False) -> Path:
    """Pack configuration.nix + install.sh into the seed ISO the live trigger mounts."""
    artifact_dir = nixos_artifact_dir(vm)
    return cloud_init.create_iso_with_files(
        artifact_dir,
        {
            "configuration.nix": render_configuration(vm_name, vm),
            "install.sh": render_install_script(vm_name, vm),
        },
        dry_run=dry_run,
        volume_id=SEED_VOLUME_ID,
    )


def seed_iso_drive_args(iso_path: Path) -> list[str]:
    return ["-drive", f"file={iso_path},format=raw,if=virtio,media=cdrom,readonly=on"]


def live_trigger_command() -> str:
    """What run_and_expect types at the live shell: mount the seed and run it as root."""
    return (
        f"sudo mkdir -p {SEED_MOUNTPOINT} && sudo mount -t iso9660 /dev/vdb {SEED_MOUNTPOINT} "
        f"&& sudo bash {SEED_MOUNTPOINT}/install.sh"
    )


def extract_nixos_boot_artifacts(
    vm: dict[str, Any], iso_path: Path, live_boot: dict[str, str] | None, dry_run: bool = False
) -> tuple[Path, Path]:
    """Extract the live kernel/initrd named by the medium (or by ``installer_boot``)."""
    boot = vm.get("installer_boot", {})
    kernel_member = str((live_boot or {}).get("kernel") or boot.get("kernel") or "")
    initrd_member = str((live_boot or {}).get("initrd") or boot.get("initrd") or "")
    if not kernel_member or not initrd_member:
        raise VMError(
            "Cannot tell which kernel to boot: the ISO's isolinux.cfg was unreadable and the "
            "profile declares no installer_boot"
        )
    artifact_dir = iso.installer_artifact_dir(vm)
    kernel_path = artifact_dir / "vmlinuz"
    initrd_path = artifact_dir / "initrd"
    iso.extract_iso_member(iso_path, kernel_member, kernel_path, dry_run=dry_run)
    iso.extract_iso_member(iso_path, initrd_member, initrd_path, dry_run=dry_run)
    return kernel_path, initrd_path


def check_profile(vm_name: str, vm: dict[str, Any]) -> list[str]:
    """Profile requirements this flow depends on, as human-readable problems."""
    problems: list[str] = []
    cfg = nixos_config(vm)
    if cfg is None:
        return [f"VM '{vm_name}' does not define nixos_config"]
    try:
        _require_identity(cfg)
        render_configuration(vm_name, vm)
    except VMError as exc:
        problems.append(str(exc))
    firmware = (vm.get("firmware") or {}).get("type")
    if firmware != "efi":
        # The generated configuration installs systemd-boot on an ESP.
        problems.append(f"the generated configuration boots with systemd-boot: firmware.type must be 'efi', not {firmware!r}")
    interface = (vm.get("disk") or {}).get("interface")
    if interface != "virtio":
        problems.append(f"disk.interface must be 'virtio' (the script partitions /dev/vda), not {interface!r}")
    return problems
