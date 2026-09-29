"""Slackware unattended install: a script run from the install DVD's own shell (``bootstrap-slackware``).

Slackware's ``setup`` is a dialog program with no answer file, but its install medium is a
complete shell: after ``rc.S`` prints a decorative ``slackware login:`` (a plain ``read``) busybox
init spawns a root shell on the console. The host boots the DVD's kernel and initrd with the
serial port as console, answers that prompt, mounts a small seed CD and runs ``install.sh`` from
it. The script partitions the disk (one bootable Linux partition, ext4), installs the ADD and REC
packages of the chosen series with ``installpkg --root`` straight from the DVD (plus the named
extras, minus the excluded ones), configures the target (fstab, DHCP, timezone, keymap, locale,
root and user, sudo, the project SSH key, sshd, a serial getty), makes tty1 log the user in and
``startx`` Xfce from ``~/.bash_profile`` (no display manager: xdm cannot autologin, KDM and SDDM
would pull KDE in), installs LILO and ends with sync -> token -> poweroff (see CLAUDE.md).
The same script installs 13.0 (2009) and 15.0 (2022): the differences are data in the profile.
"""
from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from vmctl import cloud_init, iso, runtime
from vmctl.errors import VMError

BOOTSTRAP_COMPLETE_TOKEN = "==> Slackware installation complete!"
BOOTSTRAP_FAILED_TOKEN = "==> Slackware installation FAILED"

# rc.S: `cat /etc/issue; echo -n "slackware login: "; read BOGUS_LOGIN`, then init's `-/bin/sh`
# (bash on 15.0, busybox ash on 13.0) prints PS1 `\u@\h:\w# ` from /etc/profile: the working
# directory is / on both, the user and host parts differ (ash leaves \u empty; the hostname is
# never set in the installer), so `:/# ` is what both shells print.
# Before that, the initrd asks whether to load a non-US keyboard map (a `read` too): Enter keeps
# the US map, the installed system gets `keymap` from the script (first live run, 2026-09-29).
LIVE_KEYMAP_PROMPT = "Enter 1 to select a keyboard map: "
LIVE_LOGIN_PROMPT = "slackware login: "
LIVE_SHELL_PROMPT = ":/# "

SEED_VOLUME_ID = "VMCTLSLACK"
SEED_MOUNTPOINT = "/vmctl-seed"
DVD_MOUNTPOINT = "/vmctl-dvd"
TARGET = "/mnt"

# The DVD's own isolinux.cfg line (13.0 and 15.0 alike), then the serial port as the console:
# init's shell lands on /dev/console, which is the last console= given.
LIVE_KERNEL_APPEND = ("initrd=initrd.img load_ramdisk=1 prompt_ramdisk=0 rw printk.time=0 nomodeset "
                      "SLACK_KERNEL=huge.s console=tty0 console=ttyS0,115200")
KERNEL_MEMBER = "kernels/huge.s/bzImage"
INITRD_MEMBER = "isolinux/initrd.img"

DEFAULT_SERIES = ["a", "ap", "l", "n", "x", "xap", "xfce"]
# sudo is OPT in ap on every release, Xfce is OPT in xap on 13.x (its own series from 14.0).
# iproute2 is tagged OPT on the 15.0 DVD while its rc.inet1 calls /sbin/ip (first live run, 2026-09-29).
DEFAULT_PACKAGES = ["sudo", "iproute2"]
# What would lock or blank the screen before the report's screenshot, and the big desktop
# applications the DVD's REC tags would drag in (firefox, seamonkey, thunderbird, gimp: ~400 MB).
# xfce4-pulseaudio-plugin crashes in a loop without a sound server and parks a dialog on the desktop (15.0, 2026-09-29).
DEFAULT_EXCLUDE = ["xscreensaver", "xfce4-screensaver", "xfce4-pulseaudio-plugin", "mozilla-firefox", "seamonkey", "mozilla-thunderbird", "gimp"]


def slackware_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    cfg = vm.get("slackware_config")
    if cfg is None:
        return None
    if not isinstance(cfg, dict):
        raise VMError("Invalid slackware_config: expected object")
    return cfg


def slackware_artifact_dir(vm: dict[str, Any]) -> Path:
    return runtime.resolve_path(vm["disk"]["path"]).parent / "slackware"


