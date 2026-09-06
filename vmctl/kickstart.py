"""AlmaLinux/RHEL kickstart install helpers: config generation, boot artifacts, ISO packing."""
from __future__ import annotations

import shlex
import tempfile
from pathlib import Path
from typing import Any

from vmctl import cloud_init, iso, runtime, ssh
from vmctl.errors import VMError


BOOTSTRAP_COMPLETE_TOKEN = "==> Kickstart install complete!"


def _resolve_ssh_pubkey(vm: dict[str, Any]) -> str | None:
    ssh_cfg = vm.get("ssh_provision")
    if not isinstance(ssh_cfg, dict):
        return None
    pubkey = ssh.resolve_ssh_public_key(vm, ssh_cfg)
    if pubkey is None:
        return None
    return pubkey.read_text(encoding="utf-8").strip()


def kickstart_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    cfg = vm.get("kickstart_config")
    if cfg is None:
        return None
    if not isinstance(cfg, dict):
        raise VMError("Invalid kickstart_config: expected object")
    return cfg


def kickstart_artifact_dir(vm: dict[str, Any]) -> Path:
    return runtime.resolve_path(vm["disk"]["path"]).parent / "kickstart"


def install_repo(vm: dict[str, Any]) -> str:
    """Anaconda install source: ``cdrom`` (default, full ISO) or a repository URL (netinst)."""
    cfg = kickstart_config(vm) or {}
    return str(cfg.get("inst_repo") or "cdrom").strip()


def ostree_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    """``kickstart_config.ostree`` turns the flow into an rpm-ostree install (Fedora Silverblue).

    Anaconda then replaces the package transaction with ``ostreesetup``: no ``%packages``
    section, the tree comes from the repository the ISO carries.
    """
    cfg = kickstart_config(vm) or {}
    ost = cfg.get("ostree")
    if ost is None:
        return None
    if not isinstance(ost, dict):
        raise VMError("Invalid kickstart_config.ostree: expected object")
    return ost


def resolve_ostree_ref(vm: dict[str, Any], iso_path: Path, dry_run: bool = False) -> str | None:
    """The ostree ref of the install media, read from the ISO when possible.

    The ref carries the Fedora version (``fedora/44/x86_64/silverblue``), so a profile that
    hard-codes it breaks on the next release. ``refs/heads`` inside the ISO is the source of
    truth; ``kickstart_config.ostree.ref`` is only the fallback (dry runs, missing xorriso).
    """
    ost = ostree_config(vm)
    if ost is None:
        return None
    fallback = str(ost.get("ref") or "").strip()
    if dry_run or not iso_path.is_file():
        if not fallback:
            raise VMError("kickstart_config.ostree.ref is required (fallback for the ref read from the ISO)")
        return fallback
    match = str(ost.get("ref_match") or "").strip()
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "heads"
        try:
            runtime.run(
                ["xorriso", "-osirrox", "on", "-indev", str(iso_path),
                 "-extract", "/ostree/repo/refs/heads", str(dest)],
                quiet=True,
            )
        except Exception:
            if not fallback:
                raise VMError(f"Could not read the ostree refs from {iso_path} and no fallback ref is set")
            return fallback
        refs = sorted(str(path.relative_to(dest)) for path in dest.rglob("*") if path.is_file())
    if match:
        refs = [ref for ref in refs if match in ref] or refs
    if not refs:
        if not fallback:
            raise VMError(f"No ostree ref found in {iso_path}")
        return fallback
    return refs[0]


def kernel_append(vm: dict[str, Any]) -> str:
    return (
        "inst.ks=hd:LABEL=KS_CFG:/ks.cfg inst.text inst.cmdline "
        f"inst.repo={install_repo(vm)} console=ttyS0,115200"
    )


