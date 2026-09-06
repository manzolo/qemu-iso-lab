# AlmaLinux, Rocky, Fedora and Silverblue via kickstart

Profiles: `almalinux-server` (SSH 2229, from the minimal ISO), `rocky9` (2245, the same
recipe on Rocky Linux 9), `fedora-niri-dms-local` (2233, from the Everything netinst plus
online repositories) and `fedora-silverblue` (2246, the immutable one). Flow
`bootstrap-kickstart` with Anaconda in text mode. Time: 10 minutes Alma and Rocky, 20-30
Fedora, 25-35 Silverblue.

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

## 6. Rocky Linux 9

`rocky9` is `almalinux-server` with another ISO: same kickstart, install source on the media
(`inst.repo=cdrom`), `@^minimal-environment`, SELinux enforcing.

```bash
vmctl bootstrap-kickstart rocky9
vmctl shell rocky9 -- cat /etc/rocky-release
```

## 7. Fedora Silverblue (immutable)

`fedora-silverblue` rides the same command, but the profile carries a `kickstart_config.ostree`
block and that changes what Anaconda does: the `%packages` transaction disappears and an
`ostreesetup` line takes its place, so the system is a deployment of the ostree tree the ISO
carries (`file:///ostree/repo`).

```json
"ostree": { "osname": "fedora", "remote": "fedora", "url": "file:///ostree/repo",
            "ref": "fedora/44/x86_64/silverblue", "ref_match": "silverblue" }
```

The ref carries the Fedora version, so `vmctl` reads it from `refs/heads` **inside the ISO**
at bootstrap time and only falls back to the profile value when it cannot (a dry run, no
xorriso). Swapping in the next release is a matter of changing `iso`/`iso_url`; the line
`[ok] ostree ref: fedora/NN/x86_64/silverblue` on the terminal says which tree was used.

Two consequences of immutability:

- `%post` runs inside the deployment: it can write configuration (GDM autologin, services)
  but it cannot install packages.
- Extra software is layered afterwards and takes effect at the next boot. The post-install
  runs `rpm-ostree install --idempotent --allow-inactive qemu-guest-agent spice-vdagent`;
  `rpm-ostree status` shows the pending deployment.

```bash
vmctl shell fedora-silverblue
rpm-ostree status
systemctl get-default
```
