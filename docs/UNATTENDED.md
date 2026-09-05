# Unattended installs

Five installers run headless, driven over the serial console, and end with the
VM installed, booted in the background and provisioned over SSH. Every
`bootstrap-*` command accepts `--dry-run` and prints each step it would run.

- [The common shape](#the-common-shape)
- [Ubuntu: autoinstall](#ubuntu-autoinstall)
- [Debian: preseed](#debian-preseed)
- [AlmaLinux / RHEL: kickstart](#almalinux--rhel-kickstart)
- [Arch: pacstrap script](#arch-pacstrap-script)
- [Omarchy: cidata](#omarchy-cidata)
- [The completion-token rule](#the-completion-token-rule)
- [Boot checks and the validation matrix](#boot-checks-and-the-validation-matrix)

## The common shape

1. Render the answer file from the profile section (`autoinstall`,
   `preseed_config`, `kickstart_config`, `archinstall_config`, `omarchy_config`)
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

## AlmaLinux / RHEL: kickstart

```bash
vmctl bootstrap-kickstart almalinux-server
```

Renders `ks.cfg` into a `KS_CFG` seed ISO, extracts `vmlinuz` and `initrd.img`,
boots anaconda in text mode (`inst.ks=hd:LABEL=KS_CFG:/ks.cfg inst.text
inst.cmdline`) and waits for `==> Kickstart install complete!`. The kickstart
creates the user in `wheel` with passwordless sudo and installs the SSH key
when the profile provides one.

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
