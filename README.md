# QEMU ISO Lab

[![CI](https://github.com/manzolo/qemu-iso-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/manzolo/qemu-iso-lab/actions/workflows/ci.yml)

Test any Linux distro in a QEMU/KVM virtual machine from one JSON profile:
ISO download, disk, firmware, unattended install and SSH provisioning,
driven by a single CLI (`vmctl`) or a dashboard TUI (`vmtui`).

![vmtui dashboard: every profile with live state, RAM/CPU, SSH port and disk size](docs/screenshots/vmtui-dashboard.png)

## Contents

- [Why](#why)
- [Quick start](#quick-start)
- [What do you want to do?](#what-do-you-want-to-do)
- [The catalog](#the-catalog)
- [Unattended installs](#unattended-installs)
- [The TUI](#the-tui)
- [Make it yours](#make-it-yours)
- [Documentation](#documentation)
- [Development](#development)
- [Repository layout](#repository-layout)

## Why

- **One profile, one VM.** Every guest is a JSON object: ISO source (with
  mirror discovery and checksum validation), disk, EFI or BIOS firmware, RAM,
  CPUs, network and video variants. `vmctl show <vm>` prints it resolved.
- **Zero-click installs.** Ubuntu (autoinstall), Debian (preseed), AlmaLinux and
  Fedora (kickstart), Arch (pacstrap script), Omarchy (cidata) and Alpine
  (setup-alpine) install headless on a serial console, boot, and finish with
  SSH provisioning: dotfiles, scripts, extra packages. Ten minutes later
  `vmctl shell <vm>` drops you inside.
- **Isolated and reproducible.** Each VM lives under `artifacts/<vm>/`; ISOs
  are cached once under `isos/`. `vmctl clean <vm>` puts everything back.
  A small Alpine guest boots in GitHub Actions on every push.

## Quick start

Host packages (Arch or Debian/Ubuntu shown):

```bash
sudo pacman -S qemu-desktop qemu-base edk2-ovmf python fzf
sudo apt install -y qemu-system-x86 qemu-utils ovmf python3 fzf
```

Clone, put the two commands on your `PATH`, check the host:

```bash
git clone https://github.com/manzolo/qemu-iso-lab.git
cd qemu-iso-lab
make install-cli          # symlinks vmctl and vmtui into ~/.local/bin
vmctl setup               # verifies qemu, qemu-img, OVMF, KVM
```

Then pick a VM and go:

```bash
vmctl list                          # 44 profiles
vmctl provision debian-netinst      # ISO + disk + installer, click through it
vmctl start debian-netinst          # boot the installed disk

vmctl bootstrap-preseed debian-server   # or: install Debian with zero clicks...
vmctl shell debian-server               # ...and SSH into it
```

Optional tab completion of commands and VM names (zsh shown, `bash` works the same):

```bash
echo 'eval "$(vmctl completion zsh)"' >> ~/.zshrc
```

## What do you want to do?

Everything goes through one command. `vmctl --help` shows the same map grouped
by task, and `vmctl <command> --help` the options of one command. Every command
accepts `--dry-run` in front of it.

| I want to...                                          | Run                                                      |
|-------------------------------------------------------|----------------------------------------------------------|
| check that this host can run the lab                  | `vmctl setup`                                            |
| see which VMs exist and which are installed/running   | `vmctl list`, `vmctl status`                             |
| read one profile as `vmctl` sees it                   | `vmctl show <vm>` (`--json` for scripts)                 |
| install a distro by hand (ISO, disk, installer)       | `vmctl provision <vm>` then click through the installer  |
| install Ubuntu with zero clicks, ready with SSH       | `vmctl bootstrap-unattended <vm>`                        |
| install Omarchy with zero clicks, ready with NVIDIA   | `vmctl bootstrap-omarchy arch-omarchy-nvidia-local`      |
| same for Debian / AlmaLinux / Fedora / Arch / Alpine  | `vmctl bootstrap-preseed`, `bootstrap-kickstart`, `bootstrap-archinstall`, `bootstrap-alpine <vm>` |
| boot a VM I already installed                         | `vmctl start <vm>` (`--headless --background` to detach) |
| get a shell inside it / stop it                       | `vmctl shell <vm>`, `vmctl stop <vm>`                    |
| watch the screen of a headless VM (even mid-bootstrap) | `vmctl attach <vm>` (VNC viewer, `--no-viewer` for the address only) |
| test every unattended flow without losing my VMs      | `vmctl check-vms --restore` (stashes disks, runs, restores) |
| re-run the SSH provisioning steps of a profile        | `vmctl post-install <vm>`                                |
| prove a VM still boots (CI-style)                     | `vmctl boot-check <vm>`, `vmctl check-vms`               |
| write a VM to a real USB disk, or import one          | `vmctl flash`, `vmctl import-device` (destructive, sudo) |
| free disk space                                       | `vmctl clean <vm>`, `vmctl clean --all`, `vmctl delete-iso <vm>` |
| see what a command would do without doing it          | `vmctl --dry-run <command> <vm>`                         |
| add my own user, key and dotfiles to the VMs          | edit `vms/profiles/local.json` ([Make it yours](#make-it-yours)) |
| add a new VM                                          | add a profile to `vms/profiles/*.json` ([docs/PROFILES.md](docs/PROFILES.md#adding-a-new-vm)) |
| hack on `vmctl` itself                                | `make check`, then [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) |

## The catalog

44 tracked profiles in `vms/profiles/*.json`, one file per family. `vmctl list`
prints them all; the table shows what each family offers.

| Family | Profiles | Highlights | Unattended |
|--------|----------|------------|------------|
| Arch | `archlinux`, `endeavouros`, `cachyos`, `cachyos-local`, `cachyos-nvidia-local`, `arch-noctalia-local`, `arch-dms-local`, `arch-dms-nvidia-local`, `arch-omarchy-nvidia-local` | niri + Noctalia, niri + DankMaterialShell, Omarchy + Hyprland, NVIDIA open DKMS recipes | `bootstrap-archinstall`, `bootstrap-omarchy` |
| Debian / Ubuntu | `debian-netinst`, `debian-efi`, `debian-bios`, `debian-gnome-live`, `debian-server`, `ubuntu-desktop`, `ubuntu-server`, `ubuntu-server-headless`, `ubuntu-niri`, `ubuntu-niri-local`, `popos-cosmic`, `kde-neon-user`, `linuxmint-cinnamon` | Debian 13, Ubuntu 26.04, niri on Ubuntu, COSMIC | `bootstrap-preseed`, `bootstrap-unattended` |
| Fedora / RHEL | `fedora-workstation`, `fedora-cinnamon`, `fedora-xfce`, `fedora-server`, `fedora-server-efi`, `fedora-niri-dms-local`, `almalinux-minimal`, `almalinux-server` | Fedora 42/44, niri + DankMaterialShell on Fedora, AlmaLinux 10.1 | `bootstrap-kickstart` |
| openSUSE / NixOS / Void | `opensuse-tumbleweed-kde`, `opensuse-tumbleweed-net`, `opensuse-slowroll`, `nixos-graphical`, `nixos-minimal`, `void-xfce` | rolling and declarative distros | interactive |
| Alpine / BSD / Kali | `alpine-ci`, `alpine-installed-ci`, `alpine-niri`, `freebsd`, `kali-live` | the CI smoke-test guests, niri on Alpine 3.23 (musl, OpenRC, seatd), FreeBSD 14.3 | `bootstrap-alpine` |
| Windows | `windows10-template`, `windows11-template` | import targets for physical disks (`vmctl import-device`) | n/a |

Profiles ending in `-local` are full desktop recipes with SSH provisioning,
meant to be personalised through `local.json`. Profiles named `*-ci` are tiny
guests that boot under TCG in GitHub Actions.

## Unattended installs

Six installers run headless on a serial console. Each `bootstrap-*` command
generates the answer file, extracts kernel and initrd from the ISO, boots the
installer, waits for a completion token, starts the installed VM in the
background and runs the profile's SSH provisioning.

```bash
vmctl bootstrap-unattended ubuntu-niri-local          # Ubuntu autoinstall + cloud-init
vmctl bootstrap-preseed debian-server                 # Debian preseed
vmctl bootstrap-kickstart almalinux-server            # AlmaLinux / RHEL kickstart from the ISO
vmctl bootstrap-kickstart fedora-niri-dms-local       # Fedora kickstart from the netinst + online repo
vmctl bootstrap-alpine alpine-niri                    # Alpine: setup-alpine answer file + chroot steps
vmctl bootstrap-archinstall arch-dms-local            # Arch: pacstrap script on the live ISO
vmctl bootstrap-omarchy arch-omarchy-nvidia-local     # Omarchy: official cidata mechanism
```

How each flow works, and the sequencing rule every flow must respect, is in
[docs/UNATTENDED.md](docs/UNATTENDED.md).

## The TUI

`vmtui` is a dashboard over the same `vmctl` commands: fzf when installed,
`dialog` otherwise. Every action echoes the `vmctl` command it runs, so it
doubles as a discovery tool for the CLI.

![vmtui VM menu: one-line state summary and a contextual menu with the suggested next step preselected](docs/screenshots/vmtui-vm-menu.png)

Opening a VM shows its state in one line and a single contextual menu grouped
into INSTALL, RUN, MAINTENANCE and ADVANCED. Only actions that make sense right
now are listed, and the suggested next step is preselected, so Enter does the
obvious thing: install when there is no disk, boot when it is stopped, SSH when
it is running. Details, filters, video profiles and remote SPICE viewing are in
[docs/VMTUI.md](docs/VMTUI.md).

## Make it yours

Tracked profiles use a generic guest user `lab` (password `lab`) and write
`{{user}}` wherever the name appears in a path or command. Put your identity in
the git-ignored `vms/profiles/local.json` and every profile follows:

```bash
make init-local-profile      # copies vms/profiles/local.json.example
$EDITOR vms/profiles/local.json
```

Replace `YOUR_USER`, the password hash (`openssl passwd -6`) and the SSH key
path; add `copy_from_host` entries for your dotfiles and `post_install_run`
commands for anything else. `local.json` is deep-merged over the tracked
profiles, so you only write what differs. See
[docs/PROVISIONING.md](docs/PROVISIONING.md).

## Documentation

| Page | What it covers |
|------|----------------|
| [docs/PROFILES.md](docs/PROFILES.md) | The profile model: ISO sources and discovery, disk, EFI/BIOS firmware, video variants, artifacts, Windows import templates, adding a new VM |
| [docs/UNATTENDED.md](docs/UNATTENDED.md) | The five unattended flows step by step, the completion-token rule, boot checks and the local validation matrix |
| [docs/PROVISIONING.md](docs/PROVISIONING.md) | `cloud_init`, `ssh_provision`, `autoinstall` and `omarchy_config` fields, `copy_from_host`, `post_install_run`, sudo, guest identity and `local.json` |
| [docs/VMTUI.md](docs/VMTUI.md) | The TUI in depth: dashboard, filters, contextual menu, video profiles, post-install chaining, remote SPICE |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | One-page mental map of the code: modules, import order, who owns what |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Developer loop, local checks before pushing, CI jobs, test isolation rules |
| [docs/CI_BOOT_STRATEGY.md](docs/CI_BOOT_STRATEGY.md) | Why the smoke test boots Alpine under TCG and how the ISO is discovered |
| [docs/PROFILE_TODO.md](docs/PROFILE_TODO.md) | Planned profile coverage |
| [docs/VENTOY.md](docs/VENTOY.md) | Reusing a guest disk on a Ventoy USB key |

## Development

```bash
make check      # mypy --strict + full test suite, run before every push
make ci         # the unittest invocation GitHub Actions runs
make help       # every developer target
```

The `Makefile` holds developer targets only; user-facing behaviour is a `vmctl`
subcommand. Tests never touch the host: no QEMU, no ISOs, no personal
`local.json`. Rules and the CI layout are in
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Repository layout

```text
.
├── bin/            vmctl, vmtui, ventoy-prep, ventoy-copy
├── vmctl/          the Python package behind vmctl
├── vms/
│   ├── profiles/       *.json catalog, local.json (git-ignored), local.json.example
│   └── profile-files/  scripts and dotfiles deployed by post-install
├── docs/           the pages listed above
├── tests/          unit tests (unittest / pytest)
├── isos/           cached ISOs (git-ignored)
├── artifacts/      per-VM disks, firmware vars, seeds, logs (git-ignored)
└── legacy/         the original CachyOS bash prototypes, kept for reference
```
