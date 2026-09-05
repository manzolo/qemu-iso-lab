# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Developer loop (the Makefile has ONLY these kinds of targets: `make help`)
make check                                     # mypy --strict + full pytest suite; run before every push
make test                                      # pytest only
python -m pytest tests/test_archinstall.py -v  # single test file
python -m pytest tests/ -k "test_render"       # filter by name
make ci                                        # python -m unittest discover -s tests -v (what GitHub Actions runs)
make lint                                      # python -m mypy vmctl/ --strict (enforced)
make install-cli                               # symlink vmctl + vmtui into ~/.local/bin

# VM lifecycle: ONE front door, the vmctl CLI (./bin/vmctl if not installed).
vmctl --help                       # commands grouped by task + typical flows: read this first
vmctl <command> --help             # options of one command
vmctl list --names                 # profile names, one per line
vmctl show <name> --json           # resolved profile ({{user}} already expanded)
vmctl --dry-run <command> <name>   # every command supports --dry-run
vmctl bootstrap-archinstall arch-noctalia-local
vmctl bootstrap-preseed debian-server
vmctl bootstrap-kickstart almalinux-server
vmctl bootstrap-alpine alpine-niri
```

Before pushing, run the relevant local tests first. Do not use GitHub Actions as the first place to discover breakage in unit tests, dry-run bootstrap flows, or CI wiring. At minimum, if you touch CI or unattended/bootstrap code, run `python -m unittest discover -s tests -v` and any focused bootstrap/dry-run commands affected by the change.

## Architecture

`bin/vmctl` is a 12-line shim that calls `vmctl.cli:main`; `make install-cli` symlinks it (and `vmtui`) into `~/.local/bin`, and both resolve the repository root through the symlink. The `Makefile` carries developer targets only and must not grow VM-lifecycle targets again: new user-facing behaviour goes into a `vmctl` subcommand, registered in `cli.py` inside one of the `COMMAND_GROUPS` (a test fails otherwise). `bin/vmtui` is an independent menu wrapper (fzf backend, `dialog` fallback, `VMTUI_UI` to force one) that shells out to `vmctl`.

### Module import order (no cycles allowed)

```
errors ← state ← {ui, runtime} ← {config, iso, cloud_init, qemu, archinstall, disk_inspect}
      ← {alpine, preseed, kickstart, omarchy} ← {flash, import_dev, ssh, host_setup} ← lifecycle ← cli
