"""pfSense CE 2.7.2 unattended install for the network lab (``bootstrap-pfsense``).

Ported from kvm-lab: the offline pfSense installer is a FreeBSD ``bsdinstall`` driven by
``/etc/rc.local`` on the ISO. A copy of the ISO gets three files replaced (``growisofs -M`` keeps
the hidden El Torito boot extents that an xorriso rebuild would drop):

* ``/etc/installerconfig``: scripted ZFS install on ``vtbd0`` (BIOS) that drops the rendered
  ``config.xml`` into ``/cf/conf``;
* ``/etc/rc.local``: runs it without questions and reports on the serial console (``cuau0`` =
  the host's ``-serial stdio``) with the vmctl completion token, then powers off;
* ``/usr/libexec/bsdinstall/script``: one-line patch so ``umount -a`` returning 1 on a
  ZFS-only layout does not abort the install.

The config.xml is rendered from the ``network_lab`` topology (:mod:`vmctl.netlab`): WAN on
``vtnet0`` (slirp, DHCP), LAN on ``vtnet1`` (segment, static), Unbound, automatic outbound NAT,
the web GUI + SSH reachable from the WAN side (that is the host, on plain QEMU) and NAT port
forwards to the LAN members that the router profile exposes with ``hostfwd``.
"""
from __future__ import annotations

import base64
import hashlib
import html
import shutil
from pathlib import Path
from typing import Any

from vmctl import netlab, qemu, runtime, ui
from vmctl.errors import VMError

BOOTSTRAP_COMPLETE_TOKEN = "==> pfSense installation complete!"
BOOTSTRAP_FAILED_TOKEN = "==> pfSense installation FAILED"
SHUTDOWN_GRACE_SEC = 120
CONFIG_VERSION = "23.3"  # config.xml schema written by pfSense CE 2.7.2
RC_LOCAL_MARKER = "bsdinstall script /etc/installerconfig"
BSDINSTALL_UMOUNT_NEEDLE = 'bsdinstall umount\nif [ "$ZFSBOOT_DISKS" ]; then'
BSDINSTALL_UMOUNT_PATCH = 'bsdinstall umount || [ -n "$ZFSBOOT_DISKS" ]\nif [ "$ZFSBOOT_DISKS" ]; then'
INSTALL_ISO_NAME = "install.iso"
CD_DRIVE_ID = "pfcd0"


def pfsense_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    cfg = vm.get("pfsense_config")
    if cfg is None:
        return None
    if not isinstance(cfg, dict):
        raise VMError("Invalid pfsense_config: expected object")
    return cfg


def pfsense_artifact_dir(vm: dict[str, Any]) -> Path:
    return runtime.resolve_path(vm["disk"]["path"]).parent / "pfsense"


def _identity(cfg: dict[str, Any]) -> tuple[str, str]:
    user = str(cfg.get("username") or "").strip()
    password = str(cfg.get("password") or "")
    if not user or not password:
        raise VMError("pfsense_config.username and pfsense_config.password are required (the web GUI/SSH account)")
    if user == "admin":
        raise VMError("pfsense_config.username: 'admin' is the built-in account; choose another name (it gets the same password)")
    return user, password


def bcrypt_hash(password: str) -> str:
    """pfSense stores ``bcrypt-hash`` entries; the ``bcrypt`` module is required only by this flow."""
    try:
        import bcrypt  # type: ignore[import-not-found,import-untyped,unused-ignore]
    except ImportError as exc:
        raise VMError("bootstrap-pfsense needs the Python 'bcrypt' module (apt: python3-bcrypt, pacman: python-bcrypt)") from exc
    return str(bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode())


def _e(text: Any) -> str:
    return html.escape(str(text), quote=False)


def _filter_rule(interface: str, descr: str, source: str, destination: str, protocol: str = "tcp", ipprotocol: str = "inet") -> str:
    return (
        "    <rule>\n"
        "      <type>pass</type>\n"
        f"      <interface>{interface}</interface>\n"
        f"      <ipprotocol>{ipprotocol}</ipprotocol>\n"
        f"      <protocol>{protocol}</protocol>\n"
        f"      <source>{source}</source>\n"
        f"      <destination>{destination}</destination>\n"
        f"      <descr>{_e(descr)}</descr>\n"
        "    </rule>\n"
    )


