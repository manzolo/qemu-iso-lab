"""openSUSE AutoYaST install helpers: profile generation, boot artifacts, ISO packing.

Ported from kvm-lab's `install-tumbleweed.sh`. AutoYaST is YaST's answer file: one XML
profile handed to linuxrc on the kernel command line, which drives partitioning, software
selection and a chroot script inside the freshly installed system.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from vmctl import cloud_init, iso, runtime, ssh
from vmctl.errors import VMError


BOOTSTRAP_COMPLETE_TOKEN = "==> AutoYaST install complete!"
SEED_VOLUME_ID = "AUTOINST"


def autoyast_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    cfg = vm.get("autoyast_config")
    if cfg is None:
        return None
    if not isinstance(cfg, dict):
        raise VMError("Invalid autoyast_config: expected object")
    return cfg


def autoyast_artifact_dir(vm: dict[str, Any]) -> Path:
    return runtime.resolve_path(vm["disk"]["path"]).parent / "autoyast"


def kernel_append(vm: dict[str, Any]) -> str:
    """linuxrc arguments.

    The seed is a **USB stick**, not a second CD: with two CD drives YaST cannot tell which
    one carries the installation repository and stops mid-install on "Insert 'cd-<id>'
    (Disc 1)" (observed live). One CD, one USB stick, no ambiguity.

    ``autoyast_config.install_repo`` adds an explicit ``install=`` source, for an installer
    ISO that carries no packages (the NET image) or to install from an online mirror.
    """
    cfg = autoyast_config(vm) or {}
    extra = str(cfg.get("kernel_append") or "").strip()
    repo = str(cfg.get("install_repo") or "").strip()
    append = (
        "autoyast=usb:///autoinst.xml ifcfg=*=dhcp netsetup=dhcp textmode=1 "
        "console=ttyS0,115200 console=tty0"
    )
    if repo:
        append = f"install={repo} {append}"
    return f"{append} {extra}".strip()


def _resolve_ssh_pubkey(vm: dict[str, Any]) -> str | None:
    ssh_cfg = vm.get("ssh_provision")
    if not isinstance(ssh_cfg, dict):
        return None
    pubkey_path = ssh.resolve_ssh_public_key(vm, ssh_cfg)
    if pubkey_path is None:
        return None
    return pubkey_path.read_text(encoding="utf-8").strip()


def _list_block(tag: str, items: list[str], indent: str) -> str:
    if not items:
        return ""
    rows = "\n".join(f"{indent}  <{tag}>{escape(item)}</{tag}>" for item in items)
    return rows


def chroot_script(vm_name: str, vm: dict[str, Any]) -> str:
    """The shell script AutoYaST runs inside the installed system, completion token included.

    Ordering is the project invariant: everything else first, then ``sync`` and
    ``blockdev --flushbufs``, then the token. The installer reboots afterwards and QEMU,
    started with ``-no-reboot``, exits on its own.
    """
    cfg = autoyast_config(vm) or {}
    username = str(cfg.get("username") or "").strip()
    realname = str(cfg.get("realname") or username).strip()
    password_hash = str(cfg.get("password_hash") or "").strip()
    hostname = str(cfg.get("hostname") or vm_name).strip()
    disk_device = str(cfg.get("disk_device") or "vda").strip()
    autologin = bool(cfg.get("autologin", True))
    firewall_services: list[str] = list(cfg.get("open_firewall_services") or ["ssh"])
    extra_commands: list[str] = list(cfg.get("chroot_commands") or [])
    pubkey = _resolve_ssh_pubkey(vm)

    lines = [
        "set -x",
        "getent group users >/dev/null || groupadd -g 100 users",
        f'id {username} >/dev/null 2>&1 || useradd -m -u 1000 -g users -G wheel -c "{realname}" -s /bin/bash {username}',
        f"usermod -p '{password_hash}' {username}",
        f"echo {hostname} > /etc/hostname",
        "install -d -m0755 /etc/sudoers.d",
        f"printf '%s\\n' '{username} ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/{username}",
        f"chmod 0440 /etc/sudoers.d/{username}",
        "systemctl enable sshd.service || true",
        "systemctl enable serial-getty@ttyS0.service || true",
    ]
    # openSUSE installs firewalld with only dhcpv6-client open: sshd listens and the host's
    # forwarded port still hangs at the SSH banner (verified live on the running guest).
    for service in firewall_services:
        lines.append(
            f"command -v firewall-offline-cmd >/dev/null && firewall-offline-cmd --add-service={service} || true"
        )
    if pubkey:
        lines += [
            f"install -d -m0700 -o {username} -g users /home/{username}/.ssh",
            f"printf '%s\\n' '{pubkey}' > /home/{username}/.ssh/authorized_keys",
            f"chmod 0600 /home/{username}/.ssh/authorized_keys",
            f"chown -R {username}:users /home/{username}/.ssh",
        ]
    if autologin:
        lines += [
            "install -d -m0755 /etc/gdm",
            "cat > /etc/gdm/custom.conf <<'GDMEOF'",
            "[daemon]",
            "AutomaticLoginEnable=True",
            f"AutomaticLogin={username}",
            "GDMEOF",
            "if [ -f /etc/sysconfig/displaymanager ]; then",
            f"  sed -i 's/^DISPLAYMANAGER_AUTOLOGIN=.*/DISPLAYMANAGER_AUTOLOGIN=\"{username}\"/' /etc/sysconfig/displaymanager",
            "fi",
        ]
    lines += extra_commands
    lines += [
        "sync",
        f"blockdev --flushbufs /dev/{disk_device} /dev/{disk_device}1 /dev/{disk_device}2 || true",
        f'echo "{BOOTSTRAP_COMPLETE_TOKEN}" > /dev/ttyS0 2>&1 || true',
        f'echo "{BOOTSTRAP_COMPLETE_TOKEN}" > /dev/console 2>&1 || true',
        f'echo "{BOOTSTRAP_COMPLETE_TOKEN}"',
    ]
    return "\n".join(lines) + "\n"


def render_autoyast(vm_name: str, vm: dict[str, Any]) -> str:
    cfg = autoyast_config(vm)
    if cfg is None:
        raise VMError("VM profile does not define autoyast_config")

    username = str(cfg.get("username") or "").strip()
    password_hash = str(cfg.get("password_hash") or "").strip()
    if not username:
        raise VMError("autoyast_config.username is required")
    if not password_hash:
        raise VMError("autoyast_config.password_hash is required")

    root_hash = str(cfg.get("root_password_hash") or password_hash).strip()
    keymap = str(cfg.get("keyboard_layout") or "english-us").strip()
    language = str(cfg.get("language") or "en_US").strip()
    timezone = str(cfg.get("timezone") or "UTC").strip()
    disk_device = str(cfg.get("disk_device") or "vda").strip()
    products: list[str] = list(cfg.get("products") or ["openSUSE"])
    patterns: list[str] = list(cfg.get("patterns") or ["enhanced_base", "gnome"])
    packages: list[str] = list(cfg.get("packages") or ["openssh", "qemu-guest-agent", "NetworkManager", "vim"])
    enable_services: list[str] = list(cfg.get("enable_services") or ["NetworkManager", "sshd", "qemu-guest-agent"])
    default_target = str(cfg.get("default_target") or "graphical").strip()
    final_reboot = "true" if cfg.get("final_reboot", True) else "false"
    script = chroot_script(vm_name, vm)

    return f"""<?xml version="1.0"?>
