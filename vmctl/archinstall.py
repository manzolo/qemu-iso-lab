"""Arch Linux install helpers: config generation, bootstrap script, ISO packing."""
from __future__ import annotations

import json
import re
import subprocess
import shlex
import shutil
from pathlib import Path
from typing import Any

from vmctl import arch_archive, runtime, ssh, ui
from vmctl.errors import VMError


def _resolve_ssh_pubkey(vm: dict[str, Any]) -> str | None:
    ssh_cfg = vm.get("ssh_provision")
    if not isinstance(ssh_cfg, dict):
        return None
    pubkey = ssh.resolve_ssh_public_key(vm, ssh_cfg)
    if pubkey is None:
        return None
    return pubkey.read_text(encoding="utf-8").strip()


_RUN_SH = """\
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
echo "==> archinstall pre-filled config"
echo "    Config: $SCRIPT_DIR/archinstall-config.json"
echo "    Creds:  $SCRIPT_DIR/archinstall-creds.json"
echo ""
archinstall \\
    --config "$SCRIPT_DIR/archinstall-config.json" \\
    --creds  "$SCRIPT_DIR/archinstall-creds.json"
"""

# Sentinel printed at end of bootstrap install — run_and_expect waits for this.
BOOTSTRAP_COMPLETE_TOKEN = "==> Arch Linux installation complete!"
# Printed by the install script's ERR trap right before it powers the live system off.
BOOTSTRAP_FAILED_TOKEN = "==> Arch Linux installation FAILED"

# Login prompt on the serial console (autologin is only on tty1, not ttyS0).
ARCH_SERIAL_LOGIN_PROMPT = "archiso login:"

# Shell prompt after root login on the serial console.
ARCH_LIVE_PROMPT = "root@archiso"

# Kernel arguments every archiso live system needs to find its squashfs and
# talk on the serial console; the ISO label is filled in at boot time.
LIVE_KERNEL_APPEND = "archisobasedir=arch archisolabel={label} console=ttyS0,115200 quiet"


def archinstall_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    cfg = vm.get("archinstall_config")
    if cfg is None:
        return None
    if not isinstance(cfg, dict):
        raise VMError("Invalid archinstall_config: expected object")
    return cfg


def archinstall_artifact_dir(vm: dict[str, Any]) -> Path:
    return runtime.resolve_path(vm["disk"]["path"]).parent / "archinstall"


def live_prompts(vm: dict[str, Any]) -> tuple[str, str]:
    """(login prompt, shell prompt) of the live ISO on the serial console.

    Arch derivatives built with archiso keep the same boot mechanics but a
    different hostname, so the prompts differ: CachyOS shows ``CachyOS login:``
    and ``[root@CachyOS ~]#``. Profiles override them with
    ``archinstall_config.live_login_prompt`` / ``live_shell_prompt``.
    """
    cfg = archinstall_config(vm) or {}
    login = str(cfg.get("live_login_prompt") or ARCH_SERIAL_LOGIN_PROMPT).strip()
    shell = str(cfg.get("live_shell_prompt") or ARCH_LIVE_PROMPT).strip()
    if not login or not shell:
        raise VMError("archinstall_config.live_login_prompt / live_shell_prompt must not be empty")
    return login, shell


def live_kernel_append(vm: dict[str, Any], iso_label: str) -> str:
    """Kernel command line for the live ISO; ``live_kernel_append`` adds to it.

    CachyOS uses it for ``systemd.unit=multi-user.target`` so the live Plasma
    session and Calamares never start while pacstrap runs headless.
    """
    cfg = archinstall_config(vm) or {}
    extra = str(cfg.get("live_kernel_append") or "").strip()
    append = LIVE_KERNEL_APPEND.format(label=iso_label)
    return f"{append} {extra}" if extra else append


# ---------------------------------------------------------------------------
# Interactive install helpers (install-archinstall command)
# ---------------------------------------------------------------------------

