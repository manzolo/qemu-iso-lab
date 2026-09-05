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
| `meta` | `family` drives the TUI filter and the catalog grouping; `role` and `release_model` are informational |
| `iso`, `iso_url`, `iso_urls`, `iso_discovery`, `iso_size`, `iso_sha256` | See [ISO sources](#iso-sources) |
| `disk` | See [Disk](#disk) |
| `firmware` | `efi` or `bios`, see [Firmware](#firmware) |
| `machine` | QEMU machine type, `q35` for modern guests, `pc` for old ones |
| `memory_mb`, `cpus` | Guest RAM and vCPUs |
| `network` | `user` (slirp with optional SSH port forward) |
| `audio` | Attach an audio device |
| `usb_tablet` | Absolute pointer for graphical guests |
| `video` | Named QEMU argument sets, see [Video profiles](#video-profiles) |
| `installer_boot` | `kernel` and `initrd` paths inside the ISO for the unattended flows, when they differ from the flow's default (CachyOS: `arch/boot/x86_64/vmlinuz-linux-cachyos`) |
| `notes` | Free text shown by `vmctl show` |
| `ci` | Boot-check parameters: `accel`, `headless`, `boot_from`, `expect`, `timeout_sec` |

Profiles that add `cloud_init`, `ssh_provision`, `autoinstall`, `archinstall_config`,
`preseed_config`, `kickstart_config` or `omarchy_config` unlock the unattended and
provisioning flows described in [UNATTENDED.md](UNATTENDED.md) and
[PROVISIONING.md](PROVISIONING.md).

## ISO sources

`vmctl fetch-iso` downloads to a temporary `.part` file and atomically replaces
the final ISO only after the download passes validation. If `Content-Length` is
available, truncated downloads are rejected. Cached ISOs can be validated with
`iso_size` and `iso_sha256`; invalid cached files are removed and downloaded again.

Profiles can define smarter sources without giving up a hardcoded fallback:

- `iso_discovery` reads a release index and extracts candidate ISO URLs with a
  regular expression, so a profile can follow "latest" without edits;
- `iso_urls` lists additional mirrors to try in order;
- `iso_url` remains the final fallback and keeps older profiles working.

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
| `interface` | `virtio` for Linux guests, `sata` when the guest has no virtio driver at first boot (Windows) |

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
└── runtime/            PID files, QMP and VNC sockets of background VMs
```

`vmctl status` reports these together with runtime state (tracked background
QEMU processes and SSH forward ports). `vmctl clean-stale` removes dead PID files.

## Windows import templates

`windows10-template` and `windows11-template` are conservative import targets
for `vmctl import-device`:

- both use `q35` and EFI;
- both default to a `sata` disk to avoid an immediate virtio storage driver
  dependency on first boot;
- both use `e1000e` networking for out-of-the-box compatibility;
- native Windows 11 requirements such as TPM and Secure Boot are not modeled.

## Adding a new VM

1. Copy the ISO under `isos/`, or define `iso_url` (and ideally
   `iso_discovery` so the profile follows new releases).
2. Add a VM object to the family file in `vms/profiles/`, or create a new file.
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