def check_profile(vm_name: str, vm: dict[str, Any]) -> None:
    cfg = slackware_config(vm)
    if not cfg or not str(cfg.get("username") or "").strip() or not str(cfg.get("password") or "").strip():
        raise VMError(f"{vm_name}: slackware_config.username and password are required")
    if vm["firmware"]["type"] != "bios" or vm.get("machine") != "pc":
        raise VMError(f"{vm_name}: the Slackware install boots LILO from the MBR: firmware bios and machine pc")
    if vm["disk"].get("interface", "virtio") != "ide":
        raise VMError(f"{vm_name}: the Slackware install targets the IDE disk (/dev/sda under libata on every release)")
    if not vm.get("ssh_provision"):
        raise VMError(f"{vm_name}: the Slackware bootstrap verifies the guest over SSH: ssh_provision is required")
    for key in ("series", "full_series", "packages", "exclude"):
        value = cfg.get(key)
        if value is not None and (not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value)):
            raise VMError(f"{vm_name}: slackware_config.{key} must be a list of names")


def _sh_list(values: list[str]) -> str:
    return " ".join(shlex.quote(v) for v in values)


def render_install_script(vm_name: str, vm: dict[str, Any], keys: list[str]) -> str:
    """The bash script the live shell runs from the seed: one script for every release."""
    cfg = slackware_config(vm)
    if cfg is None:
        raise VMError("VM profile does not define slackware_config")
    username = str(cfg.get("username") or "").strip()
    password = str(cfg.get("password") or "").strip()
    if not username or not password:
        raise VMError("slackware_config.username and password are required for bootstrap")
    hostname = str(cfg.get("hostname") or vm_name).strip()
    timezone = str(cfg.get("timezone") or "UTC").strip()
    keymap = str(cfg.get("keymap") or "us").strip()
    xkb = str(cfg.get("xkb_layout") or keymap).strip()
    locale = str(cfg.get("locale") or "en_US.UTF-8").strip()
    xinitrc = str(cfg.get("xinitrc") or "xinitrc.xfce").strip()
    session_process = str(cfg.get("session_process") or "xfce4-session").strip()
    series = list(cfg.get("series") or DEFAULT_SERIES)
    # Series installed whole (every package, not only the tagfile's ADD+REC): the KDE sessions need
    # l-series libraries the tagfiles leave OPT (14.2's ksmserver wants libsqlite3, 15.0's
    # plasma_session libFLAC, 2026-09-29).
    full_series = [name for name in (cfg.get("full_series") or []) if name in series]
    packages = list(cfg.get("packages") or DEFAULT_PACKAGES)
    exclude = list(cfg.get("exclude") or DEFAULT_EXCLUDE)
    keys_text = "\n".join(k.strip() for k in keys if k.strip())
    return f"""\
#!/bin/bash
# vmctl: Slackware unattended install, run from the install DVD's shell (see vmctl/slackware.py).
set -Eeuo pipefail
export PATH="$PATH:/bin:/sbin:/usr/bin:/usr/sbin:/usr/lib/setup"

vmctl_poweroff() {{
    sync
    poweroff -f 2>/dev/null || poweroff 2>/dev/null || echo o > /proc/sysrq-trigger
}}
# Say what broke and power off: the host learns of a failure from the token, not from its timeout.
trap 'echo "{BOOTSTRAP_FAILED_TOKEN}: line $LINENO: $BASH_COMMAND"; sleep 1; vmctl_poweroff' ERR

USERNAME={shlex.quote(username)}
PASSWORD={shlex.quote(password)}
HOSTNAME_SHORT={shlex.quote(hostname)}
TIMEZONE={shlex.quote(timezone)}
KEYMAP={shlex.quote(keymap)}
XKB_LAYOUT={shlex.quote(xkb)}
LOCALE={shlex.quote(locale)}
XINITRC={shlex.quote(xinitrc)}
SERIES="{_sh_list(series)}"
FULL_SERIES="{_sh_list(full_series)}"
EXTRA_PACKAGES="{_sh_list(packages)}"
EXCLUDE="{_sh_list(exclude)}"

echo "==> Mounting the install DVD..."
mkdir -p {DVD_MOUNTPOINT}
DVD=""
for dev in /dev/sr0 /dev/sr1 /dev/sr2 /dev/hdc /dev/hdd /dev/scd0 /dev/scd1; do
    [ -b "$dev" ] || continue
    if mount -t iso9660 -o ro "$dev" {DVD_MOUNTPOINT} 2>/dev/null; then
        if [ -d {DVD_MOUNTPOINT}/slackware64 ]; then DVD="$dev"; break; fi
        umount {DVD_MOUNTPOINT}
    fi
done
[ -n "$DVD" ] || {{ echo "no Slackware DVD found on the CD-ROM drives"; false; }}
echo "    DVD on $DVD"

echo "==> Partitioning the disk..."
DISK=""
for dev in /dev/sda /dev/vda /dev/hda; do
    if [ -b "$dev" ]; then DISK="$dev"; break; fi
done
[ -n "$DISK" ] || {{ echo "no target disk found"; false; }}
# One bootable Linux partition over the whole disk, DOS label: the same line for the 2009
# sfdisk (start,size,type,bootable) and today's script format.
printf ',,L,*\\n' | sfdisk "$DISK" >/dev/null
sleep 2
PART="${{DISK}}1"
[ -b "$PART" ] || {{ udevadm settle 2>/dev/null || true; sleep 2; }}
[ -b "$PART" ] || {{ echo "$PART did not appear"; false; }}

echo "==> Formatting $PART (ext4)..."
mkfs.ext4 -q -F -L slackroot "$PART"
mount -t ext4 "$PART" {TARGET}

echo "==> Installing packages from the DVD..."
INSTALLPKG="$(command -v installpkg || echo /usr/lib/setup/installpkg)"
TERSE=""
if "$INSTALLPKG" --help 2>&1 | grep -c -- '--terse' >/dev/null; then TERSE="--terse"; fi
excluded() {{
    local name="$1" x
    for x in $EXCLUDE; do [ "$x" = "$name" ] && return 0; done
    return 1
}}
installed_log() {{
    # $1 = package name: is its entry in the target's package log? grep -c reads the whole
    # listing: under pipefail, ls piped into a grep -q that quits at the first match gets a SIGPIPE
    # once the listing outgrows the pipe, and iproute2 sits early in the alphabet (2026-09-29).
    [ "$(ls "{TARGET}/var/log/packages/" | grep -c "^$1-[^-]*-[^-]*-[^-]*$")" -gt 0 ]
}}
count=0
install_one() {{
    # $1 = package file; installpkg reads the whole package name from the file name
    if [ -n "$TERSE" ]; then
        "$INSTALLPKG" --root {TARGET} $TERSE "$1" >/dev/null
    else
        "$INSTALLPKG" --root {TARGET} "$1" >/dev/null
    fi
    count=$((count + 1))
    if [ $((count % 25)) = 0 ]; then echo "    $count packages installed ($(basename "$1"))"; fi
}}
for s in $SERIES; do
    dir="{DVD_MOUNTPOINT}/slackware64/$s"
    [ -d "$dir" ] || {{ echo "    series $s is not on this DVD, skipped"; continue; }}
    # name -> file, from the file names (name-version-arch-build.t?z): never a prefix match
    index=/tmp/vmctl-index-$s
    : > "$index"
    for f in "$dir"/*.t?z; do
        b="${{f##*/}}"; b="${{b%.t?z}}"
        echo "${{b%-*-*-*}} $f" >> "$index"
    done
    wanted=/tmp/vmctl-wanted-$s
    grep -E ':(ADD|REC)$' "$dir/tagfile" | cut -d: -f1 > "$wanted" || true
    for f in $FULL_SERIES; do [ "$f" = "$s" ] && cut -d' ' -f1 "$index" > "$wanted"; done  # the whole series
    for p in $EXTRA_PACKAGES; do
        grep -q "^$p " "$index" && echo "$p" >> "$wanted" || true
    done
    echo "    series $s: $(sort -u "$wanted" | wc -l) packages"
    for name in $(sort -u "$wanted"); do
        excluded "$name" && continue
        file="$(grep "^$name " "$index" | sed -n '1p' | cut -d' ' -f2-)"
        [ -n "$file" ] || {{ echo "    $name: tagged but not on the DVD, skipped"; continue; }}
        install_one "$file"
    done
done
echo "    $count packages installed"
for p in $EXTRA_PACKAGES; do
    if ! installed_log "$p"; then
        # not among the installed packages: say what the DVD has under that name and install it
        # again, this time with installpkg's own output
        file="$(grep -h "^$p " /tmp/vmctl-index-* 2>/dev/null | sed -n '1p' | cut -d' ' -f2-)"
        echo "$p is not in /var/log/packages after the series pass (${{file:-no such package on the DVD}})"
        [ -n "$file" ] || false
        "$INSTALLPKG" --root {TARGET} "$file"
        installed_log "$p" || {{ echo "$p is still missing"; false; }}
    fi
done

echo "==> Configuring the system..."
cat > {TARGET}/etc/fstab <<EOF
$PART            /                ext4        defaults         1   1
devpts           /dev/pts         devpts      gid=5,mode=620   0   0
proc             /proc            proc        defaults         0   0
tmpfs            /dev/shm         tmpfs       nosuid,nodev,noexec 0   0
EOF
echo "$HOSTNAME_SHORT.localdomain" > {TARGET}/etc/HOSTNAME
cat > {TARGET}/etc/hosts <<EOF
127.0.0.1       localhost
127.0.1.1       $HOSTNAME_SHORT.localdomain $HOSTNAME_SHORT
EOF
sed -i 's/^USE_DHCP\\[0\\]=.*/USE_DHCP[0]="yes"/' {TARGET}/etc/rc.d/rc.inet1.conf
if [ -f "{TARGET}/usr/share/zoneinfo/$TIMEZONE" ]; then
    cp "{TARGET}/usr/share/zoneinfo/$TIMEZONE" {TARGET}/etc/localtime
    # what timeconfig does: a symlink into the target's zoneinfo. The etc package ships the
    # file as a symlink already, and writing through it from the installer resolves outside
    # the target (ENOENT on the first live run, 2026-09-29).
    ln -sf "/usr/share/zoneinfo/$TIMEZONE" {TARGET}/etc/localtime-copied-from
fi
echo "UTC" > {TARGET}/etc/hardwareclock
printf '#!/bin/sh\\nif [ -x /usr/bin/loadkeys ]; then /usr/bin/loadkeys %s.map; fi\\n' "$KEYMAP" > {TARGET}/etc/rc.d/rc.keymap
chmod 755 {TARGET}/etc/rc.d/rc.keymap
if grep -q '^export LANG=' {TARGET}/etc/profile.d/lang.sh 2>/dev/null; then
    sed -i "s/^export LANG=.*/export LANG=$LOCALE/" {TARGET}/etc/profile.d/lang.sh
else
    printf 'export LANG=%s\\n' "$LOCALE" >> {TARGET}/etc/profile.d/lang.sh
fi
mkdir -p {TARGET}/etc/X11/xorg.conf.d
cat > {TARGET}/etc/X11/xorg.conf.d/90-keyboard-layout.conf <<EOF
Section "InputClass"
    Identifier "keyboard-all"
    MatchIsKeyboard "on"
    Option "XkbLayout" "$XKB_LAYOUT"
EndSection
EOF

echo "==> Root and $USERNAME..."
mount --bind /dev {TARGET}/dev
mount --bind /proc {TARGET}/proc
mount --bind /sys {TARGET}/sys 2>/dev/null || true
echo "root:$PASSWORD" | chroot {TARGET} /usr/sbin/chpasswd
GROUPS_EXTRA=""
for g in wheel audio video cdrom floppy plugdev power netdev lp scanner; do
    chroot {TARGET} getent group "$g" >/dev/null 2>&1 && GROUPS_EXTRA="$GROUPS_EXTRA,$g"
done
chroot {TARGET} /usr/sbin/useradd -m -g users -G "${{GROUPS_EXTRA#,}}" -s /bin/bash "$USERNAME"
echo "$USERNAME:$PASSWORD" | chroot {TARGET} /usr/sbin/chpasswd
printf '%s ALL=(ALL) NOPASSWD: ALL\\n' "$USERNAME" >> {TARGET}/etc/sudoers
chmod 440 {TARGET}/etc/sudoers
HOME_DIR={TARGET}/home/$USERNAME
mkdir -p "$HOME_DIR/.ssh"
cat > "$HOME_DIR/.ssh/authorized_keys" <<'EOF'
{keys_text}
EOF
chmod 700 "$HOME_DIR/.ssh"
chmod 600 "$HOME_DIR/.ssh/authorized_keys"
# Xfce 4.10 and 4.12 ask about the panel at their first start (14.0 and 14.1, 2026-09-29): the
# user gets the default layout, so the desktop comes up complete and the clip shows no dialog.
if [ "$XINITRC" = xinitrc.xfce ] && [ -f {TARGET}/etc/xdg/xfce4/panel/default.xml ]; then
    mkdir -p "$HOME_DIR/.config/xfce4/xfconf/xfce-perchannel-xml"
    cp {TARGET}/etc/xdg/xfce4/panel/default.xml "$HOME_DIR/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-panel.xml"
    chroot {TARGET} chown -R "$USERNAME:users" "/home/$USERNAME/.config"
fi

echo "==> sshd, serial console, autologin and the desktop session..."
if grep -q '^#*UseDNS' {TARGET}/etc/ssh/sshd_config; then
    sed -i 's/^#*UseDNS.*/UseDNS no/' {TARGET}/etc/ssh/sshd_config
else
    echo 'UseDNS no' >> {TARGET}/etc/ssh/sshd_config
fi
chmod 755 {TARGET}/etc/rc.d/rc.sshd
echo 's1:12345:respawn:/sbin/agetty -L ttyS0 115200 vt100' >> {TARGET}/etc/inittab
cat > {TARGET}/usr/local/sbin/vmctl-autologin <<EOF
#!/bin/sh
# vmctl: tty1 logs $USERNAME in without a password (agetty -l); ~/.bash_profile starts X.
exec /bin/login -f $USERNAME
EOF
chmod 755 {TARGET}/usr/local/sbin/vmctl-autologin
if chroot {TARGET} /sbin/agetty --help 2>&1 | grep -c -- '--autologin' >/dev/null; then
    sed -i "s|^c1:.*|c1:1235:respawn:/sbin/agetty --noclear --autologin $USERNAME 38400 tty1 linux|" {TARGET}/etc/inittab
else
    # util-linux before 2.20 (Slackware 13.x): no --autologin, but -l runs any login program
    sed -i 's|^c1:.*|c1:1235:respawn:/sbin/agetty -n -l /usr/local/sbin/vmctl-autologin 38400 tty1 linux|' {TARGET}/etc/inittab
fi
grep -q '^c1:.*autologin' {TARGET}/etc/inittab
cat > "$HOME_DIR/.bash_profile" <<'EOF'
[ -f ~/.bashrc ] && . ~/.bashrc
# vmctl: the console login on tty1 becomes the desktop session
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
    exec startx >"$HOME/.startx.log" 2>&1
fi
EOF
ln -sf "$XINITRC" {TARGET}/etc/X11/xinit/xinitrc
cat > "$HOME_DIR/.xinitrc" <<EOF
#!/bin/sh
xset s off -dpms 2>/dev/null
exec /bin/sh /etc/X11/xinit/$XINITRC
EOF
chmod 755 "$HOME_DIR/.xinitrc"
chroot {TARGET} chown -R "$USERNAME:users" "/home/$USERNAME"
[ -f {TARGET}/etc/X11/xinit/$XINITRC ] || {{ echo "$XINITRC is not on the target: is the desktop installed?"; false; }}

echo "==> LILO..."
KERNEL="$(cd {TARGET}/boot && ls vmlinuz-huge-* 2>/dev/null | sed -n '1p')"
[ -n "$KERNEL" ] || {{ echo "no huge kernel under /boot"; false; }}
cat > {TARGET}/etc/lilo.conf <<EOF
# vmctl: LILO on the MBR, the huge kernel (no initrd), the serial port as a console too
boot = $DISK
lba32
prompt
timeout = 30
vga = normal
append = "console=tty0 console=ttyS0,115200"
image = /boot/$KERNEL
  root = $PART
  label = Linux
  read-only
EOF
chroot {TARGET} /sbin/lilo >/dev/null

echo "==> Finishing..."
umount {TARGET}/sys 2>/dev/null || true
umount {TARGET}/proc
umount {TARGET}/dev
sync
umount {TARGET}
sync
echo "{BOOTSTRAP_COMPLETE_TOKEN}"
vmctl_poweroff
"""


