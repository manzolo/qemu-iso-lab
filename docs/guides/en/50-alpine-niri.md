# Alpine Linux 3.23 with niri

Profile `alpine-niri` (SSH 2234, BIOS, 4 GB, 16 GB disk). Flow `bootstrap-alpine`:
`setup-alpine` with an answer file, then a chroot for packages and configuration. Time: 5
minutes.

## 1. Prerequisites

- ISO `alpine-standard-3.23.x-x86_64.iso`, found on the dl-cdn index by `iso_discovery`.
  The profile is pinned to 3.23: the Mesa of 3.24 has no virgl and niri, which refuses
  software rendering, never takes the screen with `virtio-vga-gl`.
- `xorriso`. In `local.json`: `alpine_config.username`, `password_hash` and
  `ssh_provision.user`.

## 2. The command

```bash
vmctl bootstrap-alpine alpine-niri
```

## 3. What happens

1. Seed ISO `ALPINESEED` in `artifacts/<vm>/alpine/` with `answers` (for `setup-alpine -f`),
   `install.sh` and `run.sh`; it is a virtio CD, `/dev/vdb` in the live system.
2. Direct boot with `boot/vmlinuz-lts` and `boot/initramfs-lts` and the live kernel line plus
   virtio and serial:

        modules=loop,squashfs,sd-mod,usb-storage,virtio_blk,virtio_pci console=tty0 console=ttyS0,115200 quiet

3. Login and trigger on the serial:

        localhost login: root
        localhost:~# modprobe virtio_blk 2>/dev/null; mdev -s 2>/dev/null; mkdir -p /media/vmctl-seed && mount -t iso9660 /dev/vdb /media/vmctl-seed && sh /media/vmctl-seed/run.sh

4. `install.sh`: exports `ERASE_DISKS=/dev/vda` and runs `setup-alpine -e -f answers`
   (keyboard, hostname, udev, DHCP, mirror plus community repository, user with SSH key,
   sshd, chrony, `setup-disk -m sys`); remounts the installed root and in the chroot
   installs the `packages` (niri, foot, fuzzel, greetd, pam-rundir, the explicit Wayland
   libraries, Mesa), the `optional_packages` one by one, sets the password, sudo for `wheel`,
   dbus and seatd, then the `chroot_commands` (greetd with autologin into `dbus-run-session
   -- niri --session`).
5. Closing: unmount, `sync`, `blockdev --flushbufs`, print
   `==> Alpine Linux installation complete!` and `poweroff -f`.
6. Post-install over SSH: niri's `config.kdl`, `niri validate`, check that the session runs.

## 4. Check

```bash
vmctl shell alpine-niri
cat /etc/alpine-release; rc-status default | head; pgrep -x niri
```

## 5. Known traps

- Without elogind nothing sets `XDG_RUNTIME_DIR`: `pam-rundir` is needed in
  `/etc/pam.d/greetd`.
- The niri package does not declare the Wayland libraries it loads at runtime:
  `wayland-libs-server` and `wayland-libs-client` must be listed (otherwise
  `NoWaylandLib`).
- greetd runs `initial_session` once per boot (`/run/greetd.run`): restarting the service
  shows the greeter, not the autologin.
