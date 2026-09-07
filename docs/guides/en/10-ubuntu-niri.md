# Ubuntu 26.04 with niri via autoinstall

Profiles: `ubuntu-niri` (SSH 2222) and `ubuntu-niri-gl` (customisable). Flow
`bootstrap-unattended`: subiquity autoinstall plus cloud-init for the first boot.
Time: 15-25 minutes.

## 1. Prerequisites

- ISO `ubuntu-26.04-live-server-amd64.iso` (downloaded by `vmctl`), OVMF, `xorriso` or
  `genisoimage`/`cloud-localds` for the seeds.
- In `local.json` (`-local` profile): `autoinstall.username`, `realname`, `password_hash`,
  `cloud_init.user`, `ssh_authorized_keys_file`, `ssh_key`, `copy_from_host` for the dotfiles.

## 2. The command

```bash
vmctl bootstrap-unattended ubuntu-niri           # --timeout 300 for the installer to exit
```

The separate stages, if you want to watch them one at a time:

```bash
vmctl prep ubuntu-niri
vmctl install-unattended ubuntu-niri --headless
vmctl start ubuntu-niri --headless --background
vmctl post-install ubuntu-niri
```

## 3. What happens

1. Two seeds in `artifacts/<vm>/`: the **autoinstall** seed (`user-data` with the
   `autoinstall` section, `meta-data`) and the **cloud-init** seed for the first boot, both
   labelled `cidata` as the `nocloud` source requires.
2. Direct boot with `casper/vmlinuz` and `casper/initrd` extracted from the ISO, `-no-reboot`
   and kernel line:

        autoinstall ds=nocloud console=ttyS0,115200n8

3. No input: subiquity reads `user-data` (storage, identity, `install_ssh`, packages) and
   reboots at the end; with `-no-reboot` QEMU exits and that is the completion signal (this
   flow has no serial token).
4. `vmctl` starts the installed disk headless with the cloud-init seed: on the first boot
   cloud-init creates/updates the user, installs `packages`, runs `runcmd` and `write_files`.
5. Post-install over SSH: waits for `cloud-init status --wait` and for apt to finish, then
   `copy_from_host` and `post_install_run` (install and check niri and DMS).

## 4. Check

```bash
vmctl shell ubuntu-niri
lsb_release -d; cloud-init status; niri --version
```

## 5. Doing it by hand

With the live-server ISO: at the GRUB menu press `e`, add `autoinstall ds=nocloud` to the
`linux` line and make the `cidata` seed available (USB key or second CD with `user-data` and
`meta-data`). Without `console=` the installer uses the screen.