def render_archinstall_config(vm_name: str, vm: dict[str, Any]) -> str:
    cfg = archinstall_config(vm)
    if cfg is None:
        raise VMError("VM profile does not define archinstall_config")

    hostname = str(cfg.get("hostname") or vm_name).strip()
    timezone = str(cfg.get("timezone") or "UTC").strip()
    kb_layout = str(cfg.get("keyboard_layout") or "us").strip()
    locale_lang = str(cfg.get("locale_lang") or "en_US").strip()
    locale_enc = str(cfg.get("locale_enc") or "UTF-8").strip()
    kernels: list[str] = list(cfg.get("kernels") or ["linux"])
    bootloader = str(cfg.get("bootloader") or "Grub").strip()
    language = str(cfg.get("language") or "English").strip()
    packages: list[str] = list(cfg.get("packages") or [])
    services: list[str] = list(cfg.get("services") or [])
    audio = bool(cfg.get("audio", False))

    base_packages = {"base-devel", "git", "openssh", "networkmanager"}
    all_packages = sorted(base_packages | set(packages))

    base_services = {"sshd", "NetworkManager"}
    all_services = sorted(base_services | set(services))

    custom_commands: list[str] = list(cfg.get("custom_commands") or [])
    if not any("NOPASSWD" in c for c in custom_commands):
        custom_commands.insert(0, "echo '%wheel ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/nopasswd-wheel")

    payload: dict[str, Any] = {
        "archinstall-language": language,
        "bootloader": bootloader,
        "custom-commands": custom_commands,
        "hostname": hostname,
        "kernels": kernels,
        "locale_config": {
            "kb_layout": kb_layout,
            "sys_enc": locale_enc,
            "sys_lang": locale_lang,
        },
        "network_config": {"type": "nm"},
        "ntp": True,
        "packages": all_packages,
        "services": all_services,
        "swap": False,
        "timezone": timezone,
    }
    if audio:
        payload["audio_config"] = {"audio": "pipewire"}

    return json.dumps(payload, indent=2) + "\n"


def render_archinstall_creds(vm: dict[str, Any]) -> str:
    cfg = archinstall_config(vm)
    if cfg is None:
        raise VMError("VM profile does not define archinstall_config")

    username = str(cfg.get("username") or "").strip()
    password = str(cfg.get("password") or "").strip()
    if not username:
        raise VMError("archinstall_config.username is required")
    if not password:
        raise VMError("archinstall_config.password is required")

    creds: dict[str, Any] = {
        "!users": [
            {
                "!password": password,
                "groups": ["wheel"],
                "sudo": True,
                "username": username,
            }
        ],
        "!root-password": None,
    }
    return json.dumps(creds, indent=2) + "\n"


def _iso_builder_cmd(out_path: Path, files: list[Path], volid: str = "ARCHCONF",
                     grafts: dict[str, Path] | None = None) -> list[str]:
    str_files = [str(f) for f in files]
    if grafts:  # directories under a name of their own (the seed's pkg/ cache)
        str_files = ["-graft-points", *str_files, *(f"{name}={path}" for name, path in grafts.items())]
    if shutil.which("xorriso"):
        return ["xorriso", "-as", "mkisofs", "-output", str(out_path),
                "-volid", volid, "-joliet", "-rock"] + str_files
    if shutil.which("genisoimage"):
        return ["genisoimage", "-output", str(out_path),
                "-volid", volid, "-joliet", "-rock"] + str_files
    raise VMError("Missing ISO builder: install xorriso or genisoimage")


def create_config_iso(vm_name: str, vm: dict[str, Any], dry_run: bool = False) -> Path:
    """Create an ISO with the archinstall JSON config + run.sh for interactive install."""
    artifact_dir = archinstall_artifact_dir(vm)
    runtime.ensure_parent(artifact_dir / "placeholder")

    config_path = artifact_dir / "archinstall-config.json"
    creds_path = artifact_dir / "archinstall-creds.json"
    run_path = artifact_dir / "run.sh"
    iso_path = artifact_dir / "archinstall-config.iso"

    if not dry_run:
        config_path.write_text(render_archinstall_config(vm_name, vm), encoding="utf-8")
        creds_path.write_text(render_archinstall_creds(vm), encoding="utf-8")
        run_path.write_text(_RUN_SH, encoding="utf-8")
        run_path.chmod(0o755)
        ui.print_status("ok", f"Config: {ui.pretty_path(config_path)}")
        ui.print_status("ok", f"Creds:  {ui.pretty_path(creds_path)}")

    runtime.run(_iso_builder_cmd(iso_path, [config_path, creds_path, run_path]), dry_run=dry_run)
    return iso_path


