# VM profiles

Every VM is a JSON object under `vms/profiles/*.json`. `vmctl` reads and merges
all files in that directory; `local.json` is loaded last and deep-merged over
the rest (see [PROVISIONING.md](PROVISIONING.md#guest-identity-and-localjson)).
`vmctl show <vm>` prints the resolved result, `--json` for scripts.

- [Core fields](#core-fields)
- [ISO sources](#iso-sources)
- [Disk](#disk)
- [Firmware](#firmware)
- [Video profiles](#video-profiles)
- [Artifacts](#artifacts)
- [Windows import templates](#windows-import-templates)
- [Adding a new VM](#adding-a-new-vm)

## Core fields

```json
{
  "my-vm": {
    "name": "My VM",
    "meta": { "slug": "my-distro", "family": "debian", "role": "desktop", "release_model": "stable" },
    "iso": "isos/example.iso",
    "iso_url": "https://example.invalid/example.iso",
    "disk": {
      "path": "artifacts/my-vm/disk.qcow2",
      "size": "32G",
      "format": "qcow2",
      "interface": "virtio"
    },
    "firmware": {
      "type": "efi",
      "code": "/usr/share/OVMF/OVMF_CODE_4M.fd",
      "vars_template": "/usr/share/OVMF/OVMF_VARS_4M.fd",
      "vars_path": "artifacts/my-vm/OVMF_VARS.fd"
    },
    "machine": "q35",
    "memory_mb": 4096,
    "cpus": 4,
    "network": "user",
    "audio": false,
    "usb_tablet": true,
    "video": { "default": "std", "variants": { "std": ["-vga", "std", "-display", "gtk"] } }
  }
}
```

| Field | Meaning |
|-------|---------|
| `name` | Human title shown by `vmctl list` and the TUI |
| `meta` | `family` drives grouping; `role` and `release_model` describe the guest. `status` is `manual`, `unattended` or `experimental`; `version` is the recipe's version (see [Profile versions](#profile-versions)); optional `verified` is the last maintainer-reported live PASS date (`YYYY-MM-DD`). `vmctl list` and HTML reports show status and verification separately from current test results. |
| `iso`, `iso_url`, `iso_urls`, `iso_discovery`, `iso_size`, `iso_sha256` | See [ISO sources](#iso-sources) |
| `disk` | See [Disk](#disk) |
| `firmware` | `efi` or `bios`, see [Firmware](#firmware) |
| `machine` | QEMU machine type, `q35` for modern guests, `pc` for old ones |
| `vmport` | `false` adds `vmport=off` to the machine: guests whose X server ships the vmmouse driver (Ubuntu 14.04 to 16.04) otherwise switch the PS/2 mouse to the VMware protocol and QEMU routes the absolute pointer to it instead of the USB tablet, leaving the desktop pointer stuck |
| `memory_mb`, `cpus` | Guest RAM and vCPUs |
| `network` | `user` (slirp with optional SSH port forward) |
| `networks` | Optional list of NICs replacing the single slirp one: `{"type": "user", "hostfwd": [{"host_port": 8080, "guest_port": 80}]}` (slirp; the first user NIC carries the `ssh_host_port` forward unless `"ssh": false`) or `{"type": "segment", "name": "lab-lan"}` (a host-local L2 segment shared by every VM naming it: multicast socket on plain QEMU, libvirt network after export). `phase` = `install`, `runtime` or `both` (default): install NICs exist only during a bootstrap and its SSH post-install, runtime NICs afterwards; a NIC keeps its slot and MAC (`mac`, or derived from disk path + slot) across phases. See [NETWORK-LAB.md](NETWORK-LAB.md). |
| `audio`, `audio_device` | Attach an audio device: `hda` (Intel HD Audio, default) or `ac97` for guests without an HDA driver (ReactOS, Windows XP and older) |
| `usb_tablet` | Absolute pointer for graphical guests |
| `guest_agent` | Optional boolean (default `false`). Adds a QEMU guest agent channel; see [Guest agent](#guest-agent). |
| `shared_dir` | Host folder shared with the guest over virtiofs: `{"source": "shared", "tag": "shared"}`. `source` is `~`-expanded, relative paths live under the repository (the default `shared/` is git-ignored). Needs `virtiofsd` on the host; every QEMU launch starts one per VM and the guest RAM becomes a shared memfd backend. On Linux guests the SSH post-install adds `/mnt/<tag>` to fstab (systemd automount), mounts it and links it as `~/<tag>` and on the desktop (`xdg-user-dir DESKTOP`, `Desktop` or `Scrivania`), like kvm-lab; Windows profiles get WinFSP + `VirtioFsSvc` at first logon, the share appears as a drive letter and a `<tag>.lnk` shortcut lands on the desktop. |
| `video` | Named QEMU argument sets, see [Video profiles](#video-profiles). Optional `headless` argument list replaces `-display none` for background/unattended boots; QMP and VNC are still added. |
| `installer_boot` | `kernel` and `initrd` paths inside the ISO for the unattended flows, when they differ from the flow's default (CachyOS: `arch/boot/x86_64/vmlinuz-linux-cachyos`) |
| `notes` | Free text shown by `vmctl show` |
| `meta` | Classification, not behaviour: `slug`, `family` (one spelling per distro: `debian`, `arch`, `windows`...), `role` (`desktop`, `server`...), `release_model`, `status` and `verified` ([Profile status](#profile-status-and-live-verification)), plus `groups`. `family`, `role`, `status` and the install flow already act as categories for `vmctl check-vms --group`; `meta.groups` adds only the ones they cannot express, such as `ubuntu` (which spans a dozen slugs inside the `debian` family) or `smoke`. Group names are lowercase letters, digits and hyphens. `vmctl list --groups` lists them all; the catalogue of declared ones is in [UNATTENDED.md](UNATTENDED.md#running-one-category-instead-of-the-whole-matrix). `logins` (`[{"user", "password", "note"}]`) lists the accounts a system fixes itself and no install section can express (Redox: `user` with no password and `root`/`password`; SerenityOS: `anon`/`foo`): the dashboard and the Textual TUI show them in a *Login* row; without it they show the user the profile creates. |

## Guest agent

Set `"guest_agent": true` in a profile or its `local.json` override, then restart
the VM to attach the channel. The guest must have `qemu-guest-agent` installed
and running; Windows also needs the virtio serial driver. Enabling the channel
does not install guest software, with one exception: on Windows 7 vmctl injects
`vioserial` in WinPE and installs a compatible agent as SYSTEM from
`SetupComplete.cmd`. Specialize only stages the script and MSI, and always exits
0. First logon checks the MSI result and service state before reporting success.
Windows 10/11 get both from `virtio-win-guest-tools.exe` at first logon.
The socket is `runtime/qga.sock` beside the disk.

Windows 7 with `guest_agent: true` also requires
`windows_config.guest_agent_msi`, an object containing `path` (host cache), `url`
and `sha256`. The tracked profile pins
[`qemu-ga-win-101.1.0-1.el7ev` (2020)](https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/archive-qemu-ga/qemu-ga-win-101.1.0-1.el7ev/).
The host validates the cached/downloaded MSI and puts it on the seed CD; the guest
needs no network download. The SHA-256 in the profile was measured from that
archive's HTTPS download (2,228,736 bytes), rather than a vendor checksum file.
This package is separate from `virtio_iso`: the agent 110.0.2 on virtio-win 0.1.285
cannot load on Windows 7 because `api-ms-win-core-path-l1-1-0.dll` is absent.
The existing `vioserial` driver and raw child port worked in the live RTM test.
See the [Windows guide](guides/en/60-windows.md) for logs and verification.

```sh
bin/vmctl agent <vm>           # OS information and addresses
bin/vmctl agent <vm> ping      # Check whether the service answers
bin/vmctl agent <vm> ip        # IPv4/IPv6 addresses, excluding loopback
bin/vmctl agent <vm> shutdown  # Submit a guest power-off request
```

These commands work without SSH. Older agents that lack OS information can
still report addresses. Requests have a five-second overall timeout.
`shutdown` reports submission, not confirmed power-off: the
[QEMU agent protocol](https://www.qemu.org/docs/master/interop/qemu-ga-ref.html#command-guest-shutdown)
does not send a success response for this asynchronous command.
`vmctl stop <vm>` tries the agent first when enabled, waits for QEMU to exit,
then uses the existing ACPI/SSH/signal fallback if needed.
| `ci` | Boot-check parameters: `accel`, `headless`, `boot_from`, `expect`, `timeout_sec` |

Profiles that add `cloud_init`, `ssh_provision`, `autoinstall`, `archinstall_config`,
`preseed_config`, `kickstart_config` (with an optional `ostree` block for Silverblue),
`autoyast_config` or `omarchy_config` unlock the unattended and
provisioning flows described in [UNATTENDED.md](UNATTENDED.md) and
[PROVISIONING.md](PROVISIONING.md).

`ssh_provision.verify_after_reboot` holds commands that describe the finished system:
vmctl reboots the guest, waits for SSH again and runs them last. Use it whenever the
desktop is installed by the post-install itself (the Arch, CachyOS and Fedora niri
recipes): greetd is enabled and restarted while the install is still running, the initial
session does not survive that, and tty1 falls back to a text login even though the same
disk brings up the desktop on the next boot.

`network_lab` (role `pfsense` with the LAN topology, or `pihole`/`client` with
`gateway_vm` and `ip`) and `pfsense_config` (`username`, `password`, `timezone`
of the router; the account is created by the installer, `admin` gets the same
password) build the network lab described in [NETWORK-LAB.md](NETWORK-LAB.md).

`acpi_poweroff_grace_sec` (optional, default 60) is how long `vmctl stop` waits
for the guest to honour the ACPI power-off before falling back to SSH and
SIGTERM; the Windows profiles set 300 because their first shutdown commits
pending feature operations.

## ISO sources

`vmctl fetch-iso` downloads to a temporary `.part` file and atomically replaces
the final ISO only after the download passes validation. If `Content-Length` is
available, truncated downloads are rejected. Cached ISOs can be validated with
`iso_size` and `iso_sha256`; invalid cached files are removed and downloaded again.

Profiles can define smarter sources without giving up a hardcoded fallback:

- `iso_discovery` reads a release index and extracts candidate ISO URLs with a
  regular expression, so a profile can follow "latest" without edits;
- `iso_urls` lists additional mirrors to try in order;
- `iso_url` remains the final fallback and keeps older profiles working;
- `iso_archive` says the download is an archive holding the ISO: `{"type": "zip", "member":
  "ReactOS-0.4.16-i386.iso"}` or `{"type": "gzip", "sha256": "<hash of the .gz>"}` (pfSense's
  `.iso.gz`). `vmctl` checks the archive's `sha256` when given (for vendors who publish that one),
  extracts the ISO, validates it against `iso_sha256` and deletes the archive;
- `iso_help` is for a medium `vmctl` cannot download at all (Windows, whose links expire; retro
  media and keys you own; pearOS's signed links): what to get, from where, and what to set in
  `local.json`. `vmctl fetch-iso`, every bootstrap and the dashboard ("ISO needed", *Get the ISO…*)
  show it instead of failing later. A tracked profile needs a download source or `iso_help`
  (a test enforces it);
- `disk_image` replaces `iso` for a system that is built, not installed (SerenityOS publishes no
  ISO): `{"path": "isos/serenityos-grub-<commit>.img", "format": "raw", "help": "how to build it"}`.
  `vmctl prep <vm>` converts the image into the profile's disk (origin `image` in `state.json`) and
  `vmctl start <vm>` boots it; `install`/`provision` refuse, and a missing image shows `help` like
  `iso_help`. The image is never validated as an ISO (that check deletes a file it does not
  recognise). `tools/build_serenityos.sh` is the recipe for a disposable builder VM, and
  [SERENITYOS.md](SERENITYOS.md) the whole procedure as it was done, traps included.

```json
"iso_discovery": {
  "index_url": "https://example.invalid/releases/latest/",
  "pattern": "href=\"(?P<url>example-[0-9.]+-x86_64\\.iso)\"",
  "sort": "desc",
  "limit": 1
},
"iso_urls": [
  "https://mirror1.example.invalid/example.iso",
  "https://mirror2.example.invalid/example.iso"
],
"iso_url": "https://example.invalid/hardcoded-fallback.iso"
```

When the index lists one directory per release instead of ISO files (CachyOS),
capture the release token and let `url_template` build the URL:

```json
"iso_discovery": {
  "index_url": "https://mirror.cachyos.org/ISO/desktop/",
  "pattern": "href=\"(\\d{6})/\"",
  "url_template": "https://mirror.cachyos.org/ISO/desktop/{match}/cachyos-desktop-linux-{match}.iso",
  "sort": "desc",
  "limit": 1
}
```

Give such profiles a stable cached name (`isos/cachyos-desktop-linux-latest.iso`)
so a new release replaces the old file instead of piling up next to it.

Import-oriented profiles may omit every ISO source on purpose: they exist for
`vmctl import-device`, where an existing physical installation becomes the VM
disk instead of booting an installer.

## Disk

| Key | Values |
|-----|--------|
| `path` | Relative to the repository root, conventionally `artifacts/<vm>/disk.qcow2` |
| `size` | `qemu-img` size string, e.g. `32G` |
| `format` | `qcow2` (default) or `vhd` for disks meant to travel to Ventoy or another hypervisor |
| `interface` | `virtio` for Linux guests, `sata` (AHCI) when the guest has no virtio driver at first boot (Windows), `ide` (PATA on the `pc` machine's own controller) for guests older than both, e.g. Ubuntu 8.04 or ReactOS |

`vmctl prep` creates the disk without booting anything; `vmctl clean` stops the
VM first and then removes it together with the other artifacts.

## Firmware

### EFI

For `efi` profiles, `vmctl`:

- prefers the `code` and `vars_template` paths from the profile;
- falls back to common OVMF locations if the configured paths are missing;
- accepts `OVMF_CODE` and `OVMF_VARS_TEMPLATE` environment overrides;
- uses the code file read-only and creates a per-VM copy of the vars file at
  `vars_path`;
- starts QEMU with two pflash drives.

The `/usr/share/OVMF/OVMF_*_4M.fd` paths used by the tracked profiles are safe
defaults; `vmctl setup` tells you if your host ships a different OVMF layout.

### BIOS

For `bios` profiles, `vmctl` does not use OVMF, creates no NVRAM file and uses
the standard SeaBIOS boot flow.

## Video profiles

`video.variants` maps a name to the QEMU arguments used for display. `default`
is what `vmctl start` uses; `installer_order` lists the variants to try in
order when booting an installer. Pick one explicitly with `--video <name>`.

| Variant | Typical arguments | Use it for |
|---------|-------------------|------------|
| `std` | `-vga std -display gtk` | Plain default |
| `safe` | `-vga std -display gtk`, plus serial output where the profile adds it | Debugging a guest that does not come up |
| `virtio-gl` | `-device virtio-vga-gl -display gtk,gl=on` | Modern Wayland compositors (niri, Hyprland) |

Some compositors behave poorly inside a VM even when the guest boots correctly;
`virtio-gl` is the variant to try first, `safe` the one to fall back to.

The TUI remembers the variant chosen for each VM under `~/.local/state/vmtui/`.

## Artifacts

Each VM keeps its state under `artifacts/<vm>/`, so profiles never collide:

```text
artifacts/<vm>/
├── disk.qcow2 (or .vhd)
├── OVMF_VARS.fd
├── installer/          extracted kernel/initrd for unattended installs
├── autoinstall/        Ubuntu autoinstall seed
├── cloud-init/         cloud-init seed (user-data, meta-data, seed.iso)
├── archinstall/        Arch config ISO / bootstrap script
├── preseed/ kickstart/ omarchy/   the other unattended seeds
├── ssh/                generated key pair when the profile asks for one
├── logs/               install, post-install and boot-check logs
├── runtime/            PID files, QMP and VNC sockets of background VMs
└── state.json          what the flows recorded about this disk (see below)
```

`vmctl status` reports these together with runtime state (tracked background
QEMU processes and SSH forward ports). `vmctl clean-stale` removes dead PID files.

### Profile versions

Every tracked profile carries `meta.version` (`MAJOR.MINOR.PATCH`) and `vms/profiles.lock`
remembers, per profile, that version, a fingerprint of the recipe and the history of bumps with
a note each. The fingerprint is what an install depends on: the entry without `name`, `meta`
and help texts, plus the bytes of every `vms/profile-files/` file it copies into the guest.
`make check` compares catalog and lock (`tools/bump_profile.py --check`): a recipe changed
without a bump fails and names the profile, while a new `meta.verified` date or a reworded
description never needs one.

```bash
tools/bump_profile.py ubuntu-26.04 patch -m "gdm autologin drop-in"   # fix: reinstalling is optional
tools/bump_profile.py ubuntu-26.04 minor -m "shared folder"           # something more
tools/bump_profile.py ubuntu-26.04 major -m "user and SSH port"       # an installed VM is no longer comparable
tools/bump_profile.py --history ubuntu-26.04
tools/bump_profile.py --init        # a new profile: 1.0.0 and its lock entry
```

The whole catalog, with versions and history, is browsable at
[manzolo.github.io/qemu-iso-lab](https://manzolo.github.io/qemu-iso-lab/) (`make site` builds
the same page locally; GitHub Pages rebuilds it on every push to `main`).

A new release of a distribution is a new profile, not a major bump. Every install records the
catalog version it used in `state.json` (`install.profile_version`, with `vmctl_version`), and
`vmctl status`, `vmctl list` (`VERSION`), the TUI and the web panel say when the disk carries an
older recipe than the catalog: a reason to reinstall, not a fault. Clones and local-only
profiles are not versioned (`meta.version` is dropped from a clone).

### What is known about the disk (`state.json`, `vmctl status`, the TUI)

Three facts are kept apart because they come from three different places:

| Fact | Source | Shown as |
|------|--------|----------|
| the image occupies N bytes on the host and has a capacity of M | measured (allocated blocks, `qemu-img info`) | `ON HOST` / `CAPACITY` |
| the disk has data on it | measured: more than 16 MiB allocated (a fresh qcow2 has ~200 KiB) | `empty` vs. everything below |
| an installation completed on it | recorded by the unattended flow when the guest's completion token arrived | `installed` |
| the installed disk booted and passed a check | recorded when the SSH post-install ran through or a disk `boot-check` passed | `verified` |

Neither size is the guest filesystem's used or free space. A disk with data and no
record is `unverified` (a pre-existing install, an installer booted by hand, an
imported device); an unattended install that started and never sent its token is
`incomplete`. `meta.verified` in the profile is the maintainer's last live PASS of the
*recipe* and never certifies the disk in `artifacts/` today.

The record is invalidated by the operation that changes the disk: every install flow
resets it when it starts (`vmctl install`/`provision` too: booting the ISO over a
verified disk makes it `unverified` until the next check), `vmctl clean` deletes it
with the disk, `import-device` clears it and notes the source device, a checkpoint
restore brings back the record saved with the checkpoint, a clone carries the
origin's record. `check-vms --restore` moves the whole directory aside and back, so
the record travels with its disk. A record over an image without data (disk deleted
by hand, then `vmctl prep`) is ignored.

## Windows import templates

`windows-10-installer` and `windows-11-installer` are conservative import targets
for `vmctl import-device`:

- both use `q35` and EFI;
- both default to a `sata` disk to avoid an immediate virtio storage driver
  dependency on first boot;
- both use `e1000e` networking for out-of-the-box compatibility;
- native Windows 11 requirements such as TPM and Secure Boot are not modeled.

`windows-11` and `windows-10` are the installable counterparts: a `virtio` disk (the
storage driver is injected during Setup), `virtio-net-pci`, and a
`windows_config` section driving `vmctl bootstrap-windows` (`driver_flavor` `w11`/`w10`, no requirement bypass on 10)
([UNATTENDED.md](UNATTENDED.md#windows-1011-autounattend)). Its `iso` has no
download URL: drop the Microsoft ISO at `isos/windows11.iso` or point `iso` at
your copy in `local.json`, together with your `edition`/`language` (multi-edition ISOs carry several images, `edition` must name one of them).

## Adding a new VM

1. Copy the ISO under `isos/`, or define `iso_url` (and ideally
   `iso_discovery` so the profile follows new releases).
2. Add a VM object to the family file in `vms/profiles/`, or create a new file,
   then `tools/bump_profile.py --init` (1.0.0 and its lock entry; a test fails otherwise).
   A sibling of an existing profile (another Ubuntu flavour, another Debian desktop) should
   `extend` the family's base and carry only its delta: see *Profile bases* below.
   Use the generic guest user `lab` and the `{{user}}` placeholder; never a
   real name, password or hash (the repository is public).
3. Choose disk format, firmware type and runtime settings.
4. Try it:

```bash
vmctl show <name>            # the resolved profile
vmctl --dry-run provision <name>
vmctl provision <name>
```

To make it install unattended, add the matching config section and read
[UNATTENDED.md](UNATTENDED.md). To provision it over SSH afterwards, add
`ssh_provision` and read [PROVISIONING.md](PROVISIONING.md). A test in
`tests/test_repo_profiles.py` loads the whole tracked catalog, so `make check`
catches a broken profile.

## Profile bases: write the shared recipe once

Ten Ubuntu flavours differ in a metapackage, a display-manager drop-in, a hostname and a port;
everything else (the autoinstall seed, sudoers, the serial getty, the video card, the ISO) is the
same recipe. A profile file may carry, next to `vms`, a `bases` object, and a profile names the
base it is built on with `extends`:

```json
{
  "bases": {
    "ubuntu-24.04-base": {
      "extends": "ubuntu-desktop-base",
      "iso": "isos/ubuntu-24.04.4-live-server-amd64.iso",
      "iso_url": "https://releases.ubuntu.com/24.04.4/ubuntu-24.04.4-live-server-amd64.iso",
      "iso_sha256": "e907d92e…"
    }
  },
  "vms": {
    "kubuntu-24.04": {
      "name": "Kubuntu 24.04 LTS (KDE Plasma)",
      "extends": "ubuntu-24.04-base",
      "meta": {"slug": "kubuntu"},
      "disk": {"size": "30G"},
      "autoinstall": {"hostname": "kubuntu", "packages": ["kubuntu-desktop", "spice-vdagent", "qemu-guest-agent"]},
      "cloud_init": {"hostname": "kubuntu", "write_files": [{"path": "/etc/sddm.conf.d/vmctl-autologin.conf", "...": "..."}]},
      "ssh_provision": {"hostname": "kubuntu", "ssh_host_port": 2241, "post_install_run": ["..."]}
    }
  }
}
```

- A base is not a VM: it is never listed, installed or versioned on its own, and it needs no
  `iso`, disk or port; `meta.version` and `meta.verified` are refused in a base, because a
  bump and a live PASS are earned per profile. A base may extend another base (`ubuntu-desktop-base` holds what every
  autoinstall desktop shares, `ubuntu-24.04-base` adds the 24.04 medium, the flavours add the
  desktop; `ubuntu-20.04`, `ubuntu-22.04` and `ubuntu-26.04` extend `ubuntu-desktop-base`
  directly with their own medium). Bases are global: any tracked file may define one, every
  file may extend it, and a name used twice (or used for both a base and a VM) is an error.
- The merge is the one of `local.json`: objects merge, lists **append** (the base's
  `write_files` and `runcmd` come first, the child's follow), scalars are replaced by the
  child. A child cannot remove what the base put in a list, so a base holds only what every
  child really shares.
- `{{name}}` in any string becomes the profile's own key, so the base can say
  `artifacts/{{name}}/disk.qcow2` and `artifacts/{{name}}/OVMF_VARS.fd`. It works in every
  profile, with or without a base.
- Everything downstream sees the resolved profile: `vmctl show`, the flows, the dashboards, the
  web editor (its *Catalog template* is the resolved one) and the catalog site, whose card says
  `on <base>`. A local override in `local.json` applies on top of the resolved profile, and a
  wholly local profile may extend a tracked base.
- The bases in the catalog today (`grep -l '"bases"' vms/profiles/*.json`): `ubuntu-desktop-base`
  → `ubuntu-24.04-base` (ten flavours; the 20.04/22.04/26.04 releases sit on the first),
  `ubuntu-preseed-base` → `ubuntu-preseed-old-base` (8.04-12.04) and `ubuntu-preseed-new-base`
  (14.04-18.04), `ubuntu-desktop-iso-base` (the manual desktop ISOs), `debian-preseed-base` →
  `debian-desktop-base`, `fedora-kickstart-base`, `archinstall-base` → `arch-base` and
  `cachyos-base`, `proxmox-node-base`, `windows-base`, `rhel-server-base`.
  `tools/extract_profile_base.py <file> <base> <vm>... [--parent BASE] --write` carves a base out
  of existing profiles (the shared part, lists by common prefix) and refuses to write unless every
  profile resolves exactly as before.
- **Versions**: the fingerprint is the resolved recipe, so editing a base changes the recipe of
  every profile built on it and `make check` names each of them; bump them together:
  `tools/bump_profile.py ubuntu-desktop-base patch --children -m "..."`. A base nothing
  extends is reported by `--check`. The migration of 2026-09-28 left every resolved profile
  byte-identical (the lock did not change), so no profile needed a bump or a new live run.

## Canonical names and compatibility

Profile names follow `<distro>[-<version>][-<variant>]`; a version distinguishes coexisting versions. The exact legacy aliases live in `vmctl/config.py`. Old CLI names still work with a one-line warning, including the unchanged CI workflow references. `local.json` keys are canonicalized before merging; defining both aliases of one profile in that file is rejected to avoid ambiguous overrides.

Stop affected VMs before migrating installed disks. From the renamed checkout:

```bash
python3 tools/migrate_profile_names.py --root /path/to/checkout
python3 tools/migrate_profile_names.py --root /path/to/checkout --apply --local-config /path/to/checkout/vms/profiles/local.json
```

The script never merges or replaces artifact directories. Repeating it after success is harmless. It saves a private backup before editing local overrides. Do not commit local overrides or backups. Switch the checkout that owns the artifacts to the renamed catalog before resuming VM commands.