def render_config_xml(vm_name: str, vm: dict[str, Any], top: dict[str, Any], password_hash: str | None = None,
                      authorized_keys: list[str] | None = None) -> str:
    """The whole ``config.xml`` of the router.

    *password_hash* is computed with bcrypt when omitted; *authorized_keys* (OpenSSH public key
    lines, typically the project key of the VM) let ``vmctl shell`` log in without a password.
    """
    cfg = pfsense_config(vm)
    if cfg is None:
        raise VMError(f"VM '{vm_name}' does not define pfsense_config")
    user, password = _identity(cfg)
    hashed = password_hash or bcrypt_hash(password)
    keys_b64 = base64.b64encode("\n".join(authorized_keys or []).encode()).decode() if authorized_keys else ""
    keys_xml = f"      <authorizedkeys>{keys_b64}</authorizedkeys>\n" if keys_b64 else ""
    lan = top["lan"]
    timezone = str(cfg.get("timezone") or "UTC")
    dhcp = top["dhcp"]

    nat_rules = "".join(
        "    <rule>\n"
        "      <source><any/></source>\n"
        f"      <destination><network>wanip</network><port>{fwd['wan_port']}</port></destination>\n"
        "      <protocol>tcp</protocol>\n"
        f"      <target>{fwd['target']}</target>\n"
        f"      <local-port>{fwd['target_port']}</local-port>\n"
        "      <interface>wan</interface>\n"
        f"      <descr>{_e(fwd['descr'])}</descr>\n"
        "      <associated-rule-id>pass</associated-rule-id>\n"
        "    </rule>\n"
        for fwd in top["forwards"]
    )
    wan_admin = "".join(
        _filter_rule("wan", f"vmctl: {what} from the WAN side (the host)", "<any/>", f"<network>wanip</network><port>{port}</port>")
        for what, port in (("web GUI", 80), ("SSH", 22))
    )
    lan_rules = (
        _filter_rule("lan", "Default allow LAN to any rule", "<network>lan</network>", "<any/>", protocol="any")
        .replace("      <protocol>any</protocol>\n", "")
        + _filter_rule("lan", "Default allow LAN IPv6 to any rule", "<network>lan</network>", "<any/>", protocol="any", ipprotocol="inet6")
        .replace("      <protocol>any</protocol>\n", "")
    )
    return f"""<?xml version="1.0"?>
<pfsense>
  <version>{CONFIG_VERSION}</version>
  <system>
    <optimization>normal</optimization>
    <hostname>{_e(top['router_hostname'])}</hostname>
    <domain>{_e(lan['domain'])}</domain>
    <group>
      <name>all</name>
      <description>All Users</description>
      <scope>system</scope>
      <gid>1998</gid>
      <member>2000</member>
    </group>
    <group>
      <name>admins</name>
      <description>System Administrators</description>
      <scope>system</scope>
      <gid>1999</gid>
      <member>0</member>
      <member>2000</member>
      <priv>page-all</priv>
    </group>
    <user>
      <name>admin</name>
      <descr>System Administrator</descr>
      <scope>system</scope>
      <groupname>admins</groupname>
      <uid>0</uid>
      <priv>user-shell-access</priv>
{keys_xml}      <bcrypt-hash>{_e(hashed)}</bcrypt-hash>
    </user>
    <user>
      <name>{_e(user)}</name>
      <descr>vmctl lab administrator</descr>
      <scope>user</scope>
      <groupname>admins</groupname>
      <uid>2000</uid>
      <priv>user-shell-access</priv>
{keys_xml}      <bcrypt-hash>{_e(hashed)}</bcrypt-hash>
    </user>
    <nextuid>2001</nextuid>
    <nextgid>2000</nextgid>
    <timeservers>2.pfsense.pool.ntp.org</timeservers>
    <webgui>
      <protocol>http</protocol>
      <loginautocomplete/>
      <dashboardcolumns>2</dashboardcolumns>
      <webguicss>pfSense.css</webguicss>
    </webgui>
    <disablenatreflection>yes</disablenatreflection>
    <disablesegmentationoffloading/>
    <disablelargereceiveoffloading/>
    <disablechecksumoffloading/>
    <ipv6allow/>
    <maximumtableentries>400000</maximumtableentries>
    <powerd_ac_mode>hadp</powerd_ac_mode>
    <powerd_battery_mode>hadp</powerd_battery_mode>
    <powerd_normal_mode>hadp</powerd_normal_mode>
    <bogons>
      <interval>monthly</interval>
    </bogons>
    <already_run_config_upgrade/>
    <ssh>
      <enable>enabled</enable>
    </ssh>
    <enableserial/>
    <serialspeed>115200</serialspeed>
    <timezone>{_e(timezone)}</timezone>
    <language>en_US</language>
    <dnsserver>{top['dns_ip']}</dnsserver>
  </system>
  <interfaces>
    <wan>
      <enable/>
      <if>vtnet0</if>
      <descr>WAN</descr>
      <ipaddr>dhcp</ipaddr>
      <ipaddrv6>dhcp6</ipaddrv6>
      <dhcp6-ia-pd-len>0</dhcp6-ia-pd-len>
    </wan>
    <lan>
      <enable/>
      <if>vtnet1</if>
      <descr>LAN</descr>
      <ipaddr>{top['router_ip']}</ipaddr>
      <subnet>{lan['prefix']}</subnet>
    </lan>
  </interfaces>
  <dhcpd>
    <lan>
      <range>
        <from>{dhcp['start']}</from>
        <to>{dhcp['end']}</to>
      </range>
    </lan>
  </dhcpd>
  <dhcpdv6/>
  <unbound>
    <enable/>
    <dnssec/>
    <active_interface/>
    <outgoing_interface/>
    <custom_options/>
    <hideidentity/>
    <hideversion/>
    <dnssecstripped/>
  </unbound>
  <dnsmasq/>
  <snmpd>
    <syslocation/>
    <syscontact/>
    <rocommunity>public</rocommunity>
  </snmpd>
  <diag>
    <ipv6nat>
      <ipaddr/>
    </ipv6nat>
  </diag>
  <syslog>
    <filterdescriptions>1</filterdescriptions>
  </syslog>
  <nat>
    <outbound>
      <mode>automatic</mode>
    </outbound>
{nat_rules}  </nat>
  <filter>
{wan_admin}{lan_rules}    <separator>
      <lan/>
    </separator>
  </filter>
  <shaper/>
  <ipsec>
    <client/>
  </ipsec>
  <aliases/>
  <proxyarp/>
  <cron>
    <item>
      <minute>*/1</minute>
      <hour>*</hour>
      <mday>*</mday>
      <month>*</month>
      <wday>*</wday>
      <who>root</who>
      <command>/usr/sbin/newsyslog</command>
    </item>
    <item>
      <minute>1</minute>
      <hour>3</hour>
      <mday>*</mday>
      <month>*</month>
      <wday>*</wday>
      <who>root</who>
      <command>/etc/rc.periodic daily</command>
    </item>
    <item>
      <minute>15</minute>
      <hour>4</hour>
      <mday>*</mday>
      <month>*</month>
      <wday>6</wday>
      <who>root</who>
      <command>/etc/rc.periodic weekly</command>
    </item>
    <item>
      <minute>30</minute>
      <hour>5</hour>
      <mday>1</mday>
      <month>*</month>
      <wday>*</wday>
      <who>root</who>
      <command>/etc/rc.periodic monthly</command>
    </item>
    <item>
      <minute>1,31</minute>
      <hour>0-5</hour>
      <mday>*</mday>
      <month>*</month>
      <wday>*</wday>
      <who>root</who>
      <command>/usr/bin/nice -n20 adjkerntz -a</command>
    </item>
    <item>
      <minute>1</minute>
      <hour>3</hour>
      <mday>1</mday>
      <month>*</month>
      <wday>*</wday>
      <who>root</who>
      <command>/usr/bin/nice -n20 /etc/rc.update_bogons.sh</command>
    </item>
    <item>
      <minute>*/60</minute>
      <hour>*</hour>
      <mday>*</mday>
      <month>*</month>
      <wday>*</wday>
      <who>root</who>
      <command>/usr/bin/nice -n20 /usr/local/sbin/expiretable -v -t 3600 virusprot</command>
    </item>
    <item>
      <minute>30</minute>
      <hour>12</hour>
      <mday>*</mday>
      <month>*</month>
      <wday>*</wday>
      <who>root</who>
      <command>/usr/bin/nice -n20 /etc/rc.update_urltables</command>
    </item>
    <item>
      <minute>1</minute>
      <hour>0</hour>
      <mday>*</mday>
      <month>*</month>
      <wday>*</wday>
      <who>root</who>
      <command>/usr/bin/nice -n20 /etc/rc.update_pkg_metadata</command>
    </item>
  </cron>
  <wol/>
  <rrd>
    <enable/>
  </rrd>
  <widgets>
    <sequence>system_information:col1:show,interfaces:col2:show</sequence>
    <period>10</period>
  </widgets>
  <openvpn/>
  <dnshaper/>
  <qinqs/>
  <captiveportal/>
  <ntpd>
    <gps/>
  </ntpd>
  <ppps/>
  <staticroutes/>
  <gateways/>
  <vlans/>
</pfsense>
"""