def config_iso_drive_args(iso_path: Path) -> list[str]:
    return ["-drive", f"file={iso_path},format=raw,if=virtio,media=cdrom,readonly=on"]


# ---------------------------------------------------------------------------
# Automated bootstrap helpers (bootstrap-archinstall command)
# ---------------------------------------------------------------------------

def render_bootstrap_script(vm_name: str, vm: dict[str, Any]) -> str:
    """Generate a self-contained bash script that installs Arch via pacstrap."""
    cfg = archinstall_config(vm)
    if cfg is None:
        raise VMError("VM profile does not define archinstall_config")

    hostname = str(cfg.get("hostname") or vm_name).strip()
    username = str(cfg.get("username") or "").strip()
    password = str(cfg.get("password") or "").strip()
    timezone = str(cfg.get("timezone") or "UTC").strip()
    kb_layout = str(cfg.get("keyboard_layout") or "us").strip()
    locale_lang = str(cfg.get("locale_lang") or "en_US").strip()
    locale_enc = str(cfg.get("locale_enc") or "UTF-8").strip()
    kernels: list[str] = list(cfg.get("kernels") or ["linux"])
    bootloader = str(cfg.get("bootloader") or "Grub").strip().lower()
    packages: list[str] = list(cfg.get("packages") or [])
    services: list[str] = list(cfg.get("services") or [])
    bootstrap_chroot_commands: list[str] = list(cfg.get("bootstrap_chroot_commands") or [])
    inherit_live_pacman_conf = bool(cfg.get("inherit_live_pacman_conf", False))
    archive_date = str(cfg.get("archive_date") or "").strip()
    insecure_tls = bool(cfg.get("archive_insecure_tls", False))
    if archive_date and not re.fullmatch(r"\d{4}/\d{2}/\d{2}", archive_date):
        raise VMError(f"archinstall_config.archive_date must be YYYY/MM/DD, not {archive_date!r}")

    if not username:
        raise VMError("archinstall_config.username is required for bootstrap")
    if not password:
        raise VMError("archinstall_config.password is required for bootstrap")

    package_set = {"base", "base-devel", "git", "linux-firmware", "networkmanager", "openssh", *kernels}
    service_set = {"NetworkManager", "sshd", *services}

    if bootloader == "grub":
        package_set.update({"efibootmgr", "grub"})

    package_set.update(packages)
    package_line = " ".join(sorted(package_set))
    services_line = " ".join(sorted(service_set))

    pubkey = _resolve_ssh_pubkey(vm)
    if pubkey:
        pubkey_quoted = shlex.quote(pubkey)
        ssh_key_block = f"""
echo "==> Installing SSH public key for {username}..."
arch-chroot /mnt install -d -m 700 -o {username} -g {username} /home/{username}/.ssh
arch-chroot /mnt bash -c "echo {pubkey_quoted} > /home/{username}/.ssh/authorized_keys"
arch-chroot /mnt chown {username}:{username} /home/{username}/.ssh/authorized_keys
arch-chroot /mnt chmod 600 /home/{username}/.ssh/authorized_keys
"""
    else:
        ssh_key_block = ""

    bootstrap_commands_block = ""
    if bootstrap_chroot_commands:
        rendered_commands = "\n".join(
            f"arch-chroot /mnt bash -lc {shlex.quote(str(command))}"
            for command in bootstrap_chroot_commands
        )
        bootstrap_commands_block = f"""
echo "==> Bootstrap guest customization..."
{rendered_commands}
"""

    pacman_conf_block = ""
    if inherit_live_pacman_conf:
        pacman_conf_block = """
echo "==> Copying the live pacman configuration into the target..."
# pacstrap resolves packages with the live /etc/pacman.conf, but the target
# gets the stock file from the pacman package: a derivative's repositories
# (CachyOS: [cachyos] + its mirrorlists) would be missing on first boot.
install -m 644 /etc/pacman.conf /mnt/etc/pacman.conf
for mirrorlist in /etc/pacman.d/*mirrorlist*; do
    [ -f "$mirrorlist" ] && install -m 644 "$mirrorlist" "/mnt/etc/pacman.d/$(basename "$mirrorlist")"
done
arch-chroot /mnt pacman-key --populate || true
"""

    archive_block = target_archive_block = ""
    if archive_date:
        server = f"https://archive.archlinux.org/repos/{archive_date}/$repo/os/$arch"
        # The Arch Linux Archive: the repositories as they were that day, so the installed system
        # is the one of its era, not today's packages on an old kernel. The packagers' keys of that
        # time have expired since, and gpg judges an expiry against today's clock: signatures are
        # not checked on a historical lab guest (SigLevel Never, live and target alike).
        archive_block = f"""
SEED_DIR="$(dirname "$(readlink -f "$0")")"
if [ -f "$SEED_DIR/{CA_BUNDLE_NAME}" ]; then
    echo "==> Installing the host's CA bundle in the live system..."
    install -m 644 "$SEED_DIR/{CA_BUNDLE_NAME}" /etc/ssl/certs/ca-certificates.crt
fi
echo "==> Using the Arch Linux Archive snapshot of {archive_date}..."
echo 'Server = {server}' > /etc/pacman.d/mirrorlist
sed -i 's/^#*SigLevel.*/SigLevel = Never/' /etc/pacman.conf
"""
        # Packages older than 2019 are redirected to archive.org, whose data nodes answer HTTP 500
        # now and then (the same file failed and then downloaded, 2026-09-29): curl retries each
        # download, 500 included, and pacman's own downloader cannot. -k only where the medium's
        # OpenSSL 1.0.1 rejects the archive's Let's Encrypt chain whatever CA bundle it is given
        # (arch-2014, archive_insecure_tls; signatures are off on these guests already).
        archive_block += f"""if [ -d "$SEED_DIR/{SEED_PACKAGE_DIR}" ]; then
    # The host cached the install set (host_package_cache): pacstrap -c reads the live system's
    # cache directories, the seed's first, and downloads only what is not there.
    echo "==> Installing from the package cache on the seed ($(ls "$SEED_DIR/{SEED_PACKAGE_DIR}" | wc -l) files)..."
    sed -i "/^\\[options\\]/a CacheDir = /var/cache/pacman/pkg/\\nCacheDir = $SEED_DIR/{SEED_PACKAGE_DIR}/" /etc/pacman.conf
    pacstrap_cache="-c"
fi
"""
        insecure = "-k " if insecure_tls else ""
        archive_block += f"""echo "==> Downloading with curl and retries{' (no TLS verification: archive_insecure_tls)' if insecure_tls else ''}..."
sed -i '/^\\[options\\]/a XferCommand = /usr/bin/curl {insecure}-L -C - -f --retry 8 --retry-delay 15 -o %o %u' /etc/pacman.conf
"""
        target_archive_block = f"""
echo "==> Keeping the target on the {archive_date} snapshot..."
install -m 644 /etc/pacman.d/mirrorlist /mnt/etc/pacman.d/mirrorlist
if [ -f "$SEED_DIR/{CA_BUNDLE_NAME}" ]; then install -m 644 "$SEED_DIR/{CA_BUNDLE_NAME}" /mnt/etc/ssl/certs/ca-certificates.crt; fi
sed -i 's/^#*SigLevel.*/SigLevel = Never/' /mnt/etc/pacman.conf
"""

    return f"""\
#!/usr/bin/env bash
set -Eeuo pipefail

# A failed step must not leave the live system sitting at its prompt: the host would learn
# of it only from its own timeout, an hour later, as a bare "Timed out" instead of the real
# error (a mirror stall killed pacstrap after 54 s and cost 60 minutes on 2026-09-13). Say
# what broke and power off; nothing to flush, the install did not finish. The success path
# below keeps its own order: sync -> flushbufs -> token -> poweroff.
trap 'echo "{BOOTSTRAP_FAILED_TOKEN}: line $LINENO: $BASH_COMMAND"; sleep 1; poweroff -f' ERR

echo "==> Waiting for the live network and pacman keyring..."
# The serial login prompt comes up before archiso's pacman-init finished
# populating the keyring and before DHCP settled; pacstrap needs both.
systemctl start pacman-init.service 2>/dev/null || true
for attempt in $(seq 1 60); do
    getent hosts archlinux.org >/dev/null 2>&1 && break
    # The 2014 medium brings no network up by itself on a serial boot (arch-2014, 2026-09-29):
    # after 10 s without DNS ask for a DHCP lease once. A medium already online never gets here.
    if [ "$attempt" = 5 ] && command -v dhcpcd >/dev/null 2>&1; then
        echo "==> No network yet: starting dhcpcd..."
        dhcpcd >/dev/null 2>&1 || true
    fi
    sleep 2
done

{archive_block}
echo "==> Partitioning /dev/vda..."
sgdisk --zap-all /dev/vda
sgdisk --new=1:0:+512MiB --typecode=1:ef00 --change-name=1:EFI /dev/vda
sgdisk --new=2:0:0       --typecode=2:8300 --change-name=2:ROOT /dev/vda
partprobe /dev/vda
sleep 1

echo "==> Formatting..."
mkfs.fat -F32 -n EFI /dev/vda1
mkfs.ext4 -L ROOT -F /dev/vda2

echo "==> Mounting..."
mount /dev/vda2 /mnt
mkdir -p /mnt/boot/efi
mount /dev/vda1 /mnt/boot/efi

# A rolling repository publishes its database and its signature as two files, and for a
# moment a mirror can serve one of each pair: pacstrap then dies on "signature from ... is
# invalid" although the key is known. Both CachyOS profiles hit that on 2026-09-14 and passed
# minutes later on the same ISO. Force a fresh sync first, with retries; "&& break" keeps a
# failed attempt away from the ERR trap, and pacstrap still does its own sync afterwards.
echo "==> Refreshing the package databases..."
for attempt in 1 2 3; do
    pacman -Syy --noconfirm >/dev/null 2>&1 && break
    echo "==> Package database sync failed (attempt $attempt of 3), retrying in 20 s..."
    sleep 20
done

echo "==> Installing base system (this will take a while)..."
# -K (a fresh keyring in the target) exists since arch-install-scripts 24 (2022); older media
# initialise the target's keyring from the live one on their own.
pacstrap_opts="${{pacstrap_cache:-}}"
if pacstrap -h 2>&1 | grep -q -- '-K'; then pacstrap_opts="$pacstrap_opts -K"; fi
# A download that dies halfway (the Arch Linux Archive answered 500 and then stalled on arch-2019,
# 2026-09-29) is retried: what was fetched stays in the target's cache. "if" keeps a failed
# attempt away from the ERR trap; the third failure is the real one.
pacstrap_done=0
for attempt in 1 2; do
    if pacstrap $pacstrap_opts /mnt {package_line}; then pacstrap_done=1; break; fi
    echo "==> pacstrap failed (attempt $attempt of 3), retrying in 30 s..."
    sleep 30
done
# The third attempt runs under the ERR trap, so a real failure names pacstrap, not a test.
[ "$pacstrap_done" = 1 ] || pacstrap $pacstrap_opts /mnt {package_line}

echo "==> Generating fstab..."
genfstab -U /mnt >> /mnt/etc/fstab
{pacman_conf_block}{target_archive_block}
echo "==> Timezone..."
arch-chroot /mnt ln -sf /usr/share/zoneinfo/{timezone} /etc/localtime
arch-chroot /mnt hwclock --systohc

echo "==> Locale..."
arch-chroot /mnt bash -c "echo '{locale_lang}.{locale_enc} {locale_enc}' >> /etc/locale.gen"
arch-chroot /mnt locale-gen
arch-chroot /mnt bash -c "echo 'LANG={locale_lang}.{locale_enc}' > /etc/locale.conf"
arch-chroot /mnt bash -c "echo 'KEYMAP={kb_layout}' > /etc/vconsole.conf"
# Same layout for the graphical session: Wayland compositors (niri, sway...) ask systemd-localed,
# which reads this file. Without it the desktop falls back to US even with KEYMAP set.
arch-chroot /mnt install -d -m 755 /etc/X11/xorg.conf.d
arch-chroot /mnt bash -c "printf 'Section \\"InputClass\\"\\n    Identifier \\"system-keyboard\\"\\n    MatchIsKeyboard \\"on\\"\\n    Option \\"XkbLayout\\" \\"{kb_layout}\\"\\nEndSection\\n' > /etc/X11/xorg.conf.d/00-keyboard.conf"

echo "==> Hostname..."
arch-chroot /mnt bash -c "echo '{hostname}' > /etc/hostname"

echo "==> Services..."
arch-chroot /mnt systemctl enable {services_line}

echo "==> User..."
arch-chroot /mnt useradd -m -G wheel -s /bin/bash {username}
arch-chroot /mnt bash -c "echo '{username}:{password}' | chpasswd"
arch-chroot /mnt bash -c "echo '%wheel ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/nopasswd-wheel"
arch-chroot /mnt chmod 0440 /etc/sudoers.d/nopasswd-wheel
{ssh_key_block}
echo "==> Bootloader..."
# Standard Arch UEFI install: grub-install (without --removable) generates a
# grubx64.efi whose prefix points to /boot/grub on the root partition, with
# all modules required to read that path baked in by grub-install itself.
# It also registers an EFI boot entry via efibootmgr automatically.
arch-chroot /mnt grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=GRUB

# Make kernel boot visible on the serial console for post-install diagnostics
# (kept on tty0 too so it shows on the QEMU display).
sed -i 's|^GRUB_CMDLINE_LINUX_DEFAULT=.*|GRUB_CMDLINE_LINUX_DEFAULT="loglevel=3 console=tty0 console=ttyS0,115200"|' /mnt/etc/default/grub

# Clear out stale temporary configs from previous failed attempts, then ask
# GRUB to generate the real config at the canonical path.
rm -f /mnt/boot/grub/grub.cfg /mnt/boot/grub/grub.cfg.new /mnt/boot/grub/grub.cfg.new.new /mnt/boot/grub/grub.cfg.new.new.new
arch-chroot /mnt grub-mkconfig -o /boot/grub/grub.cfg

# Some current Arch/GRUB combinations leave a *.new file behind instead of
# promoting it into place. If that happens, salvage the newest temporary file.
if [ ! -s /mnt/boot/grub/grub.cfg ]; then
    for candidate in \
        /mnt/boot/grub/grub.cfg.new \
        /mnt/boot/grub/grub.cfg.new.new \
        /mnt/boot/grub/grub.cfg.new.new.new
    do
        if [ -s "$candidate" ]; then
            install -D -m 600 "$candidate" /mnt/boot/grub/grub.cfg
            break
        fi
    done
fi

test -s /mnt/boot/grub/grub.cfg
# A rejected config is printed whole, numbered: the checker names a line and the disk of a
# failed row is gone before anyone can read it (arch-2014, GRUB 2.00: "Syntax error at line 160").
if ! arch-chroot /mnt grub-script-check /boot/grub/grub.cfg; then
    echo "==> grub-script-check rejected /boot/grub/grub.cfg:"
    cat -n /mnt/boot/grub/grub.cfg
    false
fi

# Replace the plain EFI loader with a standalone GRUB image that embeds the
# first-stage config and the modules needed to find the root filesystem. This
# avoids OVMF/GRUB prefix drift that otherwise drops the VM into `grub>`.
ROOT_UUID="$(blkid -s UUID -o value /dev/vda2)"
cat > /mnt/grub-embedded.cfg <<EOF
search --no-floppy --fs-uuid --set=root $ROOT_UUID
set prefix=(\\$root)/boot/grub
configfile \\$prefix/grub.cfg
EOF
arch-chroot /mnt grub-mkstandalone \\
    --format=x86_64-efi \\
    --output=/boot/efi/EFI/GRUB/grubx64.efi \\
    --modules="part_gpt part_msdos fat ext2 normal configfile search search_fs_uuid search_fs_file search_label regexp linux all_video font gfxterm gzio echo boot chain test true" \\
    "boot/grub/grub.cfg=/grub-embedded.cfg"
rm -f /mnt/grub-embedded.cfg

# Keep the removable fallback path bootable if NVRAM is reset or ignored.
mkdir -p /mnt/boot/efi/EFI/BOOT
cp /mnt/boot/efi/EFI/GRUB/grubx64.efi /mnt/boot/efi/EFI/BOOT/BOOTX64.EFI

# Leave the same first-stage config on the ESP for diagnostics and for any
# non-standalone GRUB binary that a user may install later.
cat > /mnt/boot/efi/EFI/GRUB/grub.cfg <<EOF
search --no-floppy --fs-uuid --set=root $ROOT_UUID
set prefix=(\\$root)/boot/grub
configfile \\$prefix/grub.cfg
EOF
cp /mnt/boot/efi/EFI/GRUB/grub.cfg /mnt/boot/efi/EFI/BOOT/grub.cfg

arch-chroot /mnt efibootmgr
{bootstrap_commands_block}

sync
blockdev --flushbufs /dev/vda /dev/vda1 /dev/vda2 || true
echo "{BOOTSTRAP_COMPLETE_TOKEN}"
poweroff -f
"""