def render_kickstart(vm_name: str, vm: dict[str, Any], ostree_ref: str | None = None) -> str:
    cfg = kickstart_config(vm)
    if cfg is None:
        raise VMError("VM profile does not define kickstart_config")

    hostname = str(cfg.get("hostname") or vm_name).strip()
    username = str(cfg.get("username") or "").strip()
    fullname = str(cfg.get("fullname") or username).strip()
    password_hash = str(cfg.get("password_hash") or "").strip()
    password = str(cfg.get("password") or "").strip()
    timezone = str(cfg.get("timezone") or "UTC").strip()
    kb_layout = str(cfg.get("keyboard_layout") or "us").strip()
    locale = str(cfg.get("locale") or "en_US.UTF-8").strip()
    packages: list[str] = list(cfg.get("packages") or ["@^minimal-environment", "openssh-server", "sudo", "vim-minimal"])
    post_commands: list[str] = list(cfg.get("post_commands") or [])
    disk_device = str(cfg.get("disk_device") or "vda").strip()
    selinux = str(cfg.get("selinux") or "enforcing").strip()
    firewall = str(cfg.get("firewall") or "enabled").strip()
    inst_repo = install_repo(vm)
    ignore_missing = bool(cfg.get("ignore_missing_packages", False))
    ostree = ostree_config(vm)
    autopart_options = str(cfg.get("autopart_options") or ("--noswap" if ostree else "--type=plain --noswap")).strip()
    bootloader_options = str(cfg.get("bootloader_options") or '--append="crashkernel=auto"').strip()

    if not username:
        raise VMError("kickstart_config.username is required")
    if not password_hash and not password:
        raise VMError("kickstart_config.password_hash or password is required")

    pw_directive = ""
    if password_hash:
        pw_directive = f"user --name={username} --groups=wheel --gecos=\"{fullname}\" --password=\"{password_hash}\" --iscrypted"
    else:
        pw_directive = f"user --name={username} --groups=wheel --gecos=\"{fullname}\" --password=\"{password}\" --plaintext"

    pubkey = _resolve_ssh_pubkey(vm)
    ssh_key_block = ""
    if pubkey:
        pubkey_quoted = shlex.quote(pubkey)
        ssh_key_block = f"""
echo "==> Installing SSH public key for {username}..."
install -d -m 700 -o {username} -g {username} /home/{username}/.ssh
echo {pubkey_quoted} > /home/{username}/.ssh/authorized_keys
chown {username}:{username} /home/{username}/.ssh/authorized_keys
chmod 600 /home/{username}/.ssh/authorized_keys
# Ensure SELinux context is correct for SSH keys (an ostree install may lack the tool)
restorecon -R /home/{username}/.ssh || true
"""

    custom_commands_block = ""
    if post_commands:
        rendered_commands = "\n".join(post_commands)
        custom_commands_block = f"""
log "Kickstart post customization..."
{rendered_commands}
"""

    packages_str = "\n".join(packages)
    packages_header = "%packages --ignoremissing" if ignore_missing else "%packages"
    # An ostree install has no package transaction: the tree replaces the %packages section.
    install_source_block = f"""{packages_header}
{packages_str}
%end"""
    if ostree:
        ref = str(ostree_ref or ostree.get("ref") or "").strip()
        if not ref:
            raise VMError("kickstart_config.ostree needs a ref (from the ISO or in the profile)")
        osname = str(ostree.get("osname") or "fedora").strip()
        remote = str(ostree.get("remote") or osname).strip()
        url = str(ostree.get("url") or "file:///ostree/repo").strip()
        nogpg = "" if ostree.get("gpg_verify") else " --nogpg"
        install_source_block = (
            f'ostreesetup --osname="{osname}" --remote="{remote}" --url="{url}" --ref="{ref}"{nogpg}'
        )
    # A network install source (Fedora Everything netinst, RHEL-family netinst)
    # is declared here as well as on the kernel line, so the rendered file is
    # self-describing; cdrom installs keep relying on inst.repo=cdrom.
    source_directive = "" if inst_repo == "cdrom" else f'url --url="{inst_repo}"\n'

    # We use poweroff instead of reboot to let run_and_expect notice it
    return f"""\
# AlmaLinux/RHEL/Fedora kickstart generated by vmctl
lang {locale}
keyboard {kb_layout}
timezone {timezone} --utc
{source_directive}
network --bootproto=dhcp --hostname={hostname}
firewall --{firewall}
selinux --{selinux}

rootpw --lock
{pw_directive}

zerombr
clearpart --all --initlabel --drives={disk_device}
autopart {autopart_options}

bootloader {bootloader_options}
firstboot --disable
skipx
text
poweroff

{install_source_block}

%post --erroronfail
log() {{
    echo "[kickstart-post] $*" > /dev/console 2>&1 || true
    echo "[kickstart-post] $*" > /dev/ttyS0 2>&1 || true
    echo "[kickstart-post] $*"
}}

{ssh_key_block}

log "Sudoers setup..."
echo '%wheel ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/nopasswd-wheel
chmod 0440 /etc/sudoers.d/nopasswd-wheel

{custom_commands_block}

log "Syncing and flushing buffers..."
sync
blockdev --flushbufs /dev/{disk_device} /dev/{disk_device}1 /dev/{disk_device}2 || true
echo "{BOOTSTRAP_COMPLETE_TOKEN}" > /dev/console 2>&1 || true
echo "{BOOTSTRAP_COMPLETE_TOKEN}" > /dev/ttyS0 2>&1 || true
echo "{BOOTSTRAP_COMPLETE_TOKEN}"
%end
"""


def extract_kickstart_boot_artifacts(vm: dict[str, Any], iso_path: Path, dry_run: bool = False) -> tuple[Path, Path]:
    artifact_dir = kickstart_artifact_dir(vm)
    kernel_path = artifact_dir / "vmlinuz"
    initrd_path = artifact_dir / "initrd"
    boot = vm.get("installer_boot", {})
    kernel_member = str(boot.get("kernel") or "images/pxeboot/vmlinuz")
    initrd_member = str(boot.get("initrd") or "images/pxeboot/initrd.img")
    iso.extract_iso_member(iso_path, kernel_member, kernel_path, dry_run=dry_run)
    iso.extract_iso_member(iso_path, initrd_member, initrd_path, dry_run=dry_run)
    return kernel_path, initrd_path


def create_kickstart_iso(
    vm_name: str, vm: dict[str, Any], dry_run: bool = False, ostree_ref: str | None = None
) -> Path:
    artifact_dir = kickstart_artifact_dir(vm)
    cfg = render_kickstart(vm_name, vm, ostree_ref=ostree_ref)

    return cloud_init.create_iso_with_files(
        artifact_dir,
        {"ks.cfg": cfg},
        dry_run=dry_run,
        volume_id="KS_CFG",
    )


def kickstart_iso_drive_args(iso_path: Path) -> list[str]:
    return ["-drive", f"file={iso_path},format=raw,if=virtio,media=cdrom,readonly=on"]
