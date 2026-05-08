# Architecture

QEMU ISO Lab is a layered tool for declaratively running QEMU virtual machines.
A thin shell frontend (`Makefile`) delegates to the Python `vmctl/` package via
a 12-line entry-point shim at `bin/vmctl`. The package reads JSON profiles and
produces isolated per-VM artifacts.

## Block diagram

```text
        ┌─────────────┐                ┌─────────────┐
        │   user      │                │   GitHub    │
        │ (terminal)  │                │   Actions   │
        └──┬───────┬──┘                └──────┬──────┘
           │       │                          │
       make│       │./bin/vmtui               │vmctl boot-check alpine-ci
           ▼       ▼                          │
     ┌──────────┐ ┌──────────┐                │
     │ Makefile │ │ bin/vmtui │◀──────┐       │
     │  (thin   │ │ (dialog-  │       │ runs  │
     │ frontend)│ │  based    │       │       │
     └────┬─────┘ │ wrapper)  │       │       │
          │       └────┬──────┘       │       │
          │            │              │       │
          │  ./bin/vmctl <subcmd>     │       │
          └────────────┼──────────────┘       │
                       ▼                      ▼
              ┌────────────────────────────────────┐
              │   bin/vmctl  (12-line shim)        │
              │   from vmctl.cli import main       │
              └──────────────────┬─────────────────┘
                                 ▼
              ┌────────────────────────────────────┐
              │           vmctl/  (package)        │
              │ ┌────────────────────────────────┐ │
              │ │ cli  →  lifecycle, flash,      │ │
              │ │        import_dev, disk_inspect│ │
              │ │           ↓                    │ │
              │ │ iso · cloud_init · qemu        │ │
              │ │           ↓                    │ │
              │ │ config · runtime · ui          │ │
              │ │           ↓                    │ │
              │ │ errors · state                 │ │
              │ └────────────────────────────────┘ │
              └──┬──────────────┬─────────────┬────┘
                 │ reads        │ fetches     │ writes
                 ▼              ▼             ▼
        ┌────────────────┐ ┌────────┐ ┌─────────────────┐
        │ vms/profiles/  │ │ isos/  │ │ artifacts/<vm>/ │
        │   *.json       │ │        │ │  disk.qcow2|vhd │
        └────────────────┘ └────────┘ │  OVMF_VARS.fd   │
                                      │  cloud-init/    │
                                      │  logs/          │
                                      │  runtime/       │
                                      └────────┬────────┘
                                               │
                                               │ qemu-system-x86_64 ...
                                               ▼
                                        ┌──────────────┐
                                        │  QEMU guest  │
                                        └──────────────┘
```

## What owns what

### Top-level files / dirs

| Component                         | Responsibility                                                        |
|-----------------------------------|-----------------------------------------------------------------------|
| `Makefile`                        | One-line targets that forward to `vmctl` (e.g. `make install VM=...`) |
| `bin/vmctl`                       | 12-line entry-point shim that imports `vmctl.cli.main` from the package |
| `bin/vmtui`                       | Dialog-based menu wrapper over `vmctl`; also handles remote SPICE hosts via `vms/remotes.json` |
| `bin/ventoy-prep`, `ventoy-copy`  | Off-flow helpers for Ventoy USB scenarios                              |
| `vmctl/`                          | The Python package — see module table below                            |
| `vms/profiles/*.json`             | Source of truth for VM definitions (`local.json` is git-ignored override) |
| `isos/`                           | ISO cache, populated by `vmctl fetch-iso` (git-ignored)                |
| `artifacts/<vm>/`                 | Per-VM state: disk, EFI vars, cloud-init seed, logs, runtime sockets (git-ignored) |
| `scripts/verify-split.sh`         | Tests + smoke checks bundle, used by the split refactor                |
| `tests/`                          | Python `unittest` suite for `vmctl` and `vmtui`                        |
| `docs/`                           | Architectural notes (this file, `CI_BOOT_STRATEGY.md`)                 |
| `legacy/`                         | Frozen CachyOS bash prototypes, kept for reference, not used           |