def arch_iso_label(iso_path: Path) -> str:
    """Return the Arch live ISO volume label (e.g. ARCH_202609).

    Read from the ISO itself when the file exists (so a version-agnostic cached
    name still boots with the right ``archisolabel=``); fall back to the
    ``archlinux-YYYY.MM.DD-`` filename convention, then to ``ARCH_LIVE``.
    """
    if iso_path.is_file():
        try:
            probe = subprocess.run(
                ["blkid", "-p", "-o", "value", "-s", "LABEL", str(iso_path)],
                capture_output=True, text=True, check=False,
            )
            label = probe.stdout.strip()
            if probe.returncode == 0 and label:
                return label
        except OSError:
            pass
    match = re.search(r"archlinux-(\d{4})\.(\d{2})\.\d{2}-", iso_path.name)
    if match:
        return f"ARCH_{match.group(1)}{match.group(2)}"
    return "ARCH_LIVE"


HOST_CA_BUNDLES = (Path("/etc/ssl/certs/ca-certificates.crt"), Path("/etc/pki/tls/certs/ca-bundle.crt"),
                   Path("/etc/ca-certificates/extracted/tls-ca-bundle.pem"))
CA_BUNDLE_NAME = "ca-certificates.crt"
SEED_PACKAGE_DIR = "pkg"  # arch_archive.prefetch's cache on the seed CD


