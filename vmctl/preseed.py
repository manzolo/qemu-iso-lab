"""Debian preseed install helpers: config generation, boot artifacts, initrd injection."""
from __future__ import annotations

import gzip
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from vmctl import iso, runtime, ssh
from vmctl.errors import VMError


BOOTSTRAP_COMPLETE_TOKEN = "==> Debian preseed install complete!"

# Used in lifecycle.py to build QEMU args. The preseed.cfg and late_command.sh
# are injected into the initrd via _inject_files_into_initrd, so d-i finds
# them at the initramfs root with no mount required.
#
# Short aliases (locale=, language=, country=, keymap=) are recognized by
# d-i's early `localechooser` stage which runs before `localechooser-data`
# udeb is loaded from the netinst ISO; without these, d-i prompts with
# the minimal "1: C, 2: English" choice even with a preseed file present,
# because the locale wasn't pre-injected into /proc/cmdline.
PRESEED_KERNEL_APPEND = (
    "auto-install/enable=true "
    "preseed/file=/preseed.cfg "
    "priority=critical "
    "locale={locale} "
    "language={language} "
    "country={country} "
    "keymap={keymap} "
    "DEBIAN_FRONTEND=text "
    "console=ttyS0,115200"
)


def _resolve_ssh_pubkey(vm: dict[str, Any]) -> str | None:
    ssh_cfg = vm.get("ssh_provision")
    if not isinstance(ssh_cfg, dict):
        return None
    pubkey = ssh.resolve_ssh_public_key(vm, ssh_cfg)
    if pubkey is None:
        return None
    return pubkey.read_text(encoding="utf-8").strip()


def preseed_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    cfg = vm.get("preseed_config")
    if cfg is None:
        return None
    if not isinstance(cfg, dict):
        raise VMError("Invalid preseed_config: expected object")
    return cfg


def preseed_artifact_dir(vm: dict[str, Any]) -> Path:
    return runtime.resolve_path(vm["disk"]["path"]).parent / "preseed"


def render_late_command_script(vm_name: str, vm: dict[str, Any]) -> str:
    cfg = preseed_config(vm)
    if cfg is None:
        raise VMError("VM profile does not define preseed_config")

    username = str(cfg.get("username") or "").strip()
    if not username:
        raise VMError("preseed_config.username is required")

    late_commands: list[str] = list(cfg.get("late_commands") or [])

    pubkey = _resolve_ssh_pubkey(vm)
    if pubkey:
        pubkey_quoted = shlex.quote(pubkey)
        ssh_key_block = f"""
log "Installing SSH public key for {username}..."
install -d -m 700 -o {username} -g {username} /home/{username}/.ssh || log "WARN: install -d failed"
echo {pubkey_quoted} > /home/{username}/.ssh/authorized_keys || log "WARN: write authorized_keys failed"
chown {username}:{username} /home/{username}/.ssh/authorized_keys || true
chmod 600 /home/{username}/.ssh/authorized_keys || true
"""
    else:
        ssh_key_block = ""

    custom_commands_block = ""
    if late_commands:
        rendered_commands = "\n".join(
            f"bash -lc {shlex.quote(str(command))} || log {shlex.quote('WARN: failed: ' + str(command))}"
            for command in late_commands
        )
        custom_commands_block = f"""
log "Running custom late_commands..."
{rendered_commands}
"""

    # We deliberately do NOT use `set -e` here: a single non-critical step
    # (e.g. `locale-gen` for a locale not yet enabled in /etc/locale.gen)
    # would otherwise abort the whole script with exit 255 and d-i would
    # surface "Failed to run preseeded command", missing our completion
    # token. Each step has its own `|| log` fallback. The completion
    # token must always be emitted (after sync+flush, BIBBIA-compliant).
    return f"""#!/usr/bin/env bash
log() {{ echo "[late_command] $*" > /dev/console 2>&1 || true; echo "[late_command] $*"; }}
log "START"

{ssh_key_block}
log "Sudoers setup..."
# Debian uses the 'sudo' group, not 'wheel'. Grant NOPASSWD by username
# directly so this works regardless of distro group conventions.
echo '{username} ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/nopasswd-{username} || log "WARN: sudoers write failed"
chmod 0440 /etc/sudoers.d/nopasswd-{username} || true
usermod -aG sudo {username} || log "WARN: usermod sudo failed"

{custom_commands_block}

log "Syncing and flushing buffers..."
sync
blockdev --flushbufs /dev/vda /dev/vda1 /dev/vda2 || true
log "DONE"
echo "{BOOTSTRAP_COMPLETE_TOKEN}" > /dev/console 2>&1 || true
echo "{BOOTSTRAP_COMPLETE_TOKEN}"
# Let d-i natural poweroff happen
"""

