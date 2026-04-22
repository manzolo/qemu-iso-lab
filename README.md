# VM Test Manager

Small local manager for creating, booting, and cleaning QEMU virtual machines from JSON-defined profiles.

The current implementation started as a CachyOS-specific setup, but the direction is broader: a reusable catalog of guests with configurable ISO, disk, firmware, video, and runtime settings.

Per la versione italiana, vedi [README.it.md](README.it.md).

Additional project notes live in [docs/](docs/), including [CI_BOOT_STRATEGY.md](docs/CI_BOOT_STRATEGY.md).

## Current Status

The current V1 already provides:

- a VM catalog in `vms.json`;
- a Python CLI engine in `bin/vmctl`;
- a thin `Makefile` frontend;
- initial support for both `efi` and `bios` firmware flows;
- isolated artifacts for each VM under `artifacts/<vm-name>/`.

The currently configured profile is:

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
│   └── vmctl
├── isos/
│   └── cachyos-desktop-linux-260308.iso
├── artifacts/
│   └── cachyos/
│       ├── disk.vhd
│       ├── OVMF_VARS.fd
│       ├── logs/
│       └── runtime/
└── ...
```

## Core Concepts

### `vms.json`

This file contains the VM profiles.

Each profile defines at least:

- a logical name;
- an ISO path;
- an optional ISO download URL;
- disk settings;
- firmware type;
- memory and CPU allocation;
- video profiles;
- common runtime options.

Example:

```json
{
  "vms": {
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
}
```

### `bin/vmctl`

This is the engine of the project.

Its main responsibilities are:

- loading `vms.json`;
- resolving relative and absolute paths;
- creating local disk and NVRAM artifacts;
- building the QEMU command line;
- executing `prep`, `install`, `start`, and `clean`.

### `Makefile`

The `Makefile` intentionally stays thin. It only provides convenient shortcuts around `bin/vmctl`.

## Requirements

Minimum tools:

- `qemu-system-x86_64`
- `qemu-img`
- Python 3

EFI profiles also require OVMF files, for example:

- `/usr/share/OVMF/OVMF_CODE_4M.fd`
- `/usr/share/OVMF/OVMF_VARS_4M.fd`

## Quick Start

### List VMs

```bash
make list
```

or:

```bash
./bin/vmctl list
```

### Show a profile

```bash
make show VM=cachyos
```

### Prepare disk and NVRAM

```bash
make prep VM=cachyos
```

This step:

- downloads the ISO if it is missing and `iso_url` is configured;
- creates the disk image if missing;
- creates a local EFI vars copy when the firmware is `efi`;
- prepares `logs/` and `runtime/` directories.

### Download the ISO only

```bash
make fetch-iso VM=cachyos
```

If the ISO is already present, no download is performed.

### Run a real headless boot check

```bash
make boot-check VM=alpine-ci
```

This flow is intended for CI. It uses a small Alpine `virt` ISO, boots QEMU without a GUI, watches the serial console, and succeeds only when the guest actually reaches the expected boot string.

### Boot the installer

```bash
make install VM=cachyos
```

### Boot the installed VM

```bash
make start VM=cachyos
```

### Use a different video profile

```bash
make start VM=cachyos VIDEO=safe
make start VM=cachyos VIDEO=virtio-gl
```

### Clean a VM's artifacts

```bash
make clean VM=cachyos
```

### Clean all VMs

```bash
make clean-all
```

## Direct `vmctl` Usage

Available commands:

```bash
./bin/vmctl list
./bin/vmctl show cachyos
./bin/vmctl fetch-iso cachyos
./bin/vmctl prep cachyos
./bin/vmctl install cachyos
./bin/vmctl start cachyos
./bin/vmctl boot-check alpine-ci
./bin/vmctl start cachyos --video safe
./bin/vmctl clean cachyos
./bin/vmctl clean --all
```

To print commands without executing them:

```bash
./bin/vmctl --dry-run prep cachyos
./bin/vmctl --dry-run install cachyos
./bin/vmctl --dry-run start cachyos --video safe
```

## Supported Firmware

### EFI

For `efi` profiles, `vmctl`:

- uses `OVMF_CODE` as read-only firmware;
- creates a local copy of `OVMF_VARS`;
- starts QEMU with pflash drives.

### BIOS

For `bios` profiles, `vmctl`:

- does not use OVMF;
- does not create NVRAM files;
- relies on the classic QEMU/SeaBIOS boot flow.

La V1 ha gia il ramo logico per `bios`, ma manca ancora un profilo reale di esempio da testare.

## Artifacts

Ogni VM ha i suoi artifacts isolati sotto:

```text
artifacts/<nome-vm>/
```

Esempio:

```text
artifacts/cachyos/
├── disk.vhd
├── OVMF_VARS.fd
├── logs/
└── runtime/
```

Questa scelta evita collisioni tra guest diversi.

## Video Profiles

Nel profilo `cachyos` sono presenti:

- `std`
- `safe`
- `virtio-gl`

Uso previsto:

- `std`: default semplice
- `safe`: aggiunge seriale e resta piu utile per debug
- `virtio-gl`: tentativo piu aggressivo per sessioni Wayland/compositor moderni

Nota pratica: alcuni compositor Wayland, come `niri`, possono non essere affidabili dentro la VM anche se il boot del sistema e corretto.

## Come Aggiungere Una Nuova VM

Passi minimi:

1. copiare la ISO sotto `isos/`
2. aggiungere un nuovo oggetto dentro `vms.json`
3. scegliere formato disco e firmware
4. eseguire:

```bash
make prep VM=<nome>
make install VM=<nome>
```

## Esempio Di Profilo BIOS

```json
{
  "debian-bios": {
    "name": "Debian BIOS",
    "iso": "isos/debian.iso",
    "disk": {
      "path": "artifacts/debian-bios/disk.qcow2",
      "size": "20G",
      "format": "qcow2",
      "interface": "virtio"
    },
    "firmware": {
      "type": "bios"
    },
    "machine": "pc",
    "memory_mb": 2048,
    "cpus": 2,
    "network": "user",
    "audio": true,
    "video": {
      "default": "std",
      "variants": {
        "std": ["-vga", "std", "-display", "gtk"]
      }
    }
  }
}
```

## Limiti Attuali

La V1 non copre ancora:

- validazione schema JSON;
- override CLI per RAM/CPU/disco;
- log seriali persistenti;
- integrazione Ventoy nel nuovo flusso;
- hook post-install;
- snapshot e gestione runtime avanzata.

## Roadmap Immediata

Passi sensati dopo questa base:

- aggiungere un secondo profilo reale `bios`;
- spostare gli script legacy fuori dal flusso principale;
- integrare `copy-to-ventoy` e `setup-vtoyboot` nel nuovo modello;
- aggiungere validazione del catalogo;
- migliorare il reporting dei comandi generati.

## File Collegati

- piano di refactor: [VM_MANAGER_PLAN.md](/home/manzolo/Scrivania/Temp/qemu/cachyos/VM_MANAGER_PLAN.md)
- catalogo VM: [vms.json](/home/manzolo/Scrivania/Temp/qemu/cachyos/vms.json)
- CLI: [bin/vmctl](/home/manzolo/Scrivania/Temp/qemu/cachyos/bin/vmctl)
- frontend: [Makefile](/home/manzolo/Scrivania/Temp/qemu/cachyos/Makefile)
