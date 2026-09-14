"""FreeBSD disc1 scripted bsdinstall, preserving the vendor's hidden boot extents."""
from __future__ import annotations

import hashlib
import shlex
import shutil
from pathlib import Path
from typing import Any

from vmctl import runtime, ui
from vmctl.errors import VMError

BOOTSTRAP_COMPLETE_TOKEN = "==> FreeBSD installation complete!"
BOOTSTRAP_FAILED_TOKEN = "==> FreeBSD installation FAILED"
SHUTDOWN_GRACE_SEC = 120


def freebsd_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    cfg = vm.get("freebsd_config")
    if cfg is not None and not isinstance(cfg, dict):
        raise VMError("Invalid freebsd_config: expected object")
    return cfg


def check_profile(vm_name: str, vm: dict[str, Any]) -> None:
    cfg = freebsd_config(vm)
    if not cfg or not cfg.get("username") or not cfg.get("password"):
        raise VMError(f"{vm_name}: freebsd_config.username and password are required")
    if vm["firmware"]["type"] != "bios" or vm.get("machine") != "pc":
        raise VMError(f"{vm_name}: FreeBSD scripted install requires bios/pc")
    if vm["disk"].get("interface", "virtio") != "virtio":
        raise VMError(f"{vm_name}: FreeBSD installer targets the virtio disk vtbd0")
    if vm.get("network_device") != "virtio-net-pci" or vm.get("networks"):
        raise VMError(f"{vm_name}: FreeBSD installer requires one virtio-net-pci user NIC (vtnet0)")
    if not vm.get("ssh_provision"):
        raise VMError(f"{vm_name}: FreeBSD bootstrap requires SSH provisioning")


def render_installerconfig(vm_name: str, vm: dict[str, Any], keys: list[str]) -> str:
    check_profile(vm_name, vm)
    cfg = freebsd_config(vm) or {}
    user = shlex.quote(str(cfg["username"]))
    password = shlex.quote(str(cfg["password"]))
    home = shlex.quote('/home/' + str(cfg['username']))
    hostname = shlex.quote(str(cfg.get("hostname") or vm_name))
    key_text = shlex.quote("\n".join(keys) + "\n")
    rule = shlex.quote(str(cfg['username']) + ' ALL=(ALL) NOPASSWD: ALL')
    return f'''# bsdinstall preamble, followed by the script executed inside the target.
# bsdinstall has just recreated BSDINSTALL_TMPETC, the live resolv.conf symlink target.
cp /tmp/vmctl-resolv.conf /etc/resolv.conf
PARTITIONS=vtbd0
DISTRIBUTIONS="kernel.txz base.txz"
export BSDINSTALL_DISTDIR=/usr/freebsd-dist
#!/bin/sh
set -eu
export PATH=/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/sbin:/usr/local/bin
sysrc hostname={hostname}
sysrc ifconfig_vtnet0=DHCP
sysrc sshd_enable=YES
printf '%s\\n' {password} | pw useradd {user} -m -s /bin/sh -G wheel -h 0
mkdir -p {home}/.ssh
printf '%s' {key_text} > {home}/.ssh/authorized_keys
chown -R {user}:{user} {home}/.ssh
chmod 700 {home}/.ssh
chmod 600 {home}/.ssh/authorized_keys
ASSUME_ALWAYS_YES=yes pkg bootstrap -f
ASSUME_ALWAYS_YES=yes pkg install -y sudo
mkdir -p /usr/local/etc/sudoers.d
printf '%s\\n' {rule} > /usr/local/etc/sudoers.d/vmctl
chmod 440 /usr/local/etc/sudoers.d/vmctl
visudo -c
printf '%s\\n' 'console="comconsole,vidconsole"' 'comconsole_speed="115200"' >> /boot/loader.conf
sed -i '' 's/^ttyu0.*/ttyu0 "\\/usr\\/libexec\\/getty 3wire.115200" vt100 on secure/' /etc/ttys
# bsdinstall subsequently unmounts the target (UFS flush) before the wrapper emits success.
sync
'''


