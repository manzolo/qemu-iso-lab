# QEMU ISO Lab

[![CI](https://github.com/manzolo/qemu-iso-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/manzolo/qemu-iso-lab/actions/workflows/ci.yml)

Install and run **Linux, BSD and Windows** guests on QEMU/KVM from one JSON
profile each: ISO download and checksum, disk, firmware, a **zero-click
install** and SSH provisioning. 96 profiles, from Arch, Debian, Fedora, NixOS
and every Ubuntu LTS since 8.04 to Windows 11, 10, 7, XP, 2000 and NT 4,
FreeBSD, pfSense and ReactOS. One CLI (`vmctl`) and one terminal dashboard
(`vmtui`) drive all of it.

![vmtui: search, filters, every profile with its state, and the actions that make sense for the selected VM](docs/screenshots/vmtui-dashboard.png)

## Quick start

```bash
git clone https://github.com/manzolo/qemu-iso-lab.git && cd qemu-iso-lab
make setup     # links vmctl + vmtui into ~/.local/bin, installs what is missing, checks the host
vmtui          # the dashboard: pick a profile, Enter installs or boots it
```

`make setup` shows what it is about to install and asks first: QEMU, OVMF and
the helpers through `apt` or `pacman` (sudo), Textual for the dashboard in the
repository's `.venv-tui` (no sudo). Run it again at any time: it only installs
what is missing, then prints one line per group of tools (`vmctl setup -v` for
every tool).

Ready-made first runs, each one command from an empty disk to a logged-in VM:

| What | Command | Time |
|------|---------|------|
| Debian server over SSH | `vmctl bootstrap-preseed debian-server && vmctl shell debian-server` | ~5 min |
| Ubuntu 24.04 GNOME desktop | `vmctl bootstrap-unattended ubuntu-gnome-24.04 && vmctl start ubuntu-gnome-24.04` | ~20 min |
| Arch with niri + Noctalia | `vmctl bootstrap-archinstall arch-noctalia && vmctl start arch-noctalia` | ~7 min |
| Windows 11 (your ISO in `isos/`) | `vmctl bootstrap-windows windows11-unattended && vmctl start windows11-unattended` | ~30 min |
| A whole lab | `vmctl group install proxmox-lab` ([Labs](#labs)) | ~25 min |

Tab completion: `echo 'eval "$(vmctl completion zsh)"' >> ~/.zshrc` (bash works too).

<details>
<summary>Installing the host packages by hand (other distributions, or no sudo from make)</summary>

```bash
sudo pacman -S qemu-desktop qemu-base edk2-ovmf python openssh libvirt make dialog fzf cloud-image-utils xorriso virtiofsd virt-viewer p7zip dvd+rw-tools python-bcrypt swtpm gdisk ddrescue partclone util-linux
sudo apt install -y qemu-system-x86 qemu-utils ovmf python3 python3-venv openssh-client libvirt-clients libvirt-daemon-system make dialog fzf cloud-image-utils xorriso virtiofsd virt-viewer p7zip-full dvd+rw-tools python3-bcrypt swtpm gdisk gddrescue partclone fdisk
make install textual    # only the dashboard, into .venv-tui (or: make install xorriso growisofs ...)
vmctl setup             # check again
```

Only `qemu-system-x86_64`, `qemu-img`, `python3` and the OVMF firmware are
required; everything else serves a specific flow and `vmctl setup` says which.
Without Textual, `vmtui` opens the fzf/dialog menus.
</details>

## What it installs

`vmctl list` prints every profile with its status and last live verification.
Unattended flows run headless on a serial console, then boot the installed disk
and run the profile's SSH provisioning.

| Family | Examples | Zero-click install |
|--------|----------|--------------------|
| Ubuntu and flavours | 8.04 → 26.04 desktops, Kubuntu, Xubuntu, Lubuntu, MATE, Budgie, Cinnamon, Studio, niri | `bootstrap-unattended` (autoinstall), `bootstrap-preseed` (d-i, up to 18.04) |
| Debian / Kali | server, GNOME, KDE, Xfce, Kali | `bootstrap-preseed` |
| Fedora / RHEL | Workstation, KDE, Silverblue, Kinoite, AlmaLinux, Rocky, CentOS Stream | `bootstrap-kickstart` |
| Arch family | Arch + niri/DMS/Noctalia, CachyOS, Omarchy, pearOS, NVIDIA recipes | `bootstrap-archinstall`, `bootstrap-omarchy`, `bootstrap-pearos` |
| Others | openSUSE Tumbleweed, NixOS, Alpine, FreeBSD, Void | `bootstrap-autoyast`, `bootstrap-nixos`, `bootstrap-alpine`, `bootstrap-freebsd` |
| **Windows** | 11, 10 (OpenSSH, virtio drivers, virtiofs share), 7 | `bootstrap-windows` |
| **Windows retro** | XP, 2000, NT 4.0, 98 | `bootstrap-windowsxp`, `bootstrap-windows2000`, `bootstrap-windowsnt4`, `bootstrap-windows98` |
| **Labs** | pfSense router + Pi-hole + Lubuntu client; three-node Proxmox VE cluster + client ([Labs](#labs)) | `vmctl group install netlab`, `vmctl group install proxmox-lab` |
| ReactOS | 0.4.16 | `bootstrap-reactos` |

Windows media have no public URL: put your own ISO in `isos/` (retro keys go in
`local.json`). Every profile also offers a manual install:
`vmctl provision <vm>` boots the ISO on a fresh disk.

## Everyday commands

| I want to... | Run |
|--------------|-----|
| see the VMs and their state | `vmctl list`, `vmctl status`, `vmctl show <vm>` |
| boot, enter, stop | `vmctl start <vm>` (`--headless --background`), `vmctl shell <vm>`, `vmctl stop <vm>` |
| watch a headless VM, also mid-install | `vmctl attach <vm>` (VNC), `vmctl console <vm>` (serial) |
| keep a copy to go back to | `vmctl checkpoint create <vm> clean`, then `restore` ([guide](docs/CHECKPOINTS.md)) |
| make an independent second VM | `vmctl clone <vm> <new-name>` ([guide](docs/CLONE.md)) |
| hand a VM to virt-manager | `vmctl export-libvirt <vm>` ([guide](docs/LIBVIRT.md)) |
| write to a USB disk, or import one | `vmctl flash`, `vmctl import-device` ([guide](docs/IMPORT_DISKS.md)) |
| validate the install flows | `vmctl check-vms --group smoke --restore --report --open` |
| free space | `vmctl clean <vm>`, `vmctl delete-iso <vm>` |

Every command accepts `--dry-run`; `vmctl --help` lists them all by task.

## The TUI

`vmtui` shows every profile with its live state. Right or Enter moves to the
selected VM's actions: install before there is a disk, boot after, display, SSH
and stop while it runs. **All actions…** opens the full menu, grouped and
filterable, with the suggested next step preselected. Every action runs a
`vmctl` command, and the dashboard shows the command line so you can copy it.

![All actions: the full contextual menu of a running VM](docs/screenshots/vmtui-actions.png)

`/` searches names, descriptions and families, so `windows` lists every
Windows profile:

![Search: the Windows profiles, with the unattended install ready for the selected one](docs/screenshots/vmtui-windows.png)

**Tools… / F4** opens global status, remote hosts and **Clean All**. Clean All
asks for confirmation and affects every configured VM, regardless of filters;
checkpoints and cached ISOs are kept.
F8 (or `vmtui --classic`) opens the fzf/dialog menus. Controls, video profiles and remote SPICE:
[docs/VMTUI.md](docs/VMTUI.md).

## Labs

Groups of VMs on a private segment, installed and started as one stack, each
with a generated network map (addresses, forwards, logins, a runbook):
**netlab** (pfSense router, Pi-hole DNS, Lubuntu client) and **proxmox-lab**
(three Proxmox VE nodes clustered over ZFS mirrors, plus a browser client).
In `vmtui` they are behind **Labs** (F2).

![Map of the Proxmox lab: three nodes and the client on pve-lan](docs/screenshots/lab-map-proxmox.png)

What each lab contains, the commands and the maps: [docs/LABS.md](docs/LABS.md).

## Make it yours

Tracked profiles use a generic guest user `lab` (password `lab`). Your identity,
SSH key, dotfiles and extra commands go in the git-ignored
`vms/profiles/local.json`, deep-merged over every profile:

```bash
make init-local-profile && $EDITOR vms/profiles/local.json
```

See [docs/PROVISIONING.md](docs/PROVISIONING.md).

## Documentation

[docs/README.md](docs/README.md) is the map. The main pages:
[PROFILES](docs/PROFILES.md) (the profile model, adding a VM) ·
[UNATTENDED](docs/UNATTENDED.md) (every install flow) ·
[PROVISIONING](docs/PROVISIONING.md) ·
[VMTUI](docs/VMTUI.md) ·
[LABS](docs/LABS.md) ·
[NETWORK-LAB](docs/NETWORK-LAB.md) ·
[IMPORT_DISKS](docs/IMPORT_DISKS.md) ·
[ARCHITECTURE](docs/ARCHITECTURE.md) ·
[DEVELOPMENT](docs/DEVELOPMENT.md).
Printable step-by-step guides in English and Italian are in
[docs/guides/](docs/guides/README.md) (`make guides` builds the PDFs).

## Development

```bash
make check          # mypy --strict + tests, before every push
make validate-vms   # local only: reinstall every unattended profile, HTML report (hours)
make help           # every developer target
```

The `Makefile` holds developer targets only; user-facing behaviour is a `vmctl`
subcommand. Tests never touch the host. `.claude/commands/` has Claude Code
shortcuts for this checkout (`/vm-status`, `/vm-unattended`, `/vm-ssh`, `/vm-shot`, ...).
