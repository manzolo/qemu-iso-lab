# Documentation map

Start from the repository [README](../README.md) for the quick start, then read in
this order. Each page has one job; the printable guides in `guides/` repeat the
essential steps in Italian for the terminal-at-hand case.

## 1. Use the lab

| Page | Read it when | One line |
|---|---|---|
| [PROFILES.md](PROFILES.md) | you look at `vms/profiles/*.json` for the first time, or add a VM | The profile model: core fields, ISO sources and discovery, disk, EFI/BIOS, video variants, `networks`, `shared_dir`, artifacts, Windows import templates |
| [UNATTENDED.md](UNATTENDED.md) | you run any `vmctl bootstrap-*` | Every unattended installer step by step (Ubuntu, Debian, RHEL/Fedora, Arch/CachyOS, Omarchy, Alpine, Windows, pfSense), the completion-token rule, `boot-check` and `check-vms` |
| [PROVISIONING.md](PROVISIONING.md) | you want your dotfiles, packages or commands in a fresh guest | `cloud_init`, `ssh_provision`, `autoinstall`, `copy_from_host`, `post_install_run`, sudo, guest identity and `local.json` |
| [NETWORK-LAB.md](NETWORK-LAB.md) | you build the pfSense + Pi-hole + client lab | Topology, `networks` and phases, host access through the router, the libvirt road, differences from kvm-lab, verification checklist |
| [LIBVIRT.md](LIBVIRT.md) | you want a vmctl VM in virt-manager | `export-libvirt` / `unexport-libvirt`: what is translated, what is not, how to come back |
| [VMTUI.md](VMTUI.md) | you prefer menus to flags | The TUI: dashboard, filters, contextual menu, video profiles, remote SPICE |
| [VENTOY.md](VENTOY.md) | you carry a guest disk on a Ventoy key | The two helper scripts off the `vmctl` path |

## 2. Printable guides (`guides/`)

Step-by-step cheat sheets in Italian, one per unattended flow, numbered in
reading order; `make guides` renders them with weasyprint into
`guides/pdf/qemu-iso-lab-guide.pdf` (one manual with a cover and index) and
`guides/pdf/singole/` (one PDF each). The index is
[guides/README.md](guides/README.md).

| # | Guide | Covers |
|---|---|---|
| 00 | [00-comune.md](guides/00-comune.md) | Host prerequisites, how a bootstrap works, `local.json`, daily use |
| 10 | [10-ubuntu-niri.md](guides/10-ubuntu-niri.md) | Ubuntu autoinstall + cloud-init (`bootstrap-unattended`) |
| 20 | [20-debian-server.md](guides/20-debian-server.md) | Debian preseed (`bootstrap-preseed`) |
| 30 | [30-almalinux-fedora.md](guides/30-almalinux-fedora.md) | AlmaLinux / Fedora kickstart (`bootstrap-kickstart`) |
| 40 | [40-arch-niri.md](guides/40-arch-niri.md) | Arch and CachyOS pacstrap script (`bootstrap-archinstall`) |
| 50 | [50-alpine-niri.md](guides/50-alpine-niri.md) | Alpine `setup-alpine` (`bootstrap-alpine`) |
| 60 | [60-windows.md](guides/60-windows.md) | Windows 10/11 autounattend (`bootstrap-windows`) |
| 70 | [70-network-lab.html](guides/70-network-lab.html) | The network lab with the SVG topology (`vmctl lab`, `bootstrap-pfsense`) |
| 80 | [80-virsh-cheatsheet.html](guides/80-virsh-cheatsheet.html) | virsh cheat sheet, vmctl ↔ virsh map |

## 3. Develop

| Page | One line |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | The code in one page: modules, import order, who owns what, how flows are wired |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Developer loop, host-isolation rules for tests, CI jobs, `make validate-vms` |
| [CI_BOOT_STRATEGY.md](CI_BOOT_STRATEGY.md) | Why the CI smoke test boots Alpine under TCG and how its ISO is discovered |
| [ARCH_GRUB_BOOT_FIX.md](ARCH_GRUB_BOOT_FIX.md) | Post-mortem of the Arch `grub rescue` bug behind the completion-token rule |
| [PROFILE_TODO.md](PROFILE_TODO.md) | Profile coverage still missing |

The agent-facing summary of all of this, kept in sync with the code, is
[`CLAUDE.md`](../CLAUDE.md) at the repository root.
