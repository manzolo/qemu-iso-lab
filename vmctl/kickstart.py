"""AlmaLinux/RHEL kickstart install helpers: config generation, boot artifacts, ISO packing."""
from __future__ import annotations

import re
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


# Where the RHEL-family media keep the boot configuration that names their own stage2.
STAGE2_BOOT_CONFIGS = ("/EFI/BOOT/grub.cfg", "/isolinux/isolinux.cfg", "/boot/grub2/grub.cfg")


def resolve_stage2(iso_path: Path, dry_run: bool = False) -> str | None:
    """The ``inst.stage2=`` token the medium uses for itself, read from its own boot config.

    A netinst profile boots the kernel and initrd extracted from a **pinned** ISO but installs
    from a **rolling** repository. Anaconda resolves its runtime image (stage2) from
    ``inst.repo`` when nothing else says otherwise, so the guest ends up running an initrd of
    one compose against the stage2 of another. On 2026-09-15 the CentOS Stream 10 tree moved
    from 20260908.0 to 20260914.0 and that pair stopped working: ``Anaconda.Modules.Storage``
    exited 1 at startup, the installer died before saying anything useful, and the only symptom
    was the full 3600 s timeout (verified live, twice). The medium is already attached, so
    taking its own token keeps kernel, initrd and stage2 from one build while the packages
    still come from the network.

    Returns None when the token cannot be read (dry run, missing ISO, no xorriso): the caller
    then keeps the previous behaviour instead of failing.
    """
    if dry_run or not iso_path.is_file():
        return None
    for member in STAGE2_BOOT_CONFIGS:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "boot.cfg"
            try:
                runtime.run(
                    ["xorriso", "-osirrox", "on", "-indev", str(iso_path), "-extract", member, str(dest)],
                    quiet=True,
                )
                text = dest.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
        match = re.search(r"inst\.stage2=(\S+)", text)
        if match:
            return match.group(1)
    return None


# kickstart_config.legacy: the anaconda of CentOS 5 (11.x) and 6 (13.x). No inst.* options on
# the kernel line (added in RHEL 7), part of the modern kickstart syntax unknown, so the file is
# rendered by render_legacy_kickstart and read from the initrd itself (ks=file:/ks.cfg): no
# second CD to find by device name.
LEGACY_RELEASES = ("el5", "el6")
LEGACY_KS_PATH = "ks.cfg"


def legacy_release(vm: dict[str, Any]) -> str | None:
    cfg = kickstart_config(vm) or {}
    value = cfg.get("legacy")
    if value is None:
        return None
    if value not in LEGACY_RELEASES:
        raise VMError(f"kickstart_config.legacy must be one of {', '.join(LEGACY_RELEASES)}")
    return str(value)


def kernel_append(vm: dict[str, Any], stage2: str | None = None) -> str:
    repo = install_repo(vm)
    legacy = legacy_release(vm)
    if legacy:
        # el6 takes repo=, el5 method=; a cdrom source is found by the loader on its own
        source = "" if repo == "cdrom" else (f"repo={repo} " if legacy == "el6" else f"method={repo} ")
        return f"ks=file:/{LEGACY_KS_PATH} text {source}ksdevice=eth0 noipv6 console=ttyS0,115200"
    # A cdrom install already reads stage2 from the medium; only a network source needs to be
    # told, or it would fetch the runtime image from the repository instead (see resolve_stage2).
    stage2_arg = f"inst.stage2={stage2} " if stage2 and repo != "cdrom" else ""
    return (
        "inst.ks=hd:LABEL=KS_CFG:/ks.cfg inst.text inst.cmdline "
        f"{stage2_arg}inst.repo={repo} console=ttyS0,115200"
    )


