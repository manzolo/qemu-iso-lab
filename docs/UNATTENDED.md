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
`ttyS0` getty is `serial-getty@ttyS0.service`; 18.04 autologs in through GDM3. The desktop check accepts `x-session-manager`, the name the
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

`--parallel` controls how many VMs run concurrently; `--clean-first` cleans
unattended/bootstrap profiles before the run without asking. `--restore` is the
non-destructive alternative: it stashes each installed VM's `artifacts/<vm>`
directory aside, runs the matrix on a virgin state, then removes what the test
created and moves the originals back, so validating every unattended flow does
not cost you the VMs you already have installed. A stash is kept under
`artifacts/.check-vms-restore/` only for the duration of the run.

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
