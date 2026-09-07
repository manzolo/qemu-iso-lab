# Arch Linux with niri (DankMaterialShell or Noctalia) and CachyOS

Profiles: `arch-dms` (SSH 2230), `arch-noctalia` (2226), `cachyos-desktop` (2223).
Flow `bootstrap-archinstall`: a self-contained `pacstrap` script on the live ISO, without
`archinstall`. Typical time: 15-30 minutes, depending on the mirrors.

## 1. Prerequisites

- Arch ISO (`isos/archlinux-latest-x86_64.iso`, downloaded and validated by `vmctl`) or
  CachyOS (`cachyos-desktop-linux-latest.iso`, found on the mirror through `iso_discovery`).
- Host with OVMF (EFI profiles) and `xorriso`.
- In `local.json`: `archinstall_config.username`/`password` and `ssh_provision.user` with the
  same name, optional dotfiles in `copy_from_host`, `shared_dir` if you want the shared
  folder.

## 2. The command

```bash
vmctl bootstrap-archinstall arch-dms       # --timeout 1800 by default
vmctl attach arch-dms                      # optional
```

## 3. What happens

1. **Seed** `bootstrap.iso` in `artifacts/<vm>/archinstall/`: `install.sh` (sgdisk, pacstrap,
   arch-chroot, GRUB, user, services) and `run.sh` that starts it. It is attached as a virtio
   CD, so in the live system it is `/dev/vdb`.
2. **Direct boot** with kernel and initramfs extracted from the ISO
   (`arch/boot/x86_64/vmlinuz-linux` and `initramfs-linux.img`) and kernel line:

        archisobasedir=arch archisolabel=ARCH_YYYYMM console=ttyS0,115200 quiet

3. **Login and trigger on the serial**, typed by the automation (and by you, if you do it by
   hand):

        archiso login: root
        root@archiso ~ # mkdir -p /tmp/archconf && mount /dev/vdb /tmp/archconf && bash /tmp/archconf/run.sh

4. **install.sh** in the live system: partitions `/dev/vda` (ESP + root), `pacstrap` with the
   profile's packages (niri, quickshell/DMS or Noctalia, sddm/greetd, NVIDIA open where
   foreseen), in `arch-chroot` sets locale, time zone, user and password, passwordless sudo,
   `NetworkManager`, `sshd`, GRUB on UEFI, then the profile's `bootstrap_chroot_commands`.
5. **Closing**, always in this order:

        sync
        blockdev --flushbufs /dev/vda /dev/vda1 /dev/vda2 || true
        echo "==> Arch Linux installation complete!"
        poweroff -f

6. **Post-install** over SSH: copy of the dotfiles (`copy_from_host`, for instance
   `~/.config/niri` and `~/.config/DankMaterialShell`), then the `post_install_run` (`niri
   validate`, service checks, shared folder).

## 4. CachyOS on the same flow

Only the details read from the profile change: kernel `vmlinuz-linux-cachyos` and
`initramfs-linux-cachyos.img`, label `COS_YYYYMM`, kernel line with
`systemd.unit=multi-user.target` added (so the live Plasma and Calamares do not start),
prompts `CachyOS login:` and `root@CachyOS`, and `inherit_live_pacman_conf` which copies the
live `pacman.conf` with the `[cachyos]` repo into the target. The real desktop is the
`cachyos-niri-noctalia` metapackage with `sddm`: without it niri starts but the screen stays
grey.

## 5. Check and use

```bash
vmctl shell arch-dms
niri --version && systemctl --user status dms 2>/dev/null | head -3
ls ~/shared ~/Desktop/shared      # shared folder, if configured
```

`vmctl start arch-dms` opens the graphical session with `virtio-vga-gl`; the NVIDIA
profiles need the GPU passed to the guest and are not covered by this guide.

## 6. Notes

- The flow stays valid only if the closing of `install.sh` is left alone: token after the
  flush and wait for the natural power-off (see `docs/ARCH_GRUB_BOOT_FIX.md`).
- The niri compositors disable power-key handling (`input { disable-power-key-handling }`),
  otherwise `vmctl stop` would suspend the VM instead of powering it off.