def render_installerconfig(config_xml: str) -> str:
    """``bsdinstall script`` file: ZFS on the virtio disk, BIOS boot, then our config.xml in /cf/conf."""
    if "VMCTL_PFSENSE_CONFIG" in config_xml:
        raise VMError("config.xml must not contain the heredoc delimiter")
    return f"""# pfSense CE 2.7.2: bsdinstall script, empty VirtIO disk, BIOS + ZFS (rendered by vmctl).
export DISTRIBUTIONS="base.txz"
export BSDINSTALL_DISTDIR="/usr/freebsd-dist"
export nonInteractive="YES"
export ZFSBOOT_DISKS="vtbd0"
export ZFSBOOT_POOL_NAME="pfSense"
export ZFSBOOT_VDEV_TYPE="stripe"
export ZFSBOOT_PARTITION_SCHEME="GPT"
export ZFSBOOT_BOOT_TYPE="BIOS"
export ZFSBOOT_SWAP_SIZE="1g"
export ZFSBOOT_CONFIRM_LAYOUT=""
export ZFSBOOT_DATASETS="
/ROOT mountpoint=none
/ROOT/default mountpoint=/
/ROOT/default/cf mountpoint=/cf,setuid=off,exec=off
/tmp mountpoint=/tmp,exec=on,setuid=off
/home mountpoint=/home
/var mountpoint=/var
/var/cache mountpoint=/var/cache,setuid=off,exec=off,compression=off
/var/db mountpoint=/var/db,setuid=off,exec=off
/var/empty mountpoint=/var/empty
/var/log mountpoint=/var/log,setuid=off,exec=off
/var/tmp mountpoint=/var/tmp,setuid=off
/ROOT/default/var_cache_pkg mountpoint=/var/cache/pkg,setuid=off,exec=off
/ROOT/default/var_db_pkg mountpoint=/var/db/pkg,setuid=off,exec=off
"
#!/bin/sh
set -eu
mkdir -p /cf/conf
cat > /cf/conf/config.xml <<'VMCTL_PFSENSE_CONFIG'
{config_xml}VMCTL_PFSENSE_CONFIG
chmod 600 /cf/conf/config.xml
mkdir -p /var/db/vmctl
touch /var/db/vmctl/installed
sync
"""


