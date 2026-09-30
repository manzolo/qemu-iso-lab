"""OPNsense from its DVD: the live system installs itself (bootstrap-opnsense).

The DVD boots a live OPNsense whose /usr/local/etc/rc runs the scripts of
/usr/local/etc/rc.syshook.d/start at every boot. The host grafts one there with growisofs
(like the FreeBSD flow), plus a loader.conf that puts the console on the serial port. The
script does what the vendor's opnsense-install does behind its dialogs: partitions the disk
(GPT, freebsd-boot + UFS), clones the running system onto it with cpdup (the same list of
directories), then writes a config.xml of ours instead of the factory one: WAN on vtnet0 with
DHCP and no private-network block (QEMU's user network is 10.0.2.0/24), SSH on with the
project key for an admin user whose shell is /bin/sh, sudo without password for wheel, a
WAN rule for port 22, the serial console. sync, the token on the console, power-off.
"""
from __future__ import annotations

import base64
import hashlib
import shutil
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from vmctl import pfsense, runtime, ui
from vmctl.errors import VMError

BOOTSTRAP_COMPLETE_TOKEN = "==> OPNsense installation complete!"
BOOTSTRAP_FAILED_TOKEN = "==> OPNsense installation FAILED"
SHUTDOWN_GRACE_SEC = 120
HOOK_PATH = "/usr/local/etc/rc.syshook.d/start/99-vmctl-install"
CONFIG_STAGE = "/usr/local/etc/vmctl-config.xml"
# What opnsense-install clones (usr/local is taken two levels deep, as the vendor does).
CLONE_ITEMS = (".cshrc", ".profile", "COPYRIGHT", "bin", "boot", "boot.config", "conf", "dev", "etc", "home", "lib",
               "libexec", "media", "proc", "rescue", "root", "sbin", "sys", "usr/bin", "usr/games", "usr/include",
               "usr/lib", "usr/lib32", "usr/libdata", "usr/libexec", "usr/obj", "usr/sbin", "usr/share", "usr/src", "var")


def opnsense_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    cfg = vm.get("opnsense_config")
    if cfg is None:
        return None
    if not isinstance(cfg, dict):
        raise VMError("opnsense_config must be an object")
    return cfg


def check_profile(vm_name: str, vm: dict[str, Any]) -> None:
    cfg = opnsense_config(vm)
    if cfg is None:
        raise VMError(f"VM '{vm_name}' does not define opnsense_config")
    if not str(cfg.get("username") or "").strip() or not str(cfg.get("password") or "").strip():
        raise VMError(f"{vm_name}: opnsense_config needs username and password")
    if (vm.get("firmware") or {}).get("type") != "bios":
        raise VMError(f"{vm_name}: bootstrap-opnsense installs the BIOS boot code (firmware.type bios)")
    if (vm.get("disk") or {}).get("interface") != "virtio":
        raise VMError(f"{vm_name}: bootstrap-opnsense expects the disk on virtio (vtbd0)")
    if not isinstance(vm.get("ssh_provision"), dict):
        raise VMError(f"{vm_name}: bootstrap-opnsense verifies the install over SSH (ssh_provision)")


