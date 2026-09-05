# Unattended installs

Six installers run headless, driven over the serial console, and end with the
VM installed, booted in the background and provisioned over SSH. Every
`bootstrap-*` command accepts `--dry-run` and prints each step it would run.

- [The common shape](#the-common-shape)
- [Ubuntu: autoinstall](#ubuntu-autoinstall)
- [Debian: preseed](#debian-preseed)
- [AlmaLinux / RHEL / Fedora: kickstart](#almalinux--rhel--fedora-kickstart)
- [Arch: pacstrap script](#arch-pacstrap-script)
- [Omarchy: cidata](#omarchy-cidata)
- [Alpine: setup-alpine](#alpine-setup-alpine)
- [The completion-token rule](#the-completion-token-rule)
- [Boot checks and the validation matrix](#boot-checks-and-the-validation-matrix)

## The common shape

1. Render the answer file from the profile section (`autoinstall`,
   `preseed_config`, `kickstart_config`, `archinstall_config`, `omarchy_config`,
   `alpine_config`)
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

## Ubuntu: autoinstall

```bash
vmctl bootstrap-unattended ubuntu-niri-local
```

Generates the cloud-init seed and the `autoinstall` seed, extracts
`casper/vmlinuz` and `casper/initrd`, boots with `autoinstall` and `-no-reboot`,
waits for the installer to exit, then starts the installed VM and runs
`cloud_init.post_install_run` / `ssh_provision`. Step by step:

```bash
vmctl prep ubuntu-niri-local
vmctl install-unattended ubuntu-niri-local
vmctl start ubuntu-niri-local --headless --background
vmctl post-install ubuntu-niri-local
```

## Debian: preseed

```bash
vmctl bootstrap-preseed debian-server
```

Renders `preseed.cfg` into a `PRESEED_CFG` seed ISO, extracts `vmlinuz` and
`initrd.gz`, boots the Debian installer with the preseed kernel arguments and
waits for `==> Debian preseed install complete!` on the serial console.

## AlmaLinux / RHEL / Fedora: kickstart

```bash
vmctl bootstrap-kickstart almalinux-server
vmctl bootstrap-kickstart fedora-niri-dms-local
```

Renders `ks.cfg` into a `KS_CFG` seed ISO, extracts `vmlinuz` and `initrd.img`,
boots anaconda in text mode (`inst.ks=hd:LABEL=KS_CFG:/ks.cfg inst.text
inst.cmdline`) and waits for `==> Kickstart install complete!`. The kickstart
creates the user in `wheel` with passwordless sudo and installs the SSH key
when the profile provides one.

The install source is `kickstart_config.inst_repo`: `cdrom` (default) for a
full ISO such as AlmaLinux minimal, or a repository URL for a netinst image.
`fedora-niri-dms-local` boots the Fedora Everything netinst and points
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

## Arch: pacstrap script

```bash
vmctl bootstrap-archinstall arch-dms-local
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
vmctl bootstrap-archinstall cachyos-nvidia-local
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
manual install (`vmctl install`). The `cachyos` profile (fixed VHD for Ventoy)
stays interactive.

## Omarchy: cidata

```bash
vmctl bootstrap-omarchy arch-omarchy-nvidia-local
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

On the host, `run_and_expect` waits up to 30 seconds for QEMU to exit on its
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
vmctl check-vms ubuntu-niri arch-noctalia-local --timeout 600
vmctl check-vms --parallel 4 --clean-first
```

`--parallel` controls how many VMs run concurrently; `--clean-first` cleans
unattended/bootstrap profiles before the run without asking.