def render_rc_local() -> str:
    """Installer entry point. Flush, then the token, then power off: the host waits for the poweroff."""
    return f"""#!/bin/sh
# vmctl unattended pfSense install: no console questions. Loaded by the FreeBSD rc of the ISO.
if ! mdconfig -l | grep -q md3; then
    mdconfig -a -u 3 -s 8m
    newfs /dev/md3
    mount /dev/md3 /mnt
    tar -C /etc -cf - . | tar -C /mnt -xf -
    sync
    umount /mnt
    mount /dev/md3 /etc
fi
export TERM=xterm
mkdir -p /tmp/bsdinstall_etc
if bsdinstall script /etc/installerconfig </dev/null; then
    sync
    echo "{BOOTSTRAP_COMPLETE_TOKEN}" > /dev/cuau0
    /sbin/shutdown -p now
else
    echo "{BOOTSTRAP_FAILED_TOKEN}" > /dev/cuau0
    cat /tmp/bsdinstall_log > /dev/cuau0 2>/dev/null
    sync
    /sbin/shutdown -p now
fi
"""


def patch_bsdinstall_script(text: str) -> str:
    """ZFS has no fstab mounts: ``umount -a`` may return 1 with nothing left; the pool export that follows is what matters."""
    if BSDINSTALL_UMOUNT_NEEDLE not in text:
        raise VMError("Unexpected bsdinstall version on the ISO: the ZFS unmount step to patch was not found (pfSense CE 2.7.2 expected)")
    return text.replace(BSDINSTALL_UMOUNT_NEEDLE, BSDINSTALL_UMOUNT_PATCH)


def install_iso_path(vm: dict[str, Any]) -> Path:
    return pfsense_artifact_dir(vm) / INSTALL_ISO_NAME


def _stamp(source_iso: Path, config_xml: str) -> str:
    st = source_iso.stat()
    return f"{source_iso.resolve()}\n{st.st_size}\n{st.st_mtime_ns}\n{hashlib.sha256(config_xml.encode()).hexdigest()}\n"