def create_seed_iso(vm_name: str, vm: dict[str, Any], keys: list[str], dry_run: bool = False) -> Path:
    """The seed CD: install.sh alone (the live trigger mounts it and runs it)."""
    return cloud_init.create_iso_with_files(
        slackware_artifact_dir(vm), {"install.sh": render_install_script(vm_name, vm, keys), "run.sh": render_run_script()},
        dry_run=dry_run, volume_id=SEED_VOLUME_ID)


def seed_iso_drive_args(iso_path: Path) -> list[str]:
    """The seed as the second IDE CD-ROM (index 3, hdd): -cdrom holds the DVD at index 2."""
    return ["-drive", f"file={iso_path},format=raw,if=ide,index=3,media=cdrom,readonly=on"]


def render_run_script() -> str:
    """``run.sh``, POSIX sh: runs install.sh under a real bash. The installer initrd of 15.0 has
    bash; the ones of 13.0 and 13.37 ship busybox ash under that name (`set: illegal option -E`,
    first live run of 13.37, 2026-09-29), so the DVD's own bash package (a/bash-*.t?z) is
    unpacked into the RAM disk, which has xz, libncurses and glibc for it."""
    return f"""#!/bin/sh
# vmctl: install.sh under a real bash (the 13.x installer initrds call busybox ash "bash")
if bash -c 'set -E' 2>/dev/null; then
    exec bash {SEED_MOUNTPOINT}/install.sh
fi
echo "==> The initrd's bash is busybox: unpacking the DVD's bash package..."
mkdir -p {DVD_MOUNTPOINT}
for dev in /dev/sr0 /dev/sr1 /dev/sr2 /dev/hdc /dev/hdd /dev/scd0 /dev/scd1; do
    [ -b "$dev" ] || continue
    mount -t iso9660 -o ro "$dev" {DVD_MOUNTPOINT} 2>/dev/null || continue
    [ -d {DVD_MOUNTPOINT}/slackware64 ] && break
    umount {DVD_MOUNTPOINT}
done
pkg=""
for f in {DVD_MOUNTPOINT}/slackware64/a/bash-*.t?z; do [ -f "$f" ] && pkg="$f" && break; done
if [ -z "$pkg" ]; then
    echo "{BOOTSTRAP_FAILED_TOKEN}: no bash package on the DVD"
    poweroff -f
fi
mkdir -p /tmp/vmctl-bash
case "$pkg" in
    *.txz) xz -dc "$pkg" | tar x -C /tmp/vmctl-bash ;;
    *) gzip -dc "$pkg" | tar x -C /tmp/vmctl-bash ;;
esac
umount {DVD_MOUNTPOINT}  # install.sh mounts the DVD itself
for b in /tmp/vmctl-bash/bin/bash /tmp/vmctl-bash/bin/bash[0-9]*; do
    [ -x "$b" ] && exec "$b" {SEED_MOUNTPOINT}/install.sh
done
echo "{BOOTSTRAP_FAILED_TOKEN}: the bash package holds no bash binary"
poweroff -f
"""