def render_config_xml(vm_name: str, vm: dict[str, Any], keys: list[str],
                      password_hash: str | None = None) -> str:
    """The installed system's config.xml: the factory sample reduced to one WAN, plus the admin user."""
    cfg = opnsense_config(vm) or {}
    username = str(cfg["username"]).strip()
    hostname = str(cfg.get("hostname") or vm_name).strip()
    timezone = str(cfg.get("timezone") or "Etc/UTC").strip()
    hashed = password_hash or pfsense.bcrypt_hash(str(cfg["password"]))
    authorized = base64.b64encode(("\n".join(k.strip() for k in keys if k.strip()) + "\n").encode()).decode()
    return f"""<?xml version="1.0"?>
<opnsense>
  <theme>opnsense</theme>
  <sysctl/>
  <system>
    <optimization>normal</optimization>
    <hostname>{escape(hostname)}</hostname>
    <domain>internal</domain>
    <dnsallowoverride>1</dnsallowoverride>
    <group>
      <name>admins</name>
      <description><![CDATA[System Administrators]]></description>
      <scope>system</scope>
      <gid>1999</gid>
      <member>0</member>
      <member>2000</member>
      <priv>page-all</priv>
    </group>
    <user>
      <name>root</name>
      <descr><![CDATA[System Administrator]]></descr>
      <scope>system</scope>
      <groupname>admins</groupname>
      <password>{escape(hashed)}</password>
      <uid>0</uid>
    </user>
    <user>
      <name>{escape(username)}</name>
      <descr><![CDATA[vmctl]]></descr>
      <scope>user</scope>
      <groupname>admins</groupname>
      <password>{escape(hashed)}</password>
      <uid>2000</uid>
      <shell>/bin/sh</shell>
      <authorizedkeys>{authorized}</authorizedkeys>
    </user>
    <timezone>{escape(timezone)}</timezone>
    <timeservers>0.opnsense.pool.ntp.org 1.opnsense.pool.ntp.org</timeservers>
    <webgui>
      <protocol>https</protocol>
    </webgui>
    <disablenatreflection>yes</disablenatreflection>
    <usevirtualterminal>1</usevirtualterminal>
    <disablechecksumoffloading>1</disablechecksumoffloading>
    <disablesegmentationoffloading>1</disablesegmentationoffloading>
    <disablelargereceiveoffloading>1</disablelargereceiveoffloading>
    <primaryconsole>serial</primaryconsole>
    <serialspeed>115200</serialspeed>
    <sudo_allow_wheel>2</sudo_allow_wheel>
    <ssh>
      <group>admins</group>
      <enabled>enabled</enabled>
      <interfaces/>
    </ssh>
  </system>
  <interfaces>
    <wan>
      <enable>1</enable>
      <if>vtnet0</if>
      <ipaddr>dhcp</ipaddr>
      <blockpriv>0</blockpriv>
      <blockbogons>1</blockbogons>
    </wan>
  </interfaces>
  <unbound>
    <enable>1</enable>
  </unbound>
  <nat>
    <outbound>
      <mode>automatic</mode>
    </outbound>
  </nat>
  <filter>
    <rule>
      <type>pass</type>
      <ipprotocol>inet</ipprotocol>
      <protocol>tcp</protocol>
      <descr><![CDATA[vmctl: SSH on WAN (QEMU user network)]]></descr>
      <interface>wan</interface>
      <source>
        <any/>
      </source>
      <destination>
        <any/>
        <port>22</port>
      </destination>
    </rule>
  </filter>
</opnsense>
"""


def render_install_hook() -> str:
    """The live system's start hook: runs once, in the background so the boot goes on.
    FreeBSD's sh has no ERR trap, so every step goes through run(), which names the step that
    failed on the console and powers off (a silent exit would only end at the host's timeout)."""
    items = " ".join(CLONE_ITEMS)
    lines = [
        "#!/bin/sh",
        "# vmctl: install this live OPNsense onto the disk, then power off (vmctl/opnsense.py)",
        "[ -f /tmp/vmctl-install-started ] && exit 0",
        "touch /tmp/vmctl-install-started",
        "(",
        "exec > /dev/console 2>&1",
        "failed() {",
        f'    echo "{BOOTSTRAP_FAILED_TOKEN}: $*"',
        "    sync",
        "    /sbin/shutdown -p now",
        "    exit 1",
        "}",
        'run() { "$@" || failed "$*"; }',
        'echo "==> vmctl: installing OPNsense onto the disk..."',
        'DISK=""',
        "for d in vtbd0 ada0 da0; do [ -c /dev/$d ] && DISK=$d && break; done",
        '[ -n "$DISK" ] || failed "no disk found (vtbd0, ada0, da0)"',
        "gpart destroy -F $DISK >/dev/null 2>&1 || true",
        "run gpart create -s gpt $DISK",
        "run gpart add -t freebsd-boot -s 512k -l bootfs $DISK",
        "run gpart add -t freebsd-ufs -l rootfs $DISK",
        "run gpart bootcode -b /boot/pmbr -p /boot/gptboot -i 1 $DISK",
        "run newfs -U -L rootfs /dev/${DISK}p2",
        "run mount /dev/${DISK}p2 /mnt",
        "for USRDIR in $(find /usr/local -d 1 -type d); do run mkdir -p /mnt$USRDIR; done",
        f"ITEMS=\"{items} $(find /usr/local -d 2 | sed 's|^/||')\"",
        "for ITEM in $ITEMS; do",
        "    if [ -e /$ITEM -o -L /$ITEM ]; then run cpdup -i0 -o -s0 /$ITEM /mnt/$ITEM; fi",
        "done",
        'echo "==> vmctl: system cloned"',
        "printf '/dev/gpt/rootfs\\t/\\tufs\\trw\\t1\\t1\\n' > /mnt/etc/fstab || failed fstab",
        "run mkdir -p /mnt/tmp /mnt/conf",
        "run chmod 1777 /mnt/tmp",
        f"run cp {CONFIG_STAGE} /mnt/conf/config.xml",
        f"run rm -f /mnt{HOOK_PATH} /mnt{CONFIG_STAGE}",
        "run mount -t devfs devfs /mnt/dev",
        "chroot /mnt /bin/sh /etc/rc.d/ldconfig start || true",
        "run umount /mnt/dev",
        "sync",
        "run umount /mnt",
        "sync",
        f'echo "{BOOTSTRAP_COMPLETE_TOKEN}"',
        "/sbin/shutdown -p now",
        ") &",
        "exit 0",
    ]
    return "\n".join(lines) + "\n"