### Modules inside `vmctl/`

| Module                    | Lines | Responsibility                                                  |
|---------------------------|-------|-----------------------------------------------------------------|
| `errors.py`               | ~6    | `VMError`. Imports nothing from the package.                    |
| `state.py`                | ~28   | Mutable globals: `ROOT`, `CONFIG_DIR`, `HTTP_USER_AGENT`, ... |
| `ui.py`                   | ~65   | ANSI codes + print/style helpers.                               |
| `runtime.py`              | ~204  | `run`, `run_progress`, `image_info`, path / format helpers.     |
| `config.py`               | ~108  | `load_config`, `validate_vm_profile`, `get_vm`.                 |
| `iso.py`                  | ~255  | ISO download, validation, discovery, installer extraction.     |
| `cloud_init.py`           | ~192  | cloud-init / autoinstall seed builders.                         |
| `qemu.py`                 | ~244  | QEMU command builders: machine, firmware, disk, video, audio.   |
| `disk_inspect.py`         | ~242  | `wipefs`, `lsblk`, GPT geometry, `cmd_list_*_devices`.          |
| `flash.py`                | ~214  | `cmd_flash`, `cmd_flash_helper`, sudo re-exec target.           |
| `import_dev.py`           | ~187  | `cmd_import_device`, `cmd_import_helper`, sudo re-exec target.  |
| `ssh.py`                  | ~209  | SSH/SCP helpers: target, base cmds, wait, post-install copy/run.|
| `host_setup.py`           | ~85   | OS detection, install hints, `prompt_yes_no`.                   |
| `lifecycle.py`            | ~717  | All other `cmd_*` handlers + background-VM tracking.            |
| `cli.py`                  | ~181  | `build_parser`, `dispatch_internal`, `main`. Wires it together. |

**Import direction**: `errors` ← `state` ← {`ui`, `runtime`} ← `config`/`iso`/`cloud_init`/`qemu`/`disk_inspect` ← {`flash`, `import_dev`, `ssh`, `host_setup`} ← `lifecycle` ← `cli`. No cycles. Mutable state is always accessed via the module (`from vmctl import state` then `state.ROOT`), never as `from vmctl.state import ROOT` (would capture a stale binding).

## Typical flows

**Install a new VM from scratch:**

1. `make fetch-iso VM=foo` — `vmctl` validates and caches the ISO under `isos/`.
2. `make prep VM=foo` — `vmctl` creates `artifacts/foo/disk.qcow2` and a per-VM EFI vars copy.
3. `make install VM=foo` — `vmctl` boots QEMU with the ISO and the disk attached.
4. `make start VM=foo` (after install completes) — boots the installed disk only.

**Unattended install:** `make install-unattended VM=foo` is currently the
Ubuntu autoinstall path. It builds an autoinstall seed under
`artifacts/<vm>/autoinstall/` and boots the installer with it attached.

**Smoke test under CI:** GitHub Actions runs `vmctl boot-check alpine-ci` under
TCG. See [CI_BOOT_STRATEGY.md](CI_BOOT_STRATEGY.md).

## Where things are NOT

- **No daemon.** `vmctl` is one-shot. Running guests are tracked via PID
  and socket files in `artifacts/<vm>/runtime/`.
- **No global state file.** Each VM is fully described by its profile entry
  plus its `artifacts/<vm>/` directory.
- **No central config.** Behavior overrides go through environment variables
  (`OVMF_CODE`, `VTOYBOOT_VERSION`, ...) or `vms/profiles/local.json`.
- **No schema validator yet.** Profile files are validated ad-hoc inside
  `vmctl`; a formal JSON schema may be added later.

## Adding things

- **A new VM:** add an entry to one of `vms/profiles/*.json`, grouped by family.
  Use `vms/profiles/local.json` for personal-only profiles (git-ignored).
- **A new `vmctl` subcommand:** add a parser in `main()` and a handler function.
  Tests go in `tests/test_vmctl.py`.
- **A new TUI screen:** edit `bin/vmtui`, which composes `dialog` menus and
  shells out to `vmctl`.