def live_trigger_command() -> str:
    """Typed at the live root prompt: find the seed among the CD-ROM drives and run its script."""
    return (f"mkdir -p {SEED_MOUNTPOINT}; for d in /dev/sr1 /dev/sr0 /dev/sr2 /dev/hdd /dev/hdc; do "
            f"mount -t iso9660 -o ro $d {SEED_MOUNTPOINT} 2>/dev/null && [ -f {SEED_MOUNTPOINT}/install.sh ] && break; "
            f"umount {SEED_MOUNTPOINT} 2>/dev/null; done; sh {SEED_MOUNTPOINT}/run.sh")


def extract_boot_artifacts(vm: dict[str, Any], iso_path: Path, dry_run: bool = False) -> tuple[Path, Path]:
    """The DVD's huge kernel and installer initrd (the same members on every release)."""
    boot = vm.get("installer_boot", {})
    kernel_member = str(boot.get("kernel") or KERNEL_MEMBER)
    initrd_member = str(boot.get("initrd") or INITRD_MEMBER)
    artifact_dir = iso.installer_artifact_dir(vm)
    kernel_path = artifact_dir / "vmlinuz"
    initrd_path = artifact_dir / "initrd"
    iso.extract_iso_member(iso_path, kernel_member, kernel_path, dry_run=dry_run)
    iso.extract_iso_member(iso_path, initrd_member, initrd_path, dry_run=dry_run)
    return kernel_path, initrd_path


def resolve_ssh_pubkey(vm: dict[str, Any], dry_run: bool = False) -> list[str]:
    keys = cloud_init._authorized_keys_for_vm(vm, dry_run=dry_run)
    if not keys and not dry_run:
        raise VMError("the Slackware bootstrap needs the project SSH key (ssh_provision): none resolved")
    return keys