def render_preseed(vm_name: str, vm: dict[str, Any]) -> str:
    cfg = preseed_config(vm)
    if cfg is None:
        raise VMError("VM profile does not define preseed_config")

    hostname = str(cfg.get("hostname") or vm_name).strip()
    domain = str(cfg.get("domain") or "local").strip()
    username = str(cfg.get("username") or "").strip()
    fullname = str(cfg.get("fullname") or username).strip()
    password_hash = str(cfg.get("password_hash") or "").strip()
    password = str(cfg.get("password") or "").strip()
    timezone = str(cfg.get("timezone") or "UTC").strip()
    locale = str(cfg.get("locale") or "en_US.UTF-8").strip()
    language = str(cfg.get("language") or "en").strip()
    country = str(cfg.get("country") or "US").strip()
    kb_layout = str(cfg.get("keyboard_layout") or "us").strip()
    mirror_hostname = str(cfg.get("mirror_hostname") or "deb.debian.org").strip()
    mirror_directory = str(cfg.get("mirror_directory") or "/debian").strip()
    tasks = " ".join(cfg.get("tasks") or ["standard"])
    packages = " ".join(cfg.get("packages") or ["openssh-server", "sudo", "curl", "ca-certificates"])
    disk_device = str(cfg.get("disk_device") or "/dev/vda").strip()

    if not username:
        raise VMError("preseed_config.username is required")
    if not password_hash and not password:
        raise VMError("preseed_config.password_hash or password is required")

    pw_directive = ""
    if password_hash:
        pw_directive = f"d-i passwd/user-password-crypted password {password_hash}"
    else:
        pw_directive = f"d-i passwd/user-password password {password}\nd-i passwd/user-password-again password {password}"

    return f"""\
# Debian preseed generated by vmctl
d-i preseed/early_command string echo "==> vmctl preseed loaded" > /dev/console
d-i debian-installer/locale string {locale}
d-i debian-installer/language string {language}
d-i debian-installer/country string {country}
d-i keyboard-configuration/xkb-keymap select {kb_layout}
d-i debconf/priority string critical

d-i netcfg/choose_interface select auto
d-i netcfg/get_hostname string {hostname}
d-i netcfg/get_domain string {domain}

d-i mirror/country string manual
d-i mirror/http/hostname string {mirror_hostname}
d-i mirror/http/directory string {mirror_directory}
d-i mirror/http/proxy string

d-i passwd/root-login boolean false
d-i passwd/user-fullname string {fullname}
d-i passwd/username string {username}
{pw_directive}

d-i clock-setup/utc boolean true
d-i time/zone string {timezone}
d-i clock-setup/ntp boolean true

d-i partman-auto/disk string {disk_device}
d-i partman-auto/method string regular
d-i partman-auto/choose_recipe select atomic
d-i partman-partitioning/confirm_write_new_label boolean true
d-i partman/choose_partition select finish
d-i partman/confirm boolean true
d-i partman/confirm_nooverwrite boolean true

d-i apt-setup/cdrom/set-first boolean false
tasksel tasksel/first multiselect {tasks}
d-i pkgsel/include string {packages}
d-i pkgsel/upgrade select full-upgrade

d-i grub-installer/only_debian boolean true
d-i grub-installer/with_other_os boolean true
d-i grub-installer/bootdev  string {disk_device}

d-i preseed/late_command string cp /late_command.sh /target/tmp/late_command.sh; chmod +x /target/tmp/late_command.sh; in-target /tmp/late_command.sh

d-i finish-install/reboot_in_progress note
d-i debian-installer/exit/poweroff boolean true
"""


def _inject_files_into_initrd(initrd_path: Path, files: dict[str, str]) -> None:
    """Merge the given files into a gzipped cpio (newc) initrd in place.

    Decompresses the initrd, appends the new entries to the cpio archive via
    `cpio -A`, then recompresses. Producing a single coherent cpio.gz is more
    reliable than concatenating a second cpio.gz: d-i's loader does not always
    parse multiple stacked cpio archives, which causes preseed/file= to find
    nothing and fall back to interactive prompts.
    """
    compressed = initrd_path.read_bytes()
    decompressed = gzip.decompress(compressed)
    with tempfile.TemporaryDirectory() as workdir:
        workpath = Path(workdir)
        cpio_path = workpath / "initrd.cpio"
        cpio_path.write_bytes(decompressed)
        staging = workpath / "stage"
        staging.mkdir()
        for rel_path, content in files.items():
            target = staging / rel_path.lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        find_proc = subprocess.run(
            ["find", ".", "-mindepth", "1"],
            cwd=staging,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["cpio", "-A", "-F", str(cpio_path), "-o", "-H", "newc", "--quiet"],
            input=find_proc.stdout,
            cwd=staging,
            check=True,
        )
        initrd_path.chmod(0o644)
        initrd_path.write_bytes(gzip.compress(cpio_path.read_bytes()))


def extract_preseed_boot_artifacts(
    vm_name: str, vm: dict[str, Any], iso_path: Path, dry_run: bool = False,
) -> tuple[Path, Path]:
    artifact_dir = preseed_artifact_dir(vm)
    kernel_path = artifact_dir / "vmlinuz"
    initrd_path = artifact_dir / "initrd"
    boot = vm.get("installer_boot", {})
    kernel_member = str(boot.get("kernel") or "install.amd/vmlinuz")
    initrd_member = str(boot.get("initrd") or "install.amd/initrd.gz")
    iso.extract_iso_member(iso_path, kernel_member, kernel_path, dry_run=dry_run)
    iso.extract_iso_member(iso_path, initrd_member, initrd_path, dry_run=dry_run)

    if not dry_run:
        _inject_files_into_initrd(initrd_path, {
            "preseed.cfg": render_preseed(vm_name, vm),
            "late_command.sh": render_late_command_script(vm_name, vm),
        })

    return kernel_path, initrd_path