def volume_label(source: Path, dry_run: bool = False) -> str:
    if dry_run and not source.is_file():
        return "OPNSENSE_INSTALL"
    with source.open("rb") as stream:
        stream.seek(16 * 2048)
        descriptor = stream.read(72)
    if descriptor[:7] != b"\x01CD001\x01":
        raise VMError(f"OPNsense source has no ISO9660 primary descriptor: {source}")
    return descriptor[40:72].decode("ascii").rstrip()


def ensure_install_iso(vm_name: str, vm: dict[str, Any], source: Path, keys: list[str],
                       dry_run: bool = False) -> Path:
    """A copy of the DVD with the start hook, the staged config.xml and a serial loader.conf."""
    label = volume_label(source, dry_run=dry_run)
    config_xml = render_config_xml(vm_name, vm, keys, password_hash="$2y$10$dryrun" if dry_run else None)
    hook = render_install_hook()
    directory = runtime.resolve_path(vm["disk"]["path"]).parent / "opnsense"
    dest = directory / "install.iso"
    stamp_path = dest.with_suffix(".iso.source")
    identity = hashlib.sha256((hook + label + "\n".join(keys) + str(opnsense_config(vm))).encode()).hexdigest()
    stamp = None
    if source.is_file():
        st = source.stat()
        stamp = f"{source.resolve()}\n{st.st_size}\n{st.st_mtime_ns}\n{identity}"
    if stamp and dest.is_file() and stamp_path.is_file() and stamp_path.read_text() == stamp:
        return dest
    runtime.require_command("xorriso")
    runtime.require_command("growisofs")
    work = directory / "iso-work"
    if not dry_run:
        work.mkdir(parents=True, exist_ok=True)
    loader = work / "loader.conf"
    runtime.run(["xorriso", "-osirrox", "on", "-indev", str(source), "-extract", "/boot/loader.conf", str(loader)],
                dry_run=dry_run, quiet=True)
    if not dry_run:
        loader.chmod(0o644)
        loader.write_text(loader.read_text() + '\nboot_multicons="YES"\nboot_serial="YES"\n'
                          'console="comconsole,vidconsole"\ncomconsole_speed="115200"\nautoboot_delay="1"\n')
        (work / "hook").write_text(hook)
        (work / "hook").chmod(0o755)
        (work / "config.xml").write_text(config_xml)
        (work / "config.xml").chmod(0o600)
    partial = dest.with_suffix(".iso.part")
    runtime.run(["cp", str(source), str(partial)], dry_run=dry_run, quiet=True)
    runtime.run(["growisofs", "-M", str(partial), "-d", "-l", "-r", "-V", label, "-graft-points",
                 f"{HOOK_PATH}={work / 'hook'}", f"{CONFIG_STAGE}={work / 'config.xml'}",
                 f"/boot/loader.conf={loader}"], dry_run=dry_run, quiet=True)
    if not dry_run:
        partial.replace(dest)
        shutil.rmtree(work)
        if stamp:
            stamp_path.write_text(stamp)
    ui.print_status("ok", f"OPNsense unattended ISO: {ui.pretty_path(dest)}")
    return dest


def install_media_args(path: Path) -> list[str]:
    return ["-drive", f"id=opncd,file={path},format=raw,if=none,media=cdrom,readonly=on",
            "-device", "ide-cd,drive=opncd,bus=ide.1,bootindex=2"]
