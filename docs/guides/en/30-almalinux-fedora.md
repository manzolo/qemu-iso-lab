# AlmaLinux 10 server and Fedora with niri via kickstart

Profiles: `almalinux-server` (SSH 2229, from the minimal ISO) and `fedora-niri-dms-local`
(SSH 2233, from the Everything netinst plus online repositories). Flow `bootstrap-kickstart`
with Anaconda in text mode. Time: 10 minutes Alma, 20-30 Fedora.

## 1. Prerequisites

- ISO `AlmaLinux-10.x-x86_64-minimal.iso` or `Fedora-Everything-netinst-x86_64-NN.iso`
  (downloaded by `vmctl`).
- `xorriso`, OVMF. In `local.json`: `kickstart_config.username`, `password_hash` or
  `password`, `ssh_provision.user`.

## 2. The command

```bash
vmctl bootstrap-kickstart almalinux-server
vmctl bootstrap-kickstart fedora-niri-dms-local
```

## 3. What happens

1. `ks.cfg` is rendered into `artifacts/<vm>/kickstart/` and packed into a seed ISO labelled
   `KS_CFG`, attached as a virtio CD.
2. Direct boot with `vmlinuz` and `initrd.img` extracted from the ISO and kernel line:

        inst.ks=hd:LABEL=KS_CFG:/ks.cfg inst.text inst.cmdline inst.repo=<cdrom|URL> console=ttyS0,115200

   `inst.repo` is `cdrom` for Alma (packages from the ISO) and the Fedora repository URL for
   the niri profile (`kickstart_config.inst_repo`), which also uses `%packages
   --ignoremissing`.
3. No input on the serial: Anaconda in `inst.cmdline` asks nothing, if the kickstart lacks
   something it stops with an error instead of prompting. Here too the console locale must
   stay ASCII.
4. `%post` in the target: SSH key, sudoers, SELinux permissive where foreseen, the profile's
   `post_commands` (for Fedora: COPR `avengemedia/danklinux` for quickshell, `dnf upgrade`
   because quickshell is built against Qt 6.11 from `updates`, greetd), then:

        sync; blockdev --flushbufs /dev/vda /dev/vda1 /dev/vda2 || true
        echo "==> Kickstart install complete!"
        poweroff -f

5. Post-install over SSH with the profile's `post_install_run`.

## 4. Check

```bash
vmctl shell almalinux-server
cat /etc/os-release | head -2; sudo dnf -q repolist
```

## 5. Doing it by hand

At the ISO boot menu add the kernel line above, pointing `inst.ks=` at your `ks.cfg` (file
on a labelled USB key, or `inst.ks=http://...`). Without `inst.cmdline` Anaconda shows the
text menu and asks for confirmation where the kickstart is incomplete.
