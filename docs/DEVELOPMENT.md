# Development

- [Developer loop](#developer-loop)
- [Where things go](#where-things-go)
- [Before pushing](#before-pushing)
- [Test isolation rules](#test-isolation-rules)
- [CI](#ci)

## Developer loop

```bash
make check                                     # mypy --strict + full pytest suite
make test                                      # pytest only
make lint                                      # mypy vmctl/ --strict
make ci                                        # python -m unittest discover -s tests -v (what GitHub Actions runs)
python -m pytest tests/test_archinstall.py -v  # one file
python -m pytest tests/ -k "test_render"       # filter by name
make help                                      # every target
```

The `Makefile` carries developer targets only. User-facing behaviour is a
`vmctl` subcommand, registered in `vmctl/cli.py` inside one of the
`COMMAND_GROUPS`; a test fails if a command is left out of the groups.

## Where things go

[ARCHITECTURE.md](ARCHITECTURE.md) is the one-page map. In short:

- `bin/vmctl` is a shim around `vmctl.cli:main`; `bin/vmtui` is an independent
  bash menu that shells out to `vmctl`.
- Modules import in one direction only: `errors ← state ← {ui, runtime} ←
  {config, iso, cloud_init, qemu, archinstall, ...} ← {flash, import_dev, ssh,
  host_setup} ← lifecycle ← cli`. Mutable globals (`ROOT`, `CONFIG_DIR`) live in
  `state.py` and are always read as `state.ROOT`, never imported directly.
- One module per distro-specific unattended flow (`archinstall.py`,
  `preseed.py`, `kickstart.py`, `omarchy.py`, `cloud_init.py`); the `cmd_*`
  handlers live in `lifecycle.py`.

## Before pushing

GitHub Actions is a confirmation step, not the first feedback loop. Run the
local checks that cover what you changed:

```bash
make check
make ci
vmctl --dry-run bootstrap-preseed debian-server        # when touching unattended flows
vmctl --dry-run bootstrap-kickstart almalinux-server
vmctl --dry-run install-unattended ubuntu-niri
```

When unattended or bootstrap code changes, also run the suite once with the
external tools hidden from `PATH`, to reproduce the bare CI runner locally:

```bash
mkdir -p /tmp/nobin
for p in /usr/bin/*; do case "${p##*/}" in qemu-*|fzf|dialog|xorriso|bsdtar) ;; *) ln -sf "$p" /tmp/nobin/;; esac; done
PATH=/tmp/nobin python -m unittest discover -s tests
```

## Test isolation rules

A test must pass identically on a developer's host (with QEMU, ISOs, installed
VMs, a personal `local.json`) and in the bare CI runner (with none of them).

- **Never resolve paths against the real checkout.** Relative profile paths
  (`isos/`, `artifacts/`, PID files) resolve against `state.ROOT`; tests point
  it at a temp dir. `tests/_common.py` provides `BaseVmctlTestCase` with a
  temp root and a facade over all submodules; `tests/test_vmtui.py` passes a
  temp `VMTUI_ROOT_DIR` with `vmctl/` and `bin/` symlinked from the checkout.
  Disk state is simulated by writing files there (`mark_installed`,
  `mark_prepared`).
- **Never load `vms/profiles/local.json`.** Copy the tracked profiles into a
  temp config dir and skip it.
- **Build subprocess environments from scratch**, not from `os.environ`:
  minimal `PATH`, temp `HOME`, `LC_ALL=C.UTF-8`, explicit `VMTUI_UI=dialog`.
  External tools (`dialog`, `fzf`) are faked as tiny scripts.
- **No external binaries in unit tests.** Mock `vmctl.runtime.run` and
  `shutil.which` by patching the submodule directly
  (`mock.patch.object(vmctl.runtime, "run")`), not through the facade.

## CI

`.github/workflows/ci.yml` runs on every push:

| Job | What it does |
|-----|--------------|
| `test` | `python -m unittest discover -s tests -v` on a bare runner (no QEMU) |
| `boot-smoke` | installs QEMU, `vmctl prep alpine-ci`, `vmctl boot-check alpine-ci` under TCG |
| `ubuntu-server-headless-install-boot` | headless Ubuntu autoinstall boot check |
| `ubuntu-niri-dry-run`, `debian-server-dry-run`, `almalinux-server-dry-run` | `--dry-run` of the full bootstrap flows |

The `alpine-ci` profile is the stable CI guest: keep it small and TCG-capable
([CI_BOOT_STRATEGY.md](CI_BOOT_STRATEGY.md)).