def create_bootstrap_iso(vm_name: str, vm: dict[str, Any], dry_run: bool = False) -> Path:
    """Create an ISO with the self-contained bash install script for automated bootstrap."""
    artifact_dir = archinstall_artifact_dir(vm)
    runtime.ensure_parent(artifact_dir / "placeholder")

    install_path = artifact_dir / "install.sh"
    run_path = artifact_dir / "run.sh"
    iso_path = artifact_dir / "bootstrap.iso"

    run_sh = """\
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
bash "$SCRIPT_DIR/install.sh"
"""

    if not dry_run:
        install_path.write_text(render_bootstrap_script(vm_name, vm), encoding="utf-8")
        install_path.chmod(0o755)
        run_path.write_text(run_sh, encoding="utf-8")
        run_path.chmod(0o755)
        ui.print_status("ok", f"Bootstrap script: {ui.pretty_path(install_path)}")

    members = [install_path, run_path]
    cfg = archinstall_config(vm) or {}
    if cfg.get("host_ca_bundle"):
        # A medium of 2014 does not know today's certificate authorities, and the Arch Linux
        # Archive answers HTTPS only (plain HTTP redirects): the host's bundle travels on the seed.
        bundle = next((path for path in HOST_CA_BUNDLES if path.is_file()), None)
        if bundle is None:
            raise VMError("archinstall_config.host_ca_bundle: no CA bundle on this host (" + ", ".join(map(str, HOST_CA_BUNDLES)) + ")")
        ca_path = artifact_dir / CA_BUNDLE_NAME
        if not dry_run:
            shutil.copyfile(bundle, ca_path)
        members.append(ca_path)
    grafts: dict[str, Path] = {}
    if cfg.get("host_package_cache") and cfg.get("archive_date"):
        grafts[f"{SEED_PACKAGE_DIR}/"] = arch_archive.prefetch(str(cfg["archive_date"]), cfg, dry_run=dry_run)
    runtime.run(_iso_builder_cmd(iso_path, members, volid="ARCHBOOT", grafts=grafts), dry_run=dry_run)
    return iso_path