def render_rc_local() -> str:
    return f'''#!/bin/sh
exec > /dev/console 2>&1
set -eu
export TERM=xterm
export PATH=/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/sbin:/usr/local/bin
failed() {{
    result=$?
    trap - EXIT
    tail -n 20 /tmp/bsdinstall_log 2>/dev/null || true
    echo "{BOOTSTRAP_FAILED_TOKEN}: bsdinstall/network/package step exited $result"
    sync
    /sbin/shutdown -p now
    while :; do sleep 1; done
}}
trap failed EXIT
mkdir -p /tmp/bsdinstall_etc
dhclient vtnet0
cp /etc/resolv.conf /tmp/vmctl-resolv.conf
# stdin is not a TTY: bsdinstall uses tar and cannot park on an extraction dialog.
bsdinstall script /etc/installerconfig < /dev/null
# The chroot ended with sync, then bsdinstall unmounted/flushed UFS successfully.
sync
trap - EXIT
echo "{BOOTSTRAP_COMPLETE_TOKEN}"
/sbin/shutdown -p now
while :; do sleep 1; done
'''


def volume_label(source: Path, dry_run: bool = False) -> str:
    """Read the ISO9660 primary descriptor without requiring another host tool."""
    if dry_run and not source.is_file():
        return "FREEBSD_DISC1"
    with source.open("rb") as stream:
        stream.seek(16 * 2048)
        descriptor = stream.read(72)
    if descriptor[:7] != b"\x01CD001\x01":
        raise VMError(f"FreeBSD source has no ISO9660 primary descriptor: {source}")
    return descriptor[40:72].decode("ascii").rstrip()


def ensure_install_iso(vm_name: str, vm: dict[str, Any], source: Path,
                       keys: list[str], dry_run: bool = False) -> Path:
    label = volume_label(source, dry_run=dry_run)
    installer = render_installerconfig(vm_name, vm, keys)
    wrapper = render_rc_local()
    directory = runtime.resolve_path(vm['disk']['path']).parent / 'freebsd'
    dest = directory / 'install.iso'
    stamp_path = dest.with_suffix('.iso.source')
    stamp = None
    if source.is_file():
        st = source.stat()
        stamp = f'{source.resolve()}\n{st.st_size}\n{st.st_mtime_ns}\n' + hashlib.sha256((installer + wrapper + label).encode()).hexdigest()
    if stamp and dest.is_file() and stamp_path.is_file() and stamp_path.read_text() == stamp:
        return dest
    runtime.require_command('xorriso')
    runtime.require_command('growisofs')
    work = directory / 'iso-work'
    if not dry_run:
        work.mkdir(parents=True, exist_ok=True)
    loader = work / 'loader.conf'
    runtime.run(['xorriso', '-osirrox', 'on', '-indev', str(source),
                 '-extract', '/boot/loader.conf', str(loader)], dry_run=dry_run, quiet=True)
    if not dry_run:
        loader.chmod(0o644)
        loader.write_text(loader.read_text() + '\nconsole="comconsole,vidconsole"\ncomconsole_speed="115200"\nautoboot_delay="1"\n')
        (work / 'installerconfig').write_text(installer)
        (work / 'installerconfig').chmod(0o600)
        (work / 'rc.local').write_text(wrapper)
        (work / 'rc.local').chmod(0o755)
    partial = dest.with_suffix('.iso.part')
    runtime.run(['cp', str(source), str(partial)], dry_run=dry_run, quiet=True)
    runtime.run(['growisofs', '-M', str(partial), '-d', '-l', '-r', '-V', label, '-graft-points',
                 f'/etc/installerconfig={work / "installerconfig"}',
                 f'/etc/rc.local={work / "rc.local"}', f'/boot/loader.conf={loader}'],
                dry_run=dry_run, quiet=True)
    if not dry_run:
        partial.replace(dest)
        shutil.rmtree(work)
        if stamp:
            stamp_path.write_text(stamp)
    ui.print_status('ok', f'FreeBSD unattended ISO: {ui.pretty_path(dest)}')
    return dest


def install_media_args(path: Path) -> list[str]:
    return ['-drive', f'id=freebsdcd,file={path},format=raw,if=none,media=cdrom,readonly=on',
            '-device', 'ide-cd,drive=freebsdcd,bus=ide.1,bootindex=2']
