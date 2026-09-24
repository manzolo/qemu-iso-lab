# Unattended installs

Eight installers run headless, driven over the serial console, and end with the
VM installed, booted in the background and provisioned over SSH. Every
`bootstrap-*` command accepts `--dry-run` and prints each step it would run.

- [The common shape](#the-common-shape)
- [Ubuntu: autoinstall](#ubuntu-autoinstall)
- [Debian: preseed](#debian-preseed)
- [AlmaLinux / Rocky / Fedora: kickstart](#almalinux--rhel--fedora-kickstart)
- [Fedora Silverblue: kickstart + ostree](#fedora-silverblue-kickstart--ostree)
- [openSUSE: AutoYaST](#opensuse-autoyast)
- [Arch: pacstrap script](#arch-pacstrap-script)
- [Omarchy: cidata](#omarchy-cidata)
- [Alpine: setup-alpine](#alpine-setup-alpine)
- [Ubuntu desktop flavors](#ubuntu-desktop-flavors)
- [Windows 10/11: autounattend](#windows-1011-autounattend)
- [The completion-token rule](#the-completion-token-rule)
- [Boot checks and the validation matrix](#boot-checks-and-the-validation-matrix)

## The common shape

1. Render the answer file from the profile section (`autoinstall`,
   `preseed_config`, `kickstart_config`, `archinstall_config`, `omarchy_config`,
   `alpine_config`, `windows_config`)
   and pack it into a small seed ISO under `artifacts/<vm>/`.
2. Extract the kernel and initrd from the distro ISO (`xorriso` or `bsdtar`)
   so QEMU can boot the installer directly with the right kernel arguments.
3. Boot headless with serial stdio and wait for the completion token printed by
   the install script. The guest then powers itself off.
4. Start the installed VM headless in the background, wait for SSH on the
   forwarded port, and run the profile's provisioning
   ([PROVISIONING.md](PROVISIONING.md)).

Logs land in `artifacts/<vm>/logs/`. The interactive variants (`install-unattended`,
`install-archinstall`, `install-omarchy`) stop after step 3 so you can watch
the installer or boot the disk by hand with `vmctl start`.

Headless does not mean invisible: every headless QEMU (the install stage of a
bootstrap, the background VM it starts afterwards, `vmctl start --headless`)
serves its screen on a VNC unix socket under `artifacts/<vm>/runtime/`. From
another terminal, or from the vmtui RUN menu, `vmctl attach <vm>` bridges it
to 127.0.0.1 and opens `remote-viewer` (or `vncviewer`/`remmina`, or the
command given with `--viewer`); `--no-viewer` only prints the address. The
serial log stays the source of truth for the automation, the VNC screen is
for watching.

Once a VM runs in the background (`vmctl start --headless --background`, the
post-install boot, `vmctl lab up`), its COM1/ttyS0 is a unix socket under
`artifacts/<vm>/runtime/` too: `vmctl console <vm>` attaches the terminal to it
(login on ttyS0 where a getty is enabled, the pfSense console menu), `Ctrl-]`
detaches, and everything the guest prints there is logged to
`artifacts/<vm>/logs/serial.log`. Bootstraps keep the serial on the automation's
stdio, so `console` is for installed VMs.

## Ubuntu: autoinstall

```bash
vmctl bootstrap-unattended ubuntu-niri
```

Generates the cloud-init seed and the `autoinstall` seed, extracts
`casper/vmlinuz` and `casper/initrd`, boots with `autoinstall` and `-no-reboot`,
waits for the installer to exit, then starts the installed VM and runs
`cloud_init.post_install_run` / `ssh_provision`. Step by step:

```bash
vmctl prep ubuntu-niri
vmctl install-unattended ubuntu-niri
vmctl start ubuntu-niri --headless --background
vmctl post-install ubuntu-niri
```

## Debian: preseed

```bash
vmctl bootstrap-preseed debian-server
```

Renders `preseed.cfg` into a `PRESEED_CFG` seed ISO, extracts `vmlinuz` and
`initrd.gz`, boots the Debian installer with the preseed kernel arguments and
waits for `==> Debian preseed install complete!` on the serial console.

The same flow installs the Ubuntu desktop history profiles (`ubuntu-8.04-unattended` to
`ubuntu-18.04-unattended`) from the d-i media, the alternate CDs and the 14.04-18.04 server ISOs
(`installer_boot` = `install/vmlinuz` + `install/initrd.gz`), with the `ubuntu-desktop` task.
`preseed_config` knobs added for them: `upgrade` (`none`, `safe-upgrade`, `full-upgrade`),
`extra` (raw preseed lines, e.g. an empty `apt-setup/services-select` because the EOL
archives on old-releases.ubuntu.com have no security pocket on the vendor host), and
`disk_device: "auto"` for a guest whose disk name the profile cannot predict (the 8.04
installer carries both the old IDE drivers and libata): partman then picks the only disk
and GRUB goes to `(hd0)` (`grub_bootdev` overrides it). The late command flushes the named
disk and every `/dev/[hs]d*` it finds before the completion token. Autologin (GDM 2.x or
LightDM) and the `ttyS0` getty come from `late_commands`. The late command writes the
NOPASSWD rule both as a `sudoers.d` drop-in and as the last line of `/etc/sudoers`: 8.04 has no
`#includedir` and 10.04 lists `%admin` after it, so the drop-in alone still asked for a password.
The server media do not install the `ubuntu-desktop` *task*; the metapackage goes
through `pkgsel/include` instead. From 16.04 the guests run systemd, so the shared `verify-desktop` check applies and the
`ttyS0` getty is `serial-getty@ttyS0.service`; 18.04 autologs in through GDM3.
20.04 and 22.04 (`ubuntu-20.04-unattended`, `ubuntu-22.04-unattended`) leave d-i behind: they are
`bootstrap-unattended` copies of `ubuntu-gnome-24.04` (live-server autoinstall + `ubuntu-desktop`),
which stands for 24.04 in the series. The desktop check accepts `x-session-manager`, the name the
8.04 session runs under, spelled as the 15-character comm `x-session-manag` that `pgrep -x` compares against.
The late command also appends `UseDNS no` to `sshd_config`: the old sshd spent 5 s on a reverse
lookup of the slirp host before each login and the host's SSH probe gave up first (verified
live). The screensaver lock is disabled (gconf mandatory keys on GNOME 2, a gschema override
on Unity) so the autologin session stays visible. APT's periodic cron job is disabled too: on these releases it is a shell script named `apt`
that sleeps up to 30 minutes before `apt-get update`, and the post-install wait for package
manager activity used to sit on it (it now matches `apt` by command line). `release-upgrades` is set to `Prompt=never`, or update-manager greets the first login of an EOL
release with an upgrade offer and an "is not supported anymore" dialog (verified on 12.04).

## AlmaLinux / RHEL / Fedora: kickstart

```bash
vmctl bootstrap-kickstart almalinux-server
vmctl bootstrap-kickstart fedora-niri-dms
```

Renders `ks.cfg` into a `KS_CFG` seed ISO, extracts `vmlinuz` and `initrd.img`,
boots anaconda in text mode (`inst.ks=hd:LABEL=KS_CFG:/ks.cfg inst.text
inst.cmdline`) and waits for `==> Kickstart install complete!`. The kickstart
creates the user in `wheel` with passwordless sudo and installs the SSH key
when the profile provides one.

The install source is `kickstart_config.inst_repo`: `cdrom` (default) for a
full ISO such as AlmaLinux minimal, or a repository URL for a netinst image.
`fedora-niri-dms` boots the Fedora Everything netinst and points
`inst_repo` at the online Fedora 44 repository, so anaconda fetches both its
stage2 image and the packages from the network; the rendered kickstart carries
the matching `url --url=` directive. `ignore_missing_packages: true` renders
`%packages --ignoremissing`, so a renamed desktop package does not abort the
install. `post_commands` run in the installed system's chroot: the Fedora
profile uses them for the greetd autologin and the graphical target, and leaves
DankMaterialShell to the SSH post-install (COPR repositories need the network).
That script upgrades the system first: DMS 1.6 needs `quickshell >= 0.3`, which
only the `avengemedia/danklinux` COPR provides for Fedora 44, built against the
Qt 6.11 that lives in the `updates` repository. Fedora's `greetd` package ships
`agreety` itself and creates the `greetd` user.

## Fedora Silverblue: kickstart + ostree

```bash
vmctl bootstrap-kickstart fedora-silverblue
```

The same flow with one profile block, `kickstart_config.ostree`. When it is
present the rendered kickstart drops the `%packages` section entirely and grows
an `ostreesetup --osname=... --remote=... --url=... --ref=... --nogpg` line, and
`autopart` loses `--type=plain` (an ostree deployment cannot use it).

The ref names the Fedora version, so hard-coding it in the profile would break
at the next release: `kickstart.resolve_ostree_ref()` extracts
`/ostree/repo/refs/heads` from the ISO with xorriso and picks the ref matching
`ostree.ref_match`, printing `[ok] ostree ref: ...`. `ostree.ref` is only the
fallback for a dry run or a failed read.

An immutable system also changes what `%post` can do: it configures (GDM
autologin, `systemctl enable`) but installs nothing. Extra packages are layered
afterwards, in the SSH post-install, with `rpm-ostree install --idempotent
--allow-inactive`, and become active at the next boot.

## openSUSE: AutoYaST

```bash
vmctl bootstrap-autoyast opensuse-tumbleweed-autoyast
```

Renders an AutoYaST XML profile (`autoyast_config`) into an `AUTOINST` image,
extracts `boot/x86_64/loader/linux` and its initrd from the installer ISO, and
boots with `install=<repo> autoyast=usb:///autoinst.xml ifcfg=*=dhcp
netsetup=dhcp textmode=1 console=ttyS0,115200`.

The installer ISO is the **only CD-ROM** (SATA `ide-cd` on `ide.0`) and the seed
travels on a **USB stick**. Both halves of that sentence were paid for live:
linuxrc scans `/dev/sr*` for the installation repository, so the install medium
cannot be a virtio CD; but with two CD drives YaST cannot tell which one is the
repository and stops halfway through the package install on `Insert 'cd-<id>'
(Disc 1)`, waiting for an answer nobody gives.

`autoyast_config.install_repo` names the installation source explicitly. The
profile uses the NET image, which carries no packages, plus the online `oss`
repository: a 250 MB download instead of the 4.7 GB DVD.

`second_stage` is false by default. The YaST second stage runs on tty1 at first
boot and, because the host ends the install at the guest's reboot, it decides the
previous installation failed and asks whether to continue it: the first thing the
user sees is a blue installer dialog. With it disabled the chroot script owns
what the second stage would have done, service by service: `enable_services`,
the default target, the display manager, and `firewall-offline-cmd
--add-service` for `open_firewall_services`. openSUSE's firewalld opens only
`dhcpv6-client`, so without that last one sshd listens, the forwarded port
connects and the session hangs at the banner exchange.

The profile drives partitioning (GPT, 512 MB EFI + btrfs root), patterns
(`enhanced_base`, `gnome`, `kvm_server` by default) and services, and its chroot
script creates the user and group, the passwordless sudo drop-in, the SSH
authorized key, the GDM autologin, enables `sshd` and a `ttyS0` getty, then runs
the profile's `chroot_commands` and ends with the mandatory sequence: `sync`,
`blockdev --flushbufs`, `==> AutoYaST install complete!`. YaST then reboots and
QEMU, started with `-no-reboot`, exits by itself.

## Ubuntu desktop flavors

```bash
vmctl bootstrap-unattended kubuntu-24.04
```

`lubuntu-24.04`, `kubuntu-24.04`, `xubuntu-24.04`, `ubuntu-mate-24.04` and
`ubuntu-budgie-24.04` are the kvm-lab flavor family with no new code: the same
Ubuntu Server 24.04.4 ISO, the desktop chosen by the metapackage in
`autoinstall.packages`, and a cloud-init autologin drop-in for the display
manager that flavor ships (`/etc/sddm.conf.d/` for Lubuntu and Kubuntu,
`/etc/lightdm/lightdm.conf.d/` for the other three) plus a `serial-getty@ttyS0`.
Another version or another desktop is a profile, not a patch.

## Arch: pacstrap script

```bash
vmctl bootstrap-archinstall arch-dms
```

Generates a self-contained `install.sh` (sgdisk, pacstrap, arch-chroot, GRUB,
plus `archinstall_config.bootstrap_chroot_commands`) packed into a
`bootstrap.iso`, extracts `vmlinuz-linux` and `initramfs-linux.img` from the
Arch live ISO, boots with `console=ttyS0,115200 archisobasedir=arch
archisolabel=ARCH_YYYYMM`, waits for the `root@archiso` prompt, sends the mount
and run commands automatically, and waits for
`==> Arch Linux installation complete!`.

`vmctl install-archinstall <vm>` is the interactive cousin: it renders
archinstall JSON configs onto a second virtio CD-ROM (`/dev/vdb`) and boots the
live ISO for you to run `archinstall` by hand.

### CachyOS on the same flow

```bash
vmctl bootstrap-archinstall cachyos-nvidia
```

The CachyOS desktop ISO is an archiso too (label `COS_YYYYMM`, squashfs under
`arch/`, root without a password on the serial getty), so the pacstrap flow
works on it without a dedicated installer. Three profile fields adapt it:

- `installer_boot.kernel` / `initrd` point at the CachyOS kernel
  (`arch/boot/x86_64/vmlinuz-linux-cachyos`, `initramfs-linux-cachyos.img`)
  instead of the Arch defaults;
- `archinstall_config.live_login_prompt` / `live_shell_prompt` are what the
  live system prints (`CachyOS login:`, `root@CachyOS`), and
  `live_kernel_append` adds `systemd.unit=multi-user.target` so the live Plasma
  session and Calamares never start while pacstrap runs headless;
- `archinstall_config.inherit_live_pacman_conf` copies the live `pacman.conf`
  and mirrorlists into the target after pacstrap. pacstrap resolves packages
  with the live configuration, which already carries the `[cachyos]`
  repository, but the target would otherwise get the stock file from the
  `pacman` package and lose that repository (and `linux-cachyos` updates) on
  first boot.

`kernels` lists `linux-cachyos`; `packages` reproduce what Calamares installs
for its "Niri" desktop choice (read from the ISO's `netinstall.yaml`): the
required set (`cachyos-keyring`, mirrorlists, `cachyos-hooks`, `chwd`), the
"CachyOS Packages" group (`cachyos-settings`, `cachyos-hello`,
`cachyos-kernel-manager`, ...), the common network/audio/fonts/hardware groups
and `cachyos-niri-noctalia` + `sddm`. That metapackage brings niri, the
Noctalia shell and the CachyOS niri defaults; `bootstrap_chroot_commands`
enable SDDM with autologin into the niri session. The bootstrap script also
waits for archiso's `pacman-init` and for DNS before pacstrap, since the serial
login prompt shows up before either is ready. Calamares remains available for a
manual install (`vmctl install`). The `cachyos-live` profile (fixed VHD for Ventoy)
stays interactive.

Keep the CachyOS niri/Noctalia configuration from `/etc/skel`: do not copy an
unrelated host `~/.config/niri` or `DankMaterialShell` into these two profiles
through `local.json`. Such a copy replaces the includes that start Noctalia
and can leave only CachyOS Hello over an empty grey desktop. The post-install
checks the current `noctalia` executable (v5), validates niri's configuration
and waits for both the niri service and a successful `noctalia msg status`
with a visible bar inside the graphical session. A missing shell or a session
without a usable display now fails provisioning.

These two profiles use `video.headless` to keep `virtio-vga-gl` with QEMU's
`egl-headless` backend during unattended installation and background boots.
The generic VGA used by default in headless mode leaves niri without a usable
output. EGL renders without a local window and `vmctl attach` still uses VNC;
the host needs working EGL/DRI support, as it needs OpenGL for the windowed
`virtio-gl` mode. QEMU documents this pairing in its
[display options](https://www.qemu.org/docs/master/system/invocation.html).

`vmctl stop` asks the guest for an ACPI power-off (QMP `system_powerdown`).
niri takes logind's `handle-power-key` inhibitor and maps the power key to
suspend, so a stock niri guest would suspend instead of powering off, and
resuming a suspended VM under `virtio-vga-gl` has left niri and Noctalia
unkillable at the following shutdown. The bootstrap therefore writes
`~/.config/niri/cfg/vm-power.kdl` (`input { disable-power-key-handling }`)
and includes it from the CachyOS `config.kdl`, leaving the power button to
logind (`HandlePowerKey=poweroff`). The post-install fails if the compositor
still holds that inhibitor. The niri configurations shipped for
`arch-noctalia`, `arch-dms` and `alpine-niri` carry the same option.

To repair an existing guest affected by those imports, first remove the two
`copy_from_host` entries from its local override. Inside the guest, back up
`~/.config/niri`, copy `/etc/skel/.config/niri/.` into `~/.config/niri/`, run
`niri validate`, then log out and back in. This restores the matching CachyOS
autostart and shortcuts while keeping the old configuration in the backup.

## Omarchy: cidata

```bash
vmctl bootstrap-omarchy arch-omarchy-nvidia
```

Uses the official Omarchy ISO and its supported unattended `cidata` mechanism:
the profile's `omarchy_config` becomes the cidata answer file, the installer
sets up the native Hyprland desktop with Btrfs and Limine, and the post-install
adds the NVIDIA open DKMS stack. Omarchy ignores archinstall's
`custom_commands`, so this profile sets `ssh_provision.sudo_password` to let
post-install configure passwordless sudo itself
([PROVISIONING.md](PROVISIONING.md#sudo-in-the-guest)).

Omarchy is Hyprland-based, not niri-based. The NVIDIA packages are a bare-metal
recipe: `nvidia-smi` reports no device in a VM unless a GPU is passed through.

## Alpine: setup-alpine

```bash
vmctl bootstrap-alpine alpine-niri
```

Packs a `setup-alpine` answer file, an `install.sh` and a `run.sh` into an
`ALPINESEED` seed ISO attached as a virtio CD-ROM, extracts `boot/vmlinuz-lts`
and `boot/initramfs-lts` from the Alpine standard ISO and boots the live system
with the ISO's own module list plus `console=ttyS0,115200`. `run_and_expect`
answers the `localhost login:` prompt with `root`, mounts the seed at the shell
prompt and runs it. `install.sh` then:

1. exports `ERASE_DISKS=/dev/vda` and runs `setup-alpine -e -f answers`
   (keymap, hostname, udev, DHCP, apk mirror + community repo, admin user with
   the SSH key, sshd, chrony, `setup-disk -m sys`);
2. mounts the installed root back, and in a chroot installs the profile's
   `packages` (each `optional_packages` entry on its own, so a missing one is
   only logged), sets the user's password hash, adds passwordless sudo and the
   `seat` group, enables dbus and seatd, then runs `chroot_commands`;
3. unmounts, syncs, flushes, prints `==> Alpine Linux installation complete!`
   and powers off.

`alpine-niri` is pinned to Alpine 3.23: the 3.24 Mesa build leaves out the
virgl gallium driver, so `virtio-vga-gl` gives the guest no 3D acceleration
and niri, which refuses software renderers, never takes over the display.
It uses `chroot_commands` for greetd: autologin into
`dbus-run-session -- niri --session` (OpenRC has no systemd user session).
Two Alpine specifics learned the hard way: without elogind nothing sets
`XDG_RUNTIME_DIR`, so `pam-rundir` is installed and added to
`/etc/pam.d/greetd`; and the niri apk does not depend on the Wayland
libraries it dlopens, so `wayland-libs-server` and `wayland-libs-client` are
listed explicitly (niri panics with `NoWaylandLib` otherwise). greetd runs
`initial_session` once per boot and records it in `/run/greetd.run`;
restarting the service alone shows the greeter, not the autologin.
The kernel line of the installed system keeps a serial console
(`alpine_config.kernel_opts`), so `post-install-serial.log` stays readable.

## NixOS: the configuration is the answer file

```bash
vmctl bootstrap-nixos nixos-server
vmctl bootstrap-nixos nixos-gnome
```

NixOS needs no answer file because the installation *is* a configuration. The
flow renders the profile's whole `configuration.nix` from `nixos_config`, packs
it with an `install.sh` into a `NIXSEED` seed ISO and boots the official
installer image headless. The installer autologins a shell on the serial
console, so `run_and_expect` only has to type the trigger: mount the seed and
run it with sudo. The script then partitions (GPT `BOOT` + `ROOT`, fat32 +
ext4), lets `nixos-generate-config` write the hardware part, drops the rendered
configuration on top and runs `nixos-install --no-root-passwd`, and everything
the guest ends up with — user, hashed password, SSH key, passwordless sudo,
display manager, autologin, packages — comes from that one file.

Three things are read from the medium instead of being pinned
(`nixos.resolve_live_boot`, from the ISO's own `isolinux.cfg`): the kernel and
initrd paths under `/boot/nix/store/<hash>-.../`, the `init=` store path the
live system boots through, and the ISO's volume label, which its `root=LABEL=`
names. All three change with every rebuild of a channel's image, so a profile
that pinned them would break at the next one.

What the two live runs of 2026-09-19 taught, in the order it cost time:

- the live prompt is not what it looks like. The stream carries
  `[<ESC>]0;nixos@nixos: ~<BEL>nixos@nixos:~]$`, so the literal
  `[nixos@nixos:~]$` never matches and the first run sat at the prompt until its
  timeout. `NIXOS_LIVE_PROMPT` matches `nixos@nixos:~]$`;
- mounting by `/dev/disk/by-label/...` right after `mkfs` loses the race with
  udev ("Can't lookup blockdev"); the script mounts by device path;
- `systemctl get-default` prints `default.target` on NixOS, an alias of
  `graphical.target`. The shared `verify-desktop` now accepts exactly that name
  when `graphical.target` is active, and still rejects any other target;
- Nix wraps GNOME's binary, so the shell's `comm` is `.gnome-shell-wr` and
  `pgrep -x gnome-shell` never matches: the desktop profile checks the session,
  not a process name;
- a lab guest that locks itself shows a lock screen in the report and asks for a
  password on `vmctl attach`, so the GNOME profile turns off idle activation and
  the lock through `programs.dconf.profiles.user.databases`.

`nixos_config` takes `username`, `password_hash` and `state_version` (the
channel's release), and optionally `hostname`, `timezone`, `locale`, `keymap`,
`desktop` (`none`, `gnome`, `plasma`), `user_groups`, `packages`,
`extra_config` (raw Nix lines), `disk_device` and `install_args`. A new desktop
variant is a profile, not code: `nixos-gnome` differs from `nixos-server` by one
field plus its own SSH port.

## pearOS NiceC0re: unpackfs, like its own Calamares

```bash
vmctl bootstrap-pearos pearos-nicecore-unattended
```

pearOS is Arch under a macOS-like Plasma 6 desktop, shipped as a plain archiso
image, and its installer is **Calamares in unpackfs mode**: it never pacstraps a
package list, it copies the live squashfs onto the disk and configures it.
Calamares has no answer file, so this flow reproduces what the vendor's own
`pear-calamares-config` and its `/usr/local/bin/alg-*` helpers do, in one script
on a `PEARSEED` seed ISO. `run_and_expect` answers `pearOS-Live-System login:`
with `root` (no password), mounts the seed at the live prompt and runs it:

1. GPT `EFI` (fat32) + `ROOT` (ext4), the defaults of their `partition.conf`;
2. `unsquashfs` of `/run/archiso/bootmnt/arch/x86_64/airootfs.sfs` onto the root
   and the ISO's kernel as `/boot/vmlinuz-linux-cachyos-lts` — the two entries of
   their `unpackfs.conf`. Nothing is downloaded: the install is the medium;
3. fstab, machine-id, locale, keymap, timezone, hostname;
4. the archiso hooks leave `mkinitcpio.conf`, **then** the live-only packages are
   removed, **then** the initramfs is built. In any other order the rebuild that
   removing `mkinitcpio-archiso` fires ends in `Hook 'archiso' cannot be found`
   and overwrites a good image (verified live on 2026-09-19);
5. the profile's user, passwordless sudo, the SSH key and SDDM autologin. Their
   Calamares creates a throwaway `default` user and leaves the real one to a
   first-boot OOBE (`/usr/local/bin/post_setup`, launched from an autostart
   entry); this flow creates the profile's user directly and removes that entry,
   so the guest needs no first-boot wizard;
6. the live session is stripped the way their `alg-finalisation` does it
   (pacman-init units, the tty1 autologin, the live-only `alg-*` helpers, the
   live keyring), GRUB is installed, then sync → flush → completion token →
   poweroff.

The live ISO autologins into SDDM, so the boot line adds
`systemd.unit=multi-user.target` next to `console=ttyS0,115200`, and
`archisolabel=` comes from the medium itself (`pearos_iso_label`, blkid): the
build is monthly and its label carries the month, so nothing is pinned to a
release. The medium is user-supplied — every `iso.pearos.xyz` URL needs a signed
link — see [PROFILES.md](PROFILES.md) and the profile's notes.

`pearos_config` takes `username` + `password_hash`, and optionally `hostname`,
`timezone`, `locale`, `keymap`, `session`, `user_groups`, `disk_device`,
`services`, `remove_packages` and `chroot_commands`.

## Windows 10/11: autounattend

```bash
vmctl bootstrap-windows windows11-unattended
vmctl bootstrap-windows windows10-unattended     # same flow, driver_flavor w10, no requirement bypass
```

The technique comes from the kvm-lab repository (`scripts/win11/create_win11_vm.sh`
and its `autounattend.xml`), reworked for plain QEMU. Prerequisites: the retail
ISO at the profile's `iso` path (Microsoft publishes no stable URL, so nothing
is downloaded; `local.json` may point `iso` at an existing file), `7z` and
`xorriso` on the host. The virtio-win driver ISO is fetched from fedorapeople
(`windows_config.virtio_iso_url`) unless `virtio_iso` points at a local copy.

1. **Prompt-free ISO, once.** The Microsoft ISO is unpacked with `7z` (its
   files live in UDF only, the ISO 9660 tree holds a README) and rebuilt with
   `xorriso -as mkisofs` using `efi/microsoft/boot/efisys_noprompt.bin` as the
   UEFI El Torito image and an emptied `boot/bootfix.bin`, so the guest never
   waits at "Press any key to boot from CD or DVD". The result is cached as
   `isos/<stem>-noprompt.iso` with a `.source` stamp (resolved path, size,
   mtime of the original): it does not depend on the profile, and a replaced or
   same-named source ISO triggers a rebuild.
2. **Answer file on a seed CD.** `autounattend.xml` and `vmctl-setup.ps1` go
   into a `VMCTLSEED` ISO under `artifacts/<vm>/windows/`. Windows Setup
   searches the root of every removable drive for the answer file, so the big
   ISO is never rebuilt when the profile changes.
3. **Boot.** QEMU starts headless with serial stdio (COM1 in the guest), no
   `-no-reboot` (Setup reboots several times), the disk as
   `virtio-blk-pci,bootindex=1` and three SATA CD-ROMs on their own AHCI ports:
   the install ISO (`bootindex=2`), virtio-win and the seed. OVMF falls through
   to the CD only while the disk has no bootloader. In WinPE the answer file
   injects `viostor` and `NetKVM` from the virtio-win CD (listed for every drive
   letter D..G), wipes disk 0 into EFI + MSR + Windows, bypasses the TPM /
   Secure Boot / CPU / RAM checks through `HKLM\SYSTEM\Setup\LabConfig` (no
   swtpm needed), installs `windows_config.edition` with the matching generic
   KMS client key, then creates the local administrator and enables autologon.
   Nothing runs in the specialize pass on purpose: a `RunSynchronousCommand`
   that exits non-zero there blocks Setup with a modal dialog, and disabling
   UAC there leaves the Windows 11 OOBE (a modern app) on a black screen.
   At first logon, `FirstLogonCommands` runs one short `cmd.exe /c for %d in
   (D E F G) ...` line that finds `vmctl-setup.ps1` on the seed CD and starts it
   with PowerShell. Setup stores FirstLogonCommands as `HKLM\...\RunOnce`
   values, and **Windows 10 silently skips RunOnce values longer than 260
   characters** (MAX_PATH): the previous PowerShell one-liner was 294 characters
   and never ran on Windows 10 (the entry survived every reboot, UAC on or off),
   while Windows 11 ran it. Verified in the guest with a 307-character test
   entry (ignored) next to a short one (executed). The `<OOBE>` block uses only
   `HideEULAPage`, `HideLocalAccountScreen`, `HideOnlineAccountScreens`,
   `HideWirelessSetupInOOBE` and `ProtectYourPC` (the deprecated
   `SkipMachineOOBE`/`SkipUserOOBE` are gone), and
   `Microsoft-Windows-International-Core` is declared in oobeSystem as well as in
   specialize, otherwise Windows 10 stops at the region and keyboard pages.
4. **First logon.** `vmctl-setup.ps1` runs as the local administrator (elevated)
   and installs the virtio guest tools,
   OpenSSH Server (`Add-WindowsCapability`, retried while Windows Update wakes
   up) with the project's public key in `administrators_authorized_keys` (strict
   ACL via `icacls`), disables sleep and hibernation, installs WinFSP and
   starts `VirtioFsSvc` when the profile has a `shared_dir` (the host folder
   shows up as a drive letter and gets a shortcut on the desktop), runs the profile's PowerShell
   `setup_commands`, logs each step to `C:\vmctl\setup.log` and to COM1. `setup_commands` run in the administrator's user context, so HKCU
   refers to that user.
   Every step is checked (installer exit codes, `sshd` running, `icacls`
   result, a non-zero exit code in a `setup_commands` entry); only when none
   failed does it write `==> Windows installation complete!` on COM1, otherwise
   `==> Windows installation FAILED: <steps>`. It shuts down in both cases, so a
   failure surfaces as soon as QEMU exits instead of at the timeout.
5. **Post-install over OpenSSH.** The installed VM starts headless; the SSH
   probe is `exit 0` (cmd.exe has no `true`), `copy_from_host` is a plain
   `scp -r` to a `C:/...` path and `post_install_run` commands run in cmd.exe.
   Keep them locale-independent: on an Italian Windows `systeminfo` and `sc`
   print translated labels, PowerShell one-liners do not. `vmctl post-install`
   takes the same Windows path for profiles with `windows_config`.
   `vmctl stop` sends the ACPI power button and waits `acpi_poweroff_grace_sec`
   (300 s in the Windows profiles, 60 s by default) before trying `shutdown /s`
   over SSH and finally SIGTERM: the first shutdown after the bootstrap commits
   the OpenSSH feature operation and took well over a minute on Windows 10.

Latest builds without the Microsoft download page (which blocks scripted
requests by IP): [UUP dump](https://uupdump.net) builds an ISO from Windows
Update files with its `uup_download_linux.sh` (needs `aria2`, `wimtools`,
`chntpw`); pick the build on the site or via `api.uupdump.net/listid.php`,
choose language and edition, run the script, and point `iso` at the result.

`windows_config` fields: `username`, `password` (plain text, the answer file
cannot take a hash), `realname`, `computer_name` (NetBIOS-safe, 15 chars),
`organization`, `edition` (the image name inside `install.wim`: Microsoft ISOs
say `Windows 11 Pro`, UUP dump builds say `Windows 11 Professional`; the generic
key follows the Pro/Home/Enterprise/Education family) or `image_index`,
`product_key`, `language`, `input_locale`, `timezone`
(Windows name, e.g. `W. Europe Standard Time`), `driver_flavor` (`w11`/`w10`),
`bypass_requirements`, `auto_logon`, `install_guest_tools`, `install_openssh`,
`virtio_iso`, `virtio_iso_url`, `setup_commands` (PowerShell, run as SYSTEM:
use `HKLM`, not `HKCU`, for settings).

The token rule holds here too, with a twist: Windows has no `sync` + `poweroff -f`
split, its own shutdown is the flush, so the token is written right before
`shutdown /s` and `run_and_expect` waits up to `windows.SHUTDOWN_GRACE_SEC`
(10 minutes) for QEMU to exit on its own instead of the usual 30 seconds.

### Windows 7 on the same flow

`windows7-unattended` (kvm-lab's `Windows7U`) reuses `bootstrap-windows` with the
legacy branch of `windows.py`, selected by the edition name (`Windows 7 ...`) or
`driver_flavor: w7`: BIOS profile and MBR disk layout (System Reserved + Windows),
no `LabConfig` bypass, `viostor` from the `w7` directory of the virtio-win CD
(the NIC is `e1000e`, native on 7), `Skip*OOBE` + `NetworkLocation`, and one
specialize command, `vmctl-cert.cmd` from the seed, that imports the Red Hat
driver certificate as SYSTEM and always exits 0. Windows 7 has no OpenSSH
capability and ships PowerShell 2.0, so the first-logon script only runs
`setup_commands`, prints the token on COM1 and shuts down: no SSH post-install,
no `vmctl shell`, no virtiofs (no WinFSP); `check-vms` treats a Windows profile
without `ssh_provision` as "install only". Guest tools are a manual step.

The prompt-free ISO for 7 is built with two differences, both found live: `boot/bootfix.bin`
is deleted rather than emptied (`etfsboot.com` hangs at "Booting from DVD/CD..." on an empty
file) and xorriso keeps the exact ISO 9660 names (`-D -N -d`: `CDBOOT` looks up `BOOTMGR`,
not `BOOTMGR.;1`). The cache stamp records both, so a legacy build never shares the cache
with a Windows 10/11 one.

## pfSense: scripted bsdinstall

```bash
vmctl bootstrap-pfsense pfsense-lab        # the router of the network lab; usually via: vmctl lab install
```

Ported from kvm-lab's `network-lab`. The offline pfSense CE 2.7.2 ISO is a
FreeBSD installer whose `/etc/rc.local` runs `bsdinstall script
/etc/installerconfig` when that file exists. `pfsense.py` renders the whole
`config.xml` from the `network_lab` topology ([NETWORK-LAB.md](NETWORK-LAB.md))
and builds a per-VM copy of the ISO (`artifacts/<vm>/pfsense/install.iso`, with
a `.source` stamp of source ISO + config hash):

1. `xorriso -osirrox` extracts `rc.local` and `usr/libexec/bsdinstall/script`
   from the source ISO; the flow refuses an ISO whose `rc.local` does not run the
   scripted installer (other versions, the Netgate online installer).
2. `bsdinstall/script` gets one patch: `bsdinstall umount || [ -n
   "$ZFSBOOT_DISKS" ]` (ZFS has no fstab mounts, `umount -a` may return 1 with
   nothing left; the pool export that follows is what matters).
3. `cp` + `growisofs -M ... -graft-points` update the copy in place with
   `installerconfig` (ZFS on `vtbd0`, BIOS, `config.xml` heredoc into
   `/cf/conf`), the new `rc.local` and the patched script. An xorriso rebuild
   would drop FreeBSD's hidden El Torito extents; the in-place update keeps
   them.
4. QEMU boots the `pc` machine in BIOS mode with the disk at `bootindex=1`, the
   CD at `bootindex=2` (`ide-cd` on `ide.1`), serial stdio (`cuau0` in the
   guest), WAN slirp + LAN segment, `-no-reboot`.
5. `rc.local` runs the install, then **`sync`, the token `==> pfSense
   installation complete!` on `/dev/cuau0`, `shutdown -p now`**, in that order;
   on failure `==> pfSense installation FAILED` plus the bsdinstall log, then
   the same power-off so the host fails fast instead of waiting for the timeout.

There is no SSH post-install: the account (`pfsense_config.username`, bcrypt
hash of the password, the project's public key) and every rule are in the
config.xml. The Linux members of the lab ride `bootstrap-unattended` with the
`network_lab` post-install hook.

## ReactOS: unattend.inf

```bash
vmctl bootstrap-reactos reactos            # needs isos/ReactOS-0.4.16-i386.iso (unzip the SourceForge release zip)
```

ReactOS Setup reads `unattend.inf` from its source root, `\I386` on the BootCD
(the copy under `\reactos` belongs to the LiveCD and is replaced too). With
`UnattendSetupEnabled = yes` the text-mode stage (AutoPartition + format of the
empty disk, file copy, boot loader on the MBR) and the GUI stage (owner,
computer name, Administrator password, time zone, locale) run without a
prompt, and `[GuiRunOnce]` runs commands at the first desktop logon.
`reactos.py` renders the answer file from `reactos_config` (`fullname`,
`computer_name`, `password` in plain text, `locale_id`, `timezone_index`,
`installation_type`, `fs_type` fat/btrfs, `display`, `run_once`) and builds a
per-VM ISO (`artifacts/<vm>/reactos/install.iso`, `.source` stamp of ISO +
answers): one `xorriso -indev ... -outdev ... -boot_image any replay` pass
grafts `unattend.inf` (CRLF) into both places and a `freeldr.ini` whose default
entry is `Setup` with a 2 s timeout, because the release BootCD boots the Live
environment by default. No extraction, no 7z.

QEMU boots the `pc` machine in BIOS mode with the PATA disk at `bootindex=1`
and the CD as `ide-cd` on `ide.1` at `bootindex=2`, **without** `-no-reboot`:
Setup reboots after each stage and the disk, once bootable, wins over the CD
like the Windows flow. The completion signal is not a token of ours: the
release kernel prints its debug log on COM1 whatever `freeldr.ini` says and
that port belongs to the kernel debugger, so an `echo > COM1` from GuiRunOnce
never reaches the host (verified live). The host waits for the kernel's own
ACPI line `Entering sleep state S5`, printed exactly once when the
`shutdown.exe /s /f /t 5` scheduled as the last GuiRunOnce command runs; the two
Setup reboots are resets and never print it, a stuck Setup times out. The
whole install takes about 90 s under KVM. Install only: ReactOS ships no SSH
server, the desktop autologs in as Administrator. `check-vms` treats the
profile like pfSense: install, boot for the report screenshot, stop.

## Windows XP and Windows 2000: WINNT.SIF

```bash
vmctl bootstrap-windowsxp windowsxp-unattended      # your own ISO + key in local.json
vmctl bootstrap-windows2000 windows2000-unattended  # the same flow, one section later
```

One module serves both. A profile says which generation it is by the section it
carries, `windowsxp_config` or `windows2000_config`, and three things follow
from that, each of them found the hard way on 2026-09-14:

| | Windows XP | Windows 2000 |
|---|---|---|
| licence key in the answer file | `ProductKey` | `ProductID` |
| how the guest powers itself off | `shutdown -s -t 5 -f` | WMI `Win32Shutdown(12)` from a `.vbs` on the CD |
| writing the registry | `regedit /s` | `regedit /s` |

Windows 2000 has neither `shutdown.exe` (Resource Kit only) nor SHELL32's
`SHExitWindowsEx`, which is a Windows 9x export - RUNDLL32 answered "Voce
mancante" and the guest sat on its desktop until the timeout. It does have
Windows Script Host and WMI, so the CD carries a four-line `.vbs`. And it has no
`reg.exe` either ("reg" non e' riconosciuto...), which is why the autologon
values travel as a `.reg` file applied with `regedit /s`: that one exists on
both. The same file removes `AutoLogonCount`, which Winlogon otherwise uses to
delete the very values it was given.

The ISO rebuild needs `-compliance omit_version:untranslated_names`. Without the
first, Microsoft's CD loader stops at "CDBOOT: Couldn't find NTLDR", because it
looks up `NTLDR` and not `NTLDR.;1`. Without the second, xorriso rewrites a name
like `BACHSB~1.RM_` as `BACHSB_1.RM_` - the tilde is not an ISO9660 character -
and keeps the original only in Rock Ridge, which Windows does not read: Setup
then stops on "unable to copy" for that file, on Windows 2000 and on Windows NT
alike. Windows 98 needs the opposite, see its own section.

And the boot image is copied out of the original medium and handed back as a
file rather than replayed: on a Windows CD it is a hidden extent, not a file of
the directory tree, so `-boot_image any replay` drops the boot record without a
word and the rebuilt CD falls through to PXE (verified live).

## Windows XP: WINNT.SIF

```bash
vmctl bootstrap-windowsxp windowsxp-unattended   # your own ISO + key in local.json; xorriso, and grub-mkimage if the medium does not boot
```

Setup reads `\I386\WINNT.SIF` from the installation medium: with
`UnattendMode=FullUnattended` the text stage (AutoPartition, format, file copy)
and the GUI stage run without a prompt, and `[GuiRunOnce]` runs one command at
the first logon. That command is the script `\VMCTL\VMCTL.CMD`, also carried on
the CD, whose whole output is redirected to COM1: it installs the optional
service pack, makes the autologon permanent, prints the guest's own version and
finally the completion token, then shuts the guest down. Everything lives in the
script because a `[GuiRunOnce]` value cannot contain a double quote — the INI
wraps it in one — and `^`-escaped spaces do not survive `cmd /c` parsing.

The profile is install-only: XP ships no SSH server, so `check-vms` treats it
like ReactOS and photographs the desktop instead of provisioning it.

Six things cost a live run each, on 2026-09-14, and are worth knowing before
touching this flow.

**The medium may not boot at all.** An OEM ISO can carry no El Torito record
(volume descriptors going from the primary straight to the terminator): QEMU
then stops at `Booting from DVD/CD...`. The boot floppy image it was mastered
from is not in the file, so `windowsxp.py` adds a GRUB core image
(`grub-mkimage -O i386-pc-eltorito`, which **already contains** `cdboot.img` —
concatenating it again yields an image the BIOS loads and cannot run) whose
`grub.cfg` chainloads Microsoft's `/I386/SETUPLDR.BIN` with GRUB's `ntldr`
command. A medium that does boot keeps its own record, replayed untouched.

**Never remaster the ISO.** `7z` could not read that OEM image completely (one
open error) and Setup then stopped on `Impossibile copiare il file:
cyclad-z.inf`. The answer file and the script are grafted into the *original*
image with `xorriso -indev ... -outdev`, like pfSense and ReactOS.

**The CD must sit behind the disk in the boot order.** Setup reboots twice; with
the CD first it starts over from the beginning, forever. The disk carries
`bootindex=1`, the CD `bootindex=2`, and there is no `-no-reboot`.

**`>` is a redirection, in a batch file too.** `echo ==> Windows XP ...> COM1`
makes cmd take `Windows` for a file name; the host sees `== XP installation
complete!`, does not recognise its token and times out on a guest that had
finished. Every token is written `==^>`.

**The standard VGA hangs the install.** XP has no driver for it, stays at
640x480 and the shell opens a modal "the screen resolution will be adjusted
automatically" at the first logon that nothing dismisses on a headless guest.
XP ships a Cirrus GD5446 driver, so the profile selects `-vga cirrus` and
`check_profile` refuses anything else. For the same family of reasons the USB
tablet — what makes the pointer absolute, and without which the guest pointer
drifts away from the host's — needs `usb_controller: "builtin"`: the default
`qemu-xhci` is USB 3.0, for which XP has no driver either.

**Windows Welcome is not covered by `OemSkipWelcome`.** Without
`UnattendSwitch="Yes"` in `[Unattended]`, msoobe opens on "Grazie per aver
acquistato Microsoft Windows XP" and waits for a click that neither a synthetic
key nor a PS/2 relative pointer can deliver.

`windowsxp_config` keys: `product_key` (**required**, and it belongs in
`vms/profiles/local.json`, never in a tracked profile: an OEM medium asks for it
in the GUI stage), `computer_name`, `full_name`, `organization`,
`admin_password`, `administrator_name`, `workgroup`, `timezone`, `target_path`,
`file_system`, `display`, `setup_commands` (run inside the CD script, where
quotes are allowed) and `service_pack`.

The service pack is a local package — Microsoft publishes no URL — grafted onto
the CD as `\VMCTL\SP.EXE` and installed by the script with `start /wait`,
because `update.exe` unpacks itself and hands over to a child: without the wait
the token would reach the host mid-installation. Exit code **3010** means
"installed, reboot pending", which is why the `CSDVersion` the script prints
still reads the old level: the service pack finishes applying at the next boot.
Its language must match the medium's.

Autologon is made permanent by the script, not by the answer file:
`AutoLogonCount` covers the first logon only, and Winlogon deletes
`AutoAdminLogon` and `DefaultPassword` when its counter runs out — so the script
writes the three values and removes the counter.

The host share cannot be virtiofs (WinFSP wants Windows 7, the `viofs` driver
Windows 8.1), so `shared_dir.mode: "vvfat"` exposes the directory as a FAT disk
no driver is needed for. It is read-only — QEMU documents its writable vvfat as
able to corrupt the host directory — which on an IDE disk means `snapshot=on`,
not `readonly=on`: a read-only block node makes QEMU refuse to start with "Block
node is read-only". The share is attached at runtime only, never while an
installer runs: a second disk is a second place Setup could install to.

## Windows 98: MSBATCH.INF

```bash
vmctl bootstrap-windows98 windows98-unattended   # your own ISO + key in local.json; xorriso, mtools, mkfs.vfat
```

Nothing here looks like the Windows XP flow. Setup runs from real-mode DOS,
booted by the 1.44 MB floppy image the CD carries as its El Torito record, and
it neither partitions nor formats: it expects a C: that already exists. Four
things had to be solved, each one verified live on 2026-09-14.

**The host prepares the disk.** `windows98.prepare_disk()` writes a partition
table with one active FAT32 partition, makes the filesystem with
`mkfs.vfat --offset`, and puts boot code of its own in sector 0
(`vms/profile-files/windows98/mbr.asm`, 100 bytes, assembled with nasm and
carried in the module as bytes): it chainloads the active partition when its
boot sector is bootable and otherwise returns to the BIOS with `int 0x18`, so
the boot order moves on to the CD. Without it the BIOS stops at "Booting from
Hard Disk..." on a disk that has a partition table and no boot code. The two
bytes at offset 0x5A of the fresh FAT32 boot sector — where the jump at its
start lands — become `int 0x18` for the same reason: dosfstools leaves a stub
there that prints "this is not a bootable disk" and waits for a key.

**`mkfs.vfat --offset` leaves BPB_HiddSec at zero**, and that field must hold
the partition's first sector. Windows Setup writes its own boot code over that
BPB and keeps the wrong value, then looks for IO.SYS at the wrong absolute
sector: the guest hangs at "Booting from Hard Disk..." after the file-copy
stage, with the system already on the disk. The flow writes 2048 into the boot
sector and into the FAT32 backup at sector 6 of the partition.

**The CD has a boot menu before DOS.** Not the one in `CONFIG.SYS`: a 2 KB
program, `JO.SYS`, shows "1. Avvio dal disco rigido / 2. Avvio dal CD-ROM" and
defaults to the hard disk, which is why an unattended boot always came back
where it started. Deleting that one file from the boot image makes DOS start
directly, and the flow then sets the `CONFIG.SYS` menu timeout to zero and
replaces `AUTOEXEC.BAT` with one that finds the CD, runs `FDISK /MBR` (the only
non-interactive thing FDISK does, and what puts Microsoft's boot code on the
disk for the installed system) and starts Setup with the answer file. Both files
are written CRLF: DOS obeys nothing in a batch file with Unix line endings — it
prints `OFF` instead of running `@ECHO OFF`.

**The answer file uses `ProductKey="xxxxx-xxxxx-xxxxx-xxxxx-xxxxx"` in `[Setup]`,**
as emitted by Microsoft's own `tools/RESKIT/BATCH/BATCH.EXE` on the CD.
`ProductID` is not the installation-key field: using it leaves Setup without
the supplied key and can stop at the user-information page even when the name
and organization are prefilled and `[NameAndOrg] Display=0` is set. The key belongs in
`vms/profiles/local.json`, never in a tracked profile. The rest of the file
follows the vendor's own example, which ships on the CD at
`\tools\SYSREC\MSBATCH.INF`; `[Install] AddReg` is what puts the first-logon
script in `RunOnce`, where it echoes the completion token to COM1 and calls
`rundll32.exe user.exe,exitwindows`.

Changes to the answer file invalidate the generated ISO cache on the next
bootstrap. They do not update an installer that is already running.
`bootstrap-windows98` prepares a fresh disk, so rerunning it replaces the
previous installation.

The profile pins `cpu_model: pentium3`: under KVM `common_args` otherwise
exposes the host CPU, whose feature set Windows 98 does not survive. It is the
only profile in the catalogue that names a CPU, and naming one is now what it
takes to override the KVM default.

Install only, like ReactOS and Windows XP: no SSH server exists for this guest.

## Windows NT 4.0: UNATTEND.TXT

Every trap this flow steps around, one row each with symptom, cause and remedy, is in
[`NT4_PITFALLS.md`](NT4_PITFALLS.md). Read it before changing anything below.

```bash
vmctl bootstrap-windowsnt4 windowsnt4-unattended   # your own ISO + CD key in local.json; xorriso, mtools, mkfs.vfat
```

The oldest guest in the catalogue, and the one furthest from the later flows.
The NT 4 CD boots straight into text-mode Setup, which reads no answer file, so
the unattended path is Microsoft's own `WINNT.EXE /U:<answer> /S:<source> /B`
run from DOS. The rebuilt ISO therefore carries a **FreeDOS 1.3 boot floppy**
as its El Torito image (`144m/x86BOOT.img` of the FreeDOS Floppy Edition, at
`isos/freedos-1.3-x86boot.img`), with `OAKCDROM.SYS` and `MSCDEX.EXE` from a
Windows 9x startup disk copied onto it - `windowsnt4_config.cdrom_driver_iso`
names a Windows 98 ISO and the flow extracts the two files from its boot image
into `isos/dos/`. The DOS of a Windows 98 startup disk itself refuses to run
`WINNT.EXE` from a CD ("long file name protection", verified live), which is
why the kernel is FreeDOS. `FDCONFIG.SYS` loads the CD driver, `FDAUTO.BAT`
mounts the CD as D: and starts `WINNT.EXE`, which copies `\I386` and `$OEM$` to
`C:\$WIN_NT$.~LS`, writes a boot sector for the NT loader and reboots.

**The host prepares the disk**, as in the Windows 98 flow, but keeps it **raw**
(`disk.format: raw`, enforced by the profile check). NT 4's IDE driver never
issues FLUSH CACHE, so with a qcow2 image the L1/L2/refcount updates sit in
QEMU's metadata cache until QEMU exits cleanly; two runs that ended in a hard
kill (timeout, host under memory pressure) left a 470 MB file whose header
still described the empty disk (`qemu-img check`: 4 clusters allocated), every
cluster allocated after the format reading back as zeros while the FAT, in a
cluster allocated by the format itself, carried all the later updates - which
looked exactly like a filesystem wiped from inside the guest. Same construction otherwise: the same MBR that
steps aside with `int 0x18` until the partition is bootable, one active
partition of type 06, `mkfs.vfat -F 16 -h 2048` (FAT16 - neither DOS nor NT 4
reads FAT32; `-h` is BPB_HiddSec, which the NT boot sector computes every
address from) and `int 0x18` at offset 0x3E of the fresh boot sector. The disk
is 2 GB at most: `WINNT.EXE` runs from DOS and copies to one FAT16 volume.

Three things about the answer file cost a live run each (2026-09-14):

- **`$OEM$` lives under `\I386`**, not at the root of the medium. At the root
  Setup ignores it silently: no `CMDLINES.TXT`, no service pack, `CSDVersion`
  empty. Everything in it needs an 8.3 upper-case name because the copy happens
  in DOS - an extracted service pack stopped the copy on its first lower-case
  name - so SP6a travels as its self-extracting `SP6I386.EXE` (grafted as
  `NT4SP.EXE`) and `VMCTL.CMD`, run by `CMDLINES.TXT` at the end of the GUI
  stage, starts it with `/u /q /z /o` (unattended, quiet, no reboot, overwrite
  OEM files). `UPDATE_EXIT=0` and `CSDVersion = Service Pack 6` were read back
  from the installed hive.
- **`TimeZone` is the display name, in the language of the medium.** The
  numeric indexes (110 = Berlin/Rome) belong to Windows 2000; an English string
  on an Italian CD opens the Date/Time dialog with GMT selected. The right
  string is in Microsoft's own sample answer file at `\I386\UNATTEND.TXT` on
  every localized CD, and the flow reads it from there when the profile sets no
  `timezone`.
- **The NIC is the AMD PCnet** (`network_device: pcnet`), detected by Setup.
  The DEC 21x4 (`tulip`) opens "Tipo di connessione" even in unattended mode;
  the PCnet opens its "Full duplex / Porta 10Base-T" dialog because its INF
  never looks at the unattended flag, and the flow rewrites `OEMNADAP.IN_` on
  the CD (see below). An ISA NE2000 (`ne2k_isa`, still accepted: declared with
  `InstallAdapters` and a `[NE2000Params]` section repeating QEMU's I/O base
  and IRQ) installs without any patch because `OEMNADN2.INF` honours
  `STF_GUI_UNATTENDED`, but at runtime NT 4's `ne2000.sys` reports QEMU's card
  as "not functioning" (System log, `%%31`) and the guest has no network.
  Independently of the adapter, a fresh GUI stage twice ended in a
  `tcpip.sys` IRQL_NOT_LESS_OR_EQUAL stop at the same address when the adapter
  driver had not started (PCnet with TP=1; NE2000 on IRQ 9), while the same
  disk resumed after a reboot went through: the NT 4.0 RTM TCP/IP does not
  survive a bound adapter that fails to start, so the adapter parameters must
  be right the first time.
- **The Cirrus driver hangs the first boot.** With `-vga cirrus` and
  `[Display]` at 800x600x16 the installed system spun the kernel at one address
  with 100 % CPU, the screen frozen on the desktop colour and no reaction to
  Ctrl-Alt-Del, first blamed on the NIC; the same disk booted with `-vga std`
  reached the logon screen in a minute, and an SP-less install with Cirrus hung
  the same way. So the profile is the standard VGA at 640x480 with 16 colours
  (`[Display]` 4 bpp), `check_profile` rejects cirrus, and NT 4 has no driver
  for anything better in QEMU.

- **Even the PCnet's own INF opens a dialog** (kept for a profile that names
  `pcnet`). `OEMNADAP.INF` ("Scheda
  Ethernet PCI AMD PCNET v3.11": Full duplex, Porta 10Base-T) never looks at
  `STF_GUI_UNATTENDED`, unlike Microsoft's INF for the ISA twin
  (`OEMNADAM.INF`), so no answer-file section can silence it. The rebuilt CD
  therefore carries the vendor INF with three lines added after its
  `adapteroptions` label - `ifstr(i) $(!STF_GUI_UNATTENDED) == "YES"` /
  `Set TPValue = 0` / `goto skipoptions` / `endif` - which writes what a
  confirmed dialog writes, without the dialog. `TPValue = 0` matters: the
  dialog leaves "Porta 10Base-T" unchecked and Continue stores TP=0 (auto
  port), while the INF's internal default is 1; a run that skipped the dialog
  with TP=1 met "Migrazione da WinSock 1.1 a 2.0 non riuscita" and then a
  `tcpip.sys` IRQL_NOT_LESS_OR_EQUAL stop in the network stage.
  The `.IN_` is a one-file MSZIP cabinet: the module reads it
  (`cab_extract_single`, deflate blocks sharing one window) and writes it back
  stored (`cab_store_single`), and `windowsnt4_config.patch_pcnet_inf: false`
  opts out. The file is left alone when it already knows the variable.
- **`CMDLINES.TXT` commands see no `%SystemRoot%` on the PATH**: a bare
  `regedit` came back as "non è riconosciuto come comando interno o esterno",
  so the autologon values are written with `%SystemRoot%\regedit.exe`.

Two more are inherited: `-compliance omit_version:untranslated_names` on the
xorriso rebuild, or `BACHSB~1.RM_` becomes `BACHSB_1.RM_` and text-mode Setup
stops on "Impossibile copiare il seguente file: bachsb~1.rmi" (verified live on
this medium too); and `acpi: false` in the profile, which `machine_arg` turns
into `acpi=off`.

**Completion.** `CMDLINES.TXT` copies `FIRST.CMD`, `REPORT.CMD` and the
shutdown tool to `C:\VMCTL` and imports `VMCTL.REG` with
`%SystemRoot%\regedit.exe`: the autologon values for the blank-password
Administrator, a `RunOnce` value with the first-logon command, and
`Services\Sermouse Start=4`. That last one is why the token reaches the host:
at every boot `sermouse.sys` probes COM1 for a serial mouse (the `DSs` noise in
the serial log) and still holds the port when Explorer runs RunOnce, so a
command redirected to COM1 on the RunOnce line failed silently and the value
was consumed (verified live: the same script run by hand a minute later
printed everything). The RunOnce line therefore carries no redirection;
`FIRST.CMD` waits five seconds (`ping -n 6 127.0.0.1`, the only sleep NT 4
has) and then runs `REPORT.CMD` with its output on COM1. Winlogon performs the
blank-password automatic logon exactly once and then resets `AutoAdminLogon`
to 0 (the second boot showed the Ctrl-Alt-Del prompt), so `REPORT.CMD` gives
Administrator the profile's `admin_password` (`net user`, default `lab`) and
re-imports the autologon values with it (`AUTOLOG.REG`). Not `[GuiRunOnce]`: written the NT 4 way (one
quoted command per line) it left the RunOnce key empty and nothing ran at the
first logon (hive read offline, verified live). The same .reg turns off the
crash dump and the automatic reboot after a stop error (`CrashControl`): a
dump written at a first-boot crash went through the pagefile and left the
FAT16 volume with empty directories and an unbootable C:, so a stop error now
stays on the screen for the timeline to capture. `REPORT.CMD` then prints on
COM1: `ver`, `CSDVersion` exported with `regedit /e`, the profile's
`setup_commands`, the token. The service pack is judged by that `CSDVersion`
line, not by its exit code: NT 4's `start /wait` hands nothing back (the log
said 9009, the code of the regedit that had failed before it, while the hive
already said Service Pack 6). NT 4 has no `shutdown.exe` (Resource Kit), no WMI
and no Windows Script Host, so the script ends with `VMCTLOFF.EXE`: a 1 KB PE
written by hand in `vms/profile-files/windowsnt4/exitwin.asm` (nasm, bytes
embedded in the module) that enables `SeShutdownPrivilege` and calls
`ExitWindowsEx(EWX_SHUTDOWN | EWX_FORCE)` - with `EWX_POWEROFF` added NT 4
rebooted instead (verified live). NT 4 cannot power the machine off - no APM,
no ACPI - so the guest stops at "It is now safe to turn off your computer" with
everything flushed, and `run_and_expect` closes QEMU once `SHUTDOWN_GRACE_SEC`
(120 s) has passed. This
is the one flow where the host, not the guest, ends the process; the guest's
shutdown is the flush.

Install only, like ReactOS, Windows 98 and Windows XP: no SSH server exists for
this guest. No USB either (`usb_tablet` must be off, the pointer is PS/2), and
no audio device NT 4 has a driver for. The DOS side of the flow comes from two
places: `isos/freedos-1.3-x86boot.img` is `144m/x86BOOT.img` out of
`FD13-FloppyEdition.zip` (sha256 `75a4e11a…b072`, the image itself
`3f7834ea…8925`), and `isos/dos/OAKCDROM.SYS` + `MSCDEX.EXE` are the files of
the same name on a Windows 98 startup disk (`271741af…dfc5`, `6bc3f4c4…9217`).

## The completion-token rule

Every flow signals success by printing a token on the serial console, and every
flow must respect the same ordering, because breaking it corrupts the EFI
system partition in a way that only shows up on the first real boot
(`grub rescue>`):

```bash
sync
blockdev --flushbufs /dev/vda /dev/vda1 /dev/vda2 || true
echo "==> ... installation complete!"      # the token, AFTER the flush
poweroff -f
```

On the host, `run_and_expect` waits up to 30 seconds (`exit_grace_sec`; 10
minutes for Windows, whose shutdown *is* the flush) for QEMU to exit on its
own after the token and only then falls back to terminating it. Do not "speed
up" either side. The full story is in [ARCH_GRUB_BOOT_FIX.md](ARCH_GRUB_BOOT_FIX.md).

## Boot checks and the validation matrix

`boot-check` boots a VM headless and watches the serial console for the token
declared in the profile's `ci` section. The smallest real boot test in the repo:

```bash
vmctl prep alpine-ci
vmctl boot-check alpine-ci
```

It downloads the Alpine `virt` ISO, prepares the disk, boots QEMU headless and
waits for the `login:` prompt. GitHub Actions runs it under TCG on every push
([CI_BOOT_STRATEGY.md](CI_BOOT_STRATEGY.md)). `ci.accel` in a profile is a CI
override: local runs default to KVM when available.

`check-vms` runs the local validation matrix across profiles, including the
heavier bootstrap flows:

```bash
vmctl check-vms
vmctl check-vms ubuntu-niri arch-noctalia --timeout 600
vmctl check-vms --parallel 4 --clean-first
```

### Running one category instead of the whole matrix

The full matrix takes hours. `--group NAME` runs one category of it, and repeating
the flag adds another:

```bash
vmctl list --groups                      # every category, its size and its members
vmctl check-vms --group ubuntu           # every Ubuntu profile and official flavour
vmctl check-vms --group smoke            # one profile per install flow: does the machinery still work?
vmctl check-vms --group rhel --group fedora
make validate-vms GROUP="ubuntu rhel"    # the same, with --restore and the HTML report
```

Most categories are **derived** from metadata the profile already carries, so a new VM
joins them the moment it is written:

| Axis | Groups |
|------|--------|
| `meta.family` | `debian`, `arch`, `fedora`, `rhel`, `windows`, `bsd`, `nix`, `alpine`, `opensuse`, `reactos`, `kali`, `mint`, `void` |
| `meta.status` | `unattended`, `manual`, `experimental` |
| `meta.role` | `desktop`, `server`, `router`, `installer`, `ci`, `minimal`, `import-template`... |
| install flow | `bootstrap-preseed`, `bootstrap-kickstart`, `bootstrap-windows`, `boot-check`... |

The rest are declared by hand in `meta.groups`, because no other field expresses them:

| Group | What is in it |
|-------|---------------|
| `ubuntu` | Ubuntu and its official flavours, whatever their `meta.slug`: the whole release series, the 24.04 flavour sweep, the 26.04 profiles and `lubuntu-lab`. They all share `meta.family: debian` with Debian and Kali, so the family alone could not say "the Ubuntu ones" |
| `ubuntu-releases` | One unattended desktop install per Ubuntu release, 8.04 to 26.04. 24.04's entry is `ubuntu-gnome-24.04`: it is the same recipe under the flavour naming, so the series has no separate `ubuntu-24.04-unattended` profile |
| `ubuntu-flavors` | The 24.04 flavour sweep: GNOME, KDE, LXQt, Xfce, MATE, Budgie, Unity, Cinnamon, Studio, Edubuntu |
| `debian-only` | Debian proper, without the Ubuntu and Kali profiles that share its family |
| `kali` | Both Kali profiles: the live one (family `kali`) and the preseed one (family `debian`) |
| `windows-retro` | Windows NT 4.0, 98, 2000 and XP. Windows 7, 10 and 11 stay in `windows` |
| `netlab` | The network lab: `pfsense-lab`, `pihole-lab`, `lubuntu-lab` |
| `proxmox-lab` | The Proxmox lab: `proxmox-ve` (ZFS mirror over two disks) and `proxmox-lab-client` (Xfce + Firefox), joined by the `pve-lan` segment |
| `smoke` | One profile per install flow that downloads its own medium, the lightest of each: `alpine-ci`, `ubuntu-server-ci`, `debian-server`, `almalinux-server`, `alpine-niri`, `arch-noctalia`, `opensuse-tumbleweed-autoyast`, `nixos-server`, `freebsd-unattended`. Run it before a full matrix: it answers "is every bootstrap flow still working" without the desktop installs |

A group is a selector, like the bare `check-vms`, so it skips `experimental` profiles;
naming one on the command line still runs it, even together with `--group`. Adding a
profile to a category is one line in its own `meta.groups`, and a test fails if an
Ubuntu profile forgets it.

`--parallel N` runs up to N VMs concurrently; `--parallel auto` (what
`make validate-vms` uses) packs them by the host's resources instead: every VM
costs its guest RAM plus 512 MB of QEMU overhead and its vCPUs, the budget is
the memory available at the start minus a 2 GB reserve for the host and the
CPUs with a 2x oversubscription, and the next pending VM starts as soon as it
fits, so four old Ubuntus run together while a 4 GB Windows waits for room
(a profile that does not fit even an idle host still runs, alone). The plan and
each start are printed; `peak concurrency` closes the run. `--clean-first` cleans
unattended/bootstrap profiles before the run without asking. `--restore` is the
non-destructive alternative: it stashes each installed VM's `artifacts/<vm>`
directory aside, runs the matrix on a virgin state, then removes what the test
created and moves the originals back, so validating every unattended flow does
not cost you the VMs you already have installed. A stash is kept under
`artifacts/.check-vms-restore/` only for the duration of the run.

Both parallel modes keep VMs sharing host TCP ports from running together,
including forwards used only during installation. For example, pfSense's WAN
forwards overlap with Pi-hole and Lubuntu's installer SSH ports (2238/2239),
so these jobs wait for pfSense to finish; unrelated VMs can still run alongside it.
The waiting message lists the conflicting ports. This coordination applies to
jobs in the same `check-vms` invocation.

### Occasional stalls, and how to tell one from a regression

AutoYaST has a long window where silence is normal: between linuxrc's last line
(the guest's IP addresses) and the token its chroot script prints at the end,
YaST runs on the **graphical** console and writes nothing to the serial. A
screenshot taken during that window shows the ordinary "Performing Installation
— Installing Packages" progress bar, which is what `--document` keeps; the
2026-09-19 investigation of a row that had timed out found exactly that, with
the medium six days old and seven other VMs sharing the line. Before suspecting
the profile, look at the screen.

A row that times out is not automatically a broken profile. Two guests have now been seen to
stall on an install that works: `windowsnt4-unattended` (rows 24 and 25 of
[NT4_PITFALLS.md](NT4_PITFALLS.md)) and, on 2026-09-16, `windows10-unattended`, whose matrix row
stopped at 211 s on an empty Windows Setup screen and burned the whole 3600 s timeout — while the
same row, re-run alone straight afterwards with the same ISO and the same code, reached the
completion token in nine minutes and passed in 622 s with its post-install over SSH.

How a stall looks, as opposed to a flow that is failing:

- the timeline stops producing frames. `--document` keeps a frame only when the screen changed,
  so a row whose last frame is minutes or hours before the timeout was showing one unchanging
  screen the whole time;
- the guest's disk stops being written (`ls -l artifacts/<vm>/disk.*` while the run is going),
  and the QEMU process is idle rather than busy;
- the captured output ends mid-phase with no error from the guest.

What to do with one:

1. **Re-run that single row before looking for a regression**: `vmctl check-vms <name>
   --no-clean-first --report <dir>`. Nine minutes of evidence beats an afternoon of theories, and
   for both guests above the retry passed.
2. Keep the failing evidence first. `--restore` removes the artifacts of the row when the run
   ends, so a disk or a log worth reading must be copied aside **while the run is still going**.
3. Do not demote a profile, or advance its `meta.verified`, on the strength of one stall in
   either direction: a stall says nothing about the flow, which is exactly why it is worth
   recognising it as such.

There is no automatic retry yet — the shape agreed for one is in
[PROFILE_TODO.md](PROFILE_TODO.md): a single extra attempt, the outcome always stating that it
passed at attempt 2 of 2 together with the first failure's reason, no retry for deterministic
failures such as a missing ISO, and the VM's artifacts cleaned in between.

### HTML reports and screenshots

```bash
vmctl prep alpine-ci  # boot-check profiles require a prepared disk
vmctl check-vms alpine-ci debian-server --restore --report --open
vmctl check-vms alpine-ci debian-server --parallel 2 --restore --report
vmctl check-vms debian-server --report artifacts/check-vms/my-run
```

`--report [DIR]` writes `report.html`, `results/<profile>.json`, and
`screens/<profile>.png` under `artifacts/check-vms/<timestamp>/` by default.
Use a new directory for each run, outside per-VM artifact directories so
`--restore` cannot remove the report. Put profile names before `--report`:
a following bare argument is interpreted as its directory. `--open` also
implies a report and opens the completed HTML with `xdg-open`.

The report includes host, UTC date, Git commit, totals, profile ID/display
name, flow, outcome, last phase, elapsed seconds, detail/error and final screen.
CSS and PNGs are embedded: `report.html` can be shared by itself. The separate
PNGs and JSON records are useful for automation. Filter the report by VM name/ID
and outcome directly in the browser; the counter shows the visible subset while
the summary cards retain the totals for the complete run. Screenshot conversion uses
Python's standard library; Pillow and ImageMagick are not required.

Bootstrap screenshots are taken through QMP before the final `cmd_stop`,
on success and on failure. Boot checks retain the latest live framebuffer
while serial validation runs, without changing the token/poweroff sequence.
Parallel workers write separate results and screenshots; the parent renders
the report after the workers complete. Report files survive artifact restore.

PASS means validation and capture succeeded; WARN means validation passed but
no screenshot was available; FAIL means validation failed; SKIP means the flow
could not be run. A black framebuffer can still produce a screenshot: the
report does not infer desktop readiness from pixels. Missing screenshots are
explained in the detail field. Screenshot warnings do not change the matrix's
exit status (nonzero for validation failures). During installation the phase identifies the attempted bootstrap flow;
it changes to `post-install` before SSH provisioning, including when that
provisioning fails. Durations include capture and shutdown. A worker that exits
without a result is reported at phase `worker` with unavailable duration (0).


### Profile sheets (PDF)

With `--document` each row writes its own sheet as soon as it finishes, so a long matrix fills
`<report>/pdf/<lang>/` while it runs; the index is written once, at the end of the run, and
`vmctl report-pdf <report>` rebuilds everything (index included) from a report at any point,
even while the matrix is still going.

```bash
vmctl check-vms --report --document            # the run keeps a screenshot timeline and ends with the PDFs
vmctl report-pdf                               # or later, from the newest report; --lang it for one language
```

`--document` (opt-in, needs `--report`) makes every row keep a timeline: one QMP screenshot every
30 s while the installer and the post-install run, kept only when the screen changed, at most 40
per profile. Most unattended installers work on the serial console and leave the screen black:
those frames are the last lines of the installer log instead, printed in the sheet as text. At the end (or with `vmctl report-pdf <dir>` on any report) each
profile gets a Markdown page and a PDF per language under `<report>/pdf/<lang>/` — profile facts,
outcome and duration, the captioned frames, the final screen — plus an `index.pdf`. Without
`--document` the sheets carry the final screen only. The PDF step needs the `markdown` and
`weasyprint` packages (the same as `make guides`); the Markdown is written regardless.

### Debian Xfce

`vmctl bootstrap-preseed debian-xfce --timeout 3600` installs the Debian 13 Xfce task with LightDM autologin and a ttyS0 getty. SSH uses port 2263; `verify-desktop` requires an active local session, the `xfce4` package and `xfce4-session`. Check current status with `vmctl list`.

### Debian 13 KDE (Preseed)

`vmctl bootstrap-preseed debian-kde --timeout 3600` (SSH 2262). Debian 13 kde-desktop task with sddm autologin, serial getty and active-session/package/process checks. Experimental until a clean live PASS.

### Debian 13 GNOME (Preseed)

`vmctl bootstrap-preseed debian-gnome --timeout 3600` (SSH 2264). Debian 13 gnome-desktop task with gdm3 autologin, serial getty and active-session/package/process checks. Experimental until a clean live PASS.

### Ubuntu Unity 24.04 LTS

`vmctl bootstrap-unattended ubuntu-unity-24.04 --timeout 3600` (SSH 2265). Ubuntu Server 24.04.4 autoinstall with ubuntu-unity-desktop, lightdm autologin and a serial getty. The 40G disk leaves room for desktop packages; verification requires an active local graphical session and compiz. Experimental until a clean live PASS.

### Ubuntu Cinnamon 24.04 LTS

`vmctl bootstrap-unattended ubuntu-cinnamon-24.04 --timeout 3600` (SSH 2266). Ubuntu Server 24.04.4 autoinstall with ubuntucinnamon-desktop, lightdm autologin and a serial getty. The 40G disk leaves room for desktop packages; verification requires an active local graphical session and cinnamon. Experimental until a clean live PASS.

### Ubuntu Studio 24.04 LTS

`vmctl bootstrap-unattended ubuntustudio-24.04 --timeout 3600` (SSH 2267). Ubuntu Server 24.04.4 autoinstall with ubuntustudio-desktop, sddm autologin and a serial getty. The 80G disk leaves room for desktop packages; verification requires an active local graphical session and plasmashell. Experimental until a clean live PASS.

### Edubuntu 24.04 LTS

`vmctl bootstrap-unattended edubuntu-24.04 --timeout 3600` (SSH 2268). Ubuntu Server 24.04.4 autoinstall with edubuntu-desktop, gdm3 autologin and a serial getty. The 60G disk leaves room for desktop packages; verification requires an active local graphical session and gnome-shell. Experimental until a clean live PASS.

### Fedora 44 KDE Plasma

`vmctl bootstrap-kickstart fedora-kde --timeout 3600` (SSH 2260). Plasma 6 Wayland with SDDM autologin; verifies an active local session, plasma-desktop, plasmashell and user-owned kwin_wayland. Compare kubuntu-24.04: Plasma 5.27 X11. Everything netinst uses the Fedora 44 online installation repository. Experimental until a clean live PASS.

### Fedora 44 Kinoite

`vmctl bootstrap-kickstart fedora-kinoite --timeout 3600` (SSH 2261). Plasma 6 Wayland with SDDM autologin; verifies an active local session, plasma-desktop, plasmashell and user-owned kwin_wayland. Compare kubuntu-24.04: Plasma 5.27 X11. The ostree ref is read from the ISO; %post only configures. Extra packages use rpm-ostree after installation and activate on reboot. Experimental until a clean live PASS.

`fedora-kinoite`: Fedora 44 ships Plasma Login Manager. The first installed boot uses multi-user.target; SSH layers SDDM with rpm-ostree and selects its unit for the next deployment. Desktop checks run after the mandatory reboot. [Fedora 44 login-manager change](https://fedoraproject.org/wiki/Changes/PlasmaLoginManager).

### Kali Linux Xfce (Preseed)

`vmctl bootstrap-preseed kali --timeout 3600` (SSH 2269). Kali netinst preseed uses the vendor Xfce task selection and kali-rolling mirror, LightDM autologin and serial getty. Verification requires kali-desktop-xfce, an active local session and xfce4-session. Experimental until a clean live PASS.

### CentOS Stream 10 Server (Kickstart)

`vmctl bootstrap-kickstart centos-stream-10 --timeout 3600` (SSH 2270). Server-only kickstart from the boot ISO and Stream 10 online repository. Requires an x86-64-v3 host CPU with KVM; QEMU uses the host CPU. SSH and ttyS0 getty are enabled. Experimental until a clean live PASS.

## FreeBSD disc1 (`bootstrap-freebsd`)

`freebsd-unattended` uses the vendor FreeBSD 14.3 disc1, BIOS/pc, UFS on `vtbd0`,
virtio networking (`vtnet0`) and SSH port 2271. The manual `freebsd` profile is separate.
The [bsdinstall manual](https://man.freebsd.org/cgi/man.cgi?query=bsdinstall&sektion=8&manpath=FreeBSD+14.0-RELEASE+and+Ports)
documents `/etc/installerconfig`: a partition/distribution preamble and a chroot script.
On 2026-09-14 the unmodified disc1 installed successfully from its shell; a second ISO
with only an answer-file graft confirmed that the vendor startup detects that file.
Screens and the detection transcript are in `artifacts/freebsd-probe/manual/`.

The host needs `xorriso` and `growisofs` (dvd+rw-tools). A per-VM copy is updated with
`growisofs -M -V <original-label>`, preserving FreeBSD's hidden El Torito extents
and ISO9660 label (the kernel mounts that label). Serial output uses `/dev/console`,
not the callout device `/dev/cuau0`, which is busy while ttyu0 owns the console. `rc.local` is replaced
because the vendor's `startbsdinstall` asks terminal/dialog questions. The wrapper
configures DHCP, saves the resolver outside BSDINSTALL_TMPETC (which bsdinstall
recreates; the preamble restores the live resolv.conf symlink target), invokes `bsdinstall` with non-TTY stdin, catches failures with an EXIT
trap, emits `==> FreeBSD installation FAILED: ...` and requests natural poweroff.
`run_and_expect` bounds the installer (1800 s by default); lifecycle explains its error.

The chroot installs `lab`/`lab`, the project SSH key and sudo via pkg. Its PATH includes
`/usr/local/bin`: without it package hooks cannot find `indexinfo` (observed manually).
The script ends in `sync`, bsdinstall then unmounts UFS (the filesystem flush), and only
its successful return permits `==> FreeBSD installation complete!` and `shutdown -p now`.
The host allows 120 s for that natural shutdown. A disk boot verifies the SSH identity,
`pgrep -a -x sshd`, `service sshd onestatus`, `freebsd-version` and passwordless sudo. No systemd or desktop check is used.

```sh
vmctl bootstrap-freebsd freebsd-unattended --timeout 1800
vmctl check-vms freebsd-unattended --clean-first --timeout 1800 --report --document
```

FreeBSD pgrep excludes ancestors by default: a check executed over SSH must use `-a`
to include the listener that is its ancestor ([vendor manual](https://man.freebsd.org/cgi/man.cgi?query=pgrep&sektion=1));
otherwise a healthy daemon produces a false FAIL (confirmed live on 2026-09-14).

## Proxmox VE (`bootstrap-proxmox`) and the Proxmox lab

`proxmox-ve` installs Proxmox VE 9.2 with the vendor's own automated installer on a **ZFS RAID1
root over two virtio disks**: `disk` is `vda`, and `extra_disks` adds `vdb` (see below). What the
flow does, and why:

- **No Proxmox tool on the host.** The ISO's GRUB offers "Install Proxmox VE (Automated)" as soon
  as `/auto-installer-mode.toml` (`mode = "iso"`) exists at its root, and the installer then reads
  `/answer.toml` from the same medium. That is exactly what `proxmox-auto-install-assistant
  prepare-iso --fetch-from iso` does, with `xorriso -boot_image any keep -dev <copy> -map ...`, so
  `proxmox.ensure_install_iso` does the same on a per-VM copy under `artifacts/<vm>/proxmox/`
  (cached by a stamp of source ISO + answer file). No Docker, no Debian package.
- **Direct kernel boot.** `boot/linux26` + `boot/initrd.img` are extracted from that copy and booted
  with `-kernel` and the ISO's own automated append plus `console=ttyS0,115200`, so the installer's
  progress (`INFO: progress 49.8 % - extracting base system`) reaches
  `logs/install-proxmox.stdout.log` instead of the framebuffer only. The initrd finds the copy on
  the SATA CD by itself.
- **Completion is the installer's own power-off.** The answer file sets `reboot-mode =
  "power-off"` and QEMU runs with `-no-reboot`: the installer exports the pool and powers off
  (`Finished: 'ok' Installation finished - auto powering off in 5 seconds`), QEMU exits, and that
  natural exit is the signal, as in the Ubuntu autoinstall flow. `reboot-on-error` stays false, so a
  failed install drops to a shell and shows as the `--timeout` with the console in the log.
- **Only root.** Proxmox VE creates no other user: `ssh_provision.user` must be `root`, the project
  key goes in through `root-ssh-keys`, the password through `root-password-hashed`
  (`proxmox_config.root_password_hash`; a local override changes it, the user name cannot change).
- **Verification over SSH**: `pveversion`, `zpool status -x rpool`, a `zpool list -v` check that
  the pool is one `mirror` of exactly two devices, `proxmox-boot-tool status` listing **two** ESPs
  (the guest boots from either disk), the web GUI answering on `https://127.0.0.1:8006/` and
  `pveproxy`/`pvedaemon`/`pve-cluster` active.

The host reaches the web GUI through the NAT NIC's forward on `https://127.0.0.1:8006` (`root`
and the profile's password). Running guests inside Proxmox needs nested virtualization on the host.

### The three-node cluster

`proxmox-ve`, `proxmox-ve-node2` and `proxmox-ve-node3` are three installs of the same profile
(SSH 2276/2278/2279, web GUI `https://127.0.0.1:8006`/`8007`/`8008`, `vmbr1` 10.10.10.2/.3/.4), each
with `proxmox_config.cluster = {"name": "pve-lab", "primary": "proxmox-ve"}`. `vmctl group install
proxmox-lab` forms the cluster once the stack runs with its runtime NICs (`vmctl group cluster
proxmox-lab` does that step alone on a running stack; both are idempotent). `pvecluster.form`:

- **pins each node name to its lab address in `/etc/hosts` first.** After the install every node's
  name resolves to `10.0.2.15`, the private slirp address that all three share; pmxcfs resolves the
  node name once, at start, so the line is rewritten and pve-cluster restarted;
- runs `pvecm create pve-lab --link0 10.10.10.2` on the primary, so corosync runs on the segment;
- for each other node: authorizes its root key on the primary, records the primary's host key
  (`ssh-keyscan`) and runs `pvecm add 10.10.10.2 --link0 <its address> --use_ssh`, which asks nothing;
- waits for `/etc/pve` to accept writes after every `pvecm` call: pmxcfs restarts and answers "I/O
  error" for a few seconds (the first run failed there appending the key, verified live);
- checks `Quorate: Yes` with the expected node count.

Only node 1 carries the LXC apps: a node joining a cluster must hold no guests. Verified live on
2026-09-24: `vmctl group install proxmox-lab` kept node 1 and the client, installed nodes 2 and 3
(about 6 minutes each), restarted the stack and formed the cluster (3 nodes, 3 votes, quorate); a
second run changed nothing; the client reached the three GUIs and both apps. Each node has 3 GB,
so the lab needs about 12 GB with the client.

The map page (`vmctl group map proxmox-lab --open`) ends with a **runbook** generated from the
same profiles, in the order a person would run it, with a table of contents. Every block is marked
*run* (changes something), *check* (read-only) or *try* (a reversible experiment) and carries the
SSH line of the member it runs on:

1. **Install**: `vmctl group install`, or one command per member with its flow; then `pveversion`
   and the PVE services on each node.
2. **Lab network**: `vmbr1`, the segment port with `learning off`, a ping to every other member.
3. **ZFS pool (mirror)**: the `answer.toml` snippet; on each node `zpool status -x`, `zpool status`,
   `zpool list -v`, `zfs list`, `pvesm status`, `proxmox-boot-tool status`; a drill that scrubs the
   pool, takes the second mirror half offline (DEGRADED, the node keeps running) and back (resilver).
4. **LXC containers**: the helper line and the equivalent upstream invocation; the checks read
   `/etc/pve/nodes/*/lxc/<id>.conf`, which is cluster-wide, so they hold after a migration.
5. **Cluster**: the steps node by node, then `pvecm status`/`nodes`, `corosync-cfgtool -s`,
   `ha-manager status`, and a `pct migrate` to try.
6. **From the client**: one `curl` per GUI and app.
7. **Run the stack**.

Every check block was run live on the formed cluster on 2026-09-24 (57 checks, all passing after
the container checks moved to `/etc/pve`, because the containers had been migrated from the GUI in
the meantime), and the pool drill went DEGRADED and back to ONLINE on `proxmox-ve`.

### `extra_disks`

`extra_disks` is a list of `{"path", "size", "format"}` beside `disk`: virtio disks that follow
the main one on the bus (`vdb`, `vdc`, ...), created with it by the bootstrap, with no boot index,
removed by `vmctl clean`. Checkpoints, clones and `export-libvirt` copy or render one disk and
refuse a profile that has them (a restored half of a mirror would not match its partner).

### The lab: `proxmox-ve` + `proxmox-lab-client`

Both profiles live in `vms/profiles/proxmox-lab.json` and in the declared group `proxmox-lab`.
Each VM has two NICs: `nat` (slirp: internet, SSH 2276/2277, the 8006 forward) and `lan`, the
`pve-lan` segment (the same multicast socket segment the network lab uses), **in the runtime
phase only**: the installers see one NIC and never ask which one to configure, and the NAT NIC
keeps its PCI slot and name when the second one appears. Because the post-install boot runs with
the install NICs, the segment is configured for the next boot, by MAC (both MACs are fixed in the
profiles):

- on Proxmox, `pve-lan.sh` writes a systemd `.link` naming that NIC `pvelan0`, a `vmbr1` bridge
  with `10.10.10.2/24` on it in `/etc/network/interfaces`, and rebuilds the initramfs (its udev
  names the NICs first);
- on the client (`debian-xfce` plus `firefox-esr`), `client-lan.sh` adds a NetworkManager
  profile bound to the MAC with `10.10.10.10/24`; Firefox's policies
  (`/etc/firefox/policies/policies.json`) make `https://10.10.10.2:8006/` the homepage and bookmark
  the three GUIs and the two apps, and the autostart opens the GUI plus one tab per app. The
  keyboard is `preseed_config.keyboard_layout` (`us` in the tracked profile; a local override such
  as `it` changes it).

```bash
vmctl bootstrap-proxmox proxmox-ve
vmctl bootstrap-preseed proxmox-lab-client      # or: vmctl check-vms --group proxmox-lab
vmctl stop proxmox-ve; vmctl stop proxmox-lab-client
vmctl start proxmox-ve --background --headless  # runtime phase: both NICs
vmctl start proxmox-lab-client                  # the desktop opens Firefox on the Proxmox GUI
```

The client's desktop opens Firefox on the GUI by itself: an XDG autostart entry runs
`/usr/local/bin/vmctl-open-proxmox`, which waits (up to 10 minutes) until `https://10.10.10.2:8006/`
answers, because the lab starts both VMs together and Proxmox answers later than the desktop logs in.
The policies drop the first-run and privacy-notice tabs. The certificate is Proxmox's self-signed
one, so the browser asks once. The cross-VM checks need both VMs running and are not part of a
`check-vms` row, which runs one VM at a time: `vmctl group up proxmox-lab`, then from the client
`curl -k https://10.10.10.2:8006/`, `http://10.10.10.20/`, `http://10.10.10.21:8080/`.

The post-install also switches the repositories to `pve-no-subscription` (`pve-repos.sh`: the
enterprise ones answer 401 and "Update package database" fails in the GUI) and creates two light
LXC containers with the [Proxmox VE Helper-Scripts](https://community-scripts.org/) (`lab_services`,
`pve-community.sh`): **IT-Tools** (CT 200, Alpine, 256 MB, `http://10.10.10.20/`) and **Glance**
(CT 201, Debian, 512 MB, `http://10.10.10.21:8080/`). They are bookmarked in the client's toolbar.
The scripts come from the project's `main` branch and are not pinned. What made them run, and
what went wrong first (all verified live on 2026-09-24):

- `mode=default` is what skips their whiptail menu; without a TTY and without it the script prints
  "User exited script" and exits **0**, so the helper checks the log and `pct status`. `TERM=xterm`
  keeps `clear`/`whiptail` from failing, and `/usr/local/community-scripts/diagnostics` with
  `DIAGNOSTICS=no` pre-answers the one question default mode still asks.
- Containers go on `vmbr0` (the NAT NIC: the template and packages come from the Internet) with a
  **static** address outside slirp's DHCP pool (`.15`-`.30`): `10.0.2.100` and `.101`. With DHCP the
  first container was handed `10.0.2.15`, the Proxmox host's own address (the installer turns its
  DHCP lease into a static configuration, so slirp's server believes it free), and the duplicate cut
  the host off until the container was stopped over the lab segment.
- The second NIC puts each container on `vmbr1` at its lab address. `pct` refuses a bridge that does
  not exist, even with the container stopped, and in the post-install boot the segment port is
  missing, so `pve-lan.sh` runs `ifreload -a` right away: it reports the missing `pvelan0` and exits
  1, but creates `vmbr1` with its address, and the NIC is hot-plugged. At the runtime boot the port
  joins the bridge and the containers start by themselves (`onboot`).
- **The segment port must not learn MACs.** The segment is a QEMU multicast socket, which loops every
  frame back to its sender: the bridge then learned the containers' MACs on `pvelan0` and sent their
  traffic back into the segment, so ping half worked and TCP from the client never reached them.
  `vmbr1` carries `post-up bridge link set dev pvelan0 learning off`; Proxmox's own address was never
  affected because its MAC is local to the bridge.

## Groups as stacks and the lab map (`vmctl group`)

A declared group (`meta.groups`) can be run as one stack. A **lab** is a group whose members are
all on a network segment: today `netlab` and `proxmox-lab` (`vmctl group list --labs`).

```bash
vmctl group list [--labs] [--json]
vmctl group status proxmox-lab        # running, disk state and addresses of every member
vmctl group up proxmox-lab            # headless background starts; infrastructure first
vmctl group down proxmox-lab          # reverse order
vmctl group map proxmox-lab --open    # artifacts/labs/<group>/network.html (+ lab.json)
vmctl group install proxmox-lab       # what is missing, in start order, then down + up (runtime NICs)
vmctl group clean proxmox-lab         # stop and delete every member's disk (asks; checkpoints kept)
vmctl group cluster proxmox-lab       # only the cross-VM step: form the Proxmox cluster (idempotent)
```

`install` is cumulative: installed and verified members are kept, missing ones go through their own
unattended flow (the one `check-vms` would pick), so a rerun after a failure continues where it
stopped. A member whose disk is empty or holds an unfinished install is deleted and reinstalled,
after asking (`--yes` in scripts): an installer cannot resume on it. The installs run with the
install-phase NICs, so the whole stack is stopped and started again at the end.

The start order puts routers and hypervisors first (`meta.role` router/pfsense/hypervisor), then
services (pihole/dns/server), then the rest: `pfsense-lab -> pihole-lab -> lubuntu-lab`, the network
lab's own install order. `up` skips members without an installed disk. The map is one
self-contained HTML page (inline SVG, light and dark): the host bar with every forward on
127.0.0.1, one box per VM with its live state, the segments as buses with each NIC's address and
MAC, the containers a hypervisor serves (`lab_services`) and links for the web forwards. The data
comes from the profiles only (`labs.model`): the network lab's addresses from its `network_lab`
topology, the others from `networks[].address`.

In `vmtui` the **Labs** filter (or **F2**, which switches between labs and single profiles and
keeps the selected profile) lists each lab with its members in start order. On a lab row Enter
installs what is missing, else starts the stack, else opens the map; → shows *Start stack*, *Stop
stack*, *Network map*, *Stack status*, *Install lab…* and *Clean lab…* (both ask y/N, default No,
before deleting a disk).