def ensure_install_iso(vm_name: str, vm: dict[str, Any], source_iso: Path, config_xml: str, dry_run: bool = False) -> Path:
    """The per-VM unattended ISO under ``artifacts/<vm>/pfsense/``, rebuilt when the source ISO or the config changes."""
    dest = install_iso_path(vm)
    stamp_path = dest.with_name(dest.name + ".source")
    stamp = _stamp(source_iso, config_xml) if source_iso.is_file() else None
    if dest.is_file() and stamp is not None and stamp_path.is_file() and stamp_path.read_text(encoding="utf-8") == stamp:
        ui.print_status("ok", f"Unattended pfSense ISO ready: {ui.pretty_path(dest)}")
        return dest
    runtime.require_command("xorriso")
    runtime.require_command("growisofs")
    ui.print_header("Build the unattended pfSense ISO")
    ui.print_kv("source", ui.pretty_path(source_iso))
    ui.print_kv("target", ui.pretty_path(dest))
    work = pfsense_artifact_dir(vm) / "iso-work"
    if work.exists() and not dry_run:
        shutil.rmtree(work)
    if not dry_run:
        work.mkdir(parents=True, exist_ok=True)
    rc_original = work / "rc.original"
    bsdinstall_script = work / "bsdinstall-script"
    runtime.run(["xorriso", "-osirrox", "on", "-indev", str(source_iso),
                 "-extract", "/etc/rc.local", str(rc_original),
                 "-extract", "/usr/libexec/bsdinstall/script", str(bsdinstall_script)], dry_run=dry_run, quiet=True)
    if not dry_run:
        if RC_LOCAL_MARKER not in rc_original.read_text(encoding="utf-8", errors="replace"):
            raise VMError("Incompatible ISO: this flow needs the offline pfSense CE 2.7.2 installer (rc.local runs bsdinstall script)")
        bsdinstall_script.write_text(patch_bsdinstall_script(bsdinstall_script.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
        bsdinstall_script.chmod(0o755)
        (work / "installerconfig").write_text(render_installerconfig(config_xml), encoding="utf-8")
        (work / "installerconfig").chmod(0o600)
        (work / "rc.local").write_text(render_rc_local(), encoding="utf-8")
        (work / "rc.local").chmod(0o755)
    partial = dest.with_name(dest.name + ".part")
    # A copy updated in place keeps the hidden FreeBSD El Torito extents; xorriso's replay drops them.
    runtime.run(["cp", str(source_iso), str(partial)], dry_run=dry_run, quiet=True)
    runtime.run(["growisofs", "-M", str(partial), "-d", "-l", "-r", "-V", "PFSENSE", "-graft-points",
                 f"/etc/installerconfig={work / 'installerconfig'}",
                 f"/etc/rc.local={work / 'rc.local'}",
                 f"/usr/libexec/bsdinstall/script={bsdinstall_script}"], dry_run=dry_run, quiet=True)
    if not dry_run:
        partial.replace(dest)
        shutil.rmtree(work, ignore_errors=True)
        if stamp is not None:
            stamp_path.write_text(stamp, encoding="utf-8")
    ui.print_status("ok", f"Unattended pfSense ISO: {ui.pretty_path(dest)}")
    return dest


def install_media_args(iso_path: Path) -> list[str]:
    """The installer CD on the second IDE channel of the ``pc`` machine, after the disk in the boot order."""
    return [
        "-drive", f"id={CD_DRIVE_ID},file={iso_path},format=raw,if=none,media=cdrom,readonly=on",
        "-device", f"ide-cd,drive={CD_DRIVE_ID},bus=ide.1,bootindex=2",
    ]


def check_profile(vm_name: str, vm: dict[str, Any]) -> None:
    """The scripted installer is written for BIOS + virtio disk + two virtio NICs (WAN, LAN)."""
    if vm["firmware"]["type"] != "bios":
        raise VMError(f"{vm_name}: pfSense installerconfig boots BIOS (ZFSBOOT_BOOT_TYPE=BIOS); set firmware.type to bios")
    if vm["disk"].get("interface", "virtio") != "virtio":
        raise VMError(f"{vm_name}: the installer targets vtbd0, the disk interface must be virtio")
    if str(vm["disk"]["format"]) != "qcow2":
        raise VMError(f"{vm_name}: use a qcow2 disk for pfSense")
    lab = netlab.lab_config(vm)
    if lab is None or lab["role"] != "pfsense":
        raise VMError(f"{vm_name}: bootstrap-pfsense needs network_lab.role = pfsense")
    kinds = [spec["type"] for spec in qemu.network_specs(vm, "runtime")]
    if kinds != ["user", "segment"]:
        raise VMError(f"{vm_name}: networks must be [WAN user (slirp), LAN segment] in that order (vtnet0, vtnet1)")
    if [spec["type"] for spec in qemu.network_specs(vm, "install")] != kinds:
        raise VMError(f"{vm_name}: the router keeps both NICs in every phase (no install/runtime split)")
