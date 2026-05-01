# Architecture

QEMU ISO Lab is a layered tool for declaratively running QEMU virtual machines.
A thin shell frontend (`Makefile`) delegates to a Python orchestrator
(`bin/vmctl`) which reads JSON profiles and produces isolated per-VM artifacts.

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
     │  (thin   │ │ (curses-  │       │ runs  │
     │ frontend)│ │  style    │       │       │
     └────┬─────┘ │ wrapper)  │       │       │
          │       └────┬──────┘       │       │
          │            │              │       │
          │  ./bin/vmctl <subcmd>     │       │
          └────────────┼──────────────┘       │
                       ▼                      ▼
              ┌────────────────────────────────────┐
              │            bin/vmctl               │
              │  (engine: argparse + qemu builder) │
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

| Component                         | Responsibility                                                        |
|-----------------------------------|-----------------------------------------------------------------------|
| `Makefile`                        | One-line targets that forward to `vmctl` (e.g. `make install VM=...`) |
| `bin/vmctl`                       | Loads profiles, downloads/validates ISOs, builds and runs QEMU commands, manages artifacts |
| `bin/vmtui`                       | Dialog-based menu wrapper over `vmctl`; also handles remote SPICE hosts via `vms/remotes.json` |
| `bin/ventoy-prep`, `ventoy-copy`  | Off-flow helpers for Ventoy USB scenarios                              |
| `vms/profiles/*.json`             | Source of truth for VM definitions (`local.json` is git-ignored override) |
| `isos/`                           | ISO cache, populated by `vmctl fetch-iso` (git-ignored)                |
| `artifacts/<vm>/`                 | Per-VM state: disk, EFI vars, cloud-init seed, logs, runtime sockets (git-ignored) |
| `tests/`                          | Python `unittest` suite for `vmctl` and `vmtui`                        |
| `docs/`                           | Architectural notes (this file, `CI_BOOT_STRATEGY.md`)                 |
| `legacy/`                         | Frozen CachyOS bash prototypes, kept for reference, not used           |

## Typical flows

**Install a new VM from scratch:**

1. `make fetch-iso VM=foo` — `vmctl` validates and caches the ISO under `isos/`.
2. `make prep VM=foo` — `vmctl` creates `artifacts/foo/disk.qcow2` and a per-VM EFI vars copy.
3. `make install VM=foo` — `vmctl` boots QEMU with the ISO and the disk attached.
4. `make start VM=foo` (after install completes) — boots the installed disk only.

**Unattended install:** `make install-unattended VM=foo` builds a cloud-init or
autoinstall seed under `artifacts/<vm>/cloud-init/` and boots the installer
with it attached.

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
