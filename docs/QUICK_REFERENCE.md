# Quick reference

[Back to the README](../README.md) · [All documentation](README.md)

Install the host tools first with the [installation guide](INSTALL.md). Commands below
use `vmctl`; replace `<vm>` with a profile name from `vmctl list`.

- [Install a VM](#install-a-vm)
- [Everyday commands](#everyday-commands)
- [Choose an install flow](#choose-an-install-flow)

## Install a VM

From an empty disk to a logged-in guest. Bootstrap commands install the guest;
back up an existing VM before reinstalling it.

| You get | Command | Time |
|---------|---------|------|
| Debian server, over SSH | `vmctl bootstrap-preseed debian-server && vmctl shell debian-server` | ~5 min |
| Arch with niri + Noctalia | `vmctl bootstrap-archinstall arch-noctalia && vmctl start arch-noctalia` | ~7 min |
| Ubuntu 26.04 desktop | `vmctl bootstrap-unattended ubuntu-26.04 && vmctl start ubuntu-26.04` | ~20 min |
| Windows 11 (your ISO in `isos/`) | `vmctl bootstrap-windows windows-11 && vmctl start windows-11` | ~30 min |
| A three-node Proxmox cluster | `vmctl group install proxmox-lab` | ~25 min |

Times are from the last validation run on a desktop host with KVM.

## Everyday commands

| I want to... | Run |
|--------------|-----|
| choose my profiles | `vmctl catalog add <vm> [<vm>...]`, `vmctl catalog` |
| see the VMs and their state | `vmctl list`, `vmctl status`, `vmctl show <vm>` |
| boot, enter, stop | `vmctl start <vm>`, `vmctl shell <vm>`, `vmctl stop <vm>` |
| try something and throw it away | `vmctl start <vm> --ephemeral --headless --background` (web: *Start ephemeral*) |
| watch a headless VM, even mid-install | `vmctl attach <vm>` (screen), `vmctl console <vm>` (serial) |
| record an install as a time-lapse | `vmctl record <vm>` while a bootstrap runs (GIF, `--mp4` for the video) |
| keep a copy to go back to | `vmctl checkpoint create <vm> clean`, later `restore` ([guide](CHECKPOINTS.md)) |
| make an independent second VM | `vmctl clone <vm> <new-name>` ([guide](CLONE.md)) |
| connect two VMs | `vmctl link <vm> <vm>` ([guide](LABS.md)) |
| open a VM in virt-manager | `vmctl export-libvirt <vm>` ([guide](LIBVIRT.md)) |
| write a VM to a USB disk, or import one | `vmctl flash`, `vmctl import-device` ([guide](IMPORT_DISKS.md)) |
| free space | `vmctl clean <vm>`, `vmctl delete-iso <vm>`, `vmctl clean-reports` |
| update the lab | `vmctl update` |

`vmctl --help` lists commands by task. Use the global `--dry-run` option before
a command to preview its operations, for example `vmctl --dry-run start debian-server`.
Tab completion: `echo 'eval "$(vmctl completion zsh)"' >> ~/.zshrc` (bash works too).

> [!WARNING]
> `vmctl flash` overwrites the entire physical target disk. Verify its size, model and
> serial, and back up its contents first. `import-device` reads a physical disk into a
> VM image. See the [physical disk guide](IMPORT_DISKS.md) before using either command.
> Installs, `clean`, checkpoint restores and lab resets can delete or replace VM data.

## Choose an install flow

| Family | Profiles | Unattended with |
|--------|----------|-----------------|
| Ubuntu and flavours | every LTS desktop from 8.04 to 26.04, Kubuntu, Xubuntu, Lubuntu, MATE, Budgie, Cinnamon, Studio | `bootstrap-unattended`, `bootstrap-preseed` |
| Debian, Kali | server, GNOME, KDE, Xfce, Kali | `bootstrap-preseed` |
| Fedora, RHEL | Workstation, KDE, Silverblue, Kinoite, AlmaLinux, Rocky, CentOS Stream | `bootstrap-kickstart` |
| Arch family | Arch with niri, DMS or Noctalia, CachyOS, Omarchy, pearOS, NVIDIA recipes | `bootstrap-archinstall`, `bootstrap-omarchy`, `bootstrap-pearos` |
| Linux Mint | 22.3 Cinnamon (Ubiquity in automatic mode) | `bootstrap-ubiquity` |
| More Linux | openSUSE Tumbleweed, NixOS, Alpine, cloud images | `bootstrap-autoyast`, `bootstrap-nixos`, `bootstrap-alpine`, `bootstrap-cloudimg` |
| BSD and others | FreeBSD, pfSense, ReactOS, Proxmox VE, Haiku | `bootstrap-freebsd`, `bootstrap-pfsense`, `bootstrap-reactos`, `bootstrap-proxmox`, `bootstrap-haiku` |
| Windows | 11 and 10 (OpenSSH, virtio drivers, shared folder), 7 | `bootstrap-windows` |
| Windows retro | XP, 2000, NT 4.0, 98 | `bootstrap-windowsxp`, `bootstrap-windows2000`, `bootstrap-windowsnt4`, `bootstrap-windows98` |

An unattended install runs headless on a serial console, boots the installed disk and runs the
profile's SSH provisioning. Every profile also has a manual install: `vmctl provision <vm>`
boots the ISO on a fresh disk. `vmctl` downloads and checksums every ISO that has a public
source; where there is none (Windows, the retro versions, pearOS), the dashboard's *ISO needed*
says exactly what to get and where to put it. Versions and history: [Profile versions](PROFILES.md#profile-versions).

See [Unattended installs](UNATTENDED.md) for the complete flows and validation details.