<!DOCTYPE profile>
<profile xmlns="http://www.suse.com/1.0/yast2ns" xmlns:config="http://www.suse.com/1.0/configns">
  <general>
    <mode>
      <confirm config:type="boolean">false</confirm>
      <final_reboot config:type="boolean">{final_reboot}</final_reboot>
    </mode>
  </general>
  <keyboard>
    <keymap>{escape(keymap)}</keymap>
  </keyboard>
  <language>
    <language>{escape(language)}</language>
    <languages>{escape(language)},en_US</languages>
  </language>
  <timezone>
    <hwclock>UTC</hwclock>
    <timezone>{escape(timezone)}</timezone>
  </timezone>
  <report>
    <errors><log config:type="boolean">true</log><show config:type="boolean">false</show><timeout config:type="integer">0</timeout></errors>
    <warnings><log config:type="boolean">true</log><show config:type="boolean">false</show><timeout config:type="integer">0</timeout></warnings>
    <messages><log config:type="boolean">true</log><show config:type="boolean">false</show><timeout config:type="integer">0</timeout></messages>
    <yesno_messages><log config:type="boolean">true</log><show config:type="boolean">false</show><timeout config:type="integer">0</timeout></yesno_messages>
  </report>
  <users config:type="list">
    <user>
      <username>root</username>
      <encrypted config:type="boolean">true</encrypted>
      <user_password>{escape(root_hash)}</user_password>
    </user>
  </users>
  <partitioning config:type="list">
    <drive>
      <device>/dev/{escape(disk_device)}</device>
      <disklabel>gpt</disklabel>
      <initialize config:type="boolean">true</initialize>
      <use>all</use>
      <partitions config:type="list">
        <partition>
          <create config:type="boolean">true</create>
          <filesystem config:type="symbol">vfat</filesystem>
          <format config:type="boolean">true</format>
          <mount>/boot/efi</mount>
          <partition_id config:type="integer">259</partition_id>
          <size>512M</size>
        </partition>
        <partition>
          <create config:type="boolean">true</create>
          <filesystem config:type="symbol">btrfs</filesystem>
          <format config:type="boolean">true</format>
          <mount>/</mount>
          <partition_id config:type="integer">131</partition_id>
          <size>max</size>
        </partition>
      </partitions>
    </drive>
  </partitioning>
  <bootloader>
    <loader_type>grub2-efi</loader_type>
  </bootloader>
  <services-manager>
    <default_target>{escape(default_target)}</default_target>
    <services>
      <enable config:type="list">
{_list_block("service", enable_services, "      ")}
      </enable>
      <disable config:type="list">
        <service>autoyast</service>
        <service>autoyast-initscripts</service>
        <service>autoyast-second-stage</service>
        <service>YaST2-Firstboot</service>
        <service>YaST2-Second-Stage</service>
      </disable>
    </services>
  </services-manager>
  <software>
    <products config:type="list">
{_list_block("product", products, "      ")}
    </products>
    <patterns config:type="list">
{_list_block("pattern", patterns, "      ")}
    </patterns>
    <packages config:type="list">
{_list_block("package", packages, "      ")}
    </packages>
  </software>
  <scripts>
    <chroot-scripts config:type="list">
      <script>
        <filename>vmctl-post.sh</filename>
        <interpreter>shell</interpreter>
        <chrooted config:type="boolean">true</chrooted>
        <feedback config:type="boolean">false</feedback>
        <source><![CDATA[
{script}]]></source>
      </script>
    </chroot-scripts>
  </scripts>
