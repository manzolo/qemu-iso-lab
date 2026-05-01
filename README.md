# QEMU ISO Lab

`QEMU ISO Lab` is a small local toolkit for managing test virtual machines from JSON-defined profiles.

It provides a reusable catalog of guest definitions for ISO-based installs, imports from physical disks, boot checks, and QEMU experiments.

Additional notes live in [docs/](docs/), including [CI_BOOT_STRATEGY.md](docs/CI_BOOT_STRATEGY.md).

## Overview

The project currently provides:

- a modular VM catalog under `vms/profiles/*.json`;
- a Python CLI in `bin/vmctl`;
- a minimal text UI in `bin/vmtui`;
- a thin `Makefile` frontend, including a host `setup` check;
- support for both `efi` and `bios` guests;
- isolated per-VM artifacts under `artifacts/<vm>/`;
- a lightweight CI smoke test based on `alpine-ci`.

Current profiles include desktop guests, installer/minimal guests, Windows import templates, and the `alpine-ci` smoke-test guest.

## Project Layout

```text
.
├── Makefile
├── README.md
├── vms/
│   └── profiles/
├── bin/
│   ├── vmctl
│   ├── vmtui
│   ├── ventoy-prep
│   └── ventoy-copy
├── docs/
│   └── CI_BOOT_STRATEGY.md
├── isos/
├── artifacts/
├── legacy/
└── tests/
```

`legacy/` holds the original CachyOS bash prototypes (`setup-vhd.sh`,
`run-install.sh`, `run-boot.sh`) kept for reference. Their behavior is now
covered by `vmctl prep`, `vmctl install`, and `vmctl start`.

## Requirements

Minimum host requirements:

- `qemu-system-x86_64`
- `qemu-img`
- Python 3

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

### Local Guest Flow

Use this path for a normal local guest:

```bash
make setup
make list
make show VM=<name>
make prep VM=<name>
make install VM=<name>
```

After the guest has been installed to disk:

```bash
make start VM=<name>
```

### Unattended Local Guest Flow

Use this path for local profiles that define `autoinstall` and SSH provisioning:

```bash
make prep VM=<name>
make install-unattended VM=<name>
make post-install VM=<name>
```

Or run the full flow in one step:

```bash
make bootstrap-unattended VM=<name>
```

After the first boot, you can open a shell with:

