# QEMU ISO Lab

`QEMU ISO Lab` is a small local toolkit for managing test virtual machines from JSON-defined profiles.

It started from a CachyOS-specific setup, but the project is evolving into a reusable catalog of guest definitions for ISO-based installs, boot checks, and QEMU experiments.

For the Italian version, see [README.it.md](README.it.md).

Additional notes live in [docs/](docs/), including [CI_BOOT_STRATEGY.md](docs/CI_BOOT_STRATEGY.md).

## Overview

The project currently provides:

- a VM catalog in `vms.json`;
- a Python CLI in `bin/vmctl`;
- a minimal text UI in `bin/vmtui`;
- a thin `Makefile` frontend, including a host `setup` check;
- support for both `efi` and `bios` guests;
- isolated per-VM artifacts under `artifacts/<vm>/`;
- a lightweight CI smoke test based on `alpine-ci`.

Current example profiles:

- `cachyos`
- `alpine-ci`

## Project Layout

```text
.
├── Makefile
├── README.md
├── README.it.md
├── VM_MANAGER_PLAN.md
├── vms.json
├── bin/
│   ├── vmctl
│   └── vmtui
├── docs/
│   └── CI_BOOT_STRATEGY.md
├── isos/
├── artifacts/
└── tests/
```

## Requirements

Minimum host requirements:

- `qemu-system-x86_64`
- `qemu-img`
- Python 3
- `make`

Optional:

- `dialog` for the TUI frontend;
- OVMF files for EFI guests, for example:
  - `/usr/share/OVMF/OVMF_CODE_4M.fd`
  - `/usr/share/OVMF/OVMF_VARS_4M.fd`

## Installation

Clone the repository:

```bash
git clone https://github.com/manzolo/qemu-iso-lab.git
cd qemu-iso-lab
```

Run the host check first:

```bash
make setup
```

`make` is only required if you want to use the `make ...` shortcuts shown in this README.
If you prefer, you can use `./bin/vmctl ...` directly and skip that dependency.

Install dependencies on Arch-based systems:

```bash
sudo pacman -S qemu-desktop qemu-base edk2-ovmf python dialog make
```

Install dependencies on Debian/Ubuntu:

```bash
sudo apt update
sudo apt install -y qemu-system-x86 qemu-utils ovmf python3 make dialog
```

If you do not want `make`, the minimum practical direct-CLI path is:

```bash
sudo pacman -S qemu-desktop qemu-base edk2-ovmf python dialog
# or
sudo apt install -y qemu-system-x86 qemu-utils ovmf python3 dialog
```

## Quick Start

### Local Desktop Flow

Use this path for a normal local guest such as `cachyos`:

```bash
make setup
make show VM=cachyos
make prep VM=cachyos
make install VM=cachyos
```

After the guest has been installed to disk:

```bash
make start VM=cachyos
```

### Minimal Real Boot Check

Use this path for the smallest real boot smoke test currently in the repo:

```bash
make prep VM=alpine-ci
make boot-check VM=alpine-ci
```

This flow downloads a small Alpine `virt` ISO, prepares the disk, boots QEMU headless, and waits for the serial `login:` prompt.

### Optional TUI

If you prefer a simple terminal UI:

```bash
make tui
```

The TUI is a thin frontend over `vmctl`. It lets you:

- choose a VM profile;
- run `show`, `fetch-iso`, `prep`, `install`, `start`, `boot-check`, `clean`, and `clean-all`;
- choose the video profile for `install` and `start`.

## Common Commands

With `make`:

```bash
make setup
make list
make show VM=cachyos
make fetch-iso VM=cachyos
make prep VM=cachyos
make install VM=cachyos
make start VM=cachyos
make start VM=cachyos VIDEO=safe
make boot-check VM=alpine-ci
make clean VM=cachyos
make clean-all
```

With `vmctl` directly:

```bash
./bin/vmctl setup
./bin/vmctl list
./bin/vmctl show cachyos
./bin/vmctl fetch-iso cachyos
./bin/vmctl prep cachyos
./bin/vmctl install cachyos
./bin/vmctl start cachyos
./bin/vmctl start cachyos --video safe
./bin/vmctl boot-check alpine-ci
./bin/vmctl clean cachyos
./bin/vmctl clean --all
```

Dry-run examples:

```bash
./bin/vmctl --dry-run prep cachyos
./bin/vmctl --dry-run install cachyos
./bin/vmctl --dry-run start cachyos --video safe
```

## VM Profile Model

Each VM entry in `vms.json` typically defines:

- `name`
- `iso`
- `iso_url`
- `disk`
- `firmware`
- `machine`
- `memory_mb`
- `cpus`
- `network`
- `audio`
- `video`

Import-oriented profiles may omit `iso_url` on purpose. These are intended for flows such as `import-device`, where you bring an existing physical installation into a VM disk rather than booting a distro installer ISO.

The repository now includes `windows10-template` and `windows11-template` as conservative import targets:

- both use `q35` + EFI;
- both default to a `sata` disk to avoid an immediate virtio storage driver dependency on first boot;
- both use `e1000e` networking for broader out-of-the-box Windows compatibility;
- `windows11-template` is usable for imported guests, but native Windows 11 requirements such as TPM/Secure Boot are not yet modeled by `vmctl`.

Example:

```json
{
  "cachyos": {
    "name": "CachyOS",
    "iso": "isos/cachyos-desktop-linux-260308.iso",
    "iso_url": "https://iso.cachyos.org/desktop/260308/cachyos-desktop-linux-260308.iso",
    "disk": {
      "path": "artifacts/cachyos/disk.vhd",
      "size": "30G",
      "format": "vpc",
      "subformat": "fixed",
      "interface": "virtio"
    },
    "firmware": {
      "type": "efi",
      "code": "/usr/share/OVMF/OVMF_CODE_4M.fd",
      "vars_template": "/usr/share/OVMF/OVMF_VARS_4M.fd",
      "vars_path": "artifacts/cachyos/OVMF_VARS.fd"
    },
    "machine": "q35",
    "memory_mb": 4096,
    "cpus": 4
  }
}
```

## Firmware Modes

### EFI

For `efi` profiles, `vmctl`:

- prefers the `code` and `vars_template` paths from `vms.json`;
- falls back to common OVMF locations if the configured paths are missing;
- accepts `OVMF_CODE` and `OVMF_VARS_TEMPLATE` environment overrides;
- uses `OVMF_CODE` as read-only firmware;
- creates a local copy of `OVMF_VARS`;
- starts QEMU with pflash drives.

This means entries such as:

```json
"firmware": {
  "type": "efi",
  "code": "/usr/share/OVMF/OVMF_CODE_4M.fd",
  "vars_template": "/usr/share/OVMF/OVMF_VARS_4M.fd",
  "vars_path": "artifacts/ubuntu-desktop/OVMF_VARS.fd"
}
```

are safe as defaults, and `make setup` will tell you if your host needs a different OVMF package layout.

### BIOS

For `bios` profiles, `vmctl`:

- does not use OVMF;
- does not create NVRAM files;
- uses the standard QEMU/SeaBIOS boot flow.

## Artifacts

Each VM stores its local state under:

```text
artifacts/<vm>/
```

Typical contents:

```text
artifacts/cachyos/
├── disk.vhd
├── OVMF_VARS.fd
├── logs/
└── runtime/
```

This avoids collisions between different guest profiles.

## Video Profiles

The `cachyos` profile currently includes:

- `std`
- `safe`
- `virtio-gl`

Typical usage:

- `std`: simple default mode;
- `safe`: adds serial output and is more useful for debugging;
- `virtio-gl`: more aggressive setup for modern Wayland/compositor sessions.

Practical note:

Some Wayland compositors, such as `niri`, may still behave poorly inside a VM even when the guest boots correctly.

## Adding A New VM

Minimal workflow:

1. Copy the ISO under `isos/`, or define `iso_url`.
2. Add a new VM object to `vms.json`.
3. Choose disk format, firmware type, and runtime settings.
4. Prepare and boot it:

```bash
make prep VM=<name>
make install VM=<name>
```

## CI Smoke Test

The repository includes a real boot smoke test based on `alpine-ci`.

That profile is intentionally small and CI-friendly:

- it uses Alpine `virt`;
- it boots in headless mode;
- it uses serial-console detection;
- it is designed for GitHub Actions with `tcg` rather than assuming `kvm`.

More detail is documented in [docs/CI_BOOT_STRATEGY.md](docs/CI_BOOT_STRATEGY.md).