</profile>
"""


def extract_autoyast_boot_artifacts(vm: dict[str, Any], iso_path: Path, dry_run: bool = False) -> tuple[Path, Path]:
    artifact_dir = autoyast_artifact_dir(vm)
    kernel_path = artifact_dir / "linux"
    initrd_path = artifact_dir / "initrd"
    boot = vm.get("installer_boot", {})
    kernel_member = str(boot.get("kernel") or "boot/x86_64/loader/linux")
    initrd_member = str(boot.get("initrd") or "boot/x86_64/loader/initrd")
    iso.extract_iso_member(iso_path, kernel_member, kernel_path, dry_run=dry_run)
    iso.extract_iso_member(iso_path, initrd_member, initrd_path, dry_run=dry_run)
    return kernel_path, initrd_path


def create_autoyast_iso(vm_name: str, vm: dict[str, Any], dry_run: bool = False) -> Path:
    return cloud_init.create_iso_with_files(
        autoyast_artifact_dir(vm),
        {"autoinst.xml": render_autoyast(vm_name, vm)},
        dry_run=dry_run,
        volume_id=SEED_VOLUME_ID,
    )


def install_media_args(install_iso: Path, seed_iso: Path) -> list[str]:
    """The installer ISO as the only SATA CD-ROM, the seed as a USB stick.

    linuxrc scans ``/dev/sr*`` for the installation repository, so the install medium must be
    a real CD-ROM (a virtio CD would be invisible). The answer file instead travels on
    ``usb://``: a second CD makes YaST ask which drive holds Disc 1 and the install stops.
    """
    return [
        "-drive", f"id=aycd0,file={install_iso},format=raw,if=none,media=cdrom,readonly=on",
        "-device", "ide-cd,drive=aycd0,bus=ide.0",
        "-drive", f"id=ayseed,file={seed_iso},format=raw,if=none,readonly=on",
        "-device", "usb-storage,drive=ayseed,removable=on",
    ]