```

Mutable globals (`ROOT`, `CONFIG_DIR`, etc.) live in `state.py` and are always accessed as `state.ROOT`, never imported directly — a direct import captures a stale binding and breaks tests.

### Key modules

| Module | Role |
|---|---|
| `cli.py` | Argument parser + `main()`. Wires subcommands to handlers. |
| `lifecycle.py` | All `cmd_*` handlers not involving flash/import. Background-VM PID tracking. |
| `config.py` | `load_config()` merges all `vms/profiles/*.json`. `get_vm()` resolves a name. |
| `qemu.py` | Builds `qemu-system-x86_64` argument lists. `common_args()` is the central builder. `run_and_expect()` drives headless serial-console automation. |
| `cloud_init.py` | Renders cloud-init `user-data`/`meta-data` and Ubuntu `autoinstall` seed ISOs. |
| `archinstall.py` | Arch-specific: renders archinstall JSON config (interactive) or a self-contained `pacstrap`-based `install.sh` (automated bootstrap). |
| `iso.py` | ISO download with validation, discovery regex, and member extraction (`xorriso`/`bsdtar`). |
| `alpine.py` | Alpine: `setup-alpine` answer file + chroot `install.sh` packed into a seed ISO, live-prompt automation constants. |
| `kickstart.py` / `preseed.py` | AlmaLinux/Fedora kickstart and Debian preseed rendering; `kickstart.install_repo()` picks `cdrom` or a netinst URL. |
| `ssh.py` | SSH/SCP helpers, `wait_for_ssh`, `post_install_copy`, `post_install_run`. |

### Profile model

All VM definitions live in `vms/profiles/*.json`. `load_config()` reads and merges every file in that directory; `vms/profiles/local.json` is gitignored, loaded last, and deep-merged over the tracked profiles (dicts merge, lists concatenate). It is the only place for personal data — copy from `local.json.example`.

Tracked profiles are generic on purpose: the guest user is `lab` (password `lab`, hash included) and every place where the user name appears inside a path, a command or a file body writes `{{user}}`. `config.expand_user_placeholder()` replaces it at load time with the identity declared by the profile (`ssh_provision.user`, `cloud_init.user`, `autoinstall.username`, `archinstall_config.username`, `preseed_config.username`, `kickstart_config.username`; they must agree). Overriding the identity in `local.json` therefore propagates everywhere. Never commit a real user name, password or hash into a tracked profile again; the repo is public.

SSH-provisioned ports in use: `cachyos-local` → 2223, `cachyos-nvidia-local` → 2224, `arch-noctalia-local` → 2226, `arch-dms-local` → 2230, `arch-dms-nvidia-local` → 2231, `arch-omarchy-nvidia-local` → 2232, `fedora-niri-dms-local` → 2233, `alpine-niri` → 2234 (2222/2227/2228/2229/2290 are Ubuntu/Debian/Alma; 2225 is taken by a local.json VM).

### Unattended install flows

**Ubuntu** (`bootstrap-unattended`):
1. Generates cloud-init seed ISO + autoinstall seed.
2. Extracts `casper/vmlinuz` + `casper/initrd` from the ISO, boots with `-append autoinstall -no-reboot`.
3. Waits for installer to exit, then starts the installed VM headless in background.
4. Runs `ssh_provision` / `cloud_init.post_install_run` over SSH.

**Arch** (`bootstrap-archinstall`):
1. Generates a self-contained `install.sh` (sgdisk → pacstrap → arch-chroot → GRUB) packed into a `bootstrap.iso`.
2. Extracts `arch/boot/x86_64/vmlinuz-linux` + `initramfs-linux.img` from the Arch live ISO.
3. Boots headless with serial stdio (`console=ttyS0,115200`) and `archisobasedir=arch archisolabel=ARCH_YYYYMM`.
4. Uses `run_and_expect` + `auto_inputs` to wait for `root@archiso` on the serial console, then sends the mount + run trigger automatically.
5. Waits for `"==> Arch Linux installation complete!"`, then repeats step 3–4 of the Ubuntu flow.

**Debian** (`bootstrap-preseed`):
1. Generates preseed seed ISO (`PRESEED_CFG`).
2. Extracts `vmlinuz` + `initrd.gz` from the ISO.
3. Boots headless with serial stdio and appropriate preseed kernel appends.
4. Uses `run_and_expect` to wait for `"==> Debian preseed install complete!"`, then starts installed VM headless.

**AlmaLinux/RHEL/Fedora** (`bootstrap-kickstart`; `kickstart_config.inst_repo` = `cdrom` or a netinst repository URL, `ignore_missing_packages` → `%packages --ignoremissing`):
1. Generates kickstart seed ISO (`KS_CFG`).
2. Extracts `vmlinuz` + `initrd.img` from the ISO.
3. Boots headless with serial stdio and appropriate kickstart kernel appends.
4. Uses `run_and_expect` to wait for `"==> Kickstart install complete!"`, then starts installed VM headless.

**Alpine** (`bootstrap-alpine`):
1. Generates a `setup-alpine` answer file + `install.sh` (setup-alpine with `ERASE_DISKS`, then chroot: packages, password hash, sudo, `chroot_commands`) into an `ALPINESEED` seed ISO on a virtio CD-ROM.
2. Extracts `boot/vmlinuz-<flavor>` + `boot/initramfs-<flavor>` (`lts` for the standard ISO).
3. Boots with the ISO's `modules=` list plus `console=ttyS0,115200`; `auto_inputs` answer `localhost login:` with `root` and type the mount + run trigger at `localhost:~#`.
4. Waits for `"==> Alpine Linux installation complete!"`, then the usual background start + post-install.

The interactive variant (`install-archinstall`) generates archinstall JSON configs and attaches them as a second virtio CD-ROM (`/dev/vdb`) for the user to run manually.

#### CRITICAL — completion token / ESP flush invariant (do not break)

The Arch unattended bootstrap has a sequencing rule that cost 5+ hours of debugging when violated. This applies to **all** automated unattended flows (Arch, Debian, RHEL) that signal completion via serial. **Treat this as gospel:**

1. In the install script (or `%post`/`late_command`) it MUST end with this exact ordering:
   ```bash
   sync
   blockdev --flushbufs /dev/vda /dev/vda1 /dev/vda2 || true
   echo "==> Arch Linux installation complete!"   # BOOTSTRAP_COMPLETE_TOKEN
   poweroff -f
   ```
   The completion token must appear **after** the sync+flush, never before. If the token is printed first, the host kills QEMU before the guest finishes flushing the ESP, so `EFI/BOOT/BOOTX64.EFI`, the embedded GRUB binary, and the stub `grub.cfg` files end up partially or entirely missing. Symptom: guest drops to `grub rescue>` on first real boot, and an ESP inspection shows only `EFI/GRUB/grubx64.efi` from `grub-install`.

2. In `vmctl/qemu.py::run_and_expect`, when `expected_text` matches, the function MUST call `process.wait(timeout=30)` and let the guest power itself off naturally. Only fall back to `terminate()` → `kill()` if that wait times out. Do NOT replace this with an immediate `terminate()` on token match — that re-introduces the same race.

If a future change appears to need either of these relaxed (e.g. "speed up the bootstrap by killing QEMU sooner", "drop the redundant sync"), the answer is no — investigate the actual problem elsewhere. Any new unattended flow that signals completion via a serial token must follow the same flush → token → natural-poweroff pattern.

### Testing conventions

`tests/_common.py` exports `BaseVmctlTestCase` with a temp-dir root and a `_VmctlFacade` that flattens all vmctl submodules into a single attribute namespace. When adding a new module, add its import to both the `import` block and the `_SEARCH_ORDER` tuple in `_common.py`.

Tests mock `vmctl.runtime.run` (for subprocess calls) and `shutil.which` (for tool detection). Patch the submodule directly (`mock.patch.object(vmctl.runtime, "run")`), not through the facade.

#### Host isolation (a test must pass identically on the developer's host and in the bare CI runner)

The CI `test` job has no QEMU, no `qemu-img`, no `fzf`, no ISOs and no installed VMs; the developer's host has all of them. A test that reads any of that passes only on one side. Rules:

- **Never resolve paths against the real checkout.** Relative profile paths (`isos/`, `artifacts/`, PID files under `artifacts/<vm>/runtime/`) resolve against `state.ROOT`, so tests must point it at a temp dir (`BaseVmctlTestCase` does; `test_vmtui.py` passes a temp `VMTUI_ROOT_DIR` with `vmctl/` and `bin/` symlinked from the checkout). Simulate disk state by writing files there: `mark_installed` (allocated data) / `mark_prepared` (sparse file, no `qemu-img`).
- **Never load `vms/profiles/local.json`.** It is the developer's personal, gitignored file. Copy the tracked profiles into a temp config dir and skip it; a test-specific `local.json` in that temp dir is fine.
- **Build subprocess environments from scratch**, not with `os.environ.copy()`: minimal PATH (temp tools dir, `Path(sys.executable).parent`, `os.defpath`), temp `HOME`, `LC_ALL=C.UTF-8`, explicit `VMTUI_UI=dialog`. Fake external tools (`dialog`, `fzf`) as tiny scripts in the temp tools dir.
- **No external binaries in the `test` job.** Do not call `qemu-img`, `xorriso`, `bsdtar`, `virsh` or `qemu-system-*` in unit tests; mock `vmctl.runtime.run` or fake the artifact.
- Before pushing, run the suite once with those tools hidden from PATH (e.g. a dir of symlinks to `/usr/bin/*` minus `qemu-*`, `fzf`, `dialog`, `xorriso`, `bsdtar`) to reproduce the CI runner locally.

### Artifact layout

```
artifacts/<vm>/
├── disk.qcow2 (or .vhd)
├── OVMF_VARS.fd
├── installer/          # extracted kernel/initrd for unattended install
├── autoinstall/        # Ubuntu autoinstall seed
├── cloud-init/         # cloud-init seed
├── archinstall/        # Arch config ISO / bootstrap script
├── logs/
└── runtime/            # PID files for background VMs
```

### CI

GitHub Actions runs three jobs: `test` (unittest), `boot-smoke` (`boot-check alpine-ci` under TCG/QEMU, no KVM), and `ubuntu-niri-dry-run` (`--dry-run` of the full bootstrap). The `alpine-ci` profile is the stable CI guest — keep it small and TCG-capable.

CI is a confirmation step, not the first feedback loop. If you modify workflow files, bootstrap handlers, or unattended install helpers, make the corresponding local `unittest` and dry-run checks pass before pushing.