def render_legacy_kickstart(vm_name: str, vm: dict[str, Any], release: str) -> str:
    """The kickstart of anaconda 11 (el5) and 13 (el6): the commands and options those versions
    know, verified against their own documentation. No %end on el5 (anaconda 11 predates it), no
    rootpw --lock, no user --gecos, no %post --erroronfail on el5, zerombr takes "yes" on el5, and
    the sudoers rule goes into /etc/sudoers (el5's sudo 1.7 has no #includedir)."""
    cfg = kickstart_config(vm) or {}
    hostname = str(cfg.get("hostname") or vm_name).strip()
    username = str(cfg.get("username") or "").strip()
    password_hash = str(cfg.get("password_hash") or "").strip()
    if not username or not password_hash:
        raise VMError("a legacy kickstart needs kickstart_config.username and password_hash")
    timezone = str(cfg.get("timezone") or "UTC").strip()
    kb_layout = str(cfg.get("keyboard_layout") or "us").strip()
    locale = str(cfg.get("locale") or "en_US.UTF-8").strip()
    disk_device = str(cfg.get("disk_device") or "vda").strip()
    selinux = str(cfg.get("selinux") or "enforcing").strip()
    packages: list[str] = list(cfg.get("packages") or ["@core", "openssh-server", "sudo"])
    post_commands: list[str] = list(cfg.get("post_commands") or [])
    inst_repo = install_repo(vm)
    source = "cdrom" if inst_repo == "cdrom" else f'url --url="{inst_repo}"'
    end = "\n%end" if release == "el6" else ""
    zerombr = "zerombr" if release == "el6" else "zerombr yes"
    post_header = "%post --erroronfail" if release == "el6" else "%post"
    # el5's loader crashes (SIGSEGV) on "Determining host name and domain" after a DHCP lease
    # from QEMU's user network, with any form of the network line (verified by hand, 2026-09-30):
    # the install runs on the user network's fixed addresses and %post writes DHCP back.
    if release == "el5":
        network_line = (f"network --device=eth0 --bootproto=static --ip=10.0.2.15 --netmask=255.255.255.0 "
                        f"--gateway=10.0.2.2 --nameserver=10.0.2.3 --hostname={hostname}.local --onboot=yes")
        dhcp_back = ("printf 'DEVICE=eth0\\nBOOTPROTO=dhcp\\nONBOOT=yes\\n' > /etc/sysconfig/network-scripts/ifcfg-eth0\n"
                     f"sed -i 's/^HOSTNAME=.*/HOSTNAME={hostname}/' /etc/sysconfig/network")
    else:
        network_line = f"network --device=eth0 --bootproto=dhcp --onboot=yes --hostname={hostname}"
        dhcp_back = ""
    pubkey = _resolve_ssh_pubkey(vm)
    key_block = ""
    if pubkey:
        key_block = f"""install -d -m 700 -o {username} -g {username} /home/{username}/.ssh
echo {shlex.quote(pubkey)} > /home/{username}/.ssh/authorized_keys
chown {username}:{username} /home/{username}/.ssh/authorized_keys
chmod 600 /home/{username}/.ssh/authorized_keys
restorecon -R /home/{username}/.ssh || true"""
    commands = "\n".join(post_commands)
    return f"""\
# CentOS {release[2:]} kickstart generated by vmctl (legacy anaconda)
install
{source}
lang {locale}
keyboard {kb_layout}
timezone --utc {timezone}
{network_line}
firewall --enabled --ssh
selinux --{selinux}
authconfig --enableshadow --passalgo=sha512
rootpw --iscrypted {password_hash}
user --name={username} --groups=wheel --password={password_hash} --iscrypted
services --enabled=sshd
{zerombr}
clearpart --all --initlabel --drives={disk_device}
autopart
bootloader --location=mbr --driveorder={disk_device}
firstboot --disable
skipx
text
poweroff

%packages --ignoremissing
{chr(10).join(packages)}{end}

{post_header}
[ -c /dev/ttyS0 ] && exec > /dev/ttyS0 2>&1
{key_block}
echo '{username} ALL=(ALL) NOPASSWD: ALL' >> /etc/sudoers
sed -i 's/^Defaults *requiretty/# &/' /etc/sudoers
grep -q '^UseDNS' /etc/ssh/sshd_config && sed -i 's/^UseDNS.*/UseDNS no/' /etc/ssh/sshd_config || echo 'UseDNS no' >> /etc/ssh/sshd_config
{dhcp_back}
{commands}
sync
blockdev --flushbufs /dev/{disk_device} || true
echo "{BOOTSTRAP_COMPLETE_TOKEN}" > /dev/console 2>&1 || true
echo "{BOOTSTRAP_COMPLETE_TOKEN}"{end}
"""


def cpio_newc(files: dict[str, bytes]) -> bytes:
    """A minimal newc cpio archive (regular files at the root, then the trailer), what the
    kernel unpacks after the initrd it is appended to."""
    out = bytearray()
    def entry(name: str, data: bytes, mode: int, ino: int) -> None:
        encoded = name.encode() + b"\0"
        header = "070701" + "".join(f"{v:08x}" for v in (ino, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(encoded), 0))
        out.extend(header.encode() + encoded)
        out.extend(b"\0" * (-len(out) % 4))
        out.extend(data)
        out.extend(b"\0" * (-len(out) % 4))
    for ino, (name, data) in enumerate(files.items(), start=1):
        entry(name, data, 0o100644, ino)
    entry("TRAILER!!!", b"", 0, 0)
    return bytes(out)


def add_to_legacy_initrd(initrd_path: Path, files: dict[str, str]) -> None:
    """Put *files* at the root of an el5 (gzip) or el6 (LZMA) initrd: decompressed with the
    standard library, the new newc archive appended (the kernel reads concatenated archives)
    and the whole written back as gzip, which the 2.6.18 and 2.6.32 kernels both take."""
    import gzip
    import lzma
    raw = initrd_path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        cpio = gzip.decompress(raw)
    else:
        cpio = lzma.decompress(raw, format=lzma.FORMAT_ALONE)
    extra = cpio_newc({name: text.encode() for name, text in files.items()})
    initrd_path.chmod(0o644)  # extracted from the ISO read-only (centos-6, 2026-09-30)
    initrd_path.write_bytes(gzip.compress(cpio + extra, compresslevel=6))


def render_kickstart(vm_name: str, vm: dict[str, Any], ostree_ref: str | None = None) -> str:
    cfg = kickstart_config(vm)
    if cfg is None:
        raise VMError("VM profile does not define kickstart_config")
    legacy = legacy_release(vm)
    if legacy:
        return render_legacy_kickstart(vm_name, vm, legacy)

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
    # Extra options of the network line: CentOS 7's anaconda 21 writes ONBOOT=no without
    # --onboot=yes, and the installed system then boots with no network (2026-09-30).
    network_options = str(cfg.get("network_options") or "").strip()
    network_options = f" {network_options}" if network_options else ""

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
network --bootproto=dhcp --hostname={hostname}{network_options}
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