```bash
make shell VM=<name>
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

The TUI is a thin frontend over `vmctl`. It groups VM actions into:

- `Start Here` for the recommended flows, including guided install, full bootstrap, desktop boot, headless boot, and SSH console when available;
- `Installation` for ISO download, disk preparation, manual install, autoinstall, and cloud-init installer flows;
- `Run & Access` for desktop/headless boot, Remote SPICE access, stopping background VMs, SSH access, first boot, and post-install tasks;
- `Maintenance` for boot checks, VM artifact cleanup, and cached ISO removal;
- `Advanced` for physical disk flash/import workflows.

Installer and boot actions can choose a video profile before starting QEMU.

For remote graphical access, create a local remote host config:

```bash
cp vms/remotes.json.example vms/remotes.json
```

Edit `vms/remotes.json` with the SSH target, remote project path, and SPICE ports. The TUI can also create and edit this file from `Remote Hosts`.

Then use `Choose VM` -> `Run & Access` -> `Remote SPICE`. The TUI starts QEMU on the remote host with `--spice-port`, opens an SSH tunnel, and launches `remote-viewer` locally. If `remote-viewer` is missing, the TUI offers to install `virt-viewer` with the detected package manager.

## Common Commands

With `make`:

```bash
make setup
make list
make status
make show VM=<name>
make fetch-iso VM=<name>
make prep VM=<name>
make install VM=<name>
make install-unattended VM=<name>
make post-install VM=<name>
make bootstrap-unattended VM=<name>
make start VM=<name>
make start VM=<name> VIDEO=safe
make shell VM=<name>
make boot-check VM=alpine-ci
make clean VM=<name>
make clean-all
```

With `vmctl` directly:

```bash
./bin/vmctl setup
./bin/vmctl list
./bin/vmctl status
./bin/vmctl show <name>
./bin/vmctl fetch-iso <name>
./bin/vmctl prep <name>
./bin/vmctl install <name>
./bin/vmctl install-unattended <name>
./bin/vmctl post-install <name>
./bin/vmctl bootstrap-unattended <name>
./bin/vmctl start <name>
./bin/vmctl start <name> --video safe
./bin/vmctl shell <name>
./bin/vmctl boot-check alpine-ci
./bin/vmctl clean <name>
./bin/vmctl clean --all
```

Dry-run examples:

```bash
./bin/vmctl --dry-run prep <name>
./bin/vmctl --dry-run install <name>
./bin/vmctl --dry-run start <name> --video safe
```

## VM Profile Model

Each VM entry in `vms/profiles/*.json` typically defines:

- `name`
- `iso`
- `iso_url`
- `iso_urls`
- `iso_discovery`
- `iso_size`
- `iso_sha256`
- `disk`
- `firmware`
- `machine`
- `memory_mb`
- `cpus`
- `network`
- `audio`
- `video`

`fetch-iso` downloads to a temporary `.part` file and atomically replaces the final ISO only after the download passes basic validation. If `Content-Length` is available, truncated downloads are rejected. Existing cached ISOs can also be validated with `iso_size` and `iso_sha256`; invalid cached files are removed and downloaded again.

Profiles can define smarter ISO sources without giving up a hardcoded fallback:

- `iso_discovery` reads a release index and extracts candidate ISO URLs with a regular expression;
- `iso_urls` lists additional mirrors to try in order;
- `iso_url` remains the final fallback and keeps older profiles working.

Example discovery:

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

Import-oriented profiles may omit every ISO download source on purpose. These are intended for flows such as `import-device`, where you bring an existing physical installation into a VM disk rather than booting a distro installer ISO.

Profiles that define `cloud_init`, `ssh_provision`, or `autoinstall` can also support higher-level flows such as unattended install, SSH post-install provisioning, and interactive shell access.

`status` reports basic runtime state in addition to artifact state, including tracked background QEMU processes and SSH forward ports when available.

`clean` is intentionally conservative: it now attempts to stop the VM first, then removes generated artifacts.

The repository now includes `windows10-template` and `windows11-template` as conservative import targets:

- both use `q35` + EFI;
- both default to a `sata` disk to avoid an immediate virtio storage driver dependency on first boot;
- both use `e1000e` networking for broader out-of-the-box Windows compatibility;
- `windows11-template` is usable for imported guests, but native Windows 11 requirements such as TPM/Secure Boot are not yet modeled by `vmctl`.

Example:

```json
{
  "my-vm": {
    "name": "My VM",
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
    "cpus": 4
  }
}
```

## Firmware Modes

### EFI

For `efi` profiles, `vmctl`:

- prefers the `code` and `vars_template` paths from the profile definition;
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
artifacts/my-vm/
├── disk.qcow2
├── OVMF_VARS.fd
├── logs/
└── runtime/
```

This avoids collisions between different guest profiles.

## Video Profiles

Video variants depend on the profile. Common examples include:

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
2. Add a new VM object under one of the files in `vms/profiles/`.
3. Choose disk format, firmware type, and runtime settings.
4. Prepare and boot it:

```bash
make prep VM=<name>
make install VM=<name>
```

## Cloud-Init And Post-Install

`vmctl` can also attach a generated `cloud-init` seed ISO from the VM profile and then finish guest setup over SSH.

Supported profile fields:

- `cloud_init.hostname`
- `cloud_init.user`
- `cloud_init.ssh_authorized_keys`
- `cloud_init.ssh_authorized_keys_file`
- `cloud_init.ssh_key`
- `cloud_init.ssh_host_port`
- `cloud_init.packages`
- `cloud_init.runcmd`
- `cloud_init.copy_from_host`
- `cloud_init.post_install_run`
- `autoinstall.hostname`
- `autoinstall.username`
- `autoinstall.realname`
- `autoinstall.password_hash`
- `autoinstall.timezone`
- `autoinstall.keyboard_layout`
- `autoinstall.storage_layout`
- `autoinstall.install_ssh`

Typical usage:

```bash
./bin/vmctl start ubuntu-niri --cloud-init
./bin/vmctl post-install ubuntu-niri
```

`start --cloud-init` generates `artifacts/<vm>/cloud-init/{user-data,meta-data,seed.iso}` and attaches `seed.iso` to the VM. `post-install` waits for SSH on the forwarded host port defined in the profile, copies any host files listed in `copy_from_host`, and runs the remote commands listed in `post_install_run`.

To automate the Ubuntu Server installer as well:

```bash
./bin/vmctl install-unattended ubuntu-niri-local
./bin/vmctl start ubuntu-niri-local
./bin/vmctl post-install ubuntu-niri-local
```

`install-unattended` generates an `autoinstall` seed, extracts `casper/vmlinuz` and `casper/initrd` from the ISO, and boots the installer with the `autoinstall` kernel argument. The QEMU process exits on the installer's final reboot (`-no-reboot`), after which you can boot the installed system normally and finish with `post-install`.

Commit-safe example:

- `ubuntu-niri` shows a clean `cloud_init` configuration without hardcoded personal usernames or host paths.
- `ubuntu-niri` also shows an `autoinstall` section that should be completed with a real SHA-512 password hash.

Git-ignored local override:

- copy `vms/profiles/local.json.example` to `vms/profiles/local.json`;
- replace `YOUR_USER` and the SSH/dotfile paths with your own values;
- use the `ubuntu-niri-local` profile, which stays only on your host.

Shortcut:

```bash
make init-local-profile
```

## CI Smoke Test

The repository includes a real boot smoke test based on `alpine-ci`.

That profile is intentionally small and CI-friendly:

- it uses Alpine `virt`;
- it boots in headless mode;
- it uses serial-console detection;
- it is designed for GitHub Actions with `tcg` rather than assuming `kvm`.

More detail is documented in [docs/CI_BOOT_STRATEGY.md](docs/CI_BOOT_STRATEGY.md).

## Ventoy Utilities

Two helpers under `bin/` allow reusing a guest disk on a Ventoy USB key,
independently from the main VM workflow:

- `bin/ventoy-prep` — downloads the `vtoyboot` ISO, extracts it and runs
  `vtoyboot.sh` inside a running VM so the guest disk becomes Ventoy-bootable.
  Run as root **inside the guest**.
- `bin/ventoy-copy <target> <file.vhd>` — copies a `.vhd` to a Ventoy
  partition or mountpoint, appending the `.vtoy` suffix required by Ventoy.
  Run as root **on the host**.

These tools are off the main `vmctl` flow and are only relevant for the
Ventoy multi-boot scenario.
